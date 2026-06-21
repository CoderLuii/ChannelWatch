"""Monitoring freshness watchdog shared by core and UI backend."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .helpers.atomic_io import atomic_write_json
from .helpers.config import CONFIG_DIR
from .helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

WATCHDOG_STATUS_FILE = CONFIG_DIR / "watchdog_status.json"
WATCHDOG_CHECK_INTERVAL_SECONDS = 30
DEFAULT_MONITOR_STALE_SECONDS = 300


def _isoformat(timestamp: Optional[float]) -> Optional[str]:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _normalize_enabled_server(server: Any) -> Optional[dict[str, Any]]:
    if not isinstance(server, dict):
        return None
    if server.get("deleted_at") or server.get("enabled", True) is False:
        return None
    return {
        "id": str(server.get("id", "") or ""),
        "name": str(server.get("name", server.get("host", "")) or ""),
        "host": str(server.get("host", "") or ""),
        "port": int(server.get("port", 8089) or 8089),
    }


def _snapshot_string(value: Any, default: Any = "") -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if value is None:
        return str(default or "")
    return str(default or "")


def _snapshot_int(value: Any, default: int = 8089) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _snapshot_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def _snapshot_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def load_watchdog_snapshot(status_file: Path = WATCHDOG_STATUS_FILE) -> dict[str, Any]:
    try:
        if not status_file.is_file():
            return {}
        import json

        payload = json.loads(status_file.read_text())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def summarize_enabled_dvrs(
    enabled_servers: list[dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    now: Optional[float] = None,
) -> dict[str, Any]:
    current_time = now or time.time()
    stale_threshold = int(
        snapshot.get("stale_threshold_seconds") or DEFAULT_MONITOR_STALE_SECONDS
    )
    snapshot_dvrs = (
        snapshot.get("dvrs") if isinstance(snapshot.get("dvrs"), list) else []
    )
    snapshot_by_id = {
        str(entry.get("id", "") or ""): entry
        for entry in snapshot_dvrs
        if isinstance(entry, dict) and entry.get("id")
    }

    results: list[dict[str, Any]] = []
    for server in enabled_servers:
        dvr_id = server["id"]
        entry = dict(snapshot_by_id.get(dvr_id, {}))
        last_freshness_ts = entry.get("last_freshness_ts")
        freshness_age_seconds = None
        if isinstance(last_freshness_ts, (int, float)) and last_freshness_ts > 0:
            freshness_age_seconds = max(0.0, current_time - float(last_freshness_ts))

        task_alive = bool(entry.get("task_alive", False))
        ready = bool(entry.get("ready", False))
        monitoring_status = str(entry.get("monitoring_status") or "missing")
        if not entry:
            monitoring_status = "missing"
            ready = False

        results.append(
            {
                "id": dvr_id,
                "name": str(entry.get("name") or server["name"]),
                "host": str(entry.get("host") or server["host"]),
                "port": int(entry.get("port") or server["port"]),
                "task_alive": task_alive,
                "task_done": bool(entry.get("task_done", not task_alive)),
                "monitor_running": bool(entry.get("monitor_running", False)),
                "connected": bool(entry.get("connected", False)),
                "alerts_paused": bool(entry.get("alerts_paused", False)),
                "connection_status": str(entry.get("connection_status") or "unknown"),
                "monitoring_status": monitoring_status,
                "ready": ready,
                "reason": str(
                    entry.get("reason") or "No freshness state published yet"
                ),
                "freshness_status": str(
                    entry.get("freshness_status") or monitoring_status
                ),
                "last_freshness_at": entry.get("last_freshness_at"),
                "last_freshness_source": entry.get("last_freshness_source"),
                "last_event_at": entry.get("last_event_at"),
                "freshness_age_seconds": freshness_age_seconds,
                "stale_threshold_seconds": stale_threshold,
                "last_seen_at": entry.get("last_seen_at"),
                "last_checked_at": entry.get("last_checked_at")
                or _isoformat(current_time),
            }
        )

    unhealthy = [entry for entry in results if not entry["ready"]]
    stale = [entry for entry in results if entry["monitoring_status"] == "stale"]
    dead = [entry for entry in results if entry["monitoring_status"] == "dead"]

    return {
        "ready": bool(results) and not unhealthy,
        "status": "ready" if results and not unhealthy else "degraded",
        "dvrs": results,
        "unhealthy": unhealthy,
        "stale": stale,
        "dead": dead,
        "stale_threshold_seconds": stale_threshold,
        "generated_at": snapshot.get("generated_at") or _isoformat(current_time),
    }


class Watchdog:
    """Tracks per-DVR task liveness and freshness and persists it for the UI."""

    def __init__(
        self,
        *,
        stale_threshold_seconds: int = DEFAULT_MONITOR_STALE_SECONDS,
        status_file: Path = WATCHDOG_STATUS_FILE,
    ) -> None:
        self.stale_threshold_seconds = max(
            1, int(stale_threshold_seconds or DEFAULT_MONITOR_STALE_SECONDS)
        )
        self.status_file = status_file
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def attach_monitor(self, monitor: Any) -> None:
        monitor.watchdog = self

    def mark_fresh(
        self, monitor: Any, source: str, *, timestamp: Optional[float] = None
    ) -> None:
        ts = float(timestamp or time.time())
        dvr = getattr(monitor, "dvr", None)
        dvr_id = str(getattr(dvr, "id", None) or getattr(monitor, "dvr_name", ""))
        if not dvr_id:
            return

        monitor.last_freshness_at = ts
        monitor.last_freshness_source = source
        if source == "event":
            monitor.last_event_at = ts

        with self._lock:
            entry = self._entries.setdefault(dvr_id, {})
            entry.update(
                {
                    "id": dvr_id,
                    "name": getattr(monitor, "dvr_name", dvr_id),
                    "host": getattr(monitor, "host", ""),
                    "port": getattr(monitor, "port", 8089),
                    "last_freshness_ts": ts,
                    "last_freshness_at": _isoformat(ts),
                    "last_freshness_source": source,
                    "last_event_at": _isoformat(
                        getattr(monitor, "last_event_at", 0.0) or 0.0
                    ),
                    "last_seen_at": _isoformat(
                        getattr(monitor, "_last_seen", 0.0) or 0.0
                    ),
                }
            )

    def remove_dvr(self, dvr_id: str) -> None:
        with self._lock:
            self._entries.pop(dvr_id, None)

    def persist(
        self,
        tasks: dict[str, Any],
        monitors: dict[str, Any],
        *,
        force: bool = False,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        snapshot = self.snapshot(tasks, monitors, now=now)
        if force or snapshot:
            try:
                self.status_file.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self.status_file, snapshot)
            except OSError as exc:
                log(
                    f"[Watchdog] Failed to write status file: {exc}", level=LOG_STANDARD
                )
        return snapshot

    def snapshot(
        self,
        tasks: dict[str, Any],
        monitors: dict[str, Any],
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        current_time = float(now or time.time())
        with self._lock:
            known_ids = set(self._entries) | set(tasks) | set(monitors)
            dvrs: list[dict[str, Any]] = []

            for dvr_id in sorted(known_ids):
                monitor = monitors.get(dvr_id)
                task = tasks.get(dvr_id)
                entry = dict(self._entries.get(dvr_id, {}))
                if monitor is not None:
                    entry_name = entry.get("name", dvr_id)
                    entry_host = entry.get("host", "")
                    entry_port = _snapshot_int(entry.get("port", 8089), 8089)
                    entry.update(
                        {
                            "id": dvr_id,
                            "name": _snapshot_string(
                                getattr(monitor, "dvr_name", None), entry_name
                            ),
                            "host": _snapshot_string(
                                getattr(monitor, "host", None), entry_host
                            ),
                            "port": _snapshot_int(
                                getattr(monitor, "port", None), entry_port
                            ),
                            "connected": _snapshot_bool(
                                getattr(monitor, "connected", None)
                            ),
                            "alerts_paused": _snapshot_bool(
                                getattr(monitor, "alerts_paused", None)
                            ),
                            "connection_status": _snapshot_string(
                                getattr(monitor, "_connection_status", None),
                                "unknown",
                            ),
                            "last_seen_at": _isoformat(
                                _snapshot_float(getattr(monitor, "_last_seen", None))
                            ),
                            "last_event_at": _isoformat(
                                _snapshot_float(
                                    getattr(monitor, "last_event_at", None)
                                )
                            ),
                            "last_freshness_source": _snapshot_string(
                                getattr(monitor, "last_freshness_source", None),
                                entry.get("last_freshness_source", ""),
                            ),
                            "monitor_running": _snapshot_bool(
                                getattr(monitor, "running", None)
                            ),
                        }
                    )
                    last_freshness_ts = _snapshot_float(
                        getattr(monitor, "last_freshness_at", None),
                        _snapshot_float(entry.get("last_freshness_ts"), 0.0),
                    )
                    if last_freshness_ts:
                        entry["last_freshness_ts"] = last_freshness_ts
                        entry["last_freshness_at"] = _isoformat(last_freshness_ts)

                task_alive = bool(task is not None and not task.done())
                task_done = bool(task is not None and task.done())
                last_freshness_ts = entry.get("last_freshness_ts")
                freshness_age_seconds = None
                stale = True
                if (
                    isinstance(last_freshness_ts, (int, float))
                    and last_freshness_ts > 0
                ):
                    freshness_age_seconds = max(
                        0.0, current_time - float(last_freshness_ts)
                    )
                    stale = freshness_age_seconds > self.stale_threshold_seconds

                if not task_alive:
                    monitoring_status = "dead"
                    reason = "Monitor task is not alive"
                elif last_freshness_ts is None:
                    monitoring_status = "starting"
                    reason = "Waiting for the first freshness update"
                elif stale:
                    monitoring_status = "stale"
                    reason = (
                        f"No freshness update for {int(freshness_age_seconds or 0)}s"
                    )
                else:
                    monitoring_status = "healthy"
                    reason = "Freshness updates are current"

                ready = bool(task_alive and last_freshness_ts is not None and not stale)
                entry.update(
                    {
                        "task_alive": task_alive,
                        "task_done": task_done,
                        "freshness_age_seconds": freshness_age_seconds,
                        "freshness_status": monitoring_status,
                        "monitoring_status": monitoring_status,
                        "ready": ready,
                        "reason": reason,
                        "last_checked_at": _isoformat(current_time),
                    }
                )
                self._entries[dvr_id] = entry
                dvrs.append(entry)

        healthy = all(entry.get("ready") for entry in dvrs) if dvrs else False
        summary = {
            "generated_at": _isoformat(current_time),
            "stale_threshold_seconds": self.stale_threshold_seconds,
            "check_interval_seconds": WATCHDOG_CHECK_INTERVAL_SECONDS,
            "healthy": healthy,
            "status": "ready" if healthy else "degraded",
            "dvrs": dvrs,
        }
        log(
            f"[Watchdog] Snapshot persisted for {len(dvrs)} DVR(s)"
            if dvrs
            else "[Watchdog] Snapshot persisted with no active DVRs",
            level=LOG_VERBOSE,
        )
        return summary
