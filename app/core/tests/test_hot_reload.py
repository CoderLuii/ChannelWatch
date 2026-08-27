"""Tests for: hot reload of runtime settings.

Covers the three required scenarios from the plan:
  1. DVR-A config change reloads A only
  2. DVR-B unchanged stays up (not in changed list)
  3. listen-port changes are restart-required, not applied to DVR tasks

Plus unit coverage of: soft-delete exclusion, added/removed DVRs,
global setting changes, _stop_dvr_task, and the file-change watcher.
"""

import asyncio
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from core.helpers.hot_reload import (
    RESTART_REQUIRED_KEYS,
    compute_reload_diff,
    compute_reload_targets,
    format_diff_summary,
)


def _dvr(
    dvr_id, host="192.168.1.1", port=8089, name="DVR", enabled=True, overrides=None
):
    return {
        "id": dvr_id,
        "host": host,
        "port": port,
        "name": name,
        "enabled": enabled,
        "overrides": overrides or {},
    }


def _settings(*dvrs, **extra):
    base = {"_version": 7, "dvr_servers": list(dvrs)}
    base.update(extra)
    return base


class TestEarlySighupHandling:
    def test_no_dvr_core_survives_sighup_before_async_handler_setup(self, tmp_path):
        if not hasattr(signal, "SIGHUP"):
            pytest.skip("SIGHUP is not available on this platform")

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text(
            json.dumps(_settings()), encoding="utf-8"
        )

        env = os.environ.copy()
        env["CONFIG_PATH"] = str(config_dir)
        # The readiness line is the synchronization primitive for this test.
        # Force the child to flush it even when CI captures stdout through a
        # pipe; otherwise Python's block buffering can make a healthy child
        # look as though startup never completed.
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("CHANNELS_DVR_HOST", None)
        env.pop("CHANNELS_DVR_PORT", None)
        env.pop("CHANNELS_DVR_SERVERS", None)

        proc = subprocess.Popen(
            [sys.executable, "-m", "core.main"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_DFL),
        )

        try:
            # Importing the full core under coverage, container builds, or
            # other release-gate load can legitimately take more than five
            # seconds.  This test is about SIGHUP safety after the no-DVR
            # startup boundary, not import speed.
            deadline = time.monotonic() + 20.0
            startup_output = ""
            while time.monotonic() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [], 0.1)
                if not ready:
                    assert (
                        proc.poll() is None
                    ), "core exited before no-DVR startup completed"
                    continue
                startup_output += os.read(proc.stdout.fileno(), 4096).decode(
                    "utf-8", errors="replace"
                )
                if "Waiting for DVR server configuration" in startup_output:
                    break
            assert "Waiting for DVR server configuration" in startup_output

            os.kill(proc.pid, signal.SIGHUP)
            time.sleep(2.0)

            assert proc.poll() is None, (
                "core exited after an early SIGHUP in no-DVR startup; "
                f"stdout={proc.stdout.read() if proc.stdout else ''!r} "
                f"stderr={proc.stderr.read() if proc.stderr else ''!r}"
            )
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_core_main_import_ignores_sighup_until_async_handler_replaces_it(self):
        """Manual equivalent: start fresh no-DVR core, send SIGHUP immediately, verify it stays alive."""
        if not hasattr(signal, "SIGHUP"):
            pytest.skip("SIGHUP is not available on this platform")

        import importlib
        import core.main as main_mod

        original_handler = signal.getsignal(signal.SIGHUP)
        try:
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
            importlib.reload(main_mod)

            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        finally:
            signal.signal(signal.SIGHUP, original_handler)


class TestComputeReloadDiff:
    def test_dvr_a_port_change_only_a_in_changed(self):
        dvr_a_old = _dvr("dvr_aaa", port=8089)
        dvr_b = _dvr("dvr_bbb", host="192.168.1.2", port=8089)
        dvr_a_new = _dvr("dvr_aaa", port=8090)

        diff = compute_reload_diff(
            _settings(dvr_a_old, dvr_b),
            _settings(dvr_a_new, dvr_b),
        )

        assert diff["changed_dvr_ids"] == ["dvr_aaa"]
        assert diff["added_dvr_ids"] == []
        assert diff["removed_dvr_ids"] == []

    def test_dvr_b_unchanged_not_in_diff(self):
        dvr_a_old = _dvr("dvr_aaa", port=8089)
        dvr_b = _dvr("dvr_bbb", host="192.168.1.2", port=8089)
        dvr_a_new = _dvr("dvr_aaa", port=8090)

        diff = compute_reload_diff(
            _settings(dvr_a_old, dvr_b),
            _settings(dvr_a_new, dvr_b),
        )

        assert "dvr_bbb" not in diff["changed_dvr_ids"]
        assert "dvr_bbb" not in diff["added_dvr_ids"]
        assert "dvr_bbb" not in diff["removed_dvr_ids"]

    def test_listen_port_change_is_restart_required_not_dvr_restart(self):
        old = _settings(_dvr("dvr_aaa"), uvicorn_port=8501)
        new = _settings(_dvr("dvr_aaa"), uvicorn_port=9000)

        diff = compute_reload_diff(old, new)

        assert "uvicorn_port" in diff["restart_required"]
        assert diff["changed_dvr_ids"] == []

    def test_rbac_enabled_change_is_restart_required(self):
        old = _settings(rbac_enabled=False)
        new = _settings(rbac_enabled=True)

        diff = compute_reload_diff(old, new)

        assert "rbac_enabled" in diff["restart_required"]
        assert diff["changed_dvr_ids"] == []

    def test_multi_dvr_flag_change_is_restart_required(self):
        old = _settings(multi_dvr_v2_enabled=True)
        new = _settings(multi_dvr_v2_enabled=False)

        diff = compute_reload_diff(old, new)

        assert "multi_dvr_v2_enabled" in diff["restart_required"]

    def test_dvr_added_in_added_list(self):
        dvr_a = _dvr("dvr_aaa")
        dvr_b = _dvr("dvr_bbb")

        diff = compute_reload_diff(_settings(dvr_a), _settings(dvr_a, dvr_b))

        assert "dvr_bbb" in diff["added_dvr_ids"]
        assert diff["changed_dvr_ids"] == []
        assert diff["removed_dvr_ids"] == []

    def test_dvr_removed_in_removed_list(self):
        dvr_a = _dvr("dvr_aaa")
        dvr_b = _dvr("dvr_bbb")

        diff = compute_reload_diff(_settings(dvr_a, dvr_b), _settings(dvr_a))

        assert "dvr_bbb" in diff["removed_dvr_ids"]
        assert diff["changed_dvr_ids"] == []
        assert diff["added_dvr_ids"] == []

    def test_soft_deleted_dvr_excluded_from_active_map(self):
        dvr_a = _dvr("dvr_aaa")
        dvr_b_deleted = {**_dvr("dvr_bbb"), "deleted_at": "2026-04-20T00:00:00"}

        old = _settings(dvr_a, dvr_b_deleted)
        new = _settings(dvr_a, dvr_b_deleted)

        diff = compute_reload_diff(old, new)

        assert diff["any_action"] is False

    def test_no_change_returns_empty_diff(self):
        dvr_a = _dvr("dvr_aaa")
        s = _settings(dvr_a)

        diff = compute_reload_diff(s, dict(s))

        assert diff["any_action"] is False
        assert diff["changed_dvr_ids"] == []

    def test_global_setting_change_captured(self):
        old = _settings(log_level=1)
        new = _settings(log_level=2)

        diff = compute_reload_diff(old, new)

        assert "log_level" in diff["global_changes"]
        assert diff["global_changes"]["log_level"] == {"from": 1, "to": 2}

    def test_version_change_alone_no_action(self):
        old = {"_version": 6, "dvr_servers": []}
        new = {"_version": 7, "dvr_servers": []}

        diff = compute_reload_diff(old, new)

        assert diff["any_action"] is False

    def test_dvr_name_change_triggers_restart(self):
        dvr_old = _dvr("dvr_aaa", name="Old Name")
        dvr_new = _dvr("dvr_aaa", name="New Name")

        diff = compute_reload_diff(_settings(dvr_old), _settings(dvr_new))

        assert "dvr_aaa" in diff["changed_dvr_ids"]

    def test_dvr_enabled_toggle_triggers_restart(self):
        dvr_old = _dvr("dvr_aaa", enabled=True)
        dvr_new = _dvr("dvr_aaa", enabled=False)

        diff = compute_reload_diff(_settings(dvr_old), _settings(dvr_new))

        assert "dvr_aaa" in diff["changed_dvr_ids"]

    def test_dvr_overrides_change_triggers_restart(self):
        dvr_old = _dvr("dvr_aaa", overrides={"log_level": 1})
        dvr_new = _dvr("dvr_aaa", overrides={"log_level": 2})

        diff = compute_reload_diff(_settings(dvr_old), _settings(dvr_new))

        assert "dvr_aaa" in diff["changed_dvr_ids"]

    def test_all_restart_required_keys_defined(self):
        assert "uvicorn_port" in RESTART_REQUIRED_KEYS
        assert "uvicorn_host" in RESTART_REQUIRED_KEYS
        assert "rbac_enabled" in RESTART_REQUIRED_KEYS
        assert "multi_dvr_v2_enabled" in RESTART_REQUIRED_KEYS


