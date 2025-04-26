"""Helper module for recording activities directly to the history file."""
import json
import time
import datetime
import os
import threading
import uuid
from typing import Dict, Any, Optional

from .logging import log, LOG_STANDARD, LOG_VERBOSE

# CONSTANTS
CONFIG_DIR = "/config"
HISTORY_FILE = os.path.join(CONFIG_DIR, "activity_history.json")

file_lock = threading.Lock()
NOTIFICATION_HISTORY = {}
SESSION_LOCK = threading.Lock()
COOLDOWN_PERIOD = 5

def get_icon_for_activity_type(activity_type: str) -> str:
    """Return icon name based on activity type."""
    icon_map = {
        "watching_channel": "tv",
        "watching_vod": "play",
        "recording_event": "video",
        "disk_alert": "alert-circle",
        "disk_status": "alert-circle",
        "system": "cpu",
        "test_event": "bell"
    }
    return icon_map.get(activity_type, "bell")

# FILE OPERATIONS
def load_history():
    """Load the existing activity history from file."""
    if not os.path.exists(HISTORY_FILE):
        return []
        
    with file_lock:
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            log("Error parsing activity history file, starting with empty history", level=LOG_STANDARD)
            return []
        except Exception as e:
            log(f"Error loading activity history: {e}", level=LOG_STANDARD)
            return []

def save_history(history):
    """Save the activity history to file."""
    with file_lock:
        try:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2)
            return True
        except Exception as e:
            log(f"Error saving activity history: {e}", level=LOG_STANDARD)
            return False

# SESSION TRACKING
def should_record_activity(tracking_key: str) -> bool:
    """Determine if we should record an activity based on session history."""
    with SESSION_LOCK:
        current_time = time.time()
        
        if tracking_key in NOTIFICATION_HISTORY:
            last_notification_time = NOTIFICATION_HISTORY[tracking_key]
            time_since_last = current_time - last_notification_time
            
            if time_since_last < COOLDOWN_PERIOD:
                log(f"Skipping duplicate activity for {tracking_key} (cooldown: {time_since_last:.1f}s < {COOLDOWN_PERIOD}s)", 
                    level=LOG_VERBOSE)
                return False
        
        NOTIFICATION_HISTORY[tracking_key] = current_time
        return True

def cleanup_notification_history():
    """Clean up old entries from the notification history."""
    with SESSION_LOCK:
        current_time = time.time()
        keys_to_remove = []
        
        for key, timestamp in NOTIFICATION_HISTORY.items():
            if current_time - timestamp > 3600:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del NOTIFICATION_HISTORY[key]
            
        if keys_to_remove:
            log(f"Cleaned up {len(keys_to_remove)} old notification history entries", level=LOG_VERBOSE)

# ACTIVITY RECORDING
def record_activity(
    activity_type: str, 
    title: str, 
    message: str,
    channel_name: Optional[str] = None,
    device_name: Optional[str] = None,
    device_ip: Optional[str] = None
) -> bool:
    """Records an activity directly to the activity history file."""
    try:
        device_identifier = device_name if device_name else device_ip
        if not device_identifier:
            device_identifier = "unknown"
            
        if channel_name:
            tracking_key = f"{activity_type}-{channel_name}-{device_identifier}"
        else:
            tracking_key = f"{activity_type}-{device_identifier}"
            
        if len(NOTIFICATION_HISTORY) > 100:
            cleanup_notification_history()
            
        if not should_record_activity(tracking_key):
            log(f"Skipping duplicate activity for {tracking_key}", level=LOG_VERBOSE)
            return True
            
        activity_id = str(uuid.uuid4())
        
        new_activity = {
            "id": activity_id,
            "type": activity_type,
            "title": title,
            "message": message,
            "timestamp": datetime.datetime.now().isoformat(),
            "icon": get_icon_for_activity_type(activity_type)
        }
        
        history = load_history()
        history.insert(0, new_activity)
        
        if len(history) > 500:
            history = history[:500]
        
        if save_history(history):
            log(f"Activity recorded directly to history file: {title} - {message}", level=LOG_VERBOSE)
            return True
        else:
            return False
            
    except Exception as e:
        log(f"Error recording activity to history file: {e}", level=LOG_STANDARD)
        return False

