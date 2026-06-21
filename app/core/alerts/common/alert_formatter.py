"""Formats alert messages for different notification types with consistent styling."""

from typing import Dict, Any, List, Optional
from datetime import datetime

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ...helpers.dvr_connection import build_dvr_base_url
from ...notifications.template_engine import (
    NotificationTemplateEngine,
    TemplateRenderError,
)
from ... import __version__

# ALERT FORMATTER


class AlertFormatter:
    """Formats alert messages for different notification types with consistent styling."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initializes the alert formatter with optional configuration settings."""
        self.config = {
            "show_channel_name": True,
            "show_channel_number": True,
            "show_program_name": True,
            "show_device_name": True,
            "show_ip": True,
            "show_source": True,
            "use_emoji": True,
            "title_prefix": "",
            "compact_mode": False,
            "max_line_length": 100,
        }

        if config:
            self.config.update(config)

        self.template_engine = NotificationTemplateEngine()

    # TITLE FORMATTING

    def format_title(self, title: str, emoji: Optional[str] = None) -> str:
        """Formats a notification title with optional emoji prefix."""
        if not self.config["use_emoji"]:
            return title

        prefix = emoji if emoji else self.config["title_prefix"]

        return f"{prefix}{title}"

    # MESSAGE FORMATTING

    def format_message(
        self, message_parts: Dict[str, Any], order: Optional[List[str]] = None
    ) -> str:
        """Formats a notification message from component parts in specified order."""
        if not message_parts:
            return ""

        lines = []

        log(f"Formatting message with parts: {message_parts}", level=LOG_VERBOSE)

        if not order:
            order = [
                "channel",
                "program",
                "resolution",
                "device",
                "ip",
                "source",
                "status",
                "details",
                "time",
                "custom",
            ]

        stream_count = None
        if (
            "channel" in message_parts
            and isinstance(message_parts["channel"], dict)
            and "stream_count" in message_parts["channel"]
        ):
            stream_count = message_parts["channel"]["stream_count"]

        for component in order:
            if component == "channel" and not (
                self.config["show_channel_name"] or self.config["show_channel_number"]
            ):
                continue
            if component == "ip" and not self.config["show_ip"]:
                continue
            if component == "source" and not self.config["show_source"]:
                continue
            if component == "device" and not self.config["show_device_name"]:
                continue

            value = message_parts.get(component)
            if value:
                if component == "channel" and isinstance(value, dict):
                    if value.get("name") and self.config["show_channel_name"]:
                        lines.append(f"📺 {value['name']}")
                    if value.get("number") and self.config["show_channel_number"]:
                        lines.append(f"Channel: {value['number']}")
                    if value.get("program_title") and self.config["show_program_name"]:
                        lines.append(f"Program: {value['program_title']}")
                        log(
                            f"Added program line: Program: {value['program_title']}",
                            level=LOG_VERBOSE,
                        )
                elif component == "resolution":
                    lines.append(f"Resolution: {value}")
                elif component == "device" and self.config["show_device_name"]:
                    lines.append(f"Device: {value}")
                elif component == "source" and self.config["show_source"]:
                    lines.append(f"Source: {value}")
                    if stream_count is not None:
                        lines.append(f"Total Streams: {stream_count}")
                elif component == "ip" and self.config["show_ip"]:
                    lines.append(f"Device IP: {value}")
                elif component == "status":
                    lines.append(f"Status: {value}")
                elif component == "time":
                    lines.append(f"Time: {value}")
                else:
                    lines.append(str(value))

        result = "\n".join(lines)
        log(f"Final formatted message: {result}", level=LOG_VERBOSE)
        return result

    # ALERT TYPE FORMATTING

    def format_channel_alert(
        self,
        channel_info: Dict[str, Any],
        device_info: Dict[str, Any],
        template_settings: Optional[Dict[str, Any]] = None,
        dvr: Optional[Any] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Formats a channel watching alert with standard components."""
        title = "Channels DVR - Watching TV"

        message_parts = {
            "channel": {
                "number": channel_info.get("number", ""),
                "name": channel_info.get("name", ""),
            },
            "device": device_info.get("name", "Unknown device"),
        }

        log(
            f"Format alert with channel_info: {channel_info}, device_info: {device_info}",
            level=LOG_VERBOSE,
        )

        if "program_title" in channel_info:
            log(
                f"Adding program title to message: {channel_info['program_title']}",
                level=LOG_VERBOSE,
            )
            message_parts["channel"]["program_title"] = channel_info["program_title"]

        if "stream_count" in channel_info:
            message_parts["channel"]["stream_count"] = channel_info["stream_count"]

        if device_info.get("ip_address"):
            message_parts["ip"] = device_info["ip_address"]

        if device_info.get("source"):
            message_parts["source"] = device_info["source"]

        if device_info.get("resolution"):
            message_parts["resolution"] = device_info["resolution"]

        message = self.format_message(message_parts)

        image_url = channel_info.get("logo_url") or channel_info.get("image_url")

        context = self.build_context(
            alert_type="channel_watching",
            dvr=dvr,
            extra_context={
                "channel_number": channel_info.get("number", ""),
                "channel_name": channel_info.get("name", ""),
                "program_title": channel_info.get("program_title", ""),
                "client_name": device_info.get("name", ""),
                "device_name": device_info.get("name", ""),
                "client_ip": device_info.get("ip_address", ""),
                "stream_source": device_info.get("source", ""),
                "resolution": device_info.get("resolution", ""),
                "stream_count": channel_info.get("stream_count", ""),
                "channel_logo": image_url,
                "program_image": channel_info.get("image_url", ""),
                "image_url": image_url,
                "is_live": "Yes",
                **(extra_context or {}),
            },
        )

        return self.format_templated_alert(
            alert_type="channel_watching",
            default_title=title,
            default_message=message,
            image_url=image_url,
            template_settings=template_settings,
            context=context,
        )

    def format_generic_alert(
        self, title: str, details: Dict[str, Any], emoji: Optional[str] = None
    ) -> Dict[str, Any]:
        """Formats a generic alert with flexible components."""
        formatted_title = self.format_title(title, emoji)

        message = self.format_message(details)

        image_url = details.get("image_url")

        return {"title": formatted_title, "message": message, "image_url": image_url}

    def format_templated_alert(
        self,
        alert_type: str,
        default_title: str,
        default_message: str,
        context: Dict[str, Any],
        template_settings: Optional[Dict[str, Any]] = None,
        image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        settings = template_settings or {}
        if settings.get("use_default", True):
            return {
                "title": default_title,
                "message": default_message,
                "image_url": image_url,
            }

        title_template = settings.get("title", "")
        body_template = settings.get("body", "")

        if not isinstance(title_template, str) or not isinstance(body_template, str):
            log(
                f"Invalid template config for {alert_type}; falling back to defaults",
                level=LOG_STANDARD,
            )
            return {
                "title": default_title,
                "message": default_message,
                "image_url": image_url,
            }

        try:
            rendered_title = self.template_engine.render(
                title_template, context
            ).strip()
            rendered_message = self.template_engine.render(
                body_template, context
            ).strip()
            if not rendered_title or not rendered_message:
                raise TemplateRenderError("Rendered template was blank")
            return {
                "title": rendered_title,
                "message": rendered_message,
                "image_url": image_url,
            }
        except TemplateRenderError as exc:
            log(
                f"Template render failed for {alert_type}: {exc}. Falling back to defaults.",
                level=LOG_STANDARD,
            )
            return {
                "title": default_title,
                "message": default_message,
                "image_url": image_url,
            }

    def build_context(
        self,
        alert_type: str,
        dvr: Optional[Any] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now()
        host = getattr(dvr, "host", "") if dvr else ""
        port = getattr(dvr, "port", "") if dvr else ""
        server_name = getattr(dvr, "name", "") if dvr else ""
        server_url = build_dvr_base_url(host, port) if host and port else ""

        context = {
            "server_name": server_name,
            "server_url": server_url,
            "server_version": getattr(dvr, "version", "") if dvr else "",
            "channelwatch_version": __version__,
            "alert_type": alert_type,
            "alert_type_friendly": alert_type.replace("_", " ").title(),
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "datestamp": now.strftime("%m/%d/%Y"),
            "current_year": now.strftime("%Y"),
            "current_month": now.strftime("%m"),
            "current_day": now.strftime("%d"),
            "current_time": now.strftime("%H:%M:%S"),
            "unixtime": str(int(now.timestamp())),
        }

        if extra_context:
            for key, value in extra_context.items():
                context[key] = self._normalize_context_value(value)

        return context

    def _normalize_context_value(self, value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (list, tuple)):
            return [
                self._normalize_context_value(item)
                for item in value
                if item is not None
            ]
        return value

    # NOTIFICATION CONTROL

    async def should_send_notification(
        self, session_manager, notification_key: str, cooldown_seconds: int = 3600
    ) -> bool:
        if await session_manager.was_notification_sent(
            notification_key, within_seconds=cooldown_seconds
        ):
            log(
                f"Skipping notification for {notification_key} (in cooldown period)",
                LOG_VERBOSE,
            )
            return False
        return True
