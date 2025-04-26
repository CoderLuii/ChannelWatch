"""Disk space monitoring alert implementation for tracking DVR storage capacity."""
import os
import time
import threading
import requests
from typing import Dict, Any, Optional
import random

from .base import BaseAlert
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from .common.alert_formatter import AlertFormatter
from .common.cleanup_mixin import CleanupMixin
from ..helpers.config import CoreSettings
from ..helpers.activity_recorder import record_disk_status

# DISK SPACE
class DiskSpaceAlert(BaseAlert, CleanupMixin):
    """Monitors disk space on Channels DVR storage and alerts when space runs low."""
    
    ALERT_TYPE = "Disk-Space"
    DESCRIPTION = "Notifications when DVR disk space runs low"
    
    def __init__(self, notification_manager, settings: CoreSettings):
        """Initializes the disk space alert with notification manager and settings."""
        BaseAlert.__init__(self, notification_manager)
        CleanupMixin.__init__(self)
        
        self.settings = settings
        
        host = settings.channels_dvr_host
        port = settings.channels_dvr_port
        
        self.percent_threshold = float(settings.ds_threshold_percent)
        self.gb_threshold = float(settings.ds_threshold_gb)
        
        self.check_interval = 120
        self.cooldown_period = 3600
        
        self.monitoring_thread = None
        self.running = False
        self.previous_free_space = None
        self.previous_percentage = None
        self.last_alert_time = 0
        self.alert_sent = False
        self.startup_complete = False
        self.is_test_mode = False
        self.running_test = False
        
        self.health_check_interval = 1800
        self.health_checker_thread = None
        self.last_successful_check = 0
        
        self.is_test_mode = bool(getattr(settings, 'test_mode', False))
        
        self.log_check_interval = 300
        self.last_check_log_time = 0
        
        self.estimate_time_to_threshold = False
        self.disk_history = []
        self.max_history_points = 24
        
        self.alert_formatter = AlertFormatter(config={
            'use_emoji': True,
            'title_prefix': "⚠️ ",
        })
        
        self.api_url = f"http://{host}:{port}/dvr"
        
        if not self.is_test_mode:
            self.start_monitoring()
            self._start_health_checker()
        
        self.configure_cleanup(
            enabled=True,
            interval=3600,
            auto_cleanup=False
        )
        
    # MONITORING
    def log_storage_info(self):
        """Logs current storage information to the application logs."""
        try:
            disk_info = self._get_disk_info()
            if disk_info:
                free_bytes = disk_info.get("free", 0)
                total_bytes = disk_info.get("total", 0)
                free_percentage = (free_bytes / total_bytes) * 100 if total_bytes > 0 else 0
                
                free_formatted = self._format_bytes(free_bytes)
                total_formatted = self._format_bytes(total_bytes)
                
                log(f"DVR Storage: {free_formatted} free of {total_formatted} ({free_percentage:.1f}%)")
                
                if self.estimate_time_to_threshold:
                    self._update_disk_history(free_bytes, total_bytes)
                    estimate = self._estimate_time_to_threshold()
                    if estimate and estimate > 0:
                        log(f"Estimated time to threshold: {self._format_time(estimate)}", level=LOG_VERBOSE)
        except Exception as e:
            log(f"Error getting storage info: {e}", level=LOG_STANDARD)
        
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determines if this alert should handle the given event."""
        return False
        
    def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Handles an event from the event stream."""
        return False
        
    def start_monitoring(self):
        """Start the disk space monitoring thread."""
        if not self.running:
            self.running = True
            self.monitoring_thread = threading.Thread(
                target=self._monitoring_loop, 
                daemon=True
            )
            self.monitoring_thread.start()
        
    def stop_monitoring(self):
        """Stops the disk space monitoring thread."""
        was_running = self.running
        self.running = False
        
        if self.monitoring_thread:
            try:
                self.monitoring_thread.join(timeout=1.0)
            except:
                pass
            self.monitoring_thread = None
            
        if was_running and not self.is_test_mode:
            log("Disk space monitoring stopped", level=LOG_STANDARD)
    
    def set_startup_complete(self):
        """Marks startup process as complete to allow alert notifications."""
        self.startup_complete = True
        
    def _monitoring_loop(self):
        """Main monitoring loop that runs in a separate thread."""
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        time.sleep(10)
        
        while self.running:
            try:
                jitter = random.uniform(0, 0.5)
                
                self._check_disk_space()
                consecutive_errors = 0
                
                time.sleep(self.check_interval + jitter)
                
            except Exception as e:
                consecutive_errors += 1
                log(f"Error in disk space monitoring: {e}", level=LOG_STANDARD)
                
                backoff_time = min(30, 2 ** consecutive_errors)
                time.sleep(backoff_time)
                
                if consecutive_errors >= max_consecutive_errors:
                    log("Too many consecutive errors in disk space monitoring, but continuing to retry", 
                        level=LOG_VERBOSE)
        
        if not self.is_test_mode:
            log("Disk space monitoring recovering (60s delay)", level=LOG_VERBOSE)
            recovery_thread = threading.Thread(target=self._attempt_monitoring_recovery, daemon=True)
            recovery_thread.start()
            
    def _attempt_monitoring_recovery(self):
        """Attempts to recover disk space monitoring after failure."""
        recovery_delay = 60
        time.sleep(recovery_delay)
        
        if not self.running and not self.is_test_mode:
            self.start_monitoring()
    
    def _start_health_checker(self):
        """Starts the health checker thread to ensure monitoring stays active."""
        if self.health_checker_thread is not None and self.health_checker_thread.is_alive():
            return
            
        self.health_checker_thread = threading.Thread(target=self._health_checker_loop, daemon=True)
        self.health_checker_thread.start()
        
    def _health_checker_loop(self):
        """Monitors the health of disk space checking and restarts if necessary."""
        while True:
            time.sleep(self.health_check_interval)
            
            try:
                current_time = time.time()
                
                if (not self.running or 
                    self.monitoring_thread is None or 
                    not self.monitoring_thread.is_alive() or
                    (self.last_successful_check > 0 and 
                     current_time - self.last_successful_check > self.health_check_interval * 3)):
                    
                    log("Restarting disk space monitoring", level=LOG_VERBOSE)
                    self.stop_monitoring()
                    time.sleep(5)
                    self.start_monitoring()
            except Exception as e:
                log(f"Error in disk space monitoring health check: {e}", level=LOG_STANDARD)
    
    # DISK CHECKS
    def _get_disk_info(self) -> Optional[Dict[str, Any]]:
        """Fetches disk space information from the Channels DVR API."""
        try:
            response = requests.get(self.api_url, timeout=3)
            if response.status_code != 200:
                log(f"Failed to get disk info: HTTP {response.status_code}", level=LOG_STANDARD)
                return None
                
            data = response.json()
            
            if "disk" not in data:
                log("Disk info not found in API response", level=LOG_VERBOSE)
                return None
                
            data["disk"]["path"] = data.get("path", "/shares/DVR")
            return data["disk"]
            
        except requests.exceptions.ConnectTimeout:
            log("Connection timeout reaching Channels DVR API for disk info", level=LOG_STANDARD)
            return None
        except requests.exceptions.ReadTimeout:
            log("Read timeout reaching Channels DVR API for disk info", level=LOG_STANDARD)
            return None
        except requests.exceptions.ConnectionError:
            log("Connection error reaching Channels DVR API for disk info", level=LOG_STANDARD)
            return None
        except Exception as e:
            log(f"Error fetching disk info: {e}", level=LOG_VERBOSE)
            return None
    
    def _check_disk_space(self):
        """Checks disk space and sends an alert if below thresholds."""
        try:
            if self.is_test_mode and not self.running_test:
                return

            disk_info = self._get_disk_info()
            
            if not disk_info:
                return
                
            free_bytes = disk_info.get("free", 0)
            total_bytes = disk_info.get("total", 0)
            path = disk_info.get("path", "Unknown")
            
            if total_bytes == 0:
                return
                
            free_percentage = (free_bytes / total_bytes) * 100
            
            free_formatted = self._format_bytes(free_bytes)
            
            self.last_successful_check = time.time()
            
            current_time = time.time()
            significant_change = False
            
            if self.previous_free_space is not None:
                bytes_change = abs(free_bytes - self.previous_free_space)
                percent_change = abs(free_percentage - self.previous_percentage) if self.previous_percentage else 0
                
                significant_change = (bytes_change > 1073741824) or (percent_change > 1.0)
            
            should_log = (
                self.last_check_log_time == 0 or
                (current_time - self.last_check_log_time) >= self.log_check_interval or
                significant_change
            )
            
            if should_log:
                log(f"DVR Storage: {free_formatted} free ({free_percentage:.1f}%)", level=LOG_VERBOSE)
                self.last_check_log_time = current_time
            
            self.previous_free_space = free_bytes
            self.previous_percentage = free_percentage
            
            is_percent_low = free_percentage < self.percent_threshold
            is_gb_low = free_bytes < (self.gb_threshold * 1024 * 1024 * 1024)
            
            if is_percent_low or is_gb_low:

                in_cooldown = self.alert_sent and (time.time() - self.last_alert_time) < self.cooldown_period
                if in_cooldown:
                    time_remaining = int(self.cooldown_period - (time.time() - self.last_alert_time))
                    self.last_cooldown_log_time = time.time()
                    return
                
                log(f"Low Disk Space: {free_formatted} free ({free_percentage:.1f}%)", level=LOG_STANDARD)
                
                self._send_disk_space_alert(free_bytes, total_bytes, disk_info)
                
                self.alert_sent = True
                self.last_alert_time = time.time()
            elif self.alert_sent:
                log("DVR Storage: Returned to normal levels", level=LOG_STANDARD)
                
                self.alert_sent = False
                self.last_cooldown_log_time = 0
        except Exception as e:
            log(f"Error in disk space monitoring: {e}", level=LOG_STANDARD)
            
    # NOTIFICATIONS
    def _send_disk_space_alert(self, free_bytes, total_bytes, disk_info):
        """Sends a disk space alert notification with current storage information."""
        try:
            free_percentage = (free_bytes / total_bytes) * 100 if total_bytes > 0 else 0
            
            free_formatted = self._format_bytes(free_bytes)
            total_formatted = self._format_bytes(total_bytes)
            used_bytes = disk_info.get("used", 0)
            used_formatted = self._format_bytes(used_bytes)
            
            path = disk_info.get("path", "/shares/DVR")
            
            title = "⚠️ Low Disk Space Warning"
            message = (
                f"Free Space: {free_formatted} / {total_formatted} ({free_percentage:.1f}%)\n"
                f"Used Space: {used_formatted}\n"
                f"DVR Path: {path}"
            )
            
            self.send_alert(title, message)
            
            record_disk_status(
                free_space=free_formatted,
                total_space=total_formatted,
                used_space=used_formatted,
                free_percentage=free_percentage
            )
        except Exception as e:
            log(f"Error sending disk space alert: {e}", level=LOG_STANDARD)
    
    def process_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Processes an event from the event stream."""
        return False

    # CLEANUP
    def cleanup(self) -> None:
        """Cleans up resources used by the alert. (No action needed for DiskSpaceAlert)"""
        log("Periodic cleanup called for DiskSpaceAlert - no thread stop action taken.", level=LOG_VERBOSE)

    def __del__(self):
        """Cleans up when the object is deleted."""
        self.cleanup()

    # UTILITIES
    def _format_bytes(self, bytes_value: int) -> str:
        """Formats bytes value into human-readable string."""
        if bytes_value < 0:
            return "0.00 B"
        
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        unit_index = 0
        value = float(bytes_value)
        
        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024
            unit_index += 1
            
        return f"{value:.2f} {units[unit_index]}" 

    def _format_time(self, seconds):
        """Formats time in seconds to a human-readable string."""
        if seconds < 0:
            return "Unknown"
            
        days = seconds // 86400
        seconds %= 86400
        hours = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            
        if not parts:
            return "Less than a minute"
            
        return " ".join(parts)
        
    def _update_disk_history(self, free_bytes, total_bytes):
        """Updates the disk space history for trend analysis."""
        current_time = time.time()
        self.disk_history.append({
            'timestamp': current_time,
            'free_bytes': free_bytes,
            'total_bytes': total_bytes,
            'free_percent': (free_bytes / total_bytes) * 100 if total_bytes > 0 else 0
        })
        
        if len(self.disk_history) > self.max_history_points:
            self.disk_history = self.disk_history[-self.max_history_points:]
            
    def _estimate_time_to_threshold(self):
        """Estimates time until disk space drops below threshold."""
        if len(self.disk_history) < 2:
            return None
            
        first = self.disk_history[0]
        last = self.disk_history[-1]
        
        time_diff = last['timestamp'] - first['timestamp']
        if time_diff <= 0:
            return None
            
        bytes_diff = first['free_bytes'] - last['free_bytes']
        if bytes_diff <= 0:
            return None
            
        rate = bytes_diff / time_diff
        
        critical_bytes = min(
            last['total_bytes'] * (self.percent_threshold / 100),
            self.gb_threshold * 1024 * 1024 * 1024
        )
        
        if last['free_bytes'] <= critical_bytes:
            return 0
            
        bytes_until_critical = last['free_bytes'] - critical_bytes
        seconds_until_critical = bytes_until_critical / rate
        
        return seconds_until_critical 