"""Manages and alerts on DVR recording events including scheduling, starting, completion, and cancellation."""
import threading
import time
import os
from typing import Dict, Any, Optional, List, Union
from datetime import datetime, timedelta
import pytz
import json
import copy
import traceback

from .base import BaseAlert
from .common.session_manager import SessionManager
from .common.alert_formatter import AlertFormatter
from .common.cleanup_mixin import CleanupMixin
from .common.stream_tracker import StreamTracker
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.parsing import extract_channel_number
from ..helpers.channel_info import ChannelInfoProvider
from ..helpers.job_info import JobInfoProvider
from ..helpers.config import CoreSettings
from ..helpers.activity_recorder import record_recording_event

# GLOBALS

event_lock = threading.Lock()

class RecordingEventsAlert(BaseAlert, CleanupMixin):
    """Monitors and alerts on DVR recording events with comprehensive status tracking."""
    
    ALERT_TYPE = "Recording-Events"
    DESCRIPTION = "Notifications for recording events (scheduled, started, cancelled, completed)"
    
    STATUS_EMOJI = {
        "scheduled": "📅",
        "started": "🔴",
        "completed": "✅",
        "cancelled": "🚫",
        "stopped": "⏹️",
        "failed": "⚠️"
    }
    
    ALERT_TITLE = "Channels DVR - Recording Event"
    
    def __init__(self, notification_manager, settings: CoreSettings):
        """Initializes the Recording-Events alert with notification manager and settings."""
        BaseAlert.__init__(self, notification_manager)
        CleanupMixin.__init__(self)
        
        self.settings = settings
        self.session_manager = SessionManager()
        
        host = settings.channels_dvr_host
        port = settings.channels_dvr_port
        timezone = settings.tz
        
        try:
            self.tz = pytz.timezone(timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            self.tz = pytz.timezone("UTC")
            log(f"Invalid timezone '{timezone}' from config, using UTC", level=LOG_STANDARD)
        
        self.tz_abbr = datetime.now(self.tz).strftime('%Z')
        
        show_program_name = settings.rd_program_name
        show_program_desc = settings.rd_program_desc
        show_duration = settings.rd_duration
        show_channel_name = settings.rd_channel_name
        show_channel_number = settings.rd_channel_number
        show_type = settings.rd_type
        
        self.alert_formatter = AlertFormatter(config={
            'show_channel_name': show_channel_name,
            'show_channel_number': show_channel_number,
            'show_program_name': show_program_name,
            'show_program_desc': show_program_desc,
            'show_duration': show_duration,
            'show_type': show_type,
            'use_emoji': True,
            'title_prefix': "",
            'image_support': True,
            'html_escape': True
        })
        
        self.active_recordings = {}
        self.scheduled_recordings = {}
        self.pending_recordings = {}
        
        self.max_retries = 5
        self.retry_interval = 2
        self.time_module = time
        self.alert_cooldown = 60
        
        channel_cache_ttl = settings.channel_cache_ttl
        job_cache_ttl = settings.job_cache_ttl
        
        self.channel_provider = ChannelInfoProvider(str(host) if host is not None else "", port, cache_ttl=channel_cache_ttl)
        self.job_provider = JobInfoProvider(str(host) if host is not None else "", port, cache_ttl=job_cache_ttl)
        
        self.stream_tracker = StreamTracker(str(host) if host is not None else "", port)
        
        self.stream_count_enabled = settings.stream_count
        self.recording_scheduled_enabled = settings.rd_alert_scheduled
        self.recording_started_enabled = settings.rd_alert_started
        self.recording_completed_enabled = settings.rd_alert_completed
        self.recording_cancelled_enabled = settings.rd_alert_cancelled
        
        self.configure_cleanup(
            enabled=True,
            interval=3600,
            auto_cleanup=True
        )
        
        self._start_retry_thread()
        
        self._last_event_time = time.time()
        self._event_counter = 0
        self._lock_health = {
            "last_acquisition": 0,
            "last_release": 0,
            "current_holder": None,
            "acquisition_count": 0,
            "release_count": 0
        }
        self._watchdog_thread = None
        self._start_watchdog()
        
        log("RecordingEventsAlert: Initialized with enhanced watchdog monitoring", level=LOG_VERBOSE)
    
    def _start_watchdog(self):
        """Start the watchdog thread to monitor event processing."""
        if self._watchdog_thread is None:
            self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
            self._watchdog_thread.start()
            log("RecordingEventsAlert: Watchdog thread started", level=LOG_VERBOSE)
            
    def _watchdog_loop(self):
        """Watchdog loop to detect when events stop being processed and provide recovery."""
        last_reported_issue = 0
        recovery_attempts = 0
        watchdog_cycle = 0
        
        while True:
            try:
                for _ in range(30):
                    time.sleep(10)
                    if getattr(self, '_shutdown_requested', False):
                        return
                
                watchdog_cycle += 1
                current_time = time.time()
                time_since_last_event = current_time - self._last_event_time
                
                if watchdog_cycle % 12 == 0:
                    log(f"Watchdog health check: {self._event_counter} events processed, " +
                        f"last event {time_since_last_event:.0f} seconds ago", level=LOG_VERBOSE)
                
                if time_since_last_event > 1800 and self._event_counter > 0:
                    if current_time - last_reported_issue > 3600:
                        last_reported_issue = current_time
                        log(f"WARNING: No recording events processed for {time_since_last_event:.0f} seconds. Event handling may be stalled.", 
                            level=LOG_VERBOSE)
                        
                        if self._lock_health["acquisition_count"] > self._lock_health["release_count"]:
                            log(f"Lock health: {self._lock_health['acquisition_count']} acquisitions, {self._lock_health['release_count']} releases. Lock may be stuck.", 
                                level=LOG_VERBOSE)
                            
                            lock_held_time = current_time - self._lock_health["last_acquisition"]
                            if lock_held_time > 1200:
                                log(f"Lock appears stuck for {lock_held_time:.0f} seconds. Will force reset.", level=LOG_VERBOSE)
                                try:
                                    global event_lock
                                    old_lock = event_lock
                                    event_lock = threading.Lock()
                                    self._lock_health = {
                                        "last_acquisition": 0,
                                        "last_release": current_time,
                                        "current_holder": None,
                                        "acquisition_count": 0,
                                        "release_count": 0
                                    }
                                    log("Forcibly replaced event lock to recover from deadlock", level=LOG_VERBOSE)
                                except Exception as e:
                                    log(f"Error during lock force-reset: {e}", level=LOG_VERBOSE)
                        
                        try:
                            recovery_attempts += 1
                            log(f"Watchdog: Triggering cleanup to attempt recovery (attempt {recovery_attempts})", level=LOG_VERBOSE)
                            self.run_cleanup()
                            
                            if recovery_attempts >= 2:
                                log("Watchdog: Refreshing job cache to help recovery", level=LOG_VERBOSE)
                                self.job_provider.cache_jobs()
                                
                                if recovery_attempts >= 3:
                                    log("Watchdog: Multiple recovery attempts failed, will reset internal state", level=LOG_VERBOSE)
                                    self.pending_recordings = {}
                                    self._last_event_time = current_time
                        except Exception as e:
                            log(f"Watchdog: Error during recovery attempt: {e}", level=LOG_VERBOSE)
                            log(traceback.format_exc(), level=LOG_VERBOSE)
            except Exception as e:
                log(f"Error in watchdog loop: {e}", level=LOG_VERBOSE)
                log(traceback.format_exc(), level=LOG_VERBOSE)
                time.sleep(60)
    
    def _cache_channels(self):
        """Cache recording job information at startup. Returns job count."""
        try:
            job_count = self.job_provider.cache_jobs()
            self._load_scheduled_recordings()
            return job_count
        except Exception as e:
            log(f"Error in _cache_channels: {e}", level=LOG_VERBOSE)
            return 0
    
    def _load_scheduled_recordings(self):
        """Load scheduled recordings at startup."""
        all_jobs = self.job_provider.get_all_jobs()
        scheduled_count = 0
        
        current_time = time.time()
        for job in all_jobs:
            job_id = job.get("id")
            if not job_id:
                continue
                
            start_time = job.get("start_time", 0)
            if start_time > current_time + 30:
                self.scheduled_recordings[job_id] = {
                    "job": job,
                    "created_at": current_time
                }
                scheduled_count += 1
    
    def _format_datetime(self, timestamp: int) -> str:
        """Formats a timestamp into a readable date/time with timezone."""
        dt = datetime.fromtimestamp(timestamp, self.tz)
        time_str = dt.strftime("%-I:%M %p") + f" {self.tz_abbr}"
        return f"{dt.strftime('%b %d, %Y')} {time_str}"
    
    def _format_datetime_friendly(self, timestamp: int) -> str:
        """Formats a timestamp into a user-friendly date/time with timezone using Today/Tomorrow when applicable."""
        dt = datetime.fromtimestamp(timestamp, self.tz)
        now = datetime.now(self.tz)
        
        time_str = dt.strftime("%-I:%M %p") + f" {self.tz_abbr}"
        
        if dt.date() == now.date():
            return f"Today at {time_str}"
        
        if dt.date() == (now.date() + timedelta(days=1)):
            return f"Tomorrow at {time_str}"
        
        return f"{dt.strftime('%b %d, %Y')} {time_str}"
    
    def _format_time_only(self, timestamp: int) -> str:
        """Formats a timestamp into time with timezone for active recordings."""
        dt = datetime.fromtimestamp(timestamp, self.tz)
        return dt.strftime("%-I:%M %p") + f" {self.tz_abbr}"
    
    def _format_duration(self, seconds: int) -> str:
        """Formats seconds into a human-readable duration string."""
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        
        if hours > 0:
            if minutes > 0:
                return f"{hours} hour{'s' if hours > 1 else ''} {minutes} minute{'s' if minutes > 1 else ''}"
            else:
                return f"{hours} hour{'s' if hours > 1 else ''}"
        else:
            return f"{minutes} minute{'s' if minutes > 1 else ''}"
    
    def _start_retry_thread(self):
        """Starts a background thread for retrying pending recording checks."""
        retry_thread = threading.Thread(target=self._retry_loop, daemon=True)
        retry_thread.start()
    
    def _retry_loop(self):
        """Executes the background retry loop for checking pending recording status."""
        while True:
            try:
                time.sleep(self.retry_interval)
                self._check_pending_recordings()
            except Exception as e:
                log(f"Error in retry loop: {e}", level=LOG_VERBOSE)
    
    def _check_pending_recordings(self):
        """Checks and updates the status of all pending recordings."""
        MAX_PENDING_CHECKS_PER_CYCLE = 10
        items_to_check = []

        if not event_lock.acquire(blocking=True, timeout=1.0):
            log("Could not acquire event lock for pending check (read phase), skipping cycle.", level=LOG_VERBOSE)
            return
        
        try:
            all_file_ids = list(self.pending_recordings.keys())
            if len(all_file_ids) > 0:
                log(f"Pending check: {len(all_file_ids)} items in queue.", level=LOG_VERBOSE)
            items_processed_in_snapshot = 0
            current_time_snapshot = time.time()

            for file_id in all_file_ids:
                if items_processed_in_snapshot >= MAX_PENDING_CHECKS_PER_CYCLE:
                    log(f"Pending check limit ({MAX_PENDING_CHECKS_PER_CYCLE}) reached during snapshot, will continue next cycle.", level=LOG_VERBOSE)
                    break
                
                if file_id not in self.pending_recordings:
                    continue
                
                pending_info = self.pending_recordings[file_id]
                
                if current_time_snapshot - pending_info.get("last_check", 0) >= self.retry_interval:
                    check_count = pending_info.get("check_count", 0) + 1
                    self.pending_recordings[file_id]["check_count"] = check_count
                    self.pending_recordings[file_id]["last_check"] = current_time_snapshot
                    items_to_check.append((file_id, pending_info, check_count))
                    items_processed_in_snapshot += 1
        finally:
            event_lock.release()
        
        if len(items_to_check) > 0:
            log(f"Pending check: Processing {len(items_to_check)} items outside lock.", level=LOG_VERBOSE)
        max_wait_time = 600
        current_time_processing = time.time()

        for file_id, pending_info, check_count in items_to_check:
            recording = None
            processed_ok = False
            should_delete = False
            delete_reason = ""

            try:
                log(f"Pending check: Fetching recording {file_id} (Attempt #{check_count})", level=LOG_VERBOSE)
                recording = self.job_provider.get_recording_by_id(file_id)
                log(f"Pending check: Fetched recording {file_id} (Result: {'Found' if recording else 'Not Found'})", level=LOG_VERBOSE)

                if not recording:
                    if current_time_processing - pending_info.get("first_seen", 0) > max_wait_time:
                        should_delete = True
                        delete_reason = f"timeout ({max_wait_time}s)"
                    continue
                
                returned_id = recording.get("id")
                if returned_id and returned_id != file_id:
                    log(f"API returned incorrect recording ID: requested {file_id}, received {returned_id}. Attempting fallback lookup.", level=LOG_VERBOSE)
                    correct_recording = None
                    try: 
                        all_recs = self.job_provider.get_all_recordings() 
                        if all_recs:
                            for rec in all_recs:
                                if rec.get("id") == file_id:
                                    correct_recording = rec
                                    log(f"Found correct recording {file_id} in all recordings list.", level=LOG_VERBOSE)
                                    break
                        if correct_recording:
                            recording = correct_recording
                        else:
                             log(f"Could not find correct recording {file_id} even in full list.", level=LOG_VERBOSE)
                    except Exception as lookup_err:
                         log(f"Error during fallback lookup for {file_id}: {lookup_err}", level=LOG_VERBOSE)
                         continue
                
                is_processed = False 
                if "processed" in recording:
                    if isinstance(recording["processed"], bool):
                        is_processed = recording["processed"]
                    elif isinstance(recording["processed"], str):
                        is_processed = recording["processed"].lower() == "true"
                elif "Processed" in recording:
                    if isinstance(recording["Processed"], bool):
                        is_processed = recording["Processed"]
                    elif isinstance(recording["Processed"], str):
                        is_processed = recording["Processed"].lower() == "true"

                if is_processed:
                    log(f"Processing recording {file_id} after check confirmed is_processed=True", level=LOG_VERBOSE)
                    current_count = 0
                    if self.stream_count_enabled:
                        try:
                             current_count = self.stream_tracker.get_stream_count()
                        except Exception as tracker_err:
                             log(f"Error getting stream count: {tracker_err}", level=LOG_VERBOSE)
                             current_count = 0
                    
                    try:
                         processed_ok = self._process_completed_recording(file_id, recording, pending_info.get("event_data", {}), current_count)
                         should_delete = True
                         delete_reason = f"processed (Result: {processed_ok})"
                    except Exception as process_err:
                        log(f"Error in _process_completed_recording for {file_id}: {process_err}", level=LOG_VERBOSE)

                elif current_time_processing - pending_info.get("first_seen", 0) > max_wait_time:
                    should_delete = True
                    delete_reason = f"timeout and not processed ({max_wait_time}s)"

            except Exception as outer_err:
                log(f"Unexpected error processing pending item {file_id}: {outer_err}", level=LOG_VERBOSE)
                should_delete = False 

            if should_delete:
                log(f"Attempting to remove {file_id} from pending queue (Reason: {delete_reason})", level=LOG_VERBOSE)
                if not event_lock.acquire(blocking=True, timeout=1.0):
                    log(f"Could not acquire event lock for pending check (delete phase), will try next cycle.", level=LOG_VERBOSE)
                    return
                
                try:
                    if file_id in self.pending_recordings:
                        log(f"Removing pending recording {file_id} from queue.", level=LOG_VERBOSE)
                        del self.pending_recordings[file_id]
                finally:
                    event_lock.release()
    
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determines if this alert should handle the given recording event type."""
        if event_type == "jobs.created" and "Name" in event_data:
            return True
            
        if event_type == "programs.set" and "Name" in event_data and "Value" in event_data:
            value = event_data.get("Value", "")
            if value.startswith("recording-"):
                return True
            if value.startswith("recorded-"):
                return True
                
        if event_type == "jobs.deleted" and "Name" in event_data:
            return True
            
        return False
    
    def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Process event with proper locking, fetch metadata, and delegate to specialized handlers."""
        
        self._last_event_time = time.time()
        self._event_counter += 1
        
        if not isinstance(event_data, dict):
            log(f"Invalid event data format for {event_type}: not a dictionary", level=LOG_VERBOSE)
            return False
            
        if not self._should_handle_event(event_type, event_data):
            return False
            
        job_id_to_fetch = None
        file_id_to_fetch = None
        job_details = None
        recording_details = None
        error_occurred = False
        request_time = 0
        
        try:
            if event_type == "jobs.created" or event_type == "jobs.deleted":
                job_id_to_fetch = event_data.get("Name", "")
                if not job_id_to_fetch:
                    log(f"Missing job ID in {event_type} event", level=LOG_STANDARD)
                    return False
                    
            elif event_type == "programs.set":
                value = event_data.get("Value", "")
                if value.startswith("recording-"):
                    job_id_to_fetch = value.replace("recording-", "")
                elif value.startswith("recorded-"):
                    file_id_to_fetch = value.replace("recorded-", "")

            if job_id_to_fetch:
                log(f"Pre-fetching job details for job_id '{job_id_to_fetch}' (Event: {event_type})", level=LOG_VERBOSE)
                try:
                    start_time = time.time()
                    job_details = self.job_provider.get_job_by_id(job_id_to_fetch)
                    request_time = time.time() - start_time
                    
                    if request_time > 2.0:
                        log(f"Job details fetch for '{job_id_to_fetch}' was slow: {request_time:.2f}s", level=LOG_VERBOSE)
                        
                    if not job_details:
                        log(f"Failed to pre-fetch job details for '{job_id_to_fetch}'", level=LOG_VERBOSE)
                        if event_type not in ["jobs.deleted"]:
                             error_occurred = True
                    else:
                         log(f"Successfully pre-fetched job details for '{job_id_to_fetch}'", level=LOG_VERBOSE)
                except Exception as fetch_err:
                    log(f"Exception during job details fetch for '{job_id_to_fetch}': {str(fetch_err)}", level=LOG_VERBOSE)
                    log(traceback.format_exc(), level=LOG_VERBOSE)
                    if event_type not in ["jobs.deleted"]:
                        error_occurred = True

            elif file_id_to_fetch:
                log(f"Pre-fetching recording details for file_id '{file_id_to_fetch}' (Event: {event_type})", level=LOG_VERBOSE)
                try:
                    start_time = time.time()
                    recording_details = self.job_provider.get_recording_by_id(file_id_to_fetch)
                    request_time = time.time() - start_time
                    
                    if request_time > 2.0:
                        log(f"Recording details fetch for '{file_id_to_fetch}' was slow: {request_time:.2f}s", level=LOG_VERBOSE)
                        
                    if not recording_details:
                        log(f"Failed to pre-fetch recording details for '{file_id_to_fetch}'. Will add to pending.", level=LOG_VERBOSE)
                    else:
                         log(f"Successfully pre-fetched recording details for '{file_id_to_fetch}'", level=LOG_VERBOSE)
                except Exception as fetch_err:
                    log(f"Exception during recording details fetch for '{file_id_to_fetch}': {str(fetch_err)}", level=LOG_VERBOSE)
                    log(traceback.format_exc(), level=LOG_VERBOSE)

        except Exception as e:
            log(f"Error during pre-fetch for event {event_type}: {e}", level=LOG_STANDARD)
            log(traceback.format_exc(), level=LOG_VERBOSE)
            error_occurred = True

        if error_occurred:
             if event_type == "jobs.created" or (event_type == "programs.set" and job_id_to_fetch):
                 log(f"Exiting _handle_event early due to pre-fetch error for critical event type {event_type}.", level=LOG_STANDARD)
                 return False
             log(f"Pre-fetch error occurred for {event_type}, proceeding to acquire lock for potential cleanup/pending logic.", level=LOG_VERBOSE)

        lock_acquired = False
        lock_start_time = time.time()
        
        try:
            current_thread_id = threading.get_ident()
            log(f"Acquiring lock for event {event_type} (thread {current_thread_id})...", level=LOG_VERBOSE)
            
            lock_acquired = event_lock.acquire(blocking=True, timeout=3.0)
            
            if lock_acquired:
                self._lock_health["last_acquisition"] = time.time()
                self._lock_health["acquisition_count"] += 1
                self._lock_health["current_holder"] = current_thread_id
                
                lock_acquisition_time = time.time() - lock_start_time
                if lock_acquisition_time > 0.5:
                    log(f"Slow lock acquisition for {event_type}: {lock_acquisition_time:.2f}s", level=LOG_VERBOSE)
                    
                log(f"Lock acquired for event {event_type} (thread {current_thread_id}).", level=LOG_VERBOSE)
            else:
                log(f"TIMEOUT acquiring lock for event {event_type} (thread {current_thread_id}), skipping.", level=LOG_VERBOSE)
                return False

            result = False
            try:
                if event_type == "jobs.created":
                    result = self._handle_recording_created(event_data, job_details)
                elif event_type == "jobs.deleted":
                    result = self._handle_recording_deleted(event_data, job_details)
                elif event_type == "programs.set":
                    value = event_data.get("Value", "")
                    if value.startswith("recording-"):
                        result = self._handle_recording_started(event_data, job_details)
                    elif value.startswith("recorded-"):
                        result = self._handle_recording_completed(event_data, recording_details)
                    else:
                         log(f"Unhandled programs.set value inside lock: {value}", level=LOG_VERBOSE)
                else:
                     log(f"Unhandled event type inside lock: {event_type}", level=LOG_VERBOSE)

            except Exception as e_inner:
                log(f"Error processing event {event_type} INSIDE lock: {e_inner}", level=LOG_VERBOSE)
                log(f"Exception stack trace: {traceback.format_exc()}", level=LOG_VERBOSE)
                result = False

            log(f"Processing complete for {event_type}. Result: {result}", level=LOG_VERBOSE)
            return bool(result)

        except Exception as lock_err:
            log(f"Unexpected exception during lock handling for {event_type}: {lock_err}", level=LOG_VERBOSE)
            log(f"Exception stack trace: {traceback.format_exc()}", level=LOG_VERBOSE)
            return False
            
        finally:
            if lock_acquired:
                try:
                    event_lock.release()
                    self._lock_health["last_release"] = time.time()
                    self._lock_health["release_count"] += 1
                    self._lock_health["current_holder"] = None
                    log(f"Lock released for event {event_type} (thread {current_thread_id}).", level=LOG_VERBOSE)
                except Exception as release_err:
                    log(f"Error releasing lock for {event_type}: {release_err}", level=LOG_VERBOSE)
                    log(f"Exception stack trace: {traceback.format_exc()}", level=LOG_VERBOSE)
    
    # RECORDING HANDLERS
    def _handle_recording_created(self, event_data: Dict[str, Any], job_details: Optional[Dict[str, Any]]) -> bool:
        """Handles the recording created event using pre-fetched job details."""
        if not self.recording_scheduled_enabled:
            return False

        if not job_details:
            log(f"_handle_recording_created: Missing job details for event {event_data.get('Name')}", level=LOG_STANDARD)
            return False
        
        job_id = job_details.get("id")
        if not job_id:
             log(f"_handle_recording_created: Missing job ID in provided job_details", level=LOG_STANDARD)
             return False
        
        job = job_details

        current_time = time.time()
        start_time = job.get("start_time", 0)
        
        is_scheduled = (start_time - current_time) > 30
        
        if not is_scheduled:
            return False
            
        self.active_recordings[job_id] = job
        
        self.scheduled_recordings[job_id] = {
            "job": job,
            "created_at": current_time
        }
        
        recording_title = job.get("name", "Unknown")
        item = job.get("item", {})
        channel_info = {}
        
        channels = job.get("channels", [])
        channel_number = None
        if channels and len(channels) > 0:
            channel_number = channels[0]
            channel_data = self.channel_provider.get_channel_info(channel_number)
            if channel_data:
                channel_info = {
                    "number": channel_number,
                    "name": channel_data.get("name", ""),
                    "logo_url": channel_data.get("logo_url", "")
                }
            else:
                channel_info = {
                    "number": channel_number,
                    "name": f"Channel {channel_number}"
                }
                
        start_time_str = self._format_datetime_friendly(start_time)
        
        expected_duration = job.get("duration", 0)
        duration_str = ""
        if expected_duration > 0:
            duration_str = self._format_duration(expected_duration)
        
        notification_key = f"recording-scheduled-{job_id}"
        
        should_send = self.alert_formatter.should_send_notification(
                self.session_manager, 
                notification_key, 
                self.alert_cooldown)
        if not should_send:
            return False
        
        message_parts: Dict[str, Union[str, Dict[str, str]]] = {
            'status': f"{self.STATUS_EMOJI['scheduled']} Scheduled",
        }
        
        if self.settings.rd_program_name and recording_title:
            message_parts['details'] = f"Program: {recording_title}"
        
        table_parts = []
        table_parts.append("-----------------------")
        table_parts.append(f"Scheduled: {start_time_str}")
        
        if self.settings.rd_duration and duration_str:
            table_parts.append(f"Duration:  {duration_str}")
        
        message_parts['time_table'] = "\n".join(table_parts)
        
        if channel_info:
            message_parts['channel'] = {
                'number': str(channel_info.get('number', '')),
                'name': str(channel_info.get('name', ''))
            }
            
        if self.settings.rd_program_desc and item and item.get("summary"):
            message_parts['custom'] = str(item.get("summary", ""))
            
        message = self.alert_formatter.format_message(
            message_parts,
            order=['channel', 'status', 'details', 'custom', 'time_table']
        )
        image_url = item.get("image_url")
        
        channel_label = channel_info.get('name', f'Channel {channel_info.get("number", "Unknown")}')
        log(f"Scheduled recording: {recording_title} on {channel_label} at {start_time_str}, Duration: {duration_str}", level=LOG_STANDARD)
        
        record_recording_event(
            event_type="Scheduled",
            program_name=recording_title,
            channel_name=channel_label,
            scheduled_datetime=datetime.fromtimestamp(start_time, self.tz)
        )
        
        self.send_alert(self.ALERT_TITLE, message, image_url)
        
        self.session_manager.record_notification(notification_key)
        
        return True
    
    def _handle_recording_started(self, event_data: Dict[str, Any], job_details: Optional[Dict[str, Any]]) -> bool:
        """Handles the recording started event using job details (might be None if called from programs.set)."""
        if not self.recording_started_enabled:
            return False

        job_id = None
        if job_details:
             job_id = job_details.get("id")
        else:
             value = event_data.get("Value", "")
             if value.startswith("recording-"):
                 job_id = value.replace("recording-", "")
                 if not job_details and job_id:
                      try:
                          log(f"Fetching job details again for {job_id} in _handle_recording_started", level=LOG_VERBOSE)
                          job_details = self.job_provider.get_job_by_id(job_id)
                      except Exception as e:
                           log(f"_handle_recording_started: Error fetching job details for {job_id}: {e}", level=LOG_VERBOSE)
                           return False

        if not job_details or not job_id:
            log(f"_handle_recording_started: Missing job details for event {event_data.get('Value', '')}", level=LOG_VERBOSE)
            return False
           
        job = job_details

        was_scheduled = job_id in self.scheduled_recordings
        
        if was_scheduled and job_id in self.scheduled_recordings:
            del self.scheduled_recordings[job_id]
        
        self.active_recordings[job_id] = job
        
        recording_title = job.get("name", "Unknown")
        item = job.get("item", {})
        channel_info = {}
        
        channels = job.get("channels", [])
        channel_number = None
        if channels and len(channels) > 0:
            channel_number = channels[0]
            channel_data = self.channel_provider.get_channel_info(channel_number)
            if channel_data:
                channel_info = {
                    "number": channel_number,
                    "name": channel_data.get("name", ""),
                    "logo_url": channel_data.get("logo_url", "")
                }
            else:
                channel_info = {
                    "number": channel_number,
                    "name": f"Channel {channel_number}"
                }
        
        recording_start_time = time.time()
        
        program_start_time = job.get("start_time", 0)
        expected_duration = job.get("duration", 0)
            
        stream_count = 0
        if self.stream_count_enabled and channel_number is not None:
            channel_name = channel_info.get('name', f"Channel {channel_number}")
            device_name = f"DVR_Recording_{job_id}"
            activity_str = f"Recording ch{channel_number} {channel_name} from {device_name}"
            
            self.stream_tracker.process_activity(activity_str, job_id)
            stream_count = self.stream_tracker.get_stream_count()
            
        notification_key = f"recording-started-{job_id}"
        
        if not self.recording_started_enabled:
            return False
        
        if not self.alert_formatter.should_send_notification(
                self.session_manager, 
                notification_key, 
                self.alert_cooldown):
            return False
        
        recording_type = "(Scheduled)" if was_scheduled else "(Manual)"
        message_parts: Dict[str, Union[str, Dict[str, str]]] = {
            'status': f"{self.STATUS_EMOJI['started']} Recording {recording_type}",
            'details': f"Program: {recording_title}",
        }
        
        table_parts = []
        table_parts.append("-----------------------")
        table_parts.append(f"Recording: {self._format_time_only(int(recording_start_time))}")
        if program_start_time > 0 and abs(program_start_time - recording_start_time) > 60:
            table_parts.append(f"Program:   {self._format_time_only(int(program_start_time))}")
        if expected_duration > 0:
            table_parts.append(f"Duration:  {self._format_duration(expected_duration)}")
        if self.stream_count_enabled:
            table_parts.append(f"Total Streams: {stream_count}")
        message_parts['time_table'] = "\n".join(table_parts)
        
        if channel_info:
            message_parts['channel'] = {
                'number': str(channel_info.get('number', '')),
                'name': str(channel_info.get('name', ''))
            }
            
        if item and item.get("summary"):
            message_parts['custom'] = str(item.get("summary", ""))
            
        message = self.alert_formatter.format_message(
            message_parts,
            order=['channel', 'status', 'details', 'custom', 'time_table']
        )
        image_url = item.get("image_url")
        
        channel_name = channel_info.get('name', f"Channel {channel_info.get('number', 'Unknown')}")
        duration_str = self._format_duration(expected_duration) if expected_duration > 0 else ""
        log(f"Recording started {recording_type}: {recording_title} on {channel_name}, Duration: {duration_str}", level=LOG_STANDARD)
        if self.stream_count_enabled:
            log(f"Total Streams: {stream_count}", level=LOG_STANDARD)
            
        record_recording_event(
            event_type=f"Recording {recording_type}",
            program_name=recording_title,
            channel_name=channel_name
        )
        
        alert_sent = self.send_alert(self.ALERT_TITLE, message, image_url)
        
        if alert_sent:
            self.session_manager.record_notification(notification_key)
        
        return alert_sent
    
    def _handle_recording_completed(self, event_data: Dict[str, Any], recording_details: Optional[Dict[str, Any]]) -> bool:
        """Handles the recording completed event. Job details might be None."""
        value = event_data.get("Value", "")
        file_id = value.replace("recorded-", "") if value.startswith("recorded-") else None

        if not file_id:
            log(f"_handle_recording_completed: Could not extract file ID from event {event_data}", level=LOG_STANDARD)
            return False

        process_stream_only = not self.recording_completed_enabled
        
        if file_id in self.pending_recordings:
            return False

        recording = None
        try:
            recording = self.job_provider.get_recording_by_id(file_id)
        except Exception as e:
            log(f"Exception calling get_recording_by_id for {file_id}: {e}", level=LOG_STANDARD)
            self.pending_recordings[file_id] = {
                "first_seen": time.time(), "event_data": event_data,
                "check_count": 0, "last_check": time.time()
            }
            return False
        
        current_count = 0
        job_id_from_recording = recording.get("job_id") if recording else None
        if job_id_from_recording and self.stream_count_enabled:
            self.stream_tracker.process_activity({}, job_id_from_recording)
            current_count = self.stream_tracker.get_stream_count()
            if process_stream_only:
                 log(f"Recording completed {file_id} - stream tracking only", level=LOG_VERBOSE)
                 log(f"Total Streams: {current_count}", level=LOG_STANDARD)
                 return True
        
        is_processed = False
        if recording:
            if "processed" in recording:
                if isinstance(recording["processed"], bool):
                    is_processed = recording["processed"]
                elif isinstance(recording["processed"], str):
                    is_processed = recording["processed"].lower() == "true"
            elif "Processed" in recording:
                if isinstance(recording["Processed"], bool):
                    is_processed = recording["Processed"]
                elif isinstance(recording["Processed"], str):
                    is_processed = recording["Processed"].lower() == "true"
        
        if not recording or not is_processed:
            if file_id not in self.pending_recordings:
                self.pending_recordings[file_id] = {
                     "first_seen": time.time(), "event_data": event_data,
                     "check_count": 0, "last_check": time.time()
                 }
            return False

        return self._process_completed_recording(file_id, recording, event_data, current_count)

    def _handle_recording_deleted(self, event_data: Dict[str, Any], job_details: Optional[Dict[str, Any]]) -> bool:
        """Handles 'jobs.deleted'. Uses pre-fetched job_details if available, or checks caches."""
        if not self.recording_cancelled_enabled: return False

        job_id = event_data.get("ID") or event_data.get("Name") 
        
        if not job_id:
              log(f"_handle_recording_deleted: Missing Job ID in event data {event_data}", level=LOG_VERBOSE)
              return False

        scheduled_info = self.scheduled_recordings.pop(job_id, None)
        if scheduled_info:
            job = scheduled_info.get("job")
            source = "Scheduled Cache"
            if not job: 
                 log(f"Found scheduled info for {job_id} but no job data?", level=LOG_VERBOSE)
                 return False
                 
            log(f"Processing deletion for job {job_id} (Found via: {source})", level=LOG_VERBOSE)

            notification_key = f"recording-cancelled-{job_id}" 
            
            log(f"DEBUG: Checking cooldown for key '{notification_key}'. Cooldown period: {self.alert_cooldown}s", level=LOG_VERBOSE)
            should_send = self.alert_formatter.should_send_notification(self.session_manager, notification_key, self.alert_cooldown)
            log(f"DEBUG: should_send_notification returned: {should_send}", level=LOG_VERBOSE)

            if not should_send:
                log(f"Cooldown active for {notification_key} or alert disabled. Skipping cancellation alert.", level=LOG_VERBOSE)
                return False 

            channel_info = {}
            channels = job.get("channels", [])
            if channels:
                 channel_number = channels[0]
                 channel_data = self.channel_provider.get_channel_info(channel_number)
                 if channel_data: channel_info = {"number": channel_number, "name": channel_data.get("name",""), "logo_url": channel_data.get("logo_url","")}
                 else: channel_info = {"number": channel_number, "name": f"Channel {channel_number}"}

            recording_title = job.get("name", "Unknown")
            item = job.get("item", {})
            start_time_ts = job.get("start_time")
            start_time_str = self._format_datetime_friendly(start_time_ts) if start_time_ts else "Unknown Time"
            expected_duration = job.get("duration", 0)
            duration_str = self._format_duration(expected_duration) if expected_duration > 0 else ""
            channel_label = channel_info.get('name', f'Channel {channel_info.get("number", "Unknown")}')

            message_parts = {
                'status': f"{self.STATUS_EMOJI['cancelled']} Cancelled",
                'details': f"Program: {recording_title}" if self.settings.rd_program_name and recording_title else None,
                'time_table': (
                    f"-----------------------\nScheduled: {start_time_str}\n" + 
                    (f"Duration:  {duration_str}\n" if self.settings.rd_duration and duration_str else "")
                ).strip() or None,
                'channel': {'number': str(channel_info.get('number', '')), 'name': str(channel_info.get('name', ''))} if channel_info else None,
                'custom': str(item.get("summary", "")) if self.settings.rd_program_desc and item and item.get("summary") else None
            }
            message_parts = {k: v for k, v in message_parts.items() if v}

            message = self.alert_formatter.format_message(
                 message_parts,
                 order=['channel', 'status', 'details', 'custom', 'time_table']
             )
            image_url = item.get("image_url")
            
            log(f"DEBUG_SEND: Preparing to send cancellation alert for {job_id}. Message: {message[:100]}...", level=LOG_VERBOSE)
            try:
                self.send_alert(self.ALERT_TITLE, message, image_url)
                log(f"DEBUG_SEND: Call to self.send_alert completed for {job_id}.", level=LOG_VERBOSE)
            except Exception as send_err:
                log(f"ERROR_SEND: Exception during self.send_alert for {job_id}: {send_err}", level=LOG_VERBOSE)
                return False 
                
            log(f"Recording cancelled: {recording_title} on {channel_label}, Was scheduled for: {start_time_str}", level=LOG_STANDARD)
            self.session_manager.record_notification(notification_key)
            record_recording_event(
                 event_type="Cancelled", 
                 program_name=recording_title, 
                 channel_name=channel_label, 
                 scheduled_datetime=datetime.fromtimestamp(start_time_ts, self.tz) if start_time_ts else None
             )
            return True

        elif job_details:
             job = job_details
             if job_id in self.active_recordings: del self.active_recordings[job_id]
             source = "Pre-fetched"
             log(f"Deletion detected for active/pre-fetched job {job_id} (Source: {source}). Alert will be handled by completion/stopped status.", level=LOG_VERBOSE)
             if self.stream_count_enabled: self.stream_tracker.process_activity({}, job_id) 
             return True
            
        elif job_id in self.active_recordings:
             job = self.active_recordings.pop(job_id, None)
             source = "Active Cache"
             
             if job:
                 log(f"Deletion detected for active job {job_id} (Source: {source}). Alert will be handled by completion/stopped status.", level=LOG_VERBOSE)
                 if self.stream_count_enabled: self.stream_tracker.process_activity({}, job_id) 
                 return True
             else:
                 log(f"Job {job_id} was in active cache keys but value was None?", level=LOG_VERBOSE)
              
        if not job:
            log(f"Could not find details for deleted job {job_id} (Source: {source}) - cannot process cancellation.", level=LOG_VERBOSE)
            return False
            
        log(f"Processing deletion for active/pre-fetched job {job_id} (Found via: {source})", level=LOG_VERBOSE)
        notification_key = f"recording-cancelled-{job_id}"

        log(f"DEBUG: Checking cooldown for key '{notification_key}' (Active Job). Cooldown period: {self.alert_cooldown}s", level=LOG_VERBOSE)
        should_send = self.alert_formatter.should_send_notification(self.session_manager, notification_key, self.alert_cooldown)
        log(f"DEBUG: should_send_notification returned: {should_send} (Active Job)", level=LOG_VERBOSE)

        if not should_send:
            log(f"Cooldown active for {notification_key} or alert disabled. Skipping cancellation alert. (Active Job)", level=LOG_VERBOSE)
            if self.stream_count_enabled: self.stream_tracker.process_activity({}, job_id) 
            return False

        channel_info = {}
        channels = job.get("channels", [])
        if channels:
             channel_number = channels[0]
             channel_data = self.channel_provider.get_channel_info(channel_number)
             if channel_data: channel_info = {"number": channel_number, "name": channel_data.get("name",""), "logo_url": channel_data.get("logo_url","")}
             else: channel_info = {"number": channel_number, "name": f"Channel {channel_number}"}

        recording_title = job.get("name", "Unknown")
        item = job.get("item", {})
        start_time_ts = job.get("start_time")
        start_time_str = self._format_datetime_friendly(start_time_ts) if start_time_ts else "Unknown Time"
        channel_label = channel_info.get('name', f'Channel {channel_info.get("number", "Unknown")}')

        message_parts = {
            'status': f"{self.STATUS_EMOJI['cancelled']} Cancelled (Active)",
            'details': f"Program: {recording_title}" if self.settings.rd_program_name and recording_title else None,
            'time_table': (
                f"-----------------------\nOriginal Start: {start_time_str}\n" + 
                (f"Total Streams: {self.stream_tracker.get_stream_count()}\n" if self.stream_count_enabled else "")
             ).strip() or None,
            'channel': {'number': str(channel_info.get('number', '')), 'name': str(channel_info.get('name', ''))} if channel_info else None,
            'custom': str(item.get("summary", "")) if self.settings.rd_program_desc and item and item.get("summary") else None
        }
        message_parts = {k: v for k, v in message_parts.items() if v}

        message = self.alert_formatter.format_message(
             message_parts,
             order=['channel', 'status', 'details', 'custom', 'time_table']
         )
        image_url = item.get("image_url")

        log(f"DEBUG_SEND: Preparing to send ACTIVE cancellation alert for {job_id}. Message: {message[:100]}...", level=LOG_VERBOSE)
        try:
            self.send_alert(self.ALERT_TITLE, message, image_url)
            log(f"DEBUG_SEND: Call to self.send_alert completed for ACTIVE {job_id}.", level=LOG_VERBOSE)
        except Exception as send_err:
            log(f"ERROR_SEND: Exception during self.send_alert for ACTIVE {job_id}: {send_err}", level=LOG_VERBOSE)
            if self.stream_count_enabled: self.stream_tracker.process_activity({}, job_id) 
            return False 

        log(f"Recording cancelled (Active): {recording_title} on {channel_label}, Originally scheduled for: {start_time_str}", level=LOG_STANDARD)
        self.session_manager.record_notification(notification_key)
        record_recording_event(
             event_type="Cancelled (Active)", 
             program_name=recording_title, 
             channel_name=channel_label, 
             scheduled_datetime=datetime.fromtimestamp(start_time_ts, self.tz) if start_time_ts else None
         )
        
        if self.stream_count_enabled:
             self.stream_tracker.process_activity({}, job_id) 

        return True

    def _process_completed_recording(self, file_id: str, recording: Dict[str, Any], event_data: Dict[str, Any], stream_count: int = 0) -> bool:
        """Process a completed recording and send notification."""
        recording_id = recording.get("id", "")
        if recording_id and recording_id != file_id:
            log(f"WARNING: Processing recording with mismatched IDs. Requested: {file_id}, Got: {recording_id}", level=LOG_VERBOSE)
        
        is_cancelled = False
        if "cancelled" in recording:
            if isinstance(recording["cancelled"], bool):
                is_cancelled = recording["cancelled"]
            elif isinstance(recording["cancelled"], str):
                is_cancelled = recording["cancelled"].lower() == "true"
        elif "Cancelled" in recording:
            if isinstance(recording["Cancelled"], bool):
                is_cancelled = recording["Cancelled"]
            elif isinstance(recording["Cancelled"], str):
                is_cancelled = recording["Cancelled"].lower() == "true"
        
        is_completed = False
        if "completed" in recording:
            if isinstance(recording["completed"], bool):
                is_completed = recording["completed"]
            elif isinstance(recording["completed"], str):
                is_completed = recording["completed"].lower() == "true"
        elif "Completed" in recording:
            if isinstance(recording["Completed"], bool):
                is_completed = recording["Completed"]
            elif isinstance(recording["Completed"], str):
                is_completed = recording["Completed"].lower() == "true"
                
        is_delayed = False
        if "delayed" in recording:
            if isinstance(recording["delayed"], bool):
                is_delayed = recording["delayed"]
            elif isinstance(recording["delayed"], str):
                is_delayed = recording["delayed"].lower() == "true"
        elif "Delayed" in recording:
            if isinstance(recording["Delayed"], bool):
                is_delayed = recording["Delayed"]
            elif isinstance(recording["Delayed"], str):
                is_delayed = recording["Delayed"].lower() == "true"
        
        manually_stopped = is_cancelled and is_completed
        
        is_interrupted = is_cancelled and not is_completed
        
        if manually_stopped:
            notification_key = f"recording-stopped-{file_id}"
            status_type = "stopped"
        elif is_cancelled:
            notification_key = f"recording-cancelled-{file_id}"
            status_type = "cancelled"
        else:
            notification_key = f"recording-completed-{file_id}"
            status_type = "completed"
        
        if not self.alert_formatter.should_send_notification(
                self.session_manager, 
                notification_key, 
                self.alert_cooldown):
            log(f"Cooldown active for {notification_key}. Skipping alert, but marking as processed.", level=LOG_VERBOSE)
            job_id = recording.get("job_id")
            if job_id and job_id in self.active_recordings:
                 try: del self.active_recordings[job_id] 
                 except KeyError: pass
            return True 
            
        recording_title = recording.get("title", "Unknown")
        if recording.get("episode_title"):
            recording_title += f" - {recording.get('episode_title')}"
            
        status_messages = {
            "stopped": "Stopped",
            "cancelled": "Cancelled",
            "completed": "Completed"
        }
        
        status_suffix = ""
        if status_type == "completed":
            if is_delayed:
                status_suffix = " (Delayed)"
            elif is_interrupted:
                status_suffix = " (Interrupted)"
        
        message_parts: Dict[str, Union[str, Dict[str, str]]] = {
            'status': f"{self.STATUS_EMOJI[status_type]} {status_messages[status_type]}{status_suffix}",
            'details': f"Program: {recording_title}",
        }
        
        channel_number = recording.get("channel")
        channel_info = {}
        
        if channel_number:
            channel_data = None
            try:
                channel_data = self.channel_provider.get_channel_info(channel_number)
            except Exception as e:
                log(f"Exception getting channel info for {channel_number}: {e}", level=LOG_STANDARD)
            
            if channel_data:
                channel_info = {
                    "number": channel_number,
                    "name": channel_data.get("name", ""),
                    "logo_url": channel_data.get("logo_url", "")
                }
            else:
                channel_info = {
                    "number": channel_number,
                    "name": f"Channel {channel_number}"
                }
        
        actual_duration = recording.get("duration", 0)
                
        duration_str = self._format_duration(actual_duration)
        
        table_parts = []
        table_parts.append("-----------------------")
        table_parts.append(f"Duration:  {duration_str}")
        
        if self.stream_count_enabled:
            current_count = stream_count if isinstance(stream_count, int) and stream_count > 0 else self.stream_tracker.get_stream_count()
            table_parts.append(f"Total Streams: {current_count}")
        
        message_parts['time_table'] = "\n".join(table_parts)
        
        if channel_info:
            message_parts['channel'] = {
                'number': str(channel_info.get('number', '')),
                'name': str(channel_info.get('name', ''))
            }
            
        if recording.get("summary"):
            message_parts['custom'] = str(recording.get("summary", ""))
                
        message = self.alert_formatter.format_message(
            message_parts,
            order=['channel', 'status', 'details', 'custom', 'time_table']
        )
        image_url = recording.get("image_url")
        
        start_time_ts = recording.get("job", {}).get("start_time")
        start_time_str = self._format_datetime_friendly(start_time_ts) if start_time_ts else "Unknown Start Time"
        
        channel_name = channel_info.get('name', f"Channel {channel_info.get('number', 'Unknown')}")
        status_name = status_messages[status_type] + status_suffix
        duration_display = f"Duration: {duration_str}" if actual_duration > 0 else ""
        log(f"Recording {status_name.lower()}: {recording_title} on {channel_name} {duration_display}", level=LOG_STANDARD)
        
        record_recording_event(
            event_type=status_name, 
            program_name=recording_title,
            channel_name=channel_name
        )
        
        if self.stream_count_enabled:
            current_count = stream_count if isinstance(stream_count, int) and stream_count > 0 else self.stream_tracker.get_stream_count()
            log(f"Total Streams: {current_count}", level=LOG_STANDARD)
        
        try:
            self.send_alert(self.ALERT_TITLE, message, image_url)
        except Exception as send_err:
            log(f"ERROR_SEND: Exception during self.send_alert for {file_id}: {send_err}", level=LOG_STANDARD)
            pass
            
        self.session_manager.record_notification(notification_key)
        
        job_id = recording.get("job_id")
        if job_id and job_id in self.active_recordings:
             try: del self.active_recordings[job_id] 
             except KeyError: pass

        return True

    def run_cleanup(self) -> None:
        """Executes cleanup operations for stale recording data and sessions."""
        cleanup_start = time.time()
        log(f"Starting RecordingEventsAlert cleanup process", level=LOG_VERBOSE)
        
        try:
            try:
                self.session_manager.cleanup()
                log("Session manager cleanup completed", level=LOG_VERBOSE)
            except Exception as session_err:
                log(f"Error during session manager cleanup: {session_err}", level=LOG_VERBOSE)
                log(traceback.format_exc(), level=LOG_VERBOSE)
            
            stale_jobs = []
            active_job_ids = list(self.active_recordings.keys())
            active_jobs_checked = 0
            MAX_ACTIVE_CHECKS_PER_CYCLE = 50
            
            log(f"Checking {len(active_job_ids)} active recordings (max {MAX_ACTIVE_CHECKS_PER_CYCLE} per cycle)", level=LOG_VERBOSE)
            
            for job_id in active_job_ids:
                if active_jobs_checked >= MAX_ACTIVE_CHECKS_PER_CYCLE:
                    log(f"Reached limit of {MAX_ACTIVE_CHECKS_PER_CYCLE} active job checks, will continue in next cycle", level=LOG_VERBOSE)
                    break
                active_jobs_checked += 1
                
                if job_id not in self.active_recordings:
                    continue
                    
                is_active = False
                try:
                    start_time = time.time()
                    is_active = self.job_provider.is_job_active(job_id)
                    request_time = time.time() - start_time
                    
                    if request_time > 2.0:
                        log(f"Slow job status check for {job_id}: {request_time:.2f}s", level=LOG_VERBOSE)
                        
                except Exception as e:
                    log(f"Error checking active status for job {job_id}: {e}", level=LOG_VERBOSE)
                    log(traceback.format_exc(), level=LOG_VERBOSE)
                    continue
                    
                if not is_active:
                    log(f"Job {job_id} is no longer active, marking for removal", level=LOG_VERBOSE)
                    stale_jobs.append(job_id)
            
            if stale_jobs:
                for job_id in stale_jobs:
                    if job_id in self.active_recordings:
                        try:
                            del self.active_recordings[job_id]
                            log(f"Removed stale job {job_id} from active recordings", level=LOG_VERBOSE)
                        except KeyError:
                            pass
                log(f"Removed {len(stale_jobs)} stale jobs from active recordings", level=LOG_VERBOSE)
            
            stale_scheduled = []
            scheduled_job_ids = list(self.scheduled_recordings.keys())
            scheduled_jobs_checked = 0
            MAX_SCHEDULED_CHECKS_PER_CYCLE = 50
            current_time = time.time()

            log(f"Checking {len(scheduled_job_ids)} scheduled recordings (max {MAX_SCHEDULED_CHECKS_PER_CYCLE} per cycle)", level=LOG_VERBOSE)
            
            for job_id in scheduled_job_ids:
                if scheduled_jobs_checked >= MAX_SCHEDULED_CHECKS_PER_CYCLE:
                    log(f"Reached limit of {MAX_SCHEDULED_CHECKS_PER_CYCLE} scheduled job checks, will continue in next cycle", level=LOG_VERBOSE)
                    break
                scheduled_jobs_checked += 1
                
                if job_id not in self.scheduled_recordings:
                    continue
                    
                info = self.scheduled_recordings[job_id]
                is_active = True
                try:
                    start_time = time.time()
                    is_active = self.job_provider.is_job_active(job_id)
                    request_time = time.time() - start_time
                    
                    if request_time > 2.0:
                        log(f"Slow scheduled job status check for {job_id}: {request_time:.2f}s", level=LOG_VERBOSE)
                        
                except Exception as e:
                    log(f"Error checking active status for scheduled job {job_id}: {e}", level=LOG_VERBOSE)
                    log(traceback.format_exc(), level=LOG_VERBOSE)
                    continue
                    
                if not is_active:
                    log(f"Scheduled job {job_id} is no longer active, marking for removal", level=LOG_VERBOSE)
                    stale_scheduled.append(job_id)
                    continue
                    
                if current_time - info.get("created_at", 0) > 86400:
                    log(f"Scheduled job {job_id} was created over 24 hours ago, marking as stale", level=LOG_VERBOSE)
                    stale_scheduled.append(job_id)
                    
            if stale_scheduled:
                for job_id in stale_scheduled:
                    if job_id in self.scheduled_recordings:
                        try:
                            del self.scheduled_recordings[job_id]
                            log(f"Removed stale job {job_id} from scheduled recordings", level=LOG_VERBOSE)
                        except KeyError:
                            pass
                log(f"Removed {len(stale_scheduled)} stale jobs from scheduled recordings", level=LOG_VERBOSE)
            
            pending_to_remove = []
            pending_count = len(self.pending_recordings)
            if pending_count > 0:
                log(f"Checking {pending_count} pending recordings", level=LOG_VERBOSE)
                for file_id, info in self.pending_recordings.items():
                    retry_count = info.get("retry_count", 0)
                    first_seen = info.get("first_seen", 0)
                    
                    if retry_count >= self.max_retries or (current_time - first_seen) > 21600:
                        pending_to_remove.append(file_id)
                
                for file_id in pending_to_remove:
                    try:
                        del self.pending_recordings[file_id]
                    except KeyError:
                        pass
                
                if pending_to_remove:
                    log(f"Removed {len(pending_to_remove)} stale pending recordings", level=LOG_VERBOSE)
            
            total_removed = len(stale_jobs) + len(stale_scheduled) + len(pending_to_remove)
            if total_removed > 0:
                log(f"Removed {total_removed} total stale items during cleanup", level=LOG_VERBOSE)
            
            if self.stream_count_enabled:
                try:
                    self.stream_tracker.cleanup_stale_sessions()
                    log("Stream tracker cleanup completed", level=LOG_VERBOSE)
                except Exception as stream_err:
                    log(f"Error during stream tracker cleanup: {stream_err}", level=LOG_VERBOSE)
                    log(traceback.format_exc(), level=LOG_VERBOSE)
            
            try:
                job_count = self.job_provider.cache_jobs()
                log(f"Refreshed job cache during cleanup, found {job_count} jobs", level=LOG_VERBOSE)
            except Exception as cache_err:
                log(f"Error refreshing job cache during cleanup: {cache_err}", level=LOG_VERBOSE)
                log(traceback.format_exc(), level=LOG_VERBOSE)
                
            if self._lock_health["acquisition_count"] > self._lock_health["release_count"]:
                log(f"Lock health during cleanup: {self._lock_health['acquisition_count']} acquisitions, " +
                    f"{self._lock_health['release_count']} releases. Lock may be stuck.", level=LOG_VERBOSE)
            
            cleanup_time = time.time() - cleanup_start
            log(f"RecordingEventsAlert cleanup completed in {cleanup_time:.2f}s", level=LOG_VERBOSE)
                
        except Exception as e:
            cleanup_time = time.time() - cleanup_start
            log(f"Critical error in recording events cleanup after {cleanup_time:.2f}s: {e}", level=LOG_VERBOSE)
            log(traceback.format_exc(), level=LOG_VERBOSE)
    
    def cleanup(self) -> None:
        """Executes the main cleanup routine for the recording events alert system."""
        self.run_cleanup() 
        
    def set_startup_complete(self):
        """Marks the startup process as complete to enable alert notifications."""
        pass 