# pyright: reportMissingImports=false

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.alerts.disk_space import DiskSpaceAlert
from core.helpers.config import CoreSettings


GIB = 1024**3


@pytest.fixture
def alert_factory(tmp_path):
    def _make_alert():
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")

        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
        ):
            CoreSettings._instance = None
            settings = CoreSettings()

        notification_manager = MagicMock()
        notification_manager.send_notification.return_value = True
        dvr = SimpleNamespace(host="127.0.0.1", port=8089)
        am = SimpleNamespace(
            notification_manager=notification_manager,
            settings=settings,
            dvr=dvr,
            _notification_history={},
            _history_lock=threading.Lock(),
        )
        alert = DiskSpaceAlert(am)
        return alert, notification_manager

    return _make_alert


def run_disk_check(alert, *, free_gib, total_gib, current_time):
    total_bytes = int(total_gib * GIB)
    free_bytes = int(free_gib * GIB)
    disk_info = {
        "free": free_bytes,
        "total": total_bytes,
        "used": total_bytes - free_bytes,
        "path": "/shares/DVR",
    }

    with (
        patch("core.alerts.disk_space.time.time", return_value=current_time),
        patch.object(alert, "_get_disk_info", return_value=disk_info),
        patch("core.alerts.disk_space.record_disk_status"),
    ):
        alert._check_disk_space()

    return dict(alert._disk_state)


def get_persisted_state(alert):
    return {"disk_state": dict(alert._disk_state)}


def get_last_notification_title(notification_manager):
    return notification_manager.send_notification.call_args.args[0]


