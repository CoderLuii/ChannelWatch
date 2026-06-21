"""Tests for offline-DVR exponential backoff and per-DVR alert pause.

When a DVR goes offline, ChannelWatch should retry with capped exponential
backoff, pause alerts only for that DVR, resume alerts after reconnection, and
publish status details for the UI.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch

from core.helpers.initialize import initialize_event_monitor  # noqa: F401 — pre-initializes core.helpers to break circular import chain
from core.engine.event_monitor import EventMonitor


pytest_plugins = ["core.tests.fixtures.mock_dvr_cluster"]


class TestOfflineDvrBehavior:
    def test_dvr_offline_backoff_schedule(self, mock_dvr_cluster):
        """Reconnect delays must start at 1 s and double to a 60 s cap."""
        cluster = mock_dvr_cluster(count=2)
        dvr_a = cluster[0]
        dvr_a.stop()

        monitor = EventMonitor(dvr=dvr_a)
        monitor.running = True

        sleep_calls: list[float] = []

        def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            if len(sleep_calls) >= 7:
                monitor.running = False

        with (
            patch.object(
                monitor, "_monitor_events", side_effect=ConnectionError("DVR-A offline")
            ),
            patch("core.engine.event_monitor.time.sleep", side_effect=capture_sleep),
        ):
            monitor._monitor_events_loop()

        expected = [1, 2, 4, 8, 16, 32, 60]
        assert sleep_calls == expected, (
            f"Exponential backoff must start at 1 s and double each retry, capped at 60 s.\n"
            f"  Expected: {expected}\n"
            f"  Got:      {sleep_calls}\n"
            f"Initial reconnect_delay should be 1 second."
        )

    def test_dvr_offline_pauses_alerts_for_that_dvr_only(self, mock_dvr_cluster):
        """When DVR-A is offline, only DVR-A alerts should pause."""
        cluster = mock_dvr_cluster(count=2)
        dvr_a = cluster[0]
        dvr_b = cluster[1]
        dvr_a.stop()

        monitor_a = EventMonitor(dvr=dvr_a)
        monitor_a.running = True
        monitor_b = EventMonitor(dvr=dvr_b)

        with (
            patch.object(
                monitor_a,
                "_monitor_events",
                side_effect=ConnectionError("DVR-A offline"),
            ),
            patch(
                "core.engine.event_monitor.time.sleep",
                side_effect=lambda _: setattr(monitor_a, "running", False),
            ),
        ):
            monitor_a._monitor_events_loop()

        assert monitor_a.alerts_paused is True, (
            "EventMonitor.alerts_paused must be True after DVR-A goes offline.\n"
            f"Existing attributes that contain 'pause' or 'offline': "
            f"{[a for a in dir(monitor_a) if 'pause' in a.lower() or 'offline' in a.lower()]}"
        )
        assert monitor_b.alerts_paused is False, (
            "A fresh EventMonitor (DVR-B still online) must have alerts_paused=False."
        )

    def test_dvr_reconnect_resumes_alerts(self, mock_dvr_cluster):
        """After a successful reconnection, alerts_paused must clear to False."""
        cluster = mock_dvr_cluster(count=1)
        dvr_a = cluster[0]

        monitor = EventMonitor(dvr=dvr_a)
        monitor.running = True

        call_count = {"n": 0}

        def fake_monitor_events() -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("DVR-A temporarily offline")
            monitor.connected = True
            monitor.running = False

        with (
            patch.object(monitor, "_monitor_events", side_effect=fake_monitor_events),
            patch("core.engine.event_monitor.time.sleep"),
        ):
            monitor._monitor_events_loop()

        assert call_count["n"] == 2, (
            f"Expected 2 _monitor_events calls (1 offline + 1 reconnect), got {call_count['n']}."
        )

        assert monitor.alerts_paused is False, (
            "EventMonitor.alerts_paused must be False after successful reconnection."
        )

    def test_dvr_offline_ui_status_updated(self, mock_dvr_cluster):
        """After going offline, monitor.dvr_status must report status and last_seen."""
        cluster = mock_dvr_cluster(count=1)
        dvr_a = cluster[0]
        dvr_a.stop()

        monitor = EventMonitor(dvr=dvr_a)
        monitor.running = True

        before_ts = time.time()

        with (
            patch.object(
                monitor, "_monitor_events", side_effect=ConnectionError("DVR-A offline")
            ),
            patch(
                "core.engine.event_monitor.time.sleep",
                side_effect=lambda _: setattr(monitor, "running", False),
            ),
        ):
            monitor._monitor_events_loop()

        after_ts = time.time()

        status = monitor.dvr_status

        assert isinstance(status, dict), (
            f"monitor.dvr_status must return a dict. Got {type(status)!r}. "
            "dvr_status should be exposed by EventMonitor."
        )
        assert status.get("status") == "offline", (
            f"dvr_status['status'] must be 'offline' when the DVR is unreachable. "
            f"Got: {status!r}"
        )
        assert "last_seen" in status, (
            f"dvr_status must include 'last_seen' timestamp key. "
            f"Keys present: {list(status.keys())}"
        )
        assert isinstance(status["last_seen"], (int, float)), (
            f"dvr_status['last_seen'] must be a numeric UNIX timestamp (int or float). "
            f"Got {type(status['last_seen'])!r}: {status['last_seen']!r}"
        )
        assert before_ts - 1 <= status["last_seen"] <= after_ts + 1, (
            f"last_seen={status['last_seen']:.3f} is outside the expected window "
            f"[{before_ts:.3f}, {after_ts + 1:.3f}]"
        )


class TestDvrRetryAfterBackoff:
    def test_async_monitor_events_records_retry_after_from_429_response(self):
        class FakeResponse:
            status_code = 429
            headers = {"Retry-After": "7"}

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, *args, **kwargs):
                return FakeStream()

        class DummyAlertManager:
            alert_instances = {}

            async def load_all_state(self):
                return None

            def create_background_tasks(self):
                return []

            async def save_all_state(self):
                return None

        monitor = EventMonitor(host="127.0.0.1", alert_manager=DummyAlertManager())

        async def run():
            with patch("core.engine.event_monitor.httpx.AsyncClient", FakeAsyncClient):
                await monitor._async_monitor_events()

        asyncio.run(run())

        assert monitor._consume_retry_after_delay() == 7.0

    def test_retry_after_seconds_overrides_next_reconnect_sleep(self):
        monitor = EventMonitor(host="127.0.0.1")
        monitor.running = True
        sleep_calls: list[float] = []

        def fake_monitor_events() -> None:
            monitor._record_retry_after_backoff("7")

        def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            monitor.running = False

        with (
            patch.object(monitor, "_monitor_events", side_effect=fake_monitor_events),
            patch("core.engine.event_monitor.time.sleep", side_effect=capture_sleep),
        ):
            monitor._monitor_events_loop()

        assert sleep_calls == [1.0]

    def test_retry_after_seconds_are_clamped_to_safe_maximum(self):
        assert EventMonitor._parse_retry_after("9999") == 60.0

    def test_reconnect_sleep_stops_promptly_when_monitor_is_stopped(self):
        monitor = EventMonitor(host="127.0.0.1")
        monitor.running = True
        sleep_calls: list[float] = []

        def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            monitor.running = False

        with patch("core.engine.event_monitor.time.sleep", side_effect=capture_sleep):
            monitor._sleep_interruptibly(60.0)

        assert sleep_calls == [1.0]

    def test_retry_after_http_date_is_parsed(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=30)

        delay = EventMonitor._parse_retry_after(format_datetime(future))

        assert delay is not None
        assert 0 < delay <= 30

    def test_invalid_or_missing_retry_after_uses_exponential_fallback(self):
        monitor = EventMonitor(host="127.0.0.1")
        monitor.running = True
        sleep_calls: list[float] = []

        def fake_monitor_events() -> None:
            assert monitor._record_retry_after_backoff("not-a-date") is False
            assert monitor._record_retry_after_backoff(None) is False

        def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            monitor.running = False

        with (
            patch.object(monitor, "_monitor_events", side_effect=fake_monitor_events),
            patch("core.engine.event_monitor.time.sleep", side_effect=capture_sleep),
        ):
            monitor._monitor_events_loop()

        assert sleep_calls == [1]
