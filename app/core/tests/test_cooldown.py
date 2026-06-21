"""Tests for channel watching alert cooldown behavior."""

import time
import pytest

from core.alerts.common.session_manager import SessionManager
from core.alerts.common.alert_formatter import AlertFormatter


class TestCooldownRapidEvents:
    @pytest.mark.anyio
    async def test_100_rapid_events_produce_one_notification(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        cooldown = 300
        tracking_key = "ch7-Living Room"

        sent_count = 0
        for _ in range(100):
            if await formatter.should_send_notification(sm, tracking_key, cooldown):
                sent_count += 1
                await sm.record_notification(tracking_key)

        assert sent_count == 1

    @pytest.mark.anyio
    async def test_rapid_events_different_keys_each_get_one(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        cooldown = 300

        keys = ["ch7-Living Room", "ch13-Bedroom", "ch5-Kitchen"]
        counts = {k: 0 for k in keys}

        for _ in range(50):
            for key in keys:
                if await formatter.should_send_notification(sm, key, cooldown):
                    counts[key] += 1
                    await sm.record_notification(key)

        for key in keys:
            assert counts[key] == 1

    @pytest.mark.anyio
    async def test_first_event_always_sends(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"

        assert await formatter.should_send_notification(sm, tracking_key, 300) is True

    @pytest.mark.anyio
    async def test_second_event_within_cooldown_blocked(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"
        cooldown = 300

        await sm.record_notification(tracking_key)

        assert (
            await formatter.should_send_notification(sm, tracking_key, cooldown)
            is False
        )


class TestCooldownExpiry:
    @pytest.mark.anyio
    async def test_event_after_cooldown_expiry_sends(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"
        cooldown = 300

        assert (
            await formatter.should_send_notification(sm, tracking_key, cooldown) is True
        )
        await sm.record_notification(tracking_key)

        sm.notification_history[tracking_key] = time.time() - 301

        assert (
            await formatter.should_send_notification(sm, tracking_key, cooldown) is True
        )

    @pytest.mark.anyio
    async def test_event_just_before_cooldown_expiry_blocked(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"
        cooldown = 300

        await sm.record_notification(tracking_key)
        sm.notification_history[tracking_key] = time.time() - 299

        assert (
            await formatter.should_send_notification(sm, tracking_key, cooldown)
            is False
        )

    @pytest.mark.anyio
    async def test_multiple_cooldown_cycles(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"
        cooldown = 300

        sent_count = 0
        for cycle in range(5):
            if cycle > 0:
                sm.notification_history[tracking_key] = time.time() - 301

            if await formatter.should_send_notification(sm, tracking_key, cooldown):
                sent_count += 1
                await sm.record_notification(tracking_key)

            for _ in range(10):
                assert (
                    await formatter.should_send_notification(sm, tracking_key, cooldown)
                    is False
                )

        assert sent_count == 5


class TestCooldownConfigurable:
    @pytest.mark.anyio
    async def test_short_cooldown(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"
        cooldown = 10

        await sm.record_notification(tracking_key)
        sm.notification_history[tracking_key] = time.time() - 11

        assert (
            await formatter.should_send_notification(sm, tracking_key, cooldown) is True
        )

    @pytest.mark.anyio
    async def test_zero_cooldown_always_sends(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"
        cooldown = 0

        for _ in range(10):
            assert (
                await formatter.should_send_notification(sm, tracking_key, cooldown)
                is True
            )
            await sm.record_notification(tracking_key)

    @pytest.mark.anyio
    async def test_default_300s_cooldown(self):
        sm = SessionManager()
        formatter = AlertFormatter()
        tracking_key = "ch7-Living Room"

        await sm.record_notification(tracking_key)
        sm.notification_history[tracking_key] = time.time() - 200

        assert await formatter.should_send_notification(sm, tracking_key, 300) is False

        sm.notification_history[tracking_key] = time.time() - 301

        assert await formatter.should_send_notification(sm, tracking_key, 300) is True
