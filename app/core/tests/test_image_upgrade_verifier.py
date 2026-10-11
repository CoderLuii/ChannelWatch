from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/verify-image-upgrade.py"


def _load():
    spec = importlib.util.spec_from_file_location("image_upgrade_verifier", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _status() -> dict:
    return {
        "current_version": "1.5.0",
        "image_version": "1.5.0",
        "runtime_source": "image",
        "active_bundle": None,
        "worker_runtime": {
            "python_executable": "/venv/bin/python",
            "verified_generation": None,
        },
    }


def _historical_active() -> dict:
    return {"version": "1.4.5", "path": "/config/releases/1.4.5"}


def _deactivated_active() -> dict:
    return {
        **_historical_active(),
        "deactivated_at": "2026-10-11T00:00:00Z",
        "deactivated_reason": "image-version-is-current-or-newer",
    }


def _runtime_info() -> dict:
    return {
        "diagnostic_source": "image",
        "application_dir": "/app",
        "application_version": "1.5.3",
        "process_runtime": {
            "pid": 123,
            "python_executable": "/venv/bin/python",
            "python_version": "3.14.8",
            "python_prefix": "/venv",
            "dependencies": {
                "requests": {"loaded": False},
                "cryptography": {
                    "loaded": True,
                    "path": "/venv/lib/python3.14/site-packages/cryptography/__init__.py",
                    "version": "46.0.5",
                },
                "sqlmodel": {
                    "loaded": True,
                    "path": "/venv/lib/python3.14/site-packages/sqlmodel/__init__.py",
                    "version": "0.0.31",
                },
                "pydantic_core": {
                    "loaded": True,
                    "path": "/venv/lib/python3.14/site-packages/pydantic_core/__init__.py",
                    "version": "2.41.5",
                },
                "uvicorn": {"loaded": False},
            },
        },
        "verified_generation": None,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"diagnostic_source": "selected_generation"},
        {"application_dir": "/config/channelwatch-runtime/releases/" + "a" * 64},
        {"application_version": "1.5.2"},
        {"process_runtime": {"python_executable": "/tmp/python"}},
        {
            "process_runtime": {
                **_runtime_info()["process_runtime"],
                "pid": True,
            }
        },
        {
            "process_runtime": {
                **_runtime_info()["process_runtime"],
                "python_version": "3.14.7",
            }
        },
        {
            "process_runtime": {
                **_runtime_info()["process_runtime"],
                "dependencies": {
                    **_runtime_info()["process_runtime"]["dependencies"],
                    "cryptography": {
                        "loaded": True,
                        "path": "/venv/lib/python3.14/site-packages/../selected/cryptography.py",
                        "version": "46.0.5",
                    },
                },
            }
        },
        {
            "process_runtime": {
                **_runtime_info()["process_runtime"],
                "dependencies": {
                    name: {"loaded": False}
                    for name in _runtime_info()["process_runtime"]["dependencies"]
                },
            }
        },
        {"verified_generation": {"runtime_generation_id": "a" * 64}},
    ],
)
def test_cli_runtime_info_rejects_non_image_identity(mutation: dict):
    verifier = _load()
    payload = {**_runtime_info(), **mutation}

    with pytest.raises(verifier.ImageUpgradeError, match="runtime-info"):
        verifier.validate_cli_runtime_info(
            json.dumps(payload),
            candidate_version="1.5.3",
            expected_python_version="3.14.8",
        )


def test_cli_runtime_info_runs_image_doctor_and_retains_canonical_evidence(
    monkeypatch, tmp_path: Path
):
    verifier = _load()
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps(_runtime_info()))

    monkeypatch.setattr(verifier, "run", fake_run)
    evidence = tmp_path / "runtime-info.json"

    result = verifier.verify_cli_runtime_info(
        "candidate",
        candidate_version="1.5.3",
        expected_python_version="3.14.8",
        evidence_path=evidence,
    )

    assert commands == [
        [
            "docker",
            "exec",
            "candidate",
            "/usr/local/bin/channelwatch",
            "doctor",
            "runtime-info",
        ]
    ]
    assert json.loads(evidence.read_text(encoding="utf-8")) == _runtime_info()
    assert result == {
        "sha256": verifier.sha256_file(evidence),
        "identity": {
            "diagnostic_source": "image",
            "application_dir": "/app",
            "application_version": "1.5.3",
            "python_executable": "/venv/bin/python",
            "python_version": "3.14.8",
            "mandatory_dependencies": {
                "cryptography": "/venv/lib/python3.14/site-packages/cryptography/__init__.py",
                "pydantic_core": "/venv/lib/python3.14/site-packages/pydantic_core/__init__.py",
                "sqlmodel": "/venv/lib/python3.14/site-packages/sqlmodel/__init__.py",
            },
            "verified_generation": None,
        },
    }


