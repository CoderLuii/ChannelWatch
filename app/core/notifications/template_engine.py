"""Render user-configurable notification templates.

Templates use braces for placeholders, optional prefix and suffix text, simple
conditional tags, list slicing format specifiers, and case modifiers. Rendering
raises ``TemplateRenderError`` when a template references unsupported syntax or
unknown fields.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping


CHANNEL_WATCHING_DEFAULT_TITLE = "Channels DVR - Watching TV"
CHANNEL_WATCHING_DEFAULT_BODY = "\n".join(
    [
        "{📺 <channel_name}",
        "{Channel: <channel_number}",
        "{Program: <program_title}",
        "{Resolution: <resolution}",
        "{Device: <client_name}",
        "{Device IP: <client_ip}",
        "{Source: <stream_source}",
        "{Total Streams: <stream_count}",
    ]
)

VOD_WATCHING_DEFAULT_TITLE = "🎬 Channels DVR - Watching DVR Content"
VOD_WATCHING_DEFAULT_BODY = "\n".join(
    [
        "{media_title}",
        "{progress_line}",
        "{Device Name: <client_name}",
        "{Device IP: <client_ip}",
        "{summary_block}",
        "{info_sections}",
    ]
)

RECORDING_EVENTS_DEFAULT_TITLE = "Channels DVR - Recording Event"
RECORDING_EVENTS_DEFAULT_BODY = "\n".join(
    [
        "{📺 <channel_name}",
        "{Channel: <channel_number}",
        "{status}",
        "{details}",
        "{summary_block}",
        "{time_table}",
    ]
)

DISK_SPACE_DEFAULT_TITLE = "⚠️ Low Disk Space Warning"
DISK_SPACE_DEFAULT_BODY = "\n".join(
    [
        "Free Space: {disk_free} / {disk_total} ({disk_percent})",
        "Used Space: {disk_used}",
        "DVR Path: {disk_path}",
    ]
)

TEMPLATE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "channel_watching": {
        "title": CHANNEL_WATCHING_DEFAULT_TITLE,
        "body": CHANNEL_WATCHING_DEFAULT_BODY,
        "use_default": True,
    },
    "vod_watching": {
        "title": VOD_WATCHING_DEFAULT_TITLE,
        "body": VOD_WATCHING_DEFAULT_BODY,
        "use_default": True,
    },
    "recording_events": {
        "title": RECORDING_EVENTS_DEFAULT_TITLE,
        "body": RECORDING_EVENTS_DEFAULT_BODY,
        "use_default": True,
    },
    "disk_space": {
        "title": DISK_SPACE_DEFAULT_TITLE,
        "body": DISK_SPACE_DEFAULT_BODY,
        "use_default": True,
    },
}

TEMPLATE_SETTINGS_DEFAULTS: Dict[str, Any] = {
    "cw_template_title": CHANNEL_WATCHING_DEFAULT_TITLE,
    "cw_template_body": CHANNEL_WATCHING_DEFAULT_BODY,
    "cw_template_use_default": True,
    "vod_template_title": VOD_WATCHING_DEFAULT_TITLE,
    "vod_template_body": VOD_WATCHING_DEFAULT_BODY,
    "vod_template_use_default": True,
    "rd_template_title": RECORDING_EVENTS_DEFAULT_TITLE,
    "rd_template_body": RECORDING_EVENTS_DEFAULT_BODY,
    "rd_template_use_default": True,
    "ds_template_title": DISK_SPACE_DEFAULT_TITLE,
    "ds_template_body": DISK_SPACE_DEFAULT_BODY,
    "ds_template_use_default": True,
}


class TemplateRenderError(ValueError):
    """Signal invalid notification template syntax or data references."""

    pass


class NotificationTemplateEngine:
    """Render notification title and body templates from context values.

    Supported syntax includes ``{field}``, ``{prefix <field> suffix}``, list
    slices such as ``{items:[0:2]}``, case modifiers like ``{title!u}``, and
    conditional tags including ``<movie>`` and ``<started>``.
    """

    _field_pattern = re.compile(r"\{([^{}]+)\}")
    _field_name_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _slice_pattern = re.compile(r"^\[(?:(\d+)?)?(?::(\d+)?)?\]$")
    _supported_tags = (
        "movie",
        "episode",
        "show",
        "live",
        "recorded",
        "started",
        "completed",
        "failed",
        "cancelled",
    )

    def render(self, template: str, context: Mapping[str, Any]) -> str:
        """Return *template* rendered with values from *context*.

        Conditional tags are processed before placeholders. Missing fields,
        unsupported modifiers, and non-string templates raise
        ``TemplateRenderError``.
        """
        if not isinstance(template, str):
            raise TemplateRenderError("Template must be a string")

        rendered_template = self._process_conditional_tags(template, context)
        return self._field_pattern.sub(
            lambda match: self._replace_field(match, context), rendered_template
        )

    def _replace_field(self, match: re.Match[str], context: Mapping[str, Any]) -> str:
        expression = match.group(1).strip()
        if not expression:
            raise TemplateRenderError("Empty placeholder is not allowed")

        prefix = ""
        suffix = ""
        field_expression = expression

        if "<" in field_expression:
            prefix, field_expression = field_expression.split("<", 1)

        if ">" in field_expression:
            field_expression, suffix = field_expression.split(">", 1)

        field_expression = field_expression.strip()
        field_name = field_expression
        case_modifier = ""
        format_spec = ""

        if ":" in field_name:
            field_name, format_spec = field_name.split(":", 1)

        if "!" in field_name:
            field_name, case_modifier = field_name.split("!", 1)

        field_name = field_name.strip()
        case_modifier = case_modifier.strip()
        format_spec = format_spec.strip()

        if not self._field_name_pattern.fullmatch(field_name):
            raise TemplateRenderError(f"Invalid placeholder '{expression}'")

        if field_name not in context:
            raise TemplateRenderError(f"Unknown placeholder '{field_name}'")

        value = context[field_name]
        if self._is_empty(value):
            return ""

        value = self._apply_format(value, format_spec, field_name)
        value = self._apply_case(value, case_modifier, field_name)

        if self._is_empty(value):
            return ""

        return f"{prefix}{value}{suffix}"

    def _process_conditional_tags(
        self, template: str, context: Mapping[str, Any]
    ) -> str:
        rendered = template
        for tag in self._supported_tags:
            pattern = re.compile(rf"<{tag}>(.*?)</{tag}>", re.DOTALL)
            condition = self._tag_applies(tag, context)
            replacement = r"\1" if condition else ""
            rendered = pattern.sub(replacement, rendered)
        return rendered

    def _tag_applies(self, tag: str, context: Mapping[str, Any]) -> bool:
        media_type = str(context.get("media_type", "")).strip().lower()
        is_live = context.get("is_live")
        recording_status = str(context.get("recording_status", "")).strip().lower()

        if tag == "movie":
            return media_type == "movie"
        if tag == "episode":
            return media_type == "episode"
        if tag == "show":
            return media_type in {"episode", "show"}
        if tag == "live":
            return is_live in {True, "true", "True", "yes", "Yes"}
        if tag == "recorded":
            return not self._tag_applies("live", context)
        return recording_status == tag

    def _apply_format(self, value: Any, format_spec: str, field_name: str) -> str:
        if not format_spec:
            return self._stringify_value(value)

        slice_match = self._slice_pattern.fullmatch(format_spec)
        if not slice_match:
            raise TemplateRenderError(
                f"Unsupported format specifier '{format_spec}' for '{field_name}'"
            )

        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple)):
            items = [
                self._stringify_value(item)
                for item in value
                if not self._is_empty(item)
            ]
        else:
            raise TemplateRenderError(
                f"Slice format requires a list-like value for '{field_name}'"
            )

        start = int(slice_match.group(1)) if slice_match.group(1) else None
        end = int(slice_match.group(2)) if slice_match.group(2) else None

        if ":" in format_spec:
            sliced_items = items[slice(start, end)]
        else:
            if start is None:
                raise TemplateRenderError(f"Invalid list index for '{field_name}'")
            sliced_items = items[start : start + 1]

        return ", ".join(item for item in sliced_items if item)

    def _apply_case(self, value: Any, case_modifier: str, field_name: str) -> str:
        text = self._stringify_value(value)
        if not case_modifier:
            return text
        if case_modifier == "c":
            return text.title()
        if case_modifier == "u":
            return text.upper()
        if case_modifier == "l":
            return text.lower()
        raise TemplateRenderError(
            f"Unsupported case modifier '!{case_modifier}' for '{field_name}'"
        )

    def _stringify_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (list, tuple)):
            return ", ".join(
                self._stringify_value(item)
                for item in value
                if not self._is_empty(item)
            )
        return str(value)

    def _is_empty(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value == ""
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) == 0
        return False
