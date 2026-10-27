"""Keep diagnostic samples separate from live monitoring state."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps


_diagnostic_active = ContextVar("diagnostic_active", default=False)


def is_diagnostic_execution() -> bool:
    return _diagnostic_active.get()


@contextmanager
def diagnostic_execution():
    token = _diagnostic_active.set(True)
    try:
        yield
    finally:
        _diagnostic_active.reset(token)


def _isolated_manager(manager):
    from copy import copy
    from ..engine.alert_manager import AlertManager
    from ..notifications.notification import NotificationManager, DIAGNOSTIC_DEADLINE_SECONDS

    if not isinstance(manager, AlertManager):
        return manager

    settings = copy(manager.settings)
    settings.stream_count = False
    original_notifications = manager.notification_manager
    notifications = NotificationManager(
        diagnostic_mode=True,
        diagnostic_deadline_seconds=getattr(
            original_notifications, "diagnostic_deadline_seconds", DIAGNOSTIC_DEADLINE_SECONDS
        ),
    )
    if original_notifications is not None:
        notifications.providers = original_notifications.providers.copy()
        notifications.webhook_manager = original_notifications.webhook_manager
    isolated = AlertManager(notifications, settings, dvr=manager.dvr)
    isolated.alert_instances = {
        key: type(alert)(isolated) for key, alert in manager.alert_instances.items()
    }
    return isolated


def diagnostic_sample(function):
    @wraps(function)
    async def wrapped(host, port, alert_manager):
        if is_diagnostic_execution():
            return await function(host, port, alert_manager)
        with diagnostic_execution():
            return await function(host, port, _isolated_manager(alert_manager))

    return wrapped