def test_qualification_checks_cli_identity_before_and_after_restart():
    verifier = _load()
    source = inspect.getsource(verifier.qualify)

    replacement = source.index('"candidate-runtime-info-after-replace.json"')
    restart = source.index('["docker", "restart", candidate]')
    restarted = source.index('"candidate-runtime-info-after-restart.json"')

    assert replacement < restart < restarted
    assert source.count("verify_cli_runtime_info(") == 2
    assert '"cli_runtime_info": {' in source


@pytest.mark.parametrize(
    "environment",
    [
        [],
        ["PYTHON_VERSION=3.14.7"],
        ["PYTHON_VERSION=3.14.8", "PYTHON_VERSION=3.14.8"],
        ["PYTHON_VERSION=3.14.8", 42],
    ],
)
def test_candidate_python_version_requires_exact_sealed_image_environment(environment):
    verifier = _load()

    with pytest.raises(verifier.ImageUpgradeError, match="Python version"):
        verifier.candidate_python_version({"Config": {"Env": environment}})


def test_candidate_python_version_accepts_configured_image_runtime():
    verifier = _load()

    assert (
        verifier.candidate_python_version(
            {"Config": {"Env": ["PATH=/venv/bin:/usr/bin", "PYTHON_VERSION=3.14.8"]}}
        )
        == "3.14.8"
    )


def test_upgrade_requires_new_container_then_stable_candidate_restart():
    verifier = _load()
    hashes = {"/config/settings.json": "a" * 64}

    verifier.validate_upgrade_state(
        old_container_id="old",
        replacement_container_id="new",
        restarted_container_id="new",
        status=_status(),
        candidate_version="1.5.0",
        historical_active=_historical_active(),
        deactivated_active=_deactivated_active(),
        before=hashes,
        after=dict(hashes),
    )

    with pytest.raises(verifier.ImageUpgradeError, match="new container"):
        verifier.validate_upgrade_state(
            old_container_id="same",
            replacement_container_id="same",
            restarted_container_id="same",
            status=_status(),
            candidate_version="1.5.0",
            historical_active=_historical_active(),
            deactivated_active=_deactivated_active(),
            before=hashes,
            after=dict(hashes),
        )
    with pytest.raises(verifier.ImageUpgradeError, match="restart changed"):
        verifier.validate_upgrade_state(
            old_container_id="old",
            replacement_container_id="new",
            restarted_container_id="newer",
            status=_status(),
            candidate_version="1.5.0",
            historical_active=_historical_active(),
            deactivated_active=_deactivated_active(),
            before=hashes,
            after=dict(hashes),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"current_version": "1.4.1"},
        {"image_version": "1.4.0"},
        {"runtime_source": "app_bundle"},
        {"active_bundle": _historical_active()},
        {"worker_runtime": {"python_executable": "/tmp/python", "verified_generation": None}},
        {"worker_runtime": {"python_executable": "/venv/bin/python", "verified_generation": "a" * 64}},
    ],
)
def test_upgrade_rejects_historical_runtime_selection(mutation: dict):
    verifier = _load()
    status = {**_status(), **mutation}
    with pytest.raises(verifier.ImageUpgradeError, match="immutable image runtime"):
        verifier.validate_upgrade_state(
            old_container_id="old",
            replacement_container_id="new",
            restarted_container_id="new",
            status=status,
            candidate_version="1.5.0",
            historical_active=_historical_active(),
            deactivated_active=_deactivated_active(),
            before={"settings": "a" * 64},
            after={"settings": "a" * 64},
        )


def test_upgrade_rejects_persistent_hash_change():
    verifier = _load()
    with pytest.raises(verifier.ImageUpgradeError, match="application data changed"):
        verifier.validate_upgrade_state(
            old_container_id="old",
            replacement_container_id="new",
            restarted_container_id="new",
            status=_status(),
            candidate_version="1.5.0",
            historical_active=_historical_active(),
            deactivated_active=_deactivated_active(),
            before={"settings": "a" * 64},
            after={"settings": "b" * 64},
        )


def test_upgrade_rejects_unbound_deactivated_history():
    verifier = _load()
    with pytest.raises(verifier.ImageUpgradeError, match="historical active identity"):
        verifier.validate_upgrade_state(
            old_container_id="old",
            replacement_container_id="new",
            restarted_container_id="new",
            status=_status(),
            candidate_version="1.5.0",
            historical_active=_historical_active(),
            deactivated_active={**_deactivated_active(), "path": "/other"},
            before={"settings": "a" * 64},
            after={"settings": "a" * 64},
        )


