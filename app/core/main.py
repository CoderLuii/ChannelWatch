#!/usr/bin/env python3
"""Core application module for ChannelWatch - Channels DVR monitoring and notification system."""

import signal
import asyncio
import hashlib
import json
import os
import sys
import argparse
import threading
import time
from pathlib import Path
from typing import Any

from .helpers.config import get_settings, CONFIG_FILE
from .helpers.encryption import bootstrap_encryption_key
from .watchdog import (
    Watchdog,
    WATCHDOG_CHECK_INTERVAL_SECONDS,
    DEFAULT_MONITOR_STALE_SECONDS,
)

from . import __version__, __app_name__
from .helpers.logging import log, set_log_level, setup_logging
from .helpers.initialize import (
    check_server_connectivity,
    _get_shared_rate_limiter,
    initialize_notifications,
    initialize_alerts,
    initialize_event_monitor,
)
from .diagnostics import run_test
from .helpers.channel_info import ChannelInfoProvider
from .engine.event_monitor import EventMonitor

SIGHUP = getattr(signal, "SIGHUP", None)


def _install_signal_handler(
    loop: asyncio.AbstractEventLoop, sig: signal.Signals | int, callback
) -> None:
    try:
        loop.add_signal_handler(sig, callback)
    except (NotImplementedError, RuntimeError, ValueError):
        signal.signal(sig, lambda _signum, _frame: callback())


# Ignore early reload requests until the asyncio runtime installs its SIGHUP handler.
if SIGHUP is not None:
    signal.signal(SIGHUP, signal.SIG_IGN)

try:
    ExceptionGroup
except NameError:  # pragma: no cover - TaskGroup requires Python 3.11+ at runtime.
    ExceptionGroup = Exception

event_monitors = []
MONITOR_SHUTDOWN_TIMEOUT_SECONDS = 15.0
DVR_INIT_RETRY_INITIAL_SECONDS = 1.0
DVR_INIT_RETRY_MAX_SECONDS = 60.0
RECONCILE_MAX_CONCURRENCY = 4
MONITOR_STOP_GRACE_SECONDS = 0.25

_dvr_tasks: dict[str, asyncio.Task[Any]] = {}
_dvr_monitors: dict[str, Any] = {}
_last_settings_raw: dict[str, Any] = {}
_watchdog: Watchdog | None = None
_monitor_stop_claim_lock = threading.Lock()


def _read_config_snapshot() -> tuple[bytes | None, str]:
    if not CONFIG_FILE.is_file():
        return None, ""
    content = CONFIG_FILE.read_bytes()
    return content, hashlib.sha256(content).hexdigest()


async def _read_config_snapshot_async() -> tuple[bytes | None, str]:
    return await asyncio.to_thread(_read_config_snapshot)


async def _persist_watchdog_async(*, force: bool = True) -> None:
    if _watchdog is None:
        return
    await asyncio.to_thread(_watchdog.persist, _dvr_tasks, _dvr_monitors, force=force)


async def _run_dvr(monitor) -> None:
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                asyncio.to_thread(monitor.start_monitoring),
                name=f"monitor-{monitor.dvr_name}",
            )
    except ExceptionGroup as eg:
        for exc in getattr(eg, "exceptions", (eg,)):
            log(f"[{monitor.dvr_name}] Task error: {exc}")


def _monitor_dvr_id(monitor: Any) -> str:
    dvr = getattr(monitor, "dvr", None)
    return str(getattr(dvr, "id", None) or getattr(monitor, "dvr_name", ""))


def _monitor_is_healthy(dvr_id: str) -> bool:
    task = _dvr_tasks.get(dvr_id)
    monitor = _dvr_monitors.get(dvr_id)
    if task is None or task.done() or monitor is None:
        return False
    if not getattr(monitor, "running", False):
        return False

    last_freshness = getattr(monitor, "last_freshness_at", 0.0)
    if not isinstance(last_freshness, (int, float)) or last_freshness <= 0:
        return False
    stale_after = (
        _watchdog.stale_threshold_seconds
        if _watchdog is not None
        else DEFAULT_MONITOR_STALE_SECONDS
    )
    return (time.time() - float(last_freshness)) <= stale_after


def _raw_dvr_target(raw: dict[str, Any], dvr_id: str) -> tuple[str, int, str] | None:
    for server in raw.get("dvr_servers", []):
        if not isinstance(server, dict) or str(server.get("id") or "") != dvr_id:
            continue
        try:
            port = int(server.get("port", 8089) or 8089)
        except (TypeError, ValueError):
            port = 8089
        return (
            str(server.get("host") or "").strip(),
            port,
            str(server.get("api_key") or ""),
        )
    return None


def _same_connection_target(
    old_raw: dict[str, Any], new_raw: dict[str, Any], dvr_id: str
) -> bool:
    old_target = _raw_dvr_target(old_raw, dvr_id)
    return old_target is not None and old_target == _raw_dvr_target(new_raw, dvr_id)


def _stop_alert_manager_resources(alert_manager: Any, *, dvr_name: str) -> None:
    """Permanently stop queues and alert-owned threads for one monitor attempt."""
    notification_manager = getattr(alert_manager, "notification_manager", None)
    shutdown_queue = getattr(type(notification_manager), "shutdown_delivery_queue", None)
    if callable(shutdown_queue):
        # Reload must stop accepting old-configuration work immediately. Any
        # in-flight provider call remains isolated in the daemon worker.
        shutdown_queue(notification_manager, drain=False, timeout=0.0)
    alerts = getattr(alert_manager, "alert_instances", {})
    if not isinstance(alerts, dict):
        return
    for alert in alerts.values():
        for stop_method_name in ("stop_monitoring", "stop_cleanup"):
            stop_alert = getattr(alert, stop_method_name, None)
            if not callable(stop_alert):
                continue
            try:
                stop_alert()
            except Exception as exc:
                log(
                    f"[{dvr_name}] Alert {stop_method_name} shutdown error: {exc}"
                )


