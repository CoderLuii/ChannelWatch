"""Alert registry with lazy imports for built-in alert classes."""

from importlib import import_module
from typing import Dict, Optional, Type

from ..helpers.logging import log


_BUILTIN_ALERTS = {
    "Channel-Watching": "core.alerts.channel_watching.ChannelWatchingAlert",
    "Disk-Space": "core.alerts.disk_space.DiskSpaceAlert",
    "VOD-Watching": "core.alerts.vod_watching.VODWatchingAlert",
    "Recording-Events": "core.alerts.recording_events.RecordingEventsAlert",
}

_REGISTERED_ALERTS: Dict[str, Type] = {}


def _load_class(path: str) -> Type:
    module_name, class_name = path.rsplit(".", 1)
    module = import_module(module_name)
    return getattr(module, class_name)


def get_alert_class(alert_type: str) -> Optional[Type]:
    """Return the alert class registered for an alert type."""
    if alert_type in _REGISTERED_ALERTS:
        return _REGISTERED_ALERTS[alert_type]

    path = _BUILTIN_ALERTS.get(alert_type)
    if path is None:
        return None
    return _load_class(path)


def register_alert_class(alert_type: str, alert_class: Type) -> bool:
    """Register a custom alert class."""
    if alert_type in _BUILTIN_ALERTS or alert_type in _REGISTERED_ALERTS:
        log(f"Alert type {alert_type} already registered")
        return False

    _REGISTERED_ALERTS[alert_type] = alert_class
    log(f"Registered alert type: {alert_type}")
    return True


def get_available_alert_types() -> Dict[str, str]:
    """Return available alert types and descriptions."""
    result = {}
    for alert_type in [*_BUILTIN_ALERTS.keys(), *_REGISTERED_ALERTS.keys()]:
        alert_class = get_alert_class(alert_type)
        if alert_class:
            result[alert_type] = getattr(alert_class, "DESCRIPTION", "No description")
    return result
