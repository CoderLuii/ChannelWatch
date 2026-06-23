"""Channel-Watching alert implementation for monitoring live TV viewing activity."""

import asyncio
import time
from typing import Dict, Any

from .base import BaseAlert
from .common.session_manager import SessionManager
from .common.alert_formatter import AlertFormatter
from .common.cleanup_mixin import CleanupMixin
from .common.stream_tracker import StreamTracker
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.parsing import (
    extract_channel_number,
    extract_channel_name,
    extract_device_name,
    extract_ip_address,
    extract_resolution,
    extract_source_from_session_id,
    is_valid_ip_address,
)
from ..helpers.channel_info import ChannelInfoProvider
from ..helpers.program_info import ProgramInfoProvider
from ..helpers.type_utils import ensure_str, ensure_dict
from ..helpers.activity_recorder import record_activity


# CHANNEL WATCHING
class ChannelWatchingAlert(BaseAlert, CleanupMixin):
    """Monitors and alerts on live TV channel viewing activity."""

    ALERT_TYPE = "Channel-Watching"
    ROUTING_EVENT_TYPE = "channel"
    DESCRIPTION = "Notifications when someone is watching TV"

    def __init__(self, alert_manager):
        """Initializes the Channel-Watching alert from an AlertManager instance."""
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

        show_channel_name = settings.cw_channel_name
        show_channel_number = settings.cw_channel_number
        show_program_name = settings.cw_program_name
        show_device_name = settings.cw_device_name
        show_ip = settings.cw_device_ip
        show_source = settings.cw_stream_source

        self.alert_formatter = AlertFormatter(
            config={
                "show_channel_name": show_channel_name,
                "show_channel_number": show_channel_number,
                "show_program_name": show_program_name,
                "show_device_name": show_device_name,
                "show_ip": show_ip,
                "show_source": show_source,
                "use_emoji": True,
                "title_prefix": "📺 ",
            }
        )

        self.time_module = time
        self.alert_cooldown = settings.cw_alert_cooldown
        self.template_settings = {
            "title": settings.cw_template_title,
            "body": settings.cw_template_body,
            "use_default": settings.cw_template_use_default,
        }

        channel_cache_ttl = settings.channel_cache_ttl
        program_cache_ttl = settings.program_cache_ttl

        self.channel_provider = ChannelInfoProvider(
            ensure_str(host), port, cache_ttl=channel_cache_ttl
        )
        self.stream_tracker = (
            StreamTracker(dvr=dvr) if dvr else StreamTracker(host=host, port=port)
        )
        self.program_provider = ProgramInfoProvider(
            ensure_str(host), port, timezone, cache_ttl=program_cache_ttl
        )

        self.stream_count_enabled = settings.stream_count
        self.program_name_enabled = settings.cw_program_name

        self.image_source = settings.cw_image_source

        self.configure_cleanup(enabled=True, interval=3600, auto_cleanup=True)

    # INITIALIZATION
    def _cache_channels(self):
        """Caches channel information at startup for faster lookups later."""
        self.channel_provider.cache_channels()

    def _cache_program_info(self):
        """Caches program information at startup for faster lookups later."""
        self.program_provider.cache_program_data()

    # EVENT HANDLING
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        return self._is_watching_event(event_type, event_data)

    async def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        try:
            value = event_data.get("Value", "")
            session_id = event_data.get("Name", "")

            channel_number = extract_channel_number(value)
            if not channel_number:
                return False

            device_name = extract_device_name(value)
            ip_address = extract_ip_address(value)

            device_identifier = device_name if device_name else ip_address
            if not device_identifier:
                return False

            tracking_key = f"ch{channel_number}-{device_identifier}"

            stream_changed = False
            current_count = 0
            if self.stream_count_enabled:
                stream_changed = await self.stream_tracker.process_activity(
                    value, session_id
                )
                current_count = await self.stream_tracker.get_stream_count()

            async with self._event_lock:
                if await self.session_manager.is_event_processing(tracking_key):
                    return False

                if not await self.alert_formatter.should_send_notification(
                    self.session_manager, tracking_key, self.alert_cooldown
                ):
                    return False

                await self.session_manager.mark_event_processing(tracking_key)

            try:
                if (
                    stream_changed
                    and self.stream_count_enabled
                    and not await self._is_new_session(session_id, channel_number)
                ):
                    log(f"Total Streams: {current_count}", level=LOG_STANDARD)
                    return False

                return await self._process_watching_event(event_data, tracking_key)
            finally:
                await self.session_manager.complete_event_processing(tracking_key)
        except Exception as e:
            log(f"Error processing event: {e}")
            return False

    def _is_watching_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determines if an event represents channel watching activity."""
        if (
            event_type != "activities.set"
            or "Value" not in event_data
            or not event_data.get("Value")
        ):
            return False

        value = event_data.get("Value", "")

        if "buf=" in value or "fps" in value:
            pass
        elif "Watching ch" in value:
            if "(" not in value or "Watching ch" in value and ")" not in value:
                clean_value = value.split("(")[0].strip() if "(" in value else value
                log(f"Channel activity: {clean_value}", level=LOG_VERBOSE)

        return "Watching ch" in value or (
            "channel" in value.lower() and "watching" in value.lower()
        )

    # PROCESSING
    async def _process_watching_event(
        self, event_data: Dict[str, Any], tracking_key: str
    ) -> bool:
        try:
            session_id = event_data.get("Name", "")
            value = event_data.get("Value", "")

            channel_number = extract_channel_number(value)
            if not channel_number:
                return False

            device_name = extract_device_name(value)
            if not device_name:
                device_name = "Unknown device"

            if await self.session_manager.has_session(session_id):
                session_data = await self.session_manager.get_session(session_id)
                if session_data is None:
                    return False
                old_channel_info = session_data.get("channel_info", {})
                old_channel_number = old_channel_info.get("number")

                if old_channel_number == channel_number:
                    await self.session_manager.add_session(
                        session_id,
                        channel_info=old_channel_info,
                        tracking_key=tracking_key,
                    )
                    return False

            device_sessions = []

            for (
                active_session_id,
                session_data,
            ) in self.session_manager.active_sessions.items():
                if active_session_id != session_id:
                    active_channel_info = session_data.get("channel_info", {})
                    active_device = active_channel_info.get("device", "")

                    if active_device == device_name:
                        device_sessions.append(active_session_id)

            for old_session_id in device_sessions:
                old_session_data = await self.session_manager.get_session(
                    old_session_id
                )
                if old_session_data:
                    old_channel_info = old_session_data.get("channel_info", {})

                    log(
                        f"Exited {old_channel_info.get('name', 'Unknown')} "
                        f"(Ch{old_channel_info.get('number', '')}) - Device: {old_channel_info.get('device', 'N/A')}, "
                        f"IP: {old_channel_info.get('ip', 'N/A')}, Source: {old_channel_info.get('source', 'N/A')}",
                        level=LOG_STANDARD,
                    )

                    await self.session_manager.remove_session(old_session_id)

            channel_info = {}
            channel_info["number"] = channel_number

            channel_name = extract_channel_name(value)
            if channel_name:
                channel_info["name"] = channel_name

            channel_info["device"] = device_name

            ip_from_val = extract_ip_address(value)

            ip_from_name = ""
            if session_id:
                name_parts = session_id.split("-")
                if len(name_parts) > 0:
                    last_part = name_parts[-1]
                    if is_valid_ip_address(last_part):
                        ip_from_name = last_part

            preferred_ip = ip_from_val if ip_from_val else ip_from_name
            channel_info["ip"] = preferred_ip if preferred_ip else "Unknown IP"

            resolution = extract_resolution(value)
            channel_info["resolution"] = (
                resolution if resolution else "Unknown resolution"
            )

            source = extract_source_from_session_id(session_id)
            channel_info["source"] = source if source else "Unknown source"

            if not channel_info.get("name") or not channel_info.get("logo_url"):
                channel_number_str = str(channel_number)
                provider_info = await asyncio.to_thread(
                    self.channel_provider.get_channel_info, channel_number_str
                )

                if provider_info:
                    if not channel_info.get("name") and provider_info.get("name"):
                        channel_info["name"] = provider_info["name"]
                    if provider_info.get("logo_url"):
                        channel_info["logo_url"] = provider_info["logo_url"]
                else:
                    if not channel_info.get("name"):
                        channel_info["name"] = "Unknown Channel"

            if self.stream_count_enabled:
                channel_info[
                    "stream_count"
                ] = await self.stream_tracker.get_stream_count()

            await self.session_manager.add_session(
                session_id,
                channel_info=channel_info,
                tracking_key=tracking_key,
            )
            await self.session_manager.record_notification(tracking_key)

            await asyncio.to_thread(self._send_alert, channel_info)
            return True
        except Exception as e:
            log(f"Error processing watching event: {e}")
            return False

    # NOTIFICATIONS
    def _send_alert(self, channel_info: Dict[str, Any]) -> bool:
        """Sends an alert with the given channel information."""
        try:
            channel_number = channel_info.get("number", "")
            channel_name = channel_info.get("name", "Unknown")

            device_name = channel_info.get("device", "Unknown device")
            ip_address = channel_info.get("ip", "Unknown IP")
            source = channel_info.get("source", "Unknown source")

            stream_count = channel_info.get("stream_count")

            log_message = (
                f"Watching {channel_name} (Ch{channel_number}) - Device: {device_name}"
            )

            if source != "Unknown source":
                log_message += f", Source: {source}"

            if ip_address != "Unknown IP":
                log_message += f", IP: {ip_address}"

            log(log_message, level=LOG_STANDARD)

            program_info = None
            if self.program_name_enabled:
                program_info = self.program_provider.get_current_program(channel_number)

                if program_info:
                    program_title = program_info.get("title", "Unknown Program")
                    log(
                        f"Program info: {channel_name} (Ch{channel_number}) | {program_title}",
                        level=LOG_VERBOSE,
                    )

            if self.stream_count_enabled and stream_count is not None:
                log(f"Total Streams: {stream_count}", level=LOG_STANDARD)

            image_url = ""

            channel_logo_url = channel_info.get("logo_url", "")

            program_image_url = ""
            if program_info and program_info.get("icon_url"):
                program_image_url = program_info["icon_url"]

            if self.image_source.upper() == "CHANNEL":
                if channel_logo_url:
                    image_url = channel_logo_url
                    log(f"Using channel image: {image_url}", level=LOG_VERBOSE)
                elif program_image_url:
                    image_url = program_image_url
                    log(
                        f"Channel logo empty, falling back to program image: {image_url}",
                        level=LOG_VERBOSE,
                    )
                else:
                    log(
                        "No channel logo or program image available, sending without image",
                        level=LOG_VERBOSE,
                    )
            else:
                if program_image_url:
                    image_url = program_image_url
                    log(f"Using program image: {image_url}", level=LOG_VERBOSE)
                elif channel_logo_url:
                    image_url = channel_logo_url
                    log(
                        f"Program image empty, falling back to channel logo: {image_url}",
                        level=LOG_VERBOSE,
                    )
                else:
                    log(
                        "No program image or channel logo available, sending without image",
                        level=LOG_VERBOSE,
                    )

            device_info = {
                "name": device_name,
                "source": source,
                "resolution": channel_info.get("resolution", ""),
            }

            if ip_address != "Unknown IP":
                device_info["ip_address"] = ip_address

            alert_channel_info = {
                "number": channel_number,
                "name": channel_name,
                "logo_url": image_url,
            }

            if program_info and self.program_name_enabled:
                alert_channel_info["program_title"] = program_info["title"]

            if self.stream_count_enabled and stream_count is not None:
                alert_channel_info["stream_count"] = stream_count

            log(
                f"Formatting alert with channel: {channel_name}, device: {device_name}, program: {program_info.get('title') if program_info else 'N/A'}",
                level=LOG_VERBOSE,
            )

            formatted_alert = self.alert_formatter.format_channel_alert(
                channel_info=alert_channel_info,
                device_info=device_info,
                template_settings=self.template_settings,
                dvr=self.dvr,
                extra_context={
                    "program_summary": program_info.get("description", "")
                    if program_info
                    else "",
                    "content_rating": program_info.get("rating", "")
                    if program_info
                    else "",
                    "genres": program_info.get("genres", []) if program_info else [],
                    "cast": program_info.get("cast", []) if program_info else [],
                    "categories": program_info.get("categories", [])
                    if program_info
                    else [],
                    "episode_title": program_info.get("episode_title", "")
                    if program_info
                    else "",
                    "season_number": program_info.get("season_number", "")
                    if program_info
                    else "",
                    "season_number00": str(program_info.get("season_number", "")).zfill(
                        2
                    )
                    if program_info
                    and program_info.get("season_number") not in (None, "")
                    else "",
                    "episode_number": program_info.get("episode_number", "")
                    if program_info
                    else "",
                    "episode_number00": str(
                        program_info.get("episode_number", "")
                    ).zfill(2)
                    if program_info
                    and program_info.get("episode_number") not in (None, "")
                    else "",
                    "program_duration": program_info.get("duration", "")
                    if program_info
                    else "",
                    "program_duration_secs": program_info.get("duration_seconds", "")
                    if program_info
                    else "",
                },
            )

            display_device = (
                device_name if device_name != "Unknown device" else ip_address
            )

            activity_message = f"Watching {channel_name} on {display_device}"

            record_activity(
                activity_type="watching_channel",
                title="Watching Channel",
                message=activity_message,
                channel_name=channel_name,
                channel_number=channel_number,
                device_name=device_name,
                device_ip=ip_address,
                program_title=program_info.get("title") if program_info else None,
                image_url=image_url,
                stream_source=source,
                extra={"stream_count": stream_count}
                if self.stream_count_enabled and stream_count
                else None,
                dvr_id=getattr(self.dvr, "id", None),
                dvr_name=getattr(self.dvr, "name", None),
                notification_history=self._notification_history,
            )

            if getattr(self.settings, "alert_channel_watching", True):
                result = self.send_alert(
                    title=formatted_alert["title"],
                    message=formatted_alert["message"],
                    image_url=formatted_alert.get("image_url"),
                )
            else:
                result = False

            return result
        except Exception as e:
            log(f"Error sending alert: {e}", level=LOG_STANDARD)
            return False

    # SESSIONS
    async def process_end_event(self, session_id: str) -> None:
        try:
            session_data = await self.session_manager.get_session(session_id)

            if session_data:
                channel_info = session_data.get("channel_info", {})

                log_message = f"Exited {channel_info.get('name', 'Unknown')} (Ch{channel_info.get('number', '')}) - Device: {channel_info.get('device', 'N/A')}"

                source = channel_info.get("source", "N/A")
                if source != "N/A" and source != "Unknown source":
                    log_message += f", Source: {source}"

                ip = channel_info.get("ip", "N/A")
                if ip != "N/A" and ip != "Unknown IP":
                    log_message += f", IP: {ip}"

                log(log_message, level=LOG_STANDARD)

                if self.stream_count_enabled:
                    await self.stream_tracker.process_activity({}, session_id)
                    current_count = await self.stream_tracker.get_stream_count()
                    log(
                        f"Stream ended - Total Streams: {current_count}",
                        level=LOG_STANDARD,
                    )

                await self.session_manager.remove_session(session_id)
        except Exception as e:
            log(f"Error processing end event: {e}", level=LOG_STANDARD)

    # CLEANUP
    async def run_cleanup(self) -> None:
        try:
            removed_sessions = self.cleanup_dict_by_time(
                self.session_manager.active_sessions,
                ttl=14400,
                timestamp_key="timestamp",
            )

            removed_events = self.cleanup_dict_by_timestamp(
                self.session_manager.processing_events, ttl=300
            )

            removed_notifications = self.cleanup_dict_by_timestamp(
                self.session_manager.notification_history, ttl=86400
            )

            if self.stream_count_enabled:
                await self.stream_tracker.cleanup_stale_sessions()

            self.log_cleanup_results(
                component="ChannelWatchingAlert",
                removed={
                    "sessions": len(removed_sessions),
                    "events": len(removed_events),
                    "notifications": len(removed_notifications),
                },
            )
        except Exception as e:
            log(f"Error in cleanup: {e}")

    async def cleanup(self) -> None:
        try:
            await self.run_cleanup()
        except Exception as e:
            log(f"Error cleaning up sessions: {e}")

    def __del__(self):
        """Cleans up resources when instance is deleted."""
        try:
            self.stop_cleanup()
        except Exception:
            pass

    # UTILITIES
    async def _is_new_session(self, session_id: str, channel_number: str) -> bool:
        """Determines if this is a new session or an existing one for this channel."""
        if not await self.session_manager.has_session(session_id):
            return True

        session_data = await self.session_manager.get_session(session_id)
        if session_data is None:
            return True

        old_channel_info = ensure_dict(session_data.get("channel_info"))
        old_channel_number = old_channel_info.get("number")

        return old_channel_number != channel_number
