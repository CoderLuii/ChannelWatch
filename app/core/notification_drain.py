"""Bounded cross-process notification drain coordination for app updates.

The UI process owns the automatic-update scheduler while the core process owns
the per-DVR delivery queues.  This module provides a small, local-only control
protocol under ``/config/channelwatch-runtime``.  Requests and acknowledgements
contain only random IDs, deadlines, outcome codes, and manager counts; no DVR or
notification data crosses the process boundary.
"""

from __future__ import annotations

import asyncio
import json
import math
import stat
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.helpers.atomic_io import atomic_write_json, fsync_directory

REQUEST_SCHEMA = 1
ACK_SCHEMA = 1
REQUEST_FILE = "notification-drain-request.json"
ACK_FILE = "notification-drain-ack.json"
MAX_CONTROL_FILE_BYTES = 16 * 1024
DEFAULT_POLL_SECONDS = 0.05
DEFAULT_HOLD_LEASE_SECONDS = 30.0
DEFAULT_LEASE_REFRESH_SECONDS = 5.0
LEASE_RENEWAL_HANDOFF_GRACE_SECONDS = 1.0
MAX_HOLD_LEASE_SECONDS = 240.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_json_object(path: Path) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_CONTROL_FILE_BYTES
    ):
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _runtime_paths(config_dir: Path) -> tuple[Path, Path, Path]:
    runtime_dir = Path(config_dir) / "channelwatch-runtime"
    return runtime_dir, runtime_dir / REQUEST_FILE, runtime_dir / ACK_FILE


def _request_matches(path: Path, request_id: str) -> bool:
    value = _safe_json_object(path)
    return bool(
        value
        and value.get("schema") == REQUEST_SCHEMA
        and value.get("request_id") == request_id
    )


