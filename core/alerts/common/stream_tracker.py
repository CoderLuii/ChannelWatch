"""Tracks active streaming sessions and maintains accurate stream count information."""
import threading
import time
from typing import Dict, Any, Set, Optional
import requests
import os
import json

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ...helpers.type_utils import ensure_str

# GLOBALS

_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()
STREAM_COUNT_FILE = "/config/stream_count.txt"

# STREAM TRACKER

class StreamTracker:
    """Tracks active streaming sessions and provides accurate count information."""
    
    # INITIALIZATION
    
    def __new__(cls, host: Optional[str], port: int):
        """Creates or returns the singleton instance."""
        global _INSTANCE
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = super(StreamTracker, cls).__new__(cls)
                _INSTANCE._initialized = False
            return _INSTANCE
    
    def __init__(self, host: Optional[str], port: int):
        """Initializes the stream tracker with connection settings and state tracking."""
        if getattr(self, '_initialized', False):
            return
            
        self.host = ensure_str(host)
        self.port = port
        self.base_url = f"http://{ensure_str(host)}:{port}"
        
        self.active_streams: Dict[str, Dict[str, Any]] = {}
        self.device_sessions: Dict[str, str] = {}
        self.lock = threading.Lock()
        
        self.last_count = 0
        
        self._initialized = True
        
        self._write_stream_count(0)
    
    # FILE MANAGEMENT
    
    def _write_stream_count(self, count: int):
        """Safely writes the current stream count to the shared file."""
        try:
            with open(STREAM_COUNT_FILE, 'w') as f:
                f.write(str(count))
        except Exception as e:
            log(f"StreamTracker: Error writing stream count to {STREAM_COUNT_FILE}: {e}", level=LOG_STANDARD)
    
    # SERVER STATUS
    
    def update_from_status(self) -> Optional[Dict[str, Any]]:
        """Gets current stream status from DVR server."""
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
    
    # DEVICE IDENTIFICATION
    
    def extract_device_name(self, activity_data: Any) -> Optional[str]:
        """Extracts device name from activity data using pattern matching."""
        if not activity_data:
            return None
            
        activity_str = str(activity_data)
        
        import re
        device_match = re.search(r"from\s+([^(:]+)", activity_str)
        if device_match:
            return device_match.group(1).strip()
            
        device_match = re.search(r"Device:\s*([^,]+)", activity_str)
        if device_match:
            return device_match.group(1).strip()
            
        return None
    
    # ACTIVITY PROCESSING
    
    def process_activity(self, activity_data: Any, session_id: str) -> bool:
        """Processes an activity event to track stream state and update counts."""
        count_changed = False
        new_count = 0
        with self.lock:
            old_count = len(self.device_sessions)
            
            activity_str = str(activity_data).lower()
            
            is_watching = ("watching" in activity_str or "recording" in activity_str) and "ch" in activity_str
            
            device_name = self.extract_device_name(activity_data)
            
            if is_watching and device_name:
                if device_name in self.device_sessions and self.device_sessions[device_name] != session_id:
                    old_session = self.device_sessions[device_name]

                
                self.active_streams[session_id] = {
                    'activity': activity_data,
                    'device': device_name,
                    'last_seen': time.time()
                }
                self.device_sessions[device_name] = session_id
            elif not is_watching:
                if session_id in self.active_streams:
                    session_data = self.active_streams[session_id]
                    device = session_data.get('device')
                    if device:
                        if device in self.device_sessions and self.device_sessions[device] == session_id:
                            self.device_sessions.pop(device, None)
                    self.active_streams.pop(session_id, None)
                    
            new_count = len(self.device_sessions)
            
            if old_count != new_count:
                count_changed = True
                self.last_count = new_count
            
        if count_changed:
            self._write_stream_count(new_count)
            
        return count_changed
    
    # STREAM COUNT
    
    def get_stream_count(self) -> int:
        """Gets current number of active streams based on unique devices."""
        with self.lock:
            return len(self.device_sessions)
    
    def get_stream_change_message(self) -> Optional[str]:
        """Gets a message describing the stream count change."""
        count = self.get_stream_count()
        if count != self.last_count:
            if count > self.last_count:
                return f"Total Streams: {count}"
            else:
                return f"Total Streams: {count}"
        return None
    
    # CLEANUP
    
    def cleanup_stale_sessions(self, max_age: int = 300) -> None:
        """Removes stale sessions that haven't been updated within the max age timeframe."""
        removed_count = 0
        new_count = 0
        current_time = time.time()
        with self.lock:
            stale_sessions = []
            devices_to_remove = []
            for session_id, session_data in list(self.active_streams.items()):
                if current_time - session_data['last_seen'] > max_age:
                    stale_sessions.append(session_id)
                    device = session_data.get('device')
                    if device and device in self.device_sessions:
                        if self.device_sessions[device] == session_id:
                             devices_to_remove.append(device)

            for session_id in stale_sessions:
                self.active_streams.pop(session_id, None)
                removed_count += 1
                
            for device in devices_to_remove:
                self.device_sessions.pop(device, None)

            new_count = len(self.device_sessions)

        if removed_count > 0:
            log(f"StreamTracker: Removed {removed_count} stale stream sessions", level=LOG_VERBOSE)
            self._write_stream_count(new_count) 