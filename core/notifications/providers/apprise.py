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
        "apprise_mqtt": "mqtt://{}",
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
                     log(f"Failed to add Apprise URL: {url}", LOG_STANDARD)
                else:
                     service_type = url.split('://')[0] if '://' in url else 'custom'
                     log(f"Added {service_type} notification service", LOG_VERBOSE)
            
            if self.is_configured():
                services = [url.split('://')[0] for url in self.urls if '://' in url]
                service_counts = {}
                for service in services:
                    service_counts[service] = service_counts.get(service, 0) + 1
                service_summary = ', '.join([f"{count} {name}" for name, count in service_counts.items()])
                log(f"Apprise ready with {len(self.urls)} service(s): {service_summary}", LOG_VERBOSE)
                return True
            
            log("Apprise initialized but no valid service URLs were configured.", LOG_STANDARD)
            return False
            
        except ImportError:
            log("Apprise package not installed. Run: pip install apprise")
            return False
        except Exception as e:
            log(f"Error initializing Apprise: {e}")
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
                if setting_attr == "apprise_custom" and "://" in value:
                    url = value
                else:
                    url = url_template.format(value)
                urls.append(url)
        
        email_to = settings.apprise_email_to
        if email_to:
            updated_urls = []
            found_mailto = False
            for url in urls:
                if url.startswith("mailto://"):
                    separator = '&' if '?' in url else '?'
                    updated_urls.append(f"{url}{separator}to={email_to}")
                    found_mailto = True
                else:
                    updated_urls.append(url)
            if found_mailto:
                 urls = updated_urls
            else:
                 log("APPRISE_EMAIL_TO provided, but no APPRISE_EMAIL configured.", LOG_STANDARD)

        return urls
    
    def is_configured(self) -> bool:
        """Verifies that Apprise is initialized with at least one service URL."""
        return bool(self.apprise and self.urls)
    
    # NOTIFICATION DELIVERY
    
    def send_notification(self, title: str, message: str, **kwargs) -> bool:
        """Delivers notification to all configured Apprise services with optional image attachment and timeout."""
        log(f"AP: Entering send_notification (Title: {title})", level=LOG_VERBOSE)
        if not self.is_configured():
            log("AP: Apprise not configured or no services enabled.", level=LOG_VERBOSE)
            return False
        
        success = False 
        try:
            image_url = kwargs.get('image_url')
            attach = [image_url] if image_url else None
            log(f"AP: Image URL: {image_url}, Attach: {attach}", level=LOG_VERBOSE)
            
            try:
                 apprise_module = importlib.import_module('apprise')
                 body_format = apprise_module.NotifyFormat.HTML
                 html_message = message.replace("\n", "<br />")
                 log(f"AP: Body format set to HTML.", level=LOG_VERBOSE)
            except (ImportError, AttributeError):
                 body_format = None
                 html_message = message
                 log("AP: Could not set HTML format for Apprise, using default.", level=LOG_VERBOSE)

            apprise = cast_optional(self.apprise)
            if not apprise:
                log("AP: Apprise object not initialized.", level=LOG_STANDARD)
                return False
            log(f"AP: Apprise object obtained. Preparing to call apprise.notify.", level=LOG_VERBOSE)

            log(f"AP: Calling apprise.notify (Title: {title})", level=LOG_STANDARD)
            success = apprise.notify(
                title=title,
                body=html_message,
                attach=attach,
                body_format=body_format
            )
            log(f"AP: Returned from apprise.notify (Result: {success})", level=LOG_STANDARD)

            if success:
                log("AP: Apprise library reported success.", level=LOG_VERBOSE)
            else:
                log("AP: Apprise library reported failure. Check Apprise logs if enabled.", level=LOG_STANDARD)
                
        except Exception as e:
            log(f"AP: Exception occurred during Apprise notification: {e}", level=LOG_STANDARD)
            success = False
        
        log(f"AP: Exiting send_notification (Result: {success})", level=LOG_VERBOSE)
        return success