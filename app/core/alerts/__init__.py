"""Alert system exports and registry management."""

from .registry import get_alert_class, get_available_alert_types, register_alert_class


_CLASS_EXPORTS = {
    "BaseAlert": "core.alerts.base.BaseAlert",
    "ChannelWatchingAlert": "core.alerts.channel_watching.ChannelWatchingAlert",
    "DiskSpaceAlert": "core.alerts.disk_space.DiskSpaceAlert",
    "VODWatchingAlert": "core.alerts.vod_watching.VODWatchingAlert",
    "RecordingEventsAlert": "core.alerts.recording_events.RecordingEventsAlert",
}


def __getattr__(name: str):
    if name in _CLASS_EXPORTS:
        module_name, class_name = _CLASS_EXPORTS[name].rsplit(".", 1)
        from importlib import import_module

        return getattr(import_module(module_name), class_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "BaseAlert",
    "ChannelWatchingAlert",
    "DiskSpaceAlert",
    "VODWatchingAlert",
    "RecordingEventsAlert",
    "get_alert_class",
    "register_alert_class",
    "get_available_alert_types",
]
