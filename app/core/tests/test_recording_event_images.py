import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.alerts import recording_events as recording_events_module
from core.alerts.recording_events import RecordingEventsAlert
from core.alerts.recording_outcomes import RecordingOutcome
from core.diagnostics.alerts import recording_events as recording_diagnostics
from core.helpers.job_info import JobInfoProvider


class _Settings:
    stream_count = False
    rd_alert_scheduled = True
    rd_alert_started = True
    rd_alert_completed = True
    rd_alert_cancelled = True
    rd_program_name = True
    rd_program_desc = True
    rd_duration = True
    rd_channel_name = True
    rd_channel_number = True
    rd_type = True
    alert_recording_events = True
    global_rate_limit = 20
    global_rate_window = 300
    tz = "America/Los_Angeles"
    channel_cache_ttl = 86400
    job_cache_ttl = 3600
    rd_template_title = "Channels DVR - Recording Event"
    rd_template_body = "{status}\n{details}"
    rd_template_use_default = True


def _build_alert() -> RecordingEventsAlert:
    notification_manager = MagicMock()
    notification_manager.send_notification.return_value = True
    dvr = SimpleNamespace(
        host="192.168.1.10",
        port=8089,
        id="dvr_test01",
        name="TestDVR",
        base_url="http://192.168.1.10:8089",
    )
    am = SimpleNamespace(
        notification_manager=notification_manager,
        settings=_Settings(),
        dvr=dvr,
        _notification_history={},
        _history_lock=threading.Lock(),
    )
    return RecordingEventsAlert(am)


def test_recording_image_prefers_primary_artwork_then_channel_logo():
    alert = _build_alert()

    channel_info = {"logo_url": "https://example.com/channel-logo.png"}
    recording = {
        "image_url": "",
        "image": "https://example.com/program-image.jpg",
        "icon_url": "https://example.com/program-icon.jpg",
    }

    resolved = alert._resolve_recording_image_url(
        recording=recording, channel_info=channel_info
    )

    assert resolved == "https://example.com/program-image.jpg"
    assert recording["artwork_fallback_exhausted"] is False


def test_recording_image_falls_back_to_channel_logo_when_program_art_missing():
    alert = _build_alert()

    channel_info = {"logo_url": "https://example.com/channel-logo.png"}
    recording = {"image_url": "", "image": "", "icon_url": "", "thumbnail_url": ""}

    resolved = alert._resolve_recording_image_url(
        recording=recording, channel_info=channel_info
    )

    assert resolved == "https://example.com/channel-logo.png"
    assert recording["artwork_fallback_exhausted"] is False


def test_recording_image_returns_empty_when_no_art_exists():
    alert = _build_alert()
    recording = {}

    resolved = alert._resolve_recording_image_url(recording=recording, channel_info={})

    assert resolved == ""
    assert recording["artwork_fallback_exhausted"] is True


@pytest.mark.asyncio
async def test_recording_outcome_state_helpers_preserve_live_event_processing():
    alert = _build_alert()
    persist = MagicMock()

    assert await alert._track_outcome_state(persist, "job-a") is True
    persist.assert_called_once_with("job-a")
    assert alert._outcome_persistence_error is None

    failing_persist = MagicMock(side_effect=OSError("disk full"))
    assert await alert._track_outcome_state(failing_persist, "job-b") is False
    assert alert._outcome_persistence_error == "OSError"

    alert.outcome_tracker.mark_terminal = MagicMock(return_value=True)
    assert await alert._claim_terminal_outcome("job-a", "failed") is True
    assert alert._outcome_persistence_error is None

    alert.outcome_tracker.mark_terminal = MagicMock(return_value=False)
    assert await alert._claim_terminal_outcome("job-a", "completed") is False

    alert.outcome_tracker.mark_terminal = MagicMock(
        side_effect=PermissionError("read-only state")
    )
    assert await alert._claim_terminal_outcome("job-b", "failed") is None
    assert alert._outcome_persistence_error == "PermissionError"


