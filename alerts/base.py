"""
Base alert functionality.
"""
from abc import ABC, abstractmethod
from channelwatch.alerts.patterns import ALERT_PATTERNS
from channelwatch.helpers.logging import log
from channelwatch.helpers.parsing import match_pattern

class BaseAlert(ABC):
    """Base class for all alerts."""
    
    # Alert type name - override in subclass
    ALERT_TYPE = "BaseAlert"
    
    def __init__(self, notification_manager):
        """Initialize the alert."""
        self.notification_manager = notification_manager
        
    def process_line(self, line, timestamp=None):
        """Process a log line and determine if it matches this alert type."""
        # Check if this line matches any of our patterns
        if self._matches_patterns(line):
            return self._handle_match(line, timestamp)
        return False
    
    def _matches_patterns(self, line):
        """Check if the line matches any of the patterns for this alert type."""
        for pattern in self._get_patterns():
            if match_pattern(pattern, line):
                return True
        return False
    
    def _get_patterns(self):
        """Get the patterns for this alert type."""
        return ALERT_PATTERNS.get(self.ALERT_TYPE, [])
        
    @abstractmethod
    def _handle_match(self, line, timestamp=None):
        """Handle a matched line."""
        pass
    
    def send_alert(self, message, priority=0):
        """Send an alert notification."""
        return self.notification_manager.send_notification(
            self.ALERT_TYPE, message, priority)