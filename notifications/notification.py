"""
Notification system for sending alerts.
"""
from typing import Dict, List, Optional, Any
import os

from .. import __app_name__
from ..helpers.logging import log, LOG_VERBOSE
from .providers.base import NotificationProvider
from .providers.pushover import PushoverProvider

class NotificationManager:
    """Manages sending notifications through multiple providers."""
    
    def __init__(self):
        """Initialize the notification manager."""
        self.providers: Dict[str, NotificationProvider] = {}
        
    def register_provider(self, provider: NotificationProvider) -> bool:
        """Register a notification provider."""
        if provider.PROVIDER_TYPE in self.providers:
            log(f"Provider {provider.PROVIDER_TYPE} already registered")
            return False
            
        self.providers[provider.PROVIDER_TYPE] = provider
        return True
    
    def initialize_provider(self, provider_type: str, **kwargs) -> bool:
        """Initialize a specific provider with config."""
        if provider_type not in self.providers:
            log(f"Provider {provider_type} not registered")
            return False
            
        return self.providers[provider_type].initialize(**kwargs)
    
    def get_active_providers(self) -> List[str]:
        """Get list of active provider names."""
        return [
            provider_type for provider_type, provider in self.providers.items()
            if provider.is_configured()
        ]
    
    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Send notification through all active providers."""
        if not self.providers:
            log("No notification providers registered")
            return False
            
        success = False
        active_providers = []
        
        for provider_type, provider in self.providers.items():
            if provider.is_configured():
                try:
                    if provider.send_notification(title, message, **kwargs):
                        success = True
                        active_providers.append(provider_type)
                except Exception as e:
                    log(f"Error with {provider_type} provider: {e}")
        
        if success:
            if len(active_providers) == 1:
                log(f"Notification sent via {active_providers[0]}: {title}")
            else:
                log(f"Notification sent via multiple providers ({', '.join(active_providers)}): {title}")
        
        return success