"""Pushover API integration for mobile and desktop notifications."""
import requests
from typing import Optional, Dict, Any

from ...helpers.logging import log, LOG_VERBOSE
from .base import NotificationProvider

# PUSHOVER PROVIDER

class PushoverProvider(NotificationProvider):
    """Delivers notifications through the Pushover mobile and desktop notification service."""
    
    PROVIDER_TYPE = "Pushover"
    DESCRIPTION = "Pushover notification service"
    
    def __init__(self):
        """Initializes Pushover provider with empty credentials."""
        self.user_key = None
        self.api_token = None
        self.api_url = "https://api.pushover.net/1/messages.json"
    
    # CONFIGURATION
    
    def initialize(self, **kwargs) -> bool:
        """Configures Pushover provider with user key and API token."""
        self.user_key = kwargs.get('user_key')
        self.api_token = kwargs.get('api_token')
        return self.is_configured()
    
    def is_configured(self) -> bool:
        """Verifies that both user key and API token are configured."""
        return bool(self.user_key and self.api_token)
    
    # NOTIFICATION DELIVERY
    
    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Transmits notification to Pushover API with optional image attachment."""
        if not self.is_configured():
            log("Pushover not configured")
            return False
        
        image_url = kwargs.get('image_url')
        
        payload = {
            "token": self.api_token,
            "user": self.user_key,
            "title": title,
            "message": message,
        }
        
        attachment = None
        try:
            if image_url:
                img_resp = requests.get(image_url, timeout=5)
                if img_resp.status_code == 200:
                    attachment = ("image.jpg", img_resp.content, "image/jpeg")
        except Exception as e:
            log(f"Error downloading image: {e}", LOG_VERBOSE)
        
        try:
            if attachment:
                response = requests.post(
                    self.api_url,
                    data=payload,
                    files={"attachment": attachment},
                    timeout=10
                )
            else:
                response = requests.post(
                    self.api_url,
                    data=payload,
                    timeout=10
                )
            
            if response.status_code == 200:
                return True
            else:
                log(f"Failed to send notification: {response.text}")
                return False
                
        except Exception as e:
            log(f"Error sending notification: {e}")
            return False