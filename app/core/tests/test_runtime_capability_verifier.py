from __future__ import annotations

import ast
import base64
import copy
import hashlib
import importlib.util
import json
import pathlib
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/verify-runtime-capability.py"


def _load():
    spec = importlib.util.spec_from_file_location("runtime_capability_verifier", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _descriptor() -> dict:
    return {
        "generation_id": "a" * 64,
        "dependency_inventory_sha256": "b" * 64,
        "python_version": "3.14.8",
    }


def _inventory():
    return {
        "packages": {
            "requests": {"name": "requests", "version": "2.32.5"},
            "cryptography": {"name": "cryptography", "version": "46.0.3"},
            "sqlmodel": {"name": "sqlmodel", "version": "0.0.31"},
            "uvicorn": {"name": "uvicorn", "version": "0.40.0"},
            "pydantic-core": {"name": "pydantic-core", "version": "2.41.5"},
            "sqlalchemy": {"name": "SQLAlchemy", "version": "2.1.4"},
            "httptools": {"name": "httptools", "version": "0.9.0"},
        }
    }


def _components(path: str | None = None):
    generation = "/config/channelwatch-runtime/releases/" + "a" * 64
    path = path or f"{generation}/dependencies/site-packages"
    identity = {
        "runtime_generation_id": "a" * 64,
        "runtime_descriptor_sha256": "a" * 64,
        "dependency_inventory_sha256": "b" * 64,
    }
    dependencies = {
        "requests": {"loaded": True, "path": f"{path}/requests/__init__.py", "version": "2.32.5"},
        "cryptography": {"loaded": True, "path": f"{path}/cryptography/__init__.py", "version": "46.0.3"},
        "sqlmodel": {"loaded": False},
        "uvicorn": {"loaded": False},
        "pydantic_core": {"loaded": True, "path": f"{path}/pydantic_core.so", "version": "2.41.5"},
        "sqlalchemy": {"loaded": True, "path": f"{path}/sqlalchemy/__init__.py", "version": "2.1.4"},
        "httptools": {"loaded": False},
    }
    return {
        name: {
            **identity,
            "process_runtime": {
                "pid": index,
                "python_executable": f"{generation}/environment/bin/python",
                "python_version": "3.14.8",
                "python_prefix": f"{generation}/environment",
                "dependencies": dependencies,
            },
        }
        for index, name in enumerate(("core", "ui"), start=1)
    }


def test_runtime_components_bind_both_workers_and_loaded_dependency_origins():
    verifier = _load()
    components = _components()

    assert verifier.validate_runtime_components(
        {"runtime_components": components}, _descriptor(), _inventory()
    ) == components

    wrong = _components("/venv/lib/python3.14/site-packages")
    with pytest.raises(verifier.CapabilityError, match="outside the selected generation"):
        verifier.validate_runtime_components(
            {"runtime_components": wrong}, _descriptor(), _inventory()
        )

    missing = _components()
    missing["core"]["process_runtime"]["dependencies"]["requests"] = {
        "loaded": False
    }
    with pytest.raises(verifier.CapabilityError, match="required dependency requests"):
        verifier.validate_runtime_components(
            {"runtime_components": missing}, _descriptor(), _inventory()
        )

    wrong_version = _components()
    wrong_version["ui"]["process_runtime"]["dependencies"]["sqlalchemy"]["version"] = "2.0.54"
    with pytest.raises(verifier.CapabilityError, match="unsigned version"):
        verifier.validate_runtime_components(
            {"runtime_components": wrong_version}, _descriptor(), _inventory()
        )


def test_signed_inventory_is_loaded_from_exact_bundle_and_binds_native_parser(tmp_path: Path):
    verifier = _load()
    parser_path = "dependencies/site-packages/httptools/parser/parser.cpython-314-x86_64-linux-gnu.so"
    files = [
        {"path": "dependencies/site-packages/sqlalchemy/__init__.py", "sha256": "1" * 64, "size": 1, "mode": "0644"},
        {"path": "dependencies/site-packages/httptools/__init__.py", "sha256": "2" * 64, "size": 2, "mode": "0644"},
        {"path": parser_path, "sha256": "3" * 64, "size": 3, "mode": "0755"},
    ]
    inventory = {
        "dependencies": [
            {"name": "SQLAlchemy", "version": "2.1.4"},
            {"name": "httptools", "version": "0.9.0"},
        ],
        "files": files,
        "counts": {"files": 3, "bytes": 6},
    }
    raw = (json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n").encode()
    bundle = tmp_path / "runtime.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("dependencies/inventory.json", raw)

    evidence, normalized = verifier.load_signed_dependency_inventory(
        bundle, {"dependency_inventory_sha256": hashlib.sha256(raw).hexdigest()}
    )

    assert base64.b64decode(evidence["json_base64"]) == raw
    assert evidence["size"] == len(raw)
    assert normalized["packages"]["sqlalchemy"]["version"] == "2.1.4"
    assert normalized["httptools_parser"] == files[-1]

    with pytest.raises(verifier.CapabilityError, match="digest"):
        verifier.load_signed_dependency_inventory(
            bundle, {"dependency_inventory_sha256": "f" * 64}
        )


def test_lost_apply_response_polls_same_job_without_resubmission(monkeypatch):
    verifier = _load()

    class Client:
        def __init__(self):
            self.apply_calls = 0
            self.job_calls = 0

        def request(self, path, payload=None):
            if path == "/api/v1/update/apply":
                self.apply_calls += 1
                raise urllib.error.URLError("restart closed response")
            assert path == "/api/v1/update/jobs/0123456789abcdef0123456789abcdef"
            self.job_calls += 1
            return {
                "job_id": "0123456789abcdef0123456789abcdef",
                "status": "success" if self.job_calls == 2 else "applying",
            }

    monkeypatch.setattr(verifier.time, "sleep", lambda _seconds: None)
    client = Client()
    result = verifier.apply_once_and_wait(
        client,
        "/api/v1/update/apply",
        version="1.5.2",
        job_id="0123456789abcdef0123456789abcdef",
        timeout=5,
    )

    assert result["status"] == "success"
    assert client.apply_calls == 1
    assert client.job_calls == 2


def test_wait_for_successful_job_requires_terminal_success(monkeypatch):
    verifier = _load()

    class Client:
        def __init__(self, jobs):
            self.jobs = iter(jobs)
            self.paths = []

        def request(self, path):
            self.paths.append(path)
            return next(self.jobs)

    monkeypatch.setattr(verifier.time, "sleep", lambda _seconds: None)
    job_id = "0123456789abcdef0123456789abcdef"
    client = Client(({"status": "restarting"}, {"job_id": job_id, "status": "success"}))
    assert verifier.wait_for_successful_job(
        client, job_id=job_id, timeout=5, description="rollback job"
    )["status"] == "success"
    assert client.paths == [
        f"/api/v1/update/jobs/{job_id}",
        f"/api/v1/update/jobs/{job_id}",
    ]

    with pytest.raises(verifier.CapabilityError, match="rollback job failed: failed"):
        verifier.wait_for_successful_job(
            Client(({"job_id": job_id, "status": "failed"},)),
            job_id=job_id,
            timeout=5,
            description="rollback job",
        )


def test_native_architecture_rejects_host_docker_or_requested_mismatch(monkeypatch):
    verifier = _load()
    monkeypatch.setattr(verifier.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="amd64\n"),
    )
    assert verifier.native_architecture("amd64") == "amd64"

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="arm64\n"),
    )
    with pytest.raises(verifier.CapabilityError, match="same native architecture"):
        verifier.native_architecture()

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="amd64\n"),
    )
    with pytest.raises(verifier.CapabilityError, match="not host-native"):
        verifier.native_architecture("arm64")


def test_published_port_is_refreshed_after_each_explicit_container_restart(
    monkeypatch,
):
    verifier = _load()
    ports = iter(("127.0.0.1:63604\n", "127.0.0.1:49518\n"))
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=next(ports)),
    )
    client = verifier.ApiClient("http://127.0.0.1:1", "admin", "password")

    client.set_base_url(verifier.published_base_url("canary"))
    assert client.base_url == "http://127.0.0.1:63604"
    client.set_base_url(verifier.published_base_url("canary"))
    assert client.base_url == "http://127.0.0.1:49518"


def test_feed_serves_only_exact_approved_signed_paths(tmp_path: Path):
    verifier = _load()
    catalog = tmp_path / "catalog.json"
    bundle = tmp_path / "runtime.zip"
    catalog.write_bytes(b"catalog")
    bundle.write_bytes(b"bundle")

    script = verifier.write_feed(
        tmp_path / "feed",
        {
            "https://channelwatch.coderluii.dev/updates/v3/stable.json": catalog,
            "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.5.2/runtime.zip": bundle,
        },
    )

    source = script.read_text(encoding="utf-8")
    assert "channelwatch.coderluii.dev/updates/v3/stable.json" in source
    assert "github.com/CoderLuii/ChannelWatch/releases/download/v1.5.2/runtime.zip" in source
    assert sorted(path.read_bytes() for path in (script.parent / "assets").iterdir()) == [
        b"bundle",
        b"catalog",
    ]

    with pytest.raises(verifier.CapabilityError, match="approved HTTPS origin"):
        verifier.write_feed(
            tmp_path / "bad-feed",
            {"http://example.invalid/runtime.zip": bundle},
        )


