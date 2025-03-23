"""
Time-based cleanup operations for stale data.

This module provides a mixin class that can be used to implement 
consistent cleanup operations for time-based data across different components.
"""
import time
import threading
from typing import Dict, Any, List, Callable, Optional, TypeVar, Generic

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

# Generic type for data structures
T = TypeVar('T')

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
                    log("Running automatic cleanup...", LOG_VERBOSE)
                    self.run_cleanup()
                    self.cleanup_config['last_cleanup'] = current_time
            except Exception as e:
                log(f"Error in auto-cleanup thread: {e}", LOG_STANDARD)
    
    def run_cleanup(self) -> None:
        """
        Run all cleanup operations.
        
        This method should be overridden by subclasses to call specific
        cleanup methods based on their needs.
        """
        # Base implementation does nothing
        pass
    
    def cleanup_dict_by_time(self, 
                           data: Dict[Any, Dict[str, Any]], 
                           ttl: int,
                           timestamp_key: str = 'timestamp') -> List[Any]:
        """
        Clean up dictionary items based on timestamp.
        
        Args:
            data: Dictionary with items to clean up
            ttl: Time-to-live in seconds
            timestamp_key: Key in the nested dictionary containing the timestamp
            
        Returns:
            List of keys that were removed
        """
        if not self.cleanup_config['enabled']:
            return []
            
        current_time = time.time()
        removed_keys = []
        
        for key, item_data in list(data.items()):
            # Skip items with no timestamp
            if timestamp_key not in item_data:
                continue
                
            timestamp = item_data[timestamp_key]
            if current_time - timestamp > ttl:
                removed_keys.append(key)
                data.pop(key, None)
                
        return removed_keys
    
    def cleanup_dict_by_timestamp(self,
                                data: Dict[Any, float],
                                ttl: int) -> List[Any]:
        """
        Clean up dictionary with direct timestamp values.
        
        Args:
            data: Dictionary with keys and timestamp values
            ttl: Time-to-live in seconds
            
        Returns:
            List of keys that were removed
        """
        if not self.cleanup_config['enabled']:
            return []
            
        current_time = time.time()
        removed_keys = []
        
        for key, timestamp in list(data.items()):
            if current_time - timestamp > ttl:
                removed_keys.append(key)
                data.pop(key, None)
                
        return removed_keys
    
    def cleanup_list_by_time(self,
                          data: List[Dict[str, Any]],
                          ttl: int,
                          timestamp_key: str = 'timestamp') -> int:
        """
        Clean up list items based on timestamp.
        
        Args:
            data: List of dictionaries to clean up
            ttl: Time-to-live in seconds
            timestamp_key: Key in the dictionary containing the timestamp
            
        Returns:
            Number of items removed
        """
        if not self.cleanup_config['enabled']:
            return 0
            
        current_time = time.time()
        original_length = len(data)
        
        # Remove items with expired timestamps
        data[:] = [item for item in data 
                  if timestamp_key in item and 
                  current_time - item[timestamp_key] <= ttl]
        
        return original_length - len(data)
    
    def cleanup_with_callback(self,
                           data: List[T],
                           is_expired: Callable[[T, float], bool]) -> int:
        """
        Clean up items using a custom expiration check.
        
        Args:
            data: List of items to clean up
            is_expired: Function that takes (item, current_time) and returns True if expired
            
        Returns:
            Number of items removed
        """
        if not self.cleanup_config['enabled']:
            return 0
            
        current_time = time.time()
        original_length = len(data)
        
        # Remove items that are expired according to the callback
        data[:] = [item for item in data if not is_expired(item, current_time)]
        
        return original_length - len(data)
    
    def log_cleanup_results(self, 
                          component: str, 
                          removed: Dict[str, int]) -> None:
        """
        Log cleanup results in a standardized format.
        
        Args:
            component: Name of the component being cleaned up
            removed: Dictionary with cleanup counts by category
        """
        if not removed:
            log(f"Cleanup completed for {component}: nothing to remove", LOG_VERBOSE)
            return
            
        items = [f"{count} {category}" for category, count in removed.items() if count > 0]
        if items:
            log(f"Cleanup completed for {component}: removed {', '.join(items)}", LOG_STANDARD)