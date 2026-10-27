"""Diagnostic samples leave live activity, sessions and persisted state intact."""

import copy
import sqlite3
import asyncio
import time
import threading
import json
import httpx
from unittest.mock import MagicMock, patch

import pytest

from core.diagnostics import run_test
from core.helpers.config import CoreSettings
from core.helpers.dvr_connection import DVRConnection
from core.helpers.initialize import initialize_alerts
from core.notifications.notification import NotificationManager
from core.tests.test_diagnostics_async import _ConfiguredProvider
from core.helpers import activity_recorder


@pytest.fixture
def sample_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path))
    monkeypatch.delenv("CHANNELWATCH_CONFIG_READ_ONLY", raising=False)
    monkeypatch.setattr("core.alerts.common.stream_tracker.CONFIG_PATH", str(tmp_path))
    monkeypatch.setattr(CoreSettings, "_load_and_override", lambda _self: None)
    settings = CoreSettings(stream_count=False, cw_program_name=False)
    monkeypatch.setattr("core.alerts.disk_space.DiskSpaceAlert._get_disk_info", lambda _self: {
        "free": 10 * 1024**3, "total": 100 * 1024**3, "used": 90 * 1024**3,
        "path": "/fixture",
    })
    dvr = DVRConnection("dvr_fixture", "Fixture DVR", "fixture.invalid")
    provider = _ConfiguredProvider()
    notifications = NotificationManager(diagnostic_mode=True)
    notifications.register_provider(provider)
    with patch("core.notifications.notification._load_routing_config", return_value={}):
        manager = initialize_alerts(notifications, settings, dvr=dvr)
        channel = manager.alert_instances["Channel-Watching"]
        channel.channel_provider.channel_cache = {
            "7": {"name": "Seven", "logo_url": "https://fixture.invalid/seven.png"}
        }
        monkeypatch.setattr(channel, "_cache_channels", lambda: None)
        vod = manager.alert_instances["VOD-Watching"]
        vod.vod_provider.metadata_cache = {
            "12345": {"title": "Real local video", "image_url": "real.png"}
        }
        monkeypatch.setattr(vod, "_cache_vod_metadata", lambda: None)
        recording = manager.alert_instances["Recording-Events"]
        monkeypatch.setattr(recording, "_cache_channels", lambda: None)
        monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("diagnostic made an unexpected DVR request")
        ))
        try:
            yield manager, provider, tmp_path
        finally:
            for alert in manager.alert_instances.values():
                if hasattr(alert, "stop_cleanup"):
                    alert.stop_cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "Channel-Watching", "VOD-Watching", "Recording-Scheduled",
    "Recording-Started", "Recording-Completed", "Recording-Stopped",
    "Recording-Cancelled",
    "Disk-Space",
])
async def test_samples_do_not_change_storage_or_real_session_caches(sample_manager, name):
    manager, provider, config = sample_manager
    assert activity_recorder.record_activity(
        "watching_channel", "Existing activity", "Keep this row", dvr_id=manager.dvr.id
    )
    stream_file = config / "stream_count_dvr_fixture.txt"
    stream_file.write_text("4")
    before_files = {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()}
    original = {}
    for key, alert in manager.alert_instances.items():
        for attr in ("active_sessions", "scheduled_recordings", "active_recordings", "pending_recordings"):
            if hasattr(alert, attr):
                original[(key, attr)] = copy.deepcopy(getattr(alert, attr))
        session = getattr(alert, "session_manager", None)
        if session is not None:
            await session.add_session("real-session", content="keep")
            original[(key, "session")] = copy.deepcopy(session.active_sessions)
    channel_cache = copy.deepcopy(manager.alert_instances["Channel-Watching"].channel_provider.channel_cache)
    vod_cache = copy.deepcopy(manager.alert_instances["VOD-Watching"].vod_provider.metadata_cache)
    assert await run_test(name, manager.dvr.host, manager.dvr.port, manager)
    assert provider.deliveries, name
    assert all(title.startswith("[TEST]") for title, _, _ in provider.deliveries)
    assert all(message.startswith("[TEST]") for _, message, _ in provider.deliveries)
    for (key, attr), value in original.items():
        alert = manager.alert_instances[key]
        actual = alert.session_manager.active_sessions if attr == "session" else getattr(alert, attr)
        assert actual == value
    assert manager.alert_instances["Channel-Watching"].channel_provider.channel_cache == channel_cache
    assert manager.alert_instances["VOD-Watching"].vod_provider.metadata_cache == vod_cache
    assert {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()} == before_files
    with sqlite3.connect(config / "channelwatch.db") as connection:
        assert connection.execute("SELECT title FROM activity_event").fetchall() == [("Existing activity",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("name, prefix", [
    ("Channel-Watching", "cw"), ("VOD-Watching", "vod"),
    ("Recording-Scheduled", "rd"),
])
async def test_custom_templates_cannot_hide_sample_label(sample_manager, name, prefix, capsys):
    manager, provider, config = sample_manager
    setattr(manager.settings, f"{prefix}_template_use_default", False)
    setattr(manager.settings, f"{prefix}_template_title", "My custom title")
    setattr(manager.settings, f"{prefix}_template_body", "My custom message")
    assert await run_test(name, manager.dvr.host, manager.dvr.port, manager)
    title, message, kwargs = provider.deliveries[-1]
    assert title == "[TEST] My custom title"
    assert message == "[TEST] My custom message"
    assert "[TEST] Watching" in capsys.readouterr().out or name == "Recording-Scheduled"


@pytest.mark.asyncio
async def test_channel_sample_does_not_pair_abc_with_seven_logo(sample_manager):
    manager, provider, config = sample_manager
    assert await run_test("Channel-Watching", manager.dvr.host, manager.dvr.port, manager)
    title, message, kwargs = provider.deliveries[-1]
    assert "ABC" not in message
    assert "Diagnostic" in message
    assert "seven.png" not in str(kwargs)


@pytest.mark.asyncio
async def test_channel_program_metadata_is_synthetic_and_needs_no_artwork(sample_manager):
    manager, provider, config = sample_manager
    manager.settings.cw_program_name = True
    assert await run_test("Channel-Watching", manager.dvr.host, manager.dvr.port, manager)
    title, message, kwargs = provider.deliveries[-1]
    assert "Diagnostic Program" in message
    assert "seven.png" not in str(kwargs)
    assert not kwargs.get("image_url")


@pytest.mark.asyncio
async def test_vod_sample_does_not_mix_local_file_metadata_or_artwork(sample_manager):
    manager, provider, config = sample_manager
    assert await run_test("VOD-Watching", manager.dvr.host, manager.dvr.port, manager)
    title, message, kwargs = provider.deliveries[-1]
    assert "Diagnostic Video" in message
    assert "Real local video" not in message
    assert "real.png" not in str(kwargs)


@pytest.mark.asyncio
async def test_real_monitoring_still_records_with_delivery_disabled(sample_manager):
    manager, provider, config = sample_manager
    manager.settings.alert_channel_watching = False
    channel = manager.alert_instances["Channel-Watching"]
    assert await manager.process_event("activities.set", {
        "Name": "real-M3U-session", "Value": "Watching ch7 Seven from Real Device (192.0.2.4) - 1080p"
    }) is None
    with sqlite3.connect(config / "channelwatch.db") as connection:
        assert connection.execute("SELECT channel_name FROM activity_event").fetchall() == [("Seven",)]
    assert provider.deliveries == []


def test_initializing_diagnostic_alerts_preserves_live_stream_and_outcome_files(sample_manager):
    manager, provider, config = sample_manager
    stream_file = config / "stream_count_dvr_fixture.txt"
    stream_file.write_text("4")
    outcome_path = manager.alert_instances["Recording-Events"].outcome_tracker.path
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.write_text("existing live outcome bytes")
    before = {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()}
    diagnostic = initialize_alerts(
        manager.notification_manager, manager.settings, dvr=manager.dvr,
        test_mode=True, diagnostic_mode=True,
    )
    assert set(diagnostic.alert_instances) == set(manager.alert_instances)
    assert {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()} == before
    assert diagnostic.alert_instances["Recording-Events"].outcome_tracker._load_blocked is False


def test_diagnostic_initialization_does_not_start_cleanup_daemon_threads(sample_manager):
    manager, provider, config = sample_manager
    diagnostic = None
    try:
        with patch("core.alerts.common.cleanup_mixin.threading.Thread", wraps=threading.Thread) as thread:
            diagnostic = initialize_alerts(
                manager.notification_manager, manager.settings, dvr=manager.dvr,
                test_mode=True, diagnostic_mode=True,
            )
            thread.assert_not_called()
    finally:
        if diagnostic is not None:
            for alert in diagnostic.alert_instances.values():
                if hasattr(alert, "stop_cleanup"):
                    alert.stop_cleanup()


def test_live_initialization_still_starts_cleanup_daemon_threads(sample_manager):
    manager, provider, config = sample_manager
    for key in ("Channel-Watching", "VOD-Watching", "Recording-Events"):
        assert manager.alert_instances[key].cleanup_thread.is_alive()


@pytest.mark.asyncio
async def test_sample_clone_does_not_leave_cleanup_threads_running(sample_manager):
    manager, provider, config = sample_manager
    before = {thread.ident for thread in threading.enumerate() if thread.name.endswith("-cleanup")}
    assert await run_test("VOD-Watching", manager.dvr.host, manager.dvr.port, manager)
    assert {thread.ident for thread in threading.enumerate() if thread.name.endswith("-cleanup")} == before


@pytest.mark.asyncio
async def test_sample_context_does_not_suppress_concurrent_real_history(sample_manager):
    manager, provider, config = sample_manager
    started = asyncio.Event()
    release = asyncio.Event()
    original = manager.notification_manager.send_notification_async

    async def hold_delivery(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    # Hold the real handler at its async send, while another task records a real event.
    with patch("core.alerts.base.BaseAlert.send_alert_async", side_effect=hold_delivery):
        task = asyncio.create_task(run_test("VOD-Watching", manager.dvr.host, manager.dvr.port, manager))
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert activity_recorder.record_activity("system", "Real concurrent event", "Keep")
        finally:
            release.set()
        assert await task
    with sqlite3.connect(config / "channelwatch.db") as connection:
        assert connection.execute("SELECT title FROM activity_event").fetchall() == [("Real concurrent event",)]


@pytest.mark.asyncio
async def test_api_sample_runs_real_handlers_without_persisting_activity(sample_manager, monkeypatch):
    from ui.backend import main
    import core.helpers.initialize as initialize

    manager, provider, config = sample_manager
    manager.settings.dvr_servers = [{"host": manager.dvr.host, "port": manager.dvr.port}]
    manager.settings.get_dvr_connections = lambda: [manager.dvr]
    monkeypatch.setattr(main, "CORE_APP_AVAILABLE", True)
    monkeypatch.setattr(main, "_get_core_settings_sync", lambda: manager.settings)
    monkeypatch.setattr(main, "_get_dvr_servers", lambda: [(manager.dvr.id, manager.dvr.name, manager.dvr.base_url)])
    monkeypatch.setattr("core.helpers.logging.log_handler", MagicMock())
    monkeypatch.setattr(initialize, "initialize_notifications", lambda *_args, **_kwargs: manager.notification_manager)
    assert activity_recorder.record_activity("system", "Existing activity", "Keep")
    stream_file = config / "stream_count_dvr_fixture.txt"
    stream_file.write_text("4")
    before = {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()}
    for name in ("Test Channel Watching Alert", "Test VOD Watching Alert", "Test Recording Events Alert", "Test Disk Space Alert"):
        result = await main.trigger_test_endpoint(name.replace(" ", "_"))
        assert result.status == "passed", result
        assert {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()} == before


@pytest.mark.asyncio
async def test_isolated_sample_preserves_configured_delivery_deadline(sample_manager):
    manager, provider, config = sample_manager
    manager.notification_manager.diagnostic_deadline_seconds = 0.01
    original_send = provider.send_notification

    def slow_delivery(*args, **kwargs):
        time.sleep(0.3)
        return original_send(*args, **kwargs)

    provider.send_notification = slow_delivery
    assert not await run_test("VOD-Watching", manager.dvr.host, manager.dvr.port, manager)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["passed", "failed", "skipped"])
async def test_http_diagnostic_serializes_result_and_preserves_auth_and_storage(
    sample_manager, monkeypatch, status
):
    from ui.backend import main
    from ui.backend import config as backend_config
    import core.helpers.initialize as initialize

    manager, provider, config = sample_manager
    manager.settings.alert_channel_watching = status != "skipped"
    provider.outcome = status != "failed"
    manager.settings.dvr_servers = [{
        "id": manager.dvr.id, "name": manager.dvr.name,
        "host": manager.dvr.host, "port": manager.dvr.port, "enabled": True,
    }]
    manager.settings.get_dvr_connections = lambda: [manager.dvr]
    settings_file = config / "settings.json"
    settings_file.write_text(json.dumps({
        "api_key": "diagnostic-http-fixture-key", "auth_mode": "api_key",
        "security_setup_completed": True,
        "dvr_servers": manager.settings.dvr_servers,
    }))
    monkeypatch.setattr(backend_config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(backend_config, "CONFIG_DIR", config)
    monkeypatch.setattr(main, "CORE_APP_AVAILABLE", True)
    monkeypatch.setattr(main, "CW_DISABLE_AUTH", False)
    monkeypatch.setattr(main, "RBAC_ENABLED", False)
    monkeypatch.setattr(main, "AUTH_MODE_CACHE", None)
    monkeypatch.setattr(main, "API_KEY_CACHE", None)
    monkeypatch.setattr(main, "_auth_settings_signature", None)
    monkeypatch.setattr(main, "_get_core_settings_sync", lambda: manager.settings)
    monkeypatch.setattr("core.helpers.logging.log_handler", MagicMock())
    initialize_notifications = MagicMock(return_value=manager.notification_manager)
    monkeypatch.setattr(initialize, "initialize_notifications", initialize_notifications)
    assert activity_recorder.record_activity("system", "Existing HTTP activity", "Keep")
    stream_file = config / "stream_count_dvr_fixture.txt"
    stream_file.write_text("4")
    before = {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()}
    path = "/api/run_test/Test_Channel_Watching_Alert"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://testserver"
    ) as client:
        unauthorized = await client.post(path)
        assert unauthorized.status_code == 401
        wrong_key = await client.post(path, headers={"X-API-Key": "wrong-fixture-key"})
        assert wrong_key.status_code == 401
        assert provider.deliveries == []
        # Authentication may initialize its settings-read lock before the diagnostic runs.
        before = {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()}
        response = await client.post(path, headers={"X-API-Key": "diagnostic-http-fixture-key"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["test_name"] == "Test Channel Watching Alert"
    assert result["status"] == status
    assert result["success"] is (status == "passed")
    assert result["message"]
    assert result["target_dvr"] == {"id": manager.dvr.id, "name": manager.dvr.name}
    if status == "passed":
        assert result["reason"] is None
    else:
        assert result["reason"]
    if status == "skipped":
        initialize_notifications.assert_not_called()
        assert provider.deliveries == []
    else:
        assert len(provider.deliveries) == 1
        assert provider.deliveries[0][0].startswith("[TEST]")
    assert {p.relative_to(config): p.read_bytes() for p in config.rglob("*") if p.is_file()} == before
