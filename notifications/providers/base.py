"""
Base notification provider interface.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class NotificationProvider(ABC):
    """Base class for notification providers."""
    
    # Provider type name - override in subclass
    PROVIDER_TYPE = "BaseProvider"
    DESCRIPTION = "Base notification provider"
    
    @abstractmethod
    def initialize(self, **kwargs) -> bool:
        """Initialize the provider with configuration."""
        pass
    
    @abstractmethod
    def send_notification(self, 
                          title: str, 
                          message: str, 
                          image_url: Optional[str] = None,
                          **kwargs) -> bool:
        """Send a notification."""
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the provider is properly configured."""
        pass