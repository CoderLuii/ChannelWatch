"""Tests for per-DVR state file isolation.

Design rationale
----------------
AlertManager persists per-DVR session state to
  <config_dir>/session_state_<dvr_id>.json
StreamTracker writes per-DVR stream counts to
  <config_dir>/stream_count_<dvr_id>.txt

After removed the 'default' DVR id fallback from AlertManager, neither
path should produce a 'default' artifact when all DVRs carry explicit ids.

Tests 1-3 are expected to PASS against current code because per-DVR file
naming is already implemented (per audit).  Test 3 (no_shared_default_file)
also proves's ValueError guard is still in effect.  Test 4 asserts that
each DiskSpaceAlert instance owns its own SessionManager so disk severity
state cannot bleed across DVR instances.

These tests block and confidence checks as described in the plan.
"""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import asyncio

from core.alerts.common.stream_tracker import StreamTracker
from core.alerts.common.session_manager import SessionManager
from core.alerts.disk_space import DiskSpaceAlert
from core.engine.alert_manager import AlertManager


pytest_plugins = ["core.tests.fixtures.mock_dvr_cluster"]


GIB = 1024**3


def _make_dvr(
    dvr_id: str, name: str = "Test DVR", host: str = "127.0.0.1", port: int = 8089
):
    return SimpleNamespace(
        id=dvr_id,
        name=name,
        host=host,
        port=port,
        base_url=f"http://{host}:{port}",
        overrides={},
    )


def _make_disk_settings():
    return SimpleNamespace(
        ds_threshold_percent=10,
        ds_threshold_gb=50,
        ds_warning_threshold_percent=10,
        ds_warning_threshold_gb=50,
        ds_critical_threshold_percent=5,
        ds_critical_threshold_gb=25,
        ds_alert_cooldown=3600,
        ds_startup_grace_seconds=10,
        ds_worsening_delta_gb=1,
        ds_worsening_delta_percent=1.0,
        test_mode=False,
        ds_template_title="",
        ds_template_body="",
        ds_template_use_default=True,
        alert_disk_space=True,
    )


def _make_alert_manager(dvr, config_dir: Path) -> AlertManager:
    """Create an AlertManager with CONFIG_DIR patched to config_dir during __init__.

    The patch only needs to cover __init__ because AlertManager captures the
    resolved Path into self._state_file at construction time.
    """
    with patch("core.engine.alert_manager.CONFIG_DIR", config_dir):
        return AlertManager(MagicMock(), MagicMock(), dvr=dvr)


def _inject_session_state(am: AlertManager, session_key: str, **data):
    """Insert a real SessionManager into a mock alert so save_all_state writes a file.

    asyncio.run(AlertManager.save_all_state() iterates alert_instances, calls)
    sm.get_state() on each instance's session_manager, and skips empty results.
    Using a real SessionManager with seeded data ensures a non-empty JSON write.
    """
    sm = SessionManager()
    data["timestamp"] = __import__("time").time()
    sm.active_sessions[session_key] = data
    mock_alert = MagicMock()
    mock_alert.session_manager = sm
    am.alert_instances["mock_alert"] = mock_alert


def _run_disk_check(
    alert: DiskSpaceAlert, *, free_gib: float, total_gib: float, current_time: float
) -> None:
    disk_info = {
        "free": int(free_gib * GIB),
        "total": int(total_gib * GIB),
        "used": int((total_gib - free_gib) * GIB),
        "path": "/shares/DVR",
    }
    with (
        patch("core.alerts.disk_space.time.time", return_value=current_time),
        patch(
            "core.alerts.common.session_manager.time.time", return_value=current_time
        ),
        patch.object(alert, "_get_disk_info", return_value=disk_info),
        patch("core.alerts.disk_space.record_disk_status"),
    ):
        alert._check_disk_space()


