"""Component initialization module for ChannelWatch system services."""

import httpx
from pathlib import Path
from typing import Optional

from .logging import log
from .dvr_connection import build_dvr_base_url
from ..engine.event_monitor import EventMonitor
from ..notifications.notification import NotificationManager
from ..notifications.webhook import WebhookManager
from ..engine.alert_manager import AlertManager
from .config import CoreSettings
from ..dvr_client import check_version_compatibility


# CONNECTION
def check_server_connectivity(host: str, port: int) -> bool:
    """Verify connection to Channels DVR server and report version information."""
    try:
        response = httpx.get(f"{build_dvr_base_url(host, port)}/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            version = data.get("version", "Unknown")
            log(f"Server: {host}:{port} | Version: {version}")
            compat = check_version_compatibility(version)
            if compat["warning"]:
                log(f"WARNING: {compat['warning']}")
            return True
        else:
            log(f"Connection failed: HTTP {response.status_code}")
            return False
    except Exception as e:
        log(f"Connection error: {e}")
        if host in ("localhost", "127.0.0.1", "0.0.0.0"):
            log(
                "Bridge mode detected: DVR host is set to a local address which is "
                "unreachable from inside a Docker container. Use your DVR's LAN IP "
                "or host.docker.internal instead.",
            )
        return False


# NOTIFICATIONS
def initialize_notifications(
    settings: CoreSettings,
    test_mode=False,
    plugin_dir: Optional[Path] = None,
) -> Optional[NotificationManager]:
    """Configure and activate notification providers based on user settings."""
    notification_manager = NotificationManager(
        rate_limit=settings.global_rate_limit,
        rate_window=settings.global_rate_window,
    )
    configured_providers = []

    apprise_configured = any(
        [
            settings.apprise_pushover,
            settings.apprise_discord,
            settings.apprise_email,
            settings.apprise_telegram,
            settings.apprise_slack,
            settings.apprise_gotify,
            settings.apprise_matrix,
            settings.apprise_custom,
        ]
    )

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

    try:
        webhook_manager = WebhookManager(settings)
        if webhook_manager.is_configured():
            notification_manager.register_webhook_manager(webhook_manager)
            configured_providers.append("Webhooks")
    except Exception as e:
        log(f"Error initializing Webhook delivery: {e}")

    try:
        from ..notifications.providers.plugin_loader import load_notification_plugins

        plugin_types = load_notification_plugins(
            notification_manager, plugin_dir=plugin_dir
        )
        configured_providers.extend(plugin_types)
    except Exception as e:
        log(f"Error loading notification plugins: {e}")

    if configured_providers:
        if not test_mode:
            if len(configured_providers) == 1:
                log(f"Notifications: {configured_providers[0]} configured")
            else:
                log(f"Notifications: {' & '.join(configured_providers)} configured")
        return notification_manager
    else:
        return None


# ALERTS
def initialize_alerts(
    notification_manager, settings: CoreSettings, test_mode=False, dvr=None
):
    if dvr is None:
        raise ValueError(
            "initialize_alerts requires an explicit dvr; no default fallback allowed"
        )
    alert_manager = AlertManager(notification_manager, settings, dvr=dvr)
    registered_alerts = []

    alert_mapping = {
        "alert_channel_watching": "Channel-Watching",
        "alert_disk_space": "Disk-Space",
        "alert_vod_watching": "VOD-Watching",
        "alert_recording_events": "Recording-Events",
    }

    for setting_attr, alert_type in alert_mapping.items():
        if not getattr(settings, setting_attr, False):
            continue
        if alert_manager.register_alert(alert_type):
            registered_alerts.append(alert_type)

    if registered_alerts and not test_mode:
        has_providers = (
            notification_manager and notification_manager.get_active_providers()
        )
        notification_types = [
            t for attr, t in alert_mapping.items() if getattr(settings, attr, False)
        ]
        log(f"Monitoring: {', '.join(registered_alerts)}")
        if has_providers and notification_types:
            log(f"Notifications enabled: {', '.join(notification_types)}")

    return alert_manager


# EVENT MONITOR
def initialize_event_monitor(host, port, alert_manager, dvr=None):
    """Create and configure the Channels DVR event monitoring service."""
    try:
        event_monitor = EventMonitor(host, port, alert_manager, dvr=dvr)
        return event_monitor
    except Exception as e:
        log(f"Error initializing event monitor: {e}")
        return None