class NotificationDrainRegistry:
    """Track core-owned queues and hold them during one update transaction."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._managers: weakref.WeakSet[Any] = weakref.WeakSet()
        self._active_request_id: str | None = None
        self._paused: weakref.WeakKeyDictionary[Any, bool] = weakref.WeakKeyDictionary()

    def register(self, manager: Any) -> None:
        pause = getattr(type(manager), "pause_delivery_queue", None)
        with self._lock:
            self._managers.add(manager)
            if self._active_request_id and manager not in self._paused:
                self._paused[manager] = bool(callable(pause) and pause(manager))

    def unregister(self, manager: Any) -> None:
        with self._lock:
            self._managers.discard(manager)
            self._paused.pop(manager, None)

    def begin(self, request_id: str, managers: Iterable[Any]) -> bool:
        with self._lock:
            if self._active_request_id not in {None, request_id}:
                return False
            self._active_request_id = request_id
            for manager in managers:
                self._managers.add(manager)
            for manager in tuple(self._managers):
                if manager in self._paused:
                    continue
                pause = getattr(type(manager), "pause_delivery_queue", None)
                self._paused[manager] = bool(callable(pause) and pause(manager))
            return True

    def drain(self, request_id: str, *, deadline_monotonic: float) -> tuple[bool, int]:
        """Drain the stable paused set while newly registered queues join held."""

        checked: weakref.WeakSet[Any] = weakref.WeakSet()
        while True:
            with self._lock:
                if self._active_request_id != request_id:
                    return False, len(checked)
                pending = [
                    manager
                    for manager in tuple(self._managers)
                    if manager not in checked
                ]
            if not pending:
                # Registration takes the same lock. Observing an empty set
                # while the request remains active establishes the complete
                # paused generation at this instant; later registrations are
                # paused before they can accept event work.
                return True, len(checked)
            for manager in pending:
                remaining = deadline_monotonic - time.monotonic()
                if remaining <= 0:
                    return False, len(checked)
                waiter = getattr(type(manager), "wait_for_delivery_queue", None)
                if callable(waiter) and not bool(waiter(manager, timeout=remaining)):
                    return False, len(checked)
                checked.add(manager)

    def release(self, request_id: str) -> bool:
        with self._lock:
            if self._active_request_id != request_id:
                return False
            paused = list(self._paused.items())
            self._paused.clear()
            self._active_request_id = None
        for manager, pause_was_acquired in paused:
            if not pause_was_acquired:
                continue
            resume = getattr(type(manager), "resume_delivery_queue", None)
            if callable(resume):
                try:
                    resume(manager)
                except (OSError, RuntimeError):
                    # A bounded lease below also protects the core. Queue
                    # shutdown owns the permanent fail-closed state.
                    pass
        return True

    def is_active(self, request_id: str | None = None) -> bool:
        with self._lock:
            return bool(
                self._active_request_id
                and (request_id is None or self._active_request_id == request_id)
            )


CORE_NOTIFICATION_DRAIN_REGISTRY = NotificationDrainRegistry()


def register_notification_manager(manager: Any) -> None:
    CORE_NOTIFICATION_DRAIN_REGISTRY.register(manager)


def unregister_notification_manager(manager: Any) -> None:
    CORE_NOTIFICATION_DRAIN_REGISTRY.unregister(manager)


class CoreNotificationDrainResponder:
    """Core-side request watcher and bounded queue-hold owner."""

    def __init__(
        self,
        *,
        config_dir: Path,
        managers_provider: Callable[[], Iterable[Any]],
        registry: NotificationDrainRegistry = CORE_NOTIFICATION_DRAIN_REGISTRY,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        runtime_dir, request_path, ack_path = _runtime_paths(config_dir)
        self.runtime_dir = runtime_dir
        self.request_path = request_path
        self.ack_path = ack_path
        self.managers_provider = managers_provider
        self.registry = registry
        self.poll_seconds = max(0.01, float(poll_seconds))

    @staticmethod
    def _validated_request(
        raw: Any,
    ) -> tuple[str, float, int | None, float | None] | None:
        if not isinstance(raw, dict) or raw.get("schema") != REQUEST_SCHEMA:
            return None
        request_id = str(raw.get("request_id") or "")
        deadline = raw.get("deadline_unix")
        if (
            len(request_id) != 32
            or any(char not in "0123456789abcdef" for char in request_id)
            or type(deadline) not in {int, float}
        ):
            return None
        deadline_value = float(deadline)
        if not math.isfinite(deadline_value) or deadline_value <= 0:
            return None
        lease_sequence = raw.get("lease_sequence")
        lease_seconds = raw.get("lease_seconds")
        if lease_sequence is None and lease_seconds is None:
            # v0.9.18 development candidates briefly wrote wall-clock-only
            # leases. Retain bounded compatibility for an in-flight file.
            return request_id, deadline_value, None, None
        if (
            type(lease_sequence) is not int
            or lease_sequence < 0
            or type(lease_seconds) not in {int, float}
        ):
            return None
        lease_seconds_value = float(lease_seconds)
        if not 0.1 <= lease_seconds_value <= MAX_HOLD_LEASE_SECONDS:
            return None
        return (
            request_id,
            deadline_value,
            lease_sequence,
            lease_seconds_value,
        )

    async def _wait_for_legacy_release(
        self,
        request_id: str,
        deadline_unix: float,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Honor one bounded wall-clock lease from an earlier candidate."""

        current_deadline = deadline_unix
        while not shutdown_event.is_set():
            raw = await asyncio.to_thread(_safe_json_object, self.request_path)
            validated = self._validated_request(raw)
            if validated is None or validated[0] != request_id:
                break
            if validated[2] is not None:
                return
            current_deadline = max(current_deadline, validated[1])
            if time.time() >= current_deadline:
                grace_deadline = (
                    time.monotonic() + LEASE_RENEWAL_HANDOFF_GRACE_SECONDS
                )
                renewed = False
                while (
                    not shutdown_event.is_set()
                    and time.monotonic() < grace_deadline
                ):
                    remaining_grace = grace_deadline - time.monotonic()
                    try:
                        await asyncio.wait_for(
                            shutdown_event.wait(),
                            timeout=min(self.poll_seconds, remaining_grace),
                        )
                    except TimeoutError:
                        pass
                    refreshed_raw = await asyncio.to_thread(
                        _safe_json_object, self.request_path
                    )
                    refreshed = self._validated_request(refreshed_raw)
                    if refreshed is None or refreshed[0] != request_id:
                        return
                    if refreshed[2] is not None:
                        return
                    if refreshed[1] > current_deadline:
                        current_deadline = refreshed[1]
                        renewed = True
                        break
                if not renewed:
                    break
                continue
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    def _write_ack(
        self,
        *,
        request_id: str,
        status: str,
        manager_count: int,
        deadline_unix: float,
    ) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self.ack_path,
            {
                "schema": ACK_SCHEMA,
                "request_id": request_id,
                "status": status,
                "manager_count": max(0, int(manager_count)),
                "deadline_unix": deadline_unix,
                "completed_at": _utc_now(),
            },
            sort_keys=True,
        )

    async def _wait_for_release(
        self,
        request_id: str,
        deadline_unix: float,
        lease_sequence: int | None,
        lease_seconds: float | None,
        shutdown_event: asyncio.Event,
    ) -> None:
        if lease_sequence is None or lease_seconds is None:
            await self._wait_for_legacy_release(
                request_id,
                deadline_unix,
                shutdown_event,
            )
            return

        current_sequence = lease_sequence
        expected_lease_seconds = lease_seconds
        lease_deadline_monotonic = time.monotonic() + expected_lease_seconds
        while not shutdown_event.is_set():
            raw = await asyncio.to_thread(_safe_json_object, self.request_path)
            validated = self._validated_request(raw)
            if validated is None or validated[0] != request_id:
                break
            next_sequence = validated[2]
            next_lease_seconds = validated[3]
            if (
                next_sequence is None
                or next_lease_seconds != expected_lease_seconds
                or next_sequence < current_sequence
            ):
                break
            if next_sequence > current_sequence:
                current_sequence = next_sequence
                lease_deadline_monotonic = (
                    time.monotonic() + expected_lease_seconds
                )
            if time.monotonic() >= lease_deadline_monotonic:
                break
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def run(
        self,
        shutdown_event: asyncio.Event,
        *,
        started_event: asyncio.Event | None = None,
    ) -> None:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Monitoring remains useful on a read-only or temporarily
            # unavailable config mount. The UI receives no fresh
            # acknowledgement, so automatic installation still fails closed.
            if started_event is not None:
                started_event.set()
            await shutdown_event.wait()
            return
        if started_event is not None:
            started_event.set()
        last_request_id: str | None = None
        while not shutdown_event.is_set():
            raw = await asyncio.to_thread(_safe_json_object, self.request_path)
            validated = self._validated_request(raw)
            if validated is not None and validated[0] != last_request_id:
                request_id, deadline_unix, lease_sequence, lease_seconds = validated
                last_request_id = request_id
                remaining = deadline_unix - time.time()
                manager_count = 0
                if remaining > 5 * 60:
                    # Ignore forged or corrupt acquisition windows without
                    # making their wall-clock value part of an active lease.
                    continue
                if remaining <= 0:
                    self._write_ack(
                        request_id=request_id,
                        status="expired",
                        manager_count=0,
                        deadline_unix=deadline_unix,
                    )
                else:
                    managers = tuple(self.managers_provider())
                    if not self.registry.begin(request_id, managers):
                        self._write_ack(
                            request_id=request_id,
                            status="busy",
                            manager_count=0,
                            deadline_unix=deadline_unix,
                        )
                    else:
                        try:
                            drained, manager_count = await asyncio.to_thread(
                                self.registry.drain,
                                request_id,
                                deadline_monotonic=time.monotonic() + remaining,
                            )
                            self._write_ack(
                                request_id=request_id,
                                status="drained" if drained else "drain_failed",
                                manager_count=manager_count,
                                deadline_unix=deadline_unix,
                            )
                            if drained:
                                await self._wait_for_release(
                                    request_id,
                                    deadline_unix,
                                    lease_sequence,
                                    lease_seconds,
                                    shutdown_event,
                                )
                        finally:
                            self.registry.release(request_id)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass


