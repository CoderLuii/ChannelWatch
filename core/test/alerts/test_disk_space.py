"""Tests the Disk-Space alert functionality with current storage metrics."""
import time
from typing import Dict, Any

from ...helpers.logging import log
from ..utils.test_utils import print_test_header, print_test_footer, get_server_info
from ...alerts import get_alert_class
from ...notifications.notification import NotificationManager

# ALERT TESTING

def test_disk_space_alert(host: str, port: int, alert_manager) -> bool:
    """Tests the Disk-Space alert by sending a notification with current storage metrics."""
    print_test_header("Disk-Space Alert Test")
    success = False
    
    try:
        if "Disk-Space" not in alert_manager.alert_instances:
            log("ERROR: Disk-Space alert not registered")
            print_test_footer(False)
            return False
            
        disk_space_alert = alert_manager.alert_instances["Disk-Space"]
        
        disk_space_alert.is_test_mode = True
        disk_space_alert.running_test = True
        
        log("Fetching current disk space information...")
        disk_info = disk_space_alert._get_disk_info()
        
        if not disk_info:
            log("ERROR: Failed to get disk space information")
            disk_space_alert.running_test = False
            print_test_footer(False)
            return False
            
        free_space = disk_info.get("free", 0)
        total_space = disk_info.get("total", 1)
        free_percentage = (free_space / total_space) * 100
        
        free_formatted = disk_space_alert._format_bytes(free_space)
        total_formatted = disk_space_alert._format_bytes(total_space)
        
        log(f"Current disk space: {free_formatted} free of {total_formatted} ({free_percentage:.1f}%)")
        
        log("Sending test notification for disk space alert...")
        disk_space_alert._send_disk_space_alert(free_space, total_space, disk_info)
        
        disk_space_alert.alert_sent = True
        disk_space_alert.last_alert_time = time.time()
        
        log("✅ Test notification successfully sent!")
        log("If you don't receive a notification, check your notification provider settings.")
        success = True
    
    except Exception as e:
        log(f"Error during test alert: {e}")
        success = False
    
    try:
        disk_space_alert = alert_manager.alert_instances.get("Disk-Space")
        if disk_space_alert:
            disk_space_alert.running_test = False
            if hasattr(disk_space_alert, "stop_monitoring"):
                disk_space_alert.stop_monitoring()
            log("Disk space alert test mode deactivated")
    except Exception as cleanup_error:
        log(f"Error during test cleanup: {cleanup_error}")
        
    print_test_footer(success)
    return success 