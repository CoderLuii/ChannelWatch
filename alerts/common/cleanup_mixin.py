"""
Time-based cleanup operations for stale data.

This module provides a mixin class that can be used to implement 
consistent cleanup operations for time-based data across different components.
"""
import time
import threading
from typing import Dict, Any, List

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

class CleanupMixin:
    """
    Mixin class providing reusable cleanup operations for time-based data.
    
    This mixin can be added to any class that needs to manage stale data,
    providing consistent cleanup behavior across different components.
    """
    
    def __init__(self):
        """Initialize the cleanup mixin."""
        # Cleanup configuration with default TTLs
        self.cleanup_config = {
            'enabled': True,           # Whether automatic cleanup is enabled
            'interval': 3600,          # Run cleanup every hour by default
            'last_cleanup': 0,         # Timestamp of last cleanup
            'auto_cleanup': False,     # Whether to run cleanup automatically
        }
        
        # Auto-cleanup thread
        self.cleanup_thread = None
        self.cleanup_running = False
        
        # Logging control
        self.initial_cleanup_done = False  # Track if first cleanup has happened
        self.last_logged_cleanup = 0       # When we last logged a cleanup
        self.log_cleanup_interval = 86400  # Only log cleanups once per day by default
        self.removal_threshold = 5         # Only log if more than this many items were removed
        
    def configure_cleanup(self, 
                         enabled: bool = True, 
                         interval: int = 3600, 
                         auto_cleanup: bool = False) -> None:
        """
        Configure cleanup behavior.
        
        Args:
            enabled: Whether cleanup is enabled
            interval: Interval between automatic cleanups in seconds
            auto_cleanup: Whether to run cleanup automatically in background
        """
        self.cleanup_config['enabled'] = enabled
        self.cleanup_config['interval'] = interval
        self.cleanup_config['auto_cleanup'] = auto_cleanup
        
        # Start auto-cleanup thread if requested
        if auto_cleanup and not self.cleanup_thread:
            self.cleanup_running = True
            self.cleanup_thread = threading.Thread(
                target=self._auto_cleanup_thread, 
                daemon=True
            )
            self.cleanup_thread.start()
            log(f"Auto-cleanup thread started with interval {interval}s", LOG_VERBOSE)
    
    def stop_cleanup(self) -> None:
        """Stop the automatic cleanup thread if running."""
        if self.cleanup_thread and self.cleanup_running:
            self.cleanup_running = False
            # Thread will terminate on next interval check
            log("Auto-cleanup thread stopping...", LOG_VERBOSE)
    
    def _auto_cleanup_thread(self) -> None:
        """Background thread for automatic cleanup."""
        while self.cleanup_running:
            try:
                # Sleep for a bit to avoid busy waiting
                time.sleep(10)
                
                # Check if it's time to run cleanup
                current_time = time.time()
                if (current_time - self.cleanup_config['last_cleanup'] >= 
                    self.cleanup_config['interval']):
                    
                    # Run the cleanup - logging handled in run_cleanup and log_cleanup_results
                    self.run_cleanup()
                    self.cleanup_config['last_cleanup'] = current_time
            except Exception as e:
                log(f"Error in auto-cleanup thread: {e}", LOG_STANDARD)
    
    def run_cleanup(self) -> Dict[str, Any]:
        """
        Run all cleanup operations.
        
        This method should be overridden by subclasses to call specific
        cleanup methods based on their needs.
        
        Returns:
            Dict with statistics about the cleanup operation
        """
        # Base implementation that returns basic stats
        return {
            "start_time": time.time(),
            "component": self.__class__.__name__
        }
    
    def cleanup_dict_by_time(self, 
                           data: Dict[Any, Dict[str, Any]], 
                           ttl: int,
                           timestamp_key: str = 'timestamp') -> List[Any]:
        """
        Clean up dictionary entries based on timestamp in nested dictionary.
        
        Args:
            data: Dictionary of dictionaries, where each value contains a timestamp
            ttl: Time-to-live in seconds
            timestamp_key: Key for the timestamp in the nested dictionary
            
        Returns:
            List of keys that were removed
        """
        removed_keys = []
        current_time = time.time()
        
        # Find keys to remove
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
        """
        Clean up dictionary entries based on timestamp values.
        
        Args:
            data: Dictionary where values are timestamp floats
            ttl: Time-to-live in seconds
            
        Returns:
            List of keys that were removed
        """
        removed_keys = []
        current_time = time.time()
        
        # Find keys to remove
        for key, timestamp in list(data.items()):
            if current_time - timestamp > ttl:
                del data[key]
                removed_keys.append(key)
        
        return removed_keys
        
    def log_cleanup_results(self, component: str, removed: Dict[str, int]) -> None:
        """
        Log the results of a cleanup operation.
        
        Args:
            component: Name of the component being cleaned up
            removed: Dictionary with counts of different types of removed items
        """
        # Check if anything was actually removed
        total_removed = sum(removed.values())
        current_time = time.time()
        
        # Determine if we should log this cleanup
        should_log = (
            # Log first successful cleanup
            not self.initial_cleanup_done or
            # Log if significant cleanup happened
            total_removed >= self.removal_threshold or
            # Log periodically even if nothing was removed, but not too frequently
            (total_removed > 0 and 
             current_time - self.last_logged_cleanup >= self.log_cleanup_interval)
        )
        
        # Update initial cleanup flag
        if not self.initial_cleanup_done:
            self.initial_cleanup_done = True
            
        # Only log if criteria are met
        if should_log:
            if total_removed > 0:
                # Prepare a compact single-line summary
                items_list = ", ".join(f"{count} {item}" for item, count in removed.items() if count > 0)
                log(f"Cleanup: {component} - Removed {items_list}", LOG_VERBOSE)
            elif not self.initial_cleanup_done:
                # Only for the very first run, note that cleanup is working but nothing needed
                log(f"Cleanup: {component} initialized - nothing to clean up", LOG_VERBOSE)
                
            # Update last logged time
            self.last_logged_cleanup = current_time