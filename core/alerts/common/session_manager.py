"""Manages session tracking and lifecycle for alert types with thread-safe operations."""
import time
import threading
from typing import Dict, Any, Optional

from ...helpers.logging import log, LOG_VERBOSE

# SESSION MANAGER

class SessionManager:
    """Manages session tracking and lifecycle for alert types with thread-safe operations."""
    
    def __init__(self):
        """Initializes session tracking dictionaries and lock for thread safety."""
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        self.processing_events: Dict[str, float] = {}
        self.notification_history: Dict[str, float] = {}
        self.lock = threading.Lock()
        
    # SESSION MANAGEMENT
    
    def add_session(self, session_id: str, **session_data) -> None:
        """Adds or updates a session with associated data and timestamp."""
        with self.lock:
            is_existing_session = session_id in self.active_sessions
            session_data['timestamp'] = time.time()
            self.active_sessions[session_id] = session_data
            
            if not is_existing_session:
                log(f"Session added: {session_id}", LOG_VERBOSE)
            else:
                log(f"Session updated: {session_id} (data keys: {', '.join(session_data.keys())})", LOG_VERBOSE)
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves session data for a specific session ID."""
        with self.lock:
            return self.active_sessions.get(session_id)
    
    def has_session(self, session_id: str) -> bool:
        """Checks if a session exists in the active sessions dictionary."""
        with self.lock:
            return session_id in self.active_sessions
    
    def remove_session(self, session_id: str) -> None:
        """Removes a session from the active sessions dictionary if it exists."""
        with self.lock:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
                log(f"Session removed: {session_id}", LOG_VERBOSE)
    
    # EVENT PROCESSING
    
    def mark_event_processing(self, event_id: str) -> None:
        """Marks an event as being processed with current timestamp."""
        with self.lock:
            self.processing_events[event_id] = time.time()
    
    def is_event_processing(self, event_id: str) -> bool:
        """Checks if an event is currently being processed."""
        with self.lock:
            return event_id in self.processing_events
    
    def complete_event_processing(self, event_id: str) -> None:
        """Marks an event as completed processing by removing it from processing events."""
        with self.lock:
            if event_id in self.processing_events:
                del self.processing_events[event_id]
    
    # NOTIFICATION MANAGEMENT
    
    def record_notification(self, notification_key: str) -> None:
        """Records that a notification was sent with current timestamp."""
        with self.lock:
            self.notification_history[notification_key] = time.time()
    
    def was_notification_sent(self, notification_key: str, within_seconds: int = 3600) -> bool:
        """Checks if a notification was sent within the specified time window."""
        with self.lock:
            if notification_key not in self.notification_history:
                return False
            
            timestamp = self.notification_history[notification_key]
            return (time.time() - timestamp) < within_seconds
    
    # CLEANUP
    
    def cleanup(self, session_ttl: int = 14400, event_ttl: int = 300, notification_ttl: int = 86400) -> None:
        """Cleans up stale sessions, events, and notification history based on TTL values."""
        with self.lock:
            current_time = time.time()
            
            stale_sessions = [
                session_id for session_id, data in self.active_sessions.items()
                if current_time - data['timestamp'] > session_ttl
            ]
            for session_id in stale_sessions:
                log(f"Removing stale session: {session_id}", LOG_VERBOSE)
                del self.active_sessions[session_id]
            
            stale_events = [
                event_id for event_id, timestamp in self.processing_events.items()
                if current_time - timestamp > event_ttl
            ]
            for event_id in stale_events:
                log(f"Removing stale event: {event_id}", LOG_VERBOSE)
                del self.processing_events[event_id]
            
            old_notifications = [
                notif_key for notif_key, timestamp in self.notification_history.items()
                if current_time - timestamp > notification_ttl
            ]
            for notif_key in old_notifications:
                del self.notification_history[notif_key]