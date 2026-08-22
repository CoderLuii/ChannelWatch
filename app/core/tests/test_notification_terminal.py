"""Terminal notification handoff and worker-lifecycle regressions."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.notifications.notification import NotificationManager


@pytest.fixture(autouse=True)
def legacy_all_enabled_routing():
    with patch(
        "core.notifications.notification._load_routing_config",
        return_value={},
    ):
        yield


def _provider(*, side_effect=None, return_value=True):
    provider = MagicMock()
    provider.PROVIDER_TYPE = "Apprise"
    provider.is_configured.return_value = True
    if side_effect is not None:
        provider.send_notification.side_effect = side_effect
    else:
        provider.send_notification.return_value = return_value
    return provider


def _wait_for_worker_exit(manager: NotificationManager, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        worker = manager._delivery_worker
        if worker is None or not worker.is_alive():
            return
        time.sleep(0.005)
    worker = manager._delivery_worker
    assert worker is None or not worker.is_alive()


class _PausedBeforeGetManager(NotificationManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.worker_started = threading.Event()
        self.release_worker = threading.Event()

    def _delivery_worker_loop(self) -> None:
        self.worker_started.set()
        self.release_worker.wait(timeout=2.0)
        super()._delivery_worker_loop()


def test_terminal_notice_survives_immediate_shutdown_with_paused_worker():
    manager = _PausedBeforeGetManager(rate_limit=10, rate_window=60)
    provider = _provider()
    manager.register_provider(provider)

    assert manager.enqueue_terminal_notification(
        "Hot reload failed",
        "Replacement did not become fresh",
        dvr_id="dvr-1",
        event_type="runtime",
    )
    assert manager.worker_started.wait(timeout=1.0)

    assert manager.shutdown_delivery_queue(drain=False, timeout=0.0) is False
    manager.release_worker.set()

    assert manager.wait_for_delivery_queue(timeout=1.0)
    _wait_for_worker_exit(manager)
    provider.send_notification.assert_called_once()
    with manager._delivery_queue.mutex:
        assert manager._delivery_queue.unfinished_tasks == 0
    assert manager._delivery_worker is None
    assert manager._queue_accepting is False


def test_size_one_terminal_handoff_discards_stale_work_and_clears_dedupe():
    manager = _PausedBeforeGetManager(
        rate_limit=10,
        rate_window=60,
        delivery_queue_size=1,
    )
    provider = _provider()
    manager.register_provider(provider)

    assert manager.enqueue_notification(
        "Stale",
        "old monitor work",
        dvr_id="dvr-1",
        event_type="recording",
        notification_dedupe_key="stale-key",
    )
    assert manager.worker_started.wait(timeout=1.0)
    assert manager.enqueue_terminal_notification(
        "Terminal",
        "reload failed",
        dvr_id="dvr-1",
        event_type="runtime",
        notification_dedupe_key="terminal-key",
    )
    assert manager._delivery_queue.qsize() == 1

    manager.release_worker.set()

    assert manager.wait_for_delivery_queue(timeout=1.0)
    _wait_for_worker_exit(manager)
    provider.send_notification.assert_called_once()
    assert provider.send_notification.call_args.args[:2] == (
        "Terminal",
        "reload failed",
    )
    assert manager._queued_dedupe_keys == set()
    with manager._delivery_queue.mutex:
        assert manager._delivery_queue.unfinished_tasks == 0


def test_terminal_handoff_and_teardown_do_not_wait_for_slow_provider():
    provider_entered = threading.Event()
    release_provider = threading.Event()

    def slow_provider(*_args, **_kwargs):
        provider_entered.set()
        release_provider.wait(timeout=2.0)
        return True

    manager = NotificationManager(rate_limit=10, rate_window=60)
    provider = _provider(side_effect=slow_provider)
    manager.register_provider(provider)

    started = time.monotonic()
    assert manager.enqueue_terminal_notification(
        "Terminal",
        "reload failed",
        dvr_id="dvr-1",
        event_type="runtime",
    )
    enqueue_elapsed = time.monotonic() - started
    assert provider_entered.wait(timeout=1.0)

    started = time.monotonic()
    assert manager.shutdown_delivery_queue(drain=False, timeout=0.0) is False
    shutdown_elapsed = time.monotonic() - started

    assert enqueue_elapsed < 0.5
    assert shutdown_elapsed < 0.5
    release_provider.set()
    assert manager.wait_for_delivery_queue(timeout=1.0)
    _wait_for_worker_exit(manager)
    provider.send_notification.assert_called_once()


def test_terminal_worker_contains_system_exit_and_self_exits():
    manager = NotificationManager(rate_limit=10, rate_window=60)
    provider = _provider(side_effect=SystemExit("provider abort"))
    manager.register_provider(provider)

    assert manager.enqueue_terminal_notification(
        "Terminal",
        "reload failed",
        dvr_id="dvr-1",
        event_type="runtime",
    )

    assert manager.wait_for_delivery_queue(timeout=1.0)
    _wait_for_worker_exit(manager)
    provider.send_notification.assert_called_once()
    assert manager._delivery_worker is None
    with manager._delivery_queue.mutex:
        assert manager._delivery_queue.unfinished_tasks == 0


def test_runtime_terminal_notice_has_no_outer_apprise_retry():
    manager = NotificationManager(rate_limit=10, rate_window=60)
    provider = _provider(return_value=False)
    manager.register_provider(provider)

    with patch("core.notifications.delivery.RETRY_DELAYS", [0, 0, 0]):
        assert manager.enqueue_terminal_notification(
            "Terminal",
            "reload failed",
            dvr_id="dvr-1",
            event_type="runtime",
        )
        assert manager.wait_for_delivery_queue(timeout=1.0)

    _wait_for_worker_exit(manager)
    provider.send_notification.assert_called_once()


def test_concurrent_terminal_handoffs_accept_and_attempt_exactly_one():
    manager = _PausedBeforeGetManager(rate_limit=100, rate_window=60)
    provider = _provider()
    manager.register_provider(provider)
    barrier = threading.Barrier(16)
    results: list[bool] = []
    results_lock = threading.Lock()

    def submit(index: int) -> None:
        barrier.wait(timeout=1.0)
        accepted = manager.enqueue_terminal_notification(
            f"Terminal {index}",
            "reload failed",
            dvr_id="dvr-1",
            event_type="runtime",
        )
        with results_lock:
            results.append(accepted)

    callers = [threading.Thread(target=submit, args=(index,)) for index in range(16)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=1.0)

    assert len(results) == 16
    assert results.count(True) == 1
    manager.release_worker.set()
    assert manager.wait_for_delivery_queue(timeout=1.0)
    _wait_for_worker_exit(manager)
    provider.send_notification.assert_called_once()
    with manager._delivery_queue.mutex:
        assert manager._delivery_queue.unfinished_tasks == 0


def test_enqueue_vs_terminal_handoff_stress_has_no_stale_delivery_or_leak():
    for iteration in range(40):
        manager = _PausedBeforeGetManager(
            rate_limit=10,
            rate_window=60,
            delivery_queue_size=1,
        )
        provider = _provider()
        manager.register_provider(provider)
        barrier = threading.Barrier(2)
        results: dict[str, bool] = {}

        def enqueue_stale() -> None:
            barrier.wait(timeout=1.0)
            results["stale"] = manager.enqueue_notification(
                "Stale",
                "old work",
                dvr_id="dvr-1",
                event_type="recording",
            )

        def enqueue_terminal() -> None:
            barrier.wait(timeout=1.0)
            results["terminal"] = manager.enqueue_terminal_notification(
                "Terminal",
                "reload failed",
                dvr_id="dvr-1",
                event_type="runtime",
            )

        stale_thread = threading.Thread(target=enqueue_stale)
        terminal_thread = threading.Thread(target=enqueue_terminal)
        stale_thread.start()
        terminal_thread.start()
        stale_thread.join(timeout=1.0)
        terminal_thread.join(timeout=1.0)

        assert results["terminal"] is True, iteration
        manager.release_worker.set()
        assert manager.wait_for_delivery_queue(timeout=1.0), iteration
        _wait_for_worker_exit(manager)
        provider.send_notification.assert_called_once()
        assert provider.send_notification.call_args.args[0] == "Terminal"
        assert manager._queued_dedupe_keys == set()
        with manager._delivery_queue.mutex:
            assert manager._delivery_queue.unfinished_tasks == 0


def test_terminal_rejection_does_not_claim_remote_delivery_or_close_queue():
    manager = NotificationManager(rate_limit=10, rate_window=60)

    assert not manager.enqueue_terminal_notification(
        "Terminal",
        "reload failed",
        dvr_id="dvr-1",
        event_type="runtime",
    )
    assert manager._queue_accepting is True
    assert manager._delivery_worker is None


def test_rate_limited_terminal_rejection_keeps_normal_shutdown_available():
    limiter = MagicMock()
    limiter.allow.return_value = False
    manager = NotificationManager(rate_limiter=limiter)
    provider = _provider()
    manager.register_provider(provider)

    assert not manager.enqueue_terminal_notification(
        "Terminal",
        "reload failed",
        dvr_id="dvr-1",
        event_type="runtime",
    )

    assert manager._queue_accepting is True
    assert manager.shutdown_delivery_queue(drain=False, timeout=1.0) is True
    limiter.allow.assert_called_once_with()
    provider.send_notification.assert_not_called()
    with manager._delivery_queue.mutex:
        assert manager._delivery_queue.unfinished_tasks == 0
