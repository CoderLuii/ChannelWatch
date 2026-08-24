from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load_canary_module():
    path = ROOT / "scripts/release/verify-historical-image-canaries.py"
    spec = importlib.util.spec_from_file_location("historical_image_canaries", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _failed_state(version: str = "0.9.18", digest: str = "a" * 64):
    job_id = "historical-canary-job"
    attempt_id = f"activation@{job_id}"
    return {
        "activation_artifacts": [],
        "release_directories": [f"v{version}"],
        "rollback.json": {
            "previous_active": None,
            "target_version": version,
        },
        "update-job.json": {
            "job_id": job_id,
            "scheduler_attempt_id": attempt_id,
            "operation": "apply",
            "status": "failed",
            "version": version,
            "bundle_sha256": digest,
            "rollback_applied": True,
            "rolled_back_from": version,
            "rolled_back_to": "image",
        },
        "update-scheduler.json": {
            "last_attempt": {
                "version": version,
                "bundle_sha256": digest,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "phase": "failed",
                "terminal_job_status": "failed",
                "rollback_applied": True,
            },
            "quarantines": {
                f"{version}:{digest}": {
                    "version": version,
                    "bundle_sha256": digest,
                    "reason": "activation_failed",
                    "created_at": "2026-08-25T00:00:00Z",
                }
            },
            "maintenance_attention_code": "update-activation-failed",
            "scheduled_restart_at": None,
            "scheduled_release_version": None,
            "scheduled_release_sha256": None,
            "scheduled_attempt_id": None,
        },
    }


def test_failed_activation_validator_requires_exact_transaction_identity():
    canary = _load_canary_module()
    digest = "a" * 64
    state = _failed_state(digest=digest)

    job, scheduler = canary.validate_failed_activation_state(
        state,
        target_version="0.9.18",
        bundle_sha256=digest,
    )

    assert job["job_id"] == "historical-canary-job"
    assert scheduler["quarantines"][f"0.9.18:{digest}"]["reason"] == (
        "activation_failed"
    )

    stale = copy.deepcopy(state)
    stale["activation_artifacts"] = ["activation-failed-launcher-id.json"]
    with pytest.raises(canary.CanaryError, match="control artifacts"):
        canary.validate_failed_activation_state(
            stale,
            target_version="0.9.18",
            bundle_sha256=digest,
        )

    restart_pending = copy.deepcopy(state)
    restart_pending["restart-required.json"] = {"operation": "activation_rollback"}
    with pytest.raises(canary.CanaryError, match="control artifacts"):
        canary.validate_failed_activation_state(
            restart_pending,
            target_version="0.9.18",
            bundle_sha256=digest,
        )

    wrong_attempt = copy.deepcopy(state)
    wrong_attempt["update-scheduler.json"]["last_attempt"]["job_id"] = "other"
    with pytest.raises(canary.CanaryError, match="scheduler attempt"):
        canary.validate_failed_activation_state(
            wrong_attempt,
            target_version="0.9.18",
            bundle_sha256=digest,
        )


def test_failed_activation_poll_tolerates_only_explicit_docker_restart(
    monkeypatch: pytest.MonkeyPatch,
):
    canary = _load_canary_module()
    digest = "a" * 64
    states = iter(
        [
            canary.CanaryError(
                "docker command failed: Error response from daemon: "
                "Container abc is restarting, wait until the container is running"
            ),
            _failed_state(digest=digest),
        ]
    )

    def runtime_state(_name: str):
        result = next(states)
        if isinstance(result, BaseException):
            raise result
        return result

    restart_counts = iter([11, 12])
    monkeypatch.setattr(canary, "runtime_state", runtime_state)
    monkeypatch.setattr(canary, "restart_count", lambda _name: next(restart_counts))
    monkeypatch.setattr(canary, "health", lambda _name, _path: True)
    monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)

    count, state = canary.wait_for_failed_activation(
        "historical",
        target_version="0.9.18",
        bundle_sha256=digest,
        previous_restart_count=10,
        timeout_seconds=10,
    )

    assert count == 12
    assert state["update-job.json"]["status"] == "failed"

    monkeypatch.setattr(
        canary,
        "runtime_state",
        lambda _name: (_ for _ in ()).throw(
            canary.CanaryError("docker command failed: permission denied")
        ),
    )
    monkeypatch.setattr(canary, "container_status", lambda _name: "running")
    with pytest.raises(canary.CanaryError, match="permission denied"):
        canary.poll_runtime_state("historical")

    monkeypatch.setattr(
        canary,
        "runtime_state",
        lambda _name: (_ for _ in ()).throw(
            canary.CanaryError("docker command failed: command failed")
        ),
    )
    monkeypatch.setattr(canary, "container_status", lambda _name: "restarting")
    assert canary.poll_runtime_state("historical") is None


def test_restart_poll_rejects_extra_container_restart(monkeypatch: pytest.MonkeyPatch):
    canary = _load_canary_module()
    monkeypatch.setattr(canary, "restart_count", lambda _name: 3)

    with pytest.raises(canary.CanaryError, match="more than once"):
        canary.wait_for_restart_and_health(
            "historical",
            previous_restart_count=1,
            timeout_seconds=1,
        )


