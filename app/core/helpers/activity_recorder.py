"""Helper module for recording activities directly to the history file."""

import json
import time
import datetime
import os
import uuid
import threading
from pathlib import Path
from typing import Dict, Any, Optional

from .logging import log, LOG_STANDARD, LOG_VERBOSE
from .atomic_io import atomic_write_json

# CONSTANTS
CONFIG_DIR = os.getenv("CONFIG_PATH", "/config")
HISTORY_FILE = os.path.join(CONFIG_DIR, "activity_history.json")
_history_file_lock = threading.Lock()

COOLDOWN_PERIOD = 5


def _history_file_path() -> str:
    """Return the current activity history path, honoring CONFIG_PATH."""
    config_dir = os.getenv("CONFIG_PATH")
    if config_dir:
        return os.path.join(config_dir, "activity_history.json")
    return HISTORY_FILE


def get_icon_for_activity_type(activity_type: str) -> str:
    """Return icon name based on activity type."""
    icon_map = {
        "watching_channel": "tv",
        "watching_vod": "play",
        "recording_event": "video",
        "disk_alert": "alert-circle",
        "disk_status": "alert-circle",
        "system": "cpu",
        "test_event": "bell",
    }
    return icon_map.get(activity_type, "bell")


# FILE OPERATIONS
def load_history():
    """Load the existing activity history from file."""
    history_file = _history_file_path()
    if not os.path.exists(history_file):
        return []

    try:
        with open(history_file, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        quarantined_path = quarantine_malformed_history_file()
        suffix = f"; quarantined at {quarantined_path}" if quarantined_path else ""
        log(
            f"Error parsing activity history file, starting with empty history{suffix}",
            level=LOG_STANDARD,
        )
        return []
    except Exception as e:
        log(f"Error loading activity history: {e}", level=LOG_STANDARD)
        return []


def save_history(history):
    """Save the activity history to file."""
    try:
        atomic_write_json(Path(_history_file_path()), history, indent=2)
        return True
    except Exception as e:
        log(f"Error saving activity history: {e}", level=LOG_STANDARD)
        return False


def quarantine_malformed_history_file() -> Optional[str]:
    """Move a malformed legacy activity history file aside without deleting it."""
    try:
        path = Path(_history_file_path())
        if not path.exists():
            return None
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine_path = path.with_name(f"{path.name}.corrupt-{stamp}")
        counter = 1
        while quarantine_path.exists():
            quarantine_path = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
            counter += 1
        os.replace(path, quarantine_path)
        return str(quarantine_path)
    except Exception as e:
        log(f"Error quarantining malformed activity history: {e}", level=LOG_STANDARD)
        return None


# SESSION TRACKING
def should_record_activity(
    tracking_key: str,
    notification_history: Dict[str, float],
) -> bool:
    current_time = time.time()

    if tracking_key in notification_history:
        last_notification_time = notification_history[tracking_key]
        time_since_last = current_time - last_notification_time

        if time_since_last < COOLDOWN_PERIOD:
            log(
                f"Skipping duplicate activity for {tracking_key} (cooldown: {time_since_last:.1f}s < {COOLDOWN_PERIOD}s)",
                level=LOG_VERBOSE,
            )
            return False

    notification_history[tracking_key] = current_time
    return True


def cleanup_notification_history(
    notification_history: Dict[str, float],
) -> None:
    current_time = time.time()
    keys_to_remove = [
        k for k, ts in notification_history.items() if current_time - ts > 3600
    ]

    for key in keys_to_remove:
        del notification_history[key]

    if keys_to_remove:
        log(
            f"Cleaned up {len(keys_to_remove)} old notification history entries",
            level=LOG_VERBOSE,
        )


# ACTIVITY RECORDING
def record_activity(
    activity_type: str,
    title: str,
    message: str,
    channel_name: Optional[str] = None,
    channel_number: Optional[str] = None,
    device_name: Optional[str] = None,
    device_ip: Optional[str] = None,
    program_title: Optional[str] = None,
    image_url: Optional[str] = None,
    stream_source: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    dvr_id: Optional[str] = None,
    dvr_name: Optional[str] = None,
    notification_history: Optional[Dict[str, float]] = None,
) -> bool:
    """Records an activity directly to the activity history file."""
    try:
        _history: Dict[str, float] = (
            notification_history if notification_history is not None else {}
        )

        device_identifier = device_name if device_name else device_ip
        if not device_identifier:
            device_identifier = "unknown"

        dvr_key = dvr_id or ""
        if channel_name:
            tracking_key = (
                f"{dvr_key}-{activity_type}-{channel_name}-{device_identifier}"
            )
        else:
            tracking_key = f"{dvr_key}-{activity_type}-{device_identifier}"

        if len(_history) > 100:
            cleanup_notification_history(_history)

        if not should_record_activity(tracking_key, _history):
            log(f"Skipping duplicate activity for {tracking_key}", level=LOG_VERBOSE)
            return True

        activity_id = str(uuid.uuid4())

        new_activity = {
            "id": activity_id,
            "type": activity_type,
            "title": title,
            "message": message,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "icon": get_icon_for_activity_type(activity_type),
            "channel_name": channel_name or "",
            "channel_number": channel_number or "",
            "device_name": device_name or "",
            "device_ip": device_ip or "",
            "program_title": program_title or "",
            "image_url": image_url or "",
            "stream_source": stream_source or "",
            "extra": extra or {},
            "dvr_id": dvr_id or "",
            "dvr_name": dvr_name or "",
        }

        with _history_file_lock:
            history = load_history()
            history.insert(0, new_activity)

            if len(history) > 500:
                history = history[:500]

            saved = save_history(history)

        if saved:
            log(
                f"Activity recorded directly to history file: {title} - {message}",
                level=LOG_VERBOSE,
            )
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
    device_ip: Optional[str] = None,
    image_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    dvr_id: Optional[str] = None,
    dvr_name: Optional[str] = None,
    notification_history: Optional[Dict[str, float]] = None,
) -> bool:
    """Specialized function to record VOD watching activities."""
    try:
        display_device = (
            device_name
            if device_name and device_name != "Unknown device"
            else device_ip
        )
        if not display_device:
            display_device = "Unknown device"

        activity_message = f"Watching {content_name} on {display_device}"

        return record_activity(
            activity_type="watching_vod",
            title="Watching VOD Content",
            message=activity_message,
            channel_name=content_name,
            device_name=device_name,
            device_ip=device_ip,
            program_title=content_name,
            image_url=image_url,
            extra=extra,
            dvr_id=dvr_id,
            dvr_name=dvr_name,
            notification_history=notification_history,
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
    scheduled_datetime: Optional[datetime.datetime] = None,
    image_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    dvr_id: Optional[str] = None,
    dvr_name: Optional[str] = None,
    notification_history: Optional[Dict[str, float]] = None,
) -> bool:
    """Specialized function to record recording events."""
    try:
        _history: Dict[str, float] = (
            notification_history if notification_history is not None else {}
        )

        event_type = event_type.strip()

        if event_type in ["Scheduled", "Cancelled"] and scheduled_datetime:
            formatted_date = format_scheduled_date(scheduled_datetime)
            activity_message = (
                f"{event_type}: {program_name} on {channel_name} for {formatted_date}"
            )
        else:
            activity_message = f"{event_type}: {program_name} on {channel_name}"

        dvr_key = dvr_id or ""
        tracking_key = (
            f"{dvr_key}-recording_event-{event_type}-{program_name}-{channel_name}"
        )

        activity_id = str(uuid.uuid4())

        if not should_record_activity(tracking_key, _history):
            log(
                f"Skipping duplicate recording event: {tracking_key}", level=LOG_VERBOSE
            )
            return True

        new_activity = {
            "id": activity_id,
            "type": "recording_event",
            "title": "Recording Event",
            "message": activity_message,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "icon": "video",
            "channel_name": channel_name or "",
            "program_title": program_name or "",
            "image_url": image_url or "",
            "dvr_id": dvr_id or "",
            "dvr_name": dvr_name or "",
            "extra": extra or {},
        }

        with _history_file_lock:
            history = load_history()
            history.insert(0, new_activity)

            if len(history) > 500:
                history = history[:500]

            saved = save_history(history)

        if saved:
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
    free_percentage: float,
    title: Optional[str] = None,
    message: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    dvr_id: Optional[str] = None,
    dvr_name: Optional[str] = None,
    is_test: bool = False,
    notification_history: Optional[Dict[str, float]] = None,
) -> bool:
    """Specialized function to record disk space alerts."""
    try:
        _history: Dict[str, float] = (
            notification_history if notification_history is not None else {}
        )

        activity_title = title or "Low Disk Space Warning"
        activity_message = (
            message
            or f"Low Disk Warning - {free_space} of {total_space} ({free_percentage:.1f}% free)"
        )
        path = (extra or {}).get("path", "") if isinstance(extra, dict) else ""
        dvr_identifier = dvr_id or dvr_name or path or "global"

        tracking_key = (
            f"disk_alert-{dvr_identifier}-{activity_title}-{free_percentage:.1f}"
        )

        activity_id = str(uuid.uuid4())

        if not should_record_activity(tracking_key, _history):
            log(
                f"Skipping duplicate disk status alert: {tracking_key}",
                level=LOG_VERBOSE,
            )
            return True

        new_activity: Dict[str, Any] = {
            "id": activity_id,
            "type": "disk_alert",
            "title": activity_title,
            "message": activity_message,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "icon": "alert-circle",
            "extra": extra or {},
            "dvr_id": dvr_id or "",
            "dvr_name": dvr_name or "",
        }

        if is_test:
            new_activity["is_test"] = True

        with _history_file_lock:
            history = load_history()
            history.insert(0, new_activity)

            if len(history) > 500:
                history = history[:500]

            saved = save_history(history)

        if saved:
            log(f"Disk status alert recorded: {activity_message}", level=LOG_VERBOSE)
            return True
        else:
            return False

    except Exception as e:
        log(f"Error recording disk status alert: {e}", level=LOG_STANDARD)
        return False


log(
    "Activity recorder initialized with direct file writing to activity_history.json",
    level=LOG_VERBOSE,
)
