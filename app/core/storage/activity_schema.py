"""Normalize legacy and current activity payloads for durable storage."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .models import ActivityEvent

log = logging.getLogger(__name__)


def parse_activity_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        log.warning("Cannot parse activity timestamp; falling back to epoch")
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def serialize_activity_extra(value: object) -> str:
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except (ValueError, TypeError):
            return json.dumps({"raw": value})
    return "{}"


def activity_payload_to_model(row: dict[str, Any]) -> ActivityEvent | None:
    row_id = str(row.get("id") or "").strip()
    event_type = str(row.get("type") or row.get("event_type") or "").strip()
    if not row_id or not event_type:
        return None

    return ActivityEvent(
        id=row_id,
        dvr_id=row.get("dvr_id") or "",
        event_type=event_type,
        title=row.get("title") or "",
        message=row.get("message") or "",
        timestamp=parse_activity_timestamp(
            row.get("timestamp", datetime.now(timezone.utc))
        ),
        icon=row.get("icon") or "bell",
        channel_name=row.get("channel_name") or "",
        channel_number=row.get("channel_number") or "",
        device_name=row.get("device_name") or "",
        device_ip=row.get("device_ip") or "",
        program_title=row.get("program_title") or "",
        image_url=row.get("image_url") or "",
        stream_source=row.get("stream_source") or "",
        dvr_name=row.get("dvr_name") or "",
        extra=serialize_activity_extra(row.get("extra", {})),
        is_test=bool(row.get("is_test", False)),
    )
