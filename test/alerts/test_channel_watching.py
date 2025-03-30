"""
Tests for Channel-Watching alerts.
"""
import time
from typing import Dict, Any

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info
from ...alerts import get_alert_class
from ...notifications.notification import NotificationManager

def test_channel_watching_alert(host: str, port: int, alert_manager) -> bool:
    """
    Test Channel-Watching alert by sending a mock event.
    
    Args:
        host: The server host
        port: The server port
        alert_manager: Alert manager instance
        
    Returns:
        bool: True if test successful, False otherwise
    """
    print_test_header("Channel-Watching Alert Test")
    success = False
    
    try:
        # Check if Channel-Watching alert is registered
        if "Channel-Watching" not in alert_manager.alert_instances:
            log("ERROR: Channel-Watching alert not registered")
            print_test_footer(False)
            return False
            
        # Get server info for realistic data
        server_info = get_server_info(host, port)
        
        # Create mock viewing event
        log("Creating test alert for Channel Watching...")
        
        # Create mock event data
        mock_event_data = {
            "Type": "activities.set",
            "Name": "test-session-id-M3U-TEST",
            "Value": "Watching ch7 ABC from Test Device (192.168.1.100) - 1080p"
        }
        
        # Get the alert instance and try to build logo URL for notification
        test_alert = alert_manager.alert_instances["Channel-Watching"]
        
        # Try to get a sample logo URL
        if hasattr(test_alert, '_cache_channels'):
            test_alert._cache_channels()
            
        # Ensure we have a channel logo URL in case caching fails
        channel_number = "7"  # From the mock event data
        if hasattr(test_alert, 'channel_provider'):
            # Real channel data
            real_channel_data = {
                'name': "WABC",
                'display_name': "ABC",
                'logo_url': "https://tmsimg.fancybits.co/assets/s10003_h3_aa.png?w=360&h=270"
            }
            
            # Real program data
            real_program_data = {
                'title': "Good Morning America",
                'description': "Up-to-the-minute news, weather, lifestyle and topical features.",
                'category': ["Episode", "Series", "Talk", "News"],
                'image_url': "https://tmsimg.fancybits.co/assets/p184220_b_h9_aa.jpg?w=720&h=540",
                'series_id': "184220",
                'episode': "E100",
                'episode_id': "EP039440321439",
                'is_new': True,
                'cast': ["George Stephanopoulos", "Robin Roberts", "Michael Strahan"]
            }
            
            # Get channel info
            channel_info = test_alert.channel_provider.get_channel_info(channel_number)
            
            # If no channel info or no logo URL, add the real data
            if not channel_info:
                # Create channel info with logo
                test_alert.channel_provider.channel_cache[channel_number] = real_channel_data
            elif not channel_info.get('logo_url'):
                # Update with real logo URL
                channel_info['logo_url'] = real_channel_data['logo_url']
            
            # If program info provider is available, add program data
            if hasattr(test_alert, 'program_provider') and hasattr(test_alert.program_provider, 'programs_cache'):
                # Add program info for this channel
                program_key = f"{channel_number}_{int(time.time())}"
                test_alert.program_provider.programs_cache[program_key] = real_program_data
        
        log("Sending test notification...")
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