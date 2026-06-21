"""Manages and alerts on DVR recording events including scheduling, starting, completion, and cancellation."""

import asyncio
import time
from typing import Dict, Any, Optional, Union
from datetime import datetime, timedelta
import pytz
import traceback

from .base import BaseAlert
from .common.session_manager import SessionManager
from .common.alert_formatter import AlertFormatter
from .common.cleanup_mixin import CleanupMixin
from .common.stream_tracker import StreamTracker
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.channel_info import ChannelInfoProvider
from ..helpers.job_info import JobInfoProvider
from ..helpers.activity_recorder import record_recording_event


class RecordingEventsAlert(BaseAlert, CleanupMixin):
    """Monitors and alerts on DVR recording events with status tracking."""

    ALERT_TYPE = "Recording-Events"
    ROUTING_EVENT_TYPE = "recording"
    DESCRIPTION = (
        "Notifications for recording events (scheduled, started, cancelled, completed)"
    )

    STATUS_EMOJI = {
        "scheduled": "📅",
        "started": "🔴",
        "completed": "✅",
        "cancelled": "🚫",
        "stopped": "⏹️",
        "failed": "⚠️",
    }

    ALERT_TITLE = "Channels DVR - Recording Event"

    def __init__(self, alert_manager):
        """Initializes the Recording-Events alert from an AlertManager instance."""
        BaseAlert.__init__(self, alert_manager.notification_manager)
        CleanupMixin.__init__(self)

        self.alert_manager = alert_manager
        settings = alert_manager.settings
        dvr = alert_manager.dvr

        self.settings = settings
        self.session_manager = SessionManager()
        self._notification_history: Dict[str, float] = (
            alert_manager._notification_history
        )
        self._event_lock = asyncio.Lock()

        self.dvr = dvr
        host = dvr.host if dvr else None
        port = dvr.port if dvr else 8089
        timezone = settings.tz

        try:
            self.tz = pytz.timezone(timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            self.tz = pytz.timezone("UTC")
            log(
                f"Invalid timezone '{timezone}' from config, using UTC",
                level=LOG_STANDARD,
            )

        self.tz_abbr = datetime.now(self.tz).strftime("%Z")

        show_program_name = settings.rd_program_name
        show_program_desc = settings.rd_program_desc
        show_duration = settings.rd_duration
        show_channel_name = settings.rd_channel_name
        show_channel_number = settings.rd_channel_number
        show_type = settings.rd_type

        self.alert_formatter = AlertFormatter(
            config={
                "show_channel_name": show_channel_name,
                "show_channel_number": show_channel_number,
                "show_program_name": show_program_name,
                "show_program_desc": show_program_desc,
                "show_duration": show_duration,
                "show_type": show_type,
                "use_emoji": True,
                "title_prefix": "",
                "image_support": True,
                "html_escape": True,
            }
        )

        self.active_recordings = {}
        self.scheduled_recordings = {}
        self.pending_recordings = {}

        self.max_retries = 5
        self.retry_interval = 2
        self.time_module = time
        self.alert_cooldown = 60
        self.template_settings = {
            "title": settings.rd_template_title,
            "body": settings.rd_template_body,
            "use_default": settings.rd_template_use_default,
        }

        channel_cache_ttl = settings.channel_cache_ttl
        job_cache_ttl = settings.job_cache_ttl

        self.channel_provider = ChannelInfoProvider(
            str(host) if host is not None else "", port, cache_ttl=channel_cache_ttl
        )
        self.job_provider = JobInfoProvider(
            str(host) if host is not None else "", port, cache_ttl=job_cache_ttl
        )

        self.stream_tracker = (
            StreamTracker(dvr=dvr)
            if dvr
            else StreamTracker(host=str(host) if host is not None else "", port=port)
        )

        self.stream_count_enabled = settings.stream_count
        self.recording_scheduled_enabled = settings.rd_alert_scheduled
        self.recording_started_enabled = settings.rd_alert_started
        self.recording_completed_enabled = settings.rd_alert_completed
        self.recording_cancelled_enabled = settings.rd_alert_cancelled

        self.configure_cleanup(enabled=True, interval=3600, auto_cleanup=True)

        self._last_event_time = time.time()
        self._event_counter = 0
        self._lock_health: Dict[str, Any] = {
            "last_acquisition": 0,
            "last_release": 0,
            "current_holder": None,
            "acquisition_count": 0,
            "release_count": 0,
        }

        log("RecordingEventsAlert: Initialized", level=LOG_VERBOSE)

    def _format_clock_time(self, dt: datetime) -> str:
        return dt.strftime("%I:%M %p").lstrip("0") + f" {self.tz_abbr}"

    def create_background_tasks(self) -> list:
        return [
            asyncio.create_task(self._async_retry_loop(), name="recording-retry"),
            asyncio.create_task(self._async_watchdog_loop(), name="recording-watchdog"),
        ]

    async def _async_retry_loop(self):
        while True:
            try:
                await asyncio.sleep(self.retry_interval)
                await self._async_check_pending_recordings()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log(f"Error in retry loop: {e}", level=LOG_VERBOSE)

    async def _async_watchdog_loop(self):
        last_reported_issue = 0
        recovery_attempts = 0
        watchdog_cycle = 0

        while True:
            try:
                for _ in range(30):
                    await asyncio.sleep(10)

                watchdog_cycle += 1
                current_time = time.time()
                time_since_last_event = current_time - self._last_event_time

                if watchdog_cycle % 12 == 0:
                    log(
                        f"Watchdog health check: {self._event_counter} events processed, "
                        f"last event {time_since_last_event:.0f} seconds ago",
                        level=LOG_VERBOSE,
                    )

                if time_since_last_event > 1800 and self._event_counter > 0:
                    if current_time - last_reported_issue > 3600:
                        last_reported_issue = current_time
                        log(
                            f"WARNING: No recording events for {time_since_last_event:.0f}s. "
                            "Event handling may be stalled.",
                            level=LOG_VERBOSE,
                        )

                        if (
                            self._lock_health["acquisition_count"]
                            > self._lock_health["release_count"]
                        ):
                            log(
                                f"Lock health: {self._lock_health['acquisition_count']} acquisitions, "
                                f"{self._lock_health['release_count']} releases. Lock may be stuck.",
                                level=LOG_VERBOSE,
                            )
                            lock_held_time = (
                                current_time - self._lock_health["last_acquisition"]
                            )
                            if lock_held_time > 1200:
                                log(
                                    f"Lock stuck for {lock_held_time:.0f}s. Skipping force-reset to avoid concurrent state mutation.",
                                    level=LOG_VERBOSE,
                                )

                        try:
                            recovery_attempts += 1
                            log(
                                f"Watchdog recovery attempt {recovery_attempts}",
                                level=LOG_VERBOSE,
                            )
                            await self.run_cleanup()

                            if recovery_attempts >= 2:
                                await asyncio.to_thread(self.job_provider.cache_jobs)

                                if recovery_attempts >= 3:
                                    log(
                                        "Watchdog: resetting internal state",
                                        level=LOG_VERBOSE,
                                    )
                                    self.pending_recordings = {}
                                    self._last_event_time = current_time
                        except Exception as e:
                            log(f"Watchdog recovery error: {e}", level=LOG_VERBOSE)
                            log(traceback.format_exc(), level=LOG_VERBOSE)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log(f"Error in watchdog loop: {e}", level=LOG_VERBOSE)
                log(traceback.format_exc(), level=LOG_VERBOSE)
                await asyncio.sleep(60)

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
                    "created_at": current_time,
                }
                scheduled_count += 1

    def _format_datetime(self, timestamp: int) -> str:
        """Formats a timestamp into a readable date/time with timezone."""
        dt = datetime.fromtimestamp(timestamp, self.tz)
        time_str = self._format_clock_time(dt)
        return f"{dt.strftime('%b %d, %Y')} {time_str}"

    def _format_datetime_friendly(self, timestamp: int) -> str:
        """Formats a timestamp into a user-friendly date/time with timezone using Today/Tomorrow when applicable."""
        dt = datetime.fromtimestamp(timestamp, self.tz)
        now = datetime.now(self.tz)

        time_str = self._format_clock_time(dt)

        if dt.date() == now.date():
            return f"Today at {time_str}"

        if dt.date() == (now.date() + timedelta(days=1)):
            return f"Tomorrow at {time_str}"

        return f"{dt.strftime('%b %d, %Y')} {time_str}"

    def _format_time_only(self, timestamp: int) -> str:
        """Formats a timestamp into time with timezone for active recordings."""
        dt = datetime.fromtimestamp(timestamp, self.tz)
        return self._format_clock_time(dt)

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

    def _build_recording_template_context(
        self,
        *,
        recording_status: str,
        recording_status_friendly: str,
        title: str,
        default_message: str,
        channel_info: Optional[Dict[str, Any]] = None,
        item: Optional[Dict[str, Any]] = None,
        start_time: str = "",
        end_time: str = "",
        duration: str = "",
        image_url: str = "",
        summary: str = "",
        error_message: str = "",
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        item_data = item or {}
        channel_data = channel_info or {}
        episode_title = item_data.get("episode_title", "") or item_data.get(
            "EpisodeTitle", ""
        )
        season_number = item_data.get("season_number", "") or item_data.get(
            "SeasonNumber", ""
        )
        episode_number = item_data.get("episode_number", "") or item_data.get(
            "EpisodeNumber", ""
        )

        return self.alert_formatter.build_context(
            alert_type="recording_events",
            dvr=self.dvr,
            extra_context={
                "recording_status": recording_status,
                "recording_status_friendly": recording_status_friendly,
                "job_id": item_data.get("job_id", "") or item_data.get("JobID", ""),
                "title": title,
                "show_name": item_data.get("show_name", "")
                or item_data.get("ShowTitle", "")
                or title,
                "episode_title": episode_title,
                "season_number": season_number,
                "season_number00": str(season_number).zfill(2)
                if season_number not in (None, "")
                else "",
                "episode_number": episode_number,
                "episode_number00": str(episode_number).zfill(2)
                if episode_number not in (None, "")
                else "",
                "channel_number": channel_data.get("number", ""),
                "channel_name": channel_data.get("name", ""),
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "summary": summary,
                "image_url": image_url,
                "content_rating": item_data.get("content_rating", "")
                or item_data.get("ContentRating", ""),
                "genres": item_data.get("genres", []) or item_data.get("Genres", []),
                "cast": item_data.get("cast", []) or item_data.get("Cast", []),
                "error_message": error_message,
                "file_path": item_data.get("path", "") or item_data.get("Path", ""),
                "is_pass": item_data.get("is_pass", False)
                or item_data.get("IsPass", False),
                "pass_name": item_data.get("pass_name", "")
                or item_data.get("PassName", ""),
                "completed": item_data.get("completed", False)
                or item_data.get("Completed", False),
                "corrupted": item_data.get("corrupted", False)
                or item_data.get("Corrupted", False),
                "processed": item_data.get("processed", False)
                or item_data.get("Processed", False),
                "media_type": "episode" if episode_title else "show",
                "status": recording_status_friendly,
                "details": title,
                "summary_block": summary,
                "default_message": default_message,
                **(extra_context or {}),
            },
        )

    def _resolve_recording_image_url(
        self,
        *,
        item: Optional[Dict[str, Any]] = None,
        recording: Optional[Dict[str, Any]] = None,
        channel_info: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Resolve the best available image for recording alerts/activity.

        Prefer explicit program/recording artwork first, then fall back to channel logo.
        """
        candidates: list[Any] = []
        for source in (item or {}, recording or {}):
            candidates.extend(
                [
                    source.get("image_url"),
                    source.get("image"),
                    source.get("icon_url"),
                    source.get("thumbnail_url"),
                    source.get("thumb"),
                ]
            )

        if channel_info:
            candidates.append(channel_info.get("logo_url"))

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                if recording is not None:
                    recording["artwork_fallback_exhausted"] = False
                return candidate

        if recording is not None:
            recording["artwork_fallback_exhausted"] = True
        return ""

    def _format_recording_alert(
        self, *, default_message: str, image_url: Optional[str], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self.alert_formatter.format_templated_alert(
            alert_type="recording_events",
            default_title=self.ALERT_TITLE,
            default_message=default_message,
            context=context,
            template_settings=self.template_settings,
            image_url=image_url,
        )

    async def _async_check_pending_recordings(self):
        MAX_PENDING_CHECKS_PER_CYCLE = 10
        items_to_check = []

        try:
            await asyncio.wait_for(self._event_lock.acquire(), timeout=1.0)
        except asyncio.TimeoutError:
            log(
                "Could not acquire event lock for pending check (read phase), skipping cycle.",
                level=LOG_VERBOSE,
            )
            return

        try:
            all_file_ids = list(self.pending_recordings.keys())
            if all_file_ids:
                log(
                    f"Pending check: {len(all_file_ids)} items in queue.",
                    level=LOG_VERBOSE,
                )
            items_processed = 0
            current_time_snapshot = time.time()

            for file_id in all_file_ids:
                if items_processed >= MAX_PENDING_CHECKS_PER_CYCLE:
                    log(
                        "Pending check limit reached, continuing next cycle.",
                        level=LOG_VERBOSE,
                    )
                    break
                if file_id not in self.pending_recordings:
                    continue
                pending_info = self.pending_recordings[file_id]
                if (
                    current_time_snapshot - pending_info.get("last_check", 0)
                    >= self.retry_interval
                ):
                    check_count = pending_info.get("check_count", 0) + 1
                    self.pending_recordings[file_id]["check_count"] = check_count
                    self.pending_recordings[file_id]["last_check"] = (
                        current_time_snapshot
                    )
                    items_to_check.append((file_id, dict(pending_info), check_count))
                    items_processed += 1
        finally:
            self._event_lock.release()

        if not items_to_check:
            return

        log(
            f"Pending check: Processing {len(items_to_check)} items outside lock.",
            level=LOG_VERBOSE,
        )
        max_wait_time = 600
        current_time_processing = time.time()

        for file_id, pending_info, check_count in items_to_check:
            should_delete = False
            delete_reason = ""

            try:
                log(
                    f"Pending check: Fetching recording {file_id} (Attempt #{check_count})",
                    level=LOG_VERBOSE,
                )
                recording = await asyncio.to_thread(
                    self.job_provider.get_recording_by_id, file_id
                )
                log(
                    f"Pending check: Fetched {file_id} ({'Found' if recording else 'Not Found'})",
                    level=LOG_VERBOSE,
                )

                if not recording:
                    if (
                        current_time_processing - pending_info.get("first_seen", 0)
                        > max_wait_time
                    ):
                        should_delete = True
                        delete_reason = f"timeout ({max_wait_time}s)"
                    continue

                returned_id = recording.get("id")
                if returned_id and returned_id != file_id:
                    log(
                        f"API returned wrong ID: requested {file_id}, got {returned_id}. Fallback lookup.",
                        level=LOG_VERBOSE,
                    )
                    try:
                        all_recs = await asyncio.to_thread(
                            self.job_provider.get_all_recordings
                        )
                        correct_recording = next(
                            (r for r in (all_recs or []) if r.get("id") == file_id),
                            None,
                        )
                        if correct_recording:
                            recording = correct_recording
                            log(
                                f"Found correct recording {file_id} in full list.",
                                level=LOG_VERBOSE,
                            )
                        else:
                            log(
                                f"Could not find {file_id} in full list.",
                                level=LOG_VERBOSE,
                            )
                    except Exception as lookup_err:
                        log(
                            f"Fallback lookup error for {file_id}: {lookup_err}",
                            level=LOG_VERBOSE,
                        )
                        continue

                is_processed = False
                for key in ("processed", "Processed"):
                    val = recording.get(key)
                    if val is not None:
                        is_processed = (
                            val if isinstance(val, bool) else str(val).lower() == "true"
                        )
                        break

                if is_processed:
                    log(
                        f"Processing recording {file_id} (is_processed=True)",
                        level=LOG_VERBOSE,
                    )
                    current_count = 0
                    if self.stream_count_enabled:
                        try:
                            current_count = await self.stream_tracker.get_stream_count()
                        except Exception as tracker_err:
                            log(f"Stream count error: {tracker_err}", level=LOG_VERBOSE)
                    try:
                        processed_ok = await self._process_completed_recording(
                            file_id,
                            recording,
                            pending_info.get("event_data", {}),
                            current_count,
                        )
                        should_delete = True
                        delete_reason = f"processed (Result: {processed_ok})"
                    except Exception as process_err:
                        log(
                            f"Error in _process_completed_recording for {file_id}: {process_err}",
                            level=LOG_VERBOSE,
                        )

                elif (
                    current_time_processing - pending_info.get("first_seen", 0)
                    > max_wait_time
                ):
                    should_delete = True
                    delete_reason = f"timeout and not processed ({max_wait_time}s)"

            except Exception as outer_err:
                log(
                    f"Unexpected error processing pending item {file_id}: {outer_err}",
                    level=LOG_VERBOSE,
                )
                should_delete = False

            if should_delete:
                log(
                    f"Removing {file_id} from pending queue (Reason: {delete_reason})",
                    level=LOG_VERBOSE,
                )
                try:
                    await asyncio.wait_for(self._event_lock.acquire(), timeout=1.0)
                except asyncio.TimeoutError:
                    log(
                        "Could not acquire lock for delete phase, will retry next cycle.",
                        level=LOG_VERBOSE,
                    )
                    continue
                try:
                    if file_id in self.pending_recordings:
                        del self.pending_recordings[file_id]
                        log(f"Removed pending recording {file_id}.", level=LOG_VERBOSE)
                finally:
                    self._event_lock.release()

    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determines if this alert should handle the given recording event type."""
        if event_type == "jobs.created" and "Name" in event_data:
            return True

        if (
            event_type == "programs.set"
            and "Name" in event_data
            and "Value" in event_data
        ):
            value = event_data.get("Value", "")
            if value.startswith("recording-"):
                return True
            if value.startswith("recorded-"):
                return True

        if event_type == "jobs.deleted" and "Name" in event_data:
            return True

        return False

    async def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        self._last_event_time = time.time()
        self._event_counter += 1

        if not isinstance(event_data, dict):
            log(
                f"Invalid event data format for {event_type}: not a dictionary",
                level=LOG_VERBOSE,
            )
            return False

        if not self._should_handle_event(event_type, event_data):
            return False

        job_id_to_fetch = None
        file_id_to_fetch = None
        job_details = None
        recording_details = None
        error_occurred = False

        if event_type in ("jobs.created", "jobs.deleted"):
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
            log(
                f"Pre-fetching job details for '{job_id_to_fetch}' (Event: {event_type})",
                level=LOG_VERBOSE,
            )
            try:
                t0 = time.time()
                job_details = await asyncio.to_thread(
                    self.job_provider.get_job_by_id, job_id_to_fetch
                )
                elapsed = time.time() - t0
                if elapsed > 2.0:
                    log(
                        f"Job fetch for '{job_id_to_fetch}' slow: {elapsed:.2f}s",
                        level=LOG_VERBOSE,
                    )
                if not job_details:
                    log(
                        f"Failed to pre-fetch job details for '{job_id_to_fetch}'",
                        level=LOG_VERBOSE,
                    )
                    if event_type != "jobs.deleted":
                        error_occurred = True
                else:
                    log(
                        f"Pre-fetched job details for '{job_id_to_fetch}'",
                        level=LOG_VERBOSE,
                    )
            except Exception as fetch_err:
                log(
                    f"Exception pre-fetching job '{job_id_to_fetch}': {fetch_err}",
                    level=LOG_VERBOSE,
                )
                log(traceback.format_exc(), level=LOG_VERBOSE)
                if event_type != "jobs.deleted":
                    error_occurred = True
        elif file_id_to_fetch:
            log(
                f"Pre-fetching recording details for '{file_id_to_fetch}'",
                level=LOG_VERBOSE,
            )
            try:
                t0 = time.time()
                recording_details = await asyncio.to_thread(
                    self.job_provider.get_recording_by_id, file_id_to_fetch
                )
                elapsed = time.time() - t0
                if elapsed > 2.0:
                    log(
                        f"Recording fetch for '{file_id_to_fetch}' slow: {elapsed:.2f}s",
                        level=LOG_VERBOSE,
                    )
                if not recording_details:
                    log(
                        f"Failed to pre-fetch recording '{file_id_to_fetch}'. Will add to pending.",
                        level=LOG_VERBOSE,
                    )
                else:
                    log(
                        f"Pre-fetched recording details for '{file_id_to_fetch}'",
                        level=LOG_VERBOSE,
                    )
            except Exception as fetch_err:
                log(
                    f"Exception pre-fetching recording '{file_id_to_fetch}': {fetch_err}",
                    level=LOG_VERBOSE,
                )
                log(traceback.format_exc(), level=LOG_VERBOSE)

        if error_occurred:
            if event_type == "jobs.created" or (
                event_type == "programs.set" and job_id_to_fetch
            ):
                log(
                    f"Aborting {event_type} due to pre-fetch error.", level=LOG_STANDARD
                )
                return False
            log(
                f"Pre-fetch error for {event_type}, proceeding anyway.",
                level=LOG_VERBOSE,
            )

        lock_start_time = time.time()
        try:
            log(f"Recording lock health update for {event_type}...", level=LOG_VERBOSE)
            async with self._event_lock:
                self._lock_health["last_acquisition"] = time.time()
                self._lock_health["acquisition_count"] += 1
                acq_time = time.time() - lock_start_time
                if acq_time > 0.5:
                    log(
                        f"Slow lock acquisition for {event_type}: {acq_time:.2f}s",
                        level=LOG_VERBOSE,
                    )
                self._lock_health["last_release"] = time.time()
                self._lock_health["release_count"] += 1
                self._lock_health["current_holder"] = None

            result = await self._handle_event_critical(
                event_type, event_data, job_details, recording_details
            )
            log(
                f"Processing complete for {event_type}. Result: {result}",
                level=LOG_VERBOSE,
            )
            return bool(result)

        except Exception as lock_err:
            log(
                f"Exception during lock handling for {event_type}: {lock_err}",
                level=LOG_VERBOSE,
            )
            log(traceback.format_exc(), level=LOG_VERBOSE)
            return False

    async def _handle_event_critical(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        job_details: Optional[Dict[str, Any]],
        recording_details: Optional[Dict[str, Any]],
    ) -> bool:
        try:
            if event_type == "jobs.created":
                return await self._handle_recording_created(event_data, job_details)
            elif event_type == "jobs.deleted":
                return await self._handle_recording_deleted(event_data, job_details)
            elif event_type == "programs.set":
                value = event_data.get("Value", "")
                if value.startswith("recording-"):
                    return await self._handle_recording_started(event_data, job_details)
                elif value.startswith("recorded-"):
                    return await self._handle_recording_completed(
                        event_data, recording_details
                    )
                else:
                    log(
                        f"Unhandled programs.set value inside lock: {value}",
                        level=LOG_VERBOSE,
                    )
            else:
                log(
                    f"Unhandled event type inside lock: {event_type}", level=LOG_VERBOSE
                )
        except Exception as e:
            log(
                f"Error in event critical section for {event_type}: {e}",
                level=LOG_VERBOSE,
            )
            log(traceback.format_exc(), level=LOG_VERBOSE)
        return False

    # RECORDING HANDLERS
    async def _handle_recording_created(
        self, event_data: Dict[str, Any], job_details: Optional[Dict[str, Any]]
    ) -> bool:
        """Handles the recording created event using pre-fetched job details."""
        if not job_details:
            log(
                f"_handle_recording_created: Missing job details for event {event_data.get('Name')}",
                level=LOG_STANDARD,
            )
            return False

        job_id = job_details.get("id")
        if not job_id:
            log(
                "_handle_recording_created: Missing job ID in provided job_details",
                level=LOG_STANDARD,
            )
            return False

        job = job_details

        current_time = time.time()
        start_time = job.get("start_time", 0)

        is_scheduled = (start_time - current_time) > 30

        if not is_scheduled:
            return False

        async with self._event_lock:
            self.active_recordings[job_id] = job
            self.scheduled_recordings[job_id] = {"job": job, "created_at": current_time}

        recording_title = job.get("name", "Unknown")
        item = job.get("item", {})
        channel_info = {}

        channels = job.get("channels", [])
        channel_number = None
        if channels and len(channels) > 0:
            channel_number = channels[0]
            channel_data = await asyncio.to_thread(
                self.channel_provider.get_channel_info, channel_number
            )
            if channel_data:
                channel_info = {
                    "number": channel_number,
                    "name": channel_data.get("name", ""),
                    "logo_url": channel_data.get("logo_url", ""),
                }
            else:
                channel_info = {
                    "number": channel_number,
                    "name": f"Channel {channel_number}",
                }

        start_time_str = self._format_datetime_friendly(start_time)

        expected_duration = job.get("duration", 0)
        duration_str = ""
        if expected_duration > 0:
            duration_str = self._format_duration(expected_duration)

        notification_key = f"recording-scheduled-{job_id}"

        channel_label = channel_info.get(
            "name", f"Channel {channel_info.get('number', 'Unknown')}"
        )
        log(
            f"Scheduled recording: {recording_title} on {channel_label} at {start_time_str}, Duration: {duration_str}",
            level=LOG_STANDARD,
        )

        await asyncio.to_thread(
            record_recording_event,
            event_type="Scheduled",
            program_name=recording_title,
            channel_name=channel_label,
            scheduled_datetime=datetime.fromtimestamp(start_time, self.tz),
            image_url=item.get("image_url", ""),
            extra={"duration": duration_str, "recording_type": "Scheduled"}
            if duration_str
            else {"recording_type": "Scheduled"},
            notification_history=self._notification_history,
        )

        if self.recording_scheduled_enabled and getattr(
            self.settings, "alert_recording_events", True
        ):
            should_send = await self.alert_formatter.should_send_notification(
                self.session_manager, notification_key, self.alert_cooldown
            )
            if should_send:
                message_parts: Dict[str, Union[str, Dict[str, str]]] = {
                    "status": f"{self.STATUS_EMOJI['scheduled']} Scheduled",
                }
                if self.settings.rd_program_name and recording_title:
                    message_parts["details"] = f"Program: {recording_title}"
                table_parts = []
                table_parts.append("-----------------------")
                table_parts.append(f"Scheduled: {start_time_str}")
                if self.settings.rd_duration and duration_str:
                    table_parts.append(f"Duration:  {duration_str}")
                message_parts["time_table"] = "\n".join(table_parts)
                if channel_info:
                    message_parts["channel"] = {
                        "number": str(channel_info.get("number", "")),
                        "name": str(channel_info.get("name", "")),
                    }
                if self.settings.rd_program_desc and item and item.get("summary"):
                    message_parts["custom"] = str(item.get("summary", ""))
                message = self.alert_formatter.format_message(
                    message_parts,
                    order=["channel", "status", "details", "custom", "time_table"],
                )
                image_url = self._resolve_recording_image_url(
                    item=item, channel_info=channel_info
                )
                formatted_alert = self._format_recording_alert(
                    default_message=message,
                    image_url=image_url,
                    context=self._build_recording_template_context(
                        recording_status="scheduled",
                        recording_status_friendly="Scheduled",
                        title=recording_title,
                        default_message=message,
                        channel_info=channel_info,
                        item=item,
                        start_time=start_time_str,
                        duration=duration_str,
                        image_url=image_url or "",
                        summary=str(item.get("summary", ""))
                        if item and item.get("summary")
                        else "",
                    ),
                )
                await self.send_alert_async(
                    formatted_alert["title"],
                    formatted_alert["message"],
                    formatted_alert.get("image_url"),
                )
                await self.session_manager.record_notification(notification_key)

        return True

    async def _handle_recording_started(
        self, event_data: Dict[str, Any], job_details: Optional[Dict[str, Any]]
    ) -> bool:
        """Handles the recording started event using job details (might be None if called from programs.set)."""
        job_id = None
        if job_details:
            job_id = job_details.get("id")
        else:
            value = event_data.get("Value", "")
            if value.startswith("recording-"):
                job_id = value.replace("recording-", "")
                if not job_details and job_id:
                    try:
                        log(
                            f"Fetching job details again for {job_id} in _handle_recording_started",
                            level=LOG_VERBOSE,
                        )
                        job_details = await asyncio.to_thread(
                            self.job_provider.get_job_by_id, job_id
                        )
                    except Exception as e:
                        log(
                            f"_handle_recording_started: Error fetching job details for {job_id}: {e}",
                            level=LOG_VERBOSE,
                        )
                        return False

        if not job_details or not job_id:
            log(
                f"_handle_recording_started: Missing job details for event {event_data.get('Value', '')}",
                level=LOG_VERBOSE,
            )
            return False

        job = job_details

        async with self._event_lock:
            was_scheduled = job_id in self.scheduled_recordings
            if was_scheduled:
                del self.scheduled_recordings[job_id]
            self.active_recordings[job_id] = job

        recording_title = job.get("name", "Unknown")
        item = job.get("item", {})
        channel_info = {}

        channels = job.get("channels", [])
        channel_number = None
        if channels and len(channels) > 0:
            channel_number = channels[0]
            channel_data = await asyncio.to_thread(
                self.channel_provider.get_channel_info, channel_number
            )
            if channel_data:
                channel_info = {
                    "number": channel_number,
                    "name": channel_data.get("name", ""),
                    "logo_url": channel_data.get("logo_url", ""),
                }
            else:
                channel_info = {
                    "number": channel_number,
                    "name": f"Channel {channel_number}",
                }

        recording_start_time = time.time()

        program_start_time = job.get("start_time", 0)
        expected_duration = job.get("duration", 0)

        stream_count = 0
        if self.stream_count_enabled and channel_number is not None:
            channel_name = channel_info.get("name", f"Channel {channel_number}")
            device_name = f"DVR_Recording_{job_id}"
            activity_str = (
                f"Recording ch{channel_number} {channel_name} from {device_name}"
            )

            await self.stream_tracker.process_activity(activity_str, job_id)
            stream_count = await self.stream_tracker.get_stream_count()

        notification_key = f"recording-started-{job_id}"

        recording_type = "(Scheduled)" if was_scheduled else "(Manual)"
        message_parts: Dict[str, Union[str, Dict[str, str]]] = {
            "status": f"{self.STATUS_EMOJI['started']} Recording {recording_type}",
            "details": f"Program: {recording_title}",
        }

        table_parts = []
        table_parts.append("-----------------------")
        table_parts.append(
            f"Recording: {self._format_time_only(int(recording_start_time))}"
        )
        if (
            program_start_time > 0
            and abs(program_start_time - recording_start_time) > 60
        ):
            table_parts.append(
                f"Program:   {self._format_time_only(int(program_start_time))}"
            )
        if expected_duration > 0:
            table_parts.append(f"Duration:  {self._format_duration(expected_duration)}")
        if self.stream_count_enabled:
            table_parts.append(f"Total Streams: {stream_count}")
        message_parts["time_table"] = "\n".join(table_parts)

        if channel_info:
            message_parts["channel"] = {
                "number": str(channel_info.get("number", "")),
                "name": str(channel_info.get("name", "")),
            }

        if item and item.get("summary"):
            message_parts["custom"] = str(item.get("summary", ""))

        message = self.alert_formatter.format_message(
            message_parts,
            order=["channel", "status", "details", "custom", "time_table"],
        )
        image_url = self._resolve_recording_image_url(
            item=item, channel_info=channel_info
        )
        formatted_alert = self._format_recording_alert(
            default_message=message,
            image_url=image_url,
            context=self._build_recording_template_context(
                recording_status="started",
                recording_status_friendly="Recording Started",
                title=recording_title,
                default_message=message,
                channel_info=channel_info,
                item=item,
                start_time=self._format_time_only(int(recording_start_time)),
                end_time=self._format_time_only(
                    int(program_start_time + expected_duration)
                )
                if program_start_time and expected_duration
                else "",
                duration=self._format_duration(expected_duration)
                if expected_duration > 0
                else "",
                image_url=image_url or "",
                summary=str(item.get("summary", ""))
                if item and item.get("summary")
                else "",
                extra_context={
                    "time_table": message_parts.get("time_table", ""),
                    "status": message_parts.get("status", ""),
                    "details": message_parts.get("details", ""),
                },
            ),
        )

        channel_name = channel_info.get(
            "name", f"Channel {channel_info.get('number', 'Unknown')}"
        )
        duration_str = (
            self._format_duration(expected_duration) if expected_duration > 0 else ""
        )
        log(
            f"Recording started {recording_type}: {recording_title} on {channel_name}, Duration: {duration_str}",
            level=LOG_STANDARD,
        )
        if self.stream_count_enabled:
            log(f"Total Streams: {stream_count}", level=LOG_STANDARD)

        await asyncio.to_thread(
            record_recording_event,
            event_type=f"Recording {recording_type}",
            program_name=recording_title,
            channel_name=channel_name,
            image_url=item.get("image_url", ""),
            extra={"duration": duration_str, "recording_type": recording_type}
            if duration_str
            else {"recording_type": recording_type},
            notification_history=self._notification_history,
        )

        alert_sent = False
        if self.recording_started_enabled and getattr(
            self.settings, "alert_recording_events", True
        ):
            if await self.alert_formatter.should_send_notification(
                self.session_manager, notification_key, self.alert_cooldown
            ):
                alert_sent = await self.send_alert_async(
                    formatted_alert["title"],
                    formatted_alert["message"],
                    formatted_alert.get("image_url"),
                )

        if alert_sent:
            await self.session_manager.record_notification(notification_key)

        return alert_sent

    async def _handle_recording_completed(
        self, event_data: Dict[str, Any], recording_details: Optional[Dict[str, Any]]
    ) -> bool:
        """Handles the recording completed event. Job details might be None."""
        value = event_data.get("Value", "")
        file_id = (
            value.replace("recorded-", "") if value.startswith("recorded-") else None
        )

        if not file_id:
            log(
                f"_handle_recording_completed: Could not extract file ID from event {event_data}",
                level=LOG_STANDARD,
            )
            return False

        process_stream_only = False

        async with self._event_lock:
            if file_id in self.pending_recordings:
                return False

        recording = recording_details
        if recording is None:
            try:
                recording = await asyncio.to_thread(
                    self.job_provider.get_recording_by_id, file_id
                )
            except Exception as e:
                log(
                    f"Exception calling get_recording_by_id for {file_id}: {e}",
                    level=LOG_STANDARD,
                )
                async with self._event_lock:
                    self.pending_recordings[file_id] = {
                        "first_seen": time.time(),
                        "event_data": event_data,
                        "check_count": 0,
                        "last_check": time.time(),
                    }
                return False

        current_count = 0
        job_id_from_recording = recording.get("job_id") if recording else None
        if job_id_from_recording and self.stream_count_enabled:
            await self.stream_tracker.process_activity({}, job_id_from_recording)
            current_count = await self.stream_tracker.get_stream_count()
            if process_stream_only:
                log(
                    f"Recording completed {file_id} - stream tracking only",
                    level=LOG_VERBOSE,
                )
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
            async with self._event_lock:
                if file_id not in self.pending_recordings:
                    self.pending_recordings[file_id] = {
                        "first_seen": time.time(),
                        "event_data": event_data,
                        "check_count": 0,
                        "last_check": time.time(),
                    }
            return False

        return await self._process_completed_recording(
            file_id, recording, event_data, current_count
        )

    async def _handle_recording_deleted(
        self, event_data: Dict[str, Any], job_details: Optional[Dict[str, Any]]
    ) -> bool:
        """Handles 'jobs.deleted'. Uses pre-fetched job_details if available, or checks caches."""
        job_id = event_data.get("ID") or event_data.get("Name")

        if not job_id:
            log(
                f"_handle_recording_deleted: Missing Job ID in event data {event_data}",
                level=LOG_VERBOSE,
            )
            return False

        job = None
        source = "Unknown"
        async with self._event_lock:
            scheduled_info = self.scheduled_recordings.pop(job_id, None)
        if scheduled_info:
            job = scheduled_info.get("job")
            source = "Scheduled Cache"
            if not job:
                log(
                    f"Found scheduled info for {job_id} but no job data?",
                    level=LOG_VERBOSE,
                )
                return False

            log(
                f"Processing deletion for job {job_id} (Found via: {source})",
                level=LOG_VERBOSE,
            )

            notification_key = f"recording-cancelled-{job_id}"

            channel_info = {}
            channels = job.get("channels", [])
            if channels:
                channel_number = channels[0]
                channel_data = await asyncio.to_thread(
                    self.channel_provider.get_channel_info, channel_number
                )
                if channel_data:
                    channel_info = {
                        "number": channel_number,
                        "name": channel_data.get("name", ""),
                        "logo_url": channel_data.get("logo_url", ""),
                    }
                else:
                    channel_info = {
                        "number": channel_number,
                        "name": f"Channel {channel_number}",
                    }

            recording_title = job.get("name", "Unknown")
            item = job.get("item", {})
            start_time_ts = job.get("start_time")
            start_time_str = (
                self._format_datetime_friendly(start_time_ts)
                if start_time_ts
                else "Unknown Time"
            )
            expected_duration = job.get("duration", 0)
            duration_str = (
                self._format_duration(expected_duration)
                if expected_duration > 0
                else ""
            )
            channel_label = channel_info.get(
                "name", f"Channel {channel_info.get('number', 'Unknown')}"
            )

            log(
                f"Recording cancelled: {recording_title} on {channel_label}, Was scheduled for: {start_time_str}",
                level=LOG_STANDARD,
            )

            await asyncio.to_thread(
                record_recording_event,
                event_type="Cancelled",
                program_name=recording_title,
                channel_name=channel_label,
                scheduled_datetime=datetime.fromtimestamp(start_time_ts, self.tz)
                if start_time_ts
                else None,
                image_url=item.get("image_url", ""),
                extra={"duration": duration_str, "recording_type": "Cancelled"}
                if duration_str
                else {"recording_type": "Cancelled"},
                notification_history=self._notification_history,
            )

            if self.recording_cancelled_enabled and getattr(
                self.settings, "alert_recording_events", True
            ):
                should_send = await self.alert_formatter.should_send_notification(
                    self.session_manager, notification_key, self.alert_cooldown
                )
                if should_send:
                    message_parts = {
                        "status": f"{self.STATUS_EMOJI['cancelled']} Cancelled",
                        "details": f"Program: {recording_title}"
                        if self.settings.rd_program_name and recording_title
                        else None,
                        "time_table": (
                            f"-----------------------\nScheduled: {start_time_str}\n"
                            + (
                                f"Duration:  {duration_str}\n"
                                if self.settings.rd_duration and duration_str
                                else ""
                            )
                        ).strip()
                        or None,
                        "channel": {
                            "number": str(channel_info.get("number", "")),
                            "name": str(channel_info.get("name", "")),
                        }
                        if channel_info
                        else None,
                        "custom": str(item.get("summary", ""))
                        if self.settings.rd_program_desc
                        and item
                        and item.get("summary")
                        else None,
                    }
                    message_parts = {k: v for k, v in message_parts.items() if v}
                    message = self.alert_formatter.format_message(
                        message_parts,
                        order=["channel", "status", "details", "custom", "time_table"],
                    )
                    image_url = self._resolve_recording_image_url(
                        item=item, channel_info=channel_info
                    )
                    formatted_alert = self._format_recording_alert(
                        default_message=message,
                        image_url=image_url,
                        context=self._build_recording_template_context(
                            recording_status="cancelled",
                            recording_status_friendly="Cancelled",
                            title=recording_title,
                            default_message=message,
                            channel_info=channel_info,
                            item=item,
                            start_time=start_time_str,
                            duration=duration_str,
                            image_url=image_url or "",
                            summary=str(item.get("summary", ""))
                            if item and item.get("summary")
                            else "",
                            extra_context={
                                "time_table": message_parts.get("time_table", ""),
                                "status": message_parts.get("status", ""),
                                "details": message_parts.get("details", ""),
                            },
                        ),
                    )
                    try:
                        await self.send_alert_async(
                            formatted_alert["title"],
                            formatted_alert["message"],
                            formatted_alert.get("image_url"),
                        )
                    except Exception as send_err:
                        log(
                            f"ERROR_SEND: Exception during self.send_alert for {job_id}: {send_err}",
                            level=LOG_VERBOSE,
                        )
                    await self.session_manager.record_notification(notification_key)

            return True

        elif job_details:
            job = job_details
            async with self._event_lock:
                if job_id in self.active_recordings:
                    del self.active_recordings[job_id]
            source = "Pre-fetched"
            log(
                f"Deletion detected for active/pre-fetched job {job_id} (Source: {source}). Alert will be handled by completion/stopped status.",
                level=LOG_VERBOSE,
            )
            if self.stream_count_enabled:
                await self.stream_tracker.process_activity({}, job_id)
            return True

        else:
            async with self._event_lock:
                job = self.active_recordings.pop(job_id, None)
        if job:
            source = "Active Cache"
            log(
                f"Deletion detected for active job {job_id} (Source: {source}). Alert will be handled by completion/stopped status.",
                level=LOG_VERBOSE,
            )
            if self.stream_count_enabled:
                await self.stream_tracker.process_activity({}, job_id)
            return True

        if not job:
            log(
                f"Could not find details for deleted job {job_id} (Source: {source}) - cannot process cancellation.",
                level=LOG_VERBOSE,
            )
            return False

        log(
            f"Processing deletion for active/pre-fetched job {job_id} (Found via: {source})",
            level=LOG_VERBOSE,
        )
        notification_key = f"recording-cancelled-{job_id}"

        channel_info = {}
        channels = job.get("channels", [])
        if channels:
            channel_number = channels[0]
            channel_data = await asyncio.to_thread(
                self.channel_provider.get_channel_info, channel_number
            )
            if channel_data:
                channel_info = {
                    "number": channel_number,
                    "name": channel_data.get("name", ""),
                    "logo_url": channel_data.get("logo_url", ""),
                }
            else:
                channel_info = {
                    "number": channel_number,
                    "name": f"Channel {channel_number}",
                }

        recording_title = job.get("name", "Unknown")
        item = job.get("item", {})
        start_time_ts = job.get("start_time")
        start_time_str = (
            self._format_datetime_friendly(start_time_ts)
            if start_time_ts
            else "Unknown Time"
        )
        channel_label = channel_info.get(
            "name", f"Channel {channel_info.get('number', 'Unknown')}"
        )

        log(
            f"Recording cancelled (Active): {recording_title} on {channel_label}, Originally scheduled for: {start_time_str}",
            level=LOG_STANDARD,
        )

        await asyncio.to_thread(
            record_recording_event,
            event_type="Cancelled (Active)",
            program_name=recording_title,
            channel_name=channel_label,
            scheduled_datetime=datetime.fromtimestamp(start_time_ts, self.tz)
            if start_time_ts
            else None,
            image_url=item.get("image_url", ""),
            extra={"recording_type": "Cancelled (Active)"},
            notification_history=self._notification_history,
        )

        if self.recording_cancelled_enabled and getattr(
            self.settings, "alert_recording_events", True
        ):
            should_send = await self.alert_formatter.should_send_notification(
                self.session_manager, notification_key, self.alert_cooldown
            )
            if should_send:
                message_parts = {
                    "status": f"{self.STATUS_EMOJI['cancelled']} Cancelled (Active)",
                    "details": f"Program: {recording_title}"
                    if self.settings.rd_program_name and recording_title
                    else None,
                    "time_table": (
                        f"-----------------------\nOriginal Start: {start_time_str}\n"
                        + (
                            f"Total Streams: {await self.stream_tracker.get_stream_count()}\n"
                            if self.stream_count_enabled
                            else ""
                        )
                    ).strip()
                    or None,
                    "channel": {
                        "number": str(channel_info.get("number", "")),
                        "name": str(channel_info.get("name", "")),
                    }
                    if channel_info
                    else None,
                    "custom": str(item.get("summary", ""))
                    if self.settings.rd_program_desc and item and item.get("summary")
                    else None,
                }
                message_parts = {k: v for k, v in message_parts.items() if v}
                message = self.alert_formatter.format_message(
                    message_parts,
                    order=["channel", "status", "details", "custom", "time_table"],
                )
                image_url = self._resolve_recording_image_url(
                    item=item, channel_info=channel_info
                )
                formatted_alert = self._format_recording_alert(
                    default_message=message,
                    image_url=image_url,
                    context=self._build_recording_template_context(
                        recording_status="cancelled",
                        recording_status_friendly="Cancelled",
                        title=recording_title,
                        default_message=message,
                        channel_info=channel_info,
                        item=item,
                        start_time=start_time_str,
                        image_url=image_url or "",
                        summary=str(item.get("summary", ""))
                        if item and item.get("summary")
                        else "",
                        extra_context={
                            "time_table": message_parts.get("time_table", ""),
                            "status": message_parts.get("status", ""),
                            "details": message_parts.get("details", ""),
                        },
                    ),
                )
                try:
                    await self.send_alert_async(
                        formatted_alert["title"],
                        formatted_alert["message"],
                        formatted_alert.get("image_url"),
                    )
                except Exception as send_err:
                    log(
                        f"ERROR_SEND: Exception during self.send_alert for ACTIVE {job_id}: {send_err}",
                        level=LOG_VERBOSE,
                    )
                await self.session_manager.record_notification(notification_key)

        if self.stream_count_enabled:
            await self.stream_tracker.process_activity({}, job_id)

        return True

    async def _process_completed_recording(
        self,
        file_id: str,
        recording: Dict[str, Any],
        event_data: Dict[str, Any],
        stream_count: int = 0,
    ) -> bool:
        """Process a completed recording and send notification."""
        recording_id = recording.get("id", "")
        if recording_id and recording_id != file_id:
            log(
                f"WARNING: Processing recording with mismatched IDs. Requested: {file_id}, Got: {recording_id}",
                level=LOG_VERBOSE,
            )

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

        if not await self.alert_formatter.should_send_notification(
            self.session_manager, notification_key, self.alert_cooldown
        ):
            log(
                f"Cooldown active for {notification_key}. Skipping alert, but marking as processed.",
                level=LOG_VERBOSE,
            )
            job_id = recording.get("job_id")
            if job_id:
                async with self._event_lock:
                    self.active_recordings.pop(job_id, None)
            return True

        recording_title = recording.get("title", "Unknown")
        if recording.get("episode_title"):
            recording_title += f" - {recording.get('episode_title')}"

        status_messages = {
            "stopped": "Stopped",
            "cancelled": "Cancelled",
            "completed": "Completed",
        }

        status_suffix = ""
        if status_type == "completed":
            if is_delayed:
                status_suffix = " (Delayed)"
            elif is_interrupted:
                status_suffix = " (Interrupted)"

        message_parts: Dict[str, Union[str, Dict[str, str]]] = {
            "status": f"{self.STATUS_EMOJI[status_type]} {status_messages[status_type]}{status_suffix}",
            "details": f"Program: {recording_title}",
        }

        channel_number = recording.get("channel")
        channel_info = {}

        if channel_number:
            channel_data = None
            try:
                channel_data = await asyncio.to_thread(
                    self.channel_provider.get_channel_info, channel_number
                )
            except Exception as e:
                log(
                    f"Exception getting channel info for {channel_number}: {e}",
                    level=LOG_STANDARD,
                )

            if channel_data:
                channel_info = {
                    "number": channel_number,
                    "name": channel_data.get("name", ""),
                    "logo_url": channel_data.get("logo_url", ""),
                }
            else:
                channel_info = {
                    "number": channel_number,
                    "name": f"Channel {channel_number}",
                }

        actual_duration = recording.get("duration", 0)

        duration_str = self._format_duration(actual_duration)

        table_parts = []
        table_parts.append("-----------------------")
        table_parts.append(f"Duration:  {duration_str}")

        if self.stream_count_enabled:
            current_count = (
                stream_count
                if isinstance(stream_count, int) and stream_count > 0
                else await self.stream_tracker.get_stream_count()
            )
            table_parts.append(f"Total Streams: {current_count}")

        message_parts["time_table"] = "\n".join(table_parts)

        if channel_info:
            message_parts["channel"] = {
                "number": str(channel_info.get("number", "")),
                "name": str(channel_info.get("name", "")),
            }

        if recording.get("summary"):
            message_parts["custom"] = str(recording.get("summary", ""))

        start_time_ts = recording.get("job", {}).get("start_time")
        start_time_str = (
            self._format_datetime_friendly(start_time_ts)
            if start_time_ts
            else "Unknown Start Time"
        )
        status_name = status_messages[status_type] + status_suffix

        message = self.alert_formatter.format_message(
            message_parts,
            order=["channel", "status", "details", "custom", "time_table"],
        )
        image_url = self._resolve_recording_image_url(
            recording=recording, channel_info=channel_info
        )
        formatted_alert = self._format_recording_alert(
            default_message=message,
            image_url=image_url,
            context=self._build_recording_template_context(
                recording_status=status_type,
                recording_status_friendly=status_name,
                title=recording_title,
                default_message=message,
                channel_info=channel_info,
                item=recording,
                start_time=start_time_str,
                duration=duration_str,
                image_url=image_url or "",
                summary=str(recording.get("summary", ""))
                if recording.get("summary")
                else "",
                extra_context={
                    "time_table": message_parts.get("time_table", ""),
                    "status": message_parts.get("status", ""),
                    "details": message_parts.get("details", ""),
                },
            ),
        )

        channel_name = channel_info.get(
            "name", f"Channel {channel_info.get('number', 'Unknown')}"
        )
        duration_display = f"Duration: {duration_str}" if actual_duration > 0 else ""
        log(
            f"Recording {status_name.lower()}: {recording_title} on {channel_name} {duration_display}",
            level=LOG_STANDARD,
        )

        await asyncio.to_thread(
            record_recording_event,
            event_type=status_name,
            program_name=recording_title,
            channel_name=channel_name,
            image_url=image_url,
            extra={"duration": duration_str, "recording_type": status_name}
            if duration_str
            else {"recording_type": status_name},
            notification_history=self._notification_history,
        )

        if self.stream_count_enabled:
            current_count = (
                stream_count
                if isinstance(stream_count, int) and stream_count > 0
                else await self.stream_tracker.get_stream_count()
            )
            log(f"Total Streams: {current_count}", level=LOG_STANDARD)

        if self.recording_completed_enabled and getattr(
            self.settings, "alert_recording_events", True
        ):
            try:
                await self.send_alert_async(
                    formatted_alert["title"],
                    formatted_alert["message"],
                    formatted_alert.get("image_url"),
                )
            except Exception as send_err:
                log(
                    f"ERROR_SEND: Exception during self.send_alert for {file_id}: {send_err}",
                    level=LOG_STANDARD,
                )
            await self.session_manager.record_notification(notification_key)

        job_id = recording.get("job_id")
        if job_id:
            async with self._event_lock:
                self.active_recordings.pop(job_id, None)

        return True

    async def run_cleanup(self) -> None:
        """Executes cleanup operations for stale recording data and sessions."""
        cleanup_start = time.time()
        log("Starting RecordingEventsAlert cleanup process", level=LOG_VERBOSE)

        try:
            try:
                await self.session_manager.cleanup()
                log("Session manager cleanup completed", level=LOG_VERBOSE)
            except Exception as session_err:
                log(
                    f"Error during session manager cleanup: {session_err}",
                    level=LOG_VERBOSE,
                )
                log(traceback.format_exc(), level=LOG_VERBOSE)

            stale_jobs = []
            async with self._event_lock:
                active_job_ids = list(self.active_recordings.keys())
            active_jobs_checked = 0
            MAX_ACTIVE_CHECKS_PER_CYCLE = 50

            log(
                f"Checking {len(active_job_ids)} active recordings (max {MAX_ACTIVE_CHECKS_PER_CYCLE} per cycle)",
                level=LOG_VERBOSE,
            )

            for job_id in active_job_ids:
                if active_jobs_checked >= MAX_ACTIVE_CHECKS_PER_CYCLE:
                    log(
                        f"Reached limit of {MAX_ACTIVE_CHECKS_PER_CYCLE} active job checks, will continue in next cycle",
                        level=LOG_VERBOSE,
                    )
                    break
                active_jobs_checked += 1

                is_active = False
                try:
                    start_time = time.time()
                    is_active = await asyncio.to_thread(
                        self.job_provider.is_job_active, job_id
                    )
                    request_time = time.time() - start_time

                    if request_time > 2.0:
                        log(
                            f"Slow job status check for {job_id}: {request_time:.2f}s",
                            level=LOG_VERBOSE,
                        )

                except Exception as e:
                    log(
                        f"Error checking active status for job {job_id}: {e}",
                        level=LOG_VERBOSE,
                    )
                    log(traceback.format_exc(), level=LOG_VERBOSE)
                    continue

                if not is_active:
                    log(
                        f"Job {job_id} is no longer active, marking for removal",
                        level=LOG_VERBOSE,
                    )
                    stale_jobs.append(job_id)

            if stale_jobs:
                async with self._event_lock:
                    for job_id in stale_jobs:
                        if job_id in self.active_recordings:
                            try:
                                del self.active_recordings[job_id]
                                log(
                                    f"Removed stale job {job_id} from active recordings",
                                    level=LOG_VERBOSE,
                                )
                            except KeyError:
                                pass
                log(
                    f"Removed {len(stale_jobs)} stale jobs from active recordings",
                    level=LOG_VERBOSE,
                )

            stale_scheduled = []
            async with self._event_lock:
                scheduled_snapshot = dict(self.scheduled_recordings)
            scheduled_job_ids = list(scheduled_snapshot.keys())
            scheduled_jobs_checked = 0
            MAX_SCHEDULED_CHECKS_PER_CYCLE = 50
            current_time = time.time()

            log(
                f"Checking {len(scheduled_job_ids)} scheduled recordings (max {MAX_SCHEDULED_CHECKS_PER_CYCLE} per cycle)",
                level=LOG_VERBOSE,
            )

            for job_id in scheduled_job_ids:
                if scheduled_jobs_checked >= MAX_SCHEDULED_CHECKS_PER_CYCLE:
                    log(
                        f"Reached limit of {MAX_SCHEDULED_CHECKS_PER_CYCLE} scheduled job checks, will continue in next cycle",
                        level=LOG_VERBOSE,
                    )
                    break
                scheduled_jobs_checked += 1

                info = scheduled_snapshot[job_id]
                is_active = True
                try:
                    start_time = time.time()
                    is_active = await asyncio.to_thread(
                        self.job_provider.is_job_active, job_id
                    )
                    request_time = time.time() - start_time

                    if request_time > 2.0:
                        log(
                            f"Slow scheduled job status check for {job_id}: {request_time:.2f}s",
                            level=LOG_VERBOSE,
                        )

                except Exception as e:
                    log(
                        f"Error checking active status for scheduled job {job_id}: {e}",
                        level=LOG_VERBOSE,
                    )
                    log(traceback.format_exc(), level=LOG_VERBOSE)
                    continue

                if not is_active:
                    log(
                        f"Scheduled job {job_id} is no longer active, marking for removal",
                        level=LOG_VERBOSE,
                    )
                    stale_scheduled.append(job_id)
                    continue

                if current_time - info.get("created_at", 0) > 86400:
                    log(
                        f"Scheduled job {job_id} was created over 24 hours ago, marking as stale",
                        level=LOG_VERBOSE,
                    )
                    stale_scheduled.append(job_id)

            if stale_scheduled:
                async with self._event_lock:
                    for job_id in stale_scheduled:
                        if job_id in self.scheduled_recordings:
                            try:
                                del self.scheduled_recordings[job_id]
                                log(
                                    f"Removed stale job {job_id} from scheduled recordings",
                                    level=LOG_VERBOSE,
                                )
                            except KeyError:
                                pass
                log(
                    f"Removed {len(stale_scheduled)} stale jobs from scheduled recordings",
                    level=LOG_VERBOSE,
                )

            pending_to_remove = []
            async with self._event_lock:
                pending_snapshot = dict(self.pending_recordings)
            pending_count = len(pending_snapshot)
            if pending_count > 0:
                log(f"Checking {pending_count} pending recordings", level=LOG_VERBOSE)
                for file_id, info in pending_snapshot.items():
                    retry_count = info.get("retry_count", 0)
                    first_seen = info.get("first_seen", 0)

                    if (
                        retry_count >= self.max_retries
                        or (current_time - first_seen) > 21600
                    ):
                        pending_to_remove.append(file_id)

                async with self._event_lock:
                    for file_id in pending_to_remove:
                        try:
                            del self.pending_recordings[file_id]
                        except KeyError:
                            pass

                if pending_to_remove:
                    log(
                        f"Removed {len(pending_to_remove)} stale pending recordings",
                        level=LOG_VERBOSE,
                    )

            total_removed = (
                len(stale_jobs) + len(stale_scheduled) + len(pending_to_remove)
            )
            if total_removed > 0:
                log(
                    f"Removed {total_removed} total stale items during cleanup",
                    level=LOG_VERBOSE,
                )

            if self.stream_count_enabled:
                try:
                    await self.stream_tracker.cleanup_stale_sessions()
                    log("Stream tracker cleanup completed", level=LOG_VERBOSE)
                except Exception as stream_err:
                    log(
                        f"Error during stream tracker cleanup: {stream_err}",
                        level=LOG_VERBOSE,
                    )
                    log(traceback.format_exc(), level=LOG_VERBOSE)

            try:
                job_count = await asyncio.to_thread(self.job_provider.cache_jobs)
                log(
                    f"Refreshed job cache during cleanup, found {job_count} jobs",
                    level=LOG_VERBOSE,
                )
            except Exception as cache_err:
                log(
                    f"Error refreshing job cache during cleanup: {cache_err}",
                    level=LOG_VERBOSE,
                )
                log(traceback.format_exc(), level=LOG_VERBOSE)

            if (
                self._lock_health["acquisition_count"]
                > self._lock_health["release_count"]
            ):
                log(
                    f"Lock health during cleanup: {self._lock_health['acquisition_count']} acquisitions, "
                    + f"{self._lock_health['release_count']} releases. Lock may be stuck.",
                    level=LOG_VERBOSE,
                )

            cleanup_time = time.time() - cleanup_start
            log(
                f"RecordingEventsAlert cleanup completed in {cleanup_time:.2f}s",
                level=LOG_VERBOSE,
            )

        except Exception as e:
            cleanup_time = time.time() - cleanup_start
            log(
                f"Critical error in recording events cleanup after {cleanup_time:.2f}s: {e}",
                level=LOG_VERBOSE,
            )
            log(traceback.format_exc(), level=LOG_VERBOSE)

    async def cleanup(self) -> None:
        """Executes the main cleanup routine for the recording events alert system."""
        await self.run_cleanup()

    def set_startup_complete(self):
        """Marks the startup process as complete to enable alert notifications."""
        pass
