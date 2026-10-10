"""Conservative maintenance-window calculations for DVR recording schedules."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Iterable
from zoneinfo import ZoneInfo


def _as_timestamp(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _recording_interval(recording: Any) -> tuple[int, int] | None:
    if not isinstance(recording, dict):
        return None

    start = _as_timestamp(recording.get("Time"))
    if start is None:
        return None

    stop = _as_timestamp(recording.get("stop_time") or recording.get("StopTime"))
    if stop is not None and stop > start:
        return start, stop

    airing = recording.get("Airing")
    if not isinstance(airing, dict):
        airing = {}
    duration = _as_timestamp(
        recording.get("Duration")
        or airing.get("Duration")
        or recording.get("duration")
    )
    if duration is None:
        return None
    return start, start + duration


def _merge_intervals(
    intervals: Iterable[tuple[int, int]],
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _local_boundary(day: date, hour: int, zone: tzinfo) -> datetime:
    if hour == 24:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return datetime.combine(day, time(hour=hour), tzinfo=zone)


def _daily_allowed_intervals(
    start: int,
    end: int,
    *,
    start_hour: int,
    end_hour: int,
    zone: tzinfo,
    weekdays: set[int] | None,
) -> list[tuple[int, int]]:
    local_start = datetime.fromtimestamp(start, tz=zone)
    local_end = datetime.fromtimestamp(end, tz=zone)
    day = local_start.date() - timedelta(days=1)
    last_day = local_end.date()
    intervals: list[tuple[int, int]] = []

    while day <= last_day:
        if weekdays is not None and day.weekday() not in weekdays:
            day += timedelta(days=1)
            continue
        window_start = _local_boundary(day, start_hour, zone)
        if start_hour < end_hour:
            window_end = _local_boundary(day, end_hour, zone)
        else:
            window_end = _local_boundary(day + timedelta(days=1), end_hour, zone)
        start_ts = max(start, int(window_start.timestamp()))
        end_ts = min(end, int(window_end.timestamp()))
        if end_ts > start_ts:
            intervals.append((start_ts, end_ts))
        day += timedelta(days=1)

    return _merge_intervals(intervals)


def calculate_maintenance_windows(
    recordings: Any,
    *,
    now: datetime,
    timezone_name: str,
    horizon_days: int,
    minimum_minutes: int,
    start_hour: int,
    end_hour: int,
    weekdays: set[int] | None = None,
) -> dict[str, Any]:
    """Return recording-free intervals without treating unobserved time as free."""

    if not isinstance(recordings, list) or not recordings:
        return {
            "status": "unknown",
            "coverage_end": None,
            "windows": [],
            "message": "The DVR schedule did not include enough data to prove an open window.",
        }
    if not 1 <= horizon_days <= 31:
        raise ValueError("horizon_days must be between 1 and 31")
    if not 1 <= minimum_minutes <= 1440:
        raise ValueError("minimum_minutes must be between 1 and 1440")
    if not 0 <= start_hour <= 23 or not 0 <= end_hour <= 24:
        raise ValueError("daily hours are outside the supported range")

    zone = timezone.utc if timezone_name == "UTC" else ZoneInfo(timezone_name)
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    start = int(now_utc.timestamp())
    requested_end = int((now_utc + timedelta(days=horizon_days)).timestamp())

    parsed: list[tuple[int, int]] = []
    for recording in recordings:
        interval = _recording_interval(recording)
        if interval is None:
            return {
                "status": "unknown",
                "coverage_end": None,
                "windows": [],
                "message": "The DVR schedule contained an incomplete recording entry, so no time was marked free.",
            }
        parsed.append(interval)

    latest_start = max(interval[0] for interval in parsed)
    coverage_end = min(requested_end, latest_start)
    if coverage_end <= start:
        return {
            "status": "truncated",
            "coverage_end": datetime.fromtimestamp(coverage_end, timezone.utc).isoformat(),
            "windows": [],
            "message": "The known schedule ends too soon to prove an open window.",
        }

    busy = _merge_intervals(
        (max(start, busy_start), min(coverage_end, busy_end))
        for busy_start, busy_end in parsed
        if busy_end > start and busy_start < coverage_end
    )
    gaps: list[tuple[int, int]] = []
    cursor = start
    for busy_start, busy_end in busy:
        if busy_start > cursor:
            gaps.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if cursor < coverage_end:
        gaps.append((cursor, coverage_end))

    minimum_seconds = minimum_minutes * 60
    windows: list[dict[str, Any]] = []
    for gap_start, gap_end in gaps:
        for allowed_start, allowed_end in _daily_allowed_intervals(
            gap_start,
            gap_end,
            start_hour=start_hour,
            end_hour=end_hour,
            zone=zone,
            weekdays=weekdays,
        ):
            duration_seconds = allowed_end - allowed_start
            if duration_seconds < minimum_seconds:
                continue
            windows.append(
                {
                    "start": datetime.fromtimestamp(allowed_start, timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(allowed_end, timezone.utc).isoformat(),
                    "duration_minutes": duration_seconds // 60,
                }
            )

    status = "available" if latest_start >= requested_end else "truncated"
    message = (
        "Open windows are bounded by the requested schedule range."
        if status == "available"
        else "Later time was not assessed because the known schedule ends before the requested range."
    )
    return {
        "status": status,
        "coverage_end": datetime.fromtimestamp(coverage_end, timezone.utc).isoformat(),
        "windows": windows,
        "message": message,
    }
