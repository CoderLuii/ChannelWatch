"""Regression tests for supervisor credential handling."""

import importlib.util
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


class TestEntrypointWritesSupervisorSocketConfig:
    def test_entrypoint_sets_verified_private_umask_before_exec(
        self, monkeypatch
    ):
        entrypoint = _load_entrypoint()
        observed_umask = None

        for name in (
            "_ensure_real_directory",
            "cleanup_restart_journal_candidates_before_validation",
            "validate_config_tree",
            "merge_bootstrap_env",
            "chown_tree",
            "chmod_config_tree",
            "render_supervisor_config",
            "prepare_standard_streams",
            "drop_privileges",
            "verify_config_tree_writable",
        ):
            monkeypatch.setattr(entrypoint, name, lambda *_args, **_kwargs: None)
        monkeypatch.setattr(entrypoint, "ensure_settings", lambda *_args: False)
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
        monkeypatch.setattr(entrypoint, "select_app_runtime_dir", lambda: restored_app)

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