@pytest.mark.asyncio
async def test_reconciled_recording_outcome_records_activity_and_awaits_delivery(
    monkeypatch,
):
    alert = _build_alert()
    alert.recording_failed_enabled = True
    alert.channel_provider.get_channel_info = MagicMock(
        return_value={"name": "Synthetic Channel"}
    )
    alert.alert_formatter.should_send_notification = AsyncMock(return_value=True)
    alert.session_manager.record_notification = AsyncMock()
    alert.send_alert_async = AsyncMock(return_value=True)
    outcome = RecordingOutcome(
        "job-a",
        "failed",
        {
            "name": "Synthetic recording",
            "channel": "7.1",
            "image_url": "https://example.test/image.jpg",
        },
    )

    with patch.object(
        recording_events_module, "record_recording_event", return_value=True
    ) as record_activity:
        assert await alert._process_reconciled_outcome(outcome) is True

    record_activity.assert_called_once()
    assert record_activity.call_args.kwargs["event_type"] == "Failed"
    assert record_activity.call_args.kwargs["channel_name"] == "Synthetic Channel"
    alert.send_alert_async.assert_awaited_once()
    alert.session_manager.record_notification.assert_awaited_once_with(
        "recording-failed-job-a"
    )


@pytest.mark.asyncio
async def test_reconciled_recording_outcome_handles_invalid_disabled_and_cooldown(
    monkeypatch,
):
    alert = _build_alert()
    alert.send_alert_async = AsyncMock(return_value=False)

    assert await alert._process_reconciled_outcome(
        RecordingOutcome("job-a", "unknown", {})
    ) is False

    with patch.object(
        recording_events_module, "record_recording_event", return_value=True
    ):
        assert await alert._process_reconciled_outcome(
            RecordingOutcome("job-a", "missed", {"name": "Synthetic recording"})
        ) is True

        alert.recording_missed_enabled = True
        alert.alert_formatter.should_send_notification = AsyncMock(return_value=False)
        assert await alert._process_reconciled_outcome(
            RecordingOutcome("job-b", "missed", {"name": "Synthetic recording"})
        ) is True

    alert.send_alert_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_recording_outcome_loop_reconciles_then_cancels_cleanly(monkeypatch):
    alert = _build_alert()
    outcome = RecordingOutcome("job-a", "failed", {"name": "Synthetic"})
    alert.job_provider.fetch_jobs_snapshot = MagicMock(return_value=[{"id": "job-a"}])
    alert.job_provider.fetch_recordings_snapshot = MagicMock(return_value=[])
    alert.outcome_tracker.started_jobs_missing = MagicMock(return_value=True)
    alert.outcome_tracker.reconcile = MagicMock(return_value=[outcome])
    alert._process_reconciled_outcome = AsyncMock(return_value=True)
    sleeps = 0

    async def one_cycle(_delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(recording_events_module.asyncio, "sleep", one_cycle)

    with pytest.raises(asyncio.CancelledError):
        await alert._async_recording_outcome_loop()

    alert.job_provider.fetch_jobs_snapshot.assert_called_once_with()
    alert.job_provider.fetch_recordings_snapshot.assert_called_once_with()
    alert.outcome_tracker.reconcile.assert_called_once()
    alert._process_reconciled_outcome.assert_awaited_once_with(outcome)


@pytest.mark.asyncio
async def test_recording_outcome_loop_ignores_unreachable_snapshot(monkeypatch):
    alert = _build_alert()
    alert.job_provider.fetch_jobs_snapshot = MagicMock(return_value=None)
    alert.outcome_tracker.reconcile = MagicMock()
    sleeps = 0

    async def one_cycle(_delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(recording_events_module.asyncio, "sleep", one_cycle)

    with pytest.raises(asyncio.CancelledError):
        await alert._async_recording_outcome_loop()

    alert.outcome_tracker.reconcile.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_offloads_blocking_job_provider_work_from_event_loop():
    alert = _build_alert()
    alert.session_manager.cleanup = AsyncMock()
    alert.active_recordings = {"active-job": {}}
    alert.scheduled_recordings = {"scheduled-job": {"created_at": time.time()}}

    class SlowJobProvider(JobInfoProvider):
        def __init__(self):
            super().__init__(host="192.168.1.10", port=9, cache_ttl=0)

        def is_job_active(self, job_id: str) -> bool:
            time.sleep(0.05)
            return True

        def cache_jobs(self) -> int:
            time.sleep(0.05)
            return 2

    alert.job_provider = SlowJobProvider()

    cleanup_task = asyncio.create_task(alert.cleanup())
    start = time.monotonic()
    await asyncio.sleep(0.01)
    elapsed = time.monotonic() - start
    await cleanup_task

    assert elapsed < 0.04
    assert alert.active_recordings == {"active-job": {}}
    assert "scheduled-job" in alert.scheduled_recordings


@pytest.mark.asyncio
async def test_event_processing_does_not_hold_busy_lock_across_handler_work():
    alert = _build_alert()
    alert.job_provider.get_job_by_id = MagicMock(
        return_value={
            "id": "job-queued",
            "name": "Queued Show",
            "start_time": time.time() + 3600,
            "duration": 1800,
            "channels": [],
            "item": {},
        }
    )
    alert._handle_event_critical = AsyncMock(return_value=True)
    await alert._event_lock.acquire()

    task = asyncio.create_task(
        alert._handle_event("jobs.created", {"Name": "job-queued"})
    )
    await asyncio.sleep(0.1)
    assert not task.done()

    alert._event_lock.release()
    assert await asyncio.wait_for(task, timeout=1.0) is True
    alert._handle_event_critical.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_snapshots_and_deletes_recording_dicts_under_event_lock():
    alert = _build_alert()
    alert.session_manager.cleanup = AsyncMock()
    alert.active_recordings = {"active-stale": {}}
    alert.scheduled_recordings = {
        "scheduled-stale": {"created_at": time.time() - 90000}
    }
    alert.pending_recordings = {
        "pending-stale": {"first_seen": time.time() - 90000, "retry_count": 0}
    }
    alert.job_provider.is_job_active = MagicMock(return_value=False)
    lock_entries = 0
    original_lock = alert._event_lock

    class TrackingLock:
        async def __aenter__(self):
            nonlocal lock_entries
            lock_entries += 1
            await original_lock.acquire()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            original_lock.release()

        async def acquire(self):
            await original_lock.acquire()

        def release(self):
            original_lock.release()

    alert._event_lock = cast(Any, TrackingLock())

    await alert.run_cleanup()

    assert lock_entries >= 3
    assert alert.active_recordings == {}
    assert alert.scheduled_recordings == {}
    assert alert.pending_recordings == {}


@pytest.mark.asyncio
async def test_watchdog_does_not_replace_stuck_event_lock(monkeypatch):
    alert = _build_alert()
    original_lock = alert._event_lock
    alert._last_event_time = time.time() - 1900
    alert._event_counter = 1
    alert._lock_health["last_acquisition"] = time.time() - 1300
    alert._lock_health["acquisition_count"] = 1
    alert._lock_health["release_count"] = 0
    alert.run_cleanup = AsyncMock()

    sleep_calls = 0

    async def fast_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 30:
            raise asyncio.CancelledError

    monkeypatch.setattr(recording_events_module.asyncio, "sleep", fast_sleep)

    with pytest.raises(asyncio.CancelledError):
        await alert._async_watchdog_loop()

    assert alert._event_lock is original_lock
    alert.run_cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_started_handler_offloads_job_channel_and_activity_work(monkeypatch):
    alert = _build_alert()
    alert.recording_started_enabled = False
    job = {
        "id": "job-1",
        "name": "Late Movie",
        "start_time": int(time.time()) - 10,
        "duration": 3600,
        "channels": ["5.1"],
        "item": {"summary": "A late feature."},
    }
    alert.job_provider.get_job_by_id = MagicMock(return_value=job)
    alert.channel_provider.get_channel_info = MagicMock(
        return_value={
            "name": "Movie Channel",
            "logo_url": "https://example.test/logo.png",
        }
    )
    calls = []

    async def run_inline(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(recording_events_module.asyncio, "to_thread", run_inline)

    with patch.object(
        recording_events_module, "record_recording_event", return_value=True
    ) as mock_record:
        result = await alert._handle_recording_started(
            {"Value": "recording-job-1"}, job_details=None
        )

    assert result is False
    assert alert.job_provider.get_job_by_id in calls
    assert alert.channel_provider.get_channel_info in calls
    assert mock_record in calls
    assert mock_record.call_args.kwargs["dvr_id"] == "dvr_test01"
    assert mock_record.call_args.kwargs["dvr_name"] == "TestDVR"


@pytest.mark.asyncio
async def test_completed_handler_uses_pre_fetched_recording_and_offloads_activity(
    monkeypatch,
):
    alert = _build_alert()
    alert.recording_completed_enabled = False
    alert.alert_formatter.should_send_notification = AsyncMock(return_value=True)
    alert.session_manager.record_notification = AsyncMock()
    alert.job_provider.get_recording_by_id = MagicMock(
        side_effect=AssertionError("pre-fetched recording should be used")
    )
    recording = {
        "id": "file-1",
        "processed": True,
        "completed": True,
        "title": "Evening News",
        "duration": 1800,
        "job_id": "job-1",
    }
    calls = []

    async def run_inline(func, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(recording_events_module.asyncio, "to_thread", run_inline)

    with patch.object(
        recording_events_module, "record_recording_event", return_value=True
    ) as mock_record:
        result = await alert._handle_recording_completed(
            {"Value": "recorded-file-1"}, recording_details=recording
        )

    assert result is True
    alert.job_provider.get_recording_by_id.assert_not_called()
    assert mock_record in calls
    assert mock_record.call_args.kwargs["dvr_id"] == "dvr_test01"
    assert mock_record.call_args.kwargs["dvr_name"] == "TestDVR"


@pytest.mark.asyncio
async def test_completed_activity_is_recorded_when_notification_cooldown_blocks_delivery(
    monkeypatch,
):
    alert = _build_alert()
    alert.alert_formatter.should_send_notification = AsyncMock(return_value=False)
    alert._claim_terminal_outcome = AsyncMock(return_value=True)
    alert.send_alert_async = AsyncMock(return_value=True)
    recording = {
        "id": "file-cooldown",
        "processed": True,
        "completed": True,
        "title": "Evening News",
        "duration": 1800,
        "job_id": "job-cooldown",
    }

    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(recording_events_module.asyncio, "to_thread", run_inline)

    with patch.object(
        recording_events_module, "record_recording_event", return_value=True
    ) as mock_record:
        result = await alert._process_completed_recording(
            "file-cooldown", recording, {}
        )

    assert result is True
    mock_record.assert_called_once()
    alert.send_alert_async.assert_not_awaited()


@pytest.mark.parametrize(
    "diagnostic_name",
    [
        "test_recording_scheduled",
        "test_recording_completed",
        "test_recording_stopped",
        "test_recording_cancelled",
    ],
)
@pytest.mark.asyncio
async def test_recording_diagnostics_propagate_delivery_failure(diagnostic_name):
    alert = _build_alert()
    alert.notification_manager.diagnostic_mode = True
    alert.notification_manager.send_notification_async = AsyncMock(return_value=False)
    alert.channel_provider.get_channel_info = MagicMock(
        return_value={
            "name": "Test Channel",
            "logo_url": "https://example.test/logo.png",
        }
    )

    diagnostic = getattr(recording_diagnostics, diagnostic_name)
    with patch.object(
        recording_events_module, "record_recording_event", return_value=True
    ):
        result = await diagnostic(alert)

    assert result is False
    alert.notification_manager.send_notification_async.assert_awaited_once()
