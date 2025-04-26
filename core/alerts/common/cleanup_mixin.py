"""Provides time-based cleanup operations for stale data across components."""
import time
import threading
from typing import Dict, Any, List

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

# CLEANUP MIXIN

class CleanupMixin:
    """Provides reusable cleanup operations for time-based data across components."""
    
    def __init__(self):
        """Initializes the cleanup mixin with default configuration settings."""
        self.cleanup_config = {
            'enabled': True,
            'interval': 3600,
            'last_cleanup': 0,
            'auto_cleanup': False,
        }
        
        self.cleanup_thread = None
        self.cleanup_running = False
        
        self.initial_cleanup_done = False
        self.last_logged_cleanup = 0
        self.log_cleanup_interval = 86400
        self.removal_threshold = 5
        
    # CONFIGURATION
    
    def configure_cleanup(self, 
                         enabled: bool = True, 
                         interval: int = 3600, 
                         auto_cleanup: bool = False) -> None:
        """Configures cleanup behavior with specified settings."""
        self.cleanup_config['enabled'] = enabled
        self.cleanup_config['interval'] = interval
        self.cleanup_config['auto_cleanup'] = auto_cleanup
        
        if auto_cleanup and not self.cleanup_thread:
            self.cleanup_running = True
            self.cleanup_thread = threading.Thread(
                target=self._auto_cleanup_thread, 
                daemon=True
            )
            self.cleanup_thread.start()
    
    def stop_cleanup(self) -> None:
        """Stops the automatic cleanup thread if running."""
        if self.cleanup_thread and self.cleanup_running:
            self.cleanup_running = False
            log("Auto-cleanup thread stopping...", LOG_VERBOSE)
    
    # THREAD MANAGEMENT
    
    def _auto_cleanup_thread(self) -> None:
        """Runs a background thread for automatic cleanup operations."""
        while self.cleanup_running:
            try:
                time.sleep(10)
                
                current_time = time.time()
                if (current_time - self.cleanup_config['last_cleanup'] >= 
                    self.cleanup_config['interval']):
                    
                    self.run_cleanup()
                    self.cleanup_config['last_cleanup'] = current_time
            except Exception as e:
                log(f"Error in auto-cleanup thread: {e}", LOG_STANDARD)
    
    # CLEANUP OPERATIONS
    
    def run_cleanup(self) -> Dict[str, Any]:
        """Runs all cleanup operations and returns statistics about the operation."""
        return {
            "start_time": time.time(),
            "component": self.__class__.__name__
        }
    
    def cleanup_dict_by_time(self, 
                           data: Dict[Any, Dict[str, Any]], 
                           ttl: int,
                           timestamp_key: str = 'timestamp') -> List[Any]:
        """Cleans up dictionary entries based on timestamp in nested dictionary."""
        removed_keys = []
        current_time = time.time()
        
        for key, value in list(data.items()):
            if timestamp_key in value:
                timestamp = value[timestamp_key]
                if current_time - timestamp > ttl:
                    del data[key]
                    removed_keys.append(key)
        
        return removed_keys
    
    def cleanup_dict_by_timestamp(self,
                                data: Dict[Any, float],
                                ttl: int) -> List[Any]:
        """Cleans up dictionary entries based on timestamp values."""
        removed_keys = []
        current_time = time.time()
        
        for key, timestamp in list(data.items()):
            if current_time - timestamp > ttl:
                del data[key]
                removed_keys.append(key)
        
        return removed_keys
        
    # LOGGING
    
    def log_cleanup_results(self, component: str, removed: Dict[str, int]) -> None:
        """Logs the results of a cleanup operation with appropriate verbosity."""
        total_removed = sum(removed.values())
        current_time = time.time()
        
        should_log = (
            not self.initial_cleanup_done or
            total_removed >= self.removal_threshold or
            (total_removed > 0 and 
             current_time - self.last_logged_cleanup >= self.log_cleanup_interval)
        )
        
        if not self.initial_cleanup_done:
            self.initial_cleanup_done = True
            
        if should_log:
            if total_removed > 0:
                items_list = ", ".join(f"{count} {item}" for item, count in removed.items() if count > 0)
                log(f"Cleanup: {component} - Removed {items_list}", LOG_VERBOSE)
            elif not self.initial_cleanup_done:
                log(f"Cleanup: {component} initialized - nothing to clean up", LOG_VERBOSE)
                
            self.last_logged_cleanup = current_time

    def start_auto_cleanup(self, interval: int = 60) -> None:
        """Starts a background thread for executing automatic cleanup operations."""
        if hasattr(self, '_cleanup_thread') and self._cleanup_thread and self._cleanup_thread.is_alive():
            return
            
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, args=(interval,), daemon=True)
        self._cleanup_thread.start()