"""Alert classes accept and use an AlertManager instance."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _make_settings():
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
    s.ds_threshold_percent = 10
    s.ds_threshold_gb = 50
    s.ds_warning_threshold_percent = 10
    s.ds_warning_threshold_gb = 50
    s.ds_critical_threshold_percent = 5
    s.ds_critical_threshold_gb = 25
    s.ds_alert_cooldown = 3600
    s.ds_startup_grace_seconds = 10
    s.ds_worsening_delta_gb = 1
    s.ds_worsening_delta_percent = 1.0
    s.test_mode = False
    s.ds_template_title = ""
    s.ds_template_body = ""
    s.ds_template_use_default = True
    return s


def _make_dvr(dvr_id="dvr_test01"):
    return SimpleNamespace(
        host="127.0.0.1",
        port=8089,
        id=dvr_id,
        name="TestDVR",
        base_url="http://127.0.0.1:8089",
        overrides={},
    )


def _make_am(notification_manager=None, settings=None, dvr=None):
    history = {}
    return SimpleNamespace(
        notification_manager=notification_manager or MagicMock(),
        settings=settings or _make_settings(),
        dvr=dvr or _make_dvr(),
        _notification_history=history,
    )


class TestChannelWatchingDI:
    def test_constructs_with_alert_manager(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        am = _make_am()
        with (
            patch("core.alerts.channel_watching.ChannelInfoProvider"),
            patch("core.alerts.channel_watching.StreamTracker"),
            patch("core.alerts.channel_watching.ProgramInfoProvider"),
        ):
            alert = ChannelWatchingAlert(am)
        assert alert.alert_manager is am

    def test_notification_manager_bound_from_am(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        nm = MagicMock()
        am = _make_am(notification_manager=nm)
        with (
            patch("core.alerts.channel_watching.ChannelInfoProvider"),
            patch("core.alerts.channel_watching.StreamTracker"),
            patch("core.alerts.channel_watching.ProgramInfoProvider"),
        ):
            alert = ChannelWatchingAlert(am)
        assert alert.notification_manager is nm

    def test_settings_bound_from_am(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        settings = _make_settings()
        am = _make_am(settings=settings)
        with (
            patch("core.alerts.channel_watching.ChannelInfoProvider"),
            patch("core.alerts.channel_watching.StreamTracker"),
            patch("core.alerts.channel_watching.ProgramInfoProvider"),
        ):
            alert = ChannelWatchingAlert(am)
        assert alert.settings is settings

    def test_dvr_bound_from_am(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        dvr = _make_dvr("dvr_cw001")
        am = _make_am(dvr=dvr)
        with (
            patch("core.alerts.channel_watching.ChannelInfoProvider"),
            patch("core.alerts.channel_watching.StreamTracker"),
            patch("core.alerts.channel_watching.ProgramInfoProvider"),
        ):
            alert = ChannelWatchingAlert(am)
        assert alert.dvr is dvr

    def test_notification_history_is_shared_reference(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        am = _make_am()
        with (
            patch("core.alerts.channel_watching.ChannelInfoProvider"),
            patch("core.alerts.channel_watching.StreamTracker"),
            patch("core.alerts.channel_watching.ProgramInfoProvider"),
        ):
            alert = ChannelWatchingAlert(am)
        assert alert._notification_history is am._notification_history

    def test_notification_history_reference_stays_shared_after_reconstruction(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        am = _make_am()
        with (
            patch("core.alerts.channel_watching.ChannelInfoProvider"),
            patch("core.alerts.channel_watching.StreamTracker"),
            patch("core.alerts.channel_watching.ProgramInfoProvider"),
        ):
            alert = ChannelWatchingAlert(am)
        assert alert._notification_history is am._notification_history

    def test_event_lock_is_per_instance(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        am = _make_am()
        with (
            patch("core.alerts.channel_watching.ChannelInfoProvider"),
            patch("core.alerts.channel_watching.StreamTracker"),
            patch("core.alerts.channel_watching.ProgramInfoProvider"),
        ):
            a = ChannelWatchingAlert(am)
            b = ChannelWatchingAlert(am)
        assert a._event_lock is not b._event_lock


class TestVodWatchingDI:
    def test_constructs_with_alert_manager(self):
        from core.alerts.vod_watching import VODWatchingAlert

        am = _make_am()
        with patch("core.alerts.vod_watching.VODInfoProvider"):
            alert = VODWatchingAlert(am)
        assert alert.alert_manager is am

    def test_notification_manager_bound_from_am(self):
        from core.alerts.vod_watching import VODWatchingAlert

        nm = MagicMock()
        am = _make_am(notification_manager=nm)
        with patch("core.alerts.vod_watching.VODInfoProvider"):
            alert = VODWatchingAlert(am)
        assert alert.notification_manager is nm

    def test_dvr_bound_from_am(self):
        from core.alerts.vod_watching import VODWatchingAlert

        dvr = _make_dvr("dvr_vod001")
        am = _make_am(dvr=dvr)
        with patch("core.alerts.vod_watching.VODInfoProvider"):
            alert = VODWatchingAlert(am)
        assert alert.dvr is dvr

    def test_notification_history_is_shared_reference(self):
        from core.alerts.vod_watching import VODWatchingAlert

        am = _make_am()
        with patch("core.alerts.vod_watching.VODInfoProvider"):
            alert = VODWatchingAlert(am)
        assert alert._notification_history is am._notification_history

    def test_timestamped_vod_event_records_activity_and_sends_alert(self):
        from core.alerts.vod_watching import VODWatchingAlert

        notification_manager = MagicMock()
        notification_manager.send_notification_async = AsyncMock(return_value=True)
        provider = MagicMock()
        provider.get_metadata.return_value = {
            "id": "123",
            "Title": "Example Movie",
            "Duration": "3600",
            "Image": "https://example.invalid/movie.jpg",
        }
        provider.format_metadata.return_value = {
            "title": "Example Movie",
            "duration": "1h 00m 00s",
            "progress": "1m 02s",
            "summary": "",
            "image_url": "https://example.invalid/movie.jpg",
        }
        am = _make_am(notification_manager=notification_manager)

        async def run():
            with (
                patch(
                    "core.alerts.vod_watching.VODInfoProvider", return_value=provider
                ),
                patch("core.alerts.vod_watching.record_vod_watching") as record_mock,
            ):
                alert = VODWatchingAlert(am)
                result = await alert.process_event(
                    "activities.set",
                    {
                        "Name": "6-file-123-client-a",
                        "Value": "Watching Example Movie from Living Room at 1m2s",
                    },
                )

            assert result is True
            record_mock.assert_called_once()
            notification_manager.send_notification_async.assert_awaited_once()

        __import__("asyncio").run(run())

    def test_independent_vod_event_is_not_blocked_by_slow_external_work(self):
        import asyncio

        from core.alerts.vod_watching import VODWatchingAlert

        am = _make_am()

        async def run():
            with patch("core.alerts.vod_watching.VODInfoProvider"):
                alert = VODWatchingAlert(am)

            first_started = asyncio.Event()
            second_started = asyncio.Event()
            release_first = asyncio.Event()

            async def slow_process(_event_data, session_key, _file_id, _identifier):
                if session_key == "vod123-client-a":
                    first_started.set()
                    await release_first.wait()
                elif session_key == "vod456-client-b":
                    second_started.set()
                return True, None

            alert._process_watching_event = AsyncMock(side_effect=slow_process)

            first = asyncio.create_task(
                alert.process_event(
                    "activities.set",
                    {
                        "Name": "6-file-123-client-a",
                        "Value": "Watching One from Living Room at 1m2s",
                    },
                )
            )
            await asyncio.wait_for(first_started.wait(), timeout=1)

            second = asyncio.create_task(
                alert.process_event(
                    "activities.set",
                    {
                        "Name": "6-file-456-client-b",
                        "Value": "Watching Two from Bedroom at 1m2s",
                    },
                )
            )
            await asyncio.wait_for(second_started.wait(), timeout=1)
            release_first.set()

            assert await first is True
            assert await second is True

        asyncio.run(run())


class TestChannelWatchingAsyncPaths:
    def test_independent_channel_event_is_not_blocked_by_slow_external_work(self):
        import asyncio

        from core.alerts.channel_watching import ChannelWatchingAlert

        am = _make_am()

        async def run():
            with (
                patch("core.alerts.channel_watching.ChannelInfoProvider"),
                patch("core.alerts.channel_watching.StreamTracker"),
                patch("core.alerts.channel_watching.ProgramInfoProvider"),
            ):
                alert = ChannelWatchingAlert(am)

            first_started = asyncio.Event()
            second_started = asyncio.Event()
            release_first = asyncio.Event()

            async def slow_process(_event_data, tracking_key):
                if tracking_key == "ch5-Living Room":
                    first_started.set()
                    await release_first.wait()
                elif tracking_key == "ch7-Bedroom":
                    second_started.set()
                return True

            alert._process_watching_event = AsyncMock(side_effect=slow_process)

            first = asyncio.create_task(
                alert.process_event(
                    "activities.set",
                    {
                        "Name": "1-channel-5-client-a",
                        "Value": "Watching ch5 from Living Room (192.0.2.10)",
                    },
                )
            )
            await asyncio.wait_for(first_started.wait(), timeout=1)

            second = asyncio.create_task(
                alert.process_event(
                    "activities.set",
                    {
                        "Name": "1-channel-7-client-b",
                        "Value": "Watching ch7 from Bedroom (192.0.2.11)",
                    },
                )
            )
            await asyncio.wait_for(second_started.wait(), timeout=1)
            release_first.set()

            assert await first is True
            assert await second is True

        asyncio.run(run())

    def test_channel_metadata_lookup_is_offloaded(self):
        from core.alerts.channel_watching import ChannelWatchingAlert

        provider = MagicMock()
        provider.get_channel_info.return_value = {
            "name": "Channel Five",
            "logo_url": "https://example.invalid/logo.png",
        }
        calls = []

        async def run_in_thread(func, *args, **kwargs):
            calls.append(func)
            return func(*args, **kwargs)

        async def run():
            am = _make_am()
            with (
                patch(
                    "core.alerts.channel_watching.ChannelInfoProvider",
                    return_value=provider,
                ),
                patch("core.alerts.channel_watching.StreamTracker"),
                patch("core.alerts.channel_watching.ProgramInfoProvider"),
                patch(
                    "core.alerts.channel_watching.asyncio.to_thread",
                    side_effect=run_in_thread,
                ),
            ):
                alert = ChannelWatchingAlert(am)
                alert._send_alert = MagicMock(return_value=True)
                result = await alert.process_event(
                    "activities.set",
                    {
                        "Name": "1-channel-5-client-a",
                        "Value": "Watching ch5 from Living Room (192.0.2.10)",
                    },
                )

            assert result is True
            assert provider.get_channel_info in calls

        __import__("asyncio").run(run())

    def test_notification_history_reference_stays_shared_after_reconstruction(self):
        from core.alerts.vod_watching import VODWatchingAlert

        am = _make_am()
        with patch("core.alerts.vod_watching.VODInfoProvider"):
            alert = VODWatchingAlert(am)
        assert alert._notification_history is am._notification_history


class TestRecordingEventsDI:
    def test_constructs_with_alert_manager(self):
        from core.alerts.recording_events import RecordingEventsAlert

        am = _make_am()
        with (
            patch("core.alerts.recording_events.ChannelInfoProvider"),
            patch("core.alerts.recording_events.JobInfoProvider"),
            patch("core.alerts.recording_events.StreamTracker"),
        ):
            alert = RecordingEventsAlert(am)
        assert alert.alert_manager is am

    def test_notification_manager_bound_from_am(self):
        from core.alerts.recording_events import RecordingEventsAlert

        nm = MagicMock()
        am = _make_am(notification_manager=nm)
        with (
            patch("core.alerts.recording_events.ChannelInfoProvider"),
            patch("core.alerts.recording_events.JobInfoProvider"),
            patch("core.alerts.recording_events.StreamTracker"),
        ):
            alert = RecordingEventsAlert(am)
        assert alert.notification_manager is nm

    def test_dvr_bound_from_am(self):
        from core.alerts.recording_events import RecordingEventsAlert

        dvr = _make_dvr("dvr_re001")
        am = _make_am(dvr=dvr)
        with (
            patch("core.alerts.recording_events.ChannelInfoProvider"),
            patch("core.alerts.recording_events.JobInfoProvider"),
            patch("core.alerts.recording_events.StreamTracker"),
        ):
            alert = RecordingEventsAlert(am)
        assert alert.dvr is dvr

    def test_notification_history_is_shared_reference(self):
        from core.alerts.recording_events import RecordingEventsAlert

        am = _make_am()
        with (
            patch("core.alerts.recording_events.ChannelInfoProvider"),
            patch("core.alerts.recording_events.JobInfoProvider"),
            patch("core.alerts.recording_events.StreamTracker"),
        ):
            alert = RecordingEventsAlert(am)
        assert alert._notification_history is am._notification_history

    def test_notification_history_reference_stays_shared_after_reconstruction(self):
        from core.alerts.recording_events import RecordingEventsAlert

        am = _make_am()
        with (
            patch("core.alerts.recording_events.ChannelInfoProvider"),
            patch("core.alerts.recording_events.JobInfoProvider"),
            patch("core.alerts.recording_events.StreamTracker"),
        ):
            alert = RecordingEventsAlert(am)
        assert alert._notification_history is am._notification_history


class TestDiskSpaceDI:
    def test_constructs_with_alert_manager(self):
        from core.alerts.disk_space import DiskSpaceAlert

        am = _make_am()
        alert = DiskSpaceAlert(am)
        assert alert.alert_manager is am

    def test_notification_manager_bound_from_am(self):
        from core.alerts.disk_space import DiskSpaceAlert

        nm = MagicMock()
        am = _make_am(notification_manager=nm)
        alert = DiskSpaceAlert(am)
        assert alert.notification_manager is nm

    def test_dvr_bound_from_am(self):
        from core.alerts.disk_space import DiskSpaceAlert

        dvr = _make_dvr("dvr_ds001")
        am = _make_am(dvr=dvr)
        alert = DiskSpaceAlert(am)
        assert alert.dvr is dvr

    def test_notification_history_is_shared_reference(self):
        from core.alerts.disk_space import DiskSpaceAlert

        am = _make_am()
        alert = DiskSpaceAlert(am)
        assert alert._notification_history is am._notification_history

    def test_notification_history_reference_stays_shared_after_reconstruction(self):
        from core.alerts.disk_space import DiskSpaceAlert

        am = _make_am()
        alert = DiskSpaceAlert(am)
        assert alert._notification_history is am._notification_history

    def test_two_alerts_share_history_via_same_am(self):
        from core.alerts.disk_space import DiskSpaceAlert

        am = _make_am()
        a = DiskSpaceAlert(am)
        b = DiskSpaceAlert(am)
        assert a._notification_history is b._notification_history

    def test_two_alerts_from_different_ams_have_independent_history(self):
        from core.alerts.disk_space import DiskSpaceAlert

        am_a = _make_am(dvr=_make_dvr("dvr_ds_a"))
        am_b = _make_am(dvr=_make_dvr("dvr_ds_b"))
        a = DiskSpaceAlert(am_a)
        b = DiskSpaceAlert(am_b)
        assert a._notification_history is not b._notification_history


class TestRegisterAlertPassesSelf:
    def test_register_alert_constructs_with_alert_manager(self):
        from core.engine.alert_manager import AlertManager

        dvr = _make_dvr("dvr_reg01")
        nm = MagicMock()
        am = AlertManager(nm, _make_settings(), dvr=dvr)

        captured = []

        class _Spy:
            ALERT_TYPE = "spy"

            def __init__(self, alert_manager):
                captured.append(alert_manager)

        with patch("core.engine.alert_manager.get_alert_class", return_value=_Spy):
            am.register_alert("spy")

        assert len(captured) == 1
        assert captured[0] is am
