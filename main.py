#!/usr/bin/env python3
"""
ChannelWatch - Main entry point for the Channels DVR log monitoring tool.
"""
import os
import sys
import time
from datetime import datetime

from channelwatch import __version__, __app_name__
from channelwatch.core.log_monitor import LogMonitor
from channelwatch.core.notification import NotificationManager
from channelwatch.core.alert_manager import AlertManager
from channelwatch.helpers.logging import log

def main():
    """Main entry point for the application."""
    log(f"Starting {__app_name__} v{__version__}...")

    # Configuration: interval (seconds) between log checks
    interval = int(os.getenv("LOG_CHECK_INTERVAL", "10"))

    # Initialize notification system
    notification_manager = initialize_notifications()
    if not notification_manager:
        log("ERROR: Failed to initialize notification system. Exiting.")
        sys.exit(1)

    # Initialize alert manager
    alert_manager = initialize_alerts(notification_manager)
    
    # Initialize log monitor
    log_monitor = initialize_log_monitor(alert_manager, interval)
    if not log_monitor:
        log("ERROR: Failed to initialize log monitor. Exiting.")
        sys.exit(1)
    
    # Start monitoring
    log_monitor.start_monitoring()

def initialize_notifications():
    """Initialize notification system (Pushover)."""
    # Pushover credentials from environment
    user_key = os.getenv("PUSHOVER_USER_KEY")
    api_token = os.getenv("PUSHOVER_API_TOKEN")
    
    if not user_key or not api_token:
        log("ERROR: Pushover user key or API token not set.")
        return None
    
    log("Pushover credentials found")
    return NotificationManager(user_key, api_token)

def initialize_alerts(notification_manager):
    """Initialize alert manager with enabled alerts."""
    alert_manager = AlertManager(notification_manager)
    
    # Determine which alert types are enabled
    enabled_alerts = {}
    for env_var, value in os.environ.items():
        if env_var.startswith("Alerts_"):
            alert_type = env_var[len("Alerts_"):]
            enabled_alerts[alert_type] = value.lower() in ("true", "1", "yes", "y")
    
    log(f"Initializing alerts: {', '.join([k for k, v in enabled_alerts.items() if v])}")
    
    # Register enabled alerts
    for alert_type, enabled in enabled_alerts.items():
        if enabled:
            alert_manager.register_alert(alert_type)
    
    return alert_manager

def initialize_log_monitor(alert_manager, interval):
    """Initialize log file monitor."""
    # Path to the Channels DVR log file
    log_file_path = "/channels-dvr.log"
    
    # Check file existence and readability
    if not os.path.exists(log_file_path):
        log(f"ERROR: Log file does not exist at {log_file_path}")
        return None

    if not os.access(log_file_path, os.R_OK):
        log(f"ERROR: Cannot read log file at {log_file_path} (permission denied)")
        return None
    
    log(f"Monitoring Channels DVR log file every {interval} seconds")
    return LogMonitor(log_file_path, alert_manager, interval)

if __name__ == "__main__":
    main()