# SPECIALIZED RECORDERS
def record_vod_watching(
    content_name: str,
    device_name: Optional[str] = None,
    device_ip: Optional[str] = None
) -> bool:
    """Specialized function to record VOD watching activities."""
    try:
        display_device = device_name if device_name and device_name != "Unknown device" else device_ip
        if not display_device:
            display_device = "Unknown device"
            
        activity_message = f"Watching {content_name} on {display_device}"
        
        return record_activity(
            activity_type="watching_vod",
            title="Watching VOD Content",
            message=activity_message,
            channel_name=content_name,
            device_name=device_name,
            device_ip=device_ip
        )
    except Exception as e:
        log(f"Error recording VOD watching activity: {e}", level=LOG_STANDARD)
        return False

def format_scheduled_date(scheduled_datetime: datetime.datetime) -> str:
    """Format a scheduled date into a user-friendly string."""
    try:
        now = datetime.datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + datetime.timedelta(days=1)
        
        time_str = scheduled_datetime.strftime("%I:%M %p")
        
        if scheduled_datetime.date() == today.date():
            return f"Today at {time_str}"
        elif scheduled_datetime.date() == tomorrow.date():
            return f"Tomorrow at {time_str}"
        else:
            return f"{scheduled_datetime.strftime('%b %d, %Y')} at {time_str}"
    except Exception as e:
        log(f"Error formatting scheduled date: {e}", level=LOG_VERBOSE)
        return scheduled_datetime.isoformat()

def record_recording_event(
    event_type: str,
    program_name: str,
    channel_name: str,
    scheduled_datetime: Optional[datetime.datetime] = None
) -> bool:
    """Specialized function to record recording events."""
    try:
        event_type = event_type.strip()
        
        if event_type in ["Scheduled", "Cancelled"] and scheduled_datetime:
            formatted_date = format_scheduled_date(scheduled_datetime)
            activity_message = f"{event_type}: {program_name} on {channel_name} for {formatted_date}"
        else:
            activity_message = f"{event_type}: {program_name} on {channel_name}"
            
        tracking_key = f"recording_event-{event_type}-{program_name}-{channel_name}"
        
        activity_id = str(uuid.uuid4())
        
        if not should_record_activity(tracking_key):
            log(f"Skipping duplicate recording event: {tracking_key}", level=LOG_VERBOSE)
            return True
            
        new_activity = {
            "id": activity_id,
            "type": "recording_event",
            "title": "Recording Event",
            "message": activity_message,
            "timestamp": datetime.datetime.now().isoformat(),
            "icon": "video"
        }
        
        history = load_history()
        history.insert(0, new_activity)
        
        if len(history) > 500:
            history = history[:500]
        
        if save_history(history):
            log(f"Recording event recorded: {activity_message}", level=LOG_VERBOSE)
            return True
        else:
            return False
            
    except Exception as e:
        log(f"Error recording recording event: {e}", level=LOG_STANDARD)
        return False

def record_disk_status(
    free_space: str,
    total_space: str,
    used_space: str,
    free_percentage: float
) -> bool:
    """Specialized function to record disk space alerts."""
    try:
        activity_message = f"Low Disk Warning - {free_space} of {total_space} ({free_percentage:.1f}% free)"
        
        tracking_key = f"disk_alert-{free_percentage:.1f}"
        
        activity_id = str(uuid.uuid4())
        
        if not should_record_activity(tracking_key):
            log(f"Skipping duplicate disk status alert: {tracking_key}", level=LOG_VERBOSE)
            return True
            
        new_activity = {
            "id": activity_id,
            "type": "disk_alert",
            "title": "Disk Status Event",
            "message": activity_message,
            "timestamp": datetime.datetime.now().isoformat(),
            "icon": "alert-circle"
        }
        
        history = load_history()
        history.insert(0, new_activity)
        
        if len(history) > 500:
            history = history[:500]
        
        if save_history(history):
            log(f"Disk status alert recorded: {activity_message}", level=LOG_VERBOSE)
            return True
        else:
            return False
            
    except Exception as e:
        log(f"Error recording disk status alert: {e}", level=LOG_STANDARD)
        return False

log("Activity recorder initialized with direct file writing to activity_history.json", level=LOG_VERBOSE) 