"""Tests for session manager state persistence."""

import time
import pytest

from core.alerts.common.session_manager import SessionManager


class TestGetState:
    @pytest.mark.anyio
    async def test_empty_state(self):
        sm = SessionManager()
        state = await sm.get_state()
        assert state == {"active_sessions": {}, "notification_history": {}}

    @pytest.mark.anyio
    async def test_state_includes_sessions_and_history(self):
        sm = SessionManager()
        await sm.add_session(
            "sess1", channel_info={"name": "ABC"}, tracking_key="ch7-TV"
        )
        await sm.record_notification("ch7-TV")
        state = await sm.get_state()
        assert "sess1" in state["active_sessions"]
        assert "ch7-TV" in state["notification_history"]


class TestLoadState:
    @pytest.mark.anyio
    async def test_load_restores_sessions(self):
        sm = SessionManager()
        now = time.time()
        state = {
            "active_sessions": {
                "sess1": {"channel_info": {"name": "ABC"}, "timestamp": now}
            },
            "notification_history": {"ch7-TV": now},
        }
        await sm.load_state(state)
        assert await sm.has_session("sess1")
        assert await sm.was_notification_sent("ch7-TV", within_seconds=60)

    @pytest.mark.anyio
    async def test_stale_sessions_cleaned_on_load(self):
        sm = SessionManager()
        old_time = time.time() - 7200
        recent_time = time.time() - 600
        state = {
            "active_sessions": {
                "old_sess": {"channel_info": {"name": "OLD"}, "timestamp": old_time},
                "new_sess": {"channel_info": {"name": "NEW"}, "timestamp": recent_time},
            },
            "notification_history": {
                "old_key": old_time,
                "new_key": recent_time,
            },
        }
        await sm.load_state(state, stale_threshold=3600)
        assert not await sm.has_session("old_sess")
        assert await sm.has_session("new_sess")
        assert not await sm.was_notification_sent("old_key", within_seconds=3600)
        assert await sm.was_notification_sent("new_key", within_seconds=3600)

    @pytest.mark.anyio
    async def test_load_empty_state_is_safe(self):
        sm = SessionManager()
        await sm.load_state({})
        assert await sm.get_state() == {
            "active_sessions": {},
            "notification_history": {},
        }

    @pytest.mark.anyio
    async def test_load_does_not_clear_existing(self):
        sm = SessionManager()
        await sm.add_session("existing", channel_info={"name": "X"}, tracking_key="k")
        now = time.time()
        await sm.load_state(
            {
                "active_sessions": {
                    "loaded": {"channel_info": {"name": "Y"}, "timestamp": now}
                },
                "notification_history": {},
            }
        )
        assert await sm.has_session("existing")
        assert await sm.has_session("loaded")


class TestRoundTrip:
    @pytest.mark.anyio
    async def test_save_and_load_round_trip(self):
        sm1 = SessionManager()
        await sm1.add_session(
            "sess1", channel_info={"name": "CBS", "number": "2"}, tracking_key="ch2-Den"
        )
        await sm1.record_notification("ch2-Den")
        state = await sm1.get_state()

        sm2 = SessionManager()
        await sm2.load_state(state)
        assert await sm2.has_session("sess1")
        sess_data = await sm2.get_session("sess1")
        assert sess_data["channel_info"]["name"] == "CBS"
        assert await sm2.was_notification_sent("ch2-Den", within_seconds=60)

    @pytest.mark.anyio
    async def test_notification_cooldown_survives_round_trip(self):
        sm1 = SessionManager()
        await sm1.record_notification("ch7-Living Room")
        state = await sm1.get_state()

        sm2 = SessionManager()
        await sm2.load_state(state)
        assert await sm2.was_notification_sent("ch7-Living Room", within_seconds=300)

    @pytest.mark.anyio
    async def test_multiple_sessions_round_trip(self):
        sm1 = SessionManager()
        for i in range(5):
            await sm1.add_session(
                f"sess{i}", channel_info={"name": f"Ch{i}"}, tracking_key=f"key{i}"
            )
            await sm1.record_notification(f"key{i}")
        state = await sm1.get_state()

        sm2 = SessionManager()
        await sm2.load_state(state)
        for i in range(5):
            assert await sm2.has_session(f"sess{i}")
            assert await sm2.was_notification_sent(f"key{i}", within_seconds=60)
