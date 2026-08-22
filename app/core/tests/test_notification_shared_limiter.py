import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.helpers import initialize
from core.notifications.notification import NotificationManager
from core.notifications.rate_limiter import RateLimiter


def _settings(limit: int = 2, window: int = 300):
    return SimpleNamespace(
        global_rate_limit=limit,
        global_rate_window=window,
        apprise_pushover="configured",
        apprise_discord="",
        apprise_email="",
        apprise_telegram="",
        apprise_slack="",
        apprise_gotify="",
        apprise_matrix="",
        apprise_custom="",
        webhooks=[],
    )


def _build_manager(settings, *, installation_limit=None, installation_window=None):
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Apprise"
    provider.initialize.return_value = True
    provider.is_configured.return_value = True
    with (
        patch("core.notifications.providers.apprise.AppriseProvider", return_value=provider),
        patch("core.helpers.initialize.WebhookManager") as webhook_manager,
        patch(
            "core.notifications.providers.plugin_loader.load_notification_plugins",
            return_value=[],
        ),
    ):
        webhook_manager.return_value.is_configured.return_value = False
        return initialize.initialize_notifications(
            settings,
            test_mode=True,
            installation_rate_limit=installation_limit,
            installation_rate_window=installation_window,
        )


def test_per_dvr_managers_share_limiter_for_same_effective_settings():
    initialize._shared_rate_limiter = None
    first = _build_manager(_settings())
    second = _build_manager(_settings())

    assert first is not None and second is not None
    assert first.rate_limiter is second.rate_limiter
    assert first.rate_limiter.max_notifications == 2
    assert first.rate_limiter.window_seconds == 300
    assert first.rate_limiter.allow()
    assert second.rate_limiter.allow()
    assert not first.rate_limiter.allow()


def test_shared_limiter_factory_is_thread_safe():
    initialize._shared_rate_limiter = None
    with ThreadPoolExecutor(max_workers=8) as executor:
        limiters = list(
            executor.map(lambda _: initialize._get_shared_rate_limiter(7, 60), range(32))
        )

    assert len({id(limiter) for limiter in limiters}) == 1


def test_differing_dvr_overrides_use_one_authoritative_installation_limiter():
    initialize._shared_rate_limiter = None
    first = _build_manager(
        _settings(limit=2, window=300),
        installation_limit=5,
        installation_window=120,
    )
    second = _build_manager(
        _settings(limit=99, window=999),
        installation_limit=5,
        installation_window=120,
    )

    assert first is not None and second is not None
    assert first.rate_limiter is second.rate_limiter
    assert first.rate_limiter.max_notifications == 5
    assert first.rate_limiter.window_seconds == 120


def test_authoritative_limit_reconfigures_same_instance_without_resetting_usage():
    initialize._shared_rate_limiter = None
    limiter = initialize._get_shared_rate_limiter(2, 300)
    assert limiter.allow()
    assert limiter.allow()

    reconfigured = initialize._get_shared_rate_limiter(1, 300)

    assert reconfigured is limiter
    assert reconfigured.max_notifications == 1
    assert not reconfigured.allow()


def test_notification_manager_preserves_explicit_limiter_injection():
    limiter = RateLimiter(max_notifications=3, window_seconds=45)
    manager = NotificationManager(
        rate_limit=999, rate_window=999, rate_limiter=limiter
    )
    assert manager.rate_limiter is limiter


def test_second_async_delivery_is_ingested_while_first_provider_blocks():
    manager = NotificationManager(rate_limit=10, rate_window=60)
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Console"
    provider.is_configured.return_value = True
    entered = threading.Event()
    release = threading.Event()

    def blocking_send(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=2)
        return True

    provider.send_notification.side_effect = blocking_send
    manager.register_provider(provider)

    async def exercise():
        first = await manager.send_notification_async(
            "First", "Message", dvr_id="dvr1", event_type="channel"
        )
        await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1)
        second = await manager.send_notification_async(
            "Second", "Message", dvr_id="dvr1", event_type="channel"
        )
        assert first is True and second is True
        assert provider.send_notification.call_count == 1
        release.set()
        assert await asyncio.to_thread(manager.wait_for_delivery_queue, 1.0)
        assert provider.send_notification.call_count == 2

    asyncio.run(exercise())