def test_native_architecture_requires_host_docker_and_requested_match(monkeypatch):
    verifier = _load()
    monkeypatch.setattr(verifier.platform, "machine", lambda: "x86_64")

    assert verifier.native_architecture(
        "amd64",
        runner=lambda *_args, **_kwargs: SimpleNamespace(stdout="amd64\n"),
    ) == "amd64"
    with pytest.raises(verifier.ImageUpgradeError, match="match natively"):
        verifier.native_architecture(
            "arm64",
            runner=lambda *_args, **_kwargs: SimpleNamespace(stdout="amd64\n"),
        )


def test_feed_root_is_created_before_tls_material(monkeypatch, tmp_path: Path):
    verifier = _load()
    observed: list[bool] = []

    class Capability:
        LABEL = ""

        @staticmethod
        def create_tls_material(root):
            observed.append(root.is_dir())
            return root / "ca.pem", root / "cert.pem", root / "key.pem"

        @staticmethod
        def write_feed(root, mapping):
            observed.append(root.is_dir() and len(mapping) == 2)

    monkeypatch.setattr(verifier, "_load_script", lambda *_args: Capability)
    feed = tmp_path / "nested" / "feed"
    verifier._write_feed(feed, tmp_path / "catalog.json", "https://github.com/x", tmp_path / "bundle.zip")
    assert observed == [True, True]


def test_feed_catalog_gate_requires_exact_signed_bytes(monkeypatch):
    verifier = _load()
    expected = "a" * 64
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess([], 0, expected + "\n", "")

    monkeypatch.setattr(verifier, "run", fake_run)

    assert verifier._verify_feed_catalog("old", expected, 30) == expected
    assert len(commands) == 1
    assert commands[0][:3] == ["docker", "exec", "old"]
    assert commands[0][-1] == verifier.LEGACY_CATALOG_URL

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, "b" * 64 + "\n", ""
        ),
    )
    with pytest.raises(verifier.ImageUpgradeError, match="exact signed catalog bytes"):
        verifier._verify_feed_catalog("old", expected, 30)

    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, "", "permission denied while connecting to Docker"
        ),
    )
    with pytest.raises(verifier.ImageUpgradeError, match="permission denied"):
        verifier._verify_feed_catalog("old", expected, 30)


class _JobClient:
    def __init__(self, apply_result, jobs):
        self.apply_result = apply_result
        self.jobs = iter(jobs)
        self.calls: list[tuple[str, object]] = []

    def request(self, path, payload=None):
        self.calls.append((path, payload))
        if path == "/api/v1/update/apply":
            if isinstance(self.apply_result, BaseException):
                raise self.apply_result
            return self.apply_result
        return next(self.jobs)

    def login(self):
        raise AssertionError("login should not be needed")


def _run_job_waits_immediately(monkeypatch, verifier):
    def immediate_wait(callback, _timeout, _description):
        while True:
            result = callback()
            if result is not None:
                return result

    monkeypatch.setattr(verifier, "wait_until", immediate_wait)


@pytest.mark.parametrize("baseline_image_version", ["1.4.0", "1.4.1"])
def test_legacy_baseline_binds_server_assigned_job_id(
    monkeypatch, baseline_image_version: str
):
    verifier = _load()
    requested_job_id = "1" * 32
    server_job_id = "2" * 32
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda _size: requested_job_id)
    _run_job_waits_immediately(monkeypatch, verifier)
    client = _JobClient(
        {"job_id": server_job_id},
        [
            {"job_id": server_job_id, "status": "applying"},
            {"job_id": server_job_id, "status": "success"},
        ],
    )

    result = verifier._wait_job(
        client,
        version="1.4.5",
        timeout=30,
        baseline_image_version=baseline_image_version,
    )

    assert result["job_id"] == server_job_id
    assert client.calls == [
        (
            "/api/v1/update/apply",
            {"version": "1.4.5", "job_id": requested_job_id},
        ),
        (f"/api/v1/update/jobs/{server_job_id}", None),
        (f"/api/v1/update/jobs/{server_job_id}", None),
    ]


@pytest.mark.parametrize("response_job_id", [None, "2" * 31, "A" * 32])
def test_legacy_baseline_rejects_missing_or_invalid_server_job_id(
    monkeypatch, response_job_id
):
    verifier = _load()
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda _size: "1" * 32)
    client = _JobClient({"job_id": response_job_id}, [])

    with pytest.raises(verifier.ImageUpgradeError, match="invalid job identity"):
        verifier._wait_job(
            client,
            version="1.4.5",
            timeout=30,
            baseline_image_version="1.4.0",
        )

    assert [path for path, _payload in client.calls] == ["/api/v1/update/apply"]


