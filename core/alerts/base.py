"""Base alert functionality and abstract class definition."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from core.notifications.notification import NotificationManager

# BASE ALERT
class BaseAlert(ABC):
    """Base class for all alert types with required method implementations."""
    
    ALERT_TYPE = "BaseAlert"
    
    def __init__(self, notification_manager):
        """Initializes the alert with a notification manager."""
        self.notification_manager = notification_manager
        
    # EVENTS
    def process_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Processes an event from the Channels DVR server and determines if it should be handled."""
        if self._is_end_event(event_type, event_data):
            session_id = event_data.get("Name", "")
            self.process_end_event(session_id)
            return False
            
        if self._should_handle_event(event_type, event_data):
            return self._handle_event(event_type, event_data)
            
        return False
    
    def _is_end_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determines if an event indicates a session ending."""
        return (event_type == "activities.set" and 
                "Name" in event_data and 
                not event_data.get("Value", ""))
    
    @abstractmethod
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determines if this alert should handle the given event."""
        pass
        
    @abstractmethod
    def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Handles an event that should be processed by this alert."""
        pass
    
    def process_end_event(self, session_id: str) -> None:
        """Processes an event that indicates a session has ended."""
        pass
    
    # NOTIFICATIONS
    def send_alert(self, title, message, image_url=None, **kwargs):
        """Sends an alert through the notification manager."""
        notification_kwargs = kwargs.copy()
        
        if image_url is not None:
            notification_kwargs['image_url'] = image_url
        
        result = self.notification_manager.send_notification(title, message, **notification_kwargs)
        return result
    
    # CLEANUP
    def cleanup(self) -> None:
        """Cleans up any resources used by this alert."""
        pass