def test_sse_event_monitor_path_enqueues_without_waiting_for_provider():
    from core.alerts.base import BaseAlert
    from core.engine.event_monitor import EventMonitor

    manager = NotificationManager(rate_limit=10, rate_window=60)
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Console"
    provider.is_configured.return_value = True
    entered = threading.Event()
    release = threading.Event()

    def blocking_send(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=2)
        return True

    provider.send_notification.side_effect = blocking_send
    manager.register_provider(provider)

    class EventAlert(BaseAlert):
        ROUTING_EVENT_TYPE = "channel"

        def _should_handle_event(self, *_args, **_kwargs):
            return True

        async def _handle_event(self, *_args, **_kwargs):
            return self.send_alert("Event", "Message")

    alert = EventAlert(manager)

    class EventAlertManager:
        async def process_event(self, event_type, event_data):
            return await alert.process_event(event_type, event_data)

    monitor = EventMonitor(
        host="127.0.0.1",
        port=8089,
        alert_manager=EventAlertManager(),
    )

    async def exercise():
        await monitor._process_event_line('{"Type":"activities.set","Value":"one"}')
        await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1)
        await monitor._process_event_line('{"Type":"activities.set","Value":"two"}')
        assert provider.send_notification.call_count == 1
        assert monitor.stats["total_events"] == 2
        assert monitor.stats["alert_events"] == 2
        release.set()
        assert await asyncio.to_thread(manager.wait_for_delivery_queue, 1.0)
        assert provider.send_notification.call_count == 2

    asyncio.run(exercise())


def test_hot_reload_failure_notification_does_not_wait_for_provider():
    from core.main import _notify_hot_reload_failure

    manager = NotificationManager(rate_limit=10, rate_window=60)
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Console"
    provider.is_configured.return_value = True
    entered = threading.Event()
    release = threading.Event()

    def blocking_send(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=2)
        return True

    provider.send_notification.side_effect = blocking_send
    manager.register_provider(provider)
    monitor = SimpleNamespace(
        dvr_name="Test DVR",
        dvr=SimpleNamespace(id="dvr-1", name="Test DVR"),
        alert_manager=SimpleNamespace(notification_manager=manager),
    )

    async def exercise():
        accepted = await asyncio.wait_for(
            _notify_hot_reload_failure(monitor, "test failure"), timeout=0.2
        )
        assert accepted is True
        await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1)
        # Monitor teardown observes the accepted terminal handoff; it cannot
        # drain the notice and never waits for the blocked provider.
        assert manager.shutdown_delivery_queue(drain=False, timeout=0.0) is False
        release.set()
        assert await asyncio.to_thread(manager.wait_for_delivery_queue, 1.0)
        deadline = time.monotonic() + 1.0
        while manager._delivery_worker is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert manager._delivery_worker is None
        provider.send_notification.assert_called_once()

    asyncio.run(exercise())


def test_queue_acceptance_and_worker_start_are_atomic_against_shutdown():
    manager = NotificationManager(
        rate_limit=10,
        rate_window=60,
        delivery_queue_size=2,
    )
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Console"
    provider.is_configured.return_value = True
    provider.send_notification.return_value = True
    manager.register_provider(provider)

    entered_worker_start = threading.Event()
    release_worker_start = threading.Event()
    original_start = manager._ensure_delivery_worker_locked

    def paused_start():
        entered_worker_start.set()
        release_worker_start.wait(timeout=2)
        return original_start()

    accepted: list[bool] = []
    shutdown_result: list[bool] = []
    with patch.object(manager, "_ensure_delivery_worker_locked", paused_start):
        enqueue_thread = threading.Thread(
            target=lambda: accepted.append(
                manager.enqueue_notification(
                    "Atomic", "Message", dvr_id="dvr1", event_type="channel"
                )
            )
        )
        enqueue_thread.start()
        assert entered_worker_start.wait(timeout=1)

        shutdown_thread = threading.Thread(
            target=lambda: shutdown_result.append(
                manager.shutdown_delivery_queue(drain=False, timeout=1.0)
            )
        )
        shutdown_thread.start()
        assert shutdown_thread.is_alive()
        release_worker_start.set()
        enqueue_thread.join(timeout=1)
        shutdown_thread.join(timeout=2)

    assert accepted == [True]
    assert shutdown_result == [True]
    assert manager.wait_for_delivery_queue(0.1)