@pytest.mark.parametrize(
    "jobs",
    [
        [{"job_id": "3" * 32, "status": "applying"}],
        [
            {"job_id": "2" * 32, "status": "applying"},
            {"job_id": "3" * 32, "status": "success"},
        ],
    ],
)
def test_legacy_baseline_rejects_job_identity_change(monkeypatch, jobs):
    verifier = _load()
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda _size: "1" * 32)
    _run_job_waits_immediately(monkeypatch, verifier)
    client = _JobClient({"job_id": "2" * 32}, jobs)

    with pytest.raises(verifier.ImageUpgradeError, match="identity changed"):
        verifier._wait_job(
            client,
            version="1.4.5",
            timeout=30,
            baseline_image_version="1.4.0",
        )

    assert sum(path == "/api/v1/update/apply" for path, _ in client.calls) == 1


def test_legacy_baseline_fails_closed_when_apply_response_is_lost(monkeypatch):
    verifier = _load()
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda _size: "1" * 32)
    client = _JobClient(urllib.error.URLError("response lost"), [])

    with pytest.raises(verifier.ImageUpgradeError, match="cannot be recovered"):
        verifier._wait_job(
            client,
            version="1.4.5",
            timeout=30,
            baseline_image_version="1.4.1",
        )

    assert [path for path, _payload in client.calls] == ["/api/v1/update/apply"]


def test_modern_baseline_recovers_lost_apply_response_with_requested_job_id(
    monkeypatch,
):
    verifier = _load()
    requested_job_id = "1" * 32
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda _size: requested_job_id)
    _run_job_waits_immediately(monkeypatch, verifier)
    client = _JobClient(
        TimeoutError("response lost"),
        [{"job_id": requested_job_id, "status": "success"}],
    )

    result = verifier._wait_job(
        client,
        version="1.4.5",
        timeout=30,
        baseline_image_version="1.4.2",
    )

    assert result["job_id"] == requested_job_id
    assert sum(path == "/api/v1/update/apply" for path, _ in client.calls) == 1
    assert client.calls[-1] == (
        f"/api/v1/update/jobs/{requested_job_id}",
        None,
    )


def test_modern_baseline_rejects_server_assigned_job_id(monkeypatch):
    verifier = _load()
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda _size: "1" * 32)
    client = _JobClient({"job_id": "2" * 32}, [])

    with pytest.raises(verifier.ImageUpgradeError, match="requested job"):
        verifier._wait_job(
            client,
            version="1.4.5",
            timeout=30,
            baseline_image_version="1.4.2",
        )

    assert [path for path, _payload in client.calls] == ["/api/v1/update/apply"]


def test_signed_app_bundle_is_bound_to_public_source_commit(monkeypatch, tmp_path: Path):
    verifier = _load()
    version = "1.4.5"
    release = {
        "version": version,
        "bundle_url": (
            "https://github.com/CoderLuii/ChannelWatch/releases/download/"
            f"v{version}/channelwatch-{version}.zip"
        ),
    }

    class Assets:
        @staticmethod
        def verify_update_catalog(*_args, **_kwargs):
            return {"payload": {"releases": [release]}}

        @staticmethod
        def validate_bundle_archive(*_args, **_kwargs):
            return {"git_sha": "a" * 40}

    catalog = tmp_path / "catalog.json"
    bundle = tmp_path / "bundle.zip"
    catalog.write_bytes(b"catalog")
    bundle.write_bytes(b"bundle")
    monkeypatch.setattr(verifier, "_load_script", lambda *_args: Assets)

    assert verifier.verify_signed_baseline_assets(
        catalog, bundle, version=version, source_commit="a" * 40
    )[1] == release["bundle_url"]
    with pytest.raises(verifier.ImageUpgradeError, match="signed baseline catalog"):
        verifier.verify_signed_baseline_assets(
            catalog, bundle, version=version, source_commit="b" * 40
        )


def test_dependency_preflight_loads_every_real_helper_without_docker():
    verifier = _load()

    loaded = {
        name: verifier._load_script(path, f"dependency_preflight_{name}")
        for name, path in (
            ("assets", verifier.ASSET_VERIFIER),
            ("native", verifier.NATIVE_VERIFIER),
            ("capability", verifier.CAPABILITY_VERIFIER),
        )
    }

    assert callable(loaded["assets"].verify_update_catalog)
    assert callable(loaded["native"]._safe_extract)
    assert callable(loaded["capability"].pinned_native_image)


