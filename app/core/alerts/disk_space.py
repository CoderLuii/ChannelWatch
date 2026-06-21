"""Disk space monitoring alert implementation for tracking DVR storage capacity."""

import json
import time
import threading
from typing import Dict, Any, Optional
import random
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .base import BaseAlert
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.dvr_connection import build_dvr_base_url
from .common.alert_formatter import AlertFormatter
from .common.session_manager import SessionManager
from ..helpers.activity_recorder import record_disk_status
from ..notifications.template_engine import (
    NotificationTemplateEngine,
    TemplateRenderError,
)


# DISK SPACE
class DiskSpaceAlert(BaseAlert):
    """Monitors disk space on Channels DVR storage and alerts when space runs low."""

    ALERT_TYPE = "Disk-Space"
    ROUTING_EVENT_TYPE = "disk"
    DESCRIPTION = "Notifications when DVR disk space runs low"
    DISK_STATE_SESSION_ID = "disk-space-state"
    BYTES_PER_GIB = 1024 * 1024 * 1024
    DEFAULT_WARNING_THRESHOLD_PERCENT = 10
    DEFAULT_WARNING_THRESHOLD_GB = 50
    DEFAULT_CRITICAL_THRESHOLD_PERCENT = 5
    DEFAULT_CRITICAL_THRESHOLD_GB = 25
    DEFAULT_WORSENING_DELTA_GB = 1
    DEFAULT_WORSENING_DELTA_PERCENT = 1.0
    DEFAULT_STARTUP_GRACE_PERIOD = 10
    SEVERITY_NORMAL = "normal"
    SEVERITY_WARNING = "warning"
    SEVERITY_CRITICAL = "critical"
    SEVERITY_ORDER = {
        SEVERITY_NORMAL: 0,
        SEVERITY_WARNING: 1,
        SEVERITY_CRITICAL: 2,
    }

    def __init__(self, alert_manager):
        """Initializes the Disk-Space alert from an AlertManager instance."""
        BaseAlert.__init__(self, alert_manager.notification_manager)

        self.alert_manager = alert_manager
        settings = alert_manager.settings
        dvr = alert_manager.dvr

        self.settings = settings
        self.session_manager = SessionManager()
        self._disk_state: dict[str, Any] = {}
        self._notification_history: Dict[str, float] = (
            alert_manager._notification_history
        )

        self.dvr = dvr
        host = dvr.host if dvr else None
        port = dvr.port if dvr else 8089

        legacy_warning_percent = float(
            getattr(
                settings, "ds_threshold_percent", self.DEFAULT_WARNING_THRESHOLD_PERCENT
            )
        )
        legacy_warning_gb = float(
            getattr(settings, "ds_threshold_gb", self.DEFAULT_WARNING_THRESHOLD_GB)
        )

        self.warning_threshold_percent = float(
            getattr(settings, "ds_warning_threshold_percent", legacy_warning_percent)
        )
        self.warning_threshold_gb = float(
            getattr(settings, "ds_warning_threshold_gb", legacy_warning_gb)
        )
        self.critical_threshold_percent = float(
            getattr(
                settings,
                "ds_critical_threshold_percent",
                self.DEFAULT_CRITICAL_THRESHOLD_PERCENT,
            )
        )
        self.critical_threshold_gb = float(
            getattr(
                settings, "ds_critical_threshold_gb", self.DEFAULT_CRITICAL_THRESHOLD_GB
            )
        )

        self.percent_threshold = self.warning_threshold_percent
        self.gb_threshold = self.warning_threshold_gb

        self.check_interval = 120
        self.cooldown_period = getattr(settings, "ds_alert_cooldown", 3600)
        self.startup_grace_period = float(
            getattr(
                settings, "ds_startup_grace_seconds", self.DEFAULT_STARTUP_GRACE_PERIOD
            )
        )
        self.worsening_delta_bytes = (
            float(
                getattr(
                    settings, "ds_worsening_delta_gb", self.DEFAULT_WORSENING_DELTA_GB
                )
            )
            * self.BYTES_PER_GIB
        )
        self.worsening_delta_percent = float(
            getattr(
                settings,
                "ds_worsening_delta_percent",
                self.DEFAULT_WORSENING_DELTA_PERCENT,
            )
        )

        self.monitoring_thread = None
        self.running = False
        self.previous_free_space = None
        self.previous_percentage = None
        self.startup_complete = False
        self.start_monitoring_time: Optional[float] = None
        self.startup_complete_time: Optional[float] = None
        self.is_test_mode = False
        self.running_test = False

        self.health_check_interval = 1800
        self.health_checker_thread = None
        self.last_successful_check = 0

        self.is_test_mode = bool(getattr(settings, "test_mode", False))

        self.log_check_interval = 300
        self.last_check_log_time = 0
        self.last_cooldown_log_time = 0

        self.estimate_time_to_threshold = False
        self.disk_history = []
        self.max_history_points = 24

        self.alert_formatter = AlertFormatter(
            config={
                "use_emoji": True,
                "title_prefix": "⚠️ ",
            }
        )
        self.template_engine = NotificationTemplateEngine()
        self.template_settings = {
            "title": settings.ds_template_title,
            "body": settings.ds_template_body,
            "use_default": settings.ds_template_use_default,
        }

        self.cleanup_config = {
            "enabled": True,
            "interval": 3600,
            "last_cleanup": 0,
            "auto_cleanup": False,
        }

        self.api_url = f"{build_dvr_base_url(host, port)}/dvr"

    def configure_cleanup(
        self, enabled: bool = True, interval: int = 3600, auto_cleanup: bool = False
    ) -> None:
        self.cleanup_config["enabled"] = enabled
        self.cleanup_config["interval"] = interval
        self.cleanup_config["auto_cleanup"] = auto_cleanup

    # MONITORING
    def log_storage_info(self):
        """Logs current storage information to the application logs."""
        try:
            disk_info = self._get_disk_info()
            if disk_info:
                free_bytes = disk_info.get("free", 0)
                total_bytes = disk_info.get("total", 0)
                free_percentage = (
                    (free_bytes / total_bytes) * 100 if total_bytes > 0 else 0
                )

                free_formatted = self._format_bytes(free_bytes)
                total_formatted = self._format_bytes(total_bytes)

                log(
                    f"DVR Storage: {free_formatted} free of {total_formatted} ({free_percentage:.1f}%)"
                )

                if self.estimate_time_to_threshold:
                    self._update_disk_history(free_bytes, total_bytes)
                    estimate = self._estimate_time_to_threshold()
                    if estimate and estimate > 0:
                        log(
                            f"Estimated time to threshold: {self._format_time(estimate)}",
                            level=LOG_VERBOSE,
                        )
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
            if self.start_monitoring_time is None:
                self.start_monitoring_time = time.time()
            self.running = True
            self.monitoring_thread = threading.Thread(
                target=self._monitoring_loop, daemon=True
            )
            self.monitoring_thread.start()

    def stop_monitoring(self):
        """Stops the disk space monitoring thread."""
        was_running = self.running
        self.running = False

        if self.monitoring_thread:
            try:
                self.monitoring_thread.join(timeout=1.0)
            except Exception:
                pass
            self.monitoring_thread = None

        if was_running and not self.is_test_mode:
            log("Disk space monitoring stopped", level=LOG_STANDARD)

    def set_startup_complete(self):
        """Marks startup process as complete to allow alert notifications."""
        self.startup_complete = True
        self.startup_complete_time = time.time()

    def _monitoring_loop(self):
        """Main monitoring loop that runs in a separate thread."""
        consecutive_errors = 0
        max_consecutive_errors = 5

        while self.running:
            try:
                jitter = random.uniform(0, 0.5)

                self._check_disk_space()
                consecutive_errors = 0

                sleep_duration = self._get_next_check_delay(time.time(), jitter)
                time.sleep(sleep_duration)

            except Exception as e:
                consecutive_errors += 1
                log(f"Error in disk space monitoring: {e}", level=LOG_STANDARD)

                backoff_time = min(30, 2**consecutive_errors)
                time.sleep(backoff_time)

                if consecutive_errors >= max_consecutive_errors:
                    log(
                        "Too many consecutive errors in disk space monitoring, but continuing to retry",
                        level=LOG_VERBOSE,
                    )

        if not self.is_test_mode:
            log("Disk space monitoring recovering (60s delay)", level=LOG_VERBOSE)
            recovery_thread = threading.Thread(
                target=self._attempt_monitoring_recovery, daemon=True
            )
            recovery_thread.start()

    def _attempt_monitoring_recovery(self):
        """Attempts to recover disk space monitoring after failure."""
        recovery_delay = 60
        time.sleep(recovery_delay)

        if not self.running and not self.is_test_mode:
            self.start_monitoring()

    def _start_health_checker(self):
        """Starts the health checker thread to ensure monitoring stays active."""
        if (
            self.health_checker_thread is not None
            and self.health_checker_thread.is_alive()
        ):
            return

        self.health_checker_thread = threading.Thread(
            target=self._health_checker_loop, daemon=True
        )
        self.health_checker_thread.start()

    def _health_checker_loop(self):
        """Monitors the health of disk space checking and restarts if necessary."""
        while True:
            time.sleep(self.health_check_interval)

            try:
                current_time = time.time()

                if (
                    not self.running
                    or self.monitoring_thread is None
                    or not self.monitoring_thread.is_alive()
                    or (
                        self.last_successful_check > 0
                        and current_time - self.last_successful_check
                        > self.health_check_interval * 3
                    )
                ):
                    log("Restarting disk space monitoring", level=LOG_VERBOSE)
                    self.stop_monitoring()
                    time.sleep(5)
                    self.start_monitoring()
            except Exception as e:
                log(
                    f"Error in disk space monitoring health check: {e}",
                    level=LOG_STANDARD,
                )

    # DISK CHECKS
    def _get_disk_info(self) -> Optional[Dict[str, Any]]:
        """Fetches disk space information from the Channels DVR API."""
        try:
            with urlopen(self.api_url, timeout=3) as response:
                status_code = getattr(response, "status", 200)
                if status_code != 200:
                    log(
                        f"Failed to get disk info: HTTP {status_code}",
                        level=LOG_STANDARD,
                    )
                    return None

                response_body = response.read().decode("utf-8")

            data = json.loads(response_body)

            if "disk" not in data:
                log("Disk info not found in API response", level=LOG_VERBOSE)
                return None

            data["disk"]["path"] = data.get("path", "/shares/DVR")
            return data["disk"]
        except TimeoutError:
            log(
                "Connection timeout reaching Channels DVR API for disk info",
                level=LOG_STANDARD,
            )
            return None
        except HTTPError as e:
            log(f"Failed to get disk info: HTTP {e.code}", level=LOG_STANDARD)
            return None
        except URLError:
            log(
                "Connection error reaching Channels DVR API for disk info",
                level=LOG_STANDARD,
            )
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
            if total_bytes == 0:
                return

            free_percentage = (free_bytes / total_bytes) * 100

            free_formatted = self._format_bytes(free_bytes)

            self.last_successful_check = time.time()

            current_time = time.time()
            significant_change = False

            if self.previous_free_space is not None:
                bytes_change = abs(free_bytes - self.previous_free_space)
                percent_change = (
                    abs(free_percentage - self.previous_percentage)
                    if self.previous_percentage
                    else 0
                )

                significant_change = (bytes_change > 1073741824) or (
                    percent_change > 1.0
                )

            should_log = (
                self.last_check_log_time == 0
                or (current_time - self.last_check_log_time) >= self.log_check_interval
                or significant_change
            )

            if should_log:
                log(
                    f"DVR Storage: {free_formatted} free ({free_percentage:.1f}%)",
                    level=LOG_VERBOSE,
                )
                self.last_check_log_time = current_time

            self.previous_free_space = free_bytes
            self.previous_percentage = free_percentage

            snapshot = self._build_snapshot(
                free_bytes, total_bytes, disk_info, current_time
            )
            disk_state = self._get_disk_state()

            previous_status = disk_state.get("status", self.SEVERITY_NORMAL)
            current_severity = self._get_severity_for_snapshot(snapshot)
            updated_state = self._update_seen_snapshot(disk_state, snapshot)

            if updated_state.get("status") != current_severity:
                updated_state["status"] = current_severity
                updated_state["last_transition_at"] = current_time

            self._persist_disk_state(updated_state)

            if current_severity == self.SEVERITY_NORMAL:
                if previous_status != self.SEVERITY_NORMAL:
                    log("DVR Storage: Returned to normal levels", level=LOG_STANDARD)
                    updated_state["last_notification_at"] = None
                    updated_state["last_notified_severity"] = None
                    updated_state["last_notified_free_bytes"] = None
                    updated_state["last_notified_free_percentage"] = None
                    self._persist_disk_state(updated_state)
                return

            if not self._notifications_are_eligible(current_time):
                return

            if not getattr(self.settings, "alert_disk_space", True):
                return

            if not self._should_notify_for_severity(
                previous_status=previous_status,
                current_severity=current_severity,
                state=updated_state,
                snapshot=snapshot,
                current_time=current_time,
            ):
                return

            log(
                f"{self._get_log_label_for_severity(current_severity)}: {free_formatted} free ({free_percentage:.1f}%)",
                level=LOG_STANDARD,
            )

            notification_sent = self._send_disk_space_alert(
                free_bytes,
                total_bytes,
                disk_info,
                severity=current_severity,
            )
            updated_state = self._update_seen_snapshot(updated_state, snapshot)
            updated_state["status"] = current_severity
            if notification_sent:
                updated_state["last_notified_severity"] = current_severity
                updated_state["last_notified_free_bytes"] = free_bytes
                updated_state["last_notified_free_percentage"] = free_percentage
                updated_state["last_notification_at"] = current_time
            self._persist_disk_state(updated_state)
        except Exception as e:
            log(f"Error in disk space monitoring: {e}", level=LOG_STANDARD)

    def _build_snapshot(
        self,
        free_bytes: int,
        total_bytes: int,
        disk_info: Dict[str, Any],
        timestamp: float,
    ) -> Dict[str, Any]:
        free_percentage = (free_bytes / total_bytes) * 100 if total_bytes > 0 else 0
        return {
            "timestamp": timestamp,
            "free_bytes": free_bytes,
            "free_percentage": free_percentage,
            "total_bytes": total_bytes,
            "path": disk_info.get("path", "/shares/DVR"),
        }

    def _get_disk_state(self) -> Dict[str, Any]:
        state = dict(self._disk_state)
        status = self._normalize_severity(state.get("status", self.SEVERITY_NORMAL))
        last_notified_severity = self._normalize_severity(
            state.get("last_notified_severity")
        )

        if (
            last_notified_severity is None
            and state.get("last_notified_free_bytes") is not None
        ):
            last_notified_severity = (
                status if status != self.SEVERITY_NORMAL else self.SEVERITY_WARNING
            )

        return {
            "status": status,
            "last_seen_free_bytes": state.get("last_seen_free_bytes"),
            "last_seen_free_percentage": state.get("last_seen_free_percentage"),
            "last_notified_severity": last_notified_severity,
            "last_notified_free_bytes": state.get("last_notified_free_bytes"),
            "last_notified_free_percentage": state.get("last_notified_free_percentage"),
            "last_transition_at": state.get("last_transition_at"),
            "last_notification_at": state.get("last_notification_at"),
        }

    def _persist_disk_state(self, state: Dict[str, Any]) -> None:
        self._disk_state.clear()
        self._disk_state.update(state)

    def _update_seen_snapshot(
        self, state: Dict[str, Any], snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        updated_state = dict(state)
        updated_state["last_seen_free_bytes"] = snapshot["free_bytes"]
        updated_state["last_seen_free_percentage"] = snapshot["free_percentage"]
        return updated_state

    def _notifications_are_eligible(self, current_time: float) -> bool:
        if self.start_monitoring_time is None or self.startup_complete_time is None:
            return False

        eligibility_time = max(
            self.start_monitoring_time + self.startup_grace_period,
            self.startup_complete_time,
        )
        return current_time >= eligibility_time

    def _get_notification_eligibility_time(self) -> Optional[float]:
        if self.start_monitoring_time is None or self.startup_complete_time is None:
            return None

        return max(
            self.start_monitoring_time + self.startup_grace_period,
            self.startup_complete_time,
        )

    def _get_next_check_delay(self, current_time: float, jitter: float) -> float:
        normal_delay = max(1.0, self.check_interval + jitter)
        eligibility_time = self._get_notification_eligibility_time()

        if eligibility_time is None or current_time >= eligibility_time:
            return normal_delay

        time_until_eligible = max(0.0, eligibility_time - current_time)
        return max(0.25, min(normal_delay, time_until_eligible))

    def _normalize_severity(self, severity: Optional[str]) -> Optional[str]:
        if severity == "low":
            return self.SEVERITY_WARNING
        if severity in self.SEVERITY_ORDER:
            return severity
        if severity is None:
            return None
        return self.SEVERITY_NORMAL

    def _severity_rank(self, severity: Optional[str]) -> int:
        normalized = self._normalize_severity(severity) or self.SEVERITY_NORMAL
        return self.SEVERITY_ORDER[normalized]

    def _matches_threshold(
        self, snapshot: Dict[str, Any], *, percent_threshold: float, gb_threshold: float
    ) -> bool:
        is_percent_low = snapshot["free_percentage"] < percent_threshold
        is_gb_low = snapshot["free_bytes"] < (gb_threshold * self.BYTES_PER_GIB)
        return is_percent_low or is_gb_low

    def _get_severity_for_snapshot(self, snapshot: Dict[str, Any]) -> str:
        if self._matches_threshold(
            snapshot,
            percent_threshold=self.critical_threshold_percent,
            gb_threshold=self.critical_threshold_gb,
        ):
            return self.SEVERITY_CRITICAL

        if self._matches_threshold(
            snapshot,
            percent_threshold=self.warning_threshold_percent,
            gb_threshold=self.warning_threshold_gb,
        ):
            return self.SEVERITY_WARNING

        return self.SEVERITY_NORMAL

    def _has_meaningful_worsening(
        self, state: Dict[str, Any], snapshot: Dict[str, Any]
    ) -> bool:
        last_notified_free_bytes = state.get("last_notified_free_bytes")
        last_notified_free_percentage = state.get("last_notified_free_percentage")

        if last_notified_free_bytes is None or last_notified_free_percentage is None:
            return True

        bytes_drop = last_notified_free_bytes - snapshot["free_bytes"]
        percent_drop = last_notified_free_percentage - snapshot["free_percentage"]

        return (
            bytes_drop >= self.worsening_delta_bytes
            or percent_drop >= self.worsening_delta_percent
        )

    def _cooldown_elapsed(self, state: Dict[str, Any], current_time: float) -> bool:
        last_notification_at = state.get("last_notification_at")
        if last_notification_at is None:
            return True
        return (current_time - last_notification_at) >= self.cooldown_period

    def _should_notify_for_severity(
        self,
        *,
        previous_status: str,
        current_severity: str,
        state: Dict[str, Any],
        snapshot: Dict[str, Any],
        current_time: float,
    ) -> bool:
        last_notified_severity = self._normalize_severity(
            state.get("last_notified_severity")
        )

        if last_notified_severity is None:
            return True

        if self._severity_rank(current_severity) > self._severity_rank(previous_status):
            return True

        if self._severity_rank(current_severity) > self._severity_rank(
            last_notified_severity
        ):
            return True

        if current_severity != last_notified_severity:
            return False

        if not self._has_meaningful_worsening(state, snapshot):
            return False

        return self._cooldown_elapsed(state, current_time)

    def _get_log_label_for_severity(self, severity: str) -> str:
        if severity == self.SEVERITY_CRITICAL:
            return "Critical Disk Space"
        return "Low Disk Space Warning"

    def _build_disk_space_notification(
        self,
        free_bytes,
        total_bytes,
        disk_info,
        *,
        severity: str,
        is_test: bool = False,
    ) -> Dict[str, Any]:
        free_percentage = (free_bytes / total_bytes) * 100 if total_bytes > 0 else 0
        free_formatted = self._format_bytes(free_bytes)
        total_formatted = self._format_bytes(total_bytes)
        used_bytes = disk_info.get("used", 0)
        used_formatted = self._format_bytes(used_bytes)
        path = disk_info.get("path", "/shares/DVR")

        if severity == self.SEVERITY_CRITICAL:
            title = "🚨 Low Disk Space Critical"
        else:
            title = "⚠️ Low Disk Space Warning"

        if is_test:
            title_parts = title.split(" ", 1)
            if len(title_parts) == 2:
                title = f"{title_parts[0]} [TEST] {title_parts[1]}"
            else:
                title = f"[TEST] {title}"

        message = (
            f"Free Space: {free_formatted} / {total_formatted} ({free_percentage:.1f}%)\n"
            f"Used Space: {used_formatted}\n"
            f"DVR Path: {path}"
        )

        if not self.template_settings.get("use_default", True):
            threshold_percent = (
                self.critical_threshold_percent
                if severity == self.SEVERITY_CRITICAL
                else self.warning_threshold_percent
            )
            threshold_gb = (
                self.critical_threshold_gb
                if severity == self.SEVERITY_CRITICAL
                else self.warning_threshold_gb
            )
            disk_percent = f"{100 - free_percentage:.1f}%"
            threshold_text = f"{threshold_percent:.0f}% or {threshold_gb:.0f} GB free"
            context = self.alert_formatter.build_context(
                alert_type="disk_space",
                dvr=self.dvr,
                extra_context={
                    "disk_path": path,
                    "disk_label": getattr(self.dvr, "name", "") or "DVR Storage",
                    "disk_total": total_formatted,
                    "disk_total_bytes": total_bytes,
                    "disk_used": used_formatted,
                    "disk_used_bytes": used_bytes,
                    "disk_free": free_formatted,
                    "disk_free_bytes": free_bytes,
                    "disk_percent": disk_percent,
                    "disk_percent_num": f"{100 - free_percentage:.1f}",
                    "threshold": threshold_text,
                    "threshold_num": threshold_percent,
                    "recording_count": "",
                    "oldest_recording": "",
                    "oldest_recording_date": "",
                    "severity": severity,
                },
            )
            try:
                rendered_title = self.template_engine.render(
                    self.template_settings.get("title", ""), context
                ).strip()
                rendered_message = self.template_engine.render(
                    self.template_settings.get("body", ""), context
                ).strip()
                if rendered_title and rendered_message:
                    title = rendered_title
                    message = rendered_message
                else:
                    raise TemplateRenderError("Rendered disk template was blank")
            except TemplateRenderError as exc:
                log(
                    f"Template render failed for disk_space: {exc}. Falling back to defaults.",
                    level=LOG_STANDARD,
                )

        return {
            "title": title,
            "message": message,
            "free_percentage": free_percentage,
            "free_formatted": free_formatted,
            "total_formatted": total_formatted,
            "used_formatted": used_formatted,
            "path": path,
            "is_test": is_test,
        }

    def _dispatch_disk_space_notification(
        self, payload: Dict[str, Any], **notification_kwargs
    ) -> bool:
        notification_sent = bool(
            self.send_alert(payload["title"], payload["message"], **notification_kwargs)
        )

        if not payload.get("is_test", False):
            record_disk_status(
                free_space=payload["free_formatted"],
                total_space=payload["total_formatted"],
                used_space=payload["used_formatted"],
                free_percentage=payload["free_percentage"],
                title=payload["title"],
                message=payload["message"],
                extra={"path": payload["path"]},
                dvr_id=getattr(self.dvr, "id", None),
                dvr_name=getattr(self.dvr, "name", None),
                is_test=False,
                notification_history=self._notification_history,
            )

        return notification_sent

    # NOTIFICATIONS
    def _send_disk_space_alert(
        self,
        free_bytes,
        total_bytes,
        disk_info,
        *,
        severity: Optional[str] = None,
        is_test: bool = False,
        **notification_kwargs,
    ):
        """Sends a disk space alert notification with current storage information."""
        try:
            snapshot = self._build_snapshot(
                free_bytes, total_bytes, disk_info, time.time()
            )
            resolved_severity = severity or self._get_severity_for_snapshot(snapshot)
            payload = self._build_disk_space_notification(
                free_bytes,
                total_bytes,
                disk_info,
                severity=resolved_severity,
                is_test=is_test,
            )

            if getattr(self.settings, "alert_disk_space", True):
                return self._dispatch_disk_space_notification(
                    payload, **notification_kwargs
                )

            return False
        except Exception as e:
            log(f"Error sending disk space alert: {e}", level=LOG_STANDARD)
            return False

    async def process_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Processes an event from the event stream."""
        return False

    # CLEANUP
    async def cleanup(self) -> None:
        """Cleans up resources used by the alert. (No action needed for DiskSpaceAlert)"""
        log(
            "Periodic cleanup called for DiskSpaceAlert - no thread stop action taken.",
            level=LOG_VERBOSE,
        )

    def __del__(self):
        """Cleans up when the object is deleted."""
        try:
            self.running = False
        except Exception:
            pass

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
        self.disk_history.append(
            {
                "timestamp": current_time,
                "free_bytes": free_bytes,
                "total_bytes": total_bytes,
                "free_percent": (free_bytes / total_bytes) * 100
                if total_bytes > 0
                else 0,
            }
        )

        if len(self.disk_history) > self.max_history_points:
            self.disk_history = self.disk_history[-self.max_history_points :]

    def _estimate_time_to_threshold(self):
        """Estimates time until disk space drops below threshold."""
        if len(self.disk_history) < 2:
            return None

        first = self.disk_history[0]
        last = self.disk_history[-1]

        time_diff = last["timestamp"] - first["timestamp"]
        if time_diff <= 0:
            return None

        bytes_diff = first["free_bytes"] - last["free_bytes"]
        if bytes_diff <= 0:
            return None

        rate = bytes_diff / time_diff

        threshold_bytes = max(
            last["total_bytes"] * (self.percent_threshold / 100),
            self.gb_threshold * 1024 * 1024 * 1024,
        )

        if last["free_bytes"] <= threshold_bytes:
            return 0

        bytes_until_critical = last["free_bytes"] - threshold_bytes
        seconds_until_critical = bytes_until_critical / rate

        return seconds_until_critical
