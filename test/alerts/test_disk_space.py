"""
Tests for Disk-Space alerts.
"""
import time
from typing import Dict, Any

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info
from ...alerts import get_alert_class
from ...notifications.notification import NotificationManager

def test_disk_space_alert(host: str, port: int, alert_manager) -> bool:
    """
    Test Disk-Space alert by triggering a test notification.
    
    Args:
        host: The server host
        port: The server port
        alert_manager: Alert manager instance
        
    Returns:
        bool: True if test successful, False otherwise
    """
    print_test_header("Disk-Space Alert Test")
    success = False
    
    try:
        # Check if Disk-Space alert is registered
        if "Disk-Space" not in alert_manager.alert_instances:
            log("ERROR: Disk-Space alert not registered")
            print_test_footer(False)
            return False
            
        # Get the alert instance
        disk_space_alert = alert_manager.alert_instances["Disk-Space"]
        
        # Get current disk space
        log("Fetching current disk space information...")
        disk_info = disk_space_alert._get_disk_info()
        
        if not disk_info:
            log("ERROR: Failed to get disk space information")
            print_test_footer(False)
            return False
            
        # Log current disk space
        free_space = disk_info.get("free", 0)
        total_space = disk_info.get("total", 1)
        free_percentage = (free_space / total_space) * 100
        
        # Use the proper byte formatting instead of direct GB calculation
        free_formatted = disk_space_alert._format_bytes(free_space)
        total_formatted = disk_space_alert._format_bytes(total_space)
        
        log(f"Current disk space: {free_formatted} free of {total_formatted} ({free_percentage:.1f}%)")
        
        # Send a test notification
        log("Sending test notification for disk space alert...")
        disk_space_alert._send_disk_space_alert(free_space, total_space, disk_info)
        
        log("✅ Test notification successfully sent!")
        log("If you don't receive a notification, check your notification provider settings.")
        success = True
    
    except Exception as e:
        log(f"Error during test alert: {e}")
        success = False
        
    print_test_footer(success)
    return success 