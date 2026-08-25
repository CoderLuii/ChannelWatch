import asyncio
from types import SimpleNamespace
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest

from core.diagnostics import run_test
from core.diagnostics.alerts.recording_events import (
    test_recording_events_alert as run_recording_events_diagnostic,
    test_recording_scheduled_alert as run_recording_scheduled_diagnostic,
)
from core.notifications.notification import NotificationManager
from core.notifications.providers.base import NotificationProvider


@pytest.fixture(autouse=True)
def _legacy_unconfigured_notification_routing():
    """Keep diagnostic delivery isolated from any host `/config` state."""

    with patch(
        "core.notifications.notification._load_routing_config", return_value={}
    ):
        yield


class _ConfiguredProvider(NotificationProvider):
    PROVIDER_TYPE = "DiagnosticFixture"

    def __init__(self, outcome=True):
        self.outcome = outcome
        self.deliveries = []

    def initialize(self, **kwargs):
        return True

    def is_configured(self):
        return True

    def send_notification(self, title, message, **kwargs):
        self.deliveries.append((title, message, kwargs))
        return self.outcome


class _ConfiguredWebhook:
    def __init__(self, outcome=True):
        self.outcome = outcome
        self.deliveries = []

    def is_configured(self):
        return True

    def send_notification(self, title, message, **kwargs):
        self.deliveries.append((title, message, kwargs))
        return self.outcome


@pytest.mark.asyncio
async def test_channel_diagnostic_awaits_event_processing():
    alert = object()
    manager = SimpleNamespace(
        alert_instances={"Channel-Watching": alert},
        notification_manager=SimpleNamespace(
            get_active_providers=lambda: ["fixture"],
            has_configured_destinations=lambda: True,
        ),
        process_event=AsyncMock(return_value="Channel-Watching"),
    )

    assert await run_test("Channel-Watching", "fixture.invalid", 8089, manager)
    manager.process_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_channel_diagnostic_fails_when_awaited_handler_fails():
    manager = SimpleNamespace(
        alert_instances={"Channel-Watching": object()},
        notification_manager=SimpleNamespace(
            get_active_providers=lambda: ["fixture"],
            has_configured_destinations=lambda: True,
        ),
        process_event=AsyncMock(return_value=None),
    )

    assert not await run_test("Channel-Watching", "fixture.invalid", 8089, manager)


@pytest.mark.asyncio
async def test_channel_diagnostic_metadata_warmup_does_not_block_event_loop():
    release = threading.Event()

    def blocking_cache():
        if not release.wait(0.5):
            raise AssertionError("channel metadata warm-up blocked the event loop")

    alert = SimpleNamespace(
        _cache_channels=blocking_cache,
        channel_provider=SimpleNamespace(
            channel_cache={"7": {"name": "fixture", "logo_url": "fixture"}},
            get_channel_info=lambda number: {"name": number, "logo_url": "fixture"},
        ),
        program_provider=SimpleNamespace(programs_cache={}),
    )
    manager = SimpleNamespace(
        alert_instances={"Channel-Watching": alert},
        notification_manager=SimpleNamespace(
            has_configured_destinations=lambda: True,
        ),
        process_event=AsyncMock(return_value=True),
    )
    asyncio.get_running_loop().call_later(0.01, release.set)

    assert await run_test("Channel-Watching", "fixture.invalid", 8089, manager)


@pytest.mark.asyncio
async def test_vod_diagnostic_metadata_warmup_does_not_block_event_loop():
    release = threading.Event()

    def blocking_cache():
        if not release.wait(0.5):
            raise AssertionError("VOD metadata warm-up blocked the event loop")

    alert = SimpleNamespace(
        _cache_vod_metadata=blocking_cache,
        vod_provider=SimpleNamespace(
            metadata_cache={},
            get_metadata=lambda _file_id: {"title": "fixture", "image_url": "fixture"},
        ),
    )
    manager = SimpleNamespace(
        alert_instances={"VOD-Watching": alert},
        notification_manager=SimpleNamespace(
            has_configured_destinations=lambda: True,
        ),
        process_event=AsyncMock(return_value=True),
    )
    asyncio.get_running_loop().call_later(0.01, release.set)

    assert await run_test("VOD-Watching", "fixture.invalid", 8089, manager)


@pytest.mark.asyncio
async def test_recording_diagnostic_awaits_delivery_result():
    recording_alert = SimpleNamespace(
        job_provider=SimpleNamespace(_jobs_cache={}),
        _handle_recording_created=AsyncMock(return_value=True),
    )
    manager = SimpleNamespace(
        alert_instances={"Recording-Events": recording_alert},
        notification_manager=SimpleNamespace(
            get_active_providers=lambda: ["fixture"],
            has_configured_destinations=lambda: True,
        ),
    )

    assert await run_recording_scheduled_diagnostic(
        "fixture.invalid", 8089, manager
    )
    recording_alert._handle_recording_created.assert_awaited_once()

    recording_alert._handle_recording_created.return_value = False
    assert not await run_recording_scheduled_diagnostic(
        "fixture.invalid", 8089, manager
    )


