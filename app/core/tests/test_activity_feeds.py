import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from core.storage import ActivityEvent, create_all_tables, create_db_engine, get_session


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
        "dvr_servers": [],
        "tz": "UTC",
        "api_key": "shared-ui-key",
        "rss_feed_enabled": True,
        "rss_feed_token": "activity-feed-token",
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
        "rss_feed_enabled": False,
        "rss_feed_token": "activity-feed-token",
    }
    file_path = tmp_path / "settings.json"
    file_path.write_text(json.dumps(data))
    return file_path


@contextmanager
def _make_client(settings_file, mem_engine):
    history_file = settings_file.parent / "activity_history.json"
    history_file.write_text("[]")

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
    ):
        from ui.backend.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


@pytest.mark.parametrize(
    ("endpoint", "code", "message"),
    [
        (
            "/api/v1/feeds/activity.rss",
            "ERR_FEED_TOKEN_INVALID",
            "Invalid RSS feed token",
        ),
        (
            "/api/v1/feeds/activity.atom",
            "ERR_FEED_TOKEN_INVALID",
            "Invalid Atom feed token",
        ),
        ("/api/v1/feed.rss", "ERR_FEED_TOKEN_INVALID", "Invalid RSS feed token"),
        ("/api/v1/feed.atom", "ERR_FEED_TOKEN_INVALID", "Invalid Atom feed token"),
    ],
)
def test_activity_feeds_reject_missing_or_invalid_token(
    settings_file, mem_engine, endpoint, code, message
):
    with _make_client(settings_file, mem_engine) as client:
        missing = client.get(endpoint)
        invalid = client.get(f"{endpoint}?token=wrong-token")

    assert missing.status_code == 401
    assert invalid.status_code == 401
    detail = missing.json()["detail"]
    assert detail["code"] == code
    assert detail["message"] == message


def test_activity_rss_feed_returns_machine_readable_recent_activity(
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
        response = client.get("/api/v1/feeds/activity.rss?token=activity-feed-token")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/rss+xml")

    root = ET.fromstring(response.text)
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "ChannelWatch Recent Activity"
    item = channel.find("item")
    assert item is not None
    assert item.findtext("title") == "Activity: Viewer started channel"
    assert "Living Room started watching Channel 7" in (
        item.findtext("description") or ""
    )
    assert item.findtext("category") == "watching_channel"


def test_activity_atom_feed_returns_machine_readable_recent_activity(
    settings_file, mem_engine
):
    event = ActivityEvent(
        id="activity-2",
        dvr_id="dvr_main",
        dvr_name="Main DVR",
        event_type="disk_alert",
        title="Disk warning",
        message="Disk free space is below threshold",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    _seed(mem_engine, [event])

    with _make_client(settings_file, mem_engine) as client:
        response = client.get("/api/v1/feeds/activity.atom?token=activity-feed-token")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/atom+xml")

    root = ET.fromstring(response.text)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"
    assert (
        root.findtext("atom:title", namespaces=namespace)
        == "ChannelWatch Recent Activity"
    )
    entry = root.find("atom:entry", namespace)
    assert entry is not None
    assert (
        entry.findtext("atom:title", namespaces=namespace) == "Activity: Disk warning"
    )
    assert "Disk free space is below threshold" in (
        entry.findtext("atom:summary", namespaces=namespace) or ""
    )
    category = entry.find("atom:category", namespace)
    assert category is not None
    assert category.attrib["term"] == "disk_alert"


@pytest.mark.parametrize(
    ("endpoint", "content_type"),
    [
        ("/api/v1/feed.rss", "application/rss+xml"),
        ("/api/v1/feed.atom", "application/atom+xml"),
    ],
)
def test_activity_feed_aliases_return_machine_readable_activity(
    settings_file, mem_engine, endpoint, content_type
):
    event = ActivityEvent(
        id="activity-alias",
        dvr_id="dvr_main",
        dvr_name="Main DVR",
        event_type="watching_channel",
        title="Alias activity",
        message="Alias route activity",
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    _seed(mem_engine, [event])

    with _make_client(settings_file, mem_engine) as client:
        response = client.get(f"{endpoint}?token=activity-feed-token")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert "Alias activity" in response.text


@pytest.mark.parametrize(
    ("endpoint", "code", "message"),
    [
        (
            "/api/v1/feeds/activity.rss?token=activity-feed-token",
            "ERR_FEED_DISABLED",
            "RSS feed is disabled",
        ),
        (
            "/api/v1/feeds/activity.atom?token=activity-feed-token",
            "ERR_FEED_DISABLED",
            "Atom feed is disabled",
        ),
        (
            "/api/v1/feed.rss?token=activity-feed-token",
            "ERR_FEED_DISABLED",
            "RSS feed is disabled",
        ),
        (
            "/api/v1/feed.atom?token=activity-feed-token",
            "ERR_FEED_DISABLED",
            "Atom feed is disabled",
        ),
    ],
)
def test_activity_feeds_return_404_when_disabled(
    disabled_settings_file, mem_engine, endpoint, code, message
):
    with _make_client(disabled_settings_file, mem_engine) as client:
        response = client.get(endpoint)

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"] == message
