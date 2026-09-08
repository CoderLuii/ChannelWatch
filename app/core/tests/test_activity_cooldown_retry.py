"""A rejected durable write must not consume an activity's cooldown."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from core.helpers import activity_recorder as recorder

CALLS = [
    lambda h: recorder.record_activity('watching_channel', 'TV', 'Watching', notification_history=h),
    lambda h: recorder.record_vod_watching('Movie', notification_history=h),
    lambda h: recorder.record_recording_event('Completed', 'Show', 'Channel', notification_history=h),
    lambda h: recorder.record_disk_status('1 GB', '10 GB', '9 GB', 10, notification_history=h),
]

@pytest.mark.parametrize('record', CALLS)
@pytest.mark.parametrize('failure', [False, OSError('storage unavailable')])
def test_failed_write_retries_then_deduplicates(monkeypatch, record, failure):
    persist = Mock(side_effect=[failure, True])
    monkeypatch.setattr(recorder, 'persist_activity_event', persist)
    history = {}
    assert record(history) is False
    assert history == {}
    assert record(history) is True
    assert record(history) is True
    assert persist.call_count == 2
    assert len(history) == 1

@pytest.mark.parametrize('record', CALLS)
def test_concurrent_caller_retries_failed_winner(monkeypatch, record):
    entered, release = Event(), Event()
    calls = []
    def persist(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            return False
        return True
    monkeypatch.setattr(recorder, 'persist_activity_event', persist)
    history = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(record, history)
        assert entered.wait(5)
        second = pool.submit(record, history)
        release.set()
        assert first.result() is False
        assert second.result() is True
    assert len(calls) == 2
    assert record(history) is True
    assert len(calls) == 2
