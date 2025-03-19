"""
Notification system for sending alerts.
"""
import requests
from channelwatch import __app_name__
from channelwatch.helpers.logging import log

class NotificationManager:
    """Manages sending notifications to different services."""
    
    def __init__(self, pushover_user_key, pushover_api_token):
        """Initialize the notification manager."""
        self.pushover_user_key = pushover_user_key
        self.pushover_api_token = pushover_api_token
        
    def send_notification(self, alert_type, message, priority=0):
        """Send a notification."""
        return self.send_pushover_notification(alert_type, message, priority)
    
    def send_pushover_notification(self, alert_type, message, priority=0):
        """Send a Pushover notification."""
        title = "Channels DVR - Watching TV" if alert_type == "Channel-Watching" else f"{__app_name__} - {alert_type}"
        
        try:
            resp = requests.post("https://api.pushover.net/1/messages.json", data={
                "user": self.pushover_user_key,
                "token": self.pushover_api_token,
                "title": title,
                "message": message,
                "priority": priority
            })
            
            return resp.status_code == 200
                
        except Exception as err:
            log(f"ERROR: Failed to send notification: {err}")
            return False