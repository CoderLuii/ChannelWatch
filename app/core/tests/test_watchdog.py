import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.watchdog import Watchdog, load_watchdog_snapshot, summarize_enabled_dvrs


def _monitor(*, dvr_id="dvr_aaa11111", name="Living Room"):
    return SimpleNamespace(
        dvr=SimpleNamespace(id=dvr_id, name=name),
        dvr_name=name,
        host="192.168.1.10",
        port=8089,
        running=True,
        connected=True,
        alerts_paused=False,
        _connection_status="online",
        _last_seen=0.0,
        last_event_at=0.0,
        last_freshness_at=0.0,
        last_freshness_source=None,
        alert_manager=SimpleNamespace(notification_manager=MagicMock()),
    )


class TestWatchdogSnapshot:
    def test_read_only_config_uses_shared_ephemeral_snapshot(self, tmp_path):
        status_file = tmp_path / "runtime" / "watchdog_status.json"
        with (
            patch.dict(os.environ, {"CHANNELWATCH_CONFIG_READ_ONLY": "1"}),
            patch("core.watchdog.EPHEMERAL_WATCHDOG_STATUS_FILE", status_file),
        ):
            watchdog = Watchdog()
            snapshot = watchdog.persist({}, {}, force=True, now=100.0)

        assert snapshot["generated_at"] is not None
        assert load_watchdog_snapshot(status_file) == snapshot

    def test_read_only_cross_process_snapshot_stays_fresh_beyond_five_minutes(
        self, tmp_path
    ):
        ephemeral_file = tmp_path / "runtime" / "watchdog_status.json"
        monitor = _monitor()
        task = MagicMock()
        task.done.return_value = False
        enabled = [
            {
                "id": "dvr_aaa11111",
                "name": "Living Room",
                "host": "192.168.1.10",
                "port": 8089,
            }
        ]

        with (
            patch.dict(os.environ, {"CHANNELWATCH_CONFIG_READ_ONLY": "1"}),
            patch("core.watchdog.EPHEMERAL_WATCHDOG_STATUS_FILE", ephemeral_file),
        ):
            watchdog = Watchdog(stale_threshold_seconds=300)
            watchdog.mark_fresh(monitor, "poll", timestamp=100.0)
            watchdog.persist(
                {"dvr_aaa11111": task},
                {"dvr_aaa11111": monitor},
                force=True,
                now=100.0,
            )
            watchdog.mark_fresh(monitor, "poll", timestamp=461.0)
            watchdog.persist(
                {"dvr_aaa11111": task},
                {"dvr_aaa11111": monitor},
                force=True,
                now=461.0,
            )
            loaded_by_ui_process = load_watchdog_snapshot()

        summary = summarize_enabled_dvrs(
            enabled,
            loaded_by_ui_process,
            now=462.0,
        )
        assert summary["ready"] is True
        assert summary["dvrs"][0]["freshness_age_seconds"] == 1.0
        assert loaded_by_ui_process["generated_at"] is not None

    def test_snapshot_marks_dvr_dead_when_task_is_done(self):
        watchdog = Watchdog(stale_threshold_seconds=300)
        monitor = _monitor()
        watchdog.mark_fresh(monitor, "event", timestamp=100.0)

        done_task = MagicMock()
        done_task.done.return_value = True

        snapshot = watchdog.snapshot(
            {"dvr_aaa11111": done_task}, {"dvr_aaa11111": monitor}, now=120.0
        )
        dvr = snapshot["dvrs"][0]

        assert dvr["monitoring_status"] == "dead"
        assert dvr["ready"] is False

    def test_snapshot_marks_dvr_stale_when_freshness_expires(self):
        watchdog = Watchdog(stale_threshold_seconds=30)
        monitor = _monitor()
        watchdog.mark_fresh(monitor, "poll", timestamp=100.0)

        alive_task = MagicMock()
        alive_task.done.return_value = False

        snapshot = watchdog.snapshot(
            {"dvr_aaa11111": alive_task}, {"dvr_aaa11111": monitor}, now=145.0
        )
        dvr = snapshot["dvrs"][0]

        assert dvr["monitoring_status"] == "stale"
        assert dvr["freshness_age_seconds"] == 45.0
        assert dvr["ready"] is False

    def test_summary_marks_ready_when_all_enabled_dvrs_are_healthy(self):
        summary = summarize_enabled_dvrs(
            [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "host": "192.168.1.10",
                    "port": 8089,
                }
            ],
            {
                "stale_threshold_seconds": 300,
                "dvrs": [
                    {
                        "id": "dvr_aaa11111",
                        "name": "Living Room",
                        "connected": True,
                        "task_alive": True,
                        "task_done": False,
                        "monitor_registered": True,
                        "monitor_running": True,
                        "monitoring_status": "healthy",
                        "freshness_status": "healthy",
                        "ready": True,
                        "reason": "Freshness updates are current",
                        "last_freshness_ts": 100.0,
                        "last_freshness_at": "2026-01-01T00:01:40+00:00",
                    }
                ],
            },
            now=120.0,
        )

        assert summary["ready"] is True
        assert summary["status"] == "ready"

    def test_retry_supervisor_without_registered_monitor_is_immediately_degraded(self):
        watchdog = Watchdog(stale_threshold_seconds=300)
        monitor = _monitor()
        watchdog.mark_fresh(monitor, "poll", timestamp=100.0)

        alive_supervisor = MagicMock()
        alive_supervisor.done.return_value = False

        snapshot = watchdog.snapshot(
            {"dvr_aaa11111": alive_supervisor}, {}, now=101.0
        )
        dvr = snapshot["dvrs"][0]

        assert dvr["task_alive"] is True
        assert dvr["monitor_registered"] is False
        assert dvr["monitor_running"] is False
        assert dvr["monitoring_status"] == "reconnecting"
        assert dvr["ready"] is False
        assert snapshot["healthy"] is False

    def test_registered_but_stopped_monitor_is_not_ready_with_fresh_timestamp(self):
        watchdog = Watchdog(stale_threshold_seconds=300)
        monitor = _monitor()
        watchdog.mark_fresh(monitor, "poll", timestamp=100.0)
        monitor.running = False

        alive_supervisor = MagicMock()
        alive_supervisor.done.return_value = False

        dvr = watchdog.snapshot(
            {"dvr_aaa11111": alive_supervisor},
            {"dvr_aaa11111": monitor},
            now=101.0,
        )["dvrs"][0]

        assert dvr["monitor_registered"] is True
        assert dvr["monitor_running"] is False
        assert dvr["monitoring_status"] == "dead"
        assert dvr["ready"] is False

    def test_summary_rejects_legacy_snapshot_without_monitor_registration(self):
        summary = summarize_enabled_dvrs(
            [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "host": "192.168.1.10",
                    "port": 8089,
                }
            ],
            {
                "dvrs": [
                    {
                        "id": "dvr_aaa11111",
                        "task_alive": True,
                        "monitor_running": True,
                        "monitoring_status": "healthy",
                        "ready": True,
                    }
                ]
            },
            now=120.0,
        )

        assert summary["ready"] is False
        assert summary["dvrs"][0]["ready"] is False