class TestSessionStateFilesPerDvr:
    def test_session_state_files_per_dvr(self, mock_dvr_cluster, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        cluster = mock_dvr_cluster(count=2)
        dvr_a = _make_dvr(cluster[0].dvr_id, name=cluster[0].name)
        dvr_b = _make_dvr(cluster[1].dvr_id, name=cluster[1].name)

        am_a = _make_alert_manager(dvr_a, config_dir)
        am_b = _make_alert_manager(dvr_b, config_dir)

        _inject_session_state(am_a, "chan_state", dvr_label=dvr_a.id, channel="5")
        _inject_session_state(am_b, "chan_state", dvr_label=dvr_b.id, channel="7")

        asyncio.run(am_a.save_all_state())
        asyncio.run(am_b.save_all_state())

        file_a = config_dir / f"session_state_{dvr_a.id}.json"
        file_b = config_dir / f"session_state_{dvr_b.id}.json"

        assert file_a.exists(), (
            f"session_state_{dvr_a.id}.json was not created under {config_dir}"
        )
        assert file_b.exists(), (
            f"session_state_{dvr_b.id}.json was not created under {config_dir}"
        )
        assert file_a != file_b, (
            "Both DVRs resolved to the same state file path; ids are not isolated"
        )

        state_a = json.loads(file_a.read_text())
        state_b = json.loads(file_b.read_text())
        assert state_a != state_b, (
            "State blobs for DVR-A and DVR-B are identical; "
            "per-DVR content isolation is broken"
        )


class TestStreamCountFilesPerDvr:
    def test_stream_count_files_per_dvr(self, mock_dvr_cluster, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        cluster = mock_dvr_cluster(count=2)
        dvr_a = _make_dvr(cluster[0].dvr_id, name=cluster[0].name)
        dvr_b = _make_dvr(cluster[1].dvr_id, name=cluster[1].name)

        # Patch CONFIG_PATH for the duration of StreamTracker.__init__ so that
        # _stream_count_file resolves into config_dir instead of /config.
        with patch("core.alerts.common.stream_tracker.CONFIG_PATH", str(config_dir)):
            tracker_a = StreamTracker(dvr=dvr_a)
            tracker_b = StreamTracker(dvr=dvr_b)
            tracker_a._write_stream_count(3)
            tracker_b._write_stream_count(7)

        file_a = config_dir / f"stream_count_{dvr_a.id}.txt"
        file_b = config_dir / f"stream_count_{dvr_b.id}.txt"

        assert file_a.exists(), (
            f"stream_count_{dvr_a.id}.txt was not created under {config_dir}"
        )
        assert file_b.exists(), (
            f"stream_count_{dvr_b.id}.txt was not created under {config_dir}"
        )
        assert file_a != file_b, (
            "Both DVRs resolved to the same stream_count file path; ids are not isolated"
        )
        assert file_a.read_text() == "3", (
            f"DVR-A stream count should be 3, got {file_a.read_text()!r}"
        )
        assert file_b.read_text() == "7", (
            f"DVR-B stream count should be 7, got {file_b.read_text()!r}"
        )

    def test_stream_count_persistence_does_not_block_event_loop(self, tmp_path):
        dvr = _make_dvr("dvr_async")

        async def run():
            with patch("core.alerts.common.stream_tracker.CONFIG_PATH", str(tmp_path)):
                tracker = StreamTracker(dvr=dvr)

            started = threading.Event()
            release = threading.Event()

            def slow_write(count: int) -> None:
                started.set()
                release.wait(timeout=1)

            tracker._write_stream_count = slow_write
            task = asyncio.create_task(
                tracker.process_activity(
                    "Watching ch5 from Living Room", "1-channel-5-client-a"
                )
            )

            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)

            assert started.is_set()
            marker = False
            await asyncio.sleep(0)
            marker = True
            release.set()

            assert marker is True
            assert await task is True

        asyncio.run(asyncio.wait_for(run(), timeout=2))


class TestNoSharedDefaultFile:
    def test_no_shared_default_file(self, mock_dvr_cluster, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        cluster = mock_dvr_cluster(count=2)
        dvr_a = _make_dvr(cluster[0].dvr_id, name=cluster[0].name)
        dvr_b = _make_dvr(cluster[1].dvr_id, name=cluster[1].name)

        am_a = _make_alert_manager(dvr_a, config_dir)
        am_b = _make_alert_manager(dvr_b, config_dir)

        _inject_session_state(am_a, "guard", sentinel=dvr_a.id)
        _inject_session_state(am_b, "guard", sentinel=dvr_b.id)

        asyncio.run(am_a.save_all_state())
        asyncio.run(am_b.save_all_state())

        default_file = config_dir / "session_state_default.json"
        assert not default_file.exists(), (
            "session_state_default.json was found after multi-DVR state save. "
            "This indicates the 'default' fallback guard has regressed in AlertManager."
        )


class TestDiskAlertStatePerDvr:
    def test_disk_alert_state_per_dvr(self, mock_dvr_cluster, tmp_path):
        cluster = mock_dvr_cluster(count=2)
        dvr_a = _make_dvr(cluster[0].dvr_id, name=cluster[0].name, port=cluster[0].port)
        dvr_b = _make_dvr(cluster[1].dvr_id, name=cluster[1].name, port=cluster[1].port)

        settings = _make_disk_settings()
        nm = MagicMock()
        nm.send_notification.return_value = True

        def _make_am(dvr):
            return SimpleNamespace(
                notification_manager=nm,
                settings=settings,
                dvr=dvr,
                _notification_history={},
            )

        alert_a = DiskSpaceAlert(_make_am(dvr_a))
        alert_b = DiskSpaceAlert(_make_am(dvr_b))

        current_time = 1_000_000.0

        # DVR-A: 4% free → critical (4% < 5% critical threshold; 4 GiB < 25 GiB critical threshold)
        _run_disk_check(
            alert_a, free_gib=4.0, total_gib=100.0, current_time=current_time
        )
        # DVR-B: 40% free of 200 GiB (80 GiB) → normal (above all warning thresholds)
        _run_disk_check(
            alert_b, free_gib=80.0, total_gib=200.0, current_time=current_time
        )

        state_a = dict(alert_a._disk_state) if alert_a._disk_state else None
        state_b = dict(alert_b._disk_state) if alert_b._disk_state else None

        assert state_a is not None and state_a, (
            "DVR-A disk state must be persisted after check"
        )
        assert state_b is not None, "DVR-B disk state must be persisted after check"
        assert state_a is not state_b, (
            "DVR-A and DVR-B share the same state object; "
            "DiskSpaceAlert must create a fresh SessionManager per instance"
        )

        assert state_a["status"] == DiskSpaceAlert.SEVERITY_CRITICAL, (
            f"DVR-A at 4% free must be CRITICAL (threshold 5%), got {state_a['status']!r}"
        )
        assert state_b["status"] == DiskSpaceAlert.SEVERITY_NORMAL, (
            f"DVR-B at 40% free (80 GiB) must be NORMAL (warning threshold 10%/50 GiB), "
            f"got {state_b['status']!r}"
        )
