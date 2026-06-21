"""Notification provider and alert-source preview contracts for ChannelWatch.

Plugin safety rules (enforced by the loader):
  - initialize() is called with NO keyword arguments; read config from env vars.
  - send_notification() receives only the notification payload plus safe
    contextual kwargs (dvr_id, dvr_name, event_type).  No credentials or DB
    handles are ever passed to plugins.
  - PROVIDER_TYPE must be unique; collisions with built-ins are rejected.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class NotificationProvider(ABC):
    """Interface for notification service implementations (built-in and plugin)."""

    PROVIDER_TYPE: str = "BaseProvider"
    DESCRIPTION: str = "Base notification provider"

    @abstractmethod
    def initialize(self, **kwargs) -> bool:
        """Configure the provider. Plugin loader calls this with zero kwargs."""

    @abstractmethod
    def send_notification(
        self,
        title: str,
        message: str,
        image_url: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """Deliver a notification. Return True on success.

        Safe kwargs plugins may read: dvr_id, dvr_name, event_type.
        """

    @abstractmethod
    def is_configured(self) -> bool: ...


"""v1.1+ PREVIEW STUB. NOT LOADED by the v0.9 plugin runtime.

The notification plugin loader only registers NotificationProvider subclasses.
This file demonstrates the planned AlertSource interface for community feedback only.
"""


class AlertSource(ABC):
    """Stable preview interface for future alert-source plugins (planned for v1.1)."""

    SOURCE_TYPE: str = "BaseAlertSource"
    DESCRIPTION: str = "Base alert source"

    @abstractmethod
    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> bool:
        """Subscribe a callback to this source's event stream."""

    @abstractmethod
    def emit_event(self, event: dict[str, Any]) -> bool:
        """Emit a normalized alert event to subscribed callbacks."""

    @abstractmethod
    def unsubscribe(self) -> bool:
        """Disconnect from the event stream and drop active subscribers."""