class TestFormatDiffSummary:
    def test_restart_required_mentioned(self):
        diff = {
            "changed_dvr_ids": [],
            "added_dvr_ids": [],
            "removed_dvr_ids": [],
            "restart_required": ["uvicorn_port"],
            "global_changes": {},
            "any_action": True,
        }
        summary = format_diff_summary(diff)
        assert "restart-required" in summary
        assert "uvicorn_port" in summary

    def test_no_changes_message(self):
        diff = {
            "changed_dvr_ids": [],
            "added_dvr_ids": [],
            "removed_dvr_ids": [],
            "restart_required": [],
            "global_changes": {},
            "any_action": False,
        }
        assert "no changes" in format_diff_summary(diff)

    def test_changed_dvr_in_summary(self):
        diff = {
            "changed_dvr_ids": ["dvr_aaa"],
            "added_dvr_ids": [],
            "removed_dvr_ids": [],
            "restart_required": [],
            "global_changes": {},
            "any_action": True,
        }
        assert "dvr_aaa" in format_diff_summary(diff)


class TestComputeReloadTargets:
    def test_global_change_reloads_all_active_dvrs(self):
        diff = {
            "changed_dvr_ids": [],
            "added_dvr_ids": [],
            "removed_dvr_ids": [],
            "restart_required": [],
            "global_changes": {"log_level": {"from": 1, "to": 2}},
            "any_action": True,
        }

        targets = compute_reload_targets(diff, active_dvr_ids=["dvr_aaa", "dvr_bbb"])

        assert targets == ["dvr_aaa", "dvr_bbb"]

    def test_removed_dvr_is_excluded_from_global_reload_targets(self):
        diff = {
            "changed_dvr_ids": [],
            "added_dvr_ids": [],
            "removed_dvr_ids": ["dvr_bbb"],
            "restart_required": [],
            "global_changes": {"log_level": {"from": 1, "to": 2}},
            "any_action": True,
        }

        targets = compute_reload_targets(diff, active_dvr_ids=["dvr_aaa", "dvr_bbb"])

        assert targets == ["dvr_aaa"]


class TestHandleConfigReload:
    def test_actionable_reload_discards_cached_health_notification_managers(self):
        import core.main as main_mod

        dvr = _dvr("dvr_aaa", host="192.168.1.10", name="DVR-A")
        dvr_connection = MagicMock(id="dvr_aaa", name="DVR-A")
        new_settings = MagicMock()
        new_settings.get_dvr_connections.return_value = [dvr_connection]
        original_tasks = dict(main_mod._dvr_tasks)
        original_managers = dict(main_mod._dvr_health_notification_managers)

        async def run():
            main_mod._dvr_tasks = {"dvr_aaa": object()}
            main_mod._dvr_health_notification_managers = {"dvr_aaa": object()}
            with (
                patch("core.main.get_settings", return_value=new_settings),
                patch("core.main._close_health_notification_manager") as close_manager,
                patch("core.main._reconcile_dvr", new=AsyncMock(return_value=True)),
            ):
                await main_mod._handle_config_reload(
                    _settings(dvr, log_level=1),
                    _settings(dvr, log_level=2),
                    MagicMock(),
                )

            close_manager.assert_called_once_with("dvr_aaa")

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks
            main_mod._dvr_health_notification_managers = original_managers

    def test_two_dvr_reload_restarts_only_changed_dvr(self):
        import core.main as main_mod

        original_tasks = dict(main_mod._dvr_tasks)

        dvr_a_old = _dvr("dvr_aaa", host="192.168.1.10", port=8089, name="DVR-A")
        dvr_a_new = _dvr("dvr_aaa", host="192.168.1.10", port=8090, name="DVR-A")
        dvr_b = _dvr("dvr_bbb", host="192.168.1.11", port=8089, name="DVR-B")

        dvr_a_conn = MagicMock(id="dvr_aaa", name="DVR-A")
        dvr_b_conn = MagicMock(id="dvr_bbb", name="DVR-B")
        new_settings = MagicMock()
        new_settings.get_dvr_connections.return_value = [dvr_a_conn, dvr_b_conn]

        async def run():
            main_mod._dvr_tasks = {"dvr_aaa": object(), "dvr_bbb": object()}
            with (
                patch("core.main.get_settings", return_value=new_settings),
                patch(
                    "core.main._reconcile_dvr",
                    new=AsyncMock(return_value=True),
                ) as reconcile,
                patch("core.main.log") as log_mock,
            ):
                await main_mod._handle_config_reload(
                    _settings(dvr_a_old, dvr_b),
                    _settings(dvr_a_new, dvr_b),
                    MagicMock(),
                )

                reconcile.assert_awaited_once()
                assert reconcile.await_args.args[0] is dvr_a_conn
                assert reconcile.await_args.args[1] is new_settings
                assert isinstance(reconcile.await_args.args[2], asyncio.Event)
                assert reconcile.await_args.args[3] is False
                assert reconcile.await_args.kwargs["preserve_healthy_monitor"] is False
                assert any(
                    "CONFIG_RELOADED:" in call.args[0]
                    for call in log_mock.call_args_list
                )

        asyncio.run(run())
        main_mod._dvr_tasks = original_tasks

    def test_global_runtime_change_restarts_all_active_dvrs(self):
        import core.main as main_mod

        original_tasks = dict(main_mod._dvr_tasks)

        dvr_a = _dvr("dvr_aaa", host="192.168.1.10", name="DVR-A")
        dvr_b = _dvr("dvr_bbb", host="192.168.1.11", name="DVR-B")
        dvr_a_conn = MagicMock(id="dvr_aaa", name="DVR-A")
        dvr_b_conn = MagicMock(id="dvr_bbb", name="DVR-B")
        new_settings = MagicMock()
        new_settings.get_dvr_connections.return_value = [dvr_a_conn, dvr_b_conn]

        async def run():
            main_mod._dvr_tasks = {"dvr_aaa": object(), "dvr_bbb": object()}
            with (
                patch("core.main.get_settings", return_value=new_settings),
                patch(
                    "core.main._reconcile_dvr",
                    new=AsyncMock(return_value=True),
                ) as reconcile,
            ):
                await main_mod._handle_config_reload(
                    _settings(dvr_a, dvr_b, log_level=1),
                    _settings(dvr_a, dvr_b, log_level=2),
                    MagicMock(),
                )

                assert [call.args[0] for call in reconcile.await_args_list] == [
                    dvr_a_conn,
                    dvr_b_conn,
                ]
                assert reconcile.await_count == 2
                assert all(
                    call.kwargs["preserve_healthy_monitor"]
                    for call in reconcile.await_args_list
                )

        asyncio.run(run())
        main_mod._dvr_tasks = original_tasks

    def test_restart_required_change_does_not_restart_any_dvr(self):
        import core.main as main_mod

        original_tasks = dict(main_mod._dvr_tasks)

        async def run():
            main_mod._dvr_tasks = {"dvr_aaa": object(), "dvr_bbb": object()}
            with (
                patch("core.main.get_settings", return_value=MagicMock()),
                patch("core.main._stop_dvr_task", new=AsyncMock()) as stop_task,
                patch("core.main._init_dvr_monitor_sync") as init_monitor,
                patch(
                    "core.main._start_verified_dvr_task", new=AsyncMock()
                ) as start_task,
                patch("core.main._reconcile_dvr", new=AsyncMock()) as reconcile,
                patch("core.main.log") as log_mock,
            ):
                await main_mod._handle_config_reload(
                    _settings(_dvr("dvr_aaa"), uvicorn_port=8501),
                    _settings(_dvr("dvr_aaa"), uvicorn_port=9000),
                    MagicMock(),
                )

                stop_task.assert_not_awaited()
                init_monitor.assert_not_called()
                start_task.assert_not_awaited()
                reconcile.assert_not_awaited()
                assert any(
                    "require container restart" in call.args[0]
                    for call in log_mock.call_args_list
                )

        asyncio.run(run())
        main_mod._dvr_tasks = original_tasks

    def test_settings_rebuild_is_offloaded_to_thread(self):
        import core.main as main_mod
        from core.helpers.config import CoreSettings

        original_tasks = dict(main_mod._dvr_tasks)
        original_instance = CoreSettings._instance
        sentinel_instance = CoreSettings.__new__(CoreSettings)
        to_thread_calls = []
        new_settings = MagicMock()
        new_settings.get_dvr_connections.return_value = []

        async def run():
            main_mod._dvr_tasks = {"dvr_aaa": object()}
            CoreSettings._instance = sentinel_instance

            async def run_in_thread(func, *args, **kwargs):
                to_thread_calls.append((func, args, kwargs))
                assert func is get_settings
                assert args == ()
                assert kwargs == {}
                assert CoreSettings._instance is None
                return func(*args, **kwargs)

            with (
                patch(
                    "core.main.get_settings", return_value=new_settings
                ) as get_settings,
                patch("core.main.asyncio.to_thread", side_effect=run_in_thread),
                patch("core.main._stop_dvr_task", new=AsyncMock()),
                patch("core.main._init_dvr_monitor_sync"),
                patch("core.main._start_verified_dvr_task", new=AsyncMock()),
                patch("core.main._reconcile_dvr", new=AsyncMock(return_value=True)),
            ):
                await main_mod._handle_config_reload(
                    _settings(_dvr("dvr_aaa"), log_level=1),
                    _settings(_dvr("dvr_aaa"), log_level=2),
                    MagicMock(),
                )

            assert to_thread_calls == [(get_settings, (), {})]

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks
            CoreSettings._instance = original_instance


