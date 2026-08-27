"""Validation and resolution for per-DVR notification routing."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


APPRISE_DEST_KEYS = (
    "pushover",
    "discord",
    "email",
    "telegram",
    "slack",
    "gotify",
    "matrix",
    "custom",
)
ALL_DEST_KEYS = APPRISE_DEST_KEYS + ("webhook",)
ROUTING_EVENT_TYPES = ("channel", "vod", "recording", "disk", "health")

_ALL_ENABLED = {key: True for key in ALL_DEST_KEYS}
_ALL_DISABLED = {key: False for key in ALL_DEST_KEYS}


def resolve_notification_routing(
    dvr_id: str,
    event_type: str,
    routing_config: Mapping[str, Any] | Any,
) -> dict[str, bool]:
    """Resolve one route without ever broadening an explicit event mapping.

    The empty top-level mapping is the legacy all-enabled representation. A
    missing DVR or event likewise means that no explicit route exists for that
    pair. Once an event mapping exists, omitted, unknown, or non-boolean values
    fail closed.
    """
    if routing_config == {} or not dvr_id or not event_type:
        return dict(_ALL_ENABLED)
    if not isinstance(routing_config, Mapping):
        return dict(_ALL_DISABLED)

    if dvr_id not in routing_config:
        return dict(_ALL_ENABLED)
    dvr_routing = routing_config[dvr_id]
    if not isinstance(dvr_routing, Mapping):
        return dict(_ALL_DISABLED)

    if event_type not in dvr_routing:
        return dict(_ALL_ENABLED)
    event_routing = dvr_routing[event_type]
    if not isinstance(event_routing, Mapping):
        return dict(_ALL_DISABLED)
    if any(key not in ALL_DEST_KEYS for key in event_routing):
        return dict(_ALL_DISABLED)

    return {
        key: value if isinstance((value := event_routing.get(key)), bool) else False
        for key in ALL_DEST_KEYS
    }


def normalize_notification_routing(
    routing_config: Any,
    dvr_servers: Iterable[Mapping[str, Any] | Any],
) -> dict[str, dict[str, dict[str, bool]]]:
    """Validate a saved route and normalize explicit maps to complete maps.

    Missing destination keys in an explicit event map are normalized to
    ``False``. This preserves compatibility with older partial maps without
    allowing an omitted destination to become enabled. Unknown DVR IDs, event
    types, destinations, and non-boolean values are rejected with diagnostic
    messages before settings are persisted.
    """
    if routing_config in (None, {}):
        return {}
    if not isinstance(routing_config, Mapping):
        raise ValueError("notification_routing must be an object")

    known_dvr_ids: set[str] = set()
    for server in dvr_servers:
        raw_server_id = (
            server.get("id")
            if isinstance(server, Mapping)
            else getattr(server, "id", None)
        )
        server_id = str(raw_server_id or "").strip()
        if server_id:
            known_dvr_ids.add(server_id)
    errors: list[str] = []
    normalized: dict[str, dict[str, dict[str, bool]]] = {}

    for raw_dvr_id, raw_events in routing_config.items():
        dvr_id = str(raw_dvr_id or "").strip()
        if not dvr_id or dvr_id not in known_dvr_ids:
            errors.append(f"stale or unknown DVR id {raw_dvr_id!r}")
            continue
        if not isinstance(raw_events, Mapping):
            errors.append(f"DVR {dvr_id!r} routing must be an object")
            continue

        normalized_events: dict[str, dict[str, bool]] = {}
        for raw_event_type, raw_destinations in raw_events.items():
            event_type = str(raw_event_type or "").strip().lower()
            if event_type not in ROUTING_EVENT_TYPES:
                errors.append(
                    f"DVR {dvr_id!r} has unknown event type {raw_event_type!r}"
                )
                continue
            if not isinstance(raw_destinations, Mapping):
                errors.append(
                    f"DVR {dvr_id!r} event {event_type!r} routing must be an object"
                )
                continue

            unknown_destinations = sorted(
                str(key) for key in raw_destinations if key not in ALL_DEST_KEYS
            )
            if unknown_destinations:
                errors.append(
                    f"DVR {dvr_id!r} event {event_type!r} has unknown destinations: "
                    + ", ".join(unknown_destinations)
                )
                continue

            invalid_values = sorted(
                str(key)
                for key, value in raw_destinations.items()
                if not isinstance(value, bool)
            )
            if invalid_values:
                errors.append(
                    f"DVR {dvr_id!r} event {event_type!r} destinations must be "
                    "boolean: " + ", ".join(invalid_values)
                )
                continue

            normalized_events[event_type] = {
                key: bool(raw_destinations.get(key, False)) for key in ALL_DEST_KEYS
            }

        if normalized_events:
            normalized[dvr_id] = normalized_events

    if errors:
        raise ValueError("Invalid notification routing: " + "; ".join(errors))
    return normalized


def notification_routing_diagnostics(
    routing_config: Any,
    dvr_servers: Iterable[Mapping[str, Any] | Any],
) -> list[str]:
    """Return redaction-safe validation messages for authenticated health."""
    try:
        normalize_notification_routing(routing_config, dvr_servers)
    except ValueError as exc:
        return [str(exc)]
    return []
