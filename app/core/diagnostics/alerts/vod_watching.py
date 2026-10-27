"""Runs the VOD-Watching diagnostic with simulated video-on-demand viewing events."""

import asyncio
import time

from ...helpers.logging import log
from ...helpers.diagnostic_context import diagnostic_sample
from ..output import print_test_header, print_result

# ALERT TESTING


@diagnostic_sample
async def test_vod_watching_alert(host: str, port: int, alert_manager) -> bool:
    """Tests the VOD-Watching alert by simulating a video-on-demand viewing session."""
    print_test_header("VOD-Watching Test")

    try:
        if "VOD-Watching" not in alert_manager.alert_instances:
            print_result(False, "Alert not registered")
            return False

        log(f"Target: {host}:{port}")
        log("Setting up mock event data...")

        device_name = "Test Device"
        device_ip = "192.0.2.100"

        mock_event_data = {
            "Type": "activities.set",
            "Name": "6-file-999999999-192.0.2.100",
            "Value": f"Watching file from {device_name} ({device_ip}) at 1h15m42s",
        }

        test_alert = alert_manager.alert_instances["VOD-Watching"]

        sample_metadata = {
            "id": "999999999",
            "title": "Diagnostic Video",
            "summary": "ChannelWatch diagnostic sample.",
            "image_url": "",
            "duration": 6131,
        }

        def _prepare_fixture_metadata() -> None:
            test_alert.vod_provider.metadata_cache["999999999"] = sample_metadata
            test_alert.vod_provider.last_fetch = time.time()

        await asyncio.to_thread(_prepare_fixture_metadata)

        notification_manager = alert_manager.notification_manager
        has_providers = bool(
            notification_manager
            and notification_manager.has_configured_destinations()
        )

        log("Processing event...")
        result = await alert_manager.process_event("activities.set", mock_event_data)

        if result:
            print_result(True, "Event processed, notification dispatched")
            return True
        elif not has_providers:
            print_result(
                False, "Event processed but no notification providers configured"
            )
            return False
        else:
            print_result(False, "Event processing failed")
            return False

    except Exception as e:
        print_result(False, f"Exception: {e}")
        return False