@pytest.mark.parametrize(
    "mutation",
    [
        {"dvr_servers": []},
        {"dvr_servers": [{"id": "upgrade-canary", "api_key": ""}]},
        {"dvr_servers": [{"id": "upgrade-canary", "api_key": "not-masked"}]},
        {
            "dvr_servers": [
                {
                    "id": "upgrade-canary",
                    "name": "Upgrade Canary",
                    "host": "example.com",
                    "port": 8089,
                    "enabled": True,
                    "api_key": "****",
                }
            ]
        },
    ],
)
def test_credential_semantics_reject_missing_cleared_unmasked_or_enabled(mutation):
    verifier = _load()
    client = SimpleNamespace(request=lambda path: mutation)
    assert verifier.credential_api_semantics(client) is False


def test_credential_semantics_accept_exact_disabled_masked_dvr():
    verifier = _load()
    settings = {
        "dvr_servers": [
            {
                "id": "upgrade-canary",
                "name": "Upgrade Canary",
                "host": "example.com",
                "port": 8089,
                "enabled": False,
                "api_key": "****",
            }
        ]
    }
    client = SimpleNamespace(request=lambda path: settings)
    assert verifier.credential_api_semantics(client) is True


def test_encrypted_dvr_status_uses_authenticated_security_route():
    verifier = _load()
    requests: list[str] = []
    client = SimpleNamespace(
        request=lambda path: requests.append(path)
        or {"encrypted_dvr_api_keys_at_rest": True}
    )

    verifier.require_encrypted_dvr_api_keys_at_rest(client)

    assert requests == ["/api/v1/security/status"]


@pytest.mark.parametrize(
    "payload",
    [
        {"encrypted_dvr_api_keys_at_rest": False},
        {},
        [],
        None,
    ],
)
def test_encrypted_dvr_status_requires_literal_true(payload):
    verifier = _load()
    client = SimpleNamespace(request=lambda _path: payload)

    with pytest.raises(verifier.ImageUpgradeError, match="encrypted settings"):
        verifier.require_encrypted_dvr_api_keys_at_rest(client)


@pytest.mark.parametrize("status", [401, 503])
def test_encrypted_dvr_status_propagates_http_errors(status):
    verifier = _load()
    error = urllib.error.HTTPError(
        "http://candidate/api/v1/security/status",
        status,
        "probe failure",
        {},
        None,
    )

    def fail(_path):
        raise error

    with pytest.raises(urllib.error.HTTPError) as raised:
        verifier.require_encrypted_dvr_api_keys_at_rest(
            SimpleNamespace(request=fail)
        )

    assert raised.value is error


@pytest.mark.parametrize(
    "probe",
    [
        {
            "encrypted": True,
            "plaintext_absent": True,
            "decrypts_expected": False,
        },
        {
            "encrypted": False,
            "plaintext_absent": True,
            "decrypts_expected": False,
        },
    ],
)
def test_encrypted_settings_reject_wrong_key_or_corrupt_cipher(monkeypatch, probe):
    verifier = _load()
    monkeypatch.setattr(verifier, "_docker_json", lambda *_args: probe)
    assert verifier.encrypted_settings_usable("candidate") is False


def test_encrypted_settings_require_ciphertext_absence_and_real_decryption(monkeypatch):
    verifier = _load()
    monkeypatch.setattr(
        verifier,
        "_docker_json",
        lambda *_args: {
            "encrypted": True,
            "plaintext_absent": True,
            "decrypts_expected": True,
        },
    )
    assert verifier.encrypted_settings_usable("candidate") is True


def test_process_commands_reject_selected_old_release(monkeypatch):
    verifier = _load()
    commands: list[list[str]] = []
    output = "\n".join(
        [
            "PID COMMAND",
            "101 python -u /app/core/runtime_launcher.py core --stay-alive",
            "202 python -u /app/core/runtime_launcher.py ui --host 0.0.0.0",
        ]
    )

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(
        verifier,
        "run",
        fake_run,
    )
    assert verifier.image_process_commands("candidate") == {
        "core": "python -u /app/core/runtime_launcher.py core --stay-alive",
        "ui": "python -u /app/core/runtime_launcher.py ui --host 0.0.0.0",
    }
    assert commands == [["docker", "top", "candidate", "-eo", "pid,args"]]

    selected = output.replace(
        "ui --host",
        "ui --generation /config/channelwatch-runtime/releases/" + "a" * 64 + " --host",
    )
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=selected),
    )
    with pytest.raises(verifier.ImageUpgradeError, match="historical app bundle"):
        verifier.image_process_commands("candidate")


@pytest.mark.parametrize(
    "output",
    [
        "ARGS\npython -u /app/core/runtime_launcher.py core\n"
        "python -u /app/core/runtime_launcher.py ui\n",
        "PID COMMAND\nnot-a-pid python -u /app/core/runtime_launcher.py core\n"
        "202 python -u /app/core/runtime_launcher.py ui\n",
        "PID COMMAND\n0 python -u /app/core/runtime_launcher.py core\n"
        "202 python -u /app/core/runtime_launcher.py ui\n",
        "PID COMMAND\n101\n202 python -u /app/core/runtime_launcher.py ui\n",
    ],
)
def test_process_commands_require_valid_pid_rows(monkeypatch, output):
    verifier = _load()
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=output),
    )

    with pytest.raises(verifier.ImageUpgradeError, match="PID"):
        verifier.image_process_commands("candidate")