class TestHotReloadVerification:
    def test_start_verified_dvr_task_notifies_on_failed_freshness_verification(self):
        async def run():
            import core.main as main_mod

            monitor = _monitor()
            watchdog = MagicMock()
            original_tasks = dict(main_mod._dvr_tasks)
            original_monitors = dict(main_mod._dvr_monitors)
            original_watchdog = main_mod._watchdog

            async def fake_run_dvr(_monitor):
                await asyncio.sleep(0.01)

            try:
                main_mod._dvr_tasks = {}
                main_mod._dvr_monitors = {}
                main_mod._watchdog = watchdog

                with (
                    patch("core.main._run_dvr", side_effect=fake_run_dvr),
                    patch(
                        "core.main._verify_monitor_freshness",
                        new=AsyncMock(return_value=(False, "timed out")),
                    ),
                    patch(
                        "core.main._notify_hot_reload_failure", new=AsyncMock()
                    ) as notify_failure,
                ):
                    task = await main_mod._start_verified_dvr_task(
                        monitor, verification_timeout=1.0
                    )
                    await asyncio.gather(task, return_exceptions=True)

                watchdog.attach_monitor.assert_called_once_with(monitor)
                watchdog.persist.assert_called()
                notify_failure.assert_awaited_once_with(monitor, "timed out")
                assert monitor.dvr.id not in main_mod._dvr_tasks
                assert monitor.dvr.id not in main_mod._dvr_monitors
                assert task.done()
                watchdog.remove_dvr.assert_called_once_with(monitor.dvr.id)
            finally:
                main_mod._dvr_tasks = original_tasks
                main_mod._dvr_monitors = original_monitors
                main_mod._watchdog = original_watchdog

        asyncio.run(run())