def test_queue_dedupes_explicit_keys_drops_newest_on_overflow_and_discards_on_reload():
    manager = NotificationManager(
        rate_limit=3,
        rate_window=60,
        delivery_queue_size=1,
    )
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Console"
    provider.is_configured.return_value = True
    entered = threading.Event()
    release = threading.Event()

    def blocking_send(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=2)
        return True

    provider.send_notification.side_effect = blocking_send
    manager.register_provider(provider)

    assert manager.enqueue_notification(
        "First",
        "Message",
        dvr_id="dvr1",
        event_type="recording",
        activity_event_id="event-1",
    )
    assert entered.wait(timeout=1)
    assert manager.enqueue_notification(
        "Second",
        "Message",
        dvr_id="dvr1",
        event_type="recording",
        activity_event_id="event-2",
    )
    assert manager.enqueue_notification(
        "Second duplicate",
        "Message",
        dvr_id="dvr1",
        event_type="recording",
        activity_event_id="event-2",
    )
    assert not manager.enqueue_notification(
        "Overflow",
        "Message",
        dvr_id="dvr1",
        event_type="recording",
        activity_event_id="event-3",
    )
    assert manager.delivery_queue_dropped == 1
    assert manager.rate_limiter.allow(), "overflow must not consume a rate token"

    assert not manager.shutdown_delivery_queue(drain=False, timeout=0.01)
    repeated_result: list[bool] = []
    repeated_shutdown = threading.Thread(
        target=lambda: repeated_result.append(
            manager.shutdown_delivery_queue(drain=False, timeout=1.0)
        )
    )
    repeated_shutdown.start()
    assert repeated_shutdown.is_alive()
    release.set()
    repeated_shutdown.join(timeout=2)
    assert repeated_result == [True]
    assert manager.wait_for_delivery_queue(0.1)
    assert provider.send_notification.call_count == 1


def test_queue_worker_start_failure_rejects_without_consuming_rate_limit():
    manager = NotificationManager(rate_limit=1, rate_window=60)
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Console"
    provider.is_configured.return_value = True
    manager.register_provider(provider)

    with patch.object(threading.Thread, "start", side_effect=RuntimeError("no thread")):
        assert not manager.enqueue_notification(
            "Rejected", "Message", dvr_id="dvr1", event_type="channel"
        )

    assert manager.rate_limiter.allow()
    assert manager.wait_for_delivery_queue(0.1)
    assert manager._delivery_worker is None


def test_queue_worker_survives_one_unexpected_delivery_error():
    class FlakyProvider:
        PROVIDER_TYPE = "Apprise"

        def __init__(self):
            self.enumerations = 0
            self.deliveries = 0

        def is_configured(self):
            return True

        def notification_destinations(self, _allowed):
            self.enumerations += 1
            if self.enumerations == 1:
                raise RuntimeError("unexpected enumeration failure")
            return [("custom", "custom")]

        def send_notification(self, *_args, **_kwargs):
            self.deliveries += 1
            return True

    manager = NotificationManager(rate_limit=10, rate_window=60)
    provider = FlakyProvider()
    manager.register_provider(provider)

    assert manager.enqueue_notification(
        "First", "Message", dvr_id="dvr1", event_type="channel"
    )
    assert manager.enqueue_notification(
        "Second", "Message", dvr_id="dvr1", event_type="channel"
    )
    assert manager.wait_for_delivery_queue(1.0)
    assert provider.deliveries == 1
    assert manager.shutdown_delivery_queue(timeout=1.0)