def test_feed_includes_exact_synthetic_dvr_health_contract(tmp_path: Path):
    verifier = _load()
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(b"catalog")

    source = verifier.write_feed(
        tmp_path / "feed",
        {"https://channelwatch.coderluii.dev/updates/v3/stable.json": catalog},
    ).read_text(encoding="utf-8")

    for path in (
        "/status",
        "/dvr",
        "/api/v1/channels",
        "/api/v1/all",
        "/api/v1/jobs",
        "/dvr/events/subscribe",
    ):
        assert path in source
    assert "('0.0.0.0',8089)" in source
    assert "text/event-stream" in source
    assert '"--network-alias", "synthetic-dvr"' in SCRIPT.read_text(encoding="utf-8")

    namespace = {}
    exec(compile(source.split("dvr=http.server.ThreadingHTTPServer", 1)[0], "feed.py", "exec"), namespace)
    server = namespace["http"].server.ThreadingHTTPServer(
        ("127.0.0.1", 0), namespace["DvrHandler"]
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base_url}/status", timeout=2) as response:
            assert json.load(response) == {
                "name": "Capability DVR",
                "version": "mock-1.0",
            }
        with urllib.request.urlopen(
            f"{base_url}/dvr/events/subscribe", timeout=2
        ) as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            assert response.readline() == (
                b'data: {"Type":"hello","Name":"hello","Value":"ready"}\n'
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_synthetic_rbac_settings_include_one_enabled_dvr(monkeypatch):
    verifier = _load()
    commands = []
    monkeypatch.setattr(
        verifier,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    verifier.seed_synthetic_rbac_settings("sha256:" + "a" * 64, "probe-config")

    assert len(commands) == 1
    seed_code = commands[0][-1]
    tree = ast.parse(seed_code)
    settings_write = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_text"
    )
    settings = json.loads(ast.literal_eval(settings_write.args[0]))
    assert settings["dvr_servers"] == [
        {
            "api_key": "",
            "enabled": True,
            "host": "synthetic-dvr",
            "id": "capability-dvr",
            "name": "Capability DVR",
            "port": 8089,
        }
    ]


def test_wait_for_image_health_retries_503_until_exact_ready(monkeypatch):
    verifier = _load()
    attempts = {"ready": 0}

    class Client:
        def request(self, path):
            if path == "/healthz/startup":
                return {"status": "ready"}
            assert path == "/healthz/ready"
            attempts["ready"] += 1
            if attempts["ready"] == 1:
                raise urllib.error.HTTPError(path, 503, "degraded", {}, None)
            return {"status": "ready", "ready": True}

    monkeypatch.setattr(verifier.time, "sleep", lambda _seconds: None)

    assert verifier.wait_for_image_health(Client(), 2, "synthetic image") == {
        "startup": {"status": "ready"},
        "ready": {"status": "ready", "ready": True},
    }
    assert attempts["ready"] == 2


def test_wait_for_image_health_rejects_degraded_record():
    verifier = _load()

    class Client:
        def request(self, path):
            if path == "/healthz/startup":
                return {"status": "ready"}
            return {"status": "degraded", "ready": False}

    with pytest.raises(
        verifier.CapabilityError,
        match="Image core/UI readiness checks are not both ready",
    ):
        verifier.wait_for_image_health(Client(), 2, "degraded image")


def test_wait_for_image_health_times_out_on_repeated_503(monkeypatch):
    verifier = _load()

    class Client:
        def request(self, path):
            if path == "/healthz/startup":
                return {"status": "ready"}
            raise urllib.error.HTTPError(path, 503, "degraded", {}, None)

    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(verifier.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(verifier.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        verifier.CapabilityError,
        match="Timed out waiting for degraded image: HTTPError",
    ):
        verifier.wait_for_image_health(Client(), 1, "degraded image")


def test_tls_material_passes_python_strict_server_verification(tmp_path: Path):
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID

    verifier = _load()
    root = tmp_path / "tls"
    root.mkdir()
    ca_path, cert_path, key_path = verifier.create_tls_material(root)

    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    server = x509.load_pem_x509_certificate(cert_path.read_bytes())
    ca_constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    ca_usage = ca.extensions.get_extension_for_class(x509.KeyUsage)
    ca_identifier = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
    server_constraints = server.extensions.get_extension_for_class(
        x509.BasicConstraints
    )
    server_usage = server.extensions.get_extension_for_class(x509.KeyUsage)
    server_authority = server.extensions.get_extension_for_class(
        x509.AuthorityKeyIdentifier
    )
    server_identifier = server.extensions.get_extension_for_class(
        x509.SubjectKeyIdentifier
    )
    server_purpose = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage)

    assert ca_constraints.critical is True
    assert ca_constraints.value.ca is True
    assert ca_constraints.value.path_length == 0
    assert ca_usage.critical is True
    assert ca_usage.value.key_cert_sign is True
    assert ca_usage.value.crl_sign is True
    assert ca_identifier.critical is False
    assert server_constraints.critical is True
    assert server_constraints.value.ca is False
    assert server_usage.critical is True
    assert server_usage.value.digital_signature is True
    assert server_usage.value.key_encipherment is True
    assert server_usage.value.key_cert_sign is False
    assert server_authority.value.key_identifier == ca_identifier.value.digest
    assert server_identifier.value.digest != ca_identifier.value.digest
    assert server_purpose.value == x509.ExtendedKeyUsage(
        [ExtendedKeyUsageOID.SERVER_AUTH]
    )

    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=ca_path)
    assert client_context.verify_flags & ssl.VERIFY_X509_STRICT

    def connect(context: ssl.SSLContext, hostname: str) -> list[BaseException]:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        failures: list[BaseException] = []

        def serve_once() -> None:
            try:
                connection, _address = listener.accept()
                with server_context.wrap_socket(connection, server_side=True) as secure:
                    secure.recv(1)
            except BaseException as exc:  # pragma: no cover - asserted by caller
                failures.append(exc)
            finally:
                listener.close()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        try:
            with socket.create_connection(listener.getsockname()) as connection:
                with context.wrap_socket(connection, server_hostname=hostname) as secure:
                    secure.sendall(b"x")
        finally:
            thread.join(timeout=5)
            assert not thread.is_alive()
        return failures

    assert connect(client_context, "channelwatch.coderluii.dev") == []
    with pytest.raises(ssl.SSLCertVerificationError, match="Hostname mismatch"):
        connect(client_context, "example.invalid")

    other = tmp_path / "other-tls"
    other.mkdir()
    other_ca, _other_cert, _other_key = verifier.create_tls_material(other)
    wrong_ca_context = ssl.create_default_context(cafile=other_ca)
    with pytest.raises(ssl.SSLCertVerificationError, match="certificate verify failed"):
        connect(wrong_ca_context, "channelwatch.coderluii.dev")


def test_preservation_requires_exact_nonempty_hash_mapping():
    verifier = _load()
    before = {"/config/settings.json": "a" * 64, "/config/encryption.key": "b" * 64}
    verifier.validate_preservation(before, dict(before))

    with pytest.raises(verifier.CapabilityError, match="identity changed"):
        verifier.validate_preservation(before, {**before, "/config/settings.json": "c" * 64})
    with pytest.raises(verifier.CapabilityError, match="identity changed"):
        verifier.validate_preservation({}, {})


def test_persistent_hash_probe_is_limited_to_settings_and_managed_key(
    monkeypatch,
):
    verifier = _load()
    expected = {
        "/config/settings.json": "a" * 64,
        "/config/encryption.key": "b" * 64,
    }
    observed = {}

    def execute(name, code):
        observed.update({"name": name, "code": code})
        return expected

    monkeypatch.setattr(verifier, "docker_exec_json", execute)

    assert verifier.persistent_hashes("canary") == expected
    assert observed["name"] == "canary"
    assert all(path in observed["code"] for path in expected)


def test_image_inventory_requires_exact_full_entry_equality_and_retains_raw_stage(
    tmp_path: Path,
):
    verifier = _load()
    entries = [
        {"path": "/venv", "type": "directory", "mode": "0755", "size": 0},
        {
            "path": "/venv/pyvenv.cfg",
            "type": "file",
            "mode": "0644",
            "size": 4,
            "sha256": "a" * 64,
        },
    ]
    encoded = json.dumps(
        entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    baseline = {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "path_count": len(entries),
        "regular_file_bytes": 4,
        "entries": entries,
    }

    verifier.validate_image_inventory(baseline, json.loads(json.dumps(baseline)))
    artifact = verifier.retain_image_inventory(
        baseline, tmp_path / "receipt.json", "amd64", "before"
    )
    retained = tmp_path / artifact["file"]
    assert retained.is_file()
    assert artifact["sha256"] == hashlib.sha256(retained.read_bytes()).hexdigest()
    assert artifact["size"] == retained.stat().st_size

    changed = json.loads(json.dumps(baseline))
    changed["entries"][1]["mode"] = "0755"
    with pytest.raises(verifier.CapabilityError, match="inventory changed"):
        verifier.validate_image_inventory(baseline, changed)


def test_image_inventory_probe_compiles_before_container_execution(monkeypatch):
    verifier = _load()
    entries = [
        {"path": "/venv", "type": "directory", "mode": "0755", "size": 0}
    ]
    encoded = json.dumps(
        entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()

    def execute(_name, code):
        compile(code, "<image-inventory-probe>", "exec")
        return {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "path_count": 1,
            "regular_file_bytes": 0,
            "entries": entries,
        }

    monkeypatch.setattr(verifier, "docker_exec_json", execute)
    assert verifier.image_inventory("canary")["entries"] == entries


def test_image_inventory_probe_has_exact_external_interpreter_link_policy(monkeypatch):
    verifier = _load()
    observed = {}
    entries = [
        {"path": "/venv", "type": "directory", "mode": "0755", "size": 0}
    ]

    def execute(_name, code):
        observed["code"] = code
        return {
            "sha256": "a" * 64,
            "path_count": 1,
            "regular_file_bytes": 0,
            "entries": entries,
        }

    monkeypatch.setattr(verifier, "docker_exec_json", execute)
    verifier.image_inventory("canary")
    tree = ast.parse(observed["code"])
    policy_node = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "allowed_external"
            for target in node.targets
        )
    )
    policy = {
        ast.literal_eval(key): ast.literal_eval(value.args[0])
        for key, value in zip(policy_node.keys, policy_node.values, strict=True)
    }
    assert policy == {
        f"/venv/bin/{path}": "/usr/local/bin/python3.14"
        for path in ("python", "python3", "python3.14", "𝜋thon")
    }
    assert policy.get("/venv/bin/python") != "/usr/local/bin/python"
    assert policy.get("/venv/bin/unlisted-python") is None
    assert "resolved != allowed_external.get(absolute)" in observed["code"]


