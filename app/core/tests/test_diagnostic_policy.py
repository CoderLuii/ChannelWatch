"""Diagnostic policy is enforced before notification or sample initialization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.helpers.config import CoreSettings
from ui.backend import main


TESTS = [
    "Test Connectivity", "Test API Endpoints", "Test Channel Watching Alert",
    "Test VOD Watching Alert", "Test Disk Space Alert",
    "Test Recording Scheduled Alert", "Test Recording Started Alert",
    "Test Recording Completed Alert", "Test Recording Stopped Alert",
    "Test Recording Cancelled Alert",
]


@pytest.fixture
def diagnostic_api(monkeypatch):
    import core.diagnostics as diagnostics
    import core.helpers.initialize as initialize
    import core.helpers.logging as logging

    monkeypatch.setattr(CoreSettings, "_load_and_override", lambda _self: None)
    settings = CoreSettings(stream_count=False)
    settings.dvr_servers = [{"host": "fixture.invalid", "port": 8089}]
    first = SimpleNamespace(id="dvr_first", name="First DVR", host="fixture.invalid", port=8089, overrides={})
    second = SimpleNamespace(id="dvr_second", name="Second DVR", host="second.invalid", port=8089, overrides={})
    settings.get_dvr_connections = lambda: [first, second]
    monkeypatch.setattr(main, "CORE_APP_AVAILABLE", True)
    monkeypatch.setattr(main, "_get_core_settings_sync", lambda: settings)
    monkeypatch.setattr(main, "_get_dvr_servers", lambda: [
        (first.id, first.name, "http://fixture.invalid:8089"),
        (second.id, second.name, "http://second.invalid:8089"),
    ])
    monkeypatch.setattr(logging, "log_handler", object())
    notifications = MagicMock()
    notifications.has_configured_destinations.return_value = True
    init_notifications = MagicMock(return_value=notifications)
    init_alerts = MagicMock(return_value=object())
    runner = AsyncMock(return_value=True)
    monkeypatch.setattr(initialize, "initialize_notifications", init_notifications)
    monkeypatch.setattr(initialize, "initialize_alerts", init_alerts)
    monkeypatch.setattr(diagnostics, "run_test", runner)
    return settings, first, second, init_notifications, init_alerts, runner


@pytest.mark.asyncio
@pytest.mark.parametrize("preset, expected", [
    ("balanced", {"passed": 7, "skipped": 3}),
    ("everything", {"passed": 10}),
    ("monitor_only", {"passed": 2, "skipped": 8}),
    ("important_only", {"passed": 4, "skipped": 6}),
])
async def test_diagnostic_preset_outcomes(diagnostic_api, preset, expected):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    settings.alert_channel_watching = preset == "everything"
    settings.alert_vod_watching = preset == "everything"
    settings.alert_disk_space = preset != "monitor_only"
    settings.alert_recording_events = preset != "monitor_only"
    settings.rd_alert_scheduled = preset == "everything"
    settings.rd_alert_started = preset in {"balanced", "everything"}
    settings.rd_alert_completed = preset in {"balanced", "everything"}
    settings.rd_alert_cancelled = preset != "monitor_only"
    counts = {}
    for name in TESTS:
        for mock in (init_notifications, init_alerts, runner):
            mock.reset_mock()
        result = await main._run_test_background_async(name)
        status = getattr(result, "status", None)
        assert status in {"passed", "skipped"}, result
        counts[status] = counts.get(status, 0) + 1
        assert result.target_dvr == {"id": first.id, "name": first.name}
        if status == "skipped":
            assert result.success is False
            assert "disabled" in result.reason.lower()
            init_notifications.assert_not_called()
            init_alerts.assert_not_called()
            runner.assert_not_called()
    assert counts == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("name, setting", [
    ("Test Channel Watching Alert", "alert_channel_watching"),
    ("Test VOD Watching Alert", "alert_vod_watching"),
    ("Test Disk Space Alert", "alert_disk_space"),
    ("Test Recording Scheduled Alert", "rd_alert_scheduled"),
    ("Test Recording Started Alert", "rd_alert_started"),
    ("Test Recording Completed Alert", "rd_alert_completed"),
    ("Test Recording Stopped Alert", "rd_alert_completed"),
    ("Test Recording Cancelled Alert", "rd_alert_cancelled"),
])
async def test_policy_uses_first_dvr_override_and_refreshes_next_call(
    diagnostic_api, name, setting
):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    setattr(settings, setting, True)
    first.overrides[setting] = False
    second.overrides[setting] = True
    result = await main._run_test_background_async(name)
    assert getattr(result, "status", None) == "skipped"
    init_notifications.assert_not_called()
    init_alerts.assert_not_called()
    runner.assert_not_called()
    assert getattr(settings, setting) is True
    first.overrides[setting] = True
    setattr(settings, setting, False)
    result = await main._run_test_background_async(name)
    assert result.status == "passed"
    assert getattr(settings, setting) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("name", TESTS[5:] + ["Test Recording Events Alert"])
async def test_recording_master_switch_skips_every_recording_sample(diagnostic_api, name):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    settings.alert_recording_events = False
    result = await main._run_test_background_async(name)
    assert getattr(result, "status", None) == "skipped"
    assert "Recording" in result.reason
    init_notifications.assert_not_called()
    init_alerts.assert_not_called()
    runner.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [TESTS[0], TESTS[1], TESTS[2], TESTS[5], "unknown"])
async def test_real_failure_remains_failed(diagnostic_api, name):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    runner.return_value = False
    result = await main._run_test_background_async(name)
    assert result.success is False
    assert getattr(result, "status", None) == "failed"
    assert result.reason


@pytest.mark.asyncio
async def test_timeout_remains_failed(diagnostic_api):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    runner.side_effect = TimeoutError("fixture delivery timeout")
    result = await main._run_test_background_async(TESTS[2])
    assert getattr(result, "status", None) == "failed"
    assert "timeout" in result.reason.lower()


@pytest.mark.asyncio
async def test_enabled_notification_without_destinations_fails(diagnostic_api):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    init_notifications.return_value.has_configured_destinations.return_value = False
    result = await main._run_test_background_async(TESTS[2])
    assert getattr(result, "status", None) == "failed"
    assert "destination" in result.reason.lower()
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_reported_target_and_connection_use_same_first_enabled_dvr(diagnostic_api):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    settings.dvr_servers[0] = {"host": "disabled.invalid", "port": 8089, "enabled": False}
    first.host = "first-enabled.invalid"
    first.port = 8090
    result = await main._run_test_background_async("Test Connectivity")
    assert result.status == "passed"
    assert result.target_dvr == {"id": first.id, "name": first.name}
    runner.assert_awaited_once_with("connectivity", first.host, first.port, None)


@pytest.mark.asyncio
async def test_no_enabled_core_connection_fails_without_claiming_a_target(diagnostic_api):
    settings, first, second, init_notifications, init_alerts, runner = diagnostic_api
    settings.multi_dvr_v2_enabled = False
    settings.dvr_servers = [
        {"host": "disabled.invalid", "enabled": False},
        {"host": "later.invalid", "enabled": True},
    ]
    settings.get_dvr_connections = lambda: []
    result = await main._run_test_background_async("Test Connectivity")
    assert result.status == "failed"
    assert "enabled DVR" in result.reason
    assert result.target_dvr is None
    runner.assert_not_called()
    init_notifications.assert_not_called()
    init_alerts.assert_not_called()