def _request_monitor_stop(monitor) -> None:
    # Cleanup can be requested by supervisor cancellation, a registry stop, a
    # late initializer callback, and the supervisor's finalizer.  Atomically
    # grant ownership to exactly one caller so alert/queue resources are never
    # cleaned twice, even when requests arrive from different threads.
    with _monitor_stop_claim_lock:
        if getattr(monitor, "_channelwatch_stop_requested", False) is True:
            return
        monitor._channelwatch_stop_requested = True
    try:
        stop_monitoring = getattr(monitor, "stop_monitoring", None)
        # Stop intent must reach the monitor even before its worker publishes
        # ``running = True``. EventMonitor keeps that intent sticky and refuses
        # a later start, closing the to_thread start/cancel race.
        if callable(stop_monitoring):
            stop_monitoring()
    finally:
        monitor.running = False
        _stop_alert_manager_resources(
            getattr(monitor, "alert_manager", None),
            dvr_name=getattr(monitor, "dvr_name", "unknown DVR"),
        )


def _init_dvr_monitor_sync(
    dvr, settings, test_mode: bool = False, *, validation_only: bool = False
):
    from copy import copy

    _dvr_id = getattr(dvr, "id", None)
    log(
        f"--- Initializing DVR: {dvr.name} ({dvr.host}:{dvr.port}) ---",
        extra={"dvr_id": _dvr_id},
    )
    dvr_settings = copy(settings)
    for key, val in (dvr.overrides or {}).items():
        if hasattr(dvr_settings, key):
            setattr(dvr_settings, key, val)

    connected = check_server_connectivity(dvr.host, dvr.port)
    if not connected:
        log(
            f"Cannot reach DVR '{dvr.name}' at {dvr.host}:{dvr.port}. Skipping.",
            extra={"dvr_id": _dvr_id},
        )
        return None

    if validation_only:
        # A staged same-target replacement must not initialize alert handlers,
        # notification providers, metadata caches, disk polling, or persistent
        # session state while the old healthy monitor is still authoritative.
        monitor = EventMonitor(dvr=dvr, validation_only=True)
        log(
            f"[{dvr.name}] Validation-only event probe initialized",
            extra={"dvr_id": _dvr_id},
        )
        return monitor

    dvr_notification_manager = initialize_notifications(
        dvr_settings,
        test_mode=test_mode,
        installation_rate_limit=settings.global_rate_limit,
        installation_rate_window=settings.global_rate_window,
    )
    if not dvr_notification_manager:
        log(
            f"Notifications: None configured for DVR '{dvr.name}'",
            extra={"dvr_id": _dvr_id},
        )
        from .notifications.notification import NotificationManager

        dvr_notification_manager = NotificationManager(
            rate_limit=settings.global_rate_limit,
            rate_window=settings.global_rate_window,
            rate_limiter=_get_shared_rate_limiter(
                settings.global_rate_limit,
                settings.global_rate_window,
            ),
        )

    alert_manager = initialize_alerts(
        dvr_notification_manager, dvr_settings, test_mode=test_mode, dvr=dvr
    )

    if "Disk-Space" in alert_manager.alert_instances:
        disk_space_alert = alert_manager.alert_instances["Disk-Space"]
        if hasattr(disk_space_alert, "log_storage_info") and callable(
            getattr(disk_space_alert, "log_storage_info")
        ):
            disk_space_alert.log_storage_info()

    channel_count = 0
    channel_alert = alert_manager.alert_instances.get("Channel-Watching")
    if channel_alert is not None and hasattr(channel_alert, "channel_provider"):
        try:
            channel_count = channel_alert.channel_provider.cache_channels()
        except Exception as exc:
            log(
                f"[{dvr.name}] Channel metadata preload failed: {exc}",
                extra={"dvr_id": _dvr_id},
            )
    else:
        channel_provider = ChannelInfoProvider(dvr=dvr)
        try:
            channel_count = channel_provider.cache_channels()
        except Exception:
            _stop_alert_manager_resources(alert_manager, dvr_name=dvr.name)
            raise

    if channel_count:
        log(f"[{dvr.name}] Channels: {channel_count}", extra={"dvr_id": _dvr_id})

    vod_count = 0
    recording_count = 0
    for alert_type, alert in alert_manager.alert_instances.items():
        if alert_type == "VOD-Watching" and hasattr(alert, "_cache_vod_metadata"):
            vod_count = alert._cache_vod_metadata()
        elif alert_type == "Recording-Events" and hasattr(alert, "_cache_channels"):
            recording_count = alert._cache_channels()
        elif alert_type != "Channel-Watching" and hasattr(alert, "_cache_channels"):
            alert._cache_channels()
    log(
        f"[{dvr.name}] VOD library: {vod_count} items | Recordings: {recording_count} scheduled",
        extra={"dvr_id": _dvr_id},
    )

    for alert_type, alert in alert_manager.alert_instances.items():
        if hasattr(alert, "set_startup_complete") and callable(
            getattr(alert, "set_startup_complete")
        ):
            alert.set_startup_complete()

    monitor = initialize_event_monitor(dvr.host, dvr.port, alert_manager, dvr=dvr)
    if monitor:
        if "Disk-Space" in alert_manager.alert_instances:
            disk_space_alert = alert_manager.alert_instances["Disk-Space"]
            try:
                disk_space_alert.start_monitoring()
                if hasattr(disk_space_alert, "_start_health_checker"):
                    disk_space_alert._start_health_checker()
            except Exception:
                _stop_alert_manager_resources(alert_manager, dvr_name=dvr.name)
                raise
        log(f"[{dvr.name}] Event monitor initialized", extra={"dvr_id": _dvr_id})
    else:
        _stop_alert_manager_resources(alert_manager, dvr_name=dvr.name)
        log(
            f"[{dvr.name}] Failed to initialize event monitor",
            extra={"dvr_id": _dvr_id},
        )
    return monitor


