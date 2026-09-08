"""Operational transitions survive failed publication and process reconstruction."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from core.dvr_health import DvrHealthTracker
from core.alerts.recording_outcomes import RecordingOutcomeTracker


def test_health_transition_replays_after_restart_until_acknowledged(tmp_path):
    clock = [1000.0]
    def tracker():
        return DvrHealthTracker(config_dir=tmp_path, dvr_id='test', process_started_at=0, now=lambda: clock[0])
    first = tracker()
    first.evaluate(healthy=False, delay_seconds=30)
    clock[0] += 30
    pending = first.evaluate(healthy=False, delay_seconds=30)
    restarted = tracker()
    assert restarted.evaluate(healthy=False, delay_seconds=30) == pending
    restarted.acknowledge(pending, notification_armed=True)
    assert restarted.evaluate(healthy=False, delay_seconds=30) is None
    recovery = restarted.evaluate(healthy=True, delay_seconds=30)
    restarted = tracker()
    assert restarted.evaluate(healthy=True, delay_seconds=30) == recovery
    restarted.acknowledge(recovery, notification_armed=False)
    assert restarted.evaluate(healthy=True, delay_seconds=30) is None


def test_recording_transition_replays_after_restart_until_acknowledged(tmp_path):
    def tracker():
        return RecordingOutcomeTracker(config_dir=tmp_path, dvr_id='test', now=lambda: 1000)
    first = tracker()
    pending = first.reconcile([{'id': 'job', 'name': 'Show', 'failed': True}])
    assert len(pending) == 1
    restarted = tracker()
    assert restarted.reconcile([]) == pending
    restarted.acknowledge(pending[0])
    assert tracker().reconcile([]) == []


@pytest.mark.asyncio
async def test_health_storage_rejection_prevents_queue_acceptance(monkeypatch):
    from core import main
    from core.dvr_health import DvrHealthTransition
    manager = MagicMock()
    monkeypatch.setattr(main, 'record_activity', lambda **kwargs: False)
    monkeypatch.setattr(main, '_health_notification_manager_for', lambda *a, **k: manager)
    dvr = SimpleNamespace(id='test', name='Test', overrides={})
    settings = SimpleNamespace(alert_dvr_health=True, dvr_alert_unreachable=True)
    with pytest.raises(RuntimeError):
        await main._emit_dvr_health_transition(dvr, settings, DvrHealthTransition('unreachable', '1'), test_mode=True)
    manager.enqueue_notification.assert_not_called()

@pytest.mark.asyncio
async def test_recording_metadata_storage_and_queue_failures_replay_one_activity(tmp_path, monkeypatch):
    from core.tests.test_recording_event_images import _build_alert
    from core.alerts import recording_events as events
    from core.storage import activity_store
    from unittest.mock import AsyncMock
    import sqlite3
    monkeypatch.setenv('CONFIG_PATH', str(tmp_path))
    alert = _build_alert()
    alert.outcome_tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id='test')
    alert.recording_failed_enabled = True
    alert.alert_formatter.should_send_notification = AsyncMock(return_value=True)
    alert.session_manager.record_notification = AsyncMock()
    alert.send_alert_async = AsyncMock(side_effect=[False, True])
    alert.channel_provider.get_channel_info = MagicMock(side_effect=[OSError('metadata unavailable'), {'name': 'Channel'}, {'name': 'Channel'}, {'name': 'Channel'}])
    pending = alert.outcome_tracker.reconcile([{'id': 'job', 'name': 'Show', 'channels': ['1'], 'failed': True}])[0]
    with pytest.raises(OSError):
        await alert._process_reconciled_outcome(pending)
    assert alert.outcome_tracker.pending_outcomes() == [pending]
    real_record = events.record_recording_event
    monkeypatch.setattr(events, 'record_recording_event', lambda **kwargs: False)
    assert await alert._process_reconciled_outcome(pending) is False
    alert.send_alert_async.assert_not_called()
    monkeypatch.setattr(events, 'record_recording_event', real_record)
    await alert._process_reconciled_outcome(pending)
    assert alert.outcome_tracker.pending_outcomes() == [pending]
    alert.outcome_tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id='test')
    alert._notification_history.clear()
    await alert._process_reconciled_outcome(alert.outcome_tracker.pending_outcomes()[0])
    assert alert.outcome_tracker.pending_outcomes() == []
    with sqlite3.connect(tmp_path / 'channelwatch.db') as connection:
        assert connection.execute('SELECT count(*) FROM activity_event').fetchone()[0] == 1
    assert alert.send_alert_async.await_count == 2
    keys = [call.kwargs['notification_dedupe_key'] for call in alert.send_alert_async.call_args_list]
    assert keys == [pending.event_id, pending.event_id]


def test_acknowledgement_failure_keeps_health_and_recording_pending(tmp_path, monkeypatch):
    health = DvrHealthTracker(config_dir=tmp_path, dvr_id='test', process_started_at=0, now=lambda: 1000)
    health.evaluate(healthy=False, delay_seconds=30)
    health.now = lambda: 1040
    transition = health.evaluate(healthy=False, delay_seconds=30)
    monkeypatch.setattr(health, '_save', MagicMock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        health.acknowledge(transition, notification_armed=True)
    assert health.evaluate(healthy=True, delay_seconds=30) == transition
    recording = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id='test')
    pending = recording.reconcile([{'id': 'job', 'failed': True}])[0]
    monkeypatch.setattr(recording, '_save', MagicMock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        recording.acknowledge(pending)
    assert recording.pending_outcomes() == [pending]
