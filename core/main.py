#!/usr/bin/env python3
"""Core application module for ChannelWatch - Channels DVR monitoring and notification system."""
import os
import sys
import time
import argparse
import signal

# Load configuration settings before other modules
from .helpers.config import get_settings, CONFIG_FILE

from . import __version__, __app_name__
from .helpers.logging import log, set_log_level, setup_logging
from .helpers.tools import monitor_event_stream
from .helpers.initialize import (
    check_server_connectivity, 
    initialize_notifications, 
    initialize_alerts, 
    initialize_event_monitor
)
from .test import run_test
from .helpers.channel_info import ChannelInfoProvider
from .helpers.program_info import ProgramInfoProvider

event_monitor = None

def main():
    """Application entry point handling initialization, monitoring, and command-line options."""
    
    # INITIALIZATION
    settings = get_settings()
    
    parser = argparse.ArgumentParser(description=f"{__app_name__} - Channels DVR monitoring tool")
    parser.add_argument('--test-connectivity', action='store_true', help='Test API connectivity and exit')
    parser.add_argument('--test-alert', type=str, metavar='ALERT_TYPE', help='Test alert functionality for the specified alert type')
    parser.add_argument('--test-api', action='store_true', help='Test common API endpoints')
    parser.add_argument('--monitor-events', type=int, metavar='SECONDS', help='Monitor event stream for specified seconds and exit')
    parser.add_argument('--stay-alive', action='store_true', help='Keep container running even with connection errors')
    args = parser.parse_args()
    
    # LOGGING
    config_dir = os.getenv("CONFIG_PATH", "/config") 
    retention_days = settings.log_retention_days
    log_level = settings.log_level
    log_file_path = os.path.join(config_dir, "channelwatch.log")

    test_mode = args.test_connectivity or args.test_api or args.test_alert or args.monitor_events is not None
    setup_logging(config_dir, retention_days, test_mode=test_mode)
    
    if not test_mode:
        log(f"Starting {__app_name__} v{__version__}")
        log(f"Logging: Level {log_level} ({('Standard' if log_level == 1 else 'Verbose')}) | File: {log_file_path} | Retention: {retention_days} days | Config: {CONFIG_FILE}")
    
    if log_level not in (1, 2):
        log("Warning: Invalid log_level in config, defaulting to 1 (Standard)")
        log_level = 1
    set_log_level(log_level, test_mode=test_mode)
    
    # CONNECTION
    host = settings.channels_dvr_host
    port = settings.channels_dvr_port
    
    if not host:
        log("ERROR: Channels DVR Host is not configured in settings. Please set it via the Web UI.")
        log("(Alternatively, set the CHANNELS_DVR_HOST environment variable override)")
        log("Container is now in standby mode until configuration is complete.")
        log("Configure the host in the WebUI and then restart ChannelWatch.")
        while True:
            time.sleep(3600)
            settings = get_settings()
            host = settings.channels_dvr_host
            if host:
                log("Configuration detected! Host is now set. Please restart ChannelWatch to apply.")
    
    # TEST MODE
    if args.test_connectivity:
        sys.exit(0 if run_test('connectivity', host, port) else 1)
    if args.test_api:
        sys.exit(0 if run_test('api', host, port) else 1)
    if args.monitor_events:
        duration = args.monitor_events
        sys.exit(0 if run_test('event_stream', host, port, None, duration) else 1)
    
    # SYSTEM SETUP
    notification_manager = initialize_notifications(settings, test_mode=test_mode)
    if not notification_manager:
        log("ERROR: Failed to initialize notification system")
        log("At least one notification provider must be configured.")
        log("Please configure a notification provider in the WebUI settings.")
        log("Container is now in standby mode until configuration is complete.")
        log("Steps to resolve:")
        log("1. Configure at least one notification provider in WebUI settings")
        log("2. Restart ChannelWatch")
        while True:
            time.sleep(3600)
            settings = get_settings()
            test_mgr = initialize_notifications(settings, test_mode=True)
            if test_mgr:
                log("Notification provider configured! Please restart ChannelWatch to apply.")

    alert_manager = initialize_alerts(notification_manager, settings, test_mode=test_mode)
    
    if args.test_alert and alert_manager:
        sys.exit(0 if run_test(args.test_alert, host, port, alert_manager) else 1)
    
    # VALIDATION
    connected = check_server_connectivity(host, port)
    if not connected:
        log(f"ERROR: Failed to connect to Channels DVR using host: {host}, port: {port}")
        log("Please verify settings in the Web UI or environment variable overrides.")
        log("Container is now in standby mode due to connection failure")
        log("To resume normal operation:")
        log("1. Verify the Channels DVR host address and port")
        log("2. Update your configuration if needed")
        log("3. Restart ChannelWatch")
        
        while True:
            time.sleep(3600)
            settings = get_settings()
            host = settings.channels_dvr_host
            port = settings.channels_dvr_port
            if host and check_server_connectivity(host, port):
                log("Connection to Channels DVR is now available! Please restart ChannelWatch.")
    
    # MONITORING
    event_monitor = initialize_event_monitor(host, port, alert_manager)
    if not event_monitor:
        log("ERROR: Failed to initialize event monitor")
        log("Container is now in standby mode due to event monitor initialization failure")
        log("This could be due to connection issues or internal errors.")
        log("Steps to resolve:")
        log("1. Check log files for specific errors")
        log("2. Verify network connectivity to Channels DVR")
        log("3. Restart ChannelWatch")
        while True:
            time.sleep(3600)
            event_monitor = initialize_event_monitor(host, port, alert_manager)
            if event_monitor:
                log("Event monitor now available! Please restart ChannelWatch to continue.")
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # STARTUP
    if connected and "Disk-Space" in alert_manager.alert_instances:
        disk_space_alert = alert_manager.alert_instances["Disk-Space"]
        if hasattr(disk_space_alert, 'log_storage_info') and callable(getattr(disk_space_alert, 'log_storage_info')):
            disk_space_alert.log_storage_info()
    
    time.sleep(1)
    
    channel_provider = ChannelInfoProvider(host, port)
    program_provider = ProgramInfoProvider(host, port)
    
    channel_count = channel_provider.cache_channels()
    program_count = program_provider.cache_program_data()
    
    log(f"Channels: {channel_count} | Programs: {program_count}")
    
    vod_count = 0
    recording_count = 0
    
    for alert_type, alert in alert_manager.alert_instances.items():
        if alert_type == "VOD-Watching" and hasattr(alert, '_cache_vod_metadata'):
            vod_count = alert._cache_vod_metadata()
        elif alert_type == "Recording-Events" and hasattr(alert, '_cache_channels'):
            recording_count = alert._cache_channels()
        elif hasattr(alert, '_cache_channels'):
            alert._cache_channels()
            
    log(f"VOD library: {vod_count} items | Recordings: {recording_count} scheduled")
    
    for alert_type, alert in alert_manager.alert_instances.items():
        if hasattr(alert, 'set_startup_complete') and callable(getattr(alert, 'set_startup_complete')):
            alert.set_startup_complete()
    
    event_monitor.start_monitoring()

def signal_handler(sig, frame):
    """Process termination signal handler for graceful shutdown."""
    global event_monitor
    log("Received shutdown signal, stopping...")
    if event_monitor:
        event_monitor.running = False
        time.sleep(0.5)
    sys.exit(0)

if __name__ == "__main__":
    main()