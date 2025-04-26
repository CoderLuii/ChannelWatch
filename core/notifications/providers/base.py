"""Abstract base class defining the interface for all notification providers."""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

# NOTIFICATION PROVIDER

class NotificationProvider(ABC):
    """Defines the standard interface for all notification service implementations."""
    
    PROVIDER_TYPE = "BaseProvider"
    DESCRIPTION = "Base notification provider"
    
    @abstractmethod
    def initialize(self, **kwargs) -> bool:
        """Configures the notification provider with required parameters."""
        pass
    
    @abstractmethod
    def send_notification(self, 
                          title: str, 
                          message: str, 
                          image_url: Optional[str] = None,
                          **kwargs) -> bool:
        """Transmits notification content to the configured service endpoint."""
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Validates that the provider has been properly initialized and configured."""
        pass