def test_all_embedded_container_probes_compile_through_their_wrappers(monkeypatch):
    verifier = _load()
    inventory_entries = [
        {"path": "/venv", "type": "directory", "mode": "0755", "size": 0}
    ]
    inventory_encoded = json.dumps(
        inventory_entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    controls = {
        filename: {"type": "absent"}
        for filename in (
            "active.json",
            "rollback.json",
            "restart-required.json",
            "restart-required.lock",
            "restart-services-accepted.json",
            "activation-pending.json",
            "activation-core-ready.json",
            "activation-ui-ready.json",
        )
    }
    launcher_commands = {
        "core": "/venv/bin/python -u /app/core/runtime_launcher.py core --stay-alive",
        "ui": (
            "/venv/bin/python -u /app/core/runtime_launcher.py ui "
            "--host 0.0.0.0 --port 8501 --log-level warning"
        ),
    }
    responses = iter(
        (
            {"/config/settings.json": "a" * 64, "/config/encryption.key": "b" * 64},
            {
                "sha256": hashlib.sha256(inventory_encoded).hexdigest(),
                "path_count": 1,
                "regular_file_bytes": 0,
                "entries": inventory_entries,
            },
            {"free_bytes": 4096, "selected_generation_bytes": 2048},
            {
                component: {
                    "pid": pid,
                    "ppid": pid + 10,
                    "command": f"/runtime/python worker --mode {component}",
                }
                for component, pid in (("core", 101), ("ui", 202))
            },
            {
                component: {"pid": pid, "command": launcher_commands[component]}
                for component, pid in (("core", 301), ("ui", 302))
            },
            {"core": [101], "ui": [202]},
            controls,
            {
                "mount_type": "tmpfs",
                "mount_options": ["noexec", "rw"],
                "uid": 1000,
                "gid": 1000,
                "mode": "0755",
                "free_bytes": 4096,
            },
            {
                "path": "/runtime/native.so",
                "signed_sha256": "c" * 64,
                "before_sha256": "c" * 64,
                "damaged_sha256": "d" * 64,
            },
        )
    )
    compiled = []

    def execute(_name, code):
        compile(code, f"<embedded-container-probe-{len(compiled)}>", "exec")
        compiled.append(code)
        return next(responses)

    monkeypatch.setattr(verifier, "docker_exec_json", execute)
    assert verifier.persistent_hashes("app")["/config/settings.json"] == "a" * 64
    assert verifier.image_inventory("app")["path_count"] == 1
    assert verifier.runtime_storage_observation("app")["free_bytes"] == 4096
    workers = verifier.worker_processes("app", python_executable="/runtime/python")
    assert workers["core"]["pid"] == 101
    services = {
        component: {"status": "RUNNING", "pid": pid, "uptime": "0:01:00"}
        for component, pid in (("core", 301), ("ui", 302))
    }
    assert verifier.image_supervisor_processes("app", services)["ui"]["pid"] == 302
    assert verifier.worker_pid_scan("app", python_executable="/runtime/python")["ui"] == [202]
    assert verifier.control_state("app") == controls
    assert verifier.filesystem_state("app")["mount_type"] == "tmpfs"
    assert verifier.damage_native_file(
        "app", path="/runtime/native.so", expected_sha256="c" * 64
    )["damaged_sha256"] == "d" * 64
    assert len(compiled) == 9


def test_isolated_import_probe_uses_exact_python_flags_and_rejects_parser_conflation(
    monkeypatch,
):
    verifier = _load()
    calls = []
    prefix = "/config/channelwatch-runtime/releases/" + "a" * 64
    site = f"{prefix}/dependencies/site-packages"
    imports = {
        "sqlalchemy": {"path": f"{site}/sqlalchemy/__init__.py", "version": "2.1.4"},
        "httptools": {"path": f"{site}/httptools/__init__.py", "version": "0.9.0"},
        "httptools.parser.parser": {
            "path": f"{site}/httptools/parser/parser.cpython-314-x86_64-linux-gnu.so",
            "version": "0.9.0",
            "sha256": "c" * 64,
        },
    }

    def execute(command, **_kwargs):
        compile(command[-1], "<isolated-import-probe>", "exec")
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps(imports) + "\n")

    monkeypatch.setattr(verifier, "run", execute)
    assert verifier.import_smoke(
        "canary",
        python=f"{prefix}/environment/bin/python",
        site_packages=site,
        expected_versions={"sqlalchemy": "2.1.4", "httptools": "0.9.0"},
        parser_sha256="c" * 64,
    ) == imports
    assert calls[0][0:4] == [
        "docker",
        "exec",
        "canary",
        f"{prefix}/environment/bin/python",
    ]
    assert calls[0][4:7] == ["-I", "-B", "-S"]

    imports["httptools.parser.parser"]["path"] = imports["httptools"]["path"]
    with pytest.raises(verifier.CapabilityError, match="conflated"):
        verifier.import_smoke(
            "canary",
            python=f"{prefix}/environment/bin/python",
            site_packages=site,
            expected_versions={"sqlalchemy": "2.1.4", "httptools": "0.9.0"},
            parser_sha256="c" * 64,
        )


