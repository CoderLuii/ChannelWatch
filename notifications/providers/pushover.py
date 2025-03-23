"""
Pushover notification provider.
"""
import requests
from typing import Optional, Dict, Any

from ...helpers.logging import log, LOG_VERBOSE
from .base import NotificationProvider

class PushoverProvider(NotificationProvider):
    """Pushover notification provider."""
    
    PROVIDER_TYPE = "Pushover"
    DESCRIPTION = "Pushover notification service"
    
    def __init__(self):
        """Initialize the provider."""
        self.user_key = None
        self.api_token = None
        self.api_url = "https://api.pushover.net/1/messages.json"
    
    def initialize(self, **kwargs) -> bool:
        """Initialize with Pushover credentials."""
        self.user_key = kwargs.get('user_key')
        self.api_token = kwargs.get('api_token')
        return self.is_configured()
    
    def is_configured(self) -> bool:
        """Check if provider is properly configured."""
        return bool(self.user_key and self.api_token)
    
    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Send notification via Pushover."""
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
        
        # Handle image if provided
        attachment = None
        try:
            if image_url:
                # Download image
                img_resp = requests.get(image_url, timeout=5)
                if img_resp.status_code == 200:
                    attachment = ("image.jpg", img_resp.content, "image/jpeg")
        except Exception as e:
            log(f"Error downloading image: {e}", LOG_VERBOSE)
            # Continue without image
        
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
                log(f"Notification sent via Pushover: {title}", LOG_VERBOSE)
                return True
            else:
                log(f"Failed to send notification: {response.text}")
                return False
                
        except Exception as e:
            log(f"Error sending notification: {e}")
            return False