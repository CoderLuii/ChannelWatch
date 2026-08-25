import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

import core.notification_drain as notification_drain_module
from core.notification_drain import (
    ACK_FILE,
    CORE_NOTIFICATION_DRAIN_REGISTRY,
    CoreNotificationDrainResponder,
    NotificationDrainRegistry,
    release_core_notification_drain,
    request_core_notification_drain,
    reset_notification_drain_state_for_tests,
)
from core.notifications.notification import NotificationManager


class _FakeQueueManager:
    def __init__(self, *, drains: bool = True):
        self.drains = drains
        self.pauses = 0
        self.resumes = 0
        self.waits = 0

    def pause_delivery_queue(self) -> bool:
        self.pauses += 1
        return True

    def resume_delivery_queue(self) -> bool:
        self.resumes += 1
        return True

    def wait_for_delivery_queue(self, timeout: float) -> bool:
        assert timeout > 0
        self.waits += 1
        return self.drains


@pytest.fixture(autouse=True)
def _reset_client_state():
    reset_notification_drain_state_for_tests()
    yield
    reset_notification_drain_state_for_tests()


def test_notification_queue_pause_is_reversible_but_shutdown_is_not():
    manager = NotificationManager()

    assert manager.pause_delivery_queue() is True
    assert manager._queue_accepting is False
    assert manager.resume_delivery_queue() is True
    assert manager._queue_accepting is True

    assert manager.shutdown_delivery_queue(timeout=0.0) is True
    assert manager.resume_delivery_queue() is False
    assert manager._queue_accepting is False


def test_registry_pauses_managers_registered_during_an_active_drain():
    registry = NotificationDrainRegistry()
    first = _FakeQueueManager()
    late = _FakeQueueManager()

    assert registry.begin("a" * 32, [first]) is True
    registry.register(late)
    drained, count = registry.drain("a" * 32, deadline_monotonic=time.monotonic() + 1)

    assert drained is True
    assert count == 2
    assert (first.pauses, late.pauses) == (1, 1)
    assert registry.release("a" * 32) is True
    assert (first.resumes, late.resumes) == (1, 1)


@pytest.mark.asyncio
async def test_cross_process_handshake_holds_until_exact_ui_release(tmp_path: Path):
    registry = NotificationDrainRegistry()
    manager = _FakeQueueManager()
    shutdown = asyncio.Event()
    started = asyncio.Event()
    responder = CoreNotificationDrainResponder(
        config_dir=tmp_path,
        managers_provider=lambda: (manager,),
        registry=registry,
        poll_seconds=0.01,
    )
    task = asyncio.create_task(responder.run(shutdown, started_event=started))
    await started.wait()

    try:
        assert (
            await asyncio.to_thread(
                request_core_notification_drain,
                tmp_path,
                1.0,
                poll_seconds=0.01,
            )
            is True
        )
        assert manager.pauses == 1
        assert manager.waits == 1
        assert manager.resumes == 0
        assert registry.is_active() is True

        ack = json.loads(
            (tmp_path / "channelwatch-runtime" / ACK_FILE).read_text(encoding="utf-8")
        )
        assert ack["status"] == "drained"
        assert set(ack) == {
            "schema",
            "request_id",
            "status",
            "manager_count",
            "deadline_unix",
            "completed_at",
        }

        assert release_core_notification_drain(tmp_path) is True
        for _ in range(50):
            if manager.resumes:
                break
            await asyncio.sleep(0.01)
        assert manager.resumes == 1
        assert registry.is_active() is False
    finally:
        shutdown.set()
        await task


@pytest.mark.asyncio
async def test_drain_lease_renews_for_an_apply_longer_than_acquisition_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lease_renewed = threading.Event()
    original_write_drain_request = notification_drain_module._write_drain_request

    def tracking_write_drain_request(*args, **kwargs):
        original_write_drain_request(*args, **kwargs)
        if threading.current_thread().name == "channelwatch-notification-drain-lease":
            lease_renewed.set()

    monkeypatch.setattr(
        notification_drain_module,
        "_write_drain_request",
        tracking_write_drain_request,
    )

    registry = NotificationDrainRegistry()
    manager = _FakeQueueManager()
    shutdown = asyncio.Event()
    started = asyncio.Event()
    responder = CoreNotificationDrainResponder(
        config_dir=tmp_path,
        managers_provider=lambda: (manager,),
        registry=registry,
        poll_seconds=0.01,
    )
    task = asyncio.create_task(responder.run(shutdown, started_event=started))
    await started.wait()

    try:
        assert await asyncio.to_thread(
            request_core_notification_drain,
            tmp_path,
            0.15,
            poll_seconds=0.01,
            hold_lease_seconds=2.0,
            lease_refresh_seconds=0.03,
        )

        assert await asyncio.to_thread(lease_renewed.wait, 1.0)
        await asyncio.sleep(0.2)
        assert registry.is_active() is True
        assert manager.resumes == 0

        assert release_core_notification_drain(tmp_path) is True
        for _ in range(50):
            if manager.resumes:
                break
            await asyncio.sleep(0.01)
        assert manager.resumes == 1
        assert registry.is_active() is False
    finally:
        shutdown.set()
        await task


