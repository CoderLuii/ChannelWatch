"""
Alert management system.
"""
import threading
import time
from typing import Dict, Any, Optional, List

from ..alerts import get_alert_class  # Up one level, then to alerts package
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE  # Up one level, then to helpers

class AlertManager:
    """Manages all alert types and their processing."""
    
    def __init__(self, notification_manager):
        """Initialize the alert manager."""
        self.notification_manager = notification_manager
        self.alert_instances = {}
        self.cleanup_interval = 3600  # Run cleanup once per hour
        self.last_cleanup = time.time()
        self._start_cleanup_thread()
    
    def _start_cleanup_thread(self):
        """Start a background thread to periodically clean up alert data."""
        cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        cleanup_thread.start()
    
    def _cleanup_loop(self):
        """Background loop to periodically clean up alert data."""
        while True:
            time.sleep(60)  # Check every minute
            
            current_time = time.time()
            if current_time - self.last_cleanup >= self.cleanup_interval:
                self._run_cleanup()
                self.last_cleanup = current_time
    
    def _run_cleanup(self):
        """Run cleanup on all alert instances."""
        try:
            log("Running periodic alert data cleanup", level=LOG_VERBOSE)
            for alert_type, alert_instance in self.alert_instances.items():
                try:
                    alert_instance.cleanup()
                except Exception as e:
                    log(f"Error cleaning up {alert_type}: {e}")
        except Exception as e:
            log(f"Error in cleanup: {e}")
        
    def register_alert(self, alert_type: str) -> bool:
        """Register an alert type.
        
        Args:
            alert_type: The type of alert to register
            
        Returns:
            bool: True if the alert was registered successfully, False otherwise
        """
        try:
            alert_class = get_alert_class(alert_type)
            if alert_class:
                self.alert_instances[alert_type] = alert_class(self.notification_manager)
                return True
            else:
                log(f"Unknown alert type: {alert_type}")
                return False
        except Exception as e:
            log(f"Error registering {alert_type}: {e}")
            return False
    
    def get_registered_alerts(self) -> List[str]:
        """Get a list of registered alert types.
        
        Returns:
            list: List of registered alert type names
        """
        return list(self.alert_instances.keys())

    def process_event(self, event_type: str, event_data: Dict[str, Any]) -> Optional[str]:
        """Process an event with all registered alerts.
        
        Args:
            event_type: The type of the event
            event_data: The event data dictionary
            
        Returns:
            str: The type of alert that handled the event, or None if no alert was triggered
        """
        # Skip hello events
        if event_type == "hello":
            return None
        
        # Process with each registered alert
        for alert_type, alert_instance in self.alert_instances.items():
            try:
                result = alert_instance.process_event(event_type, event_data)
                if result:
                    # Log at verbose level only to prevent duplicate logs
                    log(f"Alert triggered: {alert_type}", level=LOG_VERBOSE)
                    return alert_type
            except Exception as e:
                log(f"Error processing {alert_type}: {e}")
        
        # If no alert was triggered, return None
        return None