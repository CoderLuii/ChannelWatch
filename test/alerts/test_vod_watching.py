"""
Tests for VOD-Watching alerts.
"""
import time
from typing import Dict, Any

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info
from ...alerts import get_alert_class
from ...notifications.notification import NotificationManager

def test_vod_watching_alert(host: str, port: int, alert_manager) -> bool:
    """
    Test VOD-Watching alert by sending a mock event.
    
    Args:
        host: The server host
        port: The server port
        alert_manager: Alert manager instance
        
    Returns:
        bool: True if test successful, False otherwise
    """
    print_test_header("VOD-Watching Alert Test")
    success = False
    
    try:
        # Check if VOD-Watching alert is registered
        if "VOD-Watching" not in alert_manager.alert_instances:
            log("ERROR: VOD-Watching alert not registered")
            print_test_footer(False)
            return False
            
        # Get server info for realistic data
        server_info = get_server_info(host, port)
        
        # Create mock viewing event
        log("Creating test alert for VOD Watching...")
        
        # Test device name and IP
        device_name = "Living Room"
        device_ip = "192.168.1.100"
        
        # Create mock event data for a VOD viewing session
        mock_event_data = {
            "Type": "activities.set",
            "Name": "6-file-12345-192.168.1.100",
            "Value": f"Watching file from {device_name} ({device_ip}) at 1h15m42s"
        }
        
        log(f"Using device: '{device_name}' with IP: '{device_ip}'")
        
        # Get the alert instance
        test_alert = alert_manager.alert_instances["VOD-Watching"]
        
        # Debug notification manager and providers
        if hasattr(alert_manager, 'notification_manager'):
            nm = alert_manager.notification_manager
            log(f"Notification providers: {', '.join(nm.providers.keys()) if nm.providers else 'None'}")
        
        # Pre-cache VOD metadata
        if hasattr(test_alert, '_cache_vod_metadata'):
            test_alert._cache_vod_metadata()
        
        # Real movie data with valid image URL
        real_movie_data = {
            "id": "12345",
            "title": "Crank: High Voltage (2009)",
            "summary": "Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.",
            "full_summary": "After surviving an incredible plunge to near-certain death, Chev Chelios (Jason Statham) is abducted by Chinese mobsters. Waking up three months later, Chev finds that his nearly indestructible heart has been replaced with a battery-operated device that requires regular jolts of electricity or it will fail. Chev escapes from his captors, reunites with his lover, Eve (Amy Smart), and sets out on a frantic chase through Los Angeles to get his real heart back.",
            "content_rating": "R",
            "image_url": "https://tmsimg.fancybits.co/assets/p190667_v_v8_aq.jpg?w=480&h=720",
            "duration": 6131,  # About 1h42m
            "release_year": 2009,
            "genres": ["Action", "Thriller"],
            "cast": ["Jason Statham", "Amy Smart", "Dwight Yoakam"],
            "directors": ["Mark Neveldine", "Brian Taylor"]
        }
        
        # Create mock metadata for our test file ID if needed
        if not test_alert.vod_provider.get_metadata("12345"):
            # Add a test entry to the metadata cache
            test_alert.vod_provider.metadata_cache["12345"] = real_movie_data
        else:
            # If we have metadata but no image URL, update it
            metadata = test_alert.vod_provider.get_metadata("12345")
            if not metadata.get("image_url"):
                metadata["image_url"] = real_movie_data["image_url"]
                
            # Make sure we have the title and other important fields
            for key in ["title", "summary", "content_rating", "genres", "cast"]:
                if not metadata.get(key):
                    metadata[key] = real_movie_data[key]
        
        result = alert_manager.process_event("activities.set", mock_event_data)
        
        if result:
            log("✅ Test notification successfully sent!")
            log("If you don't receive a notification, check your notification provider settings.")
            success = True
        else:
            log("❌ Test notification failed to send.")
            log("Check your notification provider configuration.")
            success = False
    
    except Exception as e:
        log(f"Error during test alert: {e}")
        success = False
        
    print_test_footer(success)
    return success 