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
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
        env.pop("CHANNELS_DVR_HOST", None)
        env.pop("CHANNELS_DVR_PORT", None)
        env.pop("CHANNELS_DVR_SERVERS", None)

        proc = subprocess.Popen(
            [sys.executable, "-m", "core.main"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_DFL),
        )

        try:
            deadline = time.time() + 5.0
            startup_line = ""
            while time.time() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [], 0.1)
                if not ready:
                    assert proc.poll() is None, (
                        "core exited before no-DVR startup completed"
                    )
                    continue
                startup_line = proc.stdout.readline()
                if "Waiting for DVR server configuration" in startup_line:
                    break
            assert "Waiting for DVR server configuration" in startup_line

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
        replacement_monitor = MagicMock()

        async def run():
            main_mod._dvr_tasks = {"dvr_aaa": object(), "dvr_bbb": object()}
            with (
                patch("core.main.get_settings", return_value=new_settings),
                patch("core.main._stop_dvr_task", new=AsyncMock()) as stop_task,
                patch(
                    "core.main._init_dvr_monitor_sync", return_value=replacement_monitor
                ) as init_monitor,
                patch(
                    "core.main._start_verified_dvr_task", new=AsyncMock()
                ) as start_task,
                patch("core.main.log") as log_mock,
            ):
                await main_mod._handle_config_reload(
                    _settings(dvr_a_old, dvr_b),
                    _settings(dvr_a_new, dvr_b),
                    MagicMock(),
                )

                stop_task.assert_awaited_once_with("dvr_aaa")
                init_monitor.assert_called_once_with(dvr_a_conn, new_settings, False)
                start_task.assert_awaited_once_with(replacement_monitor)
                assert all(
                    call.args[0] != "dvr_bbb" for call in stop_task.await_args_list
                )
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
        replacement_monitor = MagicMock()
        new_settings = MagicMock()
        new_settings.get_dvr_connections.return_value = [dvr_a_conn, dvr_b_conn]

        async def run():
            main_mod._dvr_tasks = {"dvr_aaa": object(), "dvr_bbb": object()}
            with (
                patch("core.main.get_settings", return_value=new_settings),
                patch("core.main._stop_dvr_task", new=AsyncMock()) as stop_task,
                patch(
                    "core.main._init_dvr_monitor_sync", return_value=replacement_monitor
                ) as init_monitor,
                patch(
                    "core.main._start_verified_dvr_task", new=AsyncMock()
                ) as start_task,
            ):
                await main_mod._handle_config_reload(
                    _settings(dvr_a, dvr_b, log_level=1),
                    _settings(dvr_a, dvr_b, log_level=2),
                    MagicMock(),
                )

                assert [call.args[0] for call in stop_task.await_args_list] == [
                    "dvr_aaa",
                    "dvr_bbb",
                ]
                assert init_monitor.call_count == 2
                assert [call.args[0] for call in init_monitor.call_args_list] == [
                    dvr_a_conn,
                    dvr_b_conn,
                ]
                assert start_task.await_count == 2

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

        async def mock_handle(old, new, settings, test_mode=False):
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

        async def mock_handle(old, new, settings, test_mode=False):
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

        async def mock_handle(old, new, settings, test_mode=False):
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
