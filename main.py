#!/usr/bin/env python3
"""
ChannelWatch - Channels DVR monitoring tool for real-time notifications.
"""
import os
import sys
import time
import argparse
import requests
import signal

from . import __version__, __app_name__  # Current package
from .core.event_monitor import EventMonitor
from .notifications.notification import NotificationManager
from .notifications.providers.pushover import PushoverProvider
from .core.alert_manager import AlertManager
from .helpers.logging import log, set_log_level, setup_logging, LOG_STANDARD, LOG_VERBOSE
from .helpers.tools import monitor_event_stream

event_monitor = None

def main():
    """Main entry point for the application."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description=f"{__app_name__} - Channels DVR monitoring tool")
    parser.add_argument('--test-connectivity', action='store_true', help='Test API connectivity and exit')
    parser.add_argument('--monitor-events', type=int, metavar='SECONDS', help='Monitor event stream for specified seconds and exit')
    args = parser.parse_args()
    
    # Get config directory path and set up logging
    config_dir = os.getenv("CONFIG_PATH", "/config")
    retention_days = int(os.getenv("LOG_RETENTION_DAYS", "7"))
    setup_logging(config_dir, retention_days)
    
    # Set log level from environment (default to standard)
    log_level = int(os.getenv("LOG_LEVEL", "1"))
    if log_level not in (1, 2):
        log_level = 1
    set_log_level(log_level)
    
    # Log basic info
    log(f"Starting {__app_name__} v{__version__}")
    
    # Get host and port for Channels DVR server from environment variables
    host = os.getenv("CHANNELS_DVR_HOST")
    port = int(os.getenv("CHANNELS_DVR_PORT", "8089"))
    
    if not host:
        log("ERROR: CHANNELS_DVR_HOST environment variable not set")
        sys.exit(1)
    
    # Run requested tools if specified
    if args.test_connectivity:
        test_connectivity(host, port)
        sys.exit(0)
    
    if args.monitor_events:
        monitor_event_stream(host, port, args.monitor_events)
        sys.exit(0)
    
    # Initialize notification system first
    notification_manager = initialize_notifications()
    if not notification_manager:
        log("ERROR: Failed to initialize notification system")
        sys.exit(1)

    # Initialize alert system
    alert_manager = initialize_alerts(notification_manager)
    
    # Test connectivity first
    if not test_connectivity(host, port):
        log("ERROR: Failed to connect to Channels DVR")
        sys.exit(1)
    
    # Now create the event monitor
    event_monitor = initialize_event_monitor(host, port, alert_manager)
    if not event_monitor:
        log("ERROR: Failed to initialize event monitor")
        sys.exit(1)
    
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Wait a moment for connection logs to display fully
    time.sleep(1)
    
    # Cache channels AFTER connection is established
    for alert_type, alert in alert_manager.alert_instances.items():
        if hasattr(alert, '_cache_channels') and callable(getattr(alert, '_cache_channels')):
            alert._cache_channels()
    
    # Start monitoring LAST - this will block
    event_monitor.start_monitoring()

def signal_handler(sig, frame):
    """Handle termination signals."""
    global event_monitor
    log("Received shutdown signal, stopping...")
    if event_monitor:  # No need to check globals()
        event_monitor.running = False
    sys.exit(0)

def test_connectivity(host, port):
    """Test connectivity to the Channels DVR server."""
    try:
        log(f"Connecting to Channels DVR at {host}:{port}")
        
        # Make a direct HTTP request to test connectivity
        response = requests.get(f"http://{host}:{port}/status", timeout=5)
        
        if response.status_code == 200:
            # Try to parse the server version
            try:
                data = response.json()
                version = data.get("version", "Unknown")
                log(f"Connected to server version {version}")
                return True
            except:
                log("Connected to server but couldn't determine version")
                return True
        else:
            log(f"Connection failed: HTTP {response.status_code}")
            return False
    except Exception as e:
        log(f"Connection error: {e}")
        return False

def initialize_notifications():
    """Initialize notification system with available providers."""
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
            from .notifications.providers.apprise import AppriseProvider
            apprise = AppriseProvider()
            notification_manager.register_provider(apprise)
            if notification_manager.initialize_provider("Apprise"):
                configured_providers.append("Apprise")
        except ImportError:
            log("Apprise provider not available. Install with: pip install apprise")
    
    # Report configured providers
    if configured_providers:
        if len(configured_providers) == 1:
            log(f"Notifications: {configured_providers[0]} configured")
        else:
            log(f"Notifications: {' & '.join(configured_providers)} configured")
        return notification_manager
    else:
        log("ERROR: No notification providers configured")
        return None

def initialize_alerts(notification_manager):
    """Initialize alert manager with enabled alerts."""
    alert_manager = AlertManager(notification_manager)
    
    # Register enabled alerts
    enabled_alerts = []
    for env_var, value in os.environ.items():
        if env_var.startswith("Alerts_"):
            alert_type = env_var[len("Alerts_"):]
            if value.lower() in ("true", "1", "yes", "y"):
                if alert_manager.register_alert(alert_type):
                    enabled_alerts.append(alert_type)
    
    if enabled_alerts:
        log(f"Alerts Enabled: {', '.join(enabled_alerts)}")
    else:
        log("WARNING: No alerts enabled")
    
    return alert_manager

def initialize_event_monitor(host, port, alert_manager):
    """Initialize the event monitor."""
    try:
        # Just create the event monitor without testing connection yet
        event_monitor = EventMonitor(host, port, alert_manager)
        return event_monitor
    except Exception as e:
        log(f"Error initializing event monitor: {e}")
        return None

if __name__ == "__main__":
    main()