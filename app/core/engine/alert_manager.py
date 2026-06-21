"""Central system for managing alert types, registration, and event processing."""

import asyncio
import inspect
import json
import time
from typing import Dict, Any, Optional, List

from ..alerts.registry import get_alert_class
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.config import CoreSettings, CONFIG_DIR


# ALERT MANAGER
class AlertManager:
    """Manages all alert types, their registration, and event processing."""

    def __init__(self, notification_manager, settings: CoreSettings, dvr=None):
        if dvr is None:
            raise ValueError(
                "AlertManager requires explicit dvr_id; no default fallback allowed"
            )
        dvr_id = getattr(dvr, "id", None)
        if not dvr_id or dvr_id == "default":
            raise ValueError(
                "AlertManager requires explicit dvr_id; no default fallback allowed"
            )

        self.notification_manager = notification_manager
        self.settings = settings
        self.dvr = dvr
        self.alert_instances = {}
        self.cleanup_interval = 3600
        self.last_cleanup = time.time()
        self._state_lock = asyncio.Lock()
        self._notification_history: Dict[str, float] = {}

        self._state_file = CONFIG_DIR / f"session_state_{dvr_id}.json"
        self._save_interval = 30

    # BACKGROUND TASKS (started by EventMonitor._async_monitor_events)

    def create_background_tasks(self) -> list:
        return [
            asyncio.create_task(
                self._async_cleanup_loop(), name="alert-manager-cleanup"
            ),
            asyncio.create_task(
                self._async_state_save_loop(), name="alert-manager-state-save"
            ),
        ]

    async def _async_cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(60)
                current_time = time.time()
                if current_time - self.last_cleanup >= self.cleanup_interval:
                    await self._run_cleanup()
                    self.last_cleanup = current_time
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log(f"Error in cleanup loop: {e}")

    async def _async_state_save_loop(self):
        while True:
            try:
                await asyncio.sleep(self._save_interval)
                await self.save_all_state()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log(f"Error in state save loop: {e}", level=LOG_STANDARD)

    async def _run_cleanup(self):
        try:
            log("Running periodic alert data cleanup", level=LOG_VERBOSE)
            current_time = time.time()
            old_keys = [
                k
                for k, ts in self._notification_history.items()
                if current_time - ts > 3600
            ]
            for k in old_keys:
                del self._notification_history[k]
            if old_keys:
                log(
                    f"Cleaned up {len(old_keys)} old notification history entries",
                    level=LOG_VERBOSE,
                )
            for alert_type, alert_instance in self.alert_instances.items():
                try:
                    cleanup_result = alert_instance.cleanup()
                    if inspect.isawaitable(cleanup_result):
                        await cleanup_result
                except Exception as e:
                    log(f"Error cleaning up {alert_type}: {e}")
        except Exception as e:
            log(f"Error in cleanup: {e}")

    # REGISTRATION

    def register_alert(self, alert_type: str) -> bool:
        try:
            alert_class = get_alert_class(alert_type)
            if alert_class:
                instance = alert_class(self)
                self.alert_instances[alert_type] = instance
                return True
            else:
                log(f"Unknown alert type: {alert_type}")
                return False
        except Exception as e:
            log(f"Error registering {alert_type}: {e}")
            return False

    def get_registered_alerts(self) -> List[str]:
        return list(self.alert_instances.keys())

    # PROCESSING

    async def process_event(
        self, event_type: str, event_data: Dict[str, Any]
    ) -> Optional[str]:
        if event_type == "hello":
            return None

        for alert_type, alert_instance in self.alert_instances.items():
            try:
                result = await alert_instance.process_event(event_type, event_data)
                if result:
                    log(f"Alert triggered: {alert_type}", level=LOG_VERBOSE)
                    return alert_type
            except Exception as e:
                log(f"Error processing {alert_type}: {e}")

        return None

    # STATE PERSISTENCE

    def _write_state_file(self, all_state: Dict[str, Any]) -> None:
        tmp_file = self._state_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(all_state, default=str))
        tmp_file.replace(self._state_file)

    def _read_state_file(self) -> Optional[Dict[str, Any]]:
        if not self._state_file.is_file():
            return None
        return json.loads(self._state_file.read_text())

    async def save_all_state(self):
        async with self._state_lock:
            all_state = {}
            for alert_type, alert_instance in self.alert_instances.items():
                sm = getattr(alert_instance, "session_manager", None)
                if sm is not None:
                    all_state[alert_type] = await sm.get_state()

            if not all_state:
                return

            try:
                await asyncio.to_thread(self._write_state_file, all_state)
                log("Session state saved", level=LOG_VERBOSE)
            except OSError as e:
                log(f"Failed to save session state: {e}", level=LOG_STANDARD)

    async def load_all_state(self):
        async with self._state_lock:
            try:
                all_state = await asyncio.to_thread(self._read_state_file)
            except (json.JSONDecodeError, OSError) as e:
                log(f"Failed to load session state: {e}", level=LOG_STANDARD)
                return

            if all_state is None:
                log("No persisted session state found", level=LOG_VERBOSE)
                return

            for alert_type, alert_instance in self.alert_instances.items():
                sm = getattr(alert_instance, "session_manager", None)
                state = all_state.get(alert_type)
                if sm is not None and state is not None:
                    await sm.load_state(state)

            log("Session state loaded from disk", level=LOG_STANDARD)
