import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from core.storage import ActivityEvent, create_all_tables, create_db_engine, get_session


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _seed(engine, events):
    with get_session(engine) as session:
        for event in events:
            session.add(event)
        session.commit()


@pytest.fixture()
def mem_engine():
    engine = create_db_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    create_all_tables(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def settings_file(tmp_path):
    data = {
        "dvr_servers": [
            {
                "id": "dvr_main",
                "host": "127.0.0.1",
                "port": 8089,
                "name": "Main DVR",
                "enabled": True,
            }
        ],
        "tz": "UTC",
        "api_key": "shared-ui-key",
        "ics_feed_enabled": True,
        "ics_feed_token": "feed-secret-token",
    }
    file_path = tmp_path / "settings.json"
    file_path.write_text(json.dumps(data))
    return file_path


@pytest.fixture()
def disabled_settings_file(tmp_path):
    data = {
        "dvr_servers": [],
        "tz": "UTC",
        "api_key": "shared-ui-key",
        "ics_feed_enabled": False,
        "ics_feed_token": "feed-secret-token",
    }
    file_path = tmp_path / "settings.json"
    file_path.write_text(json.dumps(data))
    return file_path


@contextmanager
def _make_client(settings_file, mem_engine):
    history_file = settings_file.parent / "activity_history.json"
    history_file.write_text("[]")

    async def _fake_dvr_get(url, timeout=5):
        if url.endswith("/api/v1/channels"):
            return _FakeResponse([{"number": "7", "name": "Channel 7"}])
        if url.endswith("/dvr/jobs"):
            start = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
            return _FakeResponse(
                [
                    {
                        "ID": "job-123",
                        "Name": "Morning News",
                        "Time": start,
                        "Duration": 1800,
                        "Channels": ["7"],
                        "Airing": {"Image": "https://example.invalid/news.jpg"},
                    }
                ]
            )
        raise AssertionError(f"Unexpected DVR URL: {url}")

    patches = [
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.API_KEY_CACHE", "shared-ui-key"),
        patch("ui.backend.main.RBAC_ENABLED", False),
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main._get_activity_db_engine", return_value=mem_engine),
        patch("ui.backend.main._activity_db_engine", mem_engine),
        patch("ui.backend.main._STORAGE_AVAILABLE", True),
        patch(
            "ui.backend.main._dvr_http_client.get",
            new=AsyncMock(side_effect=_fake_dvr_get),
        ),
    ]

    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
        patches[8],
        patches[9],
    ):
        from ui.backend.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def test_ics_feed_rejects_missing_or_invalid_token(settings_file, mem_engine):
    with _make_client(settings_file, mem_engine) as client:
        missing = client.get("/api/v1/feeds/calendar.ics")
        invalid = client.get("/api/v1/feeds/calendar.ics?token=wrong-token")

    assert missing.status_code == 401
    assert invalid.status_code == 401
    detail = missing.json()["detail"]
    assert detail["code"] == "ERR_FEED_TOKEN_INVALID"
    assert detail["message"] == "Invalid ICS feed token"


def test_ics_feed_alias_rejects_missing_or_invalid_token(settings_file, mem_engine):
    with _make_client(settings_file, mem_engine) as client:
        missing = client.get("/api/v1/calendar.ics")
        invalid = client.get("/api/v1/calendar.ics?token=wrong-token")

    assert missing.status_code == 401
    assert invalid.status_code == 401
    detail = missing.json()["detail"]
    assert detail["code"] == "ERR_FEED_TOKEN_INVALID"
    assert detail["message"] == "Invalid ICS feed token"


def test_ics_feed_returns_calendar_payload_with_feed_token_only(
    settings_file, mem_engine
):
    event = ActivityEvent(
        id="activity-1",
        dvr_id="dvr_main",
        dvr_name="Main DVR",
        event_type="watching_channel",
        title="Viewer started channel",
        message="Living Room started watching Channel 7",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=30),
        channel_name="Channel 7",
        device_name="Living Room",
    )
    _seed(mem_engine, [event])

    with _make_client(settings_file, mem_engine) as client:
        response = client.get("/api/v1/feeds/calendar.ics?token=feed-secret-token")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    body = response.text
    assert "BEGIN:VCALENDAR" in body
    assert "PRODID:-//ChannelWatch//Calendar Feed//EN" in body
    assert "SUMMARY:Recording: Morning News" in body
    assert "SUMMARY:Activity: Viewer started channel" in body
    assert "CATEGORIES:recording\\,schedule" in body
    assert "CATEGORIES:activity\\,watching_channel" in body
    assert "DVR: Main DVR" in body


def test_ics_feed_alias_returns_calendar_payload(settings_file, mem_engine):
    with _make_client(settings_file, mem_engine) as client:
        response = client.get("/api/v1/calendar.ics?token=feed-secret-token")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in response.text
    assert "SUMMARY:Recording: Morning News" in response.text


def test_ics_feed_returns_404_when_disabled(disabled_settings_file, mem_engine):
    with _make_client(disabled_settings_file, mem_engine) as client:
        response = client.get("/api/v1/feeds/calendar.ics?token=feed-secret-token")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "ERR_FEED_DISABLED"
    assert detail["message"] == "ICS feed is disabled"


def test_ics_feed_alias_returns_404_when_disabled(disabled_settings_file, mem_engine):
    with _make_client(disabled_settings_file, mem_engine) as client:
        response = client.get("/api/v1/calendar.ics?token=feed-secret-token")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "ERR_FEED_DISABLED"
    assert detail["message"] == "ICS feed is disabled"
