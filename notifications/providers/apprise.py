"""
Apprise notification provider with simplified configuration.
"""
import os
import importlib
from typing import Optional, Dict, Any, List

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from .base import NotificationProvider

class AppriseProvider(NotificationProvider):
    """
    Apprise notification provider with environment variable configuration.
    Supports many notification services including Discord, Email, Slack, etc.
    """
    
    PROVIDER_TYPE = "Apprise"
    DESCRIPTION = "Multi-platform notification service"
    
    # Service prefixes and their URLs
    SERVICE_PREFIXES = {
        "APPRISE_DISCORD": "discord://{}",
        "APPRISE_EMAIL": "mailto://{}",
        "APPRISE_TELEGRAM": "tgram://{}",
        "APPRISE_SLACK": "slack://{}",
        "APPRISE_PUSHBULLET": "pbul://{}",
        "APPRISE_GOTIFY": "gotify://{}",
        "APPRISE_MATRIX": "matrix://{}",
        "APPRISE_MQTT": "mqtt://{}",
        "APPRISE_CUSTOM": "{}"  # For any custom URL
    }
    
    def __init__(self):
        """Initialize the provider."""
        self.apprise = None
        self.urls = []
    
    def initialize(self, **kwargs) -> bool:
        """
        Initialize Apprise with service configurations from environment.
        Automatically detects APPRISE_* environment variables and configures services.
        """
        try:
            # Try to import Apprise
            apprise_module = importlib.import_module('apprise')
            self.apprise = apprise_module.Apprise()
            
            # Get any URLs passed in directly
            urls = kwargs.get('urls', [])
            if isinstance(urls, str) and urls:
                self.urls.extend([url.strip() for url in urls.split(',') if url.strip()])
            
            # Collect URLs from environment variables
            env_urls = self._collect_urls_from_env()
            if env_urls:
                self.urls.extend(env_urls)
            
            # Add all URLs to Apprise
            for url in self.urls:
                self.apprise.add(url)
                service_type = url.split('://')[0] if '://' in url else 'custom'
                log(f"Added {service_type} notification service", LOG_VERBOSE)
            
            if self.is_configured():
                services = [url.split('://')[0] for url in self.urls if '://' in url]
                service_counts = {}
                for service in services:
                    service_counts[service] = service_counts.get(service, 0) + 1
                
                service_summary = ', '.join([f"{count} {name}" for name, count in service_counts.items()])
                log(f"Apprise ready with {service_summary}", LOG_VERBOSE)
                return True
            
            return False
            
        except ImportError:
            log("Apprise package not installed. Run: pip install apprise")
            return False
        except Exception as e:
            log(f"Error initializing Apprise: {e}")
            return False
    
    def _collect_urls_from_env(self) -> List[str]:
        """
        Collect and format Apprise URLs from environment variables.
        
        Returns:
            List of formatted Apprise URLs
        """
        urls = []
        
        # Process standard service variables
        for prefix, url_template in self.SERVICE_PREFIXES.items():
            if value := os.getenv(prefix):
                # Format the URL with the value
                url = url_template.format(value)
                urls.append(url)
        
        # Special case for email recipient
        if "mailto://" in "".join(urls) and (to := os.getenv("APPRISE_EMAIL_TO")):
            # Find the email URL and add the recipient
            for i, url in enumerate(urls):
                if url.startswith("mailto://"):
                    urls[i] = f"{url}?to={to}"
        
        return urls
    
    def is_configured(self) -> bool:
        """Check if provider is properly configured."""
        return bool(self.apprise and self.urls)
    
    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """
        Send notification via Apprise.
        
        Args:
            title: Notification title
            message: Notification message
            **kwargs: Additional arguments including:
                image_url: URL to an image to attach
        """
        if not self.is_configured():
            log("Apprise not configured")
            return False
        
        try:
            # Get image URL if provided
            image_url = kwargs.get('image_url')
            
            # Separate service URLs by type for specialized handling
            email_urls = [url for url in self.urls if url.startswith('mailto://')]
            discord_urls = [url for url in self.urls if url.startswith('discord://')]
            telegram_urls = [url for url in self.urls if url.startswith('tgram://')]
            slack_urls = [url for url in self.urls if url.startswith('slack://')]
            other_urls = [url for url in self.urls if not url.startswith('mailto://') 
                         and not url.startswith('discord://') 
                         and not url.startswith('tgram://') 
                         and not url.startswith('slack://')]
            
            success = True
            
            # Import requests for custom API calls
            import requests
            
            # Import apprise module
            apprise_module = importlib.import_module('apprise')
            
            # 1. HANDLE DISCORD
            if discord_urls and image_url:
                try:
                    # For each Discord webhook
                    for discord_url in discord_urls:
                        # Extract webhook ID and token from discord:// URL
                        parts = discord_url.replace('discord://', '').split('/')
                        if len(parts) >= 2:
                            webhook_id, webhook_token = parts[0], parts[1]
                            webhook_url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"
                            
                            # Create Discord webhook payload with embedded image
                            payload = {
                                "content": None,
                                "embeds": [
                                    {
                                        "title": title,
                                        "description": message,
                                        "color": 5814783,  # Blue color
                                        "image": {
                                            "url": image_url
                                        }
                                    }
                                ]
                            }
                            
                            # Send to Discord webhook API directly
                            response = requests.post(
                                webhook_url, 
                                json=payload,
                                headers={"Content-Type": "application/json"},
                                timeout=10
                            )
                            
                            if response.status_code != 204:
                                log(f"Error sending to Discord: {response.text}", LOG_VERBOSE)
                                success = False
                            else:
                                log("Discord notification sent with embedded image", LOG_VERBOSE)
                except Exception as e:
                    log(f"Error sending Discord notification: {e}", LOG_VERBOSE)
                    success = False
            
            # 2. HANDLE EMAIL
            if email_urls:
                try:
                    email_apprise = apprise_module.Apprise()
                    
                    # Add all email URLs
                    for url in email_urls:
                        email_apprise.add(url)
                    
                    # Convert line breaks to HTML <br> tags
                    html_message = message.replace("\n", "<br>")
                    
                    # Send HTML-formatted email
                    email_result = email_apprise.notify(
                        title=title,
                        body=html_message,
                        attach=image_url,
                        body_format=apprise_module.NotifyFormat.HTML
                    )
                    
                    if not email_result:
                        success = False
                        log("Failed to send to email services", LOG_VERBOSE)
                    else:
                        log("Email notification sent with proper formatting", LOG_VERBOSE)
                        
                except Exception as e:
                    log(f"Error sending email notification: {e}", LOG_VERBOSE)
                    success = False
            
            # 3. HANDLE TELEGRAM
            if telegram_urls and image_url:
                try:
                    for telegram_url in telegram_urls:
                        # Extract bot token and chat ID
                        parts = telegram_url.replace('tgram://', '').split('/')
                        if len(parts) >= 2:
                            bot_token, chat_id = parts[0], parts[1]
                            api_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
                            
                            # Prepare the message with proper line breaks
                            caption = f"{title}\n\n{message}"
                            
                            # Send photo with caption
                            response = requests.post(
                                api_url,
                                data={
                                    "chat_id": chat_id,
                                    "photo": image_url,
                                    "caption": caption,
                                    "parse_mode": "HTML"
                                },
                                timeout=10
                            )
                            
                            if not response.ok:
                                log(f"Error sending to Telegram: {response.text}", LOG_VERBOSE)
                                success = False
                            else:
                                log("Telegram notification sent with image", LOG_VERBOSE)
                except Exception as e:
                    log(f"Error sending Telegram notification: {e}", LOG_VERBOSE)
                    success = False
            
            # 4. HANDLE SLACK
            if slack_urls and image_url:
                try:
                    for slack_url in slack_urls:
                        # Extract tokens from slack:// URL
                        tokens = slack_url.replace('slack://', '').split('/')
                        if len(tokens) >= 3:
                            # Format blocks for Slack message with image
                            blocks = [
                                {
                                    "type": "header",
                                    "text": {
                                        "type": "plain_text",
                                        "text": title
                                    }
                                },
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": message.replace("\n", "\n")
                                    }
                                },
                                {
                                    "type": "image",
                                    "image_url": image_url,
                                    "alt_text": "Channel Logo"
                                }
                            ]
                            
                            # Use Apprise for Slack but let's customize the payload
                            slack_apprise = apprise_module.Apprise()
                            slack_apprise.add(slack_url)
                            
                            # Use the notify_remote method to customize the payload
                            slack_result = slack_apprise.notify(
                                title=title,
                                body=message,
                                attach=image_url,
                                body_format=apprise_module.NotifyFormat.MARKDOWN
                            )
                            
                            if not slack_result:
                                success = False
                                log("Failed to send to Slack", LOG_VERBOSE)
                            else:
                                log("Slack notification sent", LOG_VERBOSE)
                except Exception as e:
                    log(f"Error sending Slack notification: {e}", LOG_VERBOSE)
                    success = False
            
            # 5. HANDLE ALL OTHER SERVICES
            if other_urls:
                try:
                    other_apprise = apprise_module.Apprise()
                    
                    # Add all other URLs
                    for url in other_urls:
                        other_apprise.add(url)
                    
                    # Send to other services
                    other_result = other_apprise.notify(
                        title=title,
                        body=message,
                        attach=image_url
                    )
                    
                    if not other_result:
                        success = False
                        log("Failed to send to other services", LOG_VERBOSE)
                    else:
                        service_types = set([url.split('://')[0] for url in other_urls if '://' in url])
                        log(f"Notification sent to other services: {', '.join(service_types)}", LOG_VERBOSE)
                        
                except Exception as e:
                    log(f"Error sending to other services: {e}", LOG_VERBOSE)
                    success = False
                
            return success
                
        except Exception as e:
            log(f"Error sending Apprise notification: {e}")
            return False