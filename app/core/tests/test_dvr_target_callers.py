"""Regression coverage for DVR-only DNS validation and pinned callers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import requests

from core.helpers.dvr_target import SafeDvrRequest


def _safe_request(path: str = "/status") -> SafeDvrRequest:
    return SafeDvrRequest(
        url=f"http://192.168.1.20:8089{path}",
        host_header="dvr.lan:8089",
        connect_address="192.168.1.20",
    )


def test_connectivity_request_uses_validated_numeric_url_and_original_host(
    monkeypatch,
):
    from core.diagnostics.connectivity import server

    response = SimpleNamespace(status_code=200)
    get = MagicMock(return_value=response)
    monkeypatch.setattr(server, "build_safe_dvr_request", lambda *_args: _safe_request())
    monkeypatch.setattr(server._DVR_HTTP_SESSION, "get", get)

    assert server._DVR_HTTP_SESSION.trust_env is False

    assert (
        server._safe_get(
            "dvr.lan",
            8089,
            "/status",
            headers={"Accept": "application/json"},
            timeout=5,
            allow_redirects=True,
        )
        is response
    )
    get.assert_called_once_with(
        "http://192.168.1.20:8089/status",
        headers={"Accept": "application/json", "Host": "dvr.lan:8089"},
        timeout=5,
        allow_redirects=False,
    )


def test_connectivity_request_rejects_target_without_pinned_result(monkeypatch):
    from core.diagnostics.connectivity import server

    monkeypatch.setattr(server, "build_safe_dvr_request", lambda *_args: None)

    with pytest.raises(requests.ConnectionError, match="safety validation"):
        server._safe_get("blocked.local", 8089, "/status", timeout=5)


def test_server_info_uses_validated_numeric_url_and_original_host(monkeypatch):
    from core.diagnostics import output

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"version": "fixture"},
    )
    get = MagicMock(return_value=response)
    monkeypatch.setattr(output, "build_safe_dvr_request", lambda *_args: _safe_request())
    monkeypatch.setattr(output._DVR_HTTP_SESSION, "get", get)

    assert output._DVR_HTTP_SESSION.trust_env is False

    assert output.get_server_info("dvr.lan", 8089) == {"version": "fixture"}
    get.assert_called_once_with(
        "http://192.168.1.20:8089/status",
        headers={"Host": "dvr.lan:8089"},
        timeout=5,
        allow_redirects=False,
    )


def test_server_info_rejects_target_without_pinned_result(monkeypatch):
    from core.diagnostics import output

    monkeypatch.setattr(output, "build_safe_dvr_request", lambda *_args: None)

    assert output.get_server_info("blocked.local", 8089) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_name", "callable_name", "expected_args"),
    [
        ("connectivity", "test_connectivity", ("dvr.lan", 8089)),
        ("api", "test_api_endpoints", ("dvr.lan", 8089)),
        ("event_stream", "test_event_stream", ("dvr.lan", 8089, 7)),
    ],
)
async def test_diagnostic_dispatch_offloads_synchronous_dvr_checks(
    monkeypatch, test_name, callable_name, expected_args
):
    import core.diagnostics as diagnostics

    diagnostic = MagicMock(return_value=True)
    monkeypatch.setattr(diagnostics, callable_name, diagnostic)

    assert await diagnostics.run_test(test_name, "dvr.lan", 8089, duration=7)
    diagnostic.assert_called_once_with(*expected_args)


@pytest.mark.asyncio
async def test_diagnostic_dispatch_offloads_legacy_sync_alert(monkeypatch):
    import core.diagnostics as diagnostics

    diagnostic = MagicMock(return_value=True)
    monkeypatch.setitem(diagnostics.ALERT_TESTS, "Sync-Fixture", diagnostic)
    manager = object()

    assert await diagnostics.run_test(
        "Sync-Fixture", "dvr.lan", 8089, manager
    )
    diagnostic.assert_called_once_with("dvr.lan", 8089, manager)


def test_channel_provider_rejects_unvalidated_target(monkeypatch):
    from core.helpers import channel_info

    provider = channel_info.ChannelInfoProvider("blocked.local", 8089)
    monkeypatch.setattr(channel_info, "build_safe_dvr_request", lambda *_args, **_kwargs: None)

    with pytest.raises(httpx.ConnectError, match="safety validation"):
        provider.cache_channels()


def test_job_provider_rejects_unvalidated_target(monkeypatch):
    from core.helpers import job_info

    provider = job_info.JobInfoProvider("blocked.local", 8089)
    monkeypatch.setattr(job_info, "build_safe_dvr_request", lambda *_args, **_kwargs: None)

    with pytest.raises(httpx.ConnectError, match="safety validation"):
        provider._get("/api/v1/jobs", timeout=5)


def test_program_provider_rejects_unvalidated_target(monkeypatch):
    from core.helpers import program_info

    provider = program_info.ProgramInfoProvider("blocked.local", 8089)
    monkeypatch.setattr(program_info, "build_safe_dvr_request", lambda *_args, **_kwargs: None)

    assert provider._fetch_xmltv_data() is None


def test_vod_provider_rejects_unvalidated_target(monkeypatch):
    from core.helpers import vod_info

    provider = vod_info.VODInfoProvider("blocked.local", 8089)
    monkeypatch.setattr(vod_info, "build_safe_dvr_request", lambda *_args, **_kwargs: None)

    assert provider._fetch_metadata() == []


def test_stream_tracker_rejects_unvalidated_target(monkeypatch):
    from core.alerts.common import stream_tracker

    tracker = stream_tracker.StreamTracker.__new__(stream_tracker.StreamTracker)
    tracker.host = "blocked.local"
    tracker.port = 8089
    tracker._allow_test_loopback = False
    monkeypatch.setattr(
        stream_tracker, "build_safe_dvr_request", lambda *_args, **_kwargs: None
    )

    assert tracker.update_from_status() is None


def test_disk_alert_rejects_unvalidated_target(monkeypatch):
    from core.alerts import disk_space

    alert = disk_space.DiskSpaceAlert.__new__(disk_space.DiskSpaceAlert)
    alert.host = "blocked.local"
    alert.port = 8089
    alert._allow_test_loopback = False
    monkeypatch.setattr(
        disk_space, "build_safe_dvr_request", lambda *_args, **_kwargs: None
    )

    assert alert._get_disk_info() is None


def test_doctor_rejects_unvalidated_target(monkeypatch):
    from core.cli import doctor

    monkeypatch.setattr(doctor, "build_safe_dvr_request", lambda *_args: None)

    with pytest.raises(httpx.ConnectError, match="safety validation"):
        doctor._safe_get(
            {"host": "blocked.local", "port": 8089},
            "/status",
            timeout=5,
        )


@pytest.mark.asyncio
async def test_ui_dvr_get_pins_query_and_host_header(monkeypatch):
    from ui.backend import main

    assert main._dvr_http_client._trust_env is False
    build_request = MagicMock(return_value=_safe_request("/status?verbose=1"))
    response = SimpleNamespace(status_code=200)
    get = AsyncMock(return_value=response)
    monkeypatch.setattr(main, "build_safe_dvr_request", build_request)
    monkeypatch.setattr(main._dvr_http_client, "get", get)

    assert (
        await main._safe_dvr_get_url(
            "http://dvr.lan:8089/status?verbose=1", timeout=3
        )
        is response
    )
    build_request.assert_called_once_with("dvr.lan", 8089, "/status?verbose=1")
    get.assert_awaited_once_with(
        "http://192.168.1.20:8089/status?verbose=1",
        headers={"Host": "dvr.lan:8089"},
        timeout=3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://dvr.lan:8089/status",
        "http://user:password@dvr.lan:8089/status",
        "http://dvr.lan:not-a-port/status",
    ],
)
async def test_ui_dvr_get_rejects_malformed_connection_urls(url):
    from ui.backend import main

    with pytest.raises(httpx.ConnectError):
        await main._safe_dvr_get_url(url, timeout=3)


@pytest.mark.asyncio
async def test_ui_dvr_get_rejects_target_without_pinned_result(monkeypatch):
    from ui.backend import main

    monkeypatch.setattr(main, "build_safe_dvr_request", lambda *_args: None)

    with pytest.raises(httpx.ConnectError, match="safety validation"):
        await main._safe_dvr_get_url("http://blocked.local:8089/status", timeout=3)


@pytest.mark.asyncio
async def test_stream_count_uses_safe_dvr_request_wrapper(monkeypatch):
    from ui.backend import main

    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"activity": {"one": {}, "two": {}}},
    )
    monkeypatch.setattr(
        main,
        "_get_dvr_servers_async",
        AsyncMock(return_value=[("dvr-fixture", "Fixture", "http://dvr.lan:8089")]),
    )
    get = AsyncMock(return_value=response)
    monkeypatch.setattr(main, "_safe_dvr_get_url", get)

    assert await main._get_per_dvr_active_stream_counts() == {"dvr-fixture": 2}
    get.assert_awaited_once_with("http://dvr.lan:8089/dvr", timeout=3)


def _diagnostic_settings():
    return SimpleNamespace(
        log_retention_days=7,
        dvr_servers=[{"host": "dvr.lan", "port": 8089}],
        get_dvr_connections=lambda: [],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("awaitable_result", [True, False])
async def test_ui_connectivity_diagnostic_accepts_async_and_sync_results(
    monkeypatch, awaitable_result
):
    import core.diagnostics as diagnostics
    import core.helpers.logging as core_logging
    from ui.backend import main

    monkeypatch.setattr(main, "CORE_APP_AVAILABLE", True)
    monkeypatch.setattr(main, "_get_core_settings_sync", _diagnostic_settings)
    monkeypatch.setattr(
        main,
        "_get_dvr_servers",
        lambda: [("dvr-fixture", "Fixture", "http://dvr.lan:8089")],
    )
    monkeypatch.setattr(core_logging, "log_handler", object())
    if awaitable_result:
        diagnostic = AsyncMock(return_value=True)
    else:
        diagnostic = MagicMock(return_value=True)
    monkeypatch.setattr(diagnostics, "run_test", diagnostic)

    result = await main._run_test_background_async("Test Connectivity")

    assert result.success is True
    diagnostic.assert_called_once_with("connectivity", "dvr.lan", 8089, None)


@pytest.mark.asyncio
async def test_ui_diagnostic_endpoint_awaits_runner(monkeypatch):
    from ui.backend import main

    expected = main.TestResult(
        test_name="Test Connectivity",
        success=True,
        message="fixture",
    )
    runner = AsyncMock(return_value=expected)
    monkeypatch.setattr(main, "CORE_APP_AVAILABLE", True)
    monkeypatch.setattr(main, "_run_test_background_async", runner)

    result = await main.trigger_test_endpoint("Test_Connectivity")

    assert result is expected
    runner.assert_awaited_once_with("Test Connectivity")
