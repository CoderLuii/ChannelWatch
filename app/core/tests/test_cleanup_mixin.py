"""Thread-lifecycle tests for the shared alert cleanup mixin."""

import threading
import time

from core.alerts.common.cleanup_mixin import CleanupMixin


class _ObservedCleanup(CleanupMixin):
    _CLEANUP_POLL_SECONDS = 10.0

    def __init__(self):
        super().__init__()
        self.thread_started = threading.Event()
        self.start_count = 0
        self._start_count_lock = threading.Lock()

    def _auto_cleanup_thread(self) -> None:
        with self._start_count_lock:
            self.start_count += 1
        self.thread_started.set()
        super()._auto_cleanup_thread()


def test_stop_interrupts_wait_and_joins_cleanup_thread():
    cleanup = _ObservedCleanup()
    cleanup.configure_cleanup(interval=3600, auto_cleanup=True)
    assert cleanup.thread_started.wait(timeout=1)
    cleanup_thread = cleanup.cleanup_thread
    assert cleanup_thread is not None and cleanup_thread.is_alive()

    started = time.monotonic()
    cleanup.stop_cleanup(join_timeout=0.5)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert not cleanup_thread.is_alive()
    assert not cleanup.cleanup_running
    assert cleanup._cleanup_stop_event.is_set()


def test_repeated_and_concurrent_starts_create_only_one_thread():
    cleanup = _ObservedCleanup()
    callers = [
        threading.Thread(
            target=cleanup.configure_cleanup,
            kwargs={"interval": 3600, "auto_cleanup": True},
        )
        for _ in range(8)
    ]

    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=1)

    assert cleanup.thread_started.wait(timeout=1)
    first_thread = cleanup.cleanup_thread
    cleanup.configure_cleanup(interval=60, auto_cleanup=True)

    assert cleanup.start_count == 1
    assert cleanup.cleanup_thread is first_thread
    cleanup.stop_cleanup(join_timeout=0.5)


def test_terminal_stop_prevents_thread_resurrection():
    cleanup = _ObservedCleanup()
    cleanup.configure_cleanup(interval=3600, auto_cleanup=True)
    assert cleanup.thread_started.wait(timeout=1)
    stopped_thread = cleanup.cleanup_thread

    cleanup.stop_cleanup(join_timeout=0.5)
    cleanup.configure_cleanup(interval=1, auto_cleanup=True)
    cleanup.start_auto_cleanup(interval=1)

    assert cleanup.cleanup_thread is stopped_thread
    assert stopped_thread is not None and not stopped_thread.is_alive()
    assert cleanup.start_count == 1
    assert not cleanup.cleanup_running


def test_stop_before_start_is_terminal():
    cleanup = _ObservedCleanup()

    cleanup.stop_cleanup(join_timeout=0.01)
    cleanup.configure_cleanup(interval=1, auto_cleanup=True)

    assert cleanup.cleanup_thread is None
    assert cleanup.start_count == 0
    assert not cleanup.cleanup_running


class _BlockingCleanup(_ObservedCleanup):
    _CLEANUP_POLL_SECONDS = 0.01

    def __init__(self):
        super().__init__()
        self.cleanup_entered = threading.Event()
        self.release_cleanup = threading.Event()

    def run_cleanup(self):
        self.cleanup_entered.set()
        self.release_cleanup.wait(timeout=2)
        return super().run_cleanup()


def test_stop_join_is_bounded_when_cleanup_call_is_blocked():
    cleanup = _BlockingCleanup()
    cleanup.configure_cleanup(interval=0, auto_cleanup=True)
    assert cleanup.cleanup_entered.wait(timeout=1)
    cleanup_thread = cleanup.cleanup_thread
    assert cleanup_thread is not None

    started = time.monotonic()
    cleanup.stop_cleanup(join_timeout=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert cleanup_thread.is_alive()
    assert cleanup._cleanup_stop_event.is_set()

    cleanup.configure_cleanup(interval=0, auto_cleanup=True)
    assert cleanup.cleanup_thread is cleanup_thread
    assert cleanup.start_count == 1

    cleanup.release_cleanup.set()
    cleanup_thread.join(timeout=1)
    assert not cleanup_thread.is_alive()
