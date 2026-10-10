from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from ui.backend import main as backend_main


def _schedule(*offsets: int):
    now = datetime.now(timezone.utc)
    return [
        {
            "Time": int((now + timedelta(hours=offset)).timestamp()),
            "Duration": 3600,
        }
        for offset in offsets
    ]


def test_maintenance_windows_are_reported_per_dvr_and_fail_closed():
    servers = [
        ("dvr-a", "Living Room", "http://192.168.1.10:8089"),
        ("dvr-b", "Bedroom", "http://192.168.1.20:8089"),
    ]

    async def fetch_schedule(url: str, *, timeout: float):
        assert timeout == 5
        if "192.168.1.20" in url:
            raise OSError("offline")
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = _schedule(1, 6, 12)
        return response

    with (
        patch.object(backend_main, "CW_DISABLE_AUTH", True),
        patch.object(
            backend_main,
            "_load_settings_async",
            AsyncMock(return_value=SimpleNamespace(tz="UTC")),
        ),
        patch.object(
            backend_main,
            "_get_dvr_servers_async",
            AsyncMock(return_value=servers),
        ),
        patch.object(backend_main, "_safe_dvr_get_url", side_effect=fetch_schedule),
    ):
        client = TestClient(backend_main.app, raise_server_exceptions=False)
        try:
            response = client.get(
                "/api/v1/maintenance-windows?minimum_minutes=60&days=7&start_hour=0&end_hour=24"
            )
        finally:
            client.close()

    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "UTC"
    by_id = {entry["dvr_id"]: entry for entry in body["dvrs"]}
    assert by_id["dvr-a"]["status"] == "truncated"
    assert by_id["dvr-a"]["windows"]
    assert all(
        window["end"] <= by_id["dvr-a"]["coverage_end"]
        for window in by_id["dvr-a"]["windows"]
    )
    assert by_id["dvr-b"]["status"] == "offline"
    assert by_id["dvr-b"]["windows"] == []


def test_maintenance_windows_dvr_filter_and_malformed_schedule():
    servers = [
        ("dvr-a", "Living Room", "http://192.168.1.10:8089"),
        ("dvr-b", "Bedroom", "http://192.168.1.20:8089"),
    ]
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = [{"Time": "unknown", "Duration": 3600}]
    fetch_schedule = AsyncMock(return_value=response)

    with (
        patch.object(backend_main, "CW_DISABLE_AUTH", True),
        patch.object(
            backend_main,
            "_load_settings_async",
            AsyncMock(return_value=SimpleNamespace(tz="UTC")),
        ),
        patch.object(
            backend_main,
            "_get_dvr_servers_async",
            AsyncMock(return_value=servers),
        ),
        patch.object(backend_main, "_safe_dvr_get_url", fetch_schedule),
    ):
        client = TestClient(backend_main.app, raise_server_exceptions=False)
        try:
            result = client.get("/api/v1/maintenance-windows?dvr_id=dvr-b")
        finally:
            client.close()

    assert result.status_code == 200
    assert [entry["dvr_id"] for entry in result.json()["dvrs"]] == ["dvr-b"]
    assert result.json()["dvrs"][0]["status"] == "unknown"
    assert result.json()["dvrs"][0]["windows"] == []
    fetch_schedule.assert_awaited_once_with(
        "http://192.168.1.20:8089/dvr/jobs", timeout=5
    )


def test_unexpected_maintenance_calculation_failure_is_logged_and_fails_closed(caplog):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = _schedule(1, 6)

    with (
        patch.object(backend_main, "CW_DISABLE_AUTH", True),
        patch.object(
            backend_main,
            "_load_settings_async",
            AsyncMock(return_value=SimpleNamespace(tz="UTC")),
        ),
        patch.object(
            backend_main,
            "_get_dvr_servers_async",
            AsyncMock(return_value=[("dvr-a", "Living Room", "http://192.168.1.10:8089")]),
        ),
        patch.object(
            backend_main,
            "_safe_dvr_get_url",
            AsyncMock(return_value=response),
        ),
        patch.object(
            backend_main,
            "calculate_maintenance_windows",
            side_effect=RuntimeError("calculation defect"),
        ),
        caplog.at_level(logging.ERROR, logger="ui.backend.main"),
    ):
        client = TestClient(backend_main.app, raise_server_exceptions=False)
        try:
            result = client.get("/api/v1/maintenance-windows")
        finally:
            client.close()

    assert result.status_code == 200
    assert result.json()["dvrs"][0]["status"] == "unknown"
    assert result.json()["dvrs"][0]["windows"] == []
    assert any(
        "Unexpected maintenance-window calculation failure for DVR dvr-a"
        in record.getMessage()
        for record in caplog.records
    )