class TestDiskSpaceAlertSemantics:
    def test_disk_fetch_uses_httpx_and_returns_normalized_payload(
        self, alert_factory
    ):
        alert, _notification_manager = alert_factory()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "disk": {"free": 40 * GIB, "total": 100 * GIB},
            "path": "/shares/TV",
        }

        with patch("core.alerts.disk_space.httpx.get", return_value=response) as get:
            disk_info = alert._get_disk_info()

        get.assert_called_once_with(alert.api_url, timeout=3)
        assert disk_info == {
            "free": 40 * GIB,
            "total": 100 * GIB,
            "path": "/shares/TV",
        }

    def test_terminal_stop_interrupts_owned_threads_and_cannot_resurrect(
        self, alert_factory
    ):
        alert, _notification_manager = alert_factory()
        first_check = threading.Event()
        alert.check_interval = 0.01
        alert.health_check_interval = 0.01

        def record_check():
            first_check.set()

        with patch.object(alert, "_check_disk_space", side_effect=record_check) as check:
            assert alert.start_monitoring() is True
            assert alert._start_health_checker() is True
            assert first_check.wait(timeout=1.0)
            monitoring_thread = alert.monitoring_thread
            health_checker_thread = alert.health_checker_thread

            alert.stop_monitoring()
            calls_after_stop = check.call_count
            time.sleep(0.05)

        assert alert.running is False
        assert alert._shutdown_event.is_set()
        assert monitoring_thread is not None and not monitoring_thread.is_alive()
        assert health_checker_thread is not None and not health_checker_thread.is_alive()
        assert check.call_count == calls_after_stop
        assert alert.start_monitoring() is False
        assert alert._start_health_checker() is False

    def test_health_checker_recovers_only_a_nonterminal_dead_poller(
        self, alert_factory
    ):
        alert, _notification_manager = alert_factory()
        recovered = threading.Event()
        attempts = 0
        attempts_lock = threading.Lock()
        alert.health_check_interval = 0.01

        def controlled_loop():
            nonlocal attempts
            with attempts_lock:
                attempts += 1
                current_attempt = attempts
            if current_attempt == 1:
                with alert._lifecycle_lock:
                    alert.running = False
                return
            recovered.set()
            alert._shutdown_event.wait()
            with alert._lifecycle_lock:
                alert.running = False

        with patch.object(alert, "_monitoring_loop", side_effect=controlled_loop):
            assert alert.start_monitoring() is True
            assert alert._start_health_checker() is True
            assert recovered.wait(timeout=1.0)
            assert attempts == 2
            alert.stop_monitoring()

        assert alert._shutdown_event.is_set()
        assert alert.running is False
        assert attempts == 2

    def test_estimate_time_to_threshold_uses_first_or_boundary(self, alert_factory):
        alert, _notification_manager = alert_factory()
        alert.percent_threshold = 10
        alert.gb_threshold = 50
        total_bytes = 1000 * GIB
        alert.disk_history = [
            {"timestamp": 0, "free_bytes": 200 * GIB, "total_bytes": total_bytes},
            {"timestamp": 3600, "free_bytes": 150 * GIB, "total_bytes": total_bytes},
        ]

        assert alert._estimate_time_to_threshold() == pytest.approx(3600)

    @pytest.mark.parametrize(
        ("severity", "expected_title"),
        [
            ("warning", "⚠️ Low Disk Space Warning"),
            ("critical", "🚨 Low Disk Space Critical"),
        ],
    )
    def test_activity_recorder_reuses_built_notification_payload(
        self, alert_factory, severity, expected_title
    ):
        alert, notification_manager = alert_factory()
        disk_info = {
            "free": 20 * GIB,
            "total": 500 * GIB,
            "used": 480 * GIB,
            "path": "/shares/DVR",
        }

        with patch(
            "core.alerts.disk_space.record_disk_status"
        ) as record_disk_status_mock:
            sent = alert._send_disk_space_alert(
                disk_info["free"],
                disk_info["total"],
                disk_info,
                severity=severity,
            )

        assert sent is True
        assert notification_manager.send_notification.call_args.args == (
            expected_title,
            record_disk_status_mock.call_args.kwargs["message"],
        )
        assert record_disk_status_mock.call_args.kwargs["title"] == expected_title

    def test_activity_recorder_reuses_test_labeled_notification_payload(
        self, alert_factory
    ):
        alert, notification_manager = alert_factory()
        disk_info = {
            "free": 20 * GIB,
            "total": 500 * GIB,
            "used": 480 * GIB,
            "path": "/shares/DVR",
        }

        with patch(
            "core.alerts.disk_space.record_disk_status"
        ) as record_disk_status_mock:
            sent = alert._send_disk_space_alert(
                disk_info["free"],
                disk_info["total"],
                disk_info,
                severity="critical",
                is_test=True,
            )

        expected_title = "🚨 [TEST] Low Disk Space Critical"
        assert sent is True
        assert notification_manager.send_notification.call_args.args == (
            expected_title,
            "Free Space: 20.00 GB / 500.00 GB (4.0%)\nUsed Space: 480.00 GB\nDVR Path: /shares/DVR",
        )
        record_disk_status_mock.assert_not_called()

    def test_test_disk_alert_does_not_record_recent_activity(self, alert_factory):
        alert, notification_manager = alert_factory()
        disk_info = {
            "free": 20 * GIB,
            "total": 500 * GIB,
            "used": 480 * GIB,
            "path": "/shares/DVR",
        }

        with patch(
            "core.alerts.disk_space.record_disk_status"
        ) as record_disk_status_mock:
            sent = alert._send_disk_space_alert(
                disk_info["free"],
                disk_info["total"],
                disk_info,
                severity="critical",
                is_test=True,
            )

        assert sent is True
        record_disk_status_mock.assert_not_called()

    @pytest.mark.parametrize(
        ("free_gib", "total_gib", "expected_status", "expected_title"),
        [
            (45, 500, "warning", "⚠️ Low Disk Space Warning"),
            (20, 500, "critical", "🚨 Low Disk Space Critical"),
        ],
    )
    def test_startup_grace_suppresses_until_eligible_then_notifies(
        self,
        alert_factory,
        free_gib,
        total_gib,
        expected_status,
        expected_title,
    ):
        alert, notification_manager = alert_factory()
        alert.start_monitoring_time = 100.0
        alert.startup_complete_time = 105.0

        state = run_disk_check(
            alert,
            free_gib=free_gib,
            total_gib=total_gib,
            current_time=109.0,
        )

        assert notification_manager.send_notification.call_count == 0
        assert state["status"] == expected_status
        assert state["last_notified_severity"] is None
        assert state["last_notified_free_bytes"] is None
        assert state["last_notified_free_percentage"] is None

        state = run_disk_check(
            alert,
            free_gib=free_gib,
            total_gib=total_gib,
            current_time=111.0,
        )

        assert notification_manager.send_notification.call_count == 1
        assert state["status"] == expected_status
        assert state["last_notified_severity"] == expected_status
        assert state["last_notification_at"] == 111.0
        assert get_last_notification_title(notification_manager) == expected_title

    def test_warning_steady_state_is_suppressed_but_critical_escalation_notifies_immediately(
        self, alert_factory
    ):
        alert, notification_manager = alert_factory()
        alert.start_monitoring_time = 0.0
        alert.startup_complete_time = 0.0

        state = run_disk_check(alert, free_gib=45, total_gib=500, current_time=20.0)

        assert notification_manager.send_notification.call_count == 1
        assert state["status"] == "warning"
        assert state["last_notified_severity"] == "warning"
        assert (
            get_last_notification_title(notification_manager)
            == "⚠️ Low Disk Space Warning"
        )

        state = run_disk_check(alert, free_gib=44.6, total_gib=500, current_time=40.0)

        assert notification_manager.send_notification.call_count == 1
        assert state["status"] == "warning"
        assert state["last_notified_free_bytes"] == 45 * GIB
        assert state["last_notified_free_percentage"] == pytest.approx(9.0)

        state = run_disk_check(alert, free_gib=20, total_gib=500, current_time=60.0)

        assert notification_manager.send_notification.call_count == 2
        assert state["status"] == "critical"
        assert state["last_notified_severity"] == "critical"
        assert state["last_notified_free_bytes"] == 20 * GIB
        assert state["last_notified_free_percentage"] == pytest.approx(4.0)
        assert (
            get_last_notification_title(notification_manager)
            == "🚨 Low Disk Space Critical"
        )

    def test_failed_notification_does_not_persist_last_notified_snapshot(
        self, alert_factory
    ):
        alert, notification_manager = alert_factory()
        notification_manager.send_notification.return_value = False
        alert.start_monitoring_time = 0.0
        alert.startup_complete_time = 0.0

        state = run_disk_check(alert, free_gib=20, total_gib=500, current_time=20.0)

        assert notification_manager.send_notification.call_count == 1
        assert state["status"] == "critical"
        assert state["last_seen_free_bytes"] == 20 * GIB
        assert state["last_notified_severity"] is None
        assert state["last_notified_free_bytes"] is None
        assert state["last_notified_free_percentage"] is None
        assert state["last_notification_at"] is None

    def test_critical_state_suppresses_noise_until_meaningful_worsening_then_recovery_resets_reentry(
        self, alert_factory
    ):
        alert, notification_manager = alert_factory()
        alert.start_monitoring_time = 0.0
        alert.startup_complete_time = 0.0
        alert.cooldown_period = 0

        state = run_disk_check(alert, free_gib=20, total_gib=500, current_time=20.0)

        assert notification_manager.send_notification.call_count == 1
        assert state["status"] == "critical"
        assert state["last_notified_severity"] == "critical"

        state = run_disk_check(alert, free_gib=19.4, total_gib=500, current_time=40.0)

        assert notification_manager.send_notification.call_count == 1
        assert state["status"] == "critical"
        assert state["last_notified_free_bytes"] == 20 * GIB
        assert state["last_notified_free_percentage"] == pytest.approx(4.0)

        state = run_disk_check(alert, free_gib=18.8, total_gib=500, current_time=60.0)

        assert notification_manager.send_notification.call_count == 2
        assert state["status"] == "critical"
        assert state["last_notified_free_bytes"] == int(18.8 * GIB)
        assert state["last_notified_free_percentage"] == pytest.approx(3.76)

        state = run_disk_check(alert, free_gib=30, total_gib=500, current_time=80.0)

        assert notification_manager.send_notification.call_count == 2
        assert state["status"] == "warning"
        assert state["last_notified_severity"] == "critical"

        state = run_disk_check(alert, free_gib=60, total_gib=500, current_time=100.0)

        assert notification_manager.send_notification.call_count == 2
        assert state["status"] == "normal"
        assert state["last_notified_severity"] is None
        assert state["last_notified_free_bytes"] is None
        assert state["last_notified_free_percentage"] is None

        state = run_disk_check(alert, free_gib=45, total_gib=500, current_time=120.0)

        assert notification_manager.send_notification.call_count == 3
        assert state["status"] == "warning"
        assert state["last_notified_severity"] == "warning"
        assert (
            get_last_notification_title(notification_manager)
            == "⚠️ Low Disk Space Warning"
        )

    def test_restart_warning_reapplies_grace_and_persists_last_notified_snapshot(
        self, alert_factory
    ):
        first_alert, first_notification_manager = alert_factory()
        first_alert.start_monitoring_time = 0.0
        first_alert.startup_complete_time = 0.0

        state = run_disk_check(
            first_alert, free_gib=45, total_gib=500, current_time=20.0
        )
        persisted_state = get_persisted_state(first_alert)

        assert first_notification_manager.send_notification.call_count == 1
        assert state["last_notified_severity"] == "warning"

        restarted_alert, restarted_notification_manager = alert_factory()
        restarted_alert.start_monitoring_time = 20.0
        restarted_alert.startup_complete_time = 20.0
        restarted_alert._disk_state.update(
            persisted_state.get("disk_state", persisted_state)
        )

        state = run_disk_check(
            restarted_alert, free_gib=44.8, total_gib=500, current_time=29.0
        )

        assert restarted_notification_manager.send_notification.call_count == 0
        assert state["status"] == "warning"
        assert state["last_notified_severity"] == "warning"
        assert state["last_notified_free_bytes"] == 45 * GIB

        state = run_disk_check(
            restarted_alert, free_gib=44.6, total_gib=500, current_time=40.0
        )

        assert restarted_notification_manager.send_notification.call_count == 0
        assert state["status"] == "warning"
        assert state["last_notified_severity"] == "warning"
        assert state["last_notified_free_percentage"] == pytest.approx(9.0)

    def test_restart_critical_reapplies_grace_and_persists_last_notified_snapshot(
        self, alert_factory
    ):
        first_alert, first_notification_manager = alert_factory()
        first_alert.start_monitoring_time = 0.0
        first_alert.startup_complete_time = 0.0

        state = run_disk_check(
            first_alert, free_gib=20, total_gib=500, current_time=20.0
        )
        persisted_state = get_persisted_state(first_alert)

        assert first_notification_manager.send_notification.call_count == 1
        assert state["last_notified_severity"] == "critical"

        restarted_alert, restarted_notification_manager = alert_factory()
        restarted_alert.start_monitoring_time = 20.0
        restarted_alert.startup_complete_time = 20.0
        restarted_alert._disk_state.update(
            persisted_state.get("disk_state", persisted_state)
        )

        state = run_disk_check(
            restarted_alert, free_gib=19.8, total_gib=500, current_time=29.0
        )

        assert restarted_notification_manager.send_notification.call_count == 0
        assert state["status"] == "critical"
        assert state["last_notified_severity"] == "critical"

        state = run_disk_check(
            restarted_alert, free_gib=19.4, total_gib=500, current_time=40.0
        )

        assert restarted_notification_manager.send_notification.call_count == 0
        assert state["status"] == "critical"
        assert state["last_notified_free_bytes"] == 20 * GIB