_CLIENT_LOCK = threading.Lock()
_ACTIVE_CLIENT_REQUESTS: dict[str, tuple[str, threading.Event, threading.Thread]] = {}


def _write_drain_request(
    request_path: Path,
    *,
    request_id: str,
    requested_at: str,
    deadline_unix: float,
    lease_sequence: int | None = None,
    lease_seconds: float | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "request_id": request_id,
        "requested_at": requested_at,
        "deadline_unix": deadline_unix,
    }
    if lease_sequence is not None and lease_seconds is not None:
        payload["lease_sequence"] = lease_sequence
        payload["lease_seconds"] = lease_seconds
    atomic_write_json(request_path, payload, sort_keys=True)


def _renew_drain_lease(
    request_path: Path,
    *,
    request_id: str,
    requested_at: str,
    lease_seconds: float,
    refresh_seconds: float,
    acquisition_deadline_unix: float,
    initial_sequence: int,
    stop_event: threading.Event,
) -> None:
    """Renew only this process's active request until explicit release."""

    lease_sequence = initial_sequence
    while not stop_event.wait(refresh_seconds):
        if not _request_matches(request_path, request_id):
            return
        try:
            lease_sequence += 1
            _write_drain_request(
                request_path,
                request_id=request_id,
                requested_at=requested_at,
                deadline_unix=acquisition_deadline_unix,
                lease_sequence=lease_sequence,
                lease_seconds=lease_seconds,
            )
        except (OSError, TypeError, ValueError):
            # The existing bounded lease remains authoritative. A failed
            # heartbeat therefore releases queues instead of holding forever.
            return