class TestStopDvrTask:
    def test_stop_sets_monitor_running_false_and_awaits_task(self):
        import core.main as main_mod

        original_tasks = dict(main_mod._dvr_tasks)
        original_monitors = dict(main_mod._dvr_monitors)

        mock_monitor = MagicMock()
        mock_monitor.running = True

        stopped_flag = {"done": False}

        async def fake_monitoring():
            while mock_monitor.running:
                await asyncio.sleep(0.01)
            stopped_flag["done"] = True

        async def run():
            task = asyncio.create_task(fake_monitoring())
            main_mod._dvr_tasks["dvr_test"] = task
            main_mod._dvr_monitors["dvr_test"] = mock_monitor

            from core.main import _stop_dvr_task

            await _stop_dvr_task("dvr_test")

            assert mock_monitor.running is False
            assert stopped_flag["done"] is True
            assert "dvr_test" not in main_mod._dvr_tasks
            assert "dvr_test" not in main_mod._dvr_monitors

        asyncio.run(run())

        main_mod._dvr_tasks.update(original_tasks)
        main_mod._dvr_monitors.update(original_monitors)

    def test_stop_unknown_dvr_id_is_noop(self):
        async def run():
            from core.main import _stop_dvr_task

            await _stop_dvr_task("dvr_nonexistent")

        asyncio.run(run())


