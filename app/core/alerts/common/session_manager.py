"""Manages session tracking and lifecycle for alert types with async-safe operations."""

import asyncio
import time
from typing import Dict, Any, Optional

from ...helpers.logging import log, LOG_VERBOSE, LOG_STANDARD

# SESSION MANAGER


class SessionManager:
    """Manages session tracking and lifecycle for alert types with async-safe operations."""

    def __init__(self):
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        self.processing_events: Dict[str, float] = {}
        self.notification_history: Dict[str, float] = {}
        self.lock = asyncio.Lock()

    # SESSION MANAGEMENT

    async def add_session(self, session_id: str, **session_data) -> None:
        async with self.lock:
            is_existing_session = session_id in self.active_sessions
            session_data["timestamp"] = time.time()
            self.active_sessions[session_id] = session_data
            if not is_existing_session:
                log(f"Session added: {session_id}", LOG_VERBOSE)
            else:
                log(
                    f"Session updated: {session_id} (data keys: {', '.join(session_data.keys())})",
                    LOG_VERBOSE,
                )

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            return self.active_sessions.get(session_id)

    async def has_session(self, session_id: str) -> bool:
        async with self.lock:
            return session_id in self.active_sessions

    async def remove_session(self, session_id: str) -> None:
        async with self.lock:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
                log(f"Session removed: {session_id}", LOG_VERBOSE)

    # EVENT PROCESSING

    async def mark_event_processing(self, event_id: str) -> None:
        async with self.lock:
            self.processing_events[event_id] = time.time()

    async def is_event_processing(self, event_id: str) -> bool:
        async with self.lock:
            return event_id in self.processing_events

    async def complete_event_processing(self, event_id: str) -> None:
        async with self.lock:
            if event_id in self.processing_events:
                del self.processing_events[event_id]

    # NOTIFICATION MANAGEMENT

    async def record_notification(self, notification_key: str) -> None:
        async with self.lock:
            self.notification_history[notification_key] = time.time()

    async def was_notification_sent(
        self, notification_key: str, within_seconds: int = 3600
    ) -> bool:
        async with self.lock:
            if notification_key not in self.notification_history:
                return False
            timestamp = self.notification_history[notification_key]
            return (time.time() - timestamp) < within_seconds

    # CLEANUP

    async def cleanup(
        self,
        session_ttl: int = 14400,
        event_ttl: int = 300,
        notification_ttl: int = 86400,
    ) -> None:
        async with self.lock:
            current_time = time.time()

            stale_sessions = [
                sid
                for sid, data in self.active_sessions.items()
                if current_time - data["timestamp"] > session_ttl
            ]
            for sid in stale_sessions:
                log(f"Removing stale session: {sid}", LOG_VERBOSE)
                del self.active_sessions[sid]

            stale_events = [
                eid
                for eid, ts in self.processing_events.items()
                if current_time - ts > event_ttl
            ]
            for eid in stale_events:
                log(f"Removing stale event: {eid}", LOG_VERBOSE)
                del self.processing_events[eid]

            old_notifications = [
                k
                for k, ts in self.notification_history.items()
                if current_time - ts > notification_ttl
            ]
            for k in old_notifications:
                del self.notification_history[k]

    # STATE PERSISTENCE

    async def get_state(self) -> Dict[str, Any]:
        async with self.lock:
            return {
                "active_sessions": dict(self.active_sessions),
                "notification_history": dict(self.notification_history),
            }

    async def load_state(
        self, state: Dict[str, Any], stale_threshold: int = 3600
    ) -> None:
        async with self.lock:
            current_time = time.time()
            loaded_sessions = 0
            stale_sessions = 0

            raw_sessions = state.get("active_sessions", {})
            for session_id, data in raw_sessions.items():
                ts = data.get("timestamp", 0)
                if current_time - ts > stale_threshold:
                    stale_sessions += 1
                    continue
                self.active_sessions[session_id] = data
                loaded_sessions += 1

            raw_history = state.get("notification_history", {})
            for key, ts in raw_history.items():
                if current_time - ts <= stale_threshold:
                    self.notification_history[key] = ts

            if loaded_sessions or stale_sessions:
                log(
                    f"State restored: {loaded_sessions} active session(s), "
                    f"{stale_sessions} stale session(s) discarded",
                    level=LOG_STANDARD,
                )
