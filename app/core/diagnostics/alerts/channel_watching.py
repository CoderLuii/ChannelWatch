"""Runs the Channel-Watching diagnostic with mock viewing events."""

import asyncio
import time

from ...helpers.logging import log
from ...helpers.diagnostic_context import diagnostic_sample
from ..output import print_test_header, print_result

# ALERT TESTING


@diagnostic_sample
async def test_channel_watching_alert(host: str, port: int, alert_manager) -> bool:
    """Tests the Channel-Watching alert by simulating a channel viewing event."""
    print_test_header("Channel-Watching Test")

    try:
        if "Channel-Watching" not in alert_manager.alert_instances:
            print_result(False, "Alert not registered")
            return False

        log(f"Target: {host}:{port}")
        log("Setting up mock event data...")

        mock_event_data = {
            "Type": "activities.set",
            "Name": "test-session-id-M3U-TEST",
            "Value": "Watching ch999999 Diagnostic Channel from Test Device (192.0.2.100) - 1080p",
        }

        test_alert = alert_manager.alert_instances["Channel-Watching"]

        channel_number = "999999"

        def _prepare_fixture_metadata() -> None:
            if not hasattr(test_alert, "channel_provider"):
                return
            channel_data = {
                "name": "Diagnostic Channel",
                "display_name": "Diagnostic Channel",
                "logo_url": "",
            }
            test_alert.channel_provider.channel_cache[channel_number] = channel_data

            if hasattr(test_alert, "program_provider") and hasattr(
                test_alert.program_provider, "program_cache"
            ):
                now = int(time.time())
                test_alert.program_provider.channel_map[channel_number] = "diagnostic"
                test_alert.program_provider.program_cache["diagnostic"] = [{
                    "title": "Diagnostic Program",
                    "description": "ChannelWatch diagnostic sample.",
                    "icon_url": "",
                    "start_time": now - 60,
                    "stop_time": now + 3600,
                }]
                test_alert.program_provider.cache_timestamp = time.time()

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