def test_v099_false_success_requires_fatal_core_and_image_ui(
    monkeypatch: pytest.MonkeyPatch,
):
    canary = _load_canary_module()
    states = iter(
        [
            {
                "core": {"status": "BACKOFF", "pid": 0},
                "ui": {"status": "RUNNING", "pid": 8},
            },
            {
                "core": {"status": "FATAL", "pid": 0},
                "ui": {"status": "RUNNING", "pid": 8},
            },
        ]
    )
    monkeypatch.setattr(canary, "supervisor_state", lambda _name: next(states))
    monkeypatch.setattr(
        canary,
        "_running_app_dirs_for_children",
        lambda _name, _children: {"ui": "/app"},
    )
    monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)

    children, apps = canary.wait_for_v099_false_success("v099", 10)

    assert children["core"]["status"] == "FATAL"
    assert apps == {"ui": "/app"}


def test_v010_recovery_requires_the_immutable_entrypoint_restart_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    canary = _load_canary_module()
    restart_counts = iter([1, 3])
    monkeypatch.setattr(canary, "restart_count", lambda _name: next(restart_counts))
    monkeypatch.setattr(
        canary,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="",
            stderr=(
                "PermissionError: [Errno 13] Permission denied: "
                "'/tmp/supervisord.conf'"
            ),
        ),
    )
    monkeypatch.setattr(canary, "container_status", lambda _name: "restarting")
    monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)

    observed = canary.wait_for_v010_entrypoint_failure(
        "v010",
        previous_restart_count=1,
        timeout_seconds=10,
    )

    assert observed == 3


def test_markers_and_scenario_keys_are_explicit(tmp_path: Path):
    canary = _load_canary_module()
    (tmp_path / ".canary-fault-applied").write_text("ui\n", encoding="utf-8")

    assert canary.read_canary_marker(str(tmp_path), ".canary-fault-applied") == "ui"
    assert canary.scenario_key("activation_success", "0.9.15") == (
        "activation_success:0.9.15"
    )
    assert canary.scenario_key("tamper_rejection", "0.9.17", "bundle") == (
        "tamper_rejection:0.9.17:bundle"
    )
    canary.validate_scenario_rows(
        [
            {"scenario_key": "activation_success:0.9.15"},
            {"scenario_key": "tamper_rejection:0.9.15:bundle"},
        ]
    )
    with pytest.raises(canary.CanaryError, match="must be unique"):
        canary.validate_scenario_rows(
            [
                {"scenario_key": "activation_success:0.9.15"},
                {"scenario_key": "activation_success:0.9.15"},
            ]
        )
    with pytest.raises(canary.CanaryError, match="missing or unreadable"):
        canary.read_canary_marker(str(tmp_path), ".canary-tamper-applied")


def test_sitecustomize_records_fault_and_tamper_proof(tmp_path: Path):
    canary = _load_canary_module()
    sitecustomize = tmp_path / "sitecustomize.py"

    canary.write_sitecustomize(sitecustomize)

    source = sitecustomize.read_text(encoding="utf-8")
    assert ".canary-fault-applied" in source
    assert ".canary-tamper-applied" in source
    assert "tamper_applied.write_text('manifest\\n')" in source
    assert "tamper_applied.write_text('bundle\\n')" in source