def test_pinned_image_selects_one_native_manifest_and_exact_local_identity(monkeypatch):
    verifier = _load()
    child_digest = "sha256:" + "2" * 64
    image_id = "sha256:" + "3" * 64
    raw_index = json.dumps(
        {
            "manifests": [
                {
                    "digest": child_digest,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "digest": "sha256:" + "4" * 64,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
            ]
        },
        separators=(",", ":"),
    ).encode()
    index_digest = "sha256:" + hashlib.sha256(raw_index).hexdigest()
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[1] == "image":
            reference = f"example/channelwatch@{child_digest}"
            return SimpleNamespace(
                stdout=json.dumps(
                    [
                        {
                            "Os": "linux",
                            "Architecture": "amd64",
                            "Id": image_id,
                            "RepoDigests": [reference],
                        }
                    ]
                )
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(verifier, "run", fake_run)
    monkeypatch.setattr(verifier, "run_bytes", lambda *_args, **_kwargs: raw_index)
    assert verifier.pinned_native_image(
        "example/channelwatch", index_digest, "amd64"
    ) == (
        f"example/channelwatch@{child_digest}",
        child_digest,
        image_id,
        base64.b64encode(raw_index).decode("ascii"),
        {
            "os": "linux",
            "architecture": "amd64",
            "repo_digests": [f"example/channelwatch@{child_digest}"],
        },
    )
    assert ["--platform", "linux/amd64"] == calls[0][2:4]


def test_pinned_image_rejects_index_bytes_that_do_not_match_pinned_digest(monkeypatch):
    verifier = _load()
    monkeypatch.setattr(verifier, "run_bytes", lambda *_args, **_kwargs: b'{"manifests":[]}')

    with pytest.raises(verifier.CapabilityError, match="index bytes do not match"):
        verifier.pinned_native_image(
            "example/channelwatch", "sha256:" + "1" * 64, "amd64"
        )


def test_update_check_binds_exact_signed_runtime_target():
    verifier = _load()
    descriptor = _descriptor()
    bundle_sha256 = "c" * 64
    checked = {
        "update_available": True,
        "image_required": False,
        "catalog_state": "update_available",
        "trusted_target": {
            "version": "1.5.2",
            "runtime_descriptor": descriptor,
            "bundle_sha256": bundle_sha256,
        },
    }
    assert verifier.validate_update_check(
        checked,
        version="1.5.2",
        descriptor=descriptor,
        bundle_sha256=bundle_sha256,
    ) == checked

    for mutation in (
        lambda value: value.update(update_available=False),
        lambda value: value["trusted_target"].update(runtime_descriptor={}),
        lambda value: value["trusted_target"].update(bundle_sha256="d" * 64),
    ):
        altered = json.loads(json.dumps(checked))
        mutation(altered)
        with pytest.raises(verifier.CapabilityError, match="signed runtime target"):
            verifier.validate_update_check(
                altered,
                version="1.5.2",
                descriptor=descriptor,
                bundle_sha256=bundle_sha256,
            )


def test_rollback_response_and_status_are_strictly_bound():
    verifier = _load()
    job_id = "0123456789abcdef0123456789abcdef"
    response = {
        "operation": "rollback",
        "status": "restarting",
        "rolled_back_from": "1.5.2",
        "target": "1.5.0",
        "job_id": job_id,
    }
    status = {
        "current_version": "1.5.0",
        "launcher_protocol": 4,
        "active_bundle": None,
        "operation_busy": False,
        "worker_runtime": {
            "python_executable": "/venv/bin/python",
            "python_version": "3.14.8",
            "verified_generation": None,
        },
    }
    assert verifier.validate_rollback_response(
        response, source_version="1.5.2", target_version="1.5.0"
    ) == response
    assert verifier.validate_rollback_status(status, target_version="1.5.0") == status

    with pytest.raises(verifier.CapabilityError, match="rollback response"):
        verifier.validate_rollback_response(
            {**response, "job_id": "bad"},
            source_version="1.5.2",
            target_version="1.5.0",
        )
    with pytest.raises(verifier.CapabilityError, match="post-rollback status"):
        verifier.validate_rollback_status(
            {**status, "active_bundle": {}}, target_version="1.5.0"
        )
    with pytest.raises(verifier.CapabilityError, match="interpreter identity"):
        verifier.validate_rollback_status(
            {
                **status,
                "worker_runtime": {**status["worker_runtime"], "pid": 202},
            },
            target_version="1.5.0",
        )


def test_post_success_recovery_hold_is_not_misclassified_as_completed_rollback():
    verifier = _load()
    descriptor = _descriptor()
    bundle_sha256 = "c" * 64
    generation = "/config/channelwatch-runtime/releases/" + descriptor["generation_id"]
    successful_apply = {
        "job_id": "1" * 32,
        "operation": "apply",
        "status": "success",
        "version": "1.5.4",
        "bundle_sha256": bundle_sha256,
    }
    status = {
        "current_version": "1.5.3",
        "image_version": "1.5.3",
        "launcher_protocol": 4,
        "runtime_source": "image",
        "active_bundle": {
            "version": "1.5.4",
            "path": generation,
            "runtime_generation_id": descriptor["generation_id"],
            "runtime_descriptor_sha256": descriptor["generation_id"],
            "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
            "manifest": {"bundle_sha256": bundle_sha256},
        },
        "operation_busy": False,
        "rollback_available": True,
        "worker_runtime": {
            "python_executable": "/venv/bin/python",
            "python_version": "3.14.8",
            "verified_generation": None,
        },
        "last_job": copy.deepcopy(successful_apply),
    }
    validator = getattr(
        verifier,
        "validate_recovery_hold_status",
        lambda value, **_kwargs: verifier.validate_rollback_status(
            value, target_version="1.5.3"
        ),
    )

    assert validator(
        status,
        descriptor=descriptor,
        version="1.5.4",
        image_version="1.5.3",
        bundle_sha256=bundle_sha256,
        successful_apply_job=successful_apply,
    ) == status


@pytest.mark.parametrize(
    "mutation",
    [
        lambda status, _job: status.update(current_version="1.5.4"),
        lambda status, _job: status.update(runtime_source="selected_bundle"),
        lambda status, _job: status.update(operation_busy=True),
        lambda status, _job: status.update(rollback_available=False),
        lambda status, _job: status["active_bundle"].update(version="1.5.5"),
        lambda status, _job: status["active_bundle"].update(path="/tmp/runtime"),
        lambda status, _job: status["active_bundle"].update(runtime_generation_id="0" * 64),
        lambda status, _job: status["active_bundle"]["manifest"].update(bundle_sha256="0" * 64),
        lambda status, _job: status["worker_runtime"].update(python_executable="/tmp/python"),
        lambda status, job: job.update(status="failed"),
        lambda status, _job: status["last_job"].update(job_id="2" * 32),
    ],
)
def test_recovery_hold_status_rejects_identity_and_job_tampering(mutation):
    verifier = _load()
    descriptor = _descriptor()
    bundle_sha256 = "c" * 64
    generation = "/config/channelwatch-runtime/releases/" + descriptor["generation_id"]
    successful_apply = {
        "job_id": "1" * 32, "operation": "apply", "status": "success",
        "version": "1.5.4", "bundle_sha256": bundle_sha256,
    }
    status = {
        "current_version": "1.5.3", "image_version": "1.5.3",
        "launcher_protocol": 4, "runtime_source": "image",
        "active_bundle": {
            "version": "1.5.4", "path": generation,
            "runtime_generation_id": descriptor["generation_id"],
            "runtime_descriptor_sha256": descriptor["generation_id"],
            "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
            "manifest": {"bundle_sha256": bundle_sha256},
        },
        "operation_busy": False, "rollback_available": True,
        "worker_runtime": {
            "python_executable": "/venv/bin/python", "python_version": "3.14.8",
            "verified_generation": None,
        },
        "last_job": copy.deepcopy(successful_apply),
    }
    mutation(status, successful_apply)
    with pytest.raises(verifier.CapabilityError, match="recovery-hold|interpreter"):
        verifier.validate_recovery_hold_status(
            status,
            descriptor=descriptor,
            version="1.5.4",
            image_version="1.5.3",
            bundle_sha256=bundle_sha256,
            successful_apply_job=successful_apply,
        )


def test_terminal_rollback_is_strictly_bound():
    verifier = _load()
    apply_job = {"job_id": "1" * 32}
    response = {"job_id": "2" * 32}
    rollback_job = {
        "job_id": "2" * 32, "operation": "rollback", "status": "success",
        "version": "1.5.4", "rolled_back_from": "1.5.4",
        "restored_version": "1.5.3", "rollback_applied": True,
        "restart_required": False,
        "startup_components": {
            component: {
                "healthy": True, "version": "1.5.3",
                "ready_at": "2026-10-12T03:01:00Z",
            }
            for component in ("core", "ui")
        },
    }
    assert verifier.validate_completed_rollback_job(
        rollback_job,
        response=response,
        successful_apply_job=apply_job,
        source_version="1.5.4",
        image_version="1.5.3",
    ) == rollback_job
    for invalid in (
        {**rollback_job, "job_id": "3" * 32},
        {**rollback_job, "status": "restarting"},
        {**rollback_job, "restored_version": "1.5.4"},
        {**rollback_job, "startup_components": {"core": rollback_job["startup_components"]["core"]}},
        {
            **rollback_job,
            "startup_components": {
                **rollback_job["startup_components"],
                "ui": {**rollback_job["startup_components"]["ui"], "healthy": False},
            },
        },
    ):
        with pytest.raises(verifier.CapabilityError, match="Completed rollback"):
            verifier.validate_completed_rollback_job(
                invalid,
                response=response,
                successful_apply_job=apply_job,
                source_version="1.5.4",
                image_version="1.5.3",
            )
    with pytest.raises(verifier.CapabilityError, match="Completed rollback"):
        verifier.validate_completed_rollback_job(
            {**rollback_job, "job_id": apply_job["job_id"]},
            response={"job_id": apply_job["job_id"]},
            successful_apply_job=apply_job,
            source_version="1.5.4",
            image_version="1.5.3",
        )


def test_damaged_recovery_seals_hold_before_authenticated_explicit_rollback(
    tmp_path: Path, monkeypatch
):
    verifier = _load()
    descriptor = _descriptor()
    version = "1.5.4"
    image_version = "1.5.3"
    bundle_sha256 = "b" * 64
    generation = "/config/channelwatch-runtime/releases/" + descriptor["generation_id"]
    worker = {
        "python_version": "3.14.8", "python_executable": "/venv/bin/python",
        "verified_generation": None,
    }
    apply_job = {
        "job_id": "1" * 32, "operation": "apply", "status": "success",
        "version": version, "bundle_sha256": bundle_sha256,
    }
    active = {
        "version": version, "path": generation,
        "runtime_generation_id": descriptor["generation_id"],
        "runtime_descriptor_sha256": descriptor["generation_id"],
        "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
        "manifest": {"bundle_sha256": bundle_sha256},
    }
    hold_status = {
        "current_version": image_version, "image_version": image_version,
        "launcher_protocol": 4, "runtime_source": "image",
        "active_bundle": active, "operation_busy": False,
        "rollback_available": True, "worker_runtime": worker,
        "last_job": apply_job,
    }
    completed = {
        "job_id": "2" * 32, "operation": "rollback", "status": "success",
        "version": version, "rolled_back_from": version,
        "restored_version": image_version, "rollback_applied": True,
        "restart_required": False,
        "startup_components": {
            component: {
                "healthy": True, "version": image_version,
                "ready_at": "2026-10-12T03:01:00Z",
            }
            for component in ("core", "ui")
        },
    }
    rollback_status = {
        "current_version": image_version, "launcher_protocol": 4,
        "active_bundle": None, "operation_busy": False,
        "worker_runtime": worker, "last_job": completed,
    }
    marker = {
        "type": "file", "mode": "0600", "size": 100, "sha256": "a" * 64,
        "payload": {
            "schema": 1, "mode": "official_signed_only",
            "failed_version": version, "failed_bundle_sha256": bundle_sha256,
            "entered_at": "2026-10-12T03:00:00Z",
        },
    }
    control_hold = {
        "active.json": {**marker, "payload": active},
        "rollback.json": {**marker, "payload": {"target_version": version}},
        "update-job.json": {**marker, "payload": apply_job},
        "official-recovery-mode.json": marker,
    }
    control_rollback = {
        "active.json": {"type": "absent"},
        "rollback.json": control_hold["rollback.json"],
        "update-job.json": {**marker, "payload": completed},
        "official-recovery-mode.json": {"type": "absent"},
    }
    events = []

    class Resources:
        def create_volume(self, _kind):
            return "volume"

        def container(self, _kind):
            return "app"

    class Client:
        csrf = "authenticated"

        def __init__(self, *_args):
            self.rollback_requested = False

        def set_base_url(self, _url):
            pass

        def login(self):
            pass

        def request(self, path, payload=None):
            if path == "/api/v1/update/check":
                return {}
            if path == "/api/v1/update/status":
                return rollback_status if self.rollback_requested else hold_status
            if path == "/api/v1/update/rollback":
                assert self.csrf == "authenticated"
                events.append("rollback-request")
                self.rollback_requested = True
                return {
                    "job_id": completed["job_id"], "operation": "rollback",
                    "status": "restarting", "rolled_back_from": version,
                }
            raise AssertionError(path)

    client = Client()

    class Monitor:
        def __init__(self, *_args):
            pass

        def start(self):
            pass

        def stop(self):
            return {
                "successful_sample_count": 1, "sampling_error_count": 0,
                "restart_barrier_observed": True, "post_barrier_sample_count": 1,
                "forbidden_selected_pids": [],
            }

    original_retain = verifier.retain_recovery_diagnostic
    original_validate_hold = verifier.validate_recovery_hold_status

    def retain(*args, **kwargs):
        events.append("seal-" + kwargs["phase"])
        return original_retain(*args, **kwargs)

    def validate_hold(*args, **kwargs):
        events.append("validate-hold")
        return original_validate_hold(*args, **kwargs)

    monkeypatch.setattr(verifier, "ApiClient", lambda *_args: client)
    monkeypatch.setattr(verifier, "ForbiddenSelectedProcessMonitor", Monitor)
    monkeypatch.setattr(verifier, "seed_synthetic_rbac_settings", lambda *_args: None)
    monkeypatch.setattr(verifier, "start_probe_app", lambda **_kwargs: None)
    monkeypatch.setattr(verifier, "published_base_url", lambda *_args: "http://probe")
    monkeypatch.setattr(verifier, "wait_for_image_health", lambda *_args: None)
    monkeypatch.setattr(verifier, "capture_image_operational_state", lambda *_args, **_kwargs: {"phase": "image"})
    monkeypatch.setattr(verifier, "image_inventory", lambda *_args: {"inventory": True})
    monkeypatch.setattr(verifier, "persistent_hashes", lambda *_args: {"/config/settings.json": "c" * 64})
    monkeypatch.setattr(verifier, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="container-id\n"))
    monkeypatch.setattr(verifier, "validate_update_check", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda *_args: apply_job["job_id"])
    monkeypatch.setattr(verifier, "apply_once_and_wait", lambda *_args, **_kwargs: apply_job)
    monkeypatch.setattr(verifier, "capture_selected_operational_state", lambda *_args, **_kwargs: {"processes": {"core": {"pid": 10}, "ui": {"pid": 11}}})
    monkeypatch.setattr(verifier, "import_smoke", lambda *_args, **_kwargs: {
        "sqlalchemy": {"path": "/venv/lib/python3.14/site-packages/sqlalchemy/__init__.py", "version": "2.0.54"},
        "httptools": {"path": "/venv/lib/python3.14/site-packages/httptools/__init__.py", "version": "0.8.0"},
        "httptools.parser.parser": {"path": generation + "/dependencies/site-packages/httptools/parser/parser.so", "version": "0.8.0", "sha256": "d" * 64},
    })
    monkeypatch.setattr(verifier, "damage_native_file", lambda *_args, **_kwargs: {"damaged": True})
    monkeypatch.setattr(verifier, "wait_until", lambda callback, *_args: callback())
    monkeypatch.setattr(verifier, "recovery_control_state", lambda *_args: control_rollback if client.rollback_requested else control_hold)
    monkeypatch.setattr(verifier, "retain_recovery_diagnostic", retain)
    monkeypatch.setattr(verifier, "validate_recovery_hold_status", validate_hold)
    monkeypatch.setattr(verifier, "capture_recovery_hold_operational_state", lambda *_args, **_kwargs: {"phase": "hold"})
    monkeypatch.setattr(verifier, "selected_supervisor_processes", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(verifier, "wait_for_successful_job", lambda *_args, **_kwargs: completed)
    monkeypatch.setattr(verifier, "validate_image_inventory", lambda *_args: None)
    monkeypatch.setattr(verifier, "validate_preservation", lambda *_args: None)
    monkeypatch.setattr(verifier, "inventory_summary", lambda value: value)

    result = verifier.damaged_native_recovery(
        resources=Resources(), reference="image", architecture="amd64",
        network="network", ca_path=tmp_path / "ca.pem", descriptor=descriptor,
        bundle_sha256=bundle_sha256, inventory={
            "packages": {"sqlalchemy": {"version": "2.0.54"}, "httptools": {"version": "0.8.0"}},
            "httptools_parser": {"path": "dependencies/site-packages/httptools/parser/parser.so", "sha256": "d" * 64},
        },
        version=version, image_version=image_version, startup_timeout=5,
        update_timeout=5, evidence_json=tmp_path / "runtime-capability-amd64.json",
    )

    assert events == [
        "seal-recovery-hold", "validate-hold", "rollback-request",
        "seal-post-rollback",
    ]
    assert result["successful_apply_job"] == apply_job
    assert result["completed_rollback_job"] == completed
    assert result["recovery_marker"] == marker
    assert result["post_rollback_control_state"]["official-recovery-mode.json"] == {"type": "absent"}

    events.clear()
    client.rollback_requested = False

    def reject_hold(*_args, **_kwargs):
        events.append("validate-hold")
        raise verifier.CapabilityError("invalid retained hold")

    monkeypatch.setattr(verifier, "validate_recovery_hold_status", reject_hold)
    with pytest.raises(verifier.CapabilityError, match="invalid retained hold"):
        verifier.damaged_native_recovery(
            resources=Resources(), reference="image", architecture="amd64",
            network="network", ca_path=tmp_path / "ca.pem", descriptor=descriptor,
            bundle_sha256=bundle_sha256, inventory={
                "packages": {"sqlalchemy": {"version": "2.0.54"}, "httptools": {"version": "0.8.0"}},
                "httptools_parser": {"path": "dependencies/site-packages/httptools/parser/parser.so", "sha256": "d" * 64},
            },
            version=version, image_version=image_version, startup_timeout=5,
            update_timeout=5, evidence_json=tmp_path / "runtime-capability-amd64.json",
        )
    assert events == ["seal-recovery-hold", "validate-hold"]
    assert client.rollback_requested is False


def test_image_process_evidence_cross_binds_commands_pids_and_services(monkeypatch):
    verifier = _load()
    commands = {
        "core": "/venv/bin/python -u /app/core/runtime_launcher.py core --stay-alive",
        "ui": (
            "/venv/bin/python -u /app/core/runtime_launcher.py ui "
            "--host 0.0.0.0 --port 8501 --log-level warning"
        ),
    }

    def fake_run(command, **_kwargs):
        if command[1:3] == ["top", "app"] and command[-1] == "args":
            return SimpleNamespace(stdout="COMMAND\n" + "\n".join(commands.values()) + "\n")
        if command[1:3] == ["top", "app"] and command[-1] == "pid,args":
            return SimpleNamespace(
                stdout=(
                    "PID COMMAND\n"
                    f"901 {commands['core']}\n"
                    f"902 {commands['ui']}\n"
                )
            )
        if command[:4] == ["docker", "exec", "app", "/usr/local/bin/python"]:
            assert command[4:8] == ["-I", "-B", "-S", "-c"]
            assert "/proc" in command[-1]
            return SimpleNamespace(stdout=json.dumps({
                name: {"pid": pid, "command": commands[name]}
                for name, pid in (("core", 101), ("ui", 202))
            }))
        return SimpleNamespace(
            stdout=(
                "core RUNNING pid 101, uptime 0:01:00\n"
                "ui RUNNING pid 202, uptime 0:01:00\n"
            )
        )

    monkeypatch.setattr(verifier, "run", fake_run)
    services = verifier.image_supervisor_services("app")
    actual_commands = verifier.image_supervisor_commands("app", services)
    processes = verifier.image_supervisor_processes("app", services)
    assert processes["core"]["pid"] == 101
    assert processes["ui"]["pid"] == 202
    verifier.validate_image_process_evidence(actual_commands, processes, services)

    altered = json.loads(json.dumps(services))
    altered["ui"]["pid"] = 101
    with pytest.raises(verifier.CapabilityError, match="do not agree"):
        verifier.validate_image_process_evidence(actual_commands, processes, altered)


def _execute_image_launcher_scanner(monkeypatch, verifier, services, mutation=None):
    command_lines = {
        1: [
            "/venv/bin/python",
            "-m",
            "supervisor.supervisord",
            "-c",
            "/tmp/supervisord.conf",
        ],
        101: ["python", "-u", "/app/core/runtime_launcher.py", "core", "--stay-alive"],
        202: [
            "python", "-u", "/app/core/runtime_launcher.py", "ui", "--host",
            "0.0.0.0", "--port", "8501", "--log-level", "warning",
        ],
    }
    identities = {
        1: {"pid": 1, "ppid": 0, "uids": [1000] * 4},
        101: {"pid": 101, "ppid": 1, "uids": [1000] * 4},
        202: {"pid": 202, "ppid": 1, "uids": [1000] * 4},
    }
    missing_pid = None
    if mutation == "argv0":
        command_lines[101][0] = "SECRET_ARG_VALUE"
    elif mutation == "suffix":
        command_lines[101].append("SECRET_ARG_VALUE")
    elif mutation == "parent":
        identities[101]["ppid"] = 9
    elif mutation == "pid":
        identities[101]["pid"] = 303
    elif mutation == "uid":
        identities[101]["uids"][2] = 0
    elif mutation == "missing":
        missing_pid = 101

    class FakePath:
        def __init__(self, value):
            self.value = str(value)

        def __truediv__(self, value):
            return FakePath(f"{self.value.rstrip('/')}/{value}")

        def __str__(self):
            return self.value

        @property
        def name(self):
            return self.value.rsplit("/", 1)[-1]

        def iterdir(self):
            assert self.value == "/proc"
            return [FakePath("/proc/1"), FakePath("/proc/101"), FakePath("/proc/202")]

        def __eq__(self, other):
            return isinstance(other, FakePath) and self.value == other.value

        def resolve(self, *, strict):
            assert strict is True
            if self.value == "/venv/bin/python":
                return FakePath("/usr/local/bin/python3.14")
            if self.value.endswith("/exe"):
                raise PermissionError("ptrace access denied")
            return self

        def read_bytes(self):
            pid = int(self.value.split("/")[2])
            if pid == missing_pid:
                raise FileNotFoundError
            return b"\0".join(value.encode() for value in command_lines[pid]) + b"\0"

        def read_text(self, *, encoding):
            assert encoding == "utf-8"
            pid = int(self.value.split("/")[2])
            identity = identities[pid]
            return (
                "Name:\tpython\n"
                f"Pid:\t{identity['pid']}\n"
                f"PPid:\t{identity['ppid']}\n"
                "Uid:\t" + "\t".join(str(value) for value in identity["uids"]) + "\n"
            )

    def execute_scanner(_name, code):
        monkeypatch.setattr(pathlib, "Path", FakePath)
        output = StringIO()
        try:
            with redirect_stdout(output):
                exec(compile(code, "<image-launcher-scan>", "exec"), {})
        except SystemExit as exc:
            raise verifier.CapabilityError(f"docker failed: {exc}") from None
        return json.loads(output.getvalue())

    monkeypatch.setattr(verifier, "docker_exec_json", execute_scanner)
    return verifier.image_supervisor_processes("app", services)


def test_image_launcher_scanner_accepts_permission_denied_exe_for_exact_supervisor_pids(
    monkeypatch,
):
    verifier = _load()
    services = {
        "core": {"status": "RUNNING", "pid": 101, "uptime": "0:01:00"},
        "ui": {"status": "RUNNING", "pid": 202, "uptime": "0:01:00"},
    }
    processes = _execute_image_launcher_scanner(
        monkeypatch, verifier, services
    )
    assert processes == {
        "core": {
            "pid": 101,
            "command": "/venv/bin/python -u /app/core/runtime_launcher.py core --stay-alive",
        },
        "ui": {
            "pid": 202,
            "command": (
                "/venv/bin/python -u /app/core/runtime_launcher.py ui "
                "--host 0.0.0.0 --port 8501 --log-level warning"
            ),
        },
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("argv0", "argv0"),
        ("suffix", "argv_suffix"),
        ("pid", "pid"),
        ("parent", "parent_pid"),
        ("uid", "uids"),
        ("missing", "process_missing"),
    ],
)
def test_image_launcher_scanner_rejects_sanitized_identity_mismatches(
    monkeypatch, mutation, reason
):
    verifier = _load()
    services = {
        "core": {"status": "RUNNING", "pid": 101, "uptime": "0:01:00"},
        "ui": {"status": "RUNNING", "pid": 202, "uptime": "0:01:00"},
    }
    with pytest.raises(verifier.CapabilityError, match=reason) as error:
        _execute_image_launcher_scanner(
            monkeypatch, verifier, services, mutation=mutation
        )
    assert "SECRET_ARG_VALUE" not in str(error.value)


@pytest.mark.parametrize(
    "services",
    [
        {"core": {"status": "RUNNING", "pid": 101, "uptime": "1m"}},
        {
            "core": {"status": "RUNNING", "pid": 101, "uptime": "1m"},
            "ui": {"status": "RUNNING", "pid": 101, "uptime": "1m"},
        },
        {
            "core": {"status": "RUNNING", "pid": 101, "uptime": "1m"},
            "ui": {"status": "STARTING", "pid": 202, "uptime": "1m"},
        },
        {
            "core": {"status": "RUNNING", "pid": 0, "uptime": "1m"},
            "ui": {"status": "RUNNING", "pid": 202, "uptime": "1m"},
        },
    ],
)
def test_image_launcher_scanner_rejects_missing_duplicate_or_unknown_services(services):
    verifier = _load()
    with pytest.raises(verifier.CapabilityError, match="Supervisor service evidence"):
        verifier.image_supervisor_processes("app", services)


def test_image_operational_state_keeps_frozen_status_separate_from_launcher_pids(monkeypatch):
    verifier = _load()
    status_worker = {
        "python_version": "3.14.8",
        "python_executable": "/venv/bin/python",
        "verified_generation": None,
    }
    status = {
        "current_version": "1.5.3",
        "launcher_protocol": 4,
        "active_bundle": None,
        "operation_busy": False,
        "worker_runtime": status_worker,
    }
    commands = {
        "core": "/venv/bin/python -u /app/core/runtime_launcher.py core --stay-alive",
        "ui": (
            "/venv/bin/python -u /app/core/runtime_launcher.py ui "
            "--host 0.0.0.0 --port 8501 --log-level warning"
        ),
    }
    processes = {
        component: {"pid": pid, "command": commands[component]}
        for component, pid in (("core", 101), ("ui", 202))
    }
    services = {
        component: {"status": "RUNNING", "pid": process["pid"], "uptime": "0:01:00"}
        for component, process in processes.items()
    }
    order = []
    monkeypatch.setattr(
        verifier,
        "image_supervisor_processes",
        lambda _name, actual_services: order.append("processes") or (
            processes if actual_services == services else None
        ),
    )
    monkeypatch.setattr(
        verifier,
        "image_supervisor_services",
        lambda *_args: order.append("services") or services,
    )
    monkeypatch.setattr(
        verifier,
        "image_health",
        lambda *_args: {
            "startup": {"status": "ready"},
            "ready": {"status": "ready", "ready": True},
        },
    )
    evidence = verifier.capture_image_operational_state(
        "app",
        SimpleNamespace(request=lambda _path: status),
        image_version="1.5.3",
        require_idle=True,
    )
    assert evidence["status_worker_scope"] == "frozen_image_ui_status_interpreter_only"
    assert evidence["status_worker"] == status_worker
    assert "pid" not in evidence["status_worker"]
    assert evidence["processes"]["ui"]["pid"] == 202
    assert order == ["services", "processes"]


def test_container_process_scanners_are_valid_and_retain_parent_identity(monkeypatch):
    verifier = _load()
    launcher_commands = {
        "core": "/venv/bin/python -u /app/core/runtime_launcher.py core --stay-alive",
        "ui": (
            "/venv/bin/python -u /app/core/runtime_launcher.py ui "
            "--host 0.0.0.0 --port 8501 --log-level warning"
        ),
    }
    responses = iter(
        (
            {
                component: {
                    "pid": pid,
                    "ppid": pid + 100,
                    "command": f"/runtime/python worker --mode {component}",
                }
                for component, pid in (("core", 101), ("ui", 202))
            },
            {
                component: {"pid": pid, "command": launcher_commands[component]}
                for component, pid in (("core", 301), ("ui", 302))
            },
        )
    )

    def fake_exec(_name, code):
        compile(code, "<container-process-scan>", "exec")
        return next(responses)

    monkeypatch.setattr(verifier, "docker_exec_json", fake_exec)
    workers = verifier.worker_processes("app", python_executable="/runtime/python")
    assert workers["ui"]["ppid"] == 302
    services = {
        component: {"status": "RUNNING", "pid": pid, "uptime": "0:01:00"}
        for component, pid in (("core", 301), ("ui", 302))
    }
    assert verifier.image_supervisor_processes("app", services)["core"]["command"] == launcher_commands["core"]


def test_image_runtime_info_accepts_exact_frozen_five_module_diagnostics(monkeypatch):
    verifier = _load()
    dependencies = {
        "requests": {
            "loaded": True,
            "path": "/venv/lib/python3.14/site-packages/requests/__init__.py",
            "version": "2.32.5",
        },
        "cryptography": {
            "loaded": True,
            "path": "/venv/lib/python3.14/site-packages/cryptography/__init__.py",
            "version": "46.0.3",
        },
        "sqlmodel": {"loaded": False},
        "uvicorn": {
            "loaded": True,
            "path": "/venv/lib/python3.14/site-packages/uvicorn/__init__.py",
            "version": "0.40.0",
        },
        "pydantic_core": {
            "loaded": True,
            "path": "/venv/lib/python3.14/site-packages/pydantic_core/__init__.py",
            "version": "",
        },
    }
    payload = {
        "diagnostic_source": "image",
        "application_dir": "/app",
        "application_version": "1.5.3",
        "process_runtime": {
            "pid": 303,
            "python_executable": "/venv/bin/python",
            "python_version": "3.14.8",
            "python_prefix": "/venv",
            "dependencies": dependencies,
        },
        "verified_generation": None,
    }
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(payload, indent=2, sort_keys=True) + "\n"
        ),
    )
    assert verifier.runtime_info("app", image_version="1.5.3") == payload

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )
    assert verifier.runtime_info("app", image_version="1.5.3") == payload

    for stdout in ("", "}", "log line\n" + json.dumps(payload), json.dumps(payload) + "\n{}"):
        monkeypatch.setattr(
            verifier,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(stdout=stdout),
        )
        with pytest.raises(verifier.CapabilityError, match="did not return JSON"):
            verifier.runtime_info("app", image_version="1.5.3")

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(payload, indent=2, sort_keys=True) + "\n"
        ),
    )
    dependencies["sqlalchemy"] = {"loaded": False}
    with pytest.raises(verifier.CapabilityError, match="process evidence"):
        verifier.runtime_info("app", image_version="1.5.3")