def test_process_commands_require_exact_pid_command_header(monkeypatch):
    verifier = _load()
    output = (
        "PID USER COMMAND\n"
        "101 channelwatch python -u /app/core/runtime_launcher.py core\n"
        "202 channelwatch python -u /app/core/runtime_launcher.py ui\n"
    )
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=output),
    )

    with pytest.raises(verifier.ImageUpgradeError, match="PID COMMAND header"):
        verifier.image_process_commands("candidate")


def test_process_commands_reject_duplicate_pid(monkeypatch):
    verifier = _load()
    output = (
        "PID COMMAND\n"
        "101 python -u /app/core/runtime_launcher.py core\n"
        "101 python -u /app/core/runtime_launcher.py ui\n"
    )
    monkeypatch.setattr(
        verifier,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=output),
    )

    with pytest.raises(verifier.ImageUpgradeError, match="duplicate PID"):
        verifier.image_process_commands("candidate")


def test_cleanup_removes_only_exact_owned_resources(monkeypatch):
    verifier = _load()
    resources = verifier.Resources("owned")
    resources.containers.update({"owned-app", "foreign-app"})
    resources.volumes.add("owned-config")
    resources.networks.add("owned-network")
    resources.images.add("owned-image")
    removed: list[tuple[str, ...]] = []

    def fake_run(command, *, check=True, timeout=None):
        del check, timeout
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        if command[:2] == ["docker", "inspect"]:
            owner = verifier.LABEL_VALUE if command[-1] == "owned-app" else "other"
            return subprocess.CompletedProcess(command, 0, owner + "\n", "")
        if command[:3] in (
            ["docker", "volume", "inspect"],
            ["docker", "network", "inspect"],
        ):
            return subprocess.CompletedProcess(command, 0, verifier.LABEL_VALUE + "\n", "")
        removed.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(verifier, "run", fake_run)
    assert resources.cleanup() == ["container:foreign-app:ownership"]
    assert ("docker", "rm", "-f", "owned-app") in removed
    assert not any("foreign-app" in command for command in removed)
    assert ("docker", "volume", "rm", "owned-config") in removed
    assert ("docker", "network", "rm", "owned-network") in removed
    assert ("docker", "image", "rm", "-f", "owned-image") in removed


@pytest.mark.parametrize(
    ("kind", "name", "stderr"),
    [
        ("container", "owned-app", "error: no such object: owned-app\n"),
        (
            "volume",
            "owned-config",
            "error response from daemon: get owned-config: no such volume\n",
        ),
        (
            "network",
            "owned-network",
            "error response from daemon: network owned-network not found\n",
        ),
        (
            "image",
            "owned-image",
            "error response from daemon: No such image: owned-image\n",
        ),
    ],
)
def test_cleanup_accepts_current_docker_lowercase_missing_resource(
    monkeypatch, kind: str, name: str, stderr: str
):
    verifier = _load()
    resources = verifier.Resources("owned")
    getattr(resources, kind + "s").add(name)

    monkeypatch.setattr(
        verifier,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, "\n", stderr
        ),
    )

    assert resources.cleanup() == []


@pytest.mark.parametrize(
    ("kind", "name", "stderr"),
    [
        (
            "container",
            "owned-app",
            "permission denied while trying to connect to the Docker daemon socket",
        ),
        (
            "volume",
            "owned-config",
            "error during connect: the docker daemon is not running",
        ),
        ("network", "owned-network", "request returned Internal Server Error"),
        ("image", "owned-image", "context deadline exceeded"),
    ],
)
def test_cleanup_rejects_non_missing_inspection_errors(
    monkeypatch, kind: str, name: str, stderr: str
):
    verifier = _load()
    resources = verifier.Resources("owned")
    getattr(resources, kind + "s").add(name)

    monkeypatch.setattr(
        verifier,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", stderr),
    )

    assert resources.cleanup() == [f"{kind}:{name}:inspect"]


def test_cleanup_rejects_missing_error_for_different_resource(monkeypatch):
    verifier = _load()
    resources = verifier.Resources("owned")
    resources.networks.add("owned-network")
    monkeypatch.setattr(
        verifier,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            1,
            "",
            "Error response from daemon: network somebody-elses-network not found\n",
        ),
    )

    assert resources.cleanup() == ["network:owned-network:inspect"]


