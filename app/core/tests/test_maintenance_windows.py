from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from core.maintenance_windows import calculate_maintenance_windows


def _ts(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


def _job(start: str, minutes: int) -> dict:
    return {"Time": _ts(start), "Duration": minutes * 60}


def test_merges_overlapping_and_adjacent_recordings_before_finding_gaps():
    result = calculate_maintenance_windows(
        [
            _job("2026-10-10T12:00:00+00:00", 60),
            _job("2026-10-10T12:30:00+00:00", 90),
            _job("2026-10-10T14:00:00+00:00", 60),
            _job("2026-10-10T18:00:00+00:00", 60),
        ],
        now=datetime(2026, 10, 10, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
        horizon_days=1,
        minimum_minutes=60,
        start_hour=0,
        end_hour=24,
    )

    assert result["status"] == "truncated"
    assert result["windows"] == [
        {
            "start": "2026-10-10T10:00:00+00:00",
            "end": "2026-10-10T12:00:00+00:00",
            "duration_minutes": 120,
        },
        {
            "start": "2026-10-10T15:00:00+00:00",
            "end": "2026-10-10T18:00:00+00:00",
            "duration_minutes": 180,
        },
    ]


def test_overnight_filter_spans_midnight_without_including_daytime():
    result = calculate_maintenance_windows(
        [
            _job("2026-10-11T03:00:00+00:00", 60),
            _job("2026-10-12T08:00:00+00:00", 60),
        ],
        now=datetime(2026, 10, 10, 20, tzinfo=timezone.utc),
        timezone_name="UTC",
        horizon_days=2,
        minimum_minutes=60,
        start_hour=22,
        end_hour=6,
    )

    assert result["windows"] == [
        {
            "start": "2026-10-10T22:00:00+00:00",
            "end": "2026-10-11T03:00:00+00:00",
            "duration_minutes": 300,
        },
        {
            "start": "2026-10-11T04:00:00+00:00",
            "end": "2026-10-11T06:00:00+00:00",
            "duration_minutes": 120,
        },
        {
            "start": "2026-10-11T22:00:00+00:00",
            "end": "2026-10-12T06:00:00+00:00",
            "duration_minutes": 480,
        },
    ]


def test_dst_spring_forward_uses_elapsed_time_for_minimum_duration():
    try:
        ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        pytest.skip("host does not provide IANA timezone data")
    result = calculate_maintenance_windows(
        [
            _job("2027-03-14T06:00:00+00:00", 30),
            _job("2027-03-14T11:00:00+00:00", 30),
        ],
        now=datetime(2027, 3, 14, 5, tzinfo=timezone.utc),
        timezone_name="America/New_York",
        horizon_days=1,
        minimum_minutes=180,
        start_hour=0,
        end_hour=6,
    )

    assert result["windows"] == [
        {
            "start": "2027-03-14T06:30:00+00:00",
            "end": "2027-03-14T10:00:00+00:00",
            "duration_minutes": 210,
        }
    ]


def test_malformed_or_empty_schedule_never_becomes_free_time():
    malformed = calculate_maintenance_windows(
        [{"Time": "bad", "Duration": 3600}],
        now=datetime(2026, 10, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
        horizon_days=7,
        minimum_minutes=60,
        start_hour=0,
        end_hour=24,
    )
    empty = calculate_maintenance_windows(
        [],
        now=datetime(2026, 10, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
        horizon_days=7,
        minimum_minutes=60,
        start_hour=0,
        end_hour=24,
    )

    assert malformed["status"] == "unknown"
    assert malformed["windows"] == []
    assert empty["status"] == "unknown"
    assert empty["windows"] == []


def test_truncated_schedule_never_marks_the_unobserved_tail_free():
    result = calculate_maintenance_windows(
        [
            _job("2026-10-10T12:00:00+00:00", 60),
            _job("2026-10-10T18:00:00+00:00", 60),
        ],
        now=datetime(2026, 10, 10, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
        horizon_days=7,
        minimum_minutes=60,
        start_hour=0,
        end_hour=24,
    )

    assert result["status"] == "truncated"
    assert result["coverage_end"] == "2026-10-10T18:00:00+00:00"
    assert all(window["end"] <= result["coverage_end"] for window in result["windows"])


def test_weekday_filter_excludes_weekend_gaps():
    result = calculate_maintenance_windows(
        [
            _job("2026-10-10T08:00:00+00:00", 60),
            _job("2026-10-12T20:00:00+00:00", 60),
        ],
        now=datetime(2026, 10, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
        horizon_days=3,
        minimum_minutes=60,
        start_hour=0,
        end_hour=24,
        weekdays={0, 1, 2, 3, 4},
    )

    assert result["windows"] == [
        {
            "start": "2026-10-12T00:00:00+00:00",
            "end": "2026-10-12T20:00:00+00:00",
            "duration_minutes": 1200,
        }
    ]


def test_active_recording_keeps_current_time_busy_until_its_end():
    result = calculate_maintenance_windows(
        [
            _job("2026-10-10T09:30:00+00:00", 90),
            _job("2026-10-10T14:00:00+00:00", 60),
        ],
        now=datetime(2026, 10, 10, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
        horizon_days=1,
        minimum_minutes=60,
        start_hour=0,
        end_hour=24,
    )

    assert result["windows"] == [
        {
            "start": "2026-10-10T11:00:00+00:00",
            "end": "2026-10-10T14:00:00+00:00",
            "duration_minutes": 180,
        }
    ]
