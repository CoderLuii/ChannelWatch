"""event_lock is per-instance on all three alert modules, not module-global."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import core.alerts.channel_watching as _cw_mod
import core.alerts.vod_watching as _vod_mod
import core.alerts.recording_events as _re_mod


def _mock_settings():
    s = MagicMock()
    s.cw_channel_name = True
    s.cw_channel_number = True
    s.cw_program_name = False
    s.cw_device_name = True
    s.cw_device_ip = False
    s.cw_stream_source = False
    s.cw_alert_cooldown = 60
    s.cw_template_title = ""
    s.cw_template_body = ""
    s.cw_template_use_default = True
    s.cw_image_source = "CHANNEL"
    s.channel_cache_ttl = 300
    s.program_cache_ttl = 300
    s.stream_count = False
    s.tz = "UTC"
    s.vod_device_name = True
    s.vod_device_ip = False
    s.vod_alert_cooldown = 60
    s.vod_template_title = ""
    s.vod_template_body = ""
    s.vod_template_use_default = True
    s.vod_significant_threshold = 0
    s.rd_program_name = True
    s.rd_program_desc = False
    s.rd_duration = True
    s.rd_channel_name = True
    s.rd_channel_number = True
    s.rd_type = False
    s.rd_template_title = ""
    s.rd_template_body = ""
    s.rd_template_use_default = True
    s.rd_alert_scheduled = True
    s.rd_alert_started = True
    s.rd_alert_completed = True
    s.rd_alert_cancelled = True
    s.job_cache_ttl = 300
    return s


def _mock_dvr(host="127.0.0.1", port=8089):
    return SimpleNamespace(
        host=host, port=port, id="dvr_test01", name="TestDVR", overrides={}
    )


def _mock_am(settings=None, dvr=None):
    am = SimpleNamespace(
        notification_manager=MagicMock(),
        settings=settings or _mock_settings(),
        dvr=dvr or _mock_dvr(),
        _notification_history={},
        _history_lock=threading.Lock(),
    )
    return am


def _make_channel_watching():
    with (
        patch("core.alerts.channel_watching.ChannelInfoProvider"),
        patch("core.alerts.channel_watching.StreamTracker"),
        patch("core.alerts.channel_watching.ProgramInfoProvider"),
    ):
        return _cw_mod.ChannelWatchingAlert(_mock_am())


def _make_vod_watching():
    with patch("core.alerts.vod_watching.VODInfoProvider"):
        return _vod_mod.VODWatchingAlert(_mock_am())


def _make_recording_events():
    with (
        patch("core.alerts.recording_events.ChannelInfoProvider"),
        patch("core.alerts.recording_events.JobInfoProvider"),
        patch("core.alerts.recording_events.StreamTracker"),
    ):
        return _re_mod.RecordingEventsAlert(_mock_am())


class TestNoModuleGlobalEventLock:
    def test_channel_watching_has_no_module_global(self):
        assert not hasattr(_cw_mod, "event_lock"), (
            "core.alerts.channel_watching still exposes a module-level event_lock"
        )

    def test_vod_watching_has_no_module_global(self):
        assert not hasattr(_vod_mod, "event_lock"), (
            "core.alerts.vod_watching still exposes a module-level event_lock"
        )

    def test_recording_events_has_no_module_global(self):
        assert not hasattr(_re_mod, "event_lock"), (
            "core.alerts.recording_events still exposes a module-level event_lock"
        )


class TestInstanceEventLockExists:
    def test_channel_watching_has_instance_lock(self):
        alert = _make_channel_watching()
        assert hasattr(alert, "_event_lock")
        assert isinstance(alert._event_lock, asyncio.Lock)

    def test_vod_watching_has_instance_lock(self):
        alert = _make_vod_watching()
        assert hasattr(alert, "_event_lock")
        assert isinstance(alert._event_lock, asyncio.Lock)

    def test_recording_events_has_instance_lock(self):
        alert = _make_recording_events()
        assert hasattr(alert, "_event_lock")
        assert isinstance(alert._event_lock, asyncio.Lock)


class TestTwoInstancesHaveIndependentLocks:
    def test_channel_watching_instances_independent(self):
        a = _make_channel_watching()
        b = _make_channel_watching()
        assert a._event_lock is not b._event_lock

    def test_vod_watching_instances_independent(self):
        a = _make_vod_watching()
        b = _make_vod_watching()
        assert a._event_lock is not b._event_lock

    def test_recording_events_instances_independent(self):
        a = _make_recording_events()
        b = _make_recording_events()
        assert a._event_lock is not b._event_lock

    def test_channel_watching_lock_a_does_not_block_lock_b(self):
        a = _make_channel_watching()
        b = _make_channel_watching()
        assert a._event_lock is not b._event_lock, "Locks must be distinct objects"
        assert not a._event_lock.locked(), "A's lock starts unlocked"
        assert not b._event_lock.locked(), "B's lock starts unlocked"

    def test_recording_events_lock_a_does_not_block_lock_b(self):
        a = _make_recording_events()
        b = _make_recording_events()
        assert a._event_lock is not b._event_lock, "Locks must be distinct objects"
        assert not a._event_lock.locked(), "A's lock starts unlocked"
        assert not b._event_lock.locked(), "B's lock starts unlocked"