@pytest.mark.asyncio
async def test_recording_diagnostic_metadata_warmup_does_not_block_event_loop():
    release = threading.Event()

    def blocking_cache():
        if not release.wait(0.5):
            raise AssertionError("recording metadata warm-up blocked the event loop")

    recording_alert = SimpleNamespace(
        _cache_channels=blocking_cache,
        channel_provider=SimpleNamespace(channel_cache={}),
        job_provider=SimpleNamespace(_jobs_cache={}),
        _handle_recording_created=AsyncMock(return_value=True),
    )
    manager = SimpleNamespace(
        alert_instances={"Recording-Events": recording_alert},
        notification_manager=SimpleNamespace(
            has_configured_destinations=lambda: True,
        ),
    )
    asyncio.get_running_loop().call_later(0.01, release.set)

    assert await run_recording_scheduled_diagnostic(
        "fixture.invalid", 8089, manager
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [True, False])
async def test_diagnostic_mode_returns_actual_provider_outcome(outcome):
    provider = _ConfiguredProvider(outcome=outcome)
    manager = NotificationManager(diagnostic_mode=True)
    assert manager.register_provider(provider)

    result = await manager.send_notification_async(
        "ChannelWatch diagnostic",
        "fixture",
        dvr_id="dvr_fixture",
        event_type="channel",
    )

    assert result is outcome
    assert len(provider.deliveries) == 1
    assert provider.deliveries[0][2]["_diagnostic_deadline_monotonic"] > time.monotonic()
    assert manager._delivery_worker is None


def test_synchronous_diagnostic_delivery_receives_the_same_deadline():
    provider = _ConfiguredProvider(outcome=True)
    manager = NotificationManager(diagnostic_mode=True)
    assert manager.register_provider(provider)

    assert manager.send_notification(
        "Disk diagnostic",
        "fixture",
        dvr_id="dvr_fixture",
        event_type="disk",
    )

    assert len(provider.deliveries) == 1
    assert provider.deliveries[0][2]["_diagnostic_deadline_monotonic"] > time.monotonic()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [True, False])
async def test_diagnostic_mode_returns_actual_webhook_outcome(outcome):
    webhook = _ConfiguredWebhook(outcome=outcome)
    manager = NotificationManager(diagnostic_mode=True)
    manager.register_webhook_manager(webhook)

    result = await manager.send_notification_async(
        "ChannelWatch diagnostic",
        "fixture",
        dvr_id="dvr_fixture",
        event_type="channel",
    )

    assert result is outcome
    assert len(webhook.deliveries) == 1
    assert manager._delivery_worker is None


@pytest.mark.asyncio
async def test_diagnostic_deadline_stops_normal_recording_retry_schedule():
    class _SlowFailure(_ConfiguredProvider):
        def send_notification(self, title, message, **kwargs):
            self.deliveries.append((title, message, kwargs))
            time.sleep(0.02)
            return False

    provider = _SlowFailure()
    manager = NotificationManager(
        diagnostic_mode=True,
        diagnostic_deadline_seconds=0.05,
    )
    assert manager.register_provider(provider)

    started = time.monotonic()
    result = await manager.send_notification_async(
        "Recording diagnostic",
        "fixture",
        dvr_id="dvr_fixture",
        event_type="recording",
    )

    assert result is False
    assert time.monotonic() - started < 0.2
    assert len(provider.deliveries) == 1


@pytest.mark.asyncio
async def test_diagnostic_deadline_returns_failure_and_drains_slow_worker():
    manager = NotificationManager(
        diagnostic_mode=True,
        diagnostic_deadline_seconds=0.01,
    )
    worker_finished = threading.Event()

    def slow_delivery(*_args, **_kwargs):
        time.sleep(0.3)
        worker_finished.set()
        return True

    manager.send_notification = slow_delivery
    result = await manager.send_notification_async("Diagnostic", "fixture")
    assert result is False

    # The thread is bounded by the provider call rather than retained by a
    # queue worker. Give it time to finish before the test process exits.
    assert await asyncio.to_thread(worker_finished.wait, 0.2)


@pytest.mark.asyncio
async def test_recording_aggregate_accepts_webhook_only_and_delivers_exactly_five():
    webhook = _ConfiguredWebhook()
    manager = NotificationManager(diagnostic_mode=True)
    manager.register_webhook_manager(webhook)

    async def deliver(*_args):
        return await manager.send_notification_async(
            "[TEST] Recording event",
            "fixture",
            dvr_id="dvr_fixture",
            event_type="recording",
        )

    recording_alert = SimpleNamespace(
        job_provider=SimpleNamespace(
            _jobs_cache={},
            get_recording_by_id=lambda _file_id: None,
        ),
        channel_provider=SimpleNamespace(channel_cache={}),
        scheduled_recordings={},
        _handle_recording_created=AsyncMock(side_effect=deliver),
        _handle_recording_started=AsyncMock(side_effect=deliver),
        _handle_recording_completed=AsyncMock(side_effect=deliver),
        _handle_recording_deleted=AsyncMock(side_effect=deliver),
    )
    alert_manager = SimpleNamespace(
        alert_instances={"Recording-Events": recording_alert},
        notification_manager=manager,
    )

    assert await run_recording_events_diagnostic(
        "fixture.invalid", 8089, alert_manager
    )
    assert len(webhook.deliveries) == 5
    assert recording_alert._handle_recording_created.await_count == 1
    assert recording_alert._handle_recording_started.await_count == 1
    assert recording_alert._handle_recording_completed.await_count == 2
    assert recording_alert._handle_recording_deleted.await_count == 1
