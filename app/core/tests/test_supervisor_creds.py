"""Regression tests for supervisor credential handling."""

import importlib.util
import errno
import json
import os
import stat
import xmlrpc.client
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient


_REPO_DIR = Path(__file__).resolve().parents[3]
_ENTRYPOINT = _REPO_DIR / "app" / "core" / "docker-entrypoint.py"
_CONF_TEMPLATE = (
    _REPO_DIR / "deploy" / "config" / "supervisor" / "supervisord.conf.template"
)


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location("channelwatch_entrypoint", _ENTRYPOINT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _restart_journal(
    *,
    active: dict | None = None,
    operation: str = "activation_rollback",
    phase: str = "commit",
    job_id: str = "job-test",
) -> dict:
    reason = (
        "activation_rollback"
        if operation == "activation_rollback"
        else "runtime_transition"
    )
    return {
        "schema": 2,
        "reason": reason,
        "operation": operation,
        "phase": phase,
        "job_id": job_id,
        "source_active": {"version": "0.9.16", "path": "/failed"},
        "replace_activation_state": True,
        "created_at": "2026-08-22T12:00:00Z",
        "control": {
            "active.json": active,
            "rollback.json": {"previous_active": active},
            "activation-pending.json": None,
            "activation-core-ready.json": None,
            "activation-ui-ready.json": None,
            "update-job.json": {
                "job_id": job_id,
                "operation": operation,
                "status": "failed",
            },
        },
    }


def _write_restart_journal(path: Path, **kwargs) -> dict:
    journal = _restart_journal(**kwargs)
    path.write_text(json.dumps(journal), encoding="utf-8")
    return journal


def _seed_mature_read_only_config(entrypoint, tmp_path: Path, settings=None) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    settings_file = config_dir / "settings.json"
    settings_file.write_text(
        json.dumps(
            settings
            or {
                "_version": entrypoint.CURRENT_SCHEMA_VERSION,
                "dvr_servers": [],
                "webhooks": [],
                "tz": "UTC",
            }
        ),
        encoding="utf-8",
    )
    settings_file.chmod(0o600)
    key_file = config_dir / "encryption.key"
    key_file.write_bytes(b"k" * 32)
    key_file.chmod(0o600)
    key_lock = config_dir / ".encryption-key.lock"
    key_lock.write_bytes(b"")
    key_lock.chmod(0o600)

    entrypoint.CONFIG_DIR = config_dir
    entrypoint.SETTINGS_FILE = settings_file
    entrypoint.CHANNELWATCH_RUNTIME_DIR = config_dir / "channelwatch-runtime"
    entrypoint.RESTART_REQUIRED_PATH = (
        entrypoint.CHANNELWATCH_RUNTIME_DIR / entrypoint.RESTART_REQUIRED_FILE
    )
    return config_dir


class TestEntrypointWritesSupervisorSocketConfig:
    def test_entrypoint_sets_verified_private_umask_before_exec(
        self, monkeypatch
    ):
        entrypoint = _load_entrypoint()
        observed_umask = None

        for name in (
            "_ensure_real_directory",
            "config_filesystem_is_read_only",
            "cleanup_restart_journal_candidates_before_validation",
            "validate_config_tree",
            "merge_bootstrap_env",
            "recover_v099_update_marker_after_image_pull",
            "chown_tree",
            "chmod_config_tree",
            "render_supervisor_config",
            "prepare_standard_streams",
            "drop_privileges",
            "verify_config_tree_writable",
            "verify_container_instance_lock",
        ):
            monkeypatch.setattr(entrypoint, name, lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            entrypoint,
            "acquire_container_instance_lock",
            lambda *_args, **_kwargs: os.open(os.devnull, os.O_RDONLY),
        )
        monkeypatch.setattr(
            entrypoint, "ensure_settings", lambda *_args, **_kwargs: False
        )
        monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint", "supervisord"])

        def inspect_exec(_program, _argv):
            nonlocal observed_umask
            observed_umask = os.umask(0o027)
            raise SystemExit(0)

        monkeypatch.setattr(entrypoint.os, "execvp", inspect_exec)
        original_umask = os.umask(0o022)
        try:
            with pytest.raises(SystemExit, match="0"):
                entrypoint.main()
        finally:
            os.umask(original_umask)

        assert observed_umask == 0o027

    def test_runtime_umask_verification_fails_closed(self):
        entrypoint = _load_entrypoint()

        with (
            patch.object(entrypoint.os, "umask", side_effect=(0o022, 0o022)),
            pytest.raises(RuntimeError, match="Failed to verify runtime umask"),
        ):
            entrypoint.set_runtime_umask()

    def test_entrypoint_writes_socket_config_without_credentials(self, tmp_path, monkeypatch):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000, 1000)

        assert not (runtime_dir / "supervisor.auth").exists()
        rendered = conf_file.read_text()
        assert str(socket_file) in rendered
        assert "__SUPERVISOR_SOCKET__" not in rendered
        assert "username =" not in rendered
        assert "password =" not in rendered

        if os.name != "nt":
            assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700

    def test_entrypoint_socket_dir_uses_runtime_user_for_custom_ids(self):
        content = _ENTRYPOINT.read_text()

        assert "_set_required_runtime_permissions(" in content
        assert '(SUPERVISOR_RUNTIME_DIR, "directory", 0o700)' in content
        assert '(SUPERVISOR_CONF, "regular", 0o640)' in content

    def test_rendered_supervisord_config_is_not_world_readable(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000, 1000)

        assert "unix_http_server" in conf_file.read_text()
        if os.name != "nt":
            assert stat.S_IMODE(conf_file.stat().st_mode) == 0o640

    def test_render_replaces_config_owned_by_previous_runtime_user(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        conf_file.write_text("stale config")
        entrypoint = _load_entrypoint()
        original_write_text = Path.write_text

        def assert_replaced_before_write(path, *args, **kwargs):
            if path == conf_file:
                assert not path.exists()
            return original_write_text(path, *args, **kwargs)

        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)
        monkeypatch.setattr(Path, "write_text", assert_replaced_before_write)

        entrypoint.render_supervisor_config(1000, 1000)

        assert "unix_http_server" in conf_file.read_text()

    def test_fresh_entrypoint_clears_restart_handoff_after_successful_render(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        restart_required = (
            tmp_path
            / "config"
            / "channelwatch-runtime"
            / "restart-required.json"
        )
        restart_required.parent.mkdir(parents=True)
        restored_app = tmp_path / "restored-app"
        journal = _write_restart_journal(
            restart_required,
            active={"version": "0.9.15", "path": str(restored_app)},
        )
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(
            entrypoint, "CHANNELWATCH_RUNTIME_DIR", restart_required.parent
        )
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)
        monkeypatch.setattr(
            entrypoint,
            "select_app_runtime_dir",
            lambda **_kwargs: restored_app,
        )

        entrypoint.render_supervisor_config(1000, 1000)

        assert str(restored_app) in conf_file.read_text(encoding="utf-8")
        assert not restart_required.exists()
        restart_lock = restart_required.parent / entrypoint.RESTART_JOURNAL_LOCK_FILE
        assert restart_lock.is_file()
        if os.name != "nt":
            assert stat.S_IMODE(restart_lock.stat().st_mode) == 0o600
        assert json.loads(
            (restart_required.parent / "active.json").read_text(encoding="utf-8")
        ) == journal["control"]["active.json"]

    def test_failed_render_keeps_restart_handoff_fail_closed(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        restart_required = (
            tmp_path
            / "config"
            / "channelwatch-runtime"
            / "restart-required.json"
        )
        restart_required.parent.mkdir(parents=True)
        _write_restart_journal(restart_required)
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(
            entrypoint, "CHANNELWATCH_RUNTIME_DIR", restart_required.parent
        )
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(
            entrypoint, "SUPERVISOR_TEMPLATE", tmp_path / "missing-template"
        )
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)

        entrypoint.render_supervisor_config(1000, 1000)

        assert restart_required.exists()

    def test_failed_config_permissions_keep_restart_handoff_fail_closed(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        restart_required = (
            tmp_path
            / "config"
            / "channelwatch-runtime"
            / "restart-required.json"
        )
        restart_required.parent.mkdir(parents=True)
        _write_restart_journal(restart_required)
        entrypoint = _load_entrypoint()
        original_chmod = entrypoint._chmod_path_no_follow

        def fail_config_chmod(path, mode, *args, **kwargs):
            if path == conf_file:
                raise RuntimeError("read-only test config")
            return original_chmod(path, mode, *args, **kwargs)

        monkeypatch.setattr(
            entrypoint, "CHANNELWATCH_RUNTIME_DIR", restart_required.parent
        )
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)
        monkeypatch.setattr(entrypoint, "_chmod_path_no_follow", fail_config_chmod)

        with pytest.raises(RuntimeError, match="read-only test config"):
            entrypoint.render_supervisor_config(1000, 1000)

        assert restart_required.exists()

    def test_entrypoint_replays_abort_to_image_and_clears_activation_state(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        restart_required = runtime_state / "restart-required.json"
        (runtime_state / "active.json").write_text(
            json.dumps({"version": "0.9.16", "path": "/failed"}),
            encoding="utf-8",
        )
        for name in (
            "activation-pending.json",
            "activation-core-ready.json",
            "activation-ui-ready.json",
            "activation-failed-worker-generation.json",
        ):
            (runtime_state / name).write_text("{}", encoding="utf-8")
        journal = _write_restart_journal(
            restart_required,
            active=None,
            operation="apply",
            phase="abort",
        )
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)

        assert entrypoint.replay_restart_required_journal() == journal
        assert entrypoint.replay_restart_required_journal() == journal

        assert not (runtime_state / "active.json").exists()
        assert not list(runtime_state.glob("activation-*.json"))
        assert json.loads(
            (runtime_state / "update-job.json").read_text(encoding="utf-8")
        )["job_id"] == "job-test"
        assert restart_required.exists()

    def test_v0918_image_pull_recovers_stale_v099_marker_without_app_data_changes(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        runtime_state = config_dir / "channelwatch-runtime"
        release_dir = runtime_state / "releases" / "v0.9.18"
        release_dir.mkdir(parents=True)
        settings = config_dir / "settings.json"
        key_file = config_dir / "encryption.key"
        settings.write_bytes(
            b'{"dvr_servers":[{"id":"dvr-1","api_key":"enc:v1:private"}],'
            b'"webhooks":[{"id":"hook-1","url":"enc:v1:url",'
            b'"secret":"enc:v1:secret"}]}\n'
        )
        key_file.write_bytes(b"CWKEY3\nmanaged-envelope-bytes\n")
        (runtime_state / "active.json").write_text(
            json.dumps(
                {
                    "version": "0.9.18",
                    "path": str(release_dir),
                    "runtime_abi": "channelwatch-runtime-v1",
                    "settings_schema_version": 7,
                    "activated_at": "2026-08-24T12:00:00Z",
                    "manifest": {"bundle_sha256": "a" * 64},
                    "metadata": {"version": "0.9.18"},
                }
            ),
            encoding="utf-8",
        )
        (runtime_state / "update-job.json").write_text(
            json.dumps(
                {
                    "job_id": "published-v099-job",
                    "operation": "apply",
                    "status": "success",
                    "version": "0.9.18",
                    "backup_path": "/private/path-that-must-not-survive",
                    "message": "Update activated and ChannelWatch started successfully.",
                }
            ),
            encoding="utf-8",
        )
        rollback = runtime_state / "rollback.json"
        rollback.write_bytes(b'{"previous_active":null,"backup_path":"private"}\n')
        protected_before = {
            "settings": settings.read_bytes(),
            "key": key_file.read_bytes(),
        }
        active_before = (runtime_state / "active.json").read_bytes()
        job_before = (runtime_state / "update-job.json").read_bytes()

        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(entrypoint, "SETTINGS_FILE", settings)
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        restart_required = runtime_state / "restart-required.json"
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)

        assert entrypoint.recover_v099_update_marker_after_image_pull(
            image_version="0.9.18"
        ) is True

        # Publication is write-ahead: before replay, either both old records
        # remain authoritative or the complete journal is available.
        assert (runtime_state / "active.json").read_bytes() == active_before
        assert (runtime_state / "update-job.json").read_bytes() == job_before
        assert restart_required.is_file()
        assert entrypoint.recover_v099_update_marker_after_image_pull(
            image_version="0.9.18"
        ) is False

        journal = entrypoint.replay_restart_required_journal()
        assert isinstance(journal, dict)
        assert not (runtime_state / "active.json").exists()
        job = json.loads(
            (runtime_state / "update-job.json").read_text(encoding="utf-8")
        )
        assert job["operation"] == "image_refresh_recovery"
        assert job["status"] == "validating"
        assert job["image_pull_completed"] is False
        assert job["legacy_pointer_deactivated"] is True
        assert job["startup_validation_pending"] is True
        assert job["startup_components"] == {}
        assert job["restart_required"] is False
        assert "/private/path" not in json.dumps(job)
        assert entrypoint.clear_completed_restart_handoff(journal) is True
        assert not restart_required.exists()
        assert settings.read_bytes() == protected_before["settings"]
        assert key_file.read_bytes() == protected_before["key"]
        assert json.loads(rollback.read_text()) == {
            "previous_active": None,
            "backup_path": "private",
        }

    def test_v0918_image_pull_does_not_reclassify_adopted_activation(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        active = {
            "version": "0.9.18",
            "path": str(runtime_state / "releases" / "v0.9.18"),
            "activation_id": "adopted-generation",
            "activation_protocol": 1,
        }
        job = {
            "operation": "apply",
            "status": "success",
            "version": "0.9.18",
        }
        (runtime_state / "active.json").write_text(json.dumps(active))
        (runtime_state / "update-job.json").write_text(json.dumps(job))
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(
            entrypoint,
            "RESTART_REQUIRED_PATH",
            runtime_state / "restart-required.json",
        )

        assert entrypoint.recover_v099_update_marker_after_image_pull(
            image_version="0.9.18"
        ) is False
        assert json.loads((runtime_state / "active.json").read_text()) == active
        assert json.loads((runtime_state / "update-job.json").read_text()) == job

    def test_v0918_image_pull_restarts_incomplete_validation_generation(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        original_job = {
            "job_id": "old-container-attempt",
            "operation": "image_refresh_recovery",
            "status": "validating",
            "version": "0.9.18",
            "legacy_pointer_deactivated": True,
            "startup_validation_id": "old-generation",
            "startup_validation_pending": True,
            "startup_components": {
                "core": {"healthy": True, "ready_at": "old"}
            },
            "image_pull_completed": False,
        }
        (runtime_state / "update-job.json").write_text(json.dumps(original_job))
        entrypoint = _load_entrypoint()
        restart_required = runtime_state / "restart-required.json"
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)

        assert entrypoint.recover_v099_update_marker_after_image_pull(
            image_version="0.9.18"
        ) is True
        assert json.loads((runtime_state / "update-job.json").read_text()) == (
            original_job
        )

        journal = entrypoint.replay_restart_required_journal()
        assert isinstance(journal, dict)
        retried = json.loads((runtime_state / "update-job.json").read_text())
        assert retried["status"] == "validating"
        assert retried["job_id"] != original_job["job_id"]
        assert retried["startup_validation_id"] != "old-generation"
        assert retried["startup_components"] == {}
        assert retried["image_pull_completed"] is False

    def test_v0918_image_pull_journal_write_failure_preserves_old_pair(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        active = {
            "version": "0.9.18",
            "path": str(runtime_state / "releases" / "v0.9.18"),
            "runtime_abi": "channelwatch-runtime-v1",
            "settings_schema_version": 7,
        }
        job = {
            "job_id": "published-v099-job",
            "operation": "apply",
            "status": "success",
            "version": "0.9.18",
        }
        (runtime_state / "active.json").write_text(json.dumps(active))
        (runtime_state / "update-job.json").write_text(json.dumps(job))
        entrypoint = _load_entrypoint()
        restart_required = runtime_state / "restart-required.json"
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)
        real_atomic_write_json = entrypoint.atomic_write_json

        def fail_journal_write(path, payload, *, indent=2):
            if path == restart_required:
                raise OSError("injected journal publication failure")
            return real_atomic_write_json(path, payload, indent=indent)

        monkeypatch.setattr(entrypoint, "atomic_write_json", fail_journal_write)

        with pytest.raises(OSError, match="injected journal"):
            entrypoint.recover_v099_update_marker_after_image_pull(
                image_version="0.9.18"
            )
        assert json.loads((runtime_state / "active.json").read_text()) == active
        assert json.loads((runtime_state / "update-job.json").read_text()) == job
        assert not restart_required.exists()

    @pytest.mark.parametrize(
        "journal_mutation",
        [
            lambda payload: {**payload, "schema": 99},
            lambda payload: {**payload, "unknown": True},
            lambda payload: {
                **payload,
                "control": {
                    **payload["control"],
                    "unexpected.json": {},
                },
            },
            lambda payload: {**payload, "replace_activation_state": False},
        ],
    )
    def test_entrypoint_rejects_invalid_restart_journal(
        self, tmp_path, monkeypatch, journal_mutation
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        restart_required = runtime_state / "restart-required.json"
        restart_required.write_text(
            json.dumps(journal_mutation(_restart_journal())), encoding="utf-8"
        )
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)

        with pytest.raises(RuntimeError, match="Runtime transition journal"):
            entrypoint.replay_restart_required_journal()

        assert restart_required.exists()

    @pytest.mark.parametrize("journal_kind", ["directory", "broken-symlink"])
    def test_entrypoint_treats_non_regular_restart_journal_as_blocking(
        self, tmp_path, monkeypatch, journal_kind
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        restart_required = runtime_state / "restart-required.json"
        if journal_kind == "directory":
            restart_required.mkdir()
        else:
            restart_required.symlink_to(runtime_state / "missing-target")
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)

        with pytest.raises(RuntimeError, match="not a regular file"):
            entrypoint.replay_restart_required_journal()

        assert restart_required.lstat()

    def test_entrypoint_publishes_active_selection_last(self, tmp_path, monkeypatch):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        restart_required = runtime_state / "restart-required.json"
        _write_restart_journal(
            restart_required,
            active={"version": "0.9.15", "path": "/restored"},
        )
        entrypoint = _load_entrypoint()
        writes = []
        original_atomic_write_json = entrypoint.atomic_write_json

        def record_write(path, payload, **kwargs):
            writes.append(path.name)
            return original_atomic_write_json(path, payload, **kwargs)

        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)
        monkeypatch.setattr(entrypoint, "atomic_write_json", record_write)

        entrypoint.replay_restart_required_journal()

        assert writes[-1] == "active.json"

    def test_chown_failure_keeps_restart_handoff_fail_closed(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        runtime_state = tmp_path / "config" / "channelwatch-runtime"
        runtime_state.mkdir(parents=True)
        restart_required = runtime_state / "restart-required.json"
        _write_restart_journal(restart_required)
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)
        monkeypatch.setattr(entrypoint, "chown_path", lambda *_args: False)

        with pytest.raises(RuntimeError, match="required ownership"):
            entrypoint.render_supervisor_config(1000, 1000)

        assert restart_required.exists()

    def test_changed_restart_generation_is_not_cleared(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        runtime_state = tmp_path / "config" / "channelwatch-runtime"
        runtime_state.mkdir(parents=True)
        restart_required = runtime_state / "restart-required.json"
        _write_restart_journal(restart_required, job_id="generation-a")
        replacement = _restart_journal(job_id="generation-b")
        entrypoint = _load_entrypoint()
        replaced = False

        def replace_journal_once(*_args):
            nonlocal replaced
            if not replaced:
                entrypoint.atomic_write_json(restart_required, replacement)
                replaced = True
            return True

        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)
        monkeypatch.setattr(entrypoint, "chown_path", replace_journal_once)

        entrypoint.render_supervisor_config(1000, 1000)

        assert json.loads(restart_required.read_text(encoding="utf-8")) == replacement

    @pytest.mark.parametrize("target_kind", ["file", "directory"])
    def test_config_permission_repair_rejects_external_symlink_without_touching_target(
        self, tmp_path, target_kind
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        if target_kind == "file":
            external = tmp_path / "outside.txt"
            external.write_text("outside", encoding="utf-8")
        else:
            external = tmp_path / "outside-dir"
            external.mkdir()
            (external / "marker.txt").write_text("outside", encoding="utf-8")
        original_mode = stat.S_IMODE(external.stat().st_mode)
        unsafe = config_dir / (
            "settings.json" if target_kind == "file" else "channelwatch-runtime"
        )
        unsafe.symlink_to(external, target_is_directory=target_kind == "directory")
        entrypoint = _load_entrypoint()

        for repair in (
            lambda: entrypoint.chown_tree(config_dir, 1000, 1000),
            lambda: entrypoint.chmod_config_tree(config_dir),
        ):
            with pytest.raises(RuntimeError, match="symbolic link"):
                repair()

        assert unsafe.is_symlink()
        assert stat.S_IMODE(external.stat().st_mode) == original_mode
        if target_kind == "file":
            assert external.read_text(encoding="utf-8") == "outside"
        else:
            assert (external / "marker.txt").read_text(encoding="utf-8") == "outside"

    def test_config_permission_repair_rejects_external_hard_link_without_touching_target(
        self, tmp_path
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        external = tmp_path / "outside-sensitive"
        external.write_bytes(b"outside\x00bytes")
        external.chmod(0o600)
        inside = config_dir / "settings.json"
        os.link(external, inside)
        original_bytes = external.read_bytes()
        original_mode = stat.S_IMODE(external.stat().st_mode)
        original_owner = (external.stat().st_uid, external.stat().st_gid)
        entrypoint = _load_entrypoint()

        for operation in (
            lambda: entrypoint.validate_config_tree(config_dir),
            lambda: entrypoint.chown_tree(config_dir, 1000, 1000),
            lambda: entrypoint.chmod_config_tree(config_dir),
        ):
            with pytest.raises(RuntimeError, match="hard-linked"):
                operation()

        assert external.read_bytes() == original_bytes
        assert stat.S_IMODE(external.stat().st_mode) == original_mode
        assert (external.stat().st_uid, external.stat().st_gid) == original_owner
        assert inside.stat().st_ino == external.stat().st_ino

    def test_config_walk_retries_transient_virtiofs_identity_until_it_converges(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings_file = config_dir / "settings.json"
        settings_file.write_text("{}", encoding="utf-8")
        entrypoint = _load_entrypoint()
        real_fstat = entrypoint.os.fstat
        remaining_mismatches = 2
        sleeps = []

        def transient_virtualized_fstat(file_descriptor):
            nonlocal remaining_mismatches
            metadata = real_fstat(file_descriptor)
            if stat.S_ISREG(metadata.st_mode) and remaining_mismatches:
                remaining_mismatches -= 1
                fields = list(metadata)
                fields[1] += 1
                return os.stat_result(fields)
            return metadata

        monkeypatch.setattr(entrypoint.os, "fstat", transient_virtualized_fstat)
        monkeypatch.setattr(
            entrypoint, "ownership_metadata_is_virtualized", lambda _path: True
        )
        monkeypatch.setattr(entrypoint.time, "sleep", sleeps.append)

        entrypoint.validate_config_tree(config_dir)

        assert remaining_mismatches == 0
        assert sleeps == [0.05, 0.1]

    def test_config_walk_rejects_persistent_virtiofs_identity_mismatch(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text("{}", encoding="utf-8")
        entrypoint = _load_entrypoint()
        real_fstat = entrypoint.os.fstat
        sleeps = []

        def persistently_virtualized_fstat(file_descriptor):
            metadata = real_fstat(file_descriptor)
            if stat.S_ISREG(metadata.st_mode):
                fields = list(metadata)
                fields[1] += 1
                return os.stat_result(fields)
            return metadata

        monkeypatch.setattr(entrypoint.os, "fstat", persistently_virtualized_fstat)
        monkeypatch.setattr(
            entrypoint, "ownership_metadata_is_virtualized", lambda _path: True
        )
        monkeypatch.setattr(entrypoint.time, "sleep", sleeps.append)

        with pytest.raises(RuntimeError, match="remained inconsistent"):
            entrypoint.validate_config_tree(config_dir)

        assert sleeps == list(
            entrypoint.VIRTUALIZED_IDENTITY_RETRY_DELAYS_SECONDS
        )

    def test_config_walk_rejects_non_virtiofs_replacement_without_retry(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings_file = config_dir / "settings.json"
        settings_file.write_text("original", encoding="utf-8")
        replacement = tmp_path / "replacement"
        replacement.write_text("replacement", encoding="utf-8")
        entrypoint = _load_entrypoint()
        real_open = entrypoint.os.open
        replaced = False
        sleeps = []

        def replace_before_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if path == "settings.json" and not replaced:
                replaced = True
                replacement.replace(settings_file)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(entrypoint.os, "open", replace_before_open)
        monkeypatch.setattr(
            entrypoint, "ownership_metadata_is_virtualized", lambda _path: False
        )
        monkeypatch.setattr(entrypoint.time, "sleep", sleeps.append)

        with pytest.raises(RuntimeError, match="changed while it was inspected"):
            entrypoint.validate_config_tree(config_dir)

        assert replaced is True
        assert sleeps == []

    @pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "fifo"])
    def test_config_walk_never_retries_unsafe_objects_on_virtiofs(
        self, tmp_path, monkeypatch, unsafe_kind
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        external = tmp_path / "outside"
        external.write_text("outside", encoding="utf-8")
        unsafe = config_dir / "settings.json"
        if unsafe_kind == "symlink":
            unsafe.symlink_to(external)
        elif unsafe_kind == "hardlink":
            os.link(external, unsafe)
        else:
            if not hasattr(os, "mkfifo"):
                pytest.skip("FIFOs are unavailable on this platform")
            os.mkfifo(unsafe)
        entrypoint = _load_entrypoint()
        sleeps = []

        monkeypatch.setattr(
            entrypoint, "ownership_metadata_is_virtualized", lambda _path: True
        )
        monkeypatch.setattr(entrypoint.time, "sleep", sleeps.append)

        with pytest.raises(RuntimeError):
            entrypoint.validate_config_tree(config_dir)

        assert sleeps == []
        assert external.read_text(encoding="utf-8") == "outside"

    def test_supervisor_config_hard_link_is_rejected_without_touching_target(
        self, tmp_path
    ):
        external = tmp_path / "outside-supervisor"
        external.write_bytes(b"outside supervisor bytes")
        external.chmod(0o600)
        supervisor_config = tmp_path / "supervisord.conf"
        os.link(external, supervisor_config)
        original_bytes = external.read_bytes()
        original_mode = stat.S_IMODE(external.stat().st_mode)
        original_owner = (external.stat().st_uid, external.stat().st_gid)
        entrypoint = _load_entrypoint()

        with pytest.raises(RuntimeError, match="hard-linked atomic-write target"):
            entrypoint.atomic_write_text(supervisor_config, "unsafe replacement\n")

        assert external.read_bytes() == original_bytes
        assert stat.S_IMODE(external.stat().st_mode) == original_mode
        assert (external.stat().st_uid, external.stat().st_gid) == original_owner
        assert supervisor_config.stat().st_ino == external.stat().st_ino

    def test_preflight_removes_only_internal_candidate_link_before_validation(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        runtime_dir = config_dir / "channelwatch-runtime"
        runtime_dir.mkdir(parents=True)
        canonical = runtime_dir / "restart-required.json"
        canonical.write_text("{}", encoding="utf-8")
        candidate = runtime_dir / ".restart-required.json.candidate-crashed"
        os.link(canonical, candidate)
        unrelated = runtime_dir / "operator-data"
        unrelated.write_text("preserve", encoding="utf-8")
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_dir)

        entrypoint.cleanup_restart_journal_candidates_before_validation()
        entrypoint.validate_config_tree(config_dir)

        assert not candidate.exists()
        assert canonical.stat().st_nlink == 1
        assert canonical.read_text(encoding="utf-8") == "{}"
        assert unrelated.read_text(encoding="utf-8") == "preserve"

    def test_main_rejects_settings_symlink_before_settings_are_read(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        external = tmp_path / "outside-settings.json"
        external.write_text('{"outside":true}', encoding="utf-8")
        (config_dir / "settings.json").symlink_to(external)
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(entrypoint, "SETTINGS_FILE", config_dir / "settings.json")
        monkeypatch.setattr(
            entrypoint,
            "ensure_settings",
            lambda *_args: pytest.fail("settings access must follow config validation"),
        )

        with pytest.raises(RuntimeError, match="symbolic link"):
            entrypoint.main()

        assert external.read_text(encoding="utf-8") == '{"outside":true}'

    @pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "directory", "fifo"])
    def test_entrypoint_settings_reader_rejects_linked_and_special_paths(
        self, tmp_path, monkeypatch, unsafe_kind
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        external = tmp_path / "outside-settings.json"
        external.write_text('{"root_only_secret":"must-not-be-read"}', encoding="utf-8")
        settings_file = config_dir / "settings.json"
        if unsafe_kind == "symlink":
            settings_file.symlink_to(external)
        elif unsafe_kind == "hardlink":
            os.link(external, settings_file)
        elif unsafe_kind == "directory":
            settings_file.mkdir()
        else:
            if not hasattr(os, "mkfifo"):
                pytest.skip("FIFOs are unavailable on this platform")
            os.mkfifo(settings_file)

        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(entrypoint, "SETTINGS_FILE", settings_file)

        settings, can_write = entrypoint.load_settings()

        assert can_write is False
        assert "root_only_secret" not in settings

    def test_entrypoint_settings_reader_rejects_oversized_file(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings_file = config_dir / "settings.json"
        settings_file.write_bytes(b"x" * ((8 * 1024 * 1024) + 1))
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(entrypoint, "SETTINGS_FILE", settings_file)

        settings, can_write = entrypoint.load_settings()

        assert can_write is False
        assert settings == entrypoint.DEFAULT_SETTINGS

    def test_entrypoint_settings_reader_rejects_post_validation_symlink_swap(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings_file = config_dir / "settings.json"
        settings_file.write_text('{"tz":"UTC"}', encoding="utf-8")
        external = tmp_path / "outside-settings.json"
        external.write_text('{"root_only_secret":"must-not-be-read"}', encoding="utf-8")
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(entrypoint, "SETTINGS_FILE", settings_file)
        original_open_directory = entrypoint._open_real_directory

        def swap_after_directory_open(path, *, purpose):
            directory_fd = original_open_directory(path, purpose=purpose)
            settings_file.unlink()
            settings_file.symlink_to(external)
            return directory_fd

        monkeypatch.setattr(entrypoint, "_open_real_directory", swap_after_directory_open)

        settings, can_write = entrypoint.load_settings()

        assert can_write is False
        assert "root_only_secret" not in settings

    def test_entrypoint_settings_reader_rejects_same_size_in_place_change(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings_file = config_dir / "settings.json"
        settings_file.write_text('{"tz":"UTC"}', encoding="utf-8")
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(entrypoint, "SETTINGS_FILE", settings_file)
        original_read = os.read
        changed = False

        def change_after_first_read(descriptor, count):
            nonlocal changed
            payload = original_read(descriptor, count)
            if payload and not changed:
                changed = True
                original_mtime = settings_file.stat().st_mtime_ns
                settings_file.write_text('{"tz":"PST"}', encoding="utf-8")
                changed_time = original_mtime + 1_000_000_000
                os.utime(
                    settings_file,
                    ns=(changed_time, changed_time),
                )
            return payload

        monkeypatch.setattr(entrypoint.os, "read", change_after_first_read)

        settings, can_write = entrypoint.load_settings()

        assert can_write is False
        assert settings == entrypoint.DEFAULT_SETTINGS

    def test_supervisor_runtime_directory_symlink_is_rejected_without_chowning_target(
        self, tmp_path, monkeypatch
    ):
        external = tmp_path / "outside-runtime"
        external.mkdir()
        original_mode = stat.S_IMODE(external.stat().st_mode)
        runtime_dir = tmp_path / "channelwatch"
        runtime_dir.symlink_to(external, target_is_directory=True)
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", runtime_dir / "supervisor.sock")
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", tmp_path / "supervisord.conf")

        with pytest.raises(RuntimeError, match="Supervisor runtime"):
            entrypoint.render_supervisor_config(1000, 1000)

        assert runtime_dir.is_symlink()
        assert stat.S_IMODE(external.stat().st_mode) == original_mode

    def test_supervisor_config_symlink_is_rejected_without_touching_target(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        conf_file = tmp_path / "supervisord.conf"
        external = tmp_path / "outside-config"
        external.write_text("outside", encoding="utf-8")
        conf_file.symlink_to(external)
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", runtime_dir / "supervisor.sock")
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        with pytest.raises(RuntimeError, match="non-regular atomic-write target"):
            entrypoint.render_supervisor_config(1000, 1000)

        assert conf_file.is_symlink()
        assert external.read_text(encoding="utf-8") == "outside"

    def test_secure_atomic_write_skips_preplanted_temp_symlink_collision(
        self, tmp_path, monkeypatch
    ):
        target = tmp_path / "supervisord.conf"
        external = tmp_path / "outside-temp-target"
        external.write_text("outside", encoding="utf-8")
        collision = tmp_path / ".supervisord.conf.tmp-collision"
        collision.symlink_to(external)
        tokens = iter(("collision", "safe"))
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(
            entrypoint.uuid,
            "uuid4",
            lambda: SimpleNamespace(hex=next(tokens)),
        )

        entrypoint.atomic_write_text(target, "secure\n")

        assert target.is_file() and not target.is_symlink()
        assert target.read_text(encoding="utf-8") == "secure\n"
        assert collision.is_symlink()
        assert external.read_text(encoding="utf-8") == "outside"

    def test_restart_journal_lock_symlink_is_rejected_and_never_followed(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        restart_required = runtime_state / "restart-required.json"
        _write_restart_journal(restart_required)
        external = tmp_path / "outside-lock-target"
        external.write_text("outside", encoding="utf-8")
        lock_path = runtime_state / "restart-required.lock"
        lock_path.symlink_to(external)
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        monkeypatch.setattr(entrypoint, "RESTART_REQUIRED_PATH", restart_required)

        with pytest.raises(RuntimeError, match="transition lock safely"):
            entrypoint.replay_restart_required_journal()

        assert lock_path.is_symlink()
        assert external.read_text(encoding="utf-8") == "outside"

    @pytest.mark.skipif(os.name == "nt", reason="requires POSIX read-only fallback")
    def test_existing_restart_lock_supports_read_only_open_without_chmod(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        lock_path = runtime_state / "restart-required.lock"
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        real_open = entrypoint.os.open
        fchmod_calls: list[tuple] = []

        def read_only_open(path, flags, *args, **kwargs):
            if path == entrypoint.RESTART_JOURNAL_LOCK_FILE and flags & os.O_RDWR:
                raise OSError(errno.EROFS, "simulated read-only remount")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(entrypoint.os, "open", read_only_open)
        monkeypatch.setattr(
            entrypoint.os,
            "fchmod",
            lambda *args: fchmod_calls.append(args),
        )

        with entrypoint.restart_transition_lock():
            assert lock_path.is_file()

        assert fchmod_calls == []
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="requires POSIX read-only fallback")
    def test_read_only_restart_lock_with_wrong_mode_fails_closed(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        lock_path = runtime_state / "restart-required.lock"
        lock_path.write_bytes(b"")
        lock_path.chmod(0o640)
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        real_open = entrypoint.os.open

        def read_only_open(path, flags, *args, **kwargs):
            if path == entrypoint.RESTART_JOURNAL_LOCK_FILE and flags & os.O_RDWR:
                raise OSError(errno.EROFS, "simulated read-only remount")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(entrypoint.os, "open", read_only_open)

        with pytest.raises(RuntimeError, match="open runtime transition lock safely"):
            with entrypoint.restart_transition_lock():
                pass

    @pytest.mark.skipif(os.name == "nt", reason="requires POSIX read-only fallback")
    def test_read_only_runtime_without_existing_restart_lock_fails_closed(
        self, tmp_path, monkeypatch
    ):
        runtime_state = tmp_path / "channelwatch-runtime"
        runtime_state.mkdir()
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "CHANNELWATCH_RUNTIME_DIR", runtime_state)
        real_open = entrypoint.os.open

        def read_only_open(path, flags, *args, **kwargs):
            if path == entrypoint.RESTART_JOURNAL_LOCK_FILE and flags & os.O_RDWR:
                raise OSError(errno.EROFS, "simulated read-only remount")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(entrypoint.os, "open", read_only_open)

        with pytest.raises(RuntimeError, match="open runtime transition lock safely"):
            with entrypoint.restart_transition_lock():
                pass

    def test_supervisor_socket_symlink_is_rejected_without_touching_target(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        runtime_dir.mkdir()
        external = tmp_path / "outside-socket-target"
        external.write_text("outside", encoding="utf-8")
        socket_file = runtime_dir / "supervisor.sock"
        socket_file.symlink_to(external)
        entrypoint = _load_entrypoint()
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", tmp_path / "supervisord.conf")

        with pytest.raises(RuntimeError, match="symbolic link"):
            entrypoint.render_supervisor_config(1000, 1000)

        assert socket_file.is_symlink()
        assert external.read_text(encoding="utf-8") == "outside"


class TestEntrypointDoesNotExportEnvVars:
    def test_entrypoint_does_not_export_env_vars(self, tmp_path, monkeypatch):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.delenv("SUPERVISOR_USER", raising=False)
        monkeypatch.delenv("SUPERVISOR_PASS", raising=False)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000, 1000)

        assert os.environ.get("SUPERVISOR_USER") is None
        assert os.environ.get("SUPERVISOR_PASS") is None


class TestMainUsesSupervisorSocket:
    def test_supervisor_socket_defaults_to_tmp_runtime_dir(self):
        import ui.backend.main as ui_main

        assert ui_main.SUPERVISOR_SOCKET_FILE == os.path.join(
            "/tmp/channelwatch", "supervisor.sock"
        )

    def test_main_creates_socket_transport_without_credentials(self, tmp_path):
        import ui.backend.main as ui_main

        socket_file = tmp_path / "supervisor.sock"
        socket_file.write_text("")
        server = MagicMock()
        with (
            patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", str(socket_file)),
            patch("ui.backend.main.xmlrpc.client.ServerProxy", return_value=server) as proxy,
        ):
            assert ui_main.get_supervisor_proxy() is server

        url = proxy.call_args.args[0]
        transport = proxy.call_args.kwargs["transport"]
        assert url == "http://channelwatch-supervisor/RPC2"
        assert "@" not in url
        assert isinstance(transport, ui_main._UnixSocketTransport)

    def test_supervisor_protocol_errors_are_logged_without_credentials(
        self, tmp_path, capsys
    ):
        import ui.backend.main as ui_main

        socket_file = tmp_path / "supervisor.sock"
        socket_file.write_text("")
        protocol_error = xmlrpc.client.ProtocolError(
            "http://channelwatch-supervisor/RPC2",
            401,
            "Unauthorized",
            {},
        )

        with (
            patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", str(socket_file)),
            patch(
                "ui.backend.main.xmlrpc.client.ServerProxy", side_effect=protocol_error
            ),
        ):
            assert ui_main.get_supervisor_proxy() is None

        output = capsys.readouterr().out
        assert "ProtocolError 401 Unauthorized" in output
        assert "@" not in output


class TestMainGracefulDegrade:
    def test_main_graceful_degrade_when_file_missing(self, tmp_path):
        import ui.backend.main as ui_main

        missing = str(tmp_path / "no_such_file.sock")

        with patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", missing):
            proxy = ui_main.get_supervisor_proxy()
        assert proxy is None


class TestTemplateNoCreds:
    def test_template_keeps_supervisor_runtime_files_off_app_root(self):
        content = _CONF_TEMPLATE.read_text()

        assert "logfile=/dev/null" in content
        assert "logfile_maxbytes=0" in content
        assert "pidfile=/tmp/supervisord.pid" in content
        assert "childlogdir=/tmp" in content
        assert "[unix_http_server]" in content
        assert "inet_http_server" not in content
        assert "username =" not in content
        assert "password =" not in content

    def test_template_no_creds_in_program_ui_environment(self):
        content = _CONF_TEMPLATE.read_text()

        in_ui_section = False
        section_found = False

        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "[program:ui]":
                in_ui_section = True
                section_found = True
                continue
            if stripped.startswith("[") and stripped.endswith("]") and in_ui_section:
                break
            if in_ui_section and stripped.lower().startswith("environment="):
                assert "SUPERVISOR_USER" not in line
                assert "SUPERVISOR_PASS" not in line
                break

        assert section_found

    def test_template_uses_image_stable_runtime_launcher(self):
        content = _CONF_TEMPLATE.read_text()

        assert "python -u /app/core/runtime_launcher.py core --stay-alive" in content
        assert "python -u /app/core/runtime_launcher.py ui" in content
        assert "CHANNELWATCH_ACTIVE_APP_DIR=__APP_DIR__" in content
        assert "CHANNELWATCH_ACTIVE_STATIC_UI_DIR=__STATIC_UI_DIR__" in content
        assert "directory=/app" in content
        assert "command=uvicorn ui.backend.main:app" not in content


class TestRestartCoreDegradedResponse:
    def test_restart_core_returns_degraded_response_when_socket_missing(
        self, tmp_path
    ):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"dvr_servers": [], "api_key": "test-key"}))
        missing_socket = str(tmp_path / "no_such_file.sock")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", missing_socket),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_AUTH_MISSING"
        assert "Supervisor control socket is unavailable" in detail["message"]
        assert "recreate the local supervisor socket" in detail["remediation"]


class TestRestartControlEndpoints:
    def test_restart_core_supervisor_success_updates_start_time(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        server = MagicMock()
        started_after = datetime.now(timezone.utc)

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "CORE_LAST_START_TIME", None),
            patch.object(ui_main, "get_supervisor_proxy", return_value=server),
            patch.object(ui_main.asyncio, "sleep", AsyncMock()),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")
            updated_start = ui_main.CORE_LAST_START_TIME

        assert resp.status_code == 202
        assert resp.json()["message"] == "Restart command sent to process 'core'."
        server.supervisor.stopProcess.assert_called_once_with("core", True)
        server.supervisor.startProcess.assert_called_once_with("core", True)
        assert updated_start is not None
        assert updated_start >= started_after

    def test_restart_core_supervisor_401_fault_returns_401(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        server = MagicMock()
        server.supervisor.stopProcess.side_effect = xmlrpc.client.Fault(
            401, "Unauthorized"
        )

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "get_supervisor_proxy", return_value=server),
            patch.object(ui_main.asyncio, "sleep", AsyncMock()),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")

        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_AUTH_FAILED"
        assert detail["message"] == "Supervisor authentication failed."
        assert "regenerate supervisor credentials" in detail["remediation"]
        server.supervisor.startProcess.assert_not_called()

    def test_restart_core_supervisor_non_401_fault_returns_500(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        server = MagicMock()
        server.supervisor.stopProcess.side_effect = xmlrpc.client.Fault(42, "boom")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "get_supervisor_proxy", return_value=server),
            patch.object(ui_main.asyncio, "sleep", AsyncMock()),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_COMMAND_FAILED"
        assert detail["message"] == "Supervisor command failed: boom"
        assert "supervisord logs" in detail["remediation"]
        server.supervisor.startProcess.assert_not_called()

    def test_update_restart_adapter_uses_validated_runtime_launcher(self):
        import ui.backend.main as ui_main

        with patch("core.runtime_launcher.request_container_restart") as restart:
            assert ui_main._schedule_container_restart_for_update() is True

        restart.assert_called_once_with()

    def test_update_restart_adapter_reports_validated_signal_failure(self):
        import ui.backend.main as ui_main

        with patch(
            "core.runtime_launcher.request_container_restart",
            side_effect=RuntimeError("supervisor unavailable"),
        ) as restart:
            assert ui_main._schedule_container_restart_for_update() is False

        restart.assert_called_once_with()

    def test_restart_container_uses_truthful_validated_restart(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(
                ui_main,
                "_schedule_container_restart_for_update",
                return_value=True,
            ) as restart,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_container")

        assert resp.status_code == 202
        restart.assert_called_once_with()

    def test_restart_container_returns_503_when_no_supervisor_or_pid_one(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(
                ui_main,
                "_schedule_container_restart_for_update",
                return_value=False,
            ) as restart,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_container")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_NOT_AVAILABLE"
        restart.assert_called_once_with()


class TestReadOnlyRuntimePreflight:
    def test_mature_managed_config_is_accepted_without_changing_files(
        self, tmp_path
    ):
        entrypoint = _load_entrypoint()
        config_dir = _seed_mature_read_only_config(entrypoint, tmp_path)
        before = {
            path.relative_to(config_dir): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in config_dir.iterdir()
            if path.is_file()
        }

        entrypoint.validate_read_only_runtime_state()

        after = {
            path.relative_to(config_dir): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in config_dir.iterdir()
            if path.is_file()
        }
        assert after == before

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("missing_settings", "valid existing settings"),
            ("broad_settings_mode", "settings.json must be owner-only"),
            ("old_schema", "settings schema must be reconciled"),
            ("future_schema", "settings schema must be reconciled"),
            ("invalid_migration", "migration journal is invalid"),
            ("unknown_migration", "interrupted settings migration"),
            ("started_migration", "interrupted settings migration"),
            ("missing_key", "managed key must already exist"),
            ("wrong_key_mode", "managed key is not a private"),
            ("short_key", "managed key requires migration"),
            ("missing_key_lock", "managed-key lock must already exist"),
            ("wrong_key_lock_mode", "managed-key lock is not a private"),
            ("hot_wal", "SQLite WAL requires writable"),
            ("hot_journal", "SQLite rollback journal requires writable"),
            ("plaintext_dvr", "Protected plaintext credentials"),
            ("plaintext_webhook", "Protected plaintext credentials"),
            ("pending_transaction", "configuration transaction"),
            ("restart_journal", "runtime transition journal"),
            ("invalid_active", "active runtime selection is invalid"),
            ("invalid_job", "update job is invalid"),
            ("transition_job", "update validation is incomplete"),
            ("activation_record", "activation record requires writable"),
            ("legacy_active", "legacy active runtime"),
            ("inconsistent_recovery", "recovery marker requires writable"),
        ],
    )
    def test_unsafe_or_incomplete_state_fails_closed(
        self, tmp_path, case, message
    ):
        entrypoint = _load_entrypoint()
        config_dir = _seed_mature_read_only_config(entrypoint, tmp_path)
        settings_file = config_dir / "settings.json"
        runtime_dir = entrypoint.CHANNELWATCH_RUNTIME_DIR

        if case == "missing_settings":
            settings_file.unlink()
        elif case == "broad_settings_mode":
            settings_file.chmod(0o640)
        elif case == "old_schema":
            settings_file.write_text('{"_version":6}', encoding="utf-8")
        elif case == "future_schema":
            settings_file.write_text('{"_version":8}', encoding="utf-8")
        elif case == "invalid_migration":
            (config_dir / "migration.journal").write_text("{", encoding="utf-8")
        elif case == "started_migration":
            (config_dir / "migration.journal").write_text(
                '{"status":"started"}', encoding="utf-8"
            )
        elif case == "unknown_migration":
            (config_dir / "migration.journal").write_text(
                '{"status":"unknown"}', encoding="utf-8"
            )
        elif case == "missing_key":
            (config_dir / "encryption.key").unlink()
        elif case == "wrong_key_mode":
            (config_dir / "encryption.key").chmod(0o640)
        elif case == "short_key":
            (config_dir / "encryption.key").write_bytes(b"short")
        elif case == "missing_key_lock":
            (config_dir / ".encryption-key.lock").unlink()
        elif case == "wrong_key_lock_mode":
            (config_dir / ".encryption-key.lock").chmod(0o640)
        elif case == "hot_wal":
            (config_dir / "channelwatch.db-wal").write_bytes(b"hot")
        elif case == "hot_journal":
            (config_dir / "channelwatch.db-journal").write_bytes(b"hot")
        elif case == "plaintext_dvr":
            settings_file.write_text(
                json.dumps(
                    {
                        "_version": entrypoint.CURRENT_SCHEMA_VERSION,
                        "dvr_servers": [{"api_key": "plaintext"}],
                    }
                ),
                encoding="utf-8",
            )
        elif case == "plaintext_webhook":
            settings_file.write_text(
                json.dumps(
                    {
                        "_version": entrypoint.CURRENT_SCHEMA_VERSION,
                        "webhooks": [{"url": "https://example.test/hook"}],
                    }
                ),
                encoding="utf-8",
            )
        elif case == "pending_transaction":
            transaction_dir = config_dir / ".channelwatch-transactions"
            transaction_dir.mkdir()
            (transaction_dir / "journal.json").write_text("{}", encoding="utf-8")
        else:
            runtime_dir.mkdir()
            if case == "restart_journal":
                entrypoint.RESTART_REQUIRED_PATH.write_text("{}", encoding="utf-8")
            elif case == "invalid_active":
                (runtime_dir / "active.json").write_text("{", encoding="utf-8")
            elif case == "invalid_job":
                (runtime_dir / "update-job.json").write_text("{", encoding="utf-8")
            elif case == "transition_job":
                (runtime_dir / "update-job.json").write_text(
                    '{"status":"applying"}', encoding="utf-8"
                )
            elif case == "activation_record":
                (runtime_dir / "activation-core-ready.json").write_text(
                    "{}", encoding="utf-8"
                )
            elif case == "legacy_active":
                (runtime_dir / "active.json").write_text(
                    '{"version":"0.9.18"}', encoding="utf-8"
                )
            elif case == "inconsistent_recovery":
                (runtime_dir / "active.json").write_text(
                    json.dumps(
                        {
                            "version": "0.9.18",
                            "activation_id": "activation-1",
                            "manifest": {"bundle_sha256": "a" * 64},
                        }
                    ),
                    encoding="utf-8",
                )
                (runtime_dir / "official-recovery-mode.json").write_text(
                    json.dumps(
                        {
                            "failed_version": "0.9.19",
                            "failed_bundle_sha256": "b" * 64,
                        }
                    ),
                    encoding="utf-8",
                )

        with pytest.raises(RuntimeError, match=message):
            entrypoint.validate_read_only_runtime_state()

    def test_completed_journal_empty_sqlite_sidecars_and_terminal_job_are_safe(
        self, tmp_path
    ):
        entrypoint = _load_entrypoint()
        config_dir = _seed_mature_read_only_config(entrypoint, tmp_path)
        (config_dir / "migration.journal").write_text(
            '{"status":"completed"}', encoding="utf-8"
        )
        (config_dir / "channelwatch.db-wal").write_bytes(b"")
        (config_dir / "channelwatch.db-journal").write_bytes(b"")
        runtime_dir = entrypoint.CHANNELWATCH_RUNTIME_DIR
        runtime_dir.mkdir()
        (runtime_dir / "update-job.json").write_text(
            '{"status":"success"}', encoding="utf-8"
        )

        entrypoint.validate_read_only_runtime_state()

    def test_consistent_official_recovery_marker_is_safe(self, tmp_path):
        entrypoint = _load_entrypoint()
        _seed_mature_read_only_config(entrypoint, tmp_path)
        runtime_dir = entrypoint.CHANNELWATCH_RUNTIME_DIR
        runtime_dir.mkdir()
        digest = "a" * 64
        (runtime_dir / "active.json").write_text(
            json.dumps(
                {
                    "version": "0.9.18",
                    "activation_id": "activation-1",
                    "manifest": {"bundle_sha256": digest},
                }
            ),
            encoding="utf-8",
        )
        (runtime_dir / "official-recovery-mode.json").write_text(
            json.dumps(
                {
                    "failed_version": "0.9.18",
                    "failed_bundle_sha256": digest,
                }
            ),
            encoding="utf-8",
        )

        entrypoint.validate_read_only_runtime_state()