class TestWatchConfigAndReload:
    def test_file_change_triggers_handle_reload(self, tmp_path):
        config_file = tmp_path / "settings.json"
        dvr_a = _dvr("dvr_aaa", port=8089)
        initial = _settings(dvr_a)
        config_file.write_text(json.dumps(initial))

        handle_calls = []

        async def mock_handle(old, new, settings, test_mode=False, **_kwargs):
            handle_calls.append((old, new))

        async def run():
            from core.main import _watch_config_and_reload
            import core.main as main_mod

            main_mod._last_settings_raw.clear()
            main_mod._last_settings_raw.update(initial)

            shutdown = asyncio.Event()
            reload_event = asyncio.Event()

            async def change_file_then_stop():
                await asyncio.sleep(0.1)
                dvr_a_new = _dvr("dvr_aaa", port=8090)
                config_file.write_text(json.dumps(_settings(dvr_a_new)))
                await asyncio.sleep(2.3)
                shutdown.set()

            with patch("core.main.CONFIG_FILE", config_file):
                with patch("core.main._handle_config_reload", side_effect=mock_handle):
                    await asyncio.gather(
                        _watch_config_and_reload(shutdown, reload_event, MagicMock()),
                        change_file_then_stop(),
                    )

        asyncio.run(run())
        assert len(handle_calls) >= 1

    def test_sighup_triggers_reload_check_immediately(self, tmp_path):
        config_file = tmp_path / "settings.json"
        dvr_a = _dvr("dvr_aaa", port=8089)
        initial = _settings(dvr_a)
        config_file.write_text(json.dumps(initial))

        handle_calls = []

        async def mock_handle(old, new, settings, test_mode=False, **_kwargs):
            handle_calls.append((old, new))

        async def run():
            from core.main import _watch_config_and_reload
            import core.main as main_mod

            main_mod._last_settings_raw.clear()

            shutdown = asyncio.Event()
            reload_event = asyncio.Event()

            async def trigger_sighup_and_stop():
                await asyncio.sleep(0.05)
                dvr_a_new = _dvr("dvr_aaa", port=8090)
                config_file.write_text(json.dumps(_settings(dvr_a_new)))
                reload_event.set()
                await asyncio.sleep(0.2)
                shutdown.set()

            with patch("core.main.CONFIG_FILE", config_file):
                with patch("core.main._handle_config_reload", side_effect=mock_handle):
                    await asyncio.gather(
                        _watch_config_and_reload(shutdown, reload_event, MagicMock()),
                        trigger_sighup_and_stop(),
                    )

        asyncio.run(run())
        assert len(handle_calls) >= 1

    def test_unchanged_file_does_not_trigger_reload(self, tmp_path):
        config_file = tmp_path / "settings.json"
        dvr_a = _dvr("dvr_aaa", port=8089)
        initial = _settings(dvr_a)
        config_file.write_text(json.dumps(initial))

        handle_calls = []

        async def mock_handle(old, new, settings, test_mode=False, **_kwargs):
            handle_calls.append((old, new))

        async def run():
            from core.main import _watch_config_and_reload
            import core.main as main_mod

            main_mod._last_settings_raw.clear()
            main_mod._last_settings_raw.update(initial)

            shutdown = asyncio.Event()
            reload_event = asyncio.Event()

            async def stop_after():
                await asyncio.sleep(0.3)
                shutdown.set()

            with patch("core.main.CONFIG_FILE", config_file):
                with patch("core.main._handle_config_reload", side_effect=mock_handle):
                    await asyncio.gather(
                        _watch_config_and_reload(shutdown, reload_event, MagicMock()),
                        stop_after(),
                    )

        asyncio.run(run())
        assert len(handle_calls) == 0

    def test_failed_reconciliation_retries_unchanged_saved_content(self):
        import core.main as main_mod

        initial = _settings(_dvr("dvr_retry", port=8089))
        changed = _settings(_dvr("dvr_retry", port=8090))
        old_content = json.dumps(initial).encode()
        changed_content = json.dumps(changed).encode()
        snapshot_reads = 0
        reconcile_attempts: list[tuple[dict, dict]] = []

        async def read_snapshot():
            nonlocal snapshot_reads
            snapshot_reads += 1
            if snapshot_reads == 1:
                return old_content, "old-hash"
            return changed_content, "changed-hash"

        async def reconcile(old_raw, new_raw, *_args, **_kwargs):
            reconcile_attempts.append((old_raw, new_raw))
            if len(reconcile_attempts) == 1:
                raise RuntimeError("transient reconciliation failure")

        async def run():
            shutdown = asyncio.Event()
            reload_event = asyncio.Event()
            reload_event.set()
            main_mod._last_settings_raw.clear()
            main_mod._last_settings_raw.update(initial)

            async def drive_retries():
                while len(reconcile_attempts) < 1:
                    await asyncio.sleep(0.01)
                assert main_mod._last_settings_raw == initial
                reload_event.set()
                while len(reconcile_attempts) < 2:
                    await asyncio.sleep(0.01)
                shutdown.set()
                reload_event.set()

            with (
                patch(
                    "core.main._read_config_snapshot_async",
                    side_effect=read_snapshot,
                ),
                patch("core.main._handle_config_reload", side_effect=reconcile),
            ):
                await asyncio.gather(
                    main_mod._watch_config_and_reload(
                        shutdown, reload_event, MagicMock()
                    ),
                    drive_retries(),
                )

        asyncio.run(run())

        assert snapshot_reads >= 3
        assert reconcile_attempts == [(initial, changed), (initial, changed)]
        assert main_mod._last_settings_raw == changed

    def test_failed_reconciliation_cancels_and_drains_siblings_before_retry(self):
        import core.main as main_mod
        from core.helpers.config import CoreSettings

        old_raw = _settings(
            _dvr("dvr_a", host="192.0.2.10"),
            _dvr("dvr_b", host="192.0.2.20"),
        )
        new_raw = _settings(
            _dvr("dvr_a", host="192.0.2.11"),
            _dvr("dvr_b", host="192.0.2.21"),
        )
        desired_dvrs = [
            SimpleNamespace(id="dvr_a", name="DVR A"),
            SimpleNamespace(id="dvr_b", name="DVR B"),
        ]
        new_settings = SimpleNamespace(
            get_dvr_connections=lambda: desired_dvrs,
        )

        async def run():
            attempts = {"dvr_a": 0, "dvr_b": 0}
            active_b = 0
            max_active_b = 0
            first_b_started = asyncio.Event()
            first_b_cancelled = asyncio.Event()
            never_release_first_b = asyncio.Event()

            async def reconcile(dvr, *_args, **_kwargs):
                nonlocal active_b, max_active_b
                attempts[dvr.id] += 1
                if dvr.id == "dvr_a" and attempts[dvr.id] == 1:
                    await first_b_started.wait()
                    raise RuntimeError("transient DVR A failure")
                if dvr.id == "dvr_b":
                    active_b += 1
                    max_active_b = max(max_active_b, active_b)
                    try:
                        if attempts[dvr.id] == 1:
                            first_b_started.set()
                            try:
                                await never_release_first_b.wait()
                            except asyncio.CancelledError:
                                first_b_cancelled.set()
                                raise
                        else:
                            assert first_b_cancelled.is_set()
                    finally:
                        active_b -= 1
                return True

            previous_settings_instance = CoreSettings._instance
            try:
                with (
                    patch("core.main.get_settings", return_value=new_settings),
                    patch("core.main._reconcile_dvr", side_effect=reconcile),
                    patch(
                        "core.main._persist_watchdog_async",
                        new_callable=AsyncMock,
                    ),
                ):
                    with pytest.raises(
                        RuntimeError, match="transient DVR A failure"
                    ):
                        await main_mod._handle_config_reload(
                            old_raw,
                            new_raw,
                            SimpleNamespace(),
                            shutdown_event=asyncio.Event(),
                            reconcile_semaphore=asyncio.Semaphore(3),
                            initialization_semaphore=asyncio.Semaphore(3),
                        )

                    assert first_b_cancelled.is_set()
                    assert active_b == 0

                    await main_mod._handle_config_reload(
                        old_raw,
                        new_raw,
                        SimpleNamespace(),
                        shutdown_event=asyncio.Event(),
                        reconcile_semaphore=asyncio.Semaphore(3),
                        initialization_semaphore=asyncio.Semaphore(3),
                    )
            finally:
                CoreSettings._instance = previous_settings_instance

            assert attempts == {"dvr_a": 2, "dvr_b": 2}
            assert max_active_b == 1

        asyncio.run(run())

    def test_failed_removed_dvr_stop_cancels_and_drains_siblings_before_retry(self):
        import core.main as main_mod
        from core.helpers.config import CoreSettings

        old_raw = _settings(
            _dvr("dvr_a", host="192.0.2.10"),
            _dvr("dvr_b", host="192.0.2.20"),
        )
        new_raw = _settings()
        new_settings = SimpleNamespace(get_dvr_connections=lambda: [])

        async def run():
            attempts = {"dvr_a": 0, "dvr_b": 0}
            active_b = 0
            max_active_b = 0
            first_b_started = asyncio.Event()
            first_b_cancelled = asyncio.Event()
            never_release_first_b = asyncio.Event()

            async def stop_dvr(dvr_id):
                nonlocal active_b, max_active_b
                attempts[dvr_id] += 1
                if dvr_id == "dvr_a" and attempts[dvr_id] == 1:
                    await first_b_started.wait()
                    raise RuntimeError("transient DVR A stop failure")
                if dvr_id == "dvr_b":
                    active_b += 1
                    max_active_b = max(max_active_b, active_b)
                    try:
                        if attempts[dvr_id] == 1:
                            first_b_started.set()
                            try:
                                await never_release_first_b.wait()
                            except asyncio.CancelledError:
                                first_b_cancelled.set()
                                raise
                        else:
                            assert first_b_cancelled.is_set()
                    finally:
                        active_b -= 1

            previous_settings_instance = CoreSettings._instance
            previous_tasks = dict(main_mod._dvr_tasks)
            previous_monitors = dict(main_mod._dvr_monitors)
            try:
                with (
                    patch("core.main.get_settings", return_value=new_settings),
                    patch("core.main._stop_dvr_task", side_effect=stop_dvr),
                    patch(
                        "core.main._persist_watchdog_async",
                        new_callable=AsyncMock,
                    ),
                ):
                    with pytest.raises(
                        RuntimeError, match="transient DVR A stop failure"
                    ):
                        await main_mod._handle_config_reload(
                            old_raw,
                            new_raw,
                            SimpleNamespace(),
                            shutdown_event=asyncio.Event(),
                            reconcile_semaphore=asyncio.Semaphore(3),
                            initialization_semaphore=asyncio.Semaphore(3),
                        )

                    assert first_b_cancelled.is_set()
                    assert active_b == 0

                    await main_mod._handle_config_reload(
                        old_raw,
                        new_raw,
                        SimpleNamespace(),
                        shutdown_event=asyncio.Event(),
                        reconcile_semaphore=asyncio.Semaphore(3),
                        initialization_semaphore=asyncio.Semaphore(3),
                    )
            finally:
                CoreSettings._instance = previous_settings_instance
                main_mod._dvr_tasks.clear()
                main_mod._dvr_tasks.update(previous_tasks)
                main_mod._dvr_monitors.clear()
                main_mod._dvr_monitors.update(previous_monitors)

            assert attempts == {"dvr_a": 2, "dvr_b": 2}
            assert max_active_b == 1

        asyncio.run(run())

    def test_config_snapshot_reads_are_offloaded_to_thread(self, tmp_path):
        config_file = tmp_path / "settings.json"
        config_file.write_text(json.dumps(_settings(_dvr("dvr_aaa"))))
        calls = []

        async def run():
            from core.main import _watch_config_and_reload

            shutdown = asyncio.Event()
            reload_event = asyncio.Event()

            async def run_in_thread(func, *args, **kwargs):
                calls.append(func)
                return func(*args, **kwargs)

            async def stop_after():
                await asyncio.sleep(0.05)
                shutdown.set()
                reload_event.set()

            with (
                patch("core.main.CONFIG_FILE", config_file),
                patch("core.main.asyncio.to_thread", side_effect=run_in_thread),
            ):
                await asyncio.gather(
                    _watch_config_and_reload(shutdown, reload_event, MagicMock()),
                    stop_after(),
                )

        asyncio.run(run())
        assert any(
            getattr(func, "__name__", "") == "_read_config_snapshot" for func in calls
        )

    @pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "directory", "fifo"])
    def test_config_snapshot_rejects_linked_and_special_files(
        self, tmp_path, unsafe_kind
    ):
        import core.main as main_mod

        source = tmp_path / "outside.json"
        source.write_text(json.dumps(_settings(_dvr("dvr_outside"))))
        candidate = tmp_path / "settings.json"
        if unsafe_kind == "symlink":
            candidate.symlink_to(source)
        elif unsafe_kind == "hardlink":
            os.link(source, candidate)
        elif unsafe_kind == "directory":
            candidate.mkdir()
        else:
            if not hasattr(os, "mkfifo"):
                pytest.skip("FIFOs are unavailable on this platform")
            os.mkfifo(candidate)

        with patch("core.main.CONFIG_FILE", candidate):
            with pytest.raises(PermissionError):
                main_mod._read_config_snapshot()

    def test_config_snapshot_rejects_oversized_settings(self, tmp_path):
        import core.main as main_mod

        candidate = tmp_path / "settings.json"
        candidate.write_bytes(b"x" * (main_mod.MAX_SETTINGS_FILE_BYTES + 1))

        with patch("core.main.CONFIG_FILE", candidate):
            with pytest.raises(ValueError, match="allowed size"):
                main_mod._read_config_snapshot()

    def test_watchdog_persistence_is_offloaded_to_thread(self):
        calls = []
        watchdog = MagicMock()

        async def run():
            import core.main as main_mod

            shutdown = asyncio.Event()
            original_watchdog = main_mod._watchdog

            async def run_in_thread(func, *args, **kwargs):
                calls.append(func)
                shutdown.set()
                return func(*args, **kwargs)

            try:
                main_mod._watchdog = watchdog
                with patch("core.main.asyncio.to_thread", side_effect=run_in_thread):
                    await main_mod._watchdog_loop(shutdown)
            finally:
                main_mod._watchdog = original_watchdog

        asyncio.run(run())
        assert calls == [watchdog.persist]