def test_probe_tmpfs_ownership_matches_requested_runtime_user(monkeypatch, tmp_path):
    verifier = _load()
    commands = []
    monkeypatch.setattr(verifier, "run", lambda command, **_kwargs: commands.append(command))
    verifier.start_probe_app(
        name="probe", reference="sha256:" + "a" * 64, architecture="amd64",
        network="probe-network", volume="probe-volume", ca_path=tmp_path / "ca.pem",
        username="probe-user", password="ephemeral-test-password",
        tmpfs="rw,noexec,size=100000,mode=0755,uid=1000,gid=1000",
    )
    assert "PUID=1000" in commands[0]
    assert "PGID=1000" in commands[0]


def test_image_health_requires_exact_startup_and_readiness_records():
    verifier = _load()
    records = {
        "/healthz/startup": {"status": "ready"},
        "/healthz/ready": {"status": "ready", "ready": True},
    }
    client = SimpleNamespace(request=lambda path: records[path])
    assert verifier.image_health(client) == {
        "startup": records["/healthz/startup"],
        "ready": records["/healthz/ready"],
    }

    records["/healthz/ready"] = {"status": "ready", "ready": True, "extra": True}
    with pytest.raises(verifier.CapabilityError, match="readiness checks"):
        verifier.image_health(client)


