"""
Alert management system.
"""
import importlib
from channelwatch.alerts import get_alert_class
from channelwatch.helpers.logging import log

class AlertManager:
    """Manages all alert types and their processing."""
    
    def __init__(self, notification_manager):
        """Initialize the alert manager."""
        self.notification_manager = notification_manager
        self.alert_instances = {}
        
    def register_alert(self, alert_type):
        """Register an alert type."""
        try:
            # Get the alert class for this type
            alert_class = get_alert_class(alert_type)
            if alert_class:
                self.alert_instances[alert_type] = alert_class(self.notification_manager)
                return True
            else:
                log(f"Error: Alert type '{alert_type}' not found.")
                return False
        except Exception as e:
            log(f"Error registering alert type '{alert_type}': {e}")
            return False
    
    def process_line(self, line, timestamp=None):
        """Process a log line with all registered alerts."""
        for alert_type, alert_instance in self.alert_instances.items():
            if alert_instance.process_line(line, timestamp=timestamp):
                return alert_type
        return None