def test_cleanup_rejects_foreign_container_volume_and_network(monkeypatch):
    verifier = _load()
    resources = verifier.Resources("owned")
    resources.containers.add("owned-app")
    resources.volumes.add("owned-config")
    resources.networks.add("owned-network")
    removed: list[tuple[str, ...]] = []

    def fake_run(command, **_kwargs):
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, "other\n", "")
        removed.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(verifier, "run", fake_run)

    assert resources.cleanup() == [
        "container:owned-app:ownership",
        "volume:owned-config:ownership",
        "network:owned-network:ownership",
    ]
    assert removed == []


def test_candidate_tag_reservation_accepts_lowercase_missing_image(monkeypatch):
    verifier = _load()
    resources = verifier.Resources("owned")
    monkeypatch.setattr(
        verifier,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, "[]\n", f"Error response from daemon: No such image: {command[-1]}\n"
        ),
    )

    tag = resources.image()

    assert tag in resources.images


def test_candidate_tag_reservation_rejects_daemon_error(monkeypatch):
    verifier = _load()
    resources = verifier.Resources("owned")
    monkeypatch.setattr(
        verifier,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, "", "error during connect: the docker daemon is not running"
        ),
    )

    with pytest.raises(verifier.ImageUpgradeError, match="reserve a candidate image tag"):
        resources.image()


def test_capture_logs_records_quiet_container_and_continues(monkeypatch, tmp_path: Path):
    verifier = _load()
    seen: list[str] = []

    def fake_run(command, **_kwargs):
        name = command[-1]
        seen.append(name)
        output = "" if name == "feed" else f"{name} output\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(verifier, "run", fake_run)

    hashes, empty_containers, failures = verifier._capture_logs(
        {"old", "candidate", "feed"}, tmp_path
    )

    assert seen == ["candidate", "feed", "old"]
    assert set(hashes) == {"candidate", "feed", "old"}
    assert empty_containers == ["feed"]
    assert failures == []
    assert (tmp_path / "feed.log").is_file()
    assert (tmp_path / "old.log").is_file()


def test_capture_logs_records_command_failures_and_continues_after_exception(
    monkeypatch, tmp_path: Path
):
    verifier = _load()
    seen: list[str] = []

    def fake_run(command, **_kwargs):
        name = command[-1]
        seen.append(name)
        if name == "candidate":
            return subprocess.CompletedProcess(
                command, 1, "", "permission denied while connecting to Docker"
            )
        if name == "feed":
            raise TimeoutError("docker logs timed out")
        return subprocess.CompletedProcess(command, 0, "old output\n", "")

    monkeypatch.setattr(verifier, "run", fake_run)

    hashes, empty_containers, failures = verifier._capture_logs(
        {"old", "candidate", "feed"}, tmp_path
    )

    assert seen == ["candidate", "feed", "old"]
    assert set(hashes) == {"candidate", "feed", "old"}
    assert empty_containers == []
    assert failures == [
        {
            "container": "candidate",
            "returncode": 1,
            "failure_summary": "permission denied while connecting to Docker",
        },
        {
            "container": "feed",
            "failure_type": "TimeoutError",
            "failure_summary": "docker logs timed out",
        },
    ]
    assert "permission denied" in (tmp_path / "candidate.log").read_text(
        encoding="utf-8"
    )
    assert "TimeoutError: docker logs timed out" in (tmp_path / "feed.log").read_text(
        encoding="utf-8"
    )
    assert "old output" in (tmp_path / "old.log").read_text(encoding="utf-8")


def test_candidate_tag_is_owned_before_loader_failure_and_foreign_image_is_untouched(
    monkeypatch, tmp_path: Path
):
    verifier = _load()
    resources = verifier.Resources("owned")
    removed: list[tuple[str, ...]] = []
    image_inspections = 0

    def fail_after_load(_args, _root, *, image_tag):
        assert image_tag in resources.images
        raise verifier.ImageUpgradeError("inspection failed after load")

    def fake_run(command, *, check=True, timeout=None):
        nonlocal image_inspections
        del check, timeout
        if command[:3] == ["docker", "image", "inspect"]:
            image_inspections += 1
            if image_inspections == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "[]\n",
                    f"Error response from daemon: No such image: {command[-1]}\n",
                )
            return subprocess.CompletedProcess(command, 0, "[]", "")
        removed.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(verifier, "_load_candidate", fail_after_load)
    monkeypatch.setattr(verifier, "run", fake_run)
    with pytest.raises(verifier.ImageUpgradeError, match="inspection failed"):
        verifier._load_tracked_candidate(SimpleNamespace(), tmp_path, resources)
    tag = next(iter(resources.images))
    resources.cleanup()
    assert ("docker", "image", "rm", "-f", tag) in removed
    assert not any("foreign-image" in command for command in removed)


