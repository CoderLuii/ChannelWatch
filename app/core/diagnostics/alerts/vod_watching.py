"""Runs the VOD-Watching diagnostic with simulated video-on-demand viewing events."""

from ...helpers.logging import log
from ..output import print_test_header, print_result

# ALERT TESTING


def test_vod_watching_alert(host: str, port: int, alert_manager) -> bool:
    """Tests the VOD-Watching alert by simulating a video-on-demand viewing session."""
    print_test_header("VOD-Watching Test")

    try:
        if "VOD-Watching" not in alert_manager.alert_instances:
            print_result(False, "Alert not registered")
            return False

        log(f"Target: {host}:{port}")
        log("Setting up mock event data...")

        device_name = "Living Room"
        device_ip = "192.168.1.100"

        mock_event_data = {
            "Type": "activities.set",
            "Name": "6-file-12345-192.168.1.100",
            "Value": f"Watching file from {device_name} ({device_ip}) at 1h15m42s",
        }

        test_alert = alert_manager.alert_instances["VOD-Watching"]

        if hasattr(test_alert, "_cache_vod_metadata"):
            test_alert._cache_vod_metadata()

        real_movie_data = {
            "id": "12345",
            "title": "Crank: High Voltage (2009)",
            "summary": "Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.",
            "full_summary": "After surviving an incredible plunge to near-certain death, Chev Chelios (Jason Statham) is abducted by Chinese mobsters.",
            "content_rating": "R",
            "image_url": "https://tmsimg.fancybits.co/assets/p190667_v_v8_aq.jpg?w=480&h=720",
            "duration": 6131,
            "release_year": 2009,
            "genres": ["Action", "Thriller"],
            "cast": ["Jason Statham", "Amy Smart", "Dwight Yoakam"],
        }

        if not test_alert.vod_provider.get_metadata("12345"):
            test_alert.vod_provider.metadata_cache["12345"] = real_movie_data
        else:
            metadata = test_alert.vod_provider.get_metadata("12345")
            if not metadata.get("image_url"):
                metadata["image_url"] = real_movie_data["image_url"]
            for key in ["title", "summary", "content_rating", "genres", "cast"]:
                if not metadata.get(key):
                    metadata[key] = real_movie_data[key]

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