def test_manifest_tamper_canary_requires_applied_transport_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    canary = _load_canary_module()
    config = tmp_path / "config"
    config.mkdir()
    manifest = tmp_path / "channelwatch-update-v0.9.18.json"
    manifest.write_text(
        json.dumps(
            {
                "payload": {
                    "bundle_url": (
                        "https://github.com/CoderLuii/ChannelWatch/releases/download/"
                        "v0.9.18/channelwatch-app-v0.9.18.zip"
                    )
                }
            }
        ),
        encoding="utf-8",
    )
    bundle = tmp_path / "channelwatch-app-v0.9.18.zip"
    bundle.write_bytes(b"candidate")
    for name, value in {
        ".canary-patch-status": "patched\n",
        ".canary-tamper-applied": "manifest\n",
        ".canary-fetch-last": (
            "https://channelwatch.coderluii.dev/updates/stable.json\n"
        ),
    }.items():
        (config / name).write_text(value, encoding="utf-8")

    class Resources:
        def volume(self, _suffix: str) -> str:
            return str(config)

        def state_dir(self, _suffix: str) -> Path:
            return config

        def container(self, _suffix: str) -> str:
            return "historical-tamper"

    children = {
        "core": {"status": "RUNNING", "pid": 101},
        "ui": {"status": "RUNNING", "pid": 102},
    }
    apps = {"core": "/app", "ui": "/app"}
    state = {"activation_artifacts": [], "release_directories": []}
    monkeypatch.setattr(canary, "init_volume", lambda *args, **kwargs: None)
    monkeypatch.setattr(canary, "start_container", lambda *args, **kwargs: None)
    monkeypatch.setattr(canary, "wait_for_health", lambda *args, **kwargs: None)
    monkeypatch.setattr(canary, "require_stable_children", lambda _name: children)
    monkeypatch.setattr(canary, "running_app_dirs", lambda _name: apps)
    monkeypatch.setattr(canary, "restart_count", lambda _name: 0)
    monkeypatch.setattr(canary, "post_api", lambda *args, **kwargs: {"status": 400})
    monkeypatch.setattr(canary, "runtime_state", lambda _name: copy.deepcopy(state))
    monkeypatch.setattr(canary, "health", lambda *args, **kwargs: True)
    monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)

    result = canary.run_tamper_canary(
        {
            "version": "0.9.15",
            "source_sha": "1" * 40,
            "index_digest": f"sha256:{'2' * 64}",
            "amd64_digest": f"sha256:{'3' * 64}",
            "launcher_protocol": 1,
        },
        case="manifest",
        repository="coderluii/channelwatch",
        resources=Resources(),
        artifacts=tmp_path,
        canary_dir=tmp_path,
        manifest=manifest,
        bundle=bundle,
        target_version="0.9.18",
        startup_timeout=1,
        stability_seconds=0,
    )

    assert result["scenario_key"] == "tamper_rejection:0.9.15:manifest"
    assert result["tamper_applied"] is True
    (config / ".canary-tamper-applied").unlink()
    with pytest.raises(canary.CanaryError, match="missing or unreadable"):
        canary.run_tamper_canary(
            {
                "version": "0.9.15",
                "source_sha": "1" * 40,
                "index_digest": f"sha256:{'2' * 64}",
                "amd64_digest": f"sha256:{'3' * 64}",
                "launcher_protocol": 1,
            },
            case="manifest",
            repository="coderluii/channelwatch",
            resources=Resources(),
            artifacts=tmp_path,
            canary_dir=tmp_path,
            manifest=manifest,
            bundle=bundle,
            target_version="0.9.18",
            startup_timeout=1,
            stability_seconds=0,
        )


def test_activation_failure_canary_proves_exact_two_restart_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    canary = _load_canary_module()
    config = tmp_path / "config"
    config.mkdir()
    bundle_url = (
        "https://github.com/CoderLuii/ChannelWatch/releases/download/"
        "v0.9.18/channelwatch-app-v0.9.18.zip"
    )
    manifest = tmp_path / "channelwatch-update-v0.9.18.json"
    manifest.write_text(
        json.dumps({"payload": {"bundle_url": bundle_url}}), encoding="utf-8"
    )
    bundle = tmp_path / "channelwatch-app-v0.9.18.zip"
    bundle.write_bytes(b"candidate")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    for name, value in {
        ".canary-fault-applied": "ui\n",
        ".canary-patch-status": "patched\n",
        ".canary-fetch-complete": "bundle\n",
        ".canary-fetch-last": f"{bundle_url}\n",
    }.items():
        (config / name).write_text(value, encoding="utf-8")
    state = _failed_state(digest=digest)

    class Resources:
        def volume(self, _suffix: str) -> str:
            return str(config)

        def state_dir(self, _suffix: str) -> Path:
            return config

        def container(self, _suffix: str) -> str:
            return "historical-failure"

    children = {
        "core": {"status": "RUNNING", "pid": 201},
        "ui": {"status": "RUNNING", "pid": 202},
    }
    apps = {"core": "/app", "ui": "/app"}
    responses = iter(
        [{"status": 200}, {"status": 202, "body": {"status": "restarting"}}]
    )
    restarts = iter([4, 6])
    monkeypatch.setattr(canary, "init_volume", lambda *args, **kwargs: None)
    monkeypatch.setattr(canary, "start_container", lambda *args, **kwargs: None)
    monkeypatch.setattr(canary, "wait_for_health", lambda *args, **kwargs: None)
    monkeypatch.setattr(canary, "require_stable_children", lambda _name: children)
    monkeypatch.setattr(canary, "running_app_dirs", lambda _name: apps)
    monkeypatch.setattr(canary, "restart_count", lambda _name: next(restarts))
    monkeypatch.setattr(canary, "post_api", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(
        canary,
        "wait_for_failed_activation",
        lambda *args, **kwargs: (6, copy.deepcopy(state)),
    )
    monkeypatch.setattr(canary, "runtime_state", lambda _name: copy.deepcopy(state))
    monkeypatch.setattr(canary, "health", lambda *args, **kwargs: True)
    monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)

    result = canary.run_activation_failure_canary(
        {
            "version": "0.9.15",
            "source_sha": "1" * 40,
            "index_digest": f"sha256:{'2' * 64}",
            "amd64_digest": f"sha256:{'3' * 64}",
            "launcher_protocol": 1,
        },
        component="ui",
        repository="coderluii/channelwatch",
        resources=Resources(),
        artifacts=tmp_path,
        canary_dir=tmp_path,
        manifest=manifest,
        bundle=bundle,
        target_version="0.9.18",
        startup_timeout=1,
        stability_seconds=0,
    )

    assert result["scenario_key"] == "activation_failure:0.9.15:ui"
    assert result["restart_count_delta"] == 2
    assert result["scheduler_attempt_verified"] is True