def test_native_log_hashes_require_exact_nonempty_app_and_feed():
    verifier = _load()
    hashes = {"app": "a" * 64, "feed": "b" * 64}
    assert verifier.validate_log_hashes(hashes) == hashes

    for invalid in ({"app": "a" * 64}, {**hashes, "extra": "c" * 64}, {**hashes, "feed": ""}):
        with pytest.raises(verifier.CapabilityError, match="app and feed"):
            verifier.validate_log_hashes(invalid)


def test_docker_cleanup_collects_inspect_remove_and_ownership_failures(monkeypatch):
    verifier = _load()
    resources = verifier.DockerResources("cap")
    resources.containers.update({"cap-inspect", "cap-remove"})
    resources.volumes.add("cap-volume")
    resources.networks.add("cap-network")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        name = command[-1]
        if name == "cap-volume":
            raise RuntimeError("inspect unavailable")
        if "inspect" in command:
            if name == "cap-inspect":
                return SimpleNamespace(returncode=1, stdout="", stderr="missing")
            if name == "cap-network":
                return SimpleNamespace(returncode=0, stdout="someone-else\n", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout="channelwatch-runtime-capability\n",
                stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="remove failed")

    monkeypatch.setattr(verifier, "run", fake_run)
    assert resources.cleanup() == [
        "container:cap-inspect:inspect_failed",
        "container:cap-remove:remove_failed",
        "volume:cap-volume:inspect_error",
        "network:cap-network:ownership_mismatch",
    ]
    assert not any(command[-1] in {"cap-inspect", "cap-volume", "cap-network"} and "rm" in command for command in commands)


