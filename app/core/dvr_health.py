"""Durable transition state for DVR unreachable and recovery alerts."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.helpers.atomic_io import atomic_write_private_json, read_regular_file_bytes

STATE_SCHEMA = 1
STARTUP_GRACE_SECONDS = 300
MAX_STATE_BYTES = 256 * 1024


@dataclass(frozen=True)
class DvrHealthTransition:
    event: str
    outage_id: str
    notification_armed: bool = False


class DvrHealthTracker:
    """Turn existing watchdog health into one outage and one recovery event."""

    def __init__(
        self,
        *,
        config_dir: Path,
        dvr_id: str,
        process_started_at: float,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.dvr_id = str(dvr_id)
        self.process_started_at = float(process_started_at)
        self.now = now
        digest = hashlib.sha256(self.dvr_id.encode("utf-8")).hexdigest()[:20]
        self.path = (
            Path(config_dir)
            / "channelwatch-runtime"
            / f"dvr-health-{digest}.json"
        )
        self._lock = threading.RLock()
        self._load_blocked = False
        self._state = self._load()

    def _empty(self) -> dict[str, Any]:
        return {
            "schema": STATE_SCHEMA,
            "dvr_id": self.dvr_id,
            "unavailable_since": None,
            "outage_alerted": False,
            "outage_id": None,
            "notification_armed": False,
            "last_healthy_at": None,
            "startup_outage": False,
        }

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(
                read_regular_file_bytes(self.path, max_bytes=MAX_STATE_BYTES).decode(
                    "utf-8"
                )
            )
        except FileNotFoundError:
            return self._empty()
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            self._load_blocked = True
            return self._empty()
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != STATE_SCHEMA
            or payload.get("dvr_id") != self.dvr_id
        ):
            self._load_blocked = True
            return self._empty()
        return payload

    def _save(self) -> None:
        if self._load_blocked:
            raise RuntimeError(
                "Existing DVR health state needs recovery before it can be replaced."
            )
        atomic_write_private_json(self.path, self._state, sort_keys=True)

    def evaluate(
        self,
        *,
        healthy: bool,
        delay_seconds: int,
    ) -> DvrHealthTransition | None:
        current = self.now()
        delay = max(30, int(delay_seconds or 120))
        with self._lock:
            if healthy:
                if self._state.get("outage_alerted"):
                    outage_id = str(self._state.get("outage_id") or "unknown")
                    notification_armed = bool(
                        self._state.get("notification_armed", False)
                    )
                    self._state.update(
                        {
                            "unavailable_since": None,
                            "outage_alerted": False,
                            "outage_id": None,
                            "notification_armed": False,
                            "last_healthy_at": current,
                            "startup_outage": False,
                        }
                    )
                    self._save()
                    return DvrHealthTransition(
                        "recovered", outage_id, notification_armed
                    )
                if self._state.get("unavailable_since") is not None:
                    self._state["unavailable_since"] = None
                    self._state["outage_id"] = None
                    self._state["notification_armed"] = False
                    self._state["last_healthy_at"] = current
                    self._state["startup_outage"] = False
                    self._save()
                elif not isinstance(
                    self._state.get("last_healthy_at"), (int, float)
                ):
                    # Remember that this DVR was reachable after process
                    # startup. A later outage should use the configured delay,
                    # not the special five-minute grace reserved for a DVR
                    # that was already unavailable when ChannelWatch started.
                    self._state["last_healthy_at"] = current
                    self._state["startup_outage"] = False
                    self._save()
                return None

            if self._state.get("outage_alerted"):
                return None
            unavailable_since = self._state.get("unavailable_since")
            if not isinstance(unavailable_since, (int, float)):
                unavailable_since = current
                self._state["unavailable_since"] = current
                self._state["outage_id"] = str(int(current * 1000))
                self._state["notification_armed"] = False
                self._state["startup_outage"] = not isinstance(
                    self._state.get("last_healthy_at"), (int, float)
                )
                self._save()
                return None

            eligible_at = float(unavailable_since) + delay
            startup_outage = self._state.get("startup_outage")
            if startup_outage is True or (
                startup_outage is None
                and not isinstance(self._state.get("last_healthy_at"), (int, float))
            ):
                eligible_at = max(
                    eligible_at,
                    self.process_started_at + STARTUP_GRACE_SECONDS,
                )
            if current < eligible_at:
                return None
            outage_id = str(self._state.get("outage_id") or int(current * 1000))
            self._state["outage_alerted"] = True
            self._state["outage_id"] = outage_id
            self._state["notification_armed"] = False
            self._save()
            return DvrHealthTransition("unreachable", outage_id)

    def set_notification_armed(self, outage_id: str, armed: bool) -> None:
        """Record whether the unreachable notification entered delivery.

        Recovery delivery is paired only with an outage notification that was
        actually accepted by a notification manager.  Activity history still
        records both transitions regardless of notification configuration.
        """

        with self._lock:
            if not self._state.get("outage_alerted"):
                return
            if str(self._state.get("outage_id") or "") != str(outage_id):
                return
            next_value = bool(armed)
            if bool(self._state.get("notification_armed", False)) == next_value:
                return
            self._state["notification_armed"] = next_value
            self._save()

    def reset(self) -> None:
        with self._lock:
            self._state = self._empty()
            self._save()
