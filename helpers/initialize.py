"""
Initialization functions for ChannelWatch components.
"""
import os
import sys
import time
import requests
from typing import Optional

from .logging import log
from ..core.event_monitor import EventMonitor
from ..notifications.notification import NotificationManager
from ..notifications.providers.pushover import PushoverProvider
from ..core.alert_manager import AlertManager

def check_server_connectivity(host: str, port: int) -> bool:
    """Basic connectivity check for startup."""
    try:
        log(f"Connecting to Channels DVR at {host}:{port}")
        response = requests.get(f"http://{host}:{port}/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            version = data.get("version", "Unknown")
            log(f"Connected to server version {version}")
            return True
        else:
            log(f"Connection failed: HTTP {response.status_code}")
            return False
    except Exception as e:
        log(f"Connection error: {e}")
        return False

def initialize_notifications(test_mode=False) -> Optional[NotificationManager]:
    """Initialize notification system with available providers."""
    # Same implementation as in startup.py
    notification_manager = NotificationManager()
    configured_providers = []
    
    # Register and initialize Pushover if configured
    pushover_user_key = os.getenv("PUSHOVER_USER_KEY")
    pushover_api_token = os.getenv("PUSHOVER_API_TOKEN")
    
    if pushover_user_key and pushover_api_token:
        pushover = PushoverProvider()
        notification_manager.register_provider(pushover)
        if notification_manager.initialize_provider("Pushover", 
                                                  user_key=pushover_user_key, 
                                                  api_token=pushover_api_token):
            configured_providers.append("Pushover")
    
    # Register and initialize Apprise if any APPRISE_* variables are set
    apprise_configured = any(k.startswith("APPRISE_") for k in os.environ)
    
    if apprise_configured:
        try:
            from ..notifications.providers.apprise import AppriseProvider
            apprise = AppriseProvider()
            notification_manager.register_provider(apprise)
            if notification_manager.initialize_provider("Apprise"):
                configured_providers.append("Apprise")
        except ImportError:
            log("Apprise provider not available. Install with: pip install apprise")
    
    # Report configured providers
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

def initialize_alerts(notification_manager, test_mode=False):
    """Initialize alert manager with enabled alerts."""
    # Same implementation as in startup.py
    alert_manager = AlertManager(notification_manager)
    
    # Register enabled alerts
    enabled_alerts = []
    
    # First check for new ALERT_ prefix format
    alert_types = {
        "ALERT_CHANNEL_WATCHING": "Channel-Watching",
        "ALERT_DISK_SPACE": "Disk-Space",
        "ALERT_VOD_WATCHING": "VOD-Watching",
        "ALERT_RECORDING_EVENTS": "Recording-Events"
    }
    
    # Check for new ALERT_ prefix format first
    for env_var, alert_type in alert_types.items():
        value = os.environ.get(env_var)
        if value and value.lower() in ("true", "1", "yes", "y"):
            if alert_manager.register_alert(alert_type):
                enabled_alerts.append(alert_type)
    
    # Then check for old format without prefix for backward compatibility
    for alert_type in ["Channel-Watching", "Disk-Space", "VOD-Watching", "Recording-Events"]:
        # Only register if not already registered via new format
        if alert_type not in enabled_alerts:
            value = os.environ.get(alert_type)
            if value and value.lower() in ("true", "1", "yes", "y"):
                if alert_manager.register_alert(alert_type):
                    enabled_alerts.append(alert_type)
    
    # For backwards compatibility, also check for alerts with Alerts_ prefix
    for env_var, value in os.environ.items():
        if env_var.startswith("Alerts_"):
            alert_type = env_var[len("Alerts_"):]
            if value.lower() in ("true", "1", "yes", "y"):
                # Only register if not already registered via new formats
                if alert_type not in enabled_alerts and alert_manager.register_alert(alert_type):
                    enabled_alerts.append(alert_type)
    
    if enabled_alerts:
        if not test_mode:
            log(f"Alerts Enabled: {', '.join(enabled_alerts)}")
    else:
        log("WARNING: No alerts enabled")
    
    return alert_manager

def initialize_event_monitor(host, port, alert_manager):
    """Initialize the event monitor."""
    # Same implementation as in startup.py
    try:
        # Just create the event monitor without testing connection yet
        event_monitor = EventMonitor(host, port, alert_manager)
        return event_monitor
    except Exception as e:
        log(f"Error initializing event monitor: {e}")
        return None