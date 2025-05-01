"""Component initialization module for ChannelWatch system services."""
import os
import sys
import time
import requests
from typing import Optional

from .logging import log
from ..engine.event_monitor import EventMonitor
from ..notifications.notification import NotificationManager
from ..notifications.providers.pushover import PushoverProvider
from ..engine.alert_manager import AlertManager
from .config import CoreSettings

# CONNECTION
def check_server_connectivity(host: str, port: int) -> bool:
    """Verify connection to Channels DVR server and report version information."""
    try:
        response = requests.get(f"http://{host}:{port}/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            version = data.get("version", "Unknown")
            log(f"Server: {host}:{port} | Version: {version}")
            return True
        else:
            log(f"Connection failed: HTTP {response.status_code}")
            return False
    except Exception as e:
        log(f"Connection error: {e}")
        return False

# NOTIFICATIONS
def initialize_notifications(settings: CoreSettings, test_mode=False) -> Optional[NotificationManager]:
    """Configure and activate notification providers based on user settings."""
    notification_manager = NotificationManager()
    configured_providers = []
    
    pushover_user_key = settings.pushover_user_key
    pushover_api_token = settings.pushover_api_token
    
    if pushover_user_key and pushover_api_token:
        pushover = PushoverProvider()
        notification_manager.register_provider(pushover)
        if notification_manager.initialize_provider("Pushover", 
                                                  user_key=pushover_user_key, 
                                                  api_token=pushover_api_token):
            configured_providers.append("Pushover")
    
    apprise_configured = any([
        settings.apprise_discord, settings.apprise_email, 
        settings.apprise_telegram, settings.apprise_slack,
        settings.apprise_gotify, settings.apprise_matrix,
        settings.apprise_custom
    ])

    if apprise_configured:
        try:
            from ..notifications.providers.apprise import AppriseProvider
            apprise = AppriseProvider()
            notification_manager.register_provider(apprise)
            if notification_manager.initialize_provider("Apprise", settings=settings):
                configured_providers.append("Apprise")
        except ImportError:
            log("Apprise provider not available. Install with: pip install apprise")
        except Exception as e:
            log(f"Error initializing Apprise provider: {e}")
    
    if configured_providers:
        if not test_mode:
            if len(configured_providers) == 1:
                log(f"Notifications: {configured_providers[0]} configured")
            else:
                log(f"Notifications: {' & '.join(configured_providers)} configured")
        return notification_manager
    else:
        log("ERROR: No notification providers configured")
        return None

# ALERTS
def initialize_alerts(notification_manager, settings: CoreSettings, test_mode=False):
    """Register and enable alert handlers according to user configuration."""
    alert_manager = AlertManager(notification_manager, settings)
    enabled_alerts = []
    
    alert_mapping = {
        'alert_channel_watching': "Channel-Watching",
        'alert_disk_space': "Disk-Space",
        'alert_vod_watching': "VOD-Watching",
        'alert_recording_events': "Recording-Events"
    }
    
    for setting_attr, alert_type in alert_mapping.items():
        if getattr(settings, setting_attr, False):
            if alert_manager.register_alert(alert_type):
                enabled_alerts.append(alert_type)

    if enabled_alerts:
        if not test_mode:
            log(f"Alerts: {', '.join(enabled_alerts)}")
    else:
        log("WARNING: No alerts enabled")
    
    return alert_manager

# EVENT MONITOR
def initialize_event_monitor(host, port, alert_manager):
    """Create and configure the Channels DVR event monitoring service."""
    try:
        event_monitor = EventMonitor(host, port, alert_manager)
        return event_monitor
    except Exception as e:
        log(f"Error initializing event monitor: {e}")
        return None