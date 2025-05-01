"""Multi-platform notification provider using Apprise library for service integration."""
import os
import importlib
from typing import Optional, Dict, Any, List, cast

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from .base import NotificationProvider
from ...helpers.config import CoreSettings
from ...helpers.type_utils import cast_optional

# APPRISE PROVIDER

class AppriseProvider(NotificationProvider):
    """Integrates multiple notification services through the Apprise library."""
    
    PROVIDER_TYPE = "Apprise"
    DESCRIPTION = "Multi-platform notification service"
    
    SERVICE_MAP = {
        "apprise_discord": "discord://{}",
        "apprise_email": "mailto://{}",
        "apprise_telegram": "tgram://{}",
        "apprise_slack": "slack://{}",
        "apprise_gotify": "gotify://{}",
        "apprise_matrix": "matrix://{}",
        "apprise_custom": "{}"
    }
    
    def __init__(self):
        """Initializes Apprise provider with empty configuration."""
        self.apprise = None
        self.urls = []
        self.settings: Optional[CoreSettings] = None
    
    # CONFIGURATION
    
    def initialize(self, settings: CoreSettings, **kwargs) -> bool:
        """Configures Apprise with service URLs from application settings."""
        self.settings = settings
        try:
            apprise_module = importlib.import_module('apprise')
            self.apprise = apprise_module.Apprise()
            
            self.urls = self._collect_urls_from_settings()
            
            for url in self.urls:
                add_result = self.apprise.add(url)
                if not add_result:
                     log(f"Failed to add notification URL: {url}", LOG_STANDARD)
                else:
                     service_type = url.split('://')[0] if '://' in url else 'custom'
                     log(f"Added {service_type} service", LOG_VERBOSE)
            
            if self.is_configured():
                services = [url.split('://')[0] for url in self.urls if '://' in url]
                service_counts = {}
                for service in services:
                    service_counts[service] = service_counts.get(service, 0) + 1
                service_summary = ', '.join([f"{count} {name}" for name, count in service_counts.items()])
                log(f"Notification services ready: {service_summary}", LOG_VERBOSE)
                return True
            
            log("No valid notification services configured", LOG_STANDARD)
            return False
            
        except ImportError:
            log("Apprise package not installed. Run: pip install apprise")
            return False
        except Exception as e:
            log(f"Error initializing notification services: {e}")
            return False
    
    def _collect_urls_from_settings(self) -> List[str]:
        """Extracts and formats notification service URLs from application settings."""
        if not self.settings:
            return []
            
        urls = []
        settings = self.settings
        
        for setting_attr, url_template in self.SERVICE_MAP.items():
            value = getattr(settings, setting_attr, "")
            if value and isinstance(value, str):
                if setting_attr == "apprise_email" and "=" in value and "://" not in value and (
                    value.strip().startswith(("user=", "pass=", "smtp=", "port=")) or 
                    any(param in value for param in ["user=", "pass=", "smtp=", "port="])
                ):

                    if "from=" not in value:
                        url = f"mailtos://_?{value}&from=ChannelWatch"
                    else:
                        url = f"mailtos://_?{value}"

                elif setting_attr == "apprise_discord" and ("discord.com/api/webhooks/" in value or "discordapp.com/api/webhooks/" in value):

                    try:
                        parts = value.split("/api/webhooks/")
                        if len(parts) == 2 and "/" in parts[1]:
                            webhook_parts = parts[1].split("/", 1)
                            if len(webhook_parts) >= 2:
                                webhook_id, webhook_token = webhook_parts[0], webhook_parts[1]

                                if "?" in webhook_token:
                                    webhook_token = webhook_token.split("?", 1)[0]
                                
                                url = f"discord://{webhook_id}/{webhook_token}"
                            else:
                                url = url_template.format(value)
                                log(f"Could not extract token from Discord webhook URL", LOG_STANDARD)
                        else:
                            url = url_template.format(value)
                            log(f"Invalid Discord webhook URL format", LOG_STANDARD)
                    except Exception as e:
                        log(f"Error parsing Discord webhook URL: {e}", LOG_STANDARD)
                        url = url_template.format(value)
                elif setting_attr == "apprise_custom" and "://" in value:
                    url = value
                else:
                    url = url_template.format(value)
                    if setting_attr == "apprise_email" and "from=" not in url:
                        separator = '&' if '?' in url else '?'
                        url = f"{url}{separator}from=ChannelWatch"
                        log(f"Added ChannelWatch as sender name for email", LOG_VERBOSE)
                urls.append(url)
        
        email_to = settings.apprise_email_to
        if email_to:
            updated_urls = []
            found_mailto = False
            for url in urls:
                if url.startswith(("mailto://", "mailtos://")):
                    separator = '&' if '?' in url else '?'
                    updated_urls.append(f"{url}{separator}to={email_to}")
                    found_mailto = True
                else:
                    updated_urls.append(url)
            if found_mailto:
                 urls = updated_urls
            else:
                 pass

        return urls
    
    def is_configured(self) -> bool:
        """Verifies that Apprise is initialized with at least one service URL."""
        return bool(self.apprise and self.urls)
    
    # NOTIFICATION DELIVERY
    
    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Delivers notification to all configured services."""
        log(f"Sending notification: {title}", level=LOG_VERBOSE)
        if not self.is_configured():
            log("No notification services configured", level=LOG_VERBOSE)
            return False
        
        success = False 
        try:
            image_url = kwargs.get('image_url')
            discord_urls = []
            other_urls = []
            
            for url in self.urls:
                if url.startswith('discord://'):
                    discord_urls.append(url)
                else:
                    other_urls.append(url)
            
            apprise_module = importlib.import_module('apprise')
            if discord_urls:
                try:
                    discord_success = False
                    try:
                        import requests
                        
                        for discord_url in discord_urls:
                            if discord_url.startswith('discord://') and '/' in discord_url[10:]:
                                parts = discord_url[10:].split('/', 1)
                                if len(parts) == 2:
                                    webhook_id, webhook_token = parts
                                    webhook_url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"
                                    embed = {
                                        "title": title,
                                        "description": message,
                                        "color": 3447003,
                                    }
                                    if image_url:
                                        embed["image"] = {"url": image_url}
                                    
                                    payload = {
                                        "username": "ChannelWatch Bot",
                                        "content": "",
                                        "embeds": [embed]
                                    }
                                    
                                    log(f"Sending Discord notification", level=LOG_VERBOSE)
                                    response = requests.post(webhook_url, json=payload)
                                    
                                    if response.status_code == 204:
                                        discord_success = True
                                        log(f"Discord notification sent successfully", level=LOG_VERBOSE)
                                    else:
                                        log(f"Discord notification failed: {response.status_code} {response.text}", level=LOG_STANDARD)
                    except ImportError:
                        log("Requests library not available, using Apprise fallback for Discord", level=LOG_STANDARD)
                        discord_message = message
                        discord_apprise = apprise_module.Apprise()
                        for url in discord_urls:
                            discord_apprise.add(url)
                        
                        try:
                            body_format = apprise_module.NotifyFormat.TEXT if hasattr(apprise_module, 'NotifyFormat') else None
                        except (ImportError, AttributeError):
                            body_format = None
                        discord_success = discord_apprise.notify(
                            title=title,
                            body=discord_message,
                            body_format=body_format,
                            attach=[image_url] if image_url else None
                        )
                        if discord_success:
                            log("Discord notification sent via Apprise fallback", level=LOG_VERBOSE)
                    except Exception as e:
                        log(f"Error sending Discord notification: {e}", level=LOG_STANDARD)
                except Exception as e:
                    log(f"Discord notification error: {e}", level=LOG_STANDARD)
                    discord_success = False
            else:
                discord_success = True
            if other_urls:
                try:
                    try:
                        body_format = apprise_module.NotifyFormat.HTML if 'NotifyFormat' in dir(apprise_module) else None
                        html_message = message.replace("\n", "<br />")
                    except (ImportError, AttributeError):
                        body_format = None
                        html_message = message
                    other_apprise = apprise_module.Apprise()
                    for url in other_urls:
                        other_apprise.add(url)
                    attach = [image_url] if image_url else None
                    other_success = other_apprise.notify(
                        title=title,
                        body=html_message,
                        attach=attach,
                        body_format=body_format
                    )
                    if other_success:
                        log("Other notification services: delivery successful", level=LOG_VERBOSE)
                    else:
                        log("Other notification services: delivery failed", level=LOG_STANDARD)
                except Exception as e:
                    log(f"Error with other notification services: {e}", level=LOG_STANDARD)
                    other_success = False
            else:
                other_success = True
            success = (discord_success or other_success)
            
            if success:
                log("Notification sent successfully", level=LOG_VERBOSE)
            else:
                log("All notification services failed", level=LOG_STANDARD)
                
        except Exception as e:
            log(f"Notification error: {e}", level=LOG_STANDARD)
            success = False
        
        return success