def request_core_notification_drain(
    config_dir: Path,
    timeout: float,
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    hold_lease_seconds: float = DEFAULT_HOLD_LEASE_SECONDS,
    lease_refresh_seconds: float = DEFAULT_LEASE_REFRESH_SECONDS,
) -> bool:
    """Ask the core to drain, then renew the bounded hold until release."""

    bounded_timeout = min(60.0, max(0.1, float(timeout)))
    bounded_lease = min(
        MAX_HOLD_LEASE_SECONDS,
        max(0.1, float(hold_lease_seconds)),
    )
    bounded_refresh = min(
        bounded_lease / 2,
        max(0.01, float(lease_refresh_seconds)),
    )
    runtime_dir, request_path, ack_path = _runtime_paths(config_dir)
    key = str(runtime_dir.resolve())
    request_id = uuid.uuid4().hex
    deadline_unix = time.time() + bounded_timeout
    requested_at = _utc_now()
    with _CLIENT_LOCK:
        if key in _ACTIVE_CLIENT_REQUESTS:
            return False
        # Reserve ownership before touching shared files. The placeholder is
        # replaced atomically after the core acknowledges a complete drain.
        stop_event = threading.Event()
        placeholder = threading.Thread()
        _ACTIVE_CLIENT_REQUESTS[key] = (request_id, stop_event, placeholder)
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        _write_drain_request(
            request_path,
            request_id=request_id,
            requested_at=requested_at,
            deadline_unix=deadline_unix,
            lease_sequence=0,
            lease_seconds=bounded_lease,
        )
        deadline_monotonic = time.monotonic() + bounded_timeout
        while time.monotonic() < deadline_monotonic:
            ack = _safe_json_object(ack_path)
            if (
                ack
                and ack.get("schema") == ACK_SCHEMA
                and ack.get("request_id") == request_id
                and ack.get("deadline_unix") == deadline_unix
            ):
                if ack.get("status") == "drained" and time.time() < deadline_unix:
                    # Extend the acquisition deadline before returning to the
                    # caller, then keep it alive for the entire apply. The core
                    # rereads this deadline while holding the paused queues.
                    _write_drain_request(
                        request_path,
                        request_id=request_id,
                        requested_at=requested_at,
                        deadline_unix=deadline_unix,
                        lease_sequence=1,
                        lease_seconds=bounded_lease,
                    )
                    renewal = threading.Thread(
                        target=_renew_drain_lease,
                        kwargs={
                            "request_path": request_path,
                            "request_id": request_id,
                            "requested_at": requested_at,
                            "lease_seconds": bounded_lease,
                            "refresh_seconds": bounded_refresh,
                            "acquisition_deadline_unix": deadline_unix,
                            "initial_sequence": 1,
                            "stop_event": stop_event,
                        },
                        name="channelwatch-notification-drain-lease",
                        daemon=True,
                    )
                    with _CLIENT_LOCK:
                        current = _ACTIVE_CLIENT_REQUESTS.get(key)
                        if current is None or current[0] != request_id:
                            return False
                        _ACTIVE_CLIENT_REQUESTS[key] = (
                            request_id,
                            stop_event,
                            renewal,
                        )
                    try:
                        renewal.start()
                    except RuntimeError:
                        release_core_notification_drain(config_dir)
                        return False
                    return True
                release_core_notification_drain(config_dir)
                return False
            time.sleep(max(0.01, min(float(poll_seconds), 0.25)))
    except (OSError, TypeError, ValueError):
        pass
    release_core_notification_drain(config_dir)
    return False


def release_core_notification_drain(config_dir: Path) -> bool:
    """Release this UI process's exact request without touching another one."""

    runtime_dir, request_path, ack_path = _runtime_paths(config_dir)
    key = str(runtime_dir.resolve())
    with _CLIENT_LOCK:
        active = _ACTIVE_CLIENT_REQUESTS.pop(key, None)
    if not active:
        return False
    request_id, stop_event, renewal = active
    stop_event.set()
    if renewal.ident is not None and renewal is not threading.current_thread():
        renewal.join(timeout=1.0)
    removed = False
    try:
        if _request_matches(request_path, request_id):
            request_path.unlink()
            fsync_directory(runtime_dir)
            removed = True
    except OSError:
        return False
    try:
        ack = _safe_json_object(ack_path)
        if ack and ack.get("request_id") == request_id:
            ack_path.unlink()
            fsync_directory(runtime_dir)
    except OSError:
        pass
    return removed


def reset_notification_drain_state_for_tests() -> None:
    """Clear process-local client ownership; never used by production code."""

    with _CLIENT_LOCK:
        active = list(_ACTIVE_CLIENT_REQUESTS.values())
        _ACTIVE_CLIENT_REQUESTS.clear()
    for _request_id, stop_event, renewal in active:
        stop_event.set()
        if renewal.ident is not None and renewal is not threading.current_thread():
            renewal.join(timeout=1.0)