@pytest.mark.asyncio
async def test_expired_acquisition_read_rechecks_in_flight_lease_renewal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    responder = CoreNotificationDrainResponder(
        config_dir=tmp_path,
        managers_provider=tuple,
        registry=NotificationDrainRegistry(),
        poll_seconds=0.01,
    )
    request_id = "a" * 32
    expired_deadline = time.time() - 0.01
    renewed_deadline = time.time() + 2.0
    reads = iter(
        (
            {
                "schema": 1,
                "request_id": request_id,
                "deadline_unix": expired_deadline,
            },
            {
                "schema": 1,
                "request_id": request_id,
                "deadline_unix": renewed_deadline,
            },
            None,
        )
    )
    monkeypatch.setattr(
        notification_drain_module,
        "_safe_json_object",
        lambda _path: next(reads),
    )

    await responder._wait_for_release(
        request_id,
        expired_deadline,
        None,
        None,
        asyncio.Event(),
    )

    with pytest.raises(StopIteration):
        next(reads)


@pytest.mark.asyncio
@pytest.mark.parametrize("wall_clock_offset", [-60 * 60, 60 * 60])
async def test_monotonic_lease_is_not_expired_by_a_wall_clock_jump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wall_clock_offset: int,
):
    registry = NotificationDrainRegistry()
    manager = _FakeQueueManager()
    shutdown = asyncio.Event()
    started = asyncio.Event()
    responder = CoreNotificationDrainResponder(
        config_dir=tmp_path,
        managers_provider=lambda: (manager,),
        registry=registry,
        poll_seconds=0.01,
    )
    task = asyncio.create_task(responder.run(shutdown, started_event=started))
    await started.wait()

    try:
        assert await asyncio.to_thread(
            request_core_notification_drain,
            tmp_path,
            0.5,
            poll_seconds=0.01,
            hold_lease_seconds=1.0,
            lease_refresh_seconds=0.05,
        )
        real_time = time.time
        monkeypatch.setattr(
            notification_drain_module.time,
            "time",
            lambda: real_time() + wall_clock_offset,
        )
        await asyncio.sleep(0.2)

        assert registry.is_active() is True
        assert manager.resumes == 0
        assert release_core_notification_drain(tmp_path) is True
    finally:
        shutdown.set()
        await task


@pytest.mark.asyncio
async def test_repeated_lease_sequence_cannot_hold_queues_forever(tmp_path: Path):
    runtime = tmp_path / "channelwatch-runtime"
    runtime.mkdir()
    request_id = "b" * 32
    notification_drain_module._write_drain_request(
        runtime / notification_drain_module.REQUEST_FILE,
        request_id=request_id,
        requested_at="2026-08-25T00:00:00Z",
        deadline_unix=time.time() + 1.0,
        lease_sequence=0,
        lease_seconds=0.1,
    )
    registry = NotificationDrainRegistry()
    manager = _FakeQueueManager()
    shutdown = asyncio.Event()
    started = asyncio.Event()
    responder = CoreNotificationDrainResponder(
        config_dir=tmp_path,
        managers_provider=lambda: (manager,),
        registry=registry,
        poll_seconds=0.01,
    )
    task = asyncio.create_task(responder.run(shutdown, started_event=started))
    await started.wait()

    try:
        for _ in range(50):
            if registry.is_active():
                break
            await asyncio.sleep(0.01)
        assert registry.is_active() is True

        for _ in range(50):
            if not registry.is_active():
                break
            await asyncio.sleep(0.01)
        assert registry.is_active() is False
        assert manager.resumes == 1
    finally:
        shutdown.set()
        await task


@pytest.mark.asyncio
async def test_failed_core_drain_is_rejected_and_queues_resume(tmp_path: Path):
    registry = NotificationDrainRegistry()
    manager = _FakeQueueManager(drains=False)
    shutdown = asyncio.Event()
    started = asyncio.Event()
    responder = CoreNotificationDrainResponder(
        config_dir=tmp_path,
        managers_provider=lambda: (manager,),
        registry=registry,
        poll_seconds=0.01,
    )
    task = asyncio.create_task(responder.run(shutdown, started_event=started))
    await started.wait()

    try:
        assert (
            await asyncio.to_thread(
                request_core_notification_drain,
                tmp_path,
                0.5,
                poll_seconds=0.01,
            )
            is False
        )
        for _ in range(50):
            if manager.resumes:
                break
            await asyncio.sleep(0.01)
        assert manager.resumes == 1
        assert registry.is_active() is False
    finally:
        shutdown.set()
        await task


def test_stale_acknowledgement_cannot_authorize_an_update(tmp_path: Path):
    runtime = tmp_path / "channelwatch-runtime"
    runtime.mkdir()
    (runtime / ACK_FILE).write_text(
        json.dumps(
            {
                "schema": 1,
                "request_id": "f" * 32,
                "status": "drained",
                "deadline_unix": 9999999999.0,
            }
        ),
        encoding="utf-8",
    )

    assert request_core_notification_drain(tmp_path, 0.1, poll_seconds=0.01) is False
    assert release_core_notification_drain(tmp_path) is False


@pytest.mark.parametrize(
    ("lease_sequence", "lease_seconds"),
    (
        (None, 30.0),
        (0, None),
        (True, 30.0),
        (-1, 30.0),
        (0, 0.09),
        (0, notification_drain_module.MAX_HOLD_LEASE_SECONDS + 0.01),
    ),
)
def test_malformed_lease_metadata_is_rejected(
    lease_sequence: object,
    lease_seconds: object,
):
    assert (
        CoreNotificationDrainResponder._validated_request(
            {
                "schema": 1,
                "request_id": "c" * 32,
                "deadline_unix": time.time() + 1.0,
                "lease_sequence": lease_sequence,
                "lease_seconds": lease_seconds,
            }
        )
        is None
    )


def test_global_registry_starts_idle_for_core_runtime_tests():
    assert CORE_NOTIFICATION_DRAIN_REGISTRY.is_active() is False
