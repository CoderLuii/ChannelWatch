"""
Stream tracking functionality for monitoring active viewing sessions.
"""
import threading
import time
from typing import Dict, Any, Set, Optional
import requests

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

class StreamTracker:
    """
    Tracks active streaming sessions and provides accurate count information.
    
    The StreamTracker maintains a list of active streams across devices, handling
    new sessions, session switches, and session termination. It distinguishes between
    different devices to prevent over-counting when users switch channels.
    
    Attributes:
        host: The Channels DVR host address
        port: The Channels DVR port number
        active_streams: Dictionary of active stream sessions with metadata
        device_sessions: Mapping of device names to their current session IDs
        last_count: The last known stream count for change detection
    """
    
    def __init__(self, host: str, port: int):
        """
        Initialize the stream tracker.
        
        Args:
            host: The Channels DVR host
            port: The Channels DVR port
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        
        # Track active streams by session ID
        self.active_streams: Dict[str, Dict[str, Any]] = {}
        # Track by device name to avoid duplicate counts
        self.device_sessions: Dict[str, str] = {}
        self.lock = threading.Lock()
        
        # Last known total count
        self.last_count = 0
        
    def update_from_status(self) -> Optional[Dict[str, Any]]:
        """
        Get current stream status from DVR server.
        
        Makes an API call to the Channels DVR server to retrieve the current status,
        including client information.
        
        Returns:
            Dict with current status or None if request fails
        """
        try:
            response = requests.get(f"{self.base_url}/dvr", timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                log(f"Failed to get stream status: HTTP {response.status_code}", level=LOG_STANDARD)
                return None
        except Exception as e:
            log(f"Error getting stream status: {e}", level=LOG_STANDARD)
            return None
    
    def extract_device_name(self, activity_data: Any) -> Optional[str]:
        """
        Extract device name from activity data.
        
        Parses the activity data string to find and extract the device name.
        Tries multiple pattern matching strategies to handle different formats.
        
        Args:
            activity_data: The activity data string or object
            
        Returns:
            str: Device name or None if not found
        """
        if not activity_data:
            return None
            
        activity_str = str(activity_data)
        
        # Try to find "from Device" pattern
        import re
        device_match = re.search(r"from\s+([^(:]+)", activity_str)
        if device_match:
            return device_match.group(1).strip()
            
        # Try to find device after "Device:"
        device_match = re.search(r"Device:\s*([^,]+)", activity_str)
        if device_match:
            return device_match.group(1).strip()
            
        return None
            
    def process_activity(self, activity_data: Any, session_id: str) -> bool:
        """
        Process an activity event to track stream state.
        
        This is the core method for stream tracking. It analyzes activity events,
        updates stream counts, and tracks which device is watching what.
        The method intelligently handles:
        
        1. New viewing sessions
        2. Device switching from one stream to another
        3. Stream termination events
        4. Duplicate events for the same session
        
        Args:
            activity_data: The activity data containing information about what's being watched
                          Usually a string with format "Watching ch123 NAME from DEVICE: stats..."
            session_id: Unique identifier for this viewing session
            
        Returns:
            bool: True if stream count changed, False otherwise
        """
        with self.lock:
            # Get current status to compare
            old_count = len(self.device_sessions)
            
            # Convert to string for easier processing
            activity_str = str(activity_data).lower()
            is_watching = "watching" in activity_str and "ch" in activity_str
            
            # Try to extract device name
            device_name = self.extract_device_name(activity_data)
            
            # Debug log
            if is_watching:
                log(f"StreamTracker: Processing watching activity for session {session_id[:10]}... device: {device_name}", LOG_VERBOSE)
            
            if is_watching and device_name:
                # If this device already has a different session, remove the old one
                if device_name in self.device_sessions and self.device_sessions[device_name] != session_id:
                    old_session = self.device_sessions[device_name]
                    log(f"StreamTracker: Device {device_name} switching from session {old_session[:10]}... to {session_id[:10]}...", LOG_VERBOSE)
                    # Remove the old session
                    self.active_streams.pop(old_session, None)
                
                # Add/update stream for this device and session
                self.active_streams[session_id] = {
                    'activity': activity_data,
                    'device': device_name,
                    'last_seen': time.time()
                }
                # Track which session belongs to this device
                self.device_sessions[device_name] = session_id
            elif not is_watching:
                # This is not a watching event, remove this session if it exists
                if session_id in self.active_streams:
                    session_data = self.active_streams[session_id]
                    device = session_data.get('device')
                    if device:
                        # Remove the device mapping if it matches this session
                        if device in self.device_sessions and self.device_sessions[device] == session_id:
                            self.device_sessions.pop(device, None)
                            log(f"StreamTracker: Removed device {device} with session {session_id[:10]}...", LOG_VERBOSE)
                    # Remove the session
                    self.active_streams.pop(session_id, None)
                    
            # Re-calculate new count based on unique devices
            new_count = len(self.device_sessions)
            
            # Log the details of current streams in verbose mode
            if old_count != new_count:
                log(f"StreamTracker: Count changed from {old_count} to {new_count}", LOG_VERBOSE)
                if len(self.device_sessions) > 0:
                    devices = ", ".join(self.device_sessions.keys())
                    log(f"StreamTracker: Active devices: {devices}", LOG_VERBOSE)
            
            # Check if count changed
            if new_count != old_count:
                self.last_count = new_count
                return True
                
            return False
            
    def get_stream_count(self) -> int:
        """
        Get current number of active streams.
        
        Returns the count of unique devices with active streams.
        
        Returns:
            int: Number of active streams
        """
        with self.lock:
            # Return count based on unique devices
            return len(self.device_sessions)
            
    def cleanup_stale_sessions(self, max_age: int = 300) -> None:
        """
        Remove stale sessions that haven't been updated.
        
        Sessions older than max_age seconds are considered stale and removed.
        Default of 300 seconds (5 minutes) provides a balance between promptly
        cleaning up ended sessions and avoiding premature cleanup of actual streams.
        
        Args:
            max_age: Maximum age in seconds before a session is considered stale (default: 5 minutes)
        """
        current_time = time.time()
        with self.lock:
            # Find stale sessions
            stale_sessions = []
            for session_id, session_data in self.active_streams.items():
                if current_time - session_data['last_seen'] > max_age:
                    stale_sessions.append(session_id)
                    
                    # Also note the device to remove from device tracking
                    device = session_data.get('device')
                    if device and device in self.device_sessions:
                        if self.device_sessions[device] == session_id:
                            self.device_sessions.pop(device, None)
            
            # Remove stale sessions
            for session_id in stale_sessions:
                self.active_streams.pop(session_id, None)
                
            if stale_sessions:
                log(f"StreamTracker: Removed {len(stale_sessions)} stale stream sessions", level=LOG_VERBOSE)
                
    def get_stream_change_message(self) -> Optional[str]:
        """
        Get a message describing the stream count change.
        
        Creates a standardized message format for notification when
        stream count changes.
        
        Returns:
            str: Message about stream count change or None if no change
        """
        count = self.get_stream_count()
        if count != self.last_count:
            if count > self.last_count:
                return f"Total Streams: {count}"
            else:
                return f"Total Streams: {count}"
        return None 