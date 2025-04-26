"""Centralized notification system for managing alerts across multiple delivery channels."""
from typing import Dict, List, Optional, Any
import os

from .. import __app_name__
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from .providers.base import NotificationProvider
from .providers.pushover import PushoverProvider

# ---------------- NOTIFICATION MANAGER ----------------

class NotificationManager:
    """Coordinates alert delivery through multiple registered notification providers."""
    
    def __init__(self):
        """Initializes the notification manager with an empty provider registry."""
        self.providers: Dict[str, NotificationProvider] = {}
    
    # ---------------- PROVIDER MANAGEMENT ----------------
        
    def register_provider(self, provider: NotificationProvider) -> bool:
        """Adds a notification provider to the registry if not already present."""
        if provider.PROVIDER_TYPE in self.providers:
            log(f"Provider {provider.PROVIDER_TYPE} already registered")
            return False
            
        self.providers[provider.PROVIDER_TYPE] = provider
        return True
    
    def initialize_provider(self, provider_type: str, **kwargs) -> bool:
        """Configures a registered provider with the supplied parameters."""
        if provider_type not in self.providers:
            log(f"Provider {provider_type} not registered")
            return False
            
        return self.providers[provider_type].initialize(**kwargs)
    
    def get_active_providers(self) -> List[str]:
        """Returns list of provider names that are configured and ready to send notifications."""
        return [
            provider_type for provider_type, provider in self.providers.items()
            if provider.is_configured()
        ]
    
    # ---------------- NOTIFICATION DELIVERY ----------------
    
    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Distributes notification to all active providers and reports delivery status."""
        if not self.providers:
            return False
            
        overall_success = False
        successful_providers = []
        
        for provider_type, provider in self.providers.items():
            if provider.is_configured():
                try:
                    provider_success = provider.send_notification(title, message, **kwargs)
                    if provider_success:
                        log(f"Notification sent via {provider_type}: {title}", level=LOG_STANDARD)
                        overall_success = True
                        successful_providers.append(provider_type)
                except Exception as e:
                    log(f"Exception sending notification via {provider_type}: {e}", level=LOG_STANDARD)
        
        if not overall_success and len(self.get_active_providers()) > 0:
            log(f"Notification failed for all configured providers (Title: {title}).", level=LOG_STANDARD)
        
        return overall_success