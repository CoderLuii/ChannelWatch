from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import socket
import ssl
import threading
import urllib.error
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


def _components(path: str | None = None):
    generation = "/config/channelwatch-runtime/releases/" + "a" * 64
    path = path or f"{generation}/dependencies/site-packages"
    identity = {
        "runtime_generation_id": "a" * 64,
        "runtime_descriptor_sha256": "a" * 64,
        "dependency_inventory_sha256": "b" * 64,
    }
    dependencies = {
        "requests": {"loaded": True, "path": f"{path}/requests/__init__.py"},
        "cryptography": {"loaded": True, "path": f"{path}/cryptography/__init__.py"},
        "sqlmodel": {"loaded": False},
        "uvicorn": {"loaded": False},
        "pydantic_core": {"loaded": True, "path": f"{path}/pydantic_core.so"},
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
        {"runtime_components": components}, _descriptor()
    ) == components

    wrong = _components("/venv/lib/python3.14/site-packages")
    with pytest.raises(verifier.CapabilityError, match="outside the selected generation"):
        verifier.validate_runtime_components(
            {"runtime_components": wrong}, _descriptor()
        )

    missing = _components()
    missing["core"]["process_runtime"]["dependencies"]["requests"] = {
        "loaded": False
    }
    with pytest.raises(verifier.CapabilityError, match="required dependency requests"):
        verifier.validate_runtime_components(
            {"runtime_components": missing}, _descriptor()
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


def test_preservation_probe_requires_all_four_settings_key_and_image_venv_paths(
    monkeypatch,
):
    verifier = _load()
    expected = {
        "/config/settings.json": "a" * 64,
        "/config/encryption.key": "b" * 64,
        "/venv/pyvenv.cfg": "c" * 64,
        "/venv/bin/python": "d" * 64,
    }
    observed = {}

    def execute(name, code):
        observed.update({"name": name, "code": code})
        return expected

    monkeypatch.setattr(verifier, "docker_exec_json", execute)

    assert verifier.preservation_hashes("canary") == expected
    assert observed["name"] == "canary"
    assert all(path in observed["code"] for path in expected)


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


def test_native_log_hashes_require_exact_nonempty_app_and_feed():
    verifier = _load()
    hashes = {"app": "a" * 64, "feed": "b" * 64}
    assert verifier.validate_log_hashes(hashes) == hashes

    for invalid in ({"app": "a" * 64}, {**hashes, "extra": "c" * 64}, {**hashes, "feed": ""}):
        with pytest.raises(verifier.CapabilityError, match="app and feed"):
            verifier.validate_log_hashes(invalid)
