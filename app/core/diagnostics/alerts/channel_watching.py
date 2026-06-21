"""Runs the Channel-Watching diagnostic with mock viewing events."""

import time

from ...helpers.logging import log
from ..output import print_test_header, print_result

# ALERT TESTING


def test_channel_watching_alert(host: str, port: int, alert_manager) -> bool:
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
            "Value": "Watching ch7 ABC from Test Device (192.168.1.100) - 1080p",
        }

        test_alert = alert_manager.alert_instances["Channel-Watching"]

        if hasattr(test_alert, "_cache_channels"):
            test_alert._cache_channels()

        channel_number = "7"
        if hasattr(test_alert, "channel_provider"):
            real_channel_data = {
                "name": "WABC",
                "display_name": "ABC",
                "logo_url": "https://tmsimg.fancybits.co/assets/s10003_h3_aa.png?w=360&h=270",
            }

            channel_info = test_alert.channel_provider.get_channel_info(channel_number)
            if not channel_info:
                test_alert.channel_provider.channel_cache[channel_number] = (
                    real_channel_data
                )
            elif not channel_info.get("logo_url"):
                channel_info["logo_url"] = real_channel_data["logo_url"]

            if hasattr(test_alert, "program_provider") and hasattr(
                test_alert.program_provider, "programs_cache"
            ):
                program_key = f"{channel_number}_{int(time.time())}"
                test_alert.program_provider.programs_cache[program_key] = {
                    "title": "Good Morning America",
                    "description": "Up-to-the-minute news, weather, lifestyle and topical features.",
                    "icon_url": "https://tmsimg.fancybits.co/assets/p184220_b_h9_aa.jpg?w=720&h=540",
                }

        has_providers = bool(
            alert_manager.notification_manager
            and alert_manager.notification_manager.get_active_providers()
        )

        log("Processing event...")
        result = alert_manager.process_event("activities.set", mock_event_data)

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