class TestAsyncRuntimeIntegration:
    def test_run_monitors_dynamic_is_coroutine(self):
        import inspect
        from core.main import _run_monitors_dynamic

        assert inspect.iscoroutinefunction(_run_monitors_dynamic)

    def test_stop_dvr_task_is_coroutine(self):
        import inspect
        from core.main import _stop_dvr_task

        assert inspect.iscoroutinefunction(_stop_dvr_task)

    def test_handle_config_reload_is_coroutine(self):
        import inspect
        from core.main import _handle_config_reload

        assert inspect.iscoroutinefunction(_handle_config_reload)

    def test_watch_config_and_reload_is_coroutine(self):
        import inspect
        from core.main import _watch_config_and_reload

        assert inspect.iscoroutinefunction(_watch_config_and_reload)

    def test_init_dvr_monitor_sync_is_plain_callable(self):
        import inspect
        from core.main import _init_dvr_monitor_sync

        assert callable(_init_dvr_monitor_sync)
        assert not inspect.iscoroutinefunction(_init_dvr_monitor_sync)

    def test_no_threading_thread_in_new_hot_reload_functions(self):
        import inspect
        import core.main as mod

        src = (
            inspect.getsource(mod._run_monitors_dynamic)
            + inspect.getsource(mod._watch_config_and_reload)
            + inspect.getsource(mod._handle_config_reload)
            + inspect.getsource(mod._stop_dvr_task)
        )
        assert "threading.Thread(" not in src

    def test_run_monitors_dynamic_stops_all_on_shutdown(self):
        from core.main import _run_monitors_dynamic

        monitors = []
        for i in range(2):
            m = MagicMock()
            m.dvr_name = f"dvr-{i}"
            m.dvr = MagicMock()
            m.dvr.id = f"dvr_{i}"
            m.running = True
            m.start_monitoring = lambda mon=m: _spin(mon)
            monitors.append(m)

        def _spin(mon):
            while mon.running:
                time.sleep(0.001)

        async def run():
            shutdown = asyncio.Event()
            reload_event = asyncio.Event()
            asyncio.get_running_loop().call_later(0.05, shutdown.set)
            await _run_monitors_dynamic(monitors, MagicMock(), shutdown, reload_event)

        asyncio.run(run())
        for m in monitors:
            assert m.running is False

    def test_run_monitors_dynamic_shutdown_is_bounded_for_stuck_monitor(self):
        import core.main as main_mod

        monitor = MagicMock()
        monitor.dvr_name = "stuck-dvr"
        monitor.dvr = MagicMock()
        monitor.dvr.id = "dvr_stuck"
        monitor.running = True
        monitor.start_monitoring = lambda: time.sleep(0.2)

        async def run():
            shutdown = asyncio.Event()
            reload_event = asyncio.Event()
            asyncio.get_running_loop().call_later(0.01, shutdown.set)
            with patch.object(main_mod, "MONITOR_SHUTDOWN_TIMEOUT_SECONDS", 0.01):
                await asyncio.wait_for(
                    main_mod._run_monitors_dynamic(
                        [monitor], MagicMock(), shutdown, reload_event
                    ),
                    timeout=1.0,
                )

        asyncio.run(run())
        monitor.stop_monitoring.assert_called_once()


