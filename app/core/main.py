#!/usr/bin/env python3
"""Core application module for ChannelWatch - Channels DVR monitoring and notification system."""

import signal
import asyncio
import hashlib
import json
import os
import sys
import argparse
import time
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
    initialize_notifications,
    initialize_alerts,
    initialize_event_monitor,
)
from .diagnostics import run_test
from .helpers.channel_info import ChannelInfoProvider

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

_dvr_tasks: dict[str, asyncio.Task[Any]] = {}
_dvr_monitors: dict[str, Any] = {}
_last_settings_raw: dict[str, Any] = {}
_watchdog: Watchdog | None = None


def _read_config_snapshot() -> tuple[bytes | None, str]:
    if not CONFIG_FILE.is_file():
        return None, ""
    content = CONFIG_FILE.read_bytes()
    return content, hashlib.md5(content).hexdigest()


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


def _request_monitor_stop(monitor) -> None:
    stop_monitoring = getattr(monitor, "stop_monitoring", None)
    if callable(stop_monitoring):
        stop_monitoring()
    monitor.running = False


def _init_dvr_monitor_sync(dvr, settings, test_mode: bool = False):
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

    dvr_notification_manager = initialize_notifications(
        dvr_settings, test_mode=test_mode
    )
    if not dvr_notification_manager:
        log(
            f"Notifications: None configured for DVR '{dvr.name}'",
            extra={"dvr_id": _dvr_id},
        )
        from .notifications.notification import NotificationManager

        dvr_notification_manager = NotificationManager(
            rate_limit=dvr_settings.global_rate_limit,
            rate_window=dvr_settings.global_rate_window,
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
        disk_space_alert.start_monitoring()
        if hasattr(disk_space_alert, "_start_health_checker"):
            disk_space_alert._start_health_checker()

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
        channel_count = channel_provider.cache_channels()

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
        log(f"[{dvr.name}] Event monitor initialized", extra={"dvr_id": _dvr_id})
    else:
        log(
            f"[{dvr.name}] Failed to initialize event monitor",
            extra={"dvr_id": _dvr_id},
        )
    return monitor


async def _stop_dvr_task(dvr_id: str) -> None:
    monitor = _dvr_monitors.pop(dvr_id, None)
    if monitor is not None:
        _request_monitor_stop(monitor)

    task = _dvr_tasks.pop(dvr_id, None)
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    if _watchdog is not None:
        _watchdog.remove_dvr(dvr_id)
        await _persist_watchdog_async(force=True)


async def _watchdog_loop(shutdown_event: asyncio.Event) -> None:
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
    start_time = time.time()
    baseline = getattr(monitor, "last_freshness_at", 0.0) or 0.0

    while (time.time() - start_time) < timeout_seconds:
        if task.done() or not getattr(monitor, "running", False):
            return False, "Restarted monitor task is no longer running"
        if (getattr(monitor, "last_freshness_at", 0.0) or 0.0) > baseline:
            return True, "Freshness update observed"
        await asyncio.sleep(1.0)

    return False, f"No freshness update arrived within {int(timeout_seconds)} seconds"


async def _notify_hot_reload_failure(monitor, reason: str) -> None:
    alert_manager = getattr(monitor, "alert_manager", None)
    notification_manager = getattr(alert_manager, "notification_manager", None)
    if notification_manager is None:
        return

    dvr = getattr(monitor, "dvr", None)
    dvr_id = getattr(dvr, "id", monitor.dvr_name)
    dvr_name = getattr(dvr, "name", monitor.dvr_name)
    title = f"⚠️ Hot reload verification failed for {dvr_name}"
    message = (
        f"ChannelWatch restarted DVR '{dvr_name}' ({dvr_id}) but the replacement monitor did not recover cleanly. "
        f"Reason: {reason}. Check diagnostics and logs before trusting readiness."
    )
    await asyncio.to_thread(
        notification_manager.send_notification,
        title,
        message,
        dvr_id=dvr_id,
        dvr_name=dvr_name,
    )


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
    return task


async def _handle_config_reload(
    old_raw: dict[str, Any],
    new_raw: dict[str, Any],
    settings,
    test_mode: bool = False,
) -> None:
    from .helpers.hot_reload import (
        compute_reload_diff,
        compute_reload_targets,
        format_diff_summary,
    )
    from .helpers.config import CoreSettings

    diff = compute_reload_diff(old_raw, new_raw)
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

    if diff["global_changes"] and reload_target_ids:
        log(
            "  Reapplying shared runtime settings across active DVRs: "
            f"{reload_target_ids} (fields: {list(diff['global_changes'].keys())})"
        )

    for dvr_id in diff["removed_dvr_ids"]:
        log(
            f"  [{dvr_id}] stopping (DVR removed from config)", extra={"dvr_id": dvr_id}
        )
        await _stop_dvr_task(dvr_id)

    changed_ids = set(diff["changed_dvr_ids"])
    global_change_keys = list(diff["global_changes"].keys())

    for dvr_id in reload_target_ids:
        if dvr_id in changed_ids and global_change_keys:
            reason = f"DVR config + global settings changed ({global_change_keys})"
        elif dvr_id in changed_ids:
            reason = "DVR config changed"
        else:
            reason = f"global settings changed ({global_change_keys})"

        log(f"  [{dvr_id}] restarting ({reason})", extra={"dvr_id": dvr_id})
        await _stop_dvr_task(dvr_id)
        dvr = next(
            (d for d in new_settings.get_dvr_connections() if d.id == dvr_id), None
        )
        if dvr:
            monitor = await asyncio.to_thread(
                _init_dvr_monitor_sync, dvr, new_settings, test_mode
            )
            if monitor:
                await _start_verified_dvr_task(monitor)
                log(f"  [{dvr_id}] restarted as '{dvr.name}'", extra={"dvr_id": dvr_id})

    for dvr_id in diff["added_dvr_ids"]:
        dvr = next(
            (d for d in new_settings.get_dvr_connections() if d.id == dvr_id), None
        )
        if dvr:
            log(f"  [{dvr_id}] starting (new DVR added)", extra={"dvr_id": dvr_id})
            monitor = await asyncio.to_thread(
                _init_dvr_monitor_sync, dvr, new_settings, test_mode
            )
            if monitor:
                await _start_verified_dvr_task(monitor)
                log(f"  [{dvr_id}] started as '{dvr.name}'", extra={"dvr_id": dvr_id})


async def _watch_config_and_reload(
    shutdown_event: asyncio.Event,
    reload_event: asyncio.Event,
    settings,
    test_mode: bool = False,
) -> None:
    last_hash = ""
    try:
        _content, last_hash = await _read_config_snapshot_async()
    except OSError:
        pass

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

            last_hash = current_hash
            new_raw = json.loads(content.decode())
            old_raw = dict(_last_settings_raw)
            _last_settings_raw.clear()
            _last_settings_raw.update(new_raw)

            await _handle_config_reload(old_raw, new_raw, settings, test_mode)

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

    for monitor in initial_monitors:
        dvr_id = monitor.dvr.id if (monitor.dvr is not None) else monitor.dvr_name
        _watchdog.attach_monitor(monitor)
        task = asyncio.create_task(_run_dvr(monitor), name=f"dvr-{monitor.dvr_name}")
        _dvr_tasks[dvr_id] = task
        _dvr_monitors[dvr_id] = monitor

    await _persist_watchdog_async(force=True)

    watcher_task = asyncio.create_task(
        _watch_config_and_reload(shutdown_event, reload_event, settings, test_mode),
        name="config-watcher",
    )
    watchdog_task = asyncio.create_task(
        _watchdog_loop(shutdown_event), name="monitor-watchdog"
    )

    await shutdown_event.wait()

    log("Received shutdown signal, stopping monitors...")
    for monitor in list(_dvr_monitors.values()):
        _request_monitor_stop(monitor)

    watcher_task.cancel()
    watchdog_task.cancel()
    all_tasks = list(_dvr_tasks.values()) + [watcher_task, watchdog_task]
    try:
        await asyncio.wait_for(
            asyncio.gather(*all_tasks, return_exceptions=True),
            timeout=MONITOR_SHUTDOWN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        pending_names = [task.get_name() for task in all_tasks if not task.done()]
        log(
            "Timed out waiting for monitor shutdown; cancelling unfinished tasks: "
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

    if not dvr_connections:
        log(
            "Waiting for DVR server configuration. Set it in the Web UI at http://localhost:8501"
        )
        while True:
            await asyncio.sleep(3600)
            settings = get_settings()
            dvr_connections = settings.get_dvr_connections()
            if dvr_connections:
                log(
                    "Configuration detected! DVR servers configured. Please restart ChannelWatch to apply."
                )

    # TEST MODE (use first DVR)
    first_dvr = dvr_connections[0]
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
        _test_nm = initialize_notifications(_test_settings, test_mode=test_mode)
        if not _test_nm:
            from .notifications.notification import NotificationManager

            _test_nm = NotificationManager(
                rate_limit=_test_settings.global_rate_limit,
                rate_window=_test_settings.global_rate_window,
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

    for dvr in dvr_connections:
        monitor = _init_dvr_monitor_sync(dvr, settings, test_mode=test_mode)
        if monitor:
            event_monitors.append(monitor)

    if not event_monitors:
        log("No DVR servers could be connected. Waiting for configuration.")
        while not shutdown_event.is_set():
            await asyncio.sleep(3600)
        return

    for monitor in event_monitors:
        log(f"[{monitor.dvr_name}] Monitoring started")
    await _run_monitors_dynamic(
        event_monitors, settings, shutdown_event, reload_event, test_mode=False
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