async def _init_dvr_monitor_async(
    dvr,
    settings,
    test_mode: bool,
    initialization_semaphore: asyncio.Semaphore | None = None,
    *,
    validation_only: bool = False,
):
    """Initialize off-loop and clean up a late result after caller cancellation."""

    async def initialize():
        init_kwargs = {"validation_only": True} if validation_only else {}
        if initialization_semaphore is None:
            return await asyncio.to_thread(
                _init_dvr_monitor_sync, dvr, settings, test_mode, **init_kwargs
            )
        async with initialization_semaphore:
            return await asyncio.to_thread(
                _init_dvr_monitor_sync, dvr, settings, test_mode, **init_kwargs
            )

    initialization_task = asyncio.create_task(
        initialize(), name=f"initialize-{dvr.name}"
    )
    try:
        return await asyncio.shield(initialization_task)
    except asyncio.CancelledError:

        def cleanup_late_result(task: asyncio.Task[Any]) -> None:
            try:
                late_monitor = task.result()
            except (asyncio.CancelledError, Exception) as exc:
                if not isinstance(exc, asyncio.CancelledError):
                    log(
                        f"[{dvr.name}] Cancelled initialization finished with an "
                        f"error: {exc}",
                        extra={"dvr_id": str(dvr.id)},
                    )
                return
            if late_monitor is not None:
                _request_monitor_stop(late_monitor)

        initialization_task.add_done_callback(cleanup_late_result)
        raise


async def _stop_dvr_task(dvr_id: str) -> None:
    monitor = _dvr_monitors.pop(dvr_id, None)
    if monitor is not None:
        _request_monitor_stop(monitor)

    task = _dvr_tasks.pop(dvr_id, None)
    if task is not None and not task.done():
        # Active monitors get a short cooperative window after stop_monitoring().
        # Retry-only supervisors have no monitor and are cancelled immediately so
        # a removed/disabled DVR never waits out a long backoff.
        if monitor is not None:
            await asyncio.wait({task}, timeout=MONITOR_STOP_GRACE_SECONDS)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    if _watchdog is not None:
        _watchdog.remove_dvr(dvr_id)
        await _persist_watchdog_async(force=True)


