"""
Base alert functionality.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..helpers.logging import log  # Up one level, then to helpers

class BaseAlert(ABC):
    """Base class for all alerts.
    
    This serves as the foundation for all alert types. Each alert type should
    inherit from this class and implement the required methods.
    """
    
    # Alert type name - override in subclass
    ALERT_TYPE = "BaseAlert"
    
    def __init__(self, notification_manager):
        """Initialize the alert."""
        self.notification_manager = notification_manager
        
    def process_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Process an event from the Channels DVR server.
        
        This method handles the high-level event processing flow:
        1. Check if event should be handled by this alert type
        2. Process the event if it should be handled
        3. Handle potential session end events
        
        Args:
            event_type: The type of the event
            event_data: The event data dictionary
            
        Returns:
            bool: True if the event was handled, False otherwise
        """
        # First check if this is an end event
        if self._is_end_event(event_type, event_data):
            session_id = event_data.get("Name", "")
            self.process_end_event(session_id)
            return False
            
        # Then check if this is an event we should handle
        if self._should_handle_event(event_type, event_data):
            return self._handle_event(event_type, event_data)
            
        return False
    
    def _is_end_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Check if this is an event that indicates a session ending.
        
        Args:
            event_type: The type of the event
            event_data: The event data dictionary
            
        Returns:
            bool: True if this is an end event, False otherwise
        """
        return (event_type == "activities.set" and 
                "Name" in event_data and 
                not event_data.get("Value", ""))
    
    @abstractmethod
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determine if this alert should handle the given event.
        
        Args:
            event_type: The type of the event
            event_data: The event data dictionary
            
        Returns:
            bool: True if this alert should handle the event, False otherwise
        """
        pass
        
    @abstractmethod
    def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Handle an event that should be processed by this alert.
        
        Args:
            event_type: The type of the event
            event_data: The event data dictionary
            
        Returns:
            bool: True if the event was handled successfully, False otherwise
        """
        pass
    
    def process_end_event(self, session_id: str) -> None:
        """Process an event that indicates a session has ended.
        
        Args:
            session_id: The ID of the session that ended
        """
        # Default implementation does nothing - override in subclass if needed
        pass
    
    def send_alert(self, title, message, image_url=None, **kwargs):
        """Send an alert through the notification manager."""
        # Create a new kwargs dict to avoid modifying the passed one
        notification_kwargs = kwargs.copy()
        
        # Add image_url to kwargs if provided
        if image_url is not None:
            notification_kwargs['image_url'] = image_url
        
        # Call notification manager with only title, message, and kwargs
        return self.notification_manager.send_notification(title, message, **notification_kwargs)
    
    def cleanup(self) -> None:
        """Clean up any resources used by this alert.
        
        This method is called periodically to clean up stale data and prevent memory leaks.
        Override in subclass if needed.
        """
        pass