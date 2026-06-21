"""Base alert functionality and abstract class definition."""

import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


# BASE ALERT
class BaseAlert(ABC):
    """Base class for all alert types with required method implementations."""

    ALERT_TYPE = "BaseAlert"
    ROUTING_EVENT_TYPE: Optional[str] = None

    def __init__(self, notification_manager):
        """Initializes the alert with a notification manager."""
        self.notification_manager = notification_manager

    # EVENTS
    async def process_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        if self._is_end_event(event_type, event_data):
            session_id = event_data.get("Name", "")
            await self.process_end_event(session_id)
            return False

        if self._should_handle_event(event_type, event_data):
            return await self._handle_event(event_type, event_data)

        return False

    def _is_end_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        return (
            event_type == "activities.set"
            and "Name" in event_data
            and not event_data.get("Value", "")
        )

    @abstractmethod
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        pass

    @abstractmethod
    async def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        pass

    async def process_end_event(self, session_id: str) -> None:
        pass

    # NOTIFICATIONS
    def _build_notification_kwargs(self, image_url=None, **kwargs):
        notification_kwargs = kwargs.copy()

        if image_url is not None:
            notification_kwargs["image_url"] = image_url

        if "event_type" not in notification_kwargs and self.ROUTING_EVENT_TYPE:
            notification_kwargs["event_type"] = self.ROUTING_EVENT_TYPE

        if "dvr_id" not in notification_kwargs:
            dvr = getattr(self, "dvr", None)
            dvr_id = getattr(dvr, "id", None) if dvr is not None else None
            if dvr_id:
                notification_kwargs["dvr_id"] = dvr_id

        return notification_kwargs

    def send_alert(self, title, message, image_url=None, **kwargs):
        notification_kwargs = self._build_notification_kwargs(
            image_url=image_url, **kwargs
        )
        result = self.notification_manager.send_notification(
            title, message, **notification_kwargs
        )
        return result

    async def send_alert_async(self, title, message, image_url=None, **kwargs):
        notification_kwargs = self._build_notification_kwargs(
            image_url=image_url, **kwargs
        )
        send_async = getattr(self.notification_manager, "send_notification_async", None)
        if callable(send_async):
            return await send_async(title, message, **notification_kwargs)

        return await asyncio.to_thread(
            self.notification_manager.send_notification,
            title,
            message,
            **notification_kwargs,
        )

    # CLEANUP
    def cleanup(self) -> None:
        """Cleans up any resources used by this alert."""
        pass
