import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


SLOW_DELAY_SECONDS = 0.2
REQUESTS_GET_CALLS_PER_SYSTEM_INFO = 7
N_CONCURRENT_REQUESTS = 5

CONCURRENT_WALL_TIME_BUDGET = (
    REQUESTS_GET_CALLS_PER_SYSTEM_INFO * SLOW_DELAY_SECONDS * 3
)
SERIAL_WALL_TIME = (
    N_CONCURRENT_REQUESTS * REQUESTS_GET_CALLS_PER_SYSTEM_INFO * SLOW_DELAY_SECONDS
)

assert CONCURRENT_WALL_TIME_BUDGET < SERIAL_WALL_TIME * 0.75


@pytest.fixture()
def settings_file(tmp_path):
    data = {
        "dvr_servers": [
            {
                "id": "dvr_conc",
                "host": "192.168.1.200",
                "port": 8089,
                "name": "Slow DVR",
                "enabled": True,
            }
        ],
        "tz": "America/New_York",
        "api_key": "",
    }
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(data))
    return f


async def _slow_httpx_get(url, **kwargs):
    await asyncio.sleep(SLOW_DELAY_SECONDS)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    if any(
        p in url
        for p in (
            "/api/v1/shows",
            "/api/v1/movies",
            "/api/v1/episodes",
            "/api/v1/channels",
        )
    ):
        mock_resp.json.return_value = []
    elif "/status" in url:
        mock_resp.json.return_value = {"version": "test-1.0"}
    else:
        mock_resp.json.return_value = {}
    return mock_resp


def _make_supervisor_mock():
    mock = MagicMock()
    mock.supervisor.getProcessInfo.return_value = {
        "statename": "RUNNING",
        "start": int(time.time()) - 60,
    }
    return mock


def _make_dvr_http_client_mock():
    mock_client = AsyncMock()
    mock_client.get.side_effect = _slow_httpx_get
    return mock_client


@pytest.mark.anyio
async def test_concurrent_system_info_requests_do_not_serialize(settings_file):
    mock_dvr_client = _make_dvr_http_client_mock()
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main._dvr_http_client", mock_dvr_client),
        patch(
            "ui.backend.main.get_supervisor_proxy", return_value=_make_supervisor_mock()
        ),
    ):
        from ui.backend.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            start = time.monotonic()
            responses = await asyncio.gather(
                *[client.get("/api/system-info") for _ in range(N_CONCURRENT_REQUESTS)]
            )
            elapsed = time.monotonic() - start

    assert all(r.status_code == 200 for r in responses), (
        f"Expected all 200 responses, got: {[r.status_code for r in responses]}"
    )
    assert elapsed < CONCURRENT_WALL_TIME_BUDGET, (
        f"Elapsed {elapsed:.2f}s >= budget {CONCURRENT_WALL_TIME_BUDGET:.2f}s "
        f"(serial baseline {SERIAL_WALL_TIME:.2f}s) — event loop may still be blocked."
    )