def test_qualify_preserves_primary_failure_when_logs_and_cleanup_also_fail(
    monkeypatch, tmp_path: Path
):
    verifier = _load()
    catalog = tmp_path / "catalog.json"
    bundle = tmp_path / "bundle.zip"
    catalog.write_text("{}", encoding="utf-8")
    bundle.write_bytes(b"bundle")
    args = SimpleNamespace(
        architecture="arm64",
        baseline_catalog=catalog,
        baseline_bundle=bundle,
        baseline_app_version="1.4.5",
        baseline_app_source_commit="a" * 40,
        baseline_repository="coderluii/channelwatch",
        baseline_image_version="1.4.0",
        baseline_source_commit="b" * 40,
        baseline_index_digest="sha256:" + "c" * 64,
        baseline_manifest_digest="sha256:" + "d" * 64,
        source_commit="e" * 40,
        candidate_version="1.5.0",
        expected_archive_sha256="f" * 64,
        expected_descriptor_sha256="1" * 64,
        log_directory=tmp_path / "logs",
    )

    monkeypatch.setattr(verifier, "native_architecture", lambda value: value)
    monkeypatch.setattr(
        verifier,
        "verify_signed_baseline_assets",
        lambda *_args, **_kwargs: ({}, "https://example.invalid/bundle.zip"),
    )
    monkeypatch.setattr(
        verifier,
        "_native_manifest",
        lambda *_args, **_kwargs: ("baseline-reference", "sha256:" + "2" * 64, "image-id"),
    )
    monkeypatch.setattr(
        verifier,
        "_load_tracked_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("primary canary failure")
        ),
    )
    monkeypatch.setattr(
        verifier,
        "_capture_logs",
        lambda *_args, **_kwargs: (
            {
                "candidate": "a" * 64,
                "feed": "b" * 64,
                "old": "c" * 64,
            },
            ["feed"],
            [
                {
                    "container": "candidate",
                    "returncode": 1,
                    "failure_summary": "permission denied while connecting to Docker",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        verifier.Resources,
        "cleanup",
        lambda _self: ["container:reserved-candidate:inspect"],
    )

    receipt = verifier.qualify(args)

    assert receipt["status"] == "failed"
    assert receipt["failure_type"] == "RuntimeError"
    assert receipt["failure_summary"] == "primary canary failure"
    assert receipt["logs_sha256"] == {
        "candidate": "a" * 64,
        "feed": "b" * 64,
        "old": "c" * 64,
    }
    assert receipt["log_capture"] == {
        "status": "failed",
        "empty_containers": ["feed"],
        "failure_type": "ImageUpgradeError",
        "failure_summary": "Container log evidence failed: 1 command failure; 1 empty log.",
        "command_failures": [
            {
                "container": "candidate",
                "returncode": 1,
                "failure_summary": "permission denied while connecting to Docker",
            }
        ],
    }
    assert receipt["cleanup"] == {
        "status": "failed",
        "failures": ["container:reserved-candidate:inspect"],
    }


def test_main_writes_bounded_failure_receipt(monkeypatch, tmp_path: Path):
    verifier = _load()
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(
        verifier,
        "qualify",
        lambda _args: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )
    monkeypatch.setattr(
        verifier.os.sys,
        "argv",
        [
            str(SCRIPT),
            "--candidate-oci-archive",
            str(tmp_path / "candidate.tar"),
            "--candidate-descriptor",
            str(tmp_path / "descriptor.json"),
            "--expected-archive-sha256",
            "a" * 64,
            "--expected-descriptor-sha256",
            "b" * 64,
            "--candidate-version",
            "1.5.0",
            "--source-commit",
            "c" * 40,
            "--architecture",
            "amd64",
            "--skopeo-image",
            "skopeo@sha256:" + "d" * 64,
            "--baseline-image-version",
            "1.4.0",
            "--baseline-source-commit",
            "d" * 40,
            "--baseline-index-digest",
            "sha256:" + "e" * 64,
            "--baseline-manifest-digest",
            "sha256:" + "f" * 64,
            "--baseline-app-version",
            "1.4.1",
            "--baseline-app-source-commit",
            "e" * 40,
            "--baseline-catalog",
            str(tmp_path / "catalog.json"),
            "--baseline-bundle",
            str(tmp_path / "bundle.zip"),
            "--receipt",
            str(receipt),
            "--log-directory",
            str(tmp_path / "logs"),
        ],
    )

    assert verifier.main() == 1
    failure = json.loads(receipt.read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["failure_type"] == "RuntimeError"
    assert len(failure["failure_summary"]) <= 1000