def test_log_capture_reports_each_failed_container_without_dropping_success(monkeypatch, tmp_path):
    verifier = _load()
    resources = verifier.DockerResources("cap")
    resources.containers.update({"cap-app", "cap-feed", "cap-damaged-native"})

    def fake_run(command, **_kwargs):
        name = command[-1]
        if name == "cap-app":
            return SimpleNamespace(returncode=1, stdout="", stderr="capture failed")
        if name == "cap-damaged-native":
            raise RuntimeError("capture unavailable")
        return SimpleNamespace(returncode=0, stdout="feed log\n", stderr="")

    monkeypatch.setattr(verifier, "run", fake_run)
    lifecycle, release, artifacts, failures = verifier.capture_container_logs(
        resources,
        prefix="cap",
        app="cap-app",
        feed="cap-feed",
        evidence_json=tmp_path / "receipt.json",
    )
    assert set(lifecycle) == {"feed"}
    assert release == {}
    assert artifacts == {}
    assert failures == [
        "docker_logs:app:capture_failed",
        "docker_logs:damaged-native:capture_error",
    ]


def test_secondary_failures_preserve_primary_and_fail_successful_receipt(monkeypatch, tmp_path):
    verifier = _load()
    primary = {
        "status": "failed",
        "failure": "original lifecycle failure",
        "architecture": {"status": "running"},
    }
    verifier.apply_secondary_failures(primary, ["docker_logs:app:capture_failed"])
    assert primary["failure"] == "original lifecycle failure"
    assert primary["secondary_failures"] == ["docker_logs:app:capture_failed"]

    successful = {"status": "passed", "architecture": {"status": "passed"}}
    verifier.apply_secondary_failures(
        successful,
        ["container:cap-app:remove_failed", "container:cap-app:remove_failed"],
    )
    assert successful == {
        "status": "failed",
        "failure": "Runtime capability evidence finalization failed.",
        "secondary_failures": ["container:cap-app:remove_failed"],
        "architecture": {"status": "failed"},
    }

    temp_root = tmp_path / "temporary-secrets"
    temp_root.mkdir()
    monkeypatch.setattr(verifier.shutil, "rmtree", lambda _path: None)
    assert verifier.remove_temp_root(temp_root) == ["temp_root:remove_failed"]
    monkeypatch.setattr(
        verifier.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("locked")),
    )
    assert verifier.remove_temp_root(temp_root) == ["temp_root:remove_error"]


def test_selected_operational_state_cross_binds_live_pids_status_and_health(monkeypatch):
    verifier = _load()
    descriptor = _descriptor()
    inventory = _inventory()
    components = _components()
    generation = "/config/channelwatch-runtime/releases/" + "a" * 64
    processes = {
        component: {
            "pid": record["process_runtime"]["pid"],
            "ppid": 301 if component == "core" else 302,
            "command": f"{generation}/environment/bin/python worker --mode {component}",
        }
        for component, record in components.items()
    }
    commands = {component: record["command"] for component, record in processes.items()}
    services = {
        component: {
            "status": "RUNNING",
            "pid": record["ppid"],
            "uptime": "0:01:00",
        }
        for component, record in processes.items()
    }
    launchers = {
        component: {
            "pid": processes[component]["ppid"],
            "command": (
                "/venv/bin/python -u /app/core/runtime_launcher.py core --stay-alive"
                if component == "core"
                else "/venv/bin/python -u /app/core/runtime_launcher.py ui "
                "--host 0.0.0.0 --port 8501 --log-level warning"
            ),
        }
        for component in ("core", "ui")
    }
    status = {
        "current_version": "1.5.4",
        "active_bundle": {
            "path": generation,
            "runtime_generation_id": "a" * 64,
            "runtime_descriptor_sha256": "a" * 64,
            "dependency_inventory_sha256": "b" * 64,
        },
        "worker_runtime": {
            **copy.deepcopy(components["ui"]["process_runtime"]),
            "verified_generation": {
                "path": generation,
                "runtime_generation_id": "a" * 64,
                "runtime_descriptor_sha256": "a" * 64,
                "dependency_inventory_sha256": "b" * 64,
            },
        },
    }
    monkeypatch.setattr(verifier, "selected_supervisor_processes", lambda *_args, **_kwargs: processes)
    order = []
    monkeypatch.setattr(
        verifier,
        "image_supervisor_processes",
        lambda _name, actual_services: order.append("launchers") or (
            launchers if actual_services == services else None
        ),
    )
    monkeypatch.setattr(
        verifier,
        "image_supervisor_services",
        lambda *_args: order.append("services") or services,
    )
    monkeypatch.setattr(
        verifier,
        "image_health",
        lambda *_args: {
            "startup": {"status": "ready"},
            "ready": {"status": "ready", "ready": True},
        },
    )
    client = SimpleNamespace(request=lambda _path: status)

    evidence = verifier.capture_selected_operational_state(
        "app",
        client,
        descriptor=descriptor,
        inventory=inventory,
        version="1.5.4",
        completed_job={"runtime_components": components},
    )
    assert evidence["processes"] == processes
    assert evidence["launchers"] == launchers
    assert evidence["status_worker"]["pid"] == processes["ui"]["pid"]
    assert order[:2] == ["services", "launchers"]

    for invalid_path in (
        generation + "/app",
        generation + "/../" + "a" * 64,
        generation + "-other",
    ):
        invalid_status = copy.deepcopy(status)
        invalid_status["active_bundle"]["path"] = invalid_path
        with pytest.raises(verifier.CapabilityError, match="not bound"):
            verifier.validate_selected_status(
                invalid_status,
                descriptor=descriptor,
                inventory=inventory,
                version="1.5.4",
                ui_pid=processes["ui"]["pid"],
            )

    mismatched_launchers = copy.deepcopy(launchers)
    mismatched_launchers["ui"]["pid"] += 1
    with pytest.raises(verifier.CapabilityError, match="do not agree"):
        verifier.validate_selected_process_evidence(
            commands, processes, mismatched_launchers, services
        )

    components["core"]["process_runtime"]["pid"] = 999
    with pytest.raises(verifier.CapabilityError, match="stale"):
        verifier.capture_selected_operational_state(
            "app",
            client,
            descriptor=descriptor,
            inventory=inventory,
            version="1.5.4",
            completed_job={"runtime_components": components},
        )


def test_selected_operational_state_uses_live_worker_commands_without_docker_top(monkeypatch):
    verifier = _load()
    descriptor = _descriptor()
    generation = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
    processes = {
        component: {
            "pid": 201 + index,
            "ppid": 301 + index,
            "command": f"{generation}/environment/bin/python worker --mode {component}",
        }
        for index, component in enumerate(("core", "ui"))
    }
    services = {
        component: {"status": "RUNNING", "pid": process["ppid"], "uptime": "0:01:00"}
        for component, process in processes.items()
    }
    launchers = {
        component: {"pid": process["ppid"]}
        for component, process in processes.items()
    }

    def reject_docker_top(command, **_kwargs):
        assert command[:2] == ["docker", "top"]
        raise verifier.CapabilityError(
            "docker failed: Error response from daemon: Couldn't find PID field in ps output"
        )

    monkeypatch.setattr(verifier, "run", reject_docker_top)
    monkeypatch.setattr(verifier, "selected_supervisor_processes", lambda *_args: processes)
    monkeypatch.setattr(verifier, "image_supervisor_services", lambda *_args: services)
    monkeypatch.setattr(verifier, "image_supervisor_processes", lambda *_args: launchers)
    monkeypatch.setattr(verifier, "validate_selected_status", lambda *_args, **_kwargs: {
        "worker_runtime": {"pid": processes["ui"]["pid"]},
    })
    monkeypatch.setattr(verifier, "image_health", lambda *_args: {
        "startup": {"status": "ready"},
        "ready": {"status": "ready", "ready": True},
    })

    evidence = verifier.capture_selected_operational_state(
        "app",
        SimpleNamespace(request=lambda _path: {}),
        descriptor=descriptor,
        inventory=_inventory(),
        version="1.5.4",
        completed_job=None,
    )
    assert evidence["commands"] == {
        component: process["command"] for component, process in processes.items()
    }
    assert evidence["processes"] == processes
    assert evidence["services"] == services
    assert evidence["launchers"] == launchers


def test_damage_and_refusal_terminal_jobs_require_exact_failure_causes():
    verifier = _load()
    marker = {
        "type": "file", "mode": "0600", "size": 100, "sha256": "a" * 64,
        "payload": {
            "schema": 1, "mode": "official_signed_only",
            "failed_version": "1.5.4", "failed_bundle_sha256": "b" * 64,
            "entered_at": "2026-10-12T03:00:00Z",
        },
    }
    assert verifier.validate_recovery_marker(
        marker, version="1.5.4", bundle_sha256="b" * 64
    ) == marker
    for invalid in (
        {**marker, "type": "symlink"},
        {**marker, "payload": {**marker["payload"], "schema": True}},
        {**marker, "payload": {**marker["payload"], "mode": "automatic"}},
        {**marker, "payload": {**marker["payload"], "failed_bundle_sha256": "c" * 64}},
        {**marker, "payload": {**marker["payload"], "entered_at": "invalid"}},
    ):
        with pytest.raises(verifier.CapabilityError, match="recovery marker"):
            verifier.validate_recovery_marker(
                invalid, version="1.5.4", bundle_sha256="b" * 64
            )

    refusal = {
        "job_id": "c" * 32,
        "operation": "apply",
        "status": "failed",
        "version": "1.5.4",
        "bundle_sha256": "d" * 64,
        "rollback_applied": False,
        "restart_required": False,
        "error": "Runtime storage does not permit execution.",
    }
    assert verifier.validate_refusal_terminal_job(
        refusal,
        kind="noexec",
        job_id="c" * 32,
        version="1.5.4",
        bundle_sha256="d" * 64,
    ) == refusal
    with pytest.raises(verifier.CapabilityError, match="pre-handoff"):
        verifier.validate_refusal_terminal_job(
            {**refusal, "rollback_applied": True},
            kind="noexec",
            job_id="c" * 32,
            version="1.5.4",
            bundle_sha256="d" * 64,
        )