async def _wait_for_retry(shutdown_event: asyncio.Event, delay_seconds: float) -> bool:
    """Wait for the next retry, returning false when shutdown was requested."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=delay_seconds)
    except asyncio.TimeoutError:
        return True
    return False


async def _supervise_dvr(
    dvr,
    settings,
    shutdown_event: asyncio.Event,
    test_mode: bool = False,
    *,
    initial_monitor: Any | None = None,
    initial_monitor_task: asyncio.Task[Any] | None = None,
    initialization_semaphore: asyncio.Semaphore | None = None,
) -> None:
    """Keep one enabled DVR initialized until disabled, replaced, or shutdown."""
    dvr_id = str(dvr.id)
    retry_delay = DVR_INIT_RETRY_INITIAL_SECONDS
    monitor = initial_monitor
    monitor_task = initial_monitor_task

    try:
        while not shutdown_event.is_set():
            if monitor is None:
                try:
                    monitor = await _init_dvr_monitor_async(
                        dvr,
                        settings,
                        test_mode,
                        initialization_semaphore,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log(
                        f"[{dvr.name}] Monitor initialization failed: {exc}",
                        extra={"dvr_id": dvr_id},
                    )

                if monitor is None:
                    log(
                        f"[{dvr.name}] DVR unavailable; retrying initialization in "
                        f"{retry_delay:g}s",
                        extra={"dvr_id": dvr_id},
                    )
                    await _persist_watchdog_async(force=True)
                    if not await _wait_for_retry(shutdown_event, retry_delay):
                        break
                    retry_delay = min(retry_delay * 2.0, DVR_INIT_RETRY_MAX_SECONDS)
                    continue

            if _watchdog is not None:
                _watchdog.attach_monitor(monitor)
            _dvr_monitors[dvr_id] = monitor
            await _persist_watchdog_async(force=True)

            baseline_freshness = getattr(monitor, "last_freshness_at", 0.0) or 0.0
            if monitor_task is None:
                monitor_task = asyncio.create_task(
                    _run_dvr(monitor), name=f"monitor-{monitor.dvr_name}"
                )

            try:
                await monitor_task
            except asyncio.CancelledError:
                _request_monitor_stop(monitor)
                if not monitor_task.done():
                    monitor_task.cancel()
                await asyncio.gather(monitor_task, return_exceptions=True)
                raise
            finally:
                if _dvr_monitors.get(dvr_id) is monitor:
                    _dvr_monitors.pop(dvr_id, None)

            # A monitor task can return without clearing ``running`` (for
            # example, after an unexpected worker/thread exit).  Release its
            # alert and notification resources before forgetting it and
            # attempting a replacement.
            _request_monitor_stop(monitor)

            if shutdown_event.is_set():
                break

            observed_freshness = getattr(monitor, "last_freshness_at", 0.0) or 0.0
            attempt_was_healthy = bool(
                observed_freshness > baseline_freshness or observed_freshness > 0
            )
            if attempt_was_healthy:
                retry_delay = DVR_INIT_RETRY_INITIAL_SECONDS
            log(
                f"[{dvr.name}] Monitor stopped unexpectedly; retrying in "
                f"{retry_delay:g}s",
                extra={"dvr_id": dvr_id},
            )
            monitor = None
            monitor_task = None
            await _persist_watchdog_async(force=True)
            if not await _wait_for_retry(shutdown_event, retry_delay):
                break
            if not attempt_was_healthy:
                retry_delay = min(retry_delay * 2.0, DVR_INIT_RETRY_MAX_SECONDS)
    finally:
        if monitor is not None:
            _request_monitor_stop(monitor)
        if monitor_task is not None and not monitor_task.done():
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if _dvr_monitors.get(dvr_id) is monitor:
            _dvr_monitors.pop(dvr_id, None)


async def _start_dvr_supervisor(
    dvr,
    settings,
    shutdown_event: asyncio.Event,
    test_mode: bool = False,
    *,
    initial_monitor: Any | None = None,
    initial_monitor_task: asyncio.Task[Any] | None = None,
    initialization_semaphore: asyncio.Semaphore | None = None,
) -> asyncio.Task[Any]:
    dvr_id = str(dvr.id)
    task = asyncio.create_task(
        _supervise_dvr(
            dvr,
            settings,
            shutdown_event,
            test_mode,
            initial_monitor=initial_monitor,
            initial_monitor_task=initial_monitor_task,
            initialization_semaphore=initialization_semaphore,
        ),
        name=f"dvr-supervisor-{dvr.name}",
    )
    _dvr_tasks[dvr_id] = task
    await _persist_watchdog_async(force=True)
    return task


async def _watchdog_loop(
    shutdown_event: asyncio.Event, started_event: asyncio.Event | None = None
) -> None:
    if started_event is not None:
        started_event.set()
    while not shutdown_event.is_set():
        await _persist_watchdog_async(force=True)
        try:
            await asyncio.wait_for(
                shutdown_event.wait(), timeout=WATCHDOG_CHECK_INTERVAL_SECONDS
            )
        except asyncio.TimeoutError:
            continue


async def _verify_monitor_freshness(
    monitor,
    task: asyncio.Task[Any],
    *,
    timeout_seconds: float = 60.0,
) -> tuple[bool, str]:
    start_time = time.monotonic()
    baseline = getattr(monitor, "last_freshness_at", 0.0) or 0.0

    while (time.monotonic() - start_time) < timeout_seconds:
        if task.done():
            return False, "Restarted monitor task is no longer running"
        if (getattr(monitor, "last_freshness_at", 0.0) or 0.0) > baseline:
            return True, "Freshness update observed"
        await asyncio.sleep(min(0.25, max(0.01, timeout_seconds / 10.0)))

    return False, f"No freshness update arrived within {int(timeout_seconds)} seconds"


async def _notify_hot_reload_failure(monitor, reason: str) -> bool:
    """Hand one final reload-failure notice to the monitor's owned worker.

    A true result means the local queue accepted an exactly-once terminal
    attempt; it does not claim that a remote provider received the message.
    """
    alert_manager = getattr(monitor, "alert_manager", None)
    notification_manager = getattr(alert_manager, "notification_manager", None)
    if notification_manager is None:
        log(
            f"[{getattr(monitor, 'dvr_name', 'unknown DVR')}] Hot-reload "
            "failure notice unavailable: no notification manager"
        )
        return False

    dvr = getattr(monitor, "dvr", None)
    dvr_id = getattr(dvr, "id", monitor.dvr_name)
    dvr_name = getattr(dvr, "name", monitor.dvr_name)
    title = f"⚠️ Hot reload verification failed for {dvr_name}"
    message = (
        f"ChannelWatch could not validate a replacement monitor for DVR '{dvr_name}' ({dvr_id}). "
        f"Reason: {reason}. Check diagnostics and logs before trusting readiness."
    )
    enqueue_terminal = getattr(
        type(notification_manager), "enqueue_terminal_notification", None
    )
    if callable(enqueue_terminal):
        accepted = bool(
            enqueue_terminal(
                notification_manager,
                title,
                message,
                dvr_id=dvr_id,
                dvr_name=dvr_name,
                event_type="runtime",
            )
        )
        if not accepted:
            log(
                f"[{dvr_name}] Hot-reload failure notice was not accepted; "
                "runtime logs and readiness remain authoritative"
            )
        return accepted

    log(
        f"[{dvr_name}] Hot-reload failure notice unavailable: notification "
        "manager does not support terminal handoff"
    )
    return False


async def _start_verified_dvr_task(
    monitor, *, verification_timeout: float = 60.0
) -> asyncio.Task[Any]:
    dvr = getattr(monitor, "dvr", None)
    dvr_id = getattr(dvr, "id", monitor.dvr_name)

    if _watchdog is not None:
        _watchdog.attach_monitor(monitor)

    task = asyncio.create_task(_run_dvr(monitor), name=f"dvr-{monitor.dvr_name}")
    _dvr_tasks[dvr_id] = task
    _dvr_monitors[dvr_id] = monitor
    await _persist_watchdog_async(force=True)

    ok, reason = await _verify_monitor_freshness(
        monitor, task, timeout_seconds=verification_timeout
    )
    if ok:
        log(f"  [{dvr_id}] freshness verified after restart", extra={"dvr_id": dvr_id})
    else:
        log(
            f"  [{dvr_id}] hot-reload verification failed: {reason}",
            extra={"dvr_id": dvr_id},
        )
        await _notify_hot_reload_failure(monitor, reason)
        _request_monitor_stop(monitor)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if _dvr_tasks.get(dvr_id) is task:
            _dvr_tasks.pop(dvr_id, None)
        if _dvr_monitors.get(dvr_id) is monitor:
            _dvr_monitors.pop(dvr_id, None)
        if _watchdog is not None:
            _watchdog.remove_dvr(dvr_id)
        await _persist_watchdog_async(force=True)
    return task


async def _stage_monitor_replacement(
    dvr,
    settings,
    test_mode: bool,
    *,
    verification_timeout: float = 60.0,
    initialization_semaphore: asyncio.Semaphore | None = None,
    failure_notification_monitor: Any | None = None,
) -> bool:
    """Validate desired DVR freshness without starting a second processor.

    The validation-only monitor has no alert manager and is stopped before this
    function returns.  The caller can therefore stop the old processor and
    construct the full desired replacement without any overlap in alert or
    state ownership.
    """
    try:
        monitor = await _init_dvr_monitor_async(
            dvr,
            settings,
            test_mode,
            initialization_semaphore,
            validation_only=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log(
            f"  [{dvr.id}] replacement initialization failed: {exc}",
            extra={"dvr_id": str(dvr.id)},
        )
        return False
    if monitor is None:
        return False

    task = asyncio.create_task(
        _run_dvr(monitor), name=f"validation-probe-{monitor.dvr_name}"
    )
    try:
        ok, reason = await _verify_monitor_freshness(
            monitor, task, timeout_seconds=verification_timeout
        )
    except (asyncio.CancelledError, Exception):
        _request_monitor_stop(monitor)
        await asyncio.wait({task}, timeout=MONITOR_STOP_GRACE_SECONDS)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise

    # The probe is never promotable: stop it before returning so the subsequent
    # old-monitor stop and full replacement start have an unambiguous ordering.
    _request_monitor_stop(monitor)
    await asyncio.wait({task}, timeout=MONITOR_STOP_GRACE_SECONDS)
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    if ok:
        return True

    log(
        f"  [{dvr.id}] hot-reload verification failed: {reason}",
        extra={"dvr_id": str(dvr.id)},
    )
    try:
        await _notify_hot_reload_failure(
            failure_notification_monitor or monitor, reason
        )
    except Exception as exc:
        log(
            f"  [{dvr.id}] could not deliver hot-reload failure notice: {exc}",
            extra={"dvr_id": str(dvr.id)},
        )
    return False


async def _reconcile_dvr(
    dvr,
    settings,
    shutdown_event: asyncio.Event,
    test_mode: bool,
    *,
    preserve_healthy_monitor: bool,
    verification_timeout: float = 60.0,
    initialization_semaphore: asyncio.Semaphore | None = None,
) -> bool:
    """Apply one desired DVR definition without disturbing unrelated DVRs."""
    dvr_id = str(dvr.id)
    existing_task = _dvr_tasks.get(dvr_id)
    existing_monitor = _dvr_monitors.get(dvr_id)

    if (
        preserve_healthy_monitor
        and existing_task is not None
        and existing_monitor is not None
        and _monitor_is_healthy(dvr_id)
    ):
        replacement_valid = await _stage_monitor_replacement(
            dvr,
            settings,
            test_mode,
            verification_timeout=verification_timeout,
            initialization_semaphore=initialization_semaphore,
            failure_notification_monitor=existing_monitor,
        )
        if not replacement_valid:
            log(
                f"  [{dvr_id}] replacement validation failed; stopping the old "
                "monitor and retrying the desired configuration",
                extra={"dvr_id": dvr_id},
            )
            await _stop_dvr_task(dvr_id)
            await _start_dvr_supervisor(
                dvr,
                settings,
                shutdown_event,
                test_mode,
                initialization_semaphore=initialization_semaphore,
            )
            return False
        # The validation probe is already stopped.  Stop the old processor
        # before constructing the full desired monitor so at most one monitor
        # can ever deliver alerts or mutate activity/session state.
        await _stop_dvr_task(dvr_id)
        await _start_dvr_supervisor(
            dvr,
            settings,
            shutdown_event,
            test_mode,
            initialization_semaphore=initialization_semaphore,
        )
        return True

    if existing_task is not None or existing_monitor is not None:
        await _stop_dvr_task(dvr_id)
    await _start_dvr_supervisor(
        dvr,
        settings,
        shutdown_event,
        test_mode,
        initialization_semaphore=initialization_semaphore,
    )
    return True


async def _gather_reconciliation_tasks(
    tasks: list[asyncio.Task[Any]],
) -> None:
    """Run one reconciliation batch without leaving live siblings on failure."""

    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # gather() propagates the first failure without waiting for unrelated
        # children.  The watcher deliberately retains its previous applied
        # snapshot and retries the same saved configuration, so every sibling
        # must be cancelled and drained before that retry can begin.  Otherwise
        # two attempts for one DVR can overlap and race monitor validation,
        # shutdown, and supervisor registration.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _handle_config_reload(
    old_raw: dict[str, Any],
    new_raw: dict[str, Any],
    settings,
    test_mode: bool = False,
    *,
    shutdown_event: asyncio.Event | None = None,
    reconcile_semaphore: asyncio.Semaphore | None = None,
    initialization_semaphore: asyncio.Semaphore | None = None,
    verification_timeout: float = 60.0,
) -> None:
    from .helpers.hot_reload import (
        compute_reload_diff,
        compute_reload_targets,
        format_diff_summary,
    )
    from .helpers.config import CoreSettings

    diff = compute_reload_diff(old_raw, new_raw)
    old_ids = {
        str(server.get("id") or "")
        for server in old_raw.get("dvr_servers", [])
        if isinstance(server, dict)
        and server.get("id")
        and not server.get("deleted_at")
    }
    new_ids = {
        str(server.get("id") or "")
        for server in new_raw.get("dvr_servers", [])
        if isinstance(server, dict)
        and server.get("id")
        and not server.get("deleted_at")
    }
    credential_or_target_changes = {
        dvr_id
        for dvr_id in old_ids & new_ids
        if _raw_dvr_target(old_raw, dvr_id) != _raw_dvr_target(new_raw, dvr_id)
    }
    if credential_or_target_changes:
        diff["changed_dvr_ids"] = sorted(
            set(diff["changed_dvr_ids"]) | credential_or_target_changes
        )
        diff["any_action"] = True
    if not diff["any_action"]:
        return

    log(f"CONFIG_RELOADED: {format_diff_summary(diff)}")

    if diff["restart_required"]:
        log(
            f"  Settings changed that require container restart (not applied): {diff['restart_required']}"
        )

    CoreSettings._instance = None
    new_settings = await asyncio.to_thread(get_settings)
    reload_target_ids = compute_reload_targets(diff, active_dvr_ids=list(_dvr_tasks))
    desired_dvrs = {str(dvr.id): dvr for dvr in new_settings.get_dvr_connections()}
    shutdown_event = shutdown_event or asyncio.Event()
    reconcile_semaphore = reconcile_semaphore or asyncio.Semaphore(
        RECONCILE_MAX_CONCURRENCY
    )
    initialization_semaphore = initialization_semaphore or asyncio.Semaphore(
        RECONCILE_MAX_CONCURRENCY
    )

    if diff["global_changes"] and reload_target_ids:
        log(
            "  Reapplying shared runtime settings across active DVRs: "
            f"{reload_target_ids} (fields: {list(diff['global_changes'].keys())})"
        )

    async def stop_removed(dvr_id: str) -> None:
        async with reconcile_semaphore:
            log(
                f"  [{dvr_id}] stopping (DVR removed from config)",
                extra={"dvr_id": dvr_id},
            )
            await _stop_dvr_task(dvr_id)

    removed_tasks = [
        asyncio.create_task(
            stop_removed(dvr_id),
            name=f"stop-removed-{dvr_id}",
        )
        for dvr_id in diff["removed_dvr_ids"]
    ]
    await _gather_reconciliation_tasks(removed_tasks)

    changed_ids = set(diff["changed_dvr_ids"])
    global_change_keys = list(diff["global_changes"].keys())

    async def apply_target(dvr_id: str, *, added: bool = False) -> None:
        async with reconcile_semaphore:
            dvr = desired_dvrs.get(dvr_id)
            if dvr is None:
                log(
                    f"  [{dvr_id}] stopping (DVR disabled or unavailable in config)",
                    extra={"dvr_id": dvr_id},
                )
                await _stop_dvr_task(dvr_id)
                return

            if added:
                reason = "new DVR added"
            elif dvr_id in changed_ids and global_change_keys:
                reason = f"DVR config + global settings changed ({global_change_keys})"
            elif dvr_id in changed_ids:
                reason = "DVR config changed"
            else:
                reason = f"global settings changed ({global_change_keys})"

            log(f"  [{dvr_id}] reconciling ({reason})", extra={"dvr_id": dvr_id})
            preserve_healthy = not added and _same_connection_target(
                old_raw, new_raw, dvr_id
            )
            applied = await _reconcile_dvr(
                dvr,
                new_settings,
                shutdown_event,
                test_mode,
                preserve_healthy_monitor=preserve_healthy,
                verification_timeout=verification_timeout,
                initialization_semaphore=initialization_semaphore,
            )
            if applied:
                log(
                    f"  [{dvr_id}] monitoring desired config as '{dvr.name}'",
                    extra={"dvr_id": dvr_id},
                )

    target_ids = list(dict.fromkeys([*reload_target_ids, *diff["added_dvr_ids"]]))
    target_tasks = [
        asyncio.create_task(
            apply_target(dvr_id, added=dvr_id in diff["added_dvr_ids"]),
            name=f"reconcile-{dvr_id}",
        )
        for dvr_id in target_ids
    ]
    await _gather_reconciliation_tasks(target_tasks)

    # A removed DVR can also have been part of the prior active task set.  The
    # target computation excludes it, but keep this assertion-by-cleanup local to
    # the reconciler so a malformed diff can never leave a dangling registry.
    for dvr_id in diff["removed_dvr_ids"]:
        _dvr_tasks.pop(dvr_id, None)
        _dvr_monitors.pop(dvr_id, None)

    await _persist_watchdog_async(force=True)


async def _watch_config_and_reload(
    shutdown_event: asyncio.Event,
    reload_event: asyncio.Event,
    settings,
    test_mode: bool = False,
    *,
    reconcile_semaphore: asyncio.Semaphore | None = None,
    initialization_semaphore: asyncio.Semaphore | None = None,
    started_event: asyncio.Event | None = None,
) -> None:
    last_hash = ""
    try:
        _content, last_hash = await _read_config_snapshot_async()
    except OSError:
        pass
    if started_event is not None:
        started_event.set()

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(reload_event.wait(), timeout=2.0)
            reload_event.clear()
        except asyncio.TimeoutError:
            pass

        if shutdown_event.is_set():
            break

        try:
            content, current_hash = await _read_config_snapshot_async()
            if content is None:
                continue

            if current_hash == last_hash:
                continue

            new_raw = json.loads(content.decode())
            old_raw = dict(_last_settings_raw)

            await _handle_config_reload(
                old_raw,
                new_raw,
                settings,
                test_mode,
                shutdown_event=shutdown_event,
                reconcile_semaphore=reconcile_semaphore,
                initialization_semaphore=initialization_semaphore,
            )
            # The hash and raw snapshot describe applied runtime state, not
            # merely the latest bytes observed on disk.  If reconciliation
            # fails, retaining the previous values makes the unchanged saved
            # configuration eligible for the next poll/SIGHUP retry.
            last_hash = current_hash
            _last_settings_raw.clear()
            _last_settings_raw.update(new_raw)

        except json.JSONDecodeError as e:
            log(f"[HotReload] Invalid JSON in settings file: {e}")
        except Exception as e:
            log(f"[HotReload] Error in config watcher: {e}")


async def _run_monitors_dynamic(
    initial_monitors: list[Any],
    settings,
    shutdown_event: asyncio.Event,
    reload_event: asyncio.Event,
    test_mode: bool = False,
    on_ready=None,
) -> None:
    global _dvr_tasks, _dvr_monitors, _last_settings_raw, _watchdog

    _dvr_tasks = {}
    _dvr_monitors = {}
    _watchdog = Watchdog(
        stale_threshold_seconds=int(
            getattr(settings, "monitor_stale_seconds", DEFAULT_MONITOR_STALE_SECONDS)
        ),
    )

    try:
        content, _hash = await _read_config_snapshot_async()
        if content is not None:
            _last_settings_raw = json.loads(content.decode())
    except Exception:
        _last_settings_raw = {}

    initial_by_id = {_monitor_dvr_id(monitor): monitor for monitor in initial_monitors}
    desired_dvrs = {str(dvr.id): dvr for dvr in settings.get_dvr_connections()}
    for dvr_id, monitor in initial_by_id.items():
        if dvr_id not in desired_dvrs:
            desired_dvrs[dvr_id] = monitor.dvr

    initialization_semaphore = asyncio.Semaphore(RECONCILE_MAX_CONCURRENCY)
    await asyncio.gather(
        *(
            _start_dvr_supervisor(
                dvr,
                settings,
                shutdown_event,
                test_mode,
                initial_monitor=initial_by_id.get(dvr_id),
                initialization_semaphore=initialization_semaphore,
            )
            for dvr_id, dvr in desired_dvrs.items()
        )
    )
    await _persist_watchdog_async(force=True)

    reconcile_semaphore = asyncio.Semaphore(RECONCILE_MAX_CONCURRENCY)

    watcher_started = asyncio.Event()
    watchdog_started = asyncio.Event()
    watcher_task = asyncio.create_task(
        _watch_config_and_reload(
            shutdown_event,
            reload_event,
            settings,
            test_mode,
            reconcile_semaphore=reconcile_semaphore,
            initialization_semaphore=initialization_semaphore,
            started_event=watcher_started,
        ),
        name="config-watcher",
    )
    watchdog_task = asyncio.create_task(
        _watchdog_loop(shutdown_event, watchdog_started), name="monitor-watchdog"
    )

    all_tasks = [watcher_task, watchdog_task]
    try:
        await asyncio.gather(watcher_started.wait(), watchdog_started.wait())
        for runtime_task in all_tasks:
            if runtime_task.done():
                runtime_task.result()
        if on_ready is not None:
            ready_result = on_ready()
            if hasattr(ready_result, "__await__"):
                await ready_result

        await shutdown_event.wait()
    finally:
        log("Received shutdown signal, stopping monitors...")
        watcher_task.cancel()
        watchdog_task.cancel()
        await asyncio.gather(
            *(_stop_dvr_task(dvr_id) for dvr_id in list(_dvr_tasks)),
            return_exceptions=True,
        )

        try:
            await asyncio.wait_for(
                asyncio.gather(*all_tasks, return_exceptions=True),
                timeout=MONITOR_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            pending_names = [task.get_name() for task in all_tasks if not task.done()]
            log(
                "Timed out waiting for runtime shutdown; cancelling unfinished tasks: "
                f"{pending_names}"
            )
            for task in all_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)
        await _persist_watchdog_async(force=True)
        log("All monitors stopped.")


async def _run_monitors(monitors: list[Any], shutdown_event: asyncio.Event) -> None:
    tasks = [
        asyncio.create_task(
            _run_dvr(monitor),
            name=f"dvr-{monitor.dvr_name}",
        )
        for monitor in monitors
    ]

    await shutdown_event.wait()

    log("Received shutdown signal, stopping monitors...")
    for monitor in monitors:
        _request_monitor_stop(monitor)

    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=MONITOR_SHUTDOWN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        pending_names = [task.get_name() for task in tasks if not task.done()]
        log(
            "Timed out waiting for monitor shutdown; cancelling unfinished tasks: "
            f"{pending_names}"
        )
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    log("All monitors stopped.")


def _record_update_core_ready(config_dir: str) -> None:
    """Publish core readiness without swallowing a terminal rollback restart."""

    from .helpers.migration import CURRENT_SCHEMA_VERSION
    from .runtime_launcher import request_container_restart
    from .update_center import UpdateManager, UpdateRestartError

    def _restart_whole_container() -> bool:
        request_container_restart()
        return True

    try:
        UpdateManager(
            config_dir=Path(config_dir),
            current_version=__version__,
            settings_schema_version=CURRENT_SCHEMA_VERSION,
            restart_callable=_restart_whole_container,
        ).record_startup_success(
            component="core",
            running_version=__version__,
            activation_id=os.environ.get("CHANNELWATCH_ACTIVATION_ID", ""),
            healthy=True,
        )
    except UpdateRestartError as exc:
        # Disk selection has already rolled back. Continuing this generation
        # would leave the core running code that is no longer selected, so let
        # the runtime terminate and allow Supervisor to recover it.
        log(f"Terminal Update Center core restart failure: {exc}")
        raise
    except Exception as exc:
        log(f"Could not record Update Center core startup readiness: {exc}")


async def main() -> None:
    """Async application entry point handling initialization, monitoring, and command-line options."""

    # INITIALIZATION
    bootstrap_encryption_key()
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description=f"{__app_name__} - Channels DVR monitoring tool"
    )
    parser.add_argument(
        "--test-connectivity",
        action="store_true",
        help="Test API connectivity and exit",
    )
    parser.add_argument(
        "--test-alert",
        type=str,
        metavar="ALERT_TYPE",
        help="Test alert functionality for the specified alert type",
    )
    parser.add_argument(
        "--test-api", action="store_true", help="Test common API endpoints"
    )
    parser.add_argument(
        "--monitor-events",
        type=int,
        metavar="SECONDS",
        help="Monitor event stream for specified seconds and exit",
    )
    parser.add_argument(
        "--stay-alive",
        action="store_true",
        help="Keep container running even with connection errors",
    )
    args = parser.parse_args()

    config_dir = os.getenv("CONFIG_PATH", "/config")
    retention_days = settings.log_retention_days
    log_level = settings.log_level
    log_file_path = os.path.join(config_dir, "channelwatch.log")

    test_mode = (
        args.test_connectivity
        or args.test_api
        or args.test_alert
        or args.monitor_events is not None
    )
    setup_logging(config_dir, retention_days, test_mode=test_mode)

    def _record_runtime_ready() -> None:
        if test_mode:
            return
        _record_update_core_ready(config_dir)

    if not test_mode:
        log(f"Starting {__app_name__} v{__version__}")
        log(
            f"Logging: Level {log_level} ({('Standard' if log_level == 1 else 'Verbose')}) | File: {log_file_path} | Retention: {retention_days} days | Config: {CONFIG_FILE}"
        )

    if log_level not in (1, 2):
        log("Warning: Invalid log_level in config, defaulting to 1 (Standard)")
        log_level = 1
    set_log_level(log_level, test_mode=test_mode)

    # DVR CONNECTIONS
    dvr_connections = settings.get_dvr_connections()

    # TEST MODE (use first DVR)
    first_dvr = dvr_connections[0] if dvr_connections else None
    if test_mode and first_dvr is None:
        log("No enabled DVR is configured for the requested diagnostic.")
        raise SystemExit(1)
    if args.test_connectivity:
        sys.exit(0 if run_test("connectivity", first_dvr.host, first_dvr.port) else 1)
    if args.test_api:
        sys.exit(0 if run_test("api", first_dvr.host, first_dvr.port) else 1)
    if args.monitor_events:
        duration = args.monitor_events
        sys.exit(
            0
            if run_test("event_stream", first_dvr.host, first_dvr.port, None, duration)
            else 1
        )

    # Test alert uses first DVR's settings
    if args.test_alert:
        from copy import copy as _copy

        _test_settings = _copy(settings)
        if first_dvr and first_dvr.overrides:
            for key, val in first_dvr.overrides.items():
                if hasattr(_test_settings, key):
                    setattr(_test_settings, key, val)
        _test_nm = initialize_notifications(
            _test_settings,
            test_mode=test_mode,
            installation_rate_limit=settings.global_rate_limit,
            installation_rate_window=settings.global_rate_window,
        )
        if not _test_nm:
            from .notifications.notification import NotificationManager

            _test_nm = NotificationManager(
                rate_limit=settings.global_rate_limit,
                rate_window=settings.global_rate_window,
                rate_limiter=_get_shared_rate_limiter(
                    settings.global_rate_limit,
                    settings.global_rate_window,
                ),
            )
        alert_manager = initialize_alerts(
            _test_nm, _test_settings, test_mode=test_mode, dvr=first_dvr
        )
        sys.exit(
            0
            if run_test(args.test_alert, first_dvr.host, first_dvr.port, alert_manager)
            else 1
        )

    # SHUTDOWN SIGNAL WIRING
    shutdown_event = asyncio.Event()
    reload_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        log("Received shutdown signal, stopping...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        _install_signal_handler(loop, sig, _request_shutdown)
    if SIGHUP is not None:
        _install_signal_handler(loop, SIGHUP, lambda: reload_event.set())

    # PER-DVR MONITORING SETUP
    global event_monitors
    event_monitors.clear()
    if dvr_connections:
        log(f"Reconciling {len(dvr_connections)} configured DVR server(s)")
    else:
        log(
            "Waiting for DVR server configuration. Set it in the Web UI at "
            "http://localhost:8501; monitoring will start without a core restart."
        )
    await _run_monitors_dynamic(
        event_monitors,
        settings,
        shutdown_event,
        reload_event,
        test_mode=False,
        on_ready=_record_runtime_ready,
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
