"""Alert system initialization and registry management."""
from typing import Dict, Any, Optional, Type
from ..helpers.logging import log
from .channel_watching import ChannelWatchingAlert
from .disk_space import DiskSpaceAlert
from .vod_watching import VODWatchingAlert
from .recording_events import RecordingEventsAlert

# ---------------- ALERT REGISTRY ----------------

ALERT_TYPES = {
    "Channel-Watching": ChannelWatchingAlert,
    "Disk-Space": DiskSpaceAlert,
    "VOD-Watching": VODWatchingAlert,
    "Recording-Events": RecordingEventsAlert,
}

# ---------------- ALERT MANAGEMENT ----------------

def get_alert_class(alert_type: str) -> Optional[Type]:
    """Retrieves the alert class for a given alert type."""
    return ALERT_TYPES.get(alert_type)

def register_alert_class(alert_type: str, alert_class: Type) -> bool:
    """Registers a new alert class in the alert type registry."""
    if alert_type in ALERT_TYPES:
        log(f"Alert type {alert_type} already registered")
        return False
    
    ALERT_TYPES[alert_type] = alert_class
    log(f"Registered alert type: {alert_type}")
    return True

def get_available_alert_types() -> Dict[str, str]:
    """Retrieves a dictionary of available alert types and their descriptions."""
    result = {}
    for alert_type, alert_class in ALERT_TYPES.items():
        if alert_class:
            result[alert_type] = getattr(alert_class, "DESCRIPTION", "No description")
    
    return result

# ---------------- EXPORTS ----------------

__all__ = [
    'BaseAlert',
    'ChannelWatchingAlert',
    'DiskSpaceAlert',
    'VODWatchingAlert',
    'RecordingEventsAlert',
    'ALERT_TYPES',
    'get_alert_class',
    'register_alert_class',
    'get_available_alert_types'
]