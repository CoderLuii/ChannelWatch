"""Runs the Disk-Space diagnostic with current storage metrics."""

from ...helpers.logging import log
from ..output import print_test_header, print_result

# ALERT TESTING


def test_disk_space_alert(host: str, port: int, alert_manager) -> bool:
    """Tests the Disk-Space alert by sending a notification with current storage metrics."""
    print_test_header("Disk-Space Test")

    try:
        if "Disk-Space" not in alert_manager.alert_instances:
            print_result(False, "Alert not registered")
            return False

        disk_space_alert = alert_manager.alert_instances["Disk-Space"]
        disk_space_alert.is_test_mode = True
        disk_space_alert.running_test = True

        log(f"Target: {host}:{port}")
        log("Fetching disk space info...")
        disk_info = disk_space_alert._get_disk_info()

        if not disk_info:
            print_result(False, "Could not retrieve disk space information")
            disk_space_alert.running_test = False
            return False

        free_space = disk_info.get("free", 0)
        total_space = disk_info.get("total", 1)
        free_percentage = (free_space / total_space) * 100
        free_formatted = disk_space_alert._format_bytes(free_space)
        total_formatted = disk_space_alert._format_bytes(total_space)
        log(
            f"Disk: {free_formatted} free of {total_formatted} ({free_percentage:.1f}%)"
        )

        has_providers = bool(
            alert_manager.notification_manager
            and alert_manager.notification_manager.get_active_providers()
        )

        log("Processing alert...")
        result = disk_space_alert._send_disk_space_alert(
            free_space,
            total_space,
            disk_info,
            is_test=True,
        )

        if result:
            print_result(True, "Test event processed, notification dispatched")
            return True
        elif not has_providers:
            print_result(
                False, "Test event processed but no notification providers configured"
            )
            return False
        else:
            print_result(False, "Test event processing failed")
            return False

    except Exception as e:
        print_result(False, f"Exception: {e}")
        return False

    finally:
        try:
            disk_space_alert = alert_manager.alert_instances.get("Disk-Space")
            if disk_space_alert:
                disk_space_alert.running_test = False
                if hasattr(disk_space_alert, "stop_monitoring"):
                    disk_space_alert.stop_monitoring()
        except Exception:
            pass