def test_live_ui_resource_log_and_receipt_gates_fail_closed(tmp_path: Path):
    verifier = _load()
    screenshot_records = {}
    for stage in ("selected", "rollback"):
        path = tmp_path / f"runtime-update-{stage}.png"
        path.write_bytes(stage.encode())
        screenshot_records[stage] = verifier.artifact_descriptor(path)
    value = {
        "status": "passed",
        "apply_requests": 1,
        "rollback_requests": 1,
        "target_version": "1.5.4",
        "final_version": "1.5.3",
        "final_active_bundle": None,
        "observed_progress": True,
        "console_errors": [],
        "page_errors": [],
        "final_health": {
            "startup": {"status": "ready"},
            "ready": {"status": "ready", "ready": True},
        },
        "page_url": "http://127.0.0.1:1234/#settings:updates",
        "page_title": "ChannelWatch",
        "screenshots": screenshot_records,
    }
    assert verifier.validate_live_ui_evidence(
        value,
        target_version="1.5.4",
        image_version="1.5.3",
        base_url="http://127.0.0.1:1234",
        evidence_root=tmp_path,
    )["screenshots"] == screenshot_records
    assert verifier.validate_same_container_identity("container-a", "container-a", "Live UI") == "container-a"
    with pytest.raises(verifier.CapabilityError, match="replaced its container"):
        verifier.validate_same_container_identity("container-a", "container-b", "Live UI")
    with pytest.raises(verifier.CapabilityError, match="exact lifecycle"):
        verifier.validate_live_ui_evidence(
            {**value, "apply_requests": 2},
            target_version="1.5.4",
            image_version="1.5.3",
            base_url="http://127.0.0.1:1234",
            evidence_root=tmp_path,
        )

    observations = {
        "bundle_bytes": 1, "expanded_bytes": 2, "member_count": 3,
        "selected_generation_disk_bytes": 4, "free_bytes_before": 5,
        "free_bytes_after": 6, "sample_count": 7,
        "peak_container_memory_bytes": 8, "preparation_duration_seconds": 0.1,
        "restart_duration_seconds": 0.2, "rollback_duration_seconds": 0.3,
        "reapply_duration_seconds": 0.4,
    }
    assert verifier.validate_resource_observations(observations) == observations
    with pytest.raises(verifier.CapabilityError, match="not positive"):
        verifier.validate_resource_observations({**observations, "sample_count": 0})

    hashes = {suffix: "e" * 64 for suffix in ("damaged-native", "noexec", "low-space", "live-ui")}
    artifacts = {
        suffix: {"file": f"{suffix}.log", "sha256": digest, "size": 1}
        for suffix, digest in hashes.items()
    }
    verifier.validate_release_log_artifacts(artifacts, hashes, architecture="amd64")
    with pytest.raises(verifier.CapabilityError, match="missing"):
        verifier.validate_release_log_artifacts(
            {key: value for key, value in artifacts.items() if key != "low-space"},
            hashes,
            architecture="amd64",
        )

    receipt = {"schema": 2, "status": "passed"}
    encoded = verifier.serialize_bounded_receipt(receipt)
    assert receipt["serialized_size_bytes"] == len(encoded.encode())
    with pytest.raises(verifier.CapabilityError, match="3.5 MiB"):
        verifier.serialize_bounded_receipt(
            {"schema": 2, "status": "passed", "blob": "x" * verifier.MAX_RECEIPT_BYTES}
        )


def test_control_state_rejects_unsafe_or_transient_restart_objects(monkeypatch):
    verifier = _load()
    names = {
        "active.json", "rollback.json", "restart-required.json",
        "restart-required.lock", "restart-services-accepted.json",
        "activation-pending.json", "activation-core-ready.json",
        "activation-ui-ready.json",
    }
    state = {name: {"type": "absent"} for name in names}
    monkeypatch.setattr(verifier, "docker_exec_json", lambda *_args: state)
    assert verifier.control_state("probe") == state
    verifier.validate_no_restart_transients(state)

    unsafe = copy.deepcopy(state)
    unsafe["active.json"] = {"type": "symlink"}
    monkeypatch.setattr(verifier, "docker_exec_json", lambda *_args: unsafe)
    with pytest.raises(verifier.CapabilityError, match="unsafe"):
        verifier.control_state("probe")

    transient = copy.deepcopy(state)
    transient["restart-required.json"] = {
        "type": "file", "mode": "0600", "size": 2, "sha256": "f" * 64,
    }
    with pytest.raises(verifier.CapabilityError, match="transient"):
        verifier.validate_no_restart_transients(transient)


def test_recovery_control_and_raw_status_diagnostic_are_retained_safely(
    tmp_path: Path, monkeypatch
):
    verifier = _load()
    names = {
        "active.json", "rollback.json", "update-job.json",
        "official-recovery-mode.json",
    }
    control = {
        name: {
            "type": "file", "mode": "0600", "size": 10,
            "sha256": hashlib.sha256(name.encode()).hexdigest(),
            "payload": {"name": name},
        }
        for name in names
    }
    monkeypatch.setattr(verifier, "docker_exec_json", lambda *_args: control)
    assert verifier.recovery_control_state("probe") == control

    evidence = tmp_path / "runtime-capability-amd64.json"
    descriptor = verifier.retain_recovery_diagnostic(
        evidence,
        phase="recovery-hold",
        status={"current_version": "1.5.3", "active_bundle": {"version": "1.5.4"}},
        control=control,
    )
    retained = json.loads((tmp_path / descriptor["file"]).read_text(encoding="utf-8"))
    assert retained["phase"] == "recovery-hold"
    assert retained["status"]["active_bundle"]["version"] == "1.5.4"
    assert retained["control"] == control

    unsafe = copy.deepcopy(control)
    unsafe["official-recovery-mode.json"] = {
        "type": "symlink", "mode": "0777", "size": 10,
        "sha256": "a" * 64, "payload": {},
    }
    monkeypatch.setattr(verifier, "docker_exec_json", lambda *_args: unsafe)
    with pytest.raises(verifier.CapabilityError, match="unsafe"):
        verifier.recovery_control_state("probe")


def test_recovery_control_script_reads_regular_files_through_bounded_fds(
    tmp_path: Path
):
    verifier = _load()
    names = {
        "active.json", "rollback.json", "update-job.json",
        "official-recovery-mode.json",
    }
    for name in names:
        (tmp_path / name).write_text(json.dumps({"name": name}), encoding="utf-8")
    script = verifier.recovery_control_script(str(tmp_path))
    output = StringIO()
    with redirect_stdout(output):
        exec(compile(script, "recovery-control.py", "exec"), {})
    captured = json.loads(output.getvalue())
    assert set(captured) == names
    assert all(record["type"] == "file" for record in captured.values())
    assert "read_bytes" not in script
    assert "O_NOFOLLOW" in script
    assert script.count("os.fstat(fd)") == 2

    (tmp_path / "update-job.json").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(SystemExit, match="exceeds its bound"):
        exec(compile(script, "recovery-control.py", "exec"), {})


def test_memory_sampler_requires_numeric_samples(monkeypatch):
    verifier = _load()
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout='{"MemUsage":"12.5MiB / 1GiB"}'
        ),
    )
    sampler = verifier.MemorySampler("app")
    sampler.start()
    deadline = time.monotonic() + 2
    while not sampler.samples and time.monotonic() < deadline:
        time.sleep(0.01)
    evidence = sampler.stop()
    assert evidence["sample_count"] >= 1
    assert evidence["peak_container_memory_bytes"] == int(12.5 * 1024**2)

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout='{"MemUsage":"unknown"}'),
    )
    failed = verifier.MemorySampler("app")
    failed.start()
    deadline = time.monotonic() + 2
    while failed.failure is None and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(verifier.CapabilityError, match="memory sampling"):
        failed.stop()


def test_damaged_process_monitor_requires_real_restart_barrier_samples(monkeypatch):
    verifier = _load()
    allowed = {"core": 101, "ui": 202}
    samples = [
        {"core": [101], "ui": [202]},
        verifier.CapabilityError("container restarting"),
        {"core": [], "ui": []},
        {"core": [], "ui": []},
    ]

    def scan(*_args, **_kwargs):
        if samples:
            value = samples.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return {"core": [], "ui": []}

    monkeypatch.setattr(verifier, "worker_pid_scan", scan)
    monitor = verifier.ForbiddenSelectedProcessMonitor(
        "probe", {"generation_id": "a" * 64}, allowed
    )
    monitor.start()
    deadline = time.monotonic() + 2
    while monitor.post_barrier_sample_count < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    evidence = monitor.stop()

    assert evidence["restart_barrier_observed"] is True
    assert evidence["successful_sample_count"] >= 3
    assert evidence["sampling_error_count"] >= 1
    assert evidence["post_barrier_sample_count"] >= 1
    assert evidence["forbidden_selected_pids"] == []

    forbidden_samples = [
        {"core": [101], "ui": [202]},
        {"core": [], "ui": []},
        {"core": [303], "ui": []},
    ]

    def forbidden_scan(*_args, **_kwargs):
        if forbidden_samples:
            return forbidden_samples.pop(0)
        return {"core": [303], "ui": []}

    monkeypatch.setattr(verifier, "worker_pid_scan", forbidden_scan)
    forbidden = verifier.ForbiddenSelectedProcessMonitor(
        "probe", {"generation_id": "a" * 64}, allowed
    )
    forbidden.start()
    deadline = time.monotonic() + 2
    while not forbidden.forbidden_pids and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(verifier.CapabilityError, match="launched after"):
        forbidden.stop()