class TestMonitorReconciliation:
    @staticmethod
    def _connection(dvr_id: str, *, name: str | None = None):
        return SimpleNamespace(
            id=dvr_id,
            name=name or dvr_id,
            host="127.0.0.1",
            port=8089,
            api_key="",
            overrides={},
        )

    def test_zero_dvr_runtime_starts_watcher_watchdog_and_reports_ready(self):
        import core.main as main_mod

        settings = MagicMock(monitor_stale_seconds=300)
        settings.get_dvr_connections.return_value = []
        ready_calls: list[bool] = []

        async def run():
            shutdown = asyncio.Event()
            reload_event = asyncio.Event()
            asyncio.get_running_loop().call_later(0.02, shutdown.set)
            with (
                patch(
                    "core.main._read_config_snapshot_async",
                    new=AsyncMock(return_value=(None, "")),
                ),
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._run_monitors_dynamic(
                    [],
                    settings,
                    shutdown,
                    reload_event,
                    on_ready=lambda: ready_calls.append(True),
                )

        asyncio.run(run())
        assert ready_calls == [True]
        assert main_mod._dvr_tasks == {}
        assert main_mod._dvr_monitors == {}

    def test_runtime_does_not_report_ready_when_initial_persistence_fails(self):
        import core.main as main_mod

        settings = MagicMock(monitor_stale_seconds=300)
        settings.get_dvr_connections.return_value = []
        ready_calls: list[bool] = []

        async def run():
            with (
                patch(
                    "core.main._read_config_snapshot_async",
                    new=AsyncMock(return_value=(None, "")),
                ),
                patch(
                    "core.main._persist_watchdog_async",
                    new=AsyncMock(side_effect=RuntimeError("watchdog init failed")),
                ),
            ):
                with pytest.raises(RuntimeError, match="watchdog init failed"):
                    await main_mod._run_monitors_dynamic(
                        [],
                        settings,
                        asyncio.Event(),
                        asyncio.Event(),
                        on_ready=lambda: ready_calls.append(True),
                    )

        asyncio.run(run())
        assert ready_calls == []

    def test_terminal_update_ready_failure_drains_runtime_tasks(self):
        import core.main as main_mod
        from core.update_center import UpdateRestartError

        settings = MagicMock(monitor_stale_seconds=300)
        settings.get_dvr_connections.return_value = []

        async def run():
            with (
                patch(
                    "core.main._read_config_snapshot_async",
                    new=AsyncMock(return_value=(None, "")),
                ),
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                with pytest.raises(UpdateRestartError, match="restart failed"):
                    await main_mod._run_monitors_dynamic(
                        [],
                        settings,
                        asyncio.Event(),
                        asyncio.Event(),
                        on_ready=lambda: (_ for _ in ()).throw(
                            UpdateRestartError("restart failed")
                        ),
                    )

            live_runtime_tasks = [
                task.get_name()
                for task in asyncio.all_tasks()
                if not task.done()
                and task.get_name() in {"config-watcher", "monitor-watchdog"}
            ]
            assert live_runtime_tasks == []

        asyncio.run(run())
        assert main_mod._dvr_tasks == {}
        assert main_mod._dvr_monitors == {}

    def test_unavailable_dvr_retries_with_capped_exponential_backoff(self):
        import core.main as main_mod

        dvr = self._connection("dvr_retry")
        waits: list[float] = []

        async def fake_wait(_shutdown, delay):
            waits.append(delay)
            return len(waits) < 9

        async def run():
            with (
                patch("core.main._init_dvr_monitor_sync", return_value=None) as init,
                patch("core.main._wait_for_retry", side_effect=fake_wait),
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._supervise_dvr(dvr, MagicMock(), asyncio.Event(), False)
            assert init.call_count == 9

        asyncio.run(run())
        assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]

    def test_retry_only_supervisor_is_cancelled_without_waiting_for_backoff(self):
        import core.main as main_mod

        dvr = self._connection("dvr_cancel")
        init_called = asyncio.Event()
        original_tasks = dict(main_mod._dvr_tasks)
        original_monitors = dict(main_mod._dvr_monitors)

        async def run():
            loop = asyncio.get_running_loop()

            def unavailable(*_args):
                loop.call_soon_threadsafe(init_called.set)
                return None

            main_mod._dvr_tasks = {}
            main_mod._dvr_monitors = {}
            with (
                patch("core.main._init_dvr_monitor_sync", side_effect=unavailable),
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._start_dvr_supervisor(dvr, MagicMock(), asyncio.Event())
                await asyncio.wait_for(init_called.wait(), timeout=1.0)
                started = time.monotonic()
                await main_mod._stop_dvr_task(dvr.id)
                assert time.monotonic() - started < 0.2
                assert dvr.id not in main_mod._dvr_tasks

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks
            main_mod._dvr_monitors = original_monitors

    def test_cancelled_initialization_cleans_up_monitor_returned_by_worker(self):
        import core.main as main_mod

        dvr = self._connection("dvr_late")
        worker_started = threading.Event()
        release_worker = threading.Event()
        monitor = MagicMock()
        monitor.dvr_name = dvr.name
        monitor.running = True
        monitor.alert_manager.alert_instances = {}

        def initialize_late(*_args):
            worker_started.set()
            release_worker.wait(timeout=1.0)
            return monitor

        async def run():
            with patch("core.main._init_dvr_monitor_sync", side_effect=initialize_late):
                task = asyncio.create_task(
                    main_mod._init_dvr_monitor_async(dvr, MagicMock(), False)
                )
                await asyncio.to_thread(worker_started.wait, 1.0)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                release_worker.set()
                deadline = time.monotonic() + 1.0
                while monitor.stop_monitoring.call_count == 0:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.01)

        asyncio.run(run())
        monitor.stop_monitoring.assert_called_once()
        assert monitor.running is False

    def test_supervisor_cancel_before_monitor_start_publishes_running_is_terminal(self):
        import core.main as main_mod
        from core.engine.event_monitor import EventMonitor

        dvr = self._connection("dvr_start_cancel_race")
        dvr.base_url = f"http://{dvr.host}:{dvr.port}"
        worker_entered = threading.Event()
        release_worker = threading.Event()
        worker_exited = threading.Event()

        class PausedBeforeStartMonitor(EventMonitor):
            def __init__(self):
                super().__init__(dvr=dvr, validation_only=True)
                self.stop_calls = 0

            def start_monitoring(self):
                # The worker is already executing, so cancelling its awaiting
                # asyncio task cannot cancel this underlying thread. Pause just
                # before EventMonitor's lifecycle transition to reproduce the
                # exact start/stop interleaving.
                worker_entered.set()
                release_worker.wait(timeout=2.0)
                try:
                    super().start_monitoring()
                finally:
                    worker_exited.set()

            def stop_monitoring(self):
                self.stop_calls += 1
                super().stop_monitoring()

        monitor = PausedBeforeStartMonitor()
        original_tasks = dict(main_mod._dvr_tasks)
        original_monitors = dict(main_mod._dvr_monitors)
        original_watchdog = main_mod._watchdog

        async def run():
            main_mod._dvr_tasks = {}
            main_mod._dvr_monitors = {}
            main_mod._watchdog = None
            shutdown = asyncio.Event()
            with (
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
                patch("core.main._stop_alert_manager_resources") as cleanup,
            ):
                supervisor = asyncio.create_task(
                    main_mod._supervise_dvr(
                        dvr,
                        MagicMock(),
                        shutdown,
                        initial_monitor=monitor,
                    )
                )
                main_mod._dvr_tasks[dvr.id] = supervisor
                assert await asyncio.to_thread(worker_entered.wait, 1.0)

                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)

                assert supervisor.done()
                assert monitor.stop_calls == 1
                assert monitor._stop_requested is True
                assert monitor.running is False
                cleanup.assert_called_once_with(None, dvr_name=dvr.name)

                # Releasing a worker that was already executing must not allow
                # it to publish running state after the supervisor has exited.
                release_worker.set()
                assert await asyncio.to_thread(worker_exited.wait, 1.0)
                assert monitor.running is False
                monitor._monitor_events_loop = MagicMock()
                monitor.start_monitoring()
                monitor._monitor_events_loop.assert_not_called()

                # Finalizers and registry cleanup may ask again, but neither the
                # monitor stop nor its alert/queue cleanup may run twice.
                main_mod._request_monitor_stop(monitor)
                assert monitor.stop_calls == 1
                cleanup.assert_called_once_with(None, dvr_name=dvr.name)

        try:
            asyncio.run(run())
        finally:
            release_worker.set()
            monitor.running = False
            main_mod._dvr_tasks = original_tasks
            main_mod._dvr_monitors = original_monitors
            main_mod._watchdog = original_watchdog

    def test_added_dvr_is_reconciled_without_core_restart(self):
        import core.main as main_mod

        dvr = self._connection("dvr_added")
        settings = MagicMock()
        settings.get_dvr_connections.return_value = [dvr]
        original_tasks = dict(main_mod._dvr_tasks)

        async def run():
            main_mod._dvr_tasks = {}
            with (
                patch("core.main.get_settings", return_value=settings),
                patch(
                    "core.main._reconcile_dvr",
                    new=AsyncMock(return_value=True),
                ) as reconcile,
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._handle_config_reload(
                    _settings(),
                    _settings(_dvr("dvr_added")),
                    MagicMock(),
                    shutdown_event=asyncio.Event(),
                )
            reconcile.assert_awaited_once()
            assert reconcile.await_args.args[0] is dvr

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks

    def test_dvr_api_key_change_reconciles_as_target_change(self):
        import core.main as main_mod

        dvr = self._connection("dvr_key")
        settings = MagicMock()
        settings.get_dvr_connections.return_value = [dvr]
        old = _settings({**_dvr("dvr_key"), "api_key": "old-encrypted-key"})
        new = _settings({**_dvr("dvr_key"), "api_key": "new-encrypted-key"})
        original_tasks = dict(main_mod._dvr_tasks)

        async def run():
            main_mod._dvr_tasks = {dvr.id: object()}
            with (
                patch("core.main.get_settings", return_value=settings),
                patch(
                    "core.main._reconcile_dvr",
                    new=AsyncMock(return_value=True),
                ) as reconcile,
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._handle_config_reload(old, new, MagicMock())
            reconcile.assert_awaited_once()
            assert reconcile.await_args.kwargs["preserve_healthy_monitor"] is False

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks

    def test_unavailable_dvr_recovers_without_restarting_core(self):
        import core.main as main_mod

        dvr = self._connection("dvr_recover")
        monitor = SimpleNamespace(
            dvr=dvr,
            dvr_name=dvr.name,
            running=False,
            last_freshness_at=0.0,
            alert_manager=SimpleNamespace(alert_instances={}),
        )
        attempts = 0
        waits: list[float] = []
        shutdown = asyncio.Event()

        def initialize(*_args):
            nonlocal attempts
            attempts += 1
            return monitor if attempts == 3 else None

        async def no_delay(_shutdown, delay):
            waits.append(delay)
            return True

        async def run_monitor(active_monitor):
            active_monitor.running = True
            active_monitor.last_freshness_at = time.time()
            shutdown.set()

        async def run():
            with (
                patch("core.main._init_dvr_monitor_sync", side_effect=initialize),
                patch("core.main._wait_for_retry", side_effect=no_delay),
                patch("core.main._run_dvr", side_effect=run_monitor),
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._supervise_dvr(dvr, MagicMock(), shutdown)

        asyncio.run(run())
        assert attempts == 3
        assert waits == [1.0, 2.0]
        assert monitor.last_freshness_at > 0

    def test_unexpected_monitor_exit_releases_notification_queue_before_retry(self):
        import core.main as main_mod
        from core.notifications.notification import NotificationManager

        dvr = self._connection("dvr_unexpected_exit")
        manager = NotificationManager(rate_limit=10, rate_window=60)
        monitor = SimpleNamespace(
            dvr=dvr,
            dvr_name=dvr.name,
            running=True,
            last_freshness_at=0.0,
            start_monitoring=lambda: None,
            stop_monitoring=MagicMock(),
            alert_manager=SimpleNamespace(
                notification_manager=manager,
                alert_instances={},
            ),
        )

        async def run():
            with (
                patch.object(main_mod, "_dvr_monitors", {}),
                patch.object(main_mod, "_watchdog", None),
                patch("core.main._wait_for_retry", new=AsyncMock(return_value=False)),
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._supervise_dvr(
                    dvr,
                    MagicMock(),
                    asyncio.Event(),
                    initial_monitor=monitor,
                )

        asyncio.run(run())

        monitor.stop_monitoring.assert_called_once_with()
        assert monitor.running is False
        assert manager.enqueue_notification(
            "Rejected after monitor exit",
            "Message",
            dvr_id=dvr.id,
            event_type="runtime",
        ) is False

    def test_disabling_dvr_stops_active_retry_supervisor(self):
        import core.main as main_mod

        settings = MagicMock()
        settings.get_dvr_connections.return_value = []
        original_tasks = dict(main_mod._dvr_tasks)

        async def run():
            main_mod._dvr_tasks = {"dvr_disable": object()}
            with (
                patch("core.main.get_settings", return_value=settings),
                patch("core.main._stop_dvr_task", new=AsyncMock()) as stop_dvr,
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._handle_config_reload(
                    _settings(_dvr("dvr_disable", enabled=True)),
                    _settings(_dvr("dvr_disable", enabled=False)),
                    MagicMock(),
                )
            stop_dvr.assert_awaited_once_with("dvr_disable")

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks

    def test_reconciliation_is_concurrent_and_bounded(self):
        import core.main as main_mod

        ids = [f"dvr_{index}" for index in range(6)]
        raw_dvrs = [
            _dvr(dvr_id, host=f"192.0.2.{index + 1}")
            for index, dvr_id in enumerate(ids)
        ]
        connections = [self._connection(dvr_id) for dvr_id in ids]
        settings = MagicMock()
        settings.get_dvr_connections.return_value = connections
        current = 0
        maximum = 0
        original_tasks = dict(main_mod._dvr_tasks)

        async def reconcile(*_args, **_kwargs):
            nonlocal current, maximum
            current += 1
            maximum = max(maximum, current)
            await asyncio.sleep(0.03)
            current -= 1
            return True

        async def run():
            main_mod._dvr_tasks = {dvr_id: object() for dvr_id in ids}
            with (
                patch("core.main.get_settings", return_value=settings),
                patch("core.main._reconcile_dvr", side_effect=reconcile),
                patch("core.main._persist_watchdog_async", new=AsyncMock()),
            ):
                await main_mod._handle_config_reload(
                    _settings(*raw_dvrs, log_level=1),
                    _settings(*raw_dvrs, log_level=2),
                    MagicMock(),
                    shutdown_event=asyncio.Event(),
                    reconcile_semaphore=asyncio.Semaphore(2),
                )

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks
        assert maximum == 2

    def test_failed_validation_probe_is_stopped_before_return(self):
        import core.main as main_mod

        dvr = self._connection("dvr_failed_probe")
        monitor = SimpleNamespace(
            dvr=dvr,
            dvr_name=dvr.name,
            running=False,
            last_freshness_at=0.0,
            alert_manager=None,
            stop_monitoring=MagicMock(),
        )
        task_finished = asyncio.Event()

        async def run_probe(active_monitor):
            active_monitor.running = True
            try:
                while active_monitor.running:
                    await asyncio.sleep(0)
            finally:
                task_finished.set()

        async def run():
            with (
                patch(
                    "core.main._init_dvr_monitor_async",
                    new=AsyncMock(return_value=monitor),
                ),
                patch("core.main._run_dvr", side_effect=run_probe),
            ):
                result = await main_mod._stage_monitor_replacement(
                    dvr,
                    MagicMock(),
                    False,
                    verification_timeout=0.02,
                )
            assert result is False
            assert task_finished.is_set()
            assert monitor.running is False

        asyncio.run(run())
        monitor.stop_monitoring.assert_called_once_with()

    def test_failed_safe_replacement_cleans_old_monitor_and_retries_desired(self):
        import core.main as main_mod

        dvr = self._connection("dvr_safe")
        old_monitor = SimpleNamespace(
            dvr=dvr,
            dvr_name=dvr.name,
            running=True,
            last_freshness_at=time.time(),
        )
        original_tasks = dict(main_mod._dvr_tasks)
        original_monitors = dict(main_mod._dvr_monitors)
        original_watchdog = main_mod._watchdog

        async def old_supervisor():
            await asyncio.Event().wait()

        async def run():
            task = asyncio.create_task(old_supervisor())
            desired_settings = MagicMock()
            shutdown = asyncio.Event()
            main_mod._dvr_tasks = {dvr.id: task}
            main_mod._dvr_monitors = {dvr.id: old_monitor}
            main_mod._watchdog = SimpleNamespace(stale_threshold_seconds=300)
            replacement_supervisor = MagicMock()
            with (
                patch(
                    "core.main._stage_monitor_replacement",
                    new=AsyncMock(return_value=None),
                ),
                patch("core.main._stop_dvr_task", new=AsyncMock()) as stop_dvr,
                patch(
                    "core.main._start_dvr_supervisor",
                    new=AsyncMock(return_value=replacement_supervisor),
                ) as start_dvr,
            ):
                applied = await main_mod._reconcile_dvr(
                    dvr,
                    desired_settings,
                    shutdown,
                    False,
                    preserve_healthy_monitor=True,
                )
            assert applied is False
            stop_dvr.assert_awaited_once_with(dvr.id)
            start_dvr.assert_awaited_once_with(
                dvr,
                desired_settings,
                shutdown,
                False,
                initialization_semaphore=None,
            )
            assert main_mod._dvr_tasks[dvr.id] is task
            assert main_mod._dvr_monitors[dvr.id] is old_monitor
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks
            main_mod._dvr_monitors = original_monitors
            main_mod._watchdog = original_watchdog

    def test_staged_same_target_event_has_exactly_one_active_processor(self):
        import core.main as main_mod
        from core.engine.event_monitor import EventMonitor

        dvr = self._connection("dvr_single_processor")
        dvr.base_url = f"http://{dvr.host}:{dvr.port}"
        effects = {"notifications": 0, "activity": 0, "state": 0}

        class CountingAlertManager:
            async def process_event(self, _event_type, _event_data):
                effects["notifications"] += 1
                effects["activity"] += 1
                effects["state"] += 1
                return "Channel-Watching"

        old_monitor = EventMonitor(dvr=dvr, alert_manager=CountingAlertManager())
        old_monitor.running = True
        old_monitor.last_freshness_at = time.time()
        probe = EventMonitor(dvr=dvr, validation_only=True)
        probe_started = asyncio.Event()
        release_probe_event = asyncio.Event()
        desired_started = asyncio.Event()
        original_tasks = dict(main_mod._dvr_tasks)
        original_monitors = dict(main_mod._dvr_monitors)
        original_watchdog = main_mod._watchdog

        async def old_supervisor():
            await asyncio.Event().wait()

        async def run_probe(active_monitor):
            assert active_monitor is probe
            active_monitor.running = True
            probe_started.set()
            await release_probe_event.wait()
            await active_monitor._process_event_line('{"Type":"watching"}')
            while active_monitor.running:
                await asyncio.sleep(0)

        async def start_desired(*_args, **_kwargs):
            # Promotion may only start after both the probe and old processor
            # have become inactive.
            assert probe.running is False
            assert old_monitor.running is False
            desired_started.set()
            return MagicMock()

        async def run():
            old_task = asyncio.create_task(old_supervisor())
            main_mod._dvr_tasks = {dvr.id: old_task}
            main_mod._dvr_monitors = {dvr.id: old_monitor}
            main_mod._watchdog = None
            with (
                patch(
                    "core.main._init_dvr_monitor_async",
                    new=AsyncMock(return_value=probe),
                ) as initialize_probe,
                patch("core.main._run_dvr", side_effect=run_probe),
                patch(
                    "core.main._start_dvr_supervisor",
                    side_effect=start_desired,
                ),
            ):
                reconciliation = asyncio.create_task(
                    main_mod._reconcile_dvr(
                        dvr,
                        MagicMock(),
                        asyncio.Event(),
                        False,
                        preserve_healthy_monitor=True,
                        verification_timeout=1.0,
                    )
                )
                await asyncio.wait_for(probe_started.wait(), timeout=1.0)

                natural_event = '{"Type":"watching"}'
                await old_monitor._process_event_line(natural_event)
                release_probe_event.set()

                assert await asyncio.wait_for(reconciliation, timeout=1.0) is True
                assert desired_started.is_set()
                assert effects == {
                    "notifications": 1,
                    "activity": 1,
                    "state": 1,
                }
                initialize_probe.assert_awaited_once_with(
                    dvr,
                    ANY,
                    False,
                    None,
                    validation_only=True,
                )

        try:
            asyncio.run(run())
        finally:
            main_mod._dvr_tasks = original_tasks
            main_mod._dvr_monitors = original_monitors
            main_mod._watchdog = original_watchdog


class TestUiSaveTriggersHotReload:
    def test_settings_post_sends_sighup_without_supervisor_restart(self, tmp_path):
        if not hasattr(signal, "SIGHUP"):
            pytest.skip("SIGHUP is not available on this platform")

        settings_file = tmp_path / "settings.json"
        payload = _settings(
            _dvr("dvr_aaa", host="192.168.1.10", name="DVR-A"),
            api_key="test-api-key-12345",
            log_level=1,
            rbac_enabled=False,
        )
        settings_file.write_text(json.dumps(payload), encoding="utf-8")

        from starlette.testclient import TestClient

        supervisor = MagicMock()
        supervisor.supervisor.getProcessInfo.return_value = {
            "pid": 4321,
            "statename": "RUNNING",
        }

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        ):
            import ui.backend.main as ui_main

            with (
                patch.object(ui_main, "API_KEY_CACHE", "test-api-key-12345"),
                patch.object(ui_main, "RBAC_ENABLED", False),
                patch.object(ui_main, "get_supervisor_proxy", return_value=supervisor),
                patch.object(ui_main.os, "kill") as kill_mock,
            ):
                client = TestClient(ui_main.app, raise_server_exceptions=False)
                response = client.post(
                    "/api/settings",
                    json={**payload, "rbac_enabled": True},
                    headers={"X-API-Key": "test-api-key-12345"},
                )

        assert response.status_code == 200
        kill_mock.assert_called_once_with(4321, signal.SIGHUP)
        supervisor.supervisor.stopProcess.assert_not_called()
        supervisor.supervisor.startProcess.assert_not_called()
        assert (
            json.loads(settings_file.read_text(encoding="utf-8"))["rbac_enabled"]
            is True
        )
        assert ui_main.RBAC_ENABLED is False


class TestDvrInitializationResourceOwnership:
    @staticmethod
    def _disk_alert():
        return MagicMock(
            spec=[
                "log_storage_info",
                "start_monitoring",
                "_start_health_checker",
                "set_startup_complete",
                "stop_monitoring",
                "stop_cleanup",
            ]
        )

    @staticmethod
    def _dvr():
        return SimpleNamespace(
            id="dvr_resource",
            name="Resource DVR",
            host="192.0.2.40",
            port=8089,
            api_key="",
            overrides={},
        )

    @staticmethod
    def _settings():
        return SimpleNamespace(global_rate_limit=20, global_rate_window=300)

    def test_validation_only_initialization_creates_no_runtime_resources(self):
        import core.main as main_mod

        validation_monitor = object()
        with (
            patch("core.main.check_server_connectivity", return_value=True),
            patch("core.main.EventMonitor", return_value=validation_monitor) as event,
            patch("core.main.initialize_notifications") as notifications,
            patch("core.main.initialize_alerts") as alerts,
            patch("core.main.ChannelInfoProvider") as channel_provider,
            patch("core.main.initialize_event_monitor") as full_event_monitor,
        ):
            result = main_mod._init_dvr_monitor_sync(
                self._dvr(), self._settings(), validation_only=True
            )

        assert result is validation_monitor
        event.assert_called_once_with(dvr=self._dvr(), validation_only=True)
        notifications.assert_not_called()
        alerts.assert_not_called()
        channel_provider.assert_not_called()
        full_event_monitor.assert_not_called()

    def test_event_monitor_construction_failure_never_starts_disk_threads(self):
        import core.main as main_mod
        from core.notifications.notification import NotificationManager

        disk_alert = self._disk_alert()
        notification_manager = NotificationManager()
        alert_manager = SimpleNamespace(
            notification_manager=notification_manager,
            alert_instances={"Disk-Space": disk_alert},
        )
        channel_provider = MagicMock()
        channel_provider.cache_channels.return_value = 0

        with (
            patch("core.main.check_server_connectivity", return_value=True),
            patch(
                "core.main.initialize_notifications",
                return_value=notification_manager,
            ),
            patch("core.main.initialize_alerts", return_value=alert_manager),
            patch("core.main.ChannelInfoProvider", return_value=channel_provider),
            patch("core.main.initialize_event_monitor", return_value=None),
        ):
            monitor = main_mod._init_dvr_monitor_sync(
                self._dvr(), self._settings()
            )

        assert monitor is None
        disk_alert.log_storage_info.assert_called_once_with()
        disk_alert.start_monitoring.assert_not_called()
        disk_alert._start_health_checker.assert_not_called()
        disk_alert.stop_monitoring.assert_called_once_with()
        disk_alert.stop_cleanup.assert_called_once_with()
        assert notification_manager._queue_accepting is False

    def test_disk_thread_start_failure_cleans_the_entire_monitor_attempt(self):
        import core.main as main_mod
        from core.notifications.notification import NotificationManager

        disk_alert = self._disk_alert()
        disk_alert._start_health_checker.side_effect = RuntimeError("thread failed")
        notification_manager = NotificationManager()
        alert_manager = SimpleNamespace(
            notification_manager=notification_manager,
            alert_instances={"Disk-Space": disk_alert},
        )
        channel_provider = MagicMock()
        channel_provider.cache_channels.return_value = 0

        with (
            patch("core.main.check_server_connectivity", return_value=True),
            patch(
                "core.main.initialize_notifications",
                return_value=notification_manager,
            ),
            patch("core.main.initialize_alerts", return_value=alert_manager),
            patch("core.main.ChannelInfoProvider", return_value=channel_provider),
            patch("core.main.initialize_event_monitor", return_value=object()),
            pytest.raises(RuntimeError, match="thread failed"),
        ):
            main_mod._init_dvr_monitor_sync(self._dvr(), self._settings())

        disk_alert.start_monitoring.assert_called_once_with()
        disk_alert.stop_monitoring.assert_called_once_with()
        disk_alert.stop_cleanup.assert_called_once_with()
        assert notification_manager._queue_accepting is False

    def test_metadata_preload_failure_cleans_alert_owned_resources(self):
        import core.main as main_mod
        from core.notifications.notification import NotificationManager

        disk_alert = self._disk_alert()
        notification_manager = NotificationManager()
        alert_manager = SimpleNamespace(
            notification_manager=notification_manager,
            alert_instances={"Disk-Space": disk_alert},
        )
        channel_provider = MagicMock()
        channel_provider.cache_channels.side_effect = RuntimeError("metadata failed")

        with (
            patch("core.main.check_server_connectivity", return_value=True),
            patch(
                "core.main.initialize_notifications",
                return_value=notification_manager,
            ),
            patch("core.main.initialize_alerts", return_value=alert_manager),
            patch("core.main.ChannelInfoProvider", return_value=channel_provider),
            pytest.raises(RuntimeError, match="metadata failed"),
        ):
            main_mod._init_dvr_monitor_sync(self._dvr(), self._settings())

        disk_alert.start_monitoring.assert_not_called()
        disk_alert.stop_monitoring.assert_called_once_with()
        disk_alert.stop_cleanup.assert_called_once_with()
        assert notification_manager._queue_accepting is False

    def test_monitor_stop_failure_still_cleans_alert_owned_resources(self):
        import core.main as main_mod
        from core.notifications.notification import NotificationManager

        disk_alert = self._disk_alert()
        notification_manager = NotificationManager()
        monitor = SimpleNamespace(
            running=True,
            dvr_name="Resource DVR",
            stop_monitoring=MagicMock(side_effect=RuntimeError("monitor stop failed")),
            alert_manager=SimpleNamespace(
                notification_manager=notification_manager,
                alert_instances={"Disk-Space": disk_alert},
            ),
        )

        with pytest.raises(RuntimeError, match="monitor stop failed"):
            main_mod._request_monitor_stop(monitor)

        assert monitor.running is False
        disk_alert.stop_monitoring.assert_called_once_with()
        disk_alert.stop_cleanup.assert_called_once_with()
        assert notification_manager._queue_accepting is False
