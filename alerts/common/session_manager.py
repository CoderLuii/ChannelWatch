"""
Session and event tracking functionality for alerts.
"""
import time
import threading
from typing import Dict, Any, Optional

from ...helpers.logging import log, LOG_VERBOSE

class SessionManager:
    """
    Manages session tracking and lifecycle for alert types.
    
    This class provides a thread-safe way to track viewing sessions, processing
    events, and notification history. It's a core component for ensuring alerts
    aren't sent redundantly and that session state is properly maintained.
    
    Key features:
    - Thread-safe session tracking with locking
    - Cooldown periods for notifications to prevent alert flooding
    - Automatic cleanup of stale data
    - Tracking of which events are currently being processed
    
    Attributes:
        active_sessions: Dictionary mapping session IDs to session data
        processing_events: Dictionary of events currently being processed
        notification_history: Dictionary tracking sent notifications with timestamps
        lock: Threading lock for thread-safe operations
    """
    
    def __init__(self):
        """Initialize session tracking dictionaries and lock for thread safety."""
        # Active sessions with their associated data
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        # Events currently being processed with timestamps
        self.processing_events: Dict[str, float] = {}
        # History of sent notifications with timestamps
        self.notification_history: Dict[str, float] = {}
        # Lock to prevent concurrent modifications
        self.lock = threading.Lock()
        
    def add_session(self, session_id: str, **session_data) -> None:
        """
        Add or update a session with associated data.
        
        This method is used to track new viewing sessions or update existing ones.
        It automatically adds a timestamp to the session data for later cleanup.
        
        Args:
            session_id: Unique identifier for the session (usually from Channels DVR)
            **session_data: Any key-value data to store with the session (e.g., channel info, device info)
        """
        with self.lock:
            session_data['timestamp'] = time.time()
            self.active_sessions[session_id] = session_data
            log(f"Session added/updated: {session_id}", LOG_VERBOSE)
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session data for a specific session ID.
        
        Retrieves all metadata associated with a viewing session.
        Used to retrieve information about ongoing sessions.
        
        Args:
            session_id: The session ID to retrieve
            
        Returns:
            Dict containing session data or None if not found
        """
        with self.lock:
            return self.active_sessions.get(session_id)
    
    def has_session(self, session_id: str) -> bool:
        """
        Check if a session exists.
        
        Quick check to determine if a session is currently being tracked.
        Used to determine if an event belongs to a new or existing session.
        
        Args:
            session_id: The session ID to check
            
        Returns:
            True if the session exists, False otherwise
        """
        with self.lock:
            return session_id in self.active_sessions
    
    def remove_session(self, session_id: str) -> None:
        """
        Remove a session if it exists.
        
        Used when a viewing session has ended to clean up its tracking data.
        Safe to call even if the session doesn't exist.
        
        Args:
            session_id: The session ID to remove
        """
        with self.lock:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
                log(f"Session removed: {session_id}", LOG_VERBOSE)
    
    def mark_event_processing(self, event_id: str) -> None:
        """
        Mark an event as being processed.
        
        Used to prevent duplicate processing of the same event.
        Especially important for concurrent event handling.
        
        Args:
            event_id: Unique identifier for the event (typically channel-device combination)
        """
        with self.lock:
            self.processing_events[event_id] = time.time()
    
    def is_event_processing(self, event_id: str) -> bool:
        """
        Check if an event is currently being processed.
        
        Prevents multiple handlers from processing the same event simultaneously.
        Also prevents duplicate notifications for the same event within a short time.
        
        Args:
            event_id: The event ID to check
            
        Returns:
            True if the event is being processed, False otherwise
        """
        with self.lock:
            return event_id in self.processing_events
    
    def complete_event_processing(self, event_id: str) -> None:
        """
        Mark an event as completed processing.
        
        Should be called after event processing is complete to allow
        future identical events to be processed.
        
        Args:
            event_id: The event ID to complete
        """
        with self.lock:
            if event_id in self.processing_events:
                del self.processing_events[event_id]
    
    def record_notification(self, notification_key: str) -> None:
        """
        Record that a notification was sent to prevent duplicates.
        
        Stores a timestamp for when a notification was sent, allowing
        cooldown periods to prevent notification flooding.
        
        Args:
            notification_key: Unique key for the notification (typically channel-device combination)
        """
        with self.lock:
            self.notification_history[notification_key] = time.time()
    
    def was_notification_sent(self, notification_key: str, within_seconds: int = 3600) -> bool:
        """
        Check if a notification was sent within the specified time window.
        
        Used to implement cooldown periods to prevent alert flooding.
        Default of 1 hour prevents repeated alerts for the same channel/device.
        
        Args:
            notification_key: The notification key to check
            within_seconds: Time window in seconds (default: 1 hour)
            
        Returns:
            True if notification was sent within the time window
        """
        with self.lock:
            if notification_key not in self.notification_history:
                return False
            
            timestamp = self.notification_history[notification_key]
            return (time.time() - timestamp) < within_seconds
    
    def cleanup(self, session_ttl: int = 14400, event_ttl: int = 300, notification_ttl: int = 86400) -> None:
        """
        Clean up stale sessions, events, and notification history.
        
        Removes old data to prevent memory leaks and maintain performance.
        The TTL values are chosen based on real-world usage patterns:
        
        - session_ttl (4 hours): Long enough for extended viewing sessions, but will
          eventually clean up forgotten/abandoned sessions
        - event_ttl (5 minutes): Events should complete processing within seconds,
          so 5 minutes is conservative to prevent stuck events from blocking future processing
        - notification_ttl (24 hours): Keeps a full day of notification history for
          reference while still eventually cleaning up old history
        
        Args:
            session_ttl: Time-to-live for sessions in seconds (default: 4 hours)
            event_ttl: Time-to-live for events in seconds (default: 5 minutes)
            notification_ttl: Time-to-live for notifications in seconds (default: 24 hours)
        """
        with self.lock:
            current_time = time.time()
            
            # Clean up stale sessions
            stale_sessions = [
                session_id for session_id, data in self.active_sessions.items()
                if current_time - data['timestamp'] > session_ttl
            ]
            for session_id in stale_sessions:
                log(f"Removing stale session: {session_id}", LOG_VERBOSE)
                del self.active_sessions[session_id]
            
            # Clean up stale processing events
            stale_events = [
                event_id for event_id, timestamp in self.processing_events.items()
                if current_time - timestamp > event_ttl
            ]
            for event_id in stale_events:
                log(f"Removing stale event: {event_id}", LOG_VERBOSE)
                del self.processing_events[event_id]
            
            # Clean up old notification history
            old_notifications = [
                notif_key for notif_key, timestamp in self.notification_history.items()
                if current_time - timestamp > notification_ttl
            ]
            for notif_key in old_notifications:
                del self.notification_history[notif_key]