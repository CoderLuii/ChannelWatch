"""Notification management system for ChannelWatch alerts and status updates."""

__all__ = ["NotificationManager"]


def __getattr__(name):
    if name == "NotificationManager":
        from .notification import NotificationManager

        return NotificationManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
