"""Central system for managing alert types, registration, and event processing."""
import threading
import time
from typing import Dict, Any, Optional, List

from ..alerts import get_alert_class
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.config import CoreSettings

# ALERT MANAGER
class AlertManager:
    """Manages all alert types, their registration, and event processing."""
    
    def __init__(self, notification_manager, settings: CoreSettings):
        """Initializes manager with notification system and starts cleanup thread."""
        self.notification_manager = notification_manager
        self.settings = settings
        self.alert_instances = {}
        self.cleanup_interval = 3600
        self.last_cleanup = time.time()
        self._start_cleanup_thread()
    
    # CLEANUP
    def _start_cleanup_thread(self):
        """Starts a background daemon thread for periodic alert data cleanup."""
        cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        cleanup_thread.start()
    
    def _cleanup_loop(self):
        """Runs an infinite loop checking when to execute the next cleanup cycle."""
        while True:
            time.sleep(60)
            
            current_time = time.time()
            if current_time - self.last_cleanup >= self.cleanup_interval:
                self._run_cleanup()
                self.last_cleanup = current_time
    
    def _run_cleanup(self):
        """Executes cleanup operations on all registered alert instances."""
        try:
            log("Running periodic alert data cleanup", level=LOG_VERBOSE)
            for alert_type, alert_instance in self.alert_instances.items():
                try:
                    alert_instance.cleanup()
                except Exception as e:
                    log(f"Error cleaning up {alert_type}: {e}")
        except Exception as e:
            log(f"Error in cleanup: {e}")
        
    # REGISTRATION
    def register_alert(self, alert_type: str) -> bool:
        """Registers an alert type by instantiating its class with required dependencies."""
        try:
            alert_class = get_alert_class(alert_type)
            if alert_class:
                self.alert_instances[alert_type] = alert_class(self.notification_manager, self.settings)
                return True
            else:
                log(f"Unknown alert type: {alert_type}")
                return False
        except Exception as e:
            log(f"Error registering {alert_type}: {e}")
            return False
    
    def get_registered_alerts(self) -> List[str]:
        """Returns a list of all currently registered alert type names."""
        return list(self.alert_instances.keys())

    # PROCESSING
    def process_event(self, event_type: str, event_data: Dict[str, Any]) -> Optional[str]:
        """Processes an event through all registered alerts and returns the triggered alert type."""
        if event_type == "hello":
            return None
        
        for alert_type, alert_instance in self.alert_instances.items():
            try:
                result = alert_instance.process_event(event_type, event_data)
                if result:
                    log(f"Alert triggered: {alert_type}", level=LOG_VERBOSE)
                    return alert_type
            except Exception as e:
                log(f"Error processing {alert_type}: {e}")
        
        return None