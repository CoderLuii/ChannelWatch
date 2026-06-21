import json
import csv
import uuid
from io import StringIO
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from core.storage import (
    ActivityEvent,
    create_all_tables,
    create_db_engine,
    get_session,
)
from sqlmodel import select
from sqlalchemy.sql.selectable import Select


def _make_event(
    event_type: str = "watching_channel",
    dvr_id: str = "dvr_test01",
    title: str = "Test Event",
    **overrides,
) -> ActivityEvent:
    return ActivityEvent(
        id=str(uuid.uuid4()),
        dvr_id=dvr_id,
        event_type=event_type,
        title=title,
        message=overrides.pop("message", ""),
        timestamp=overrides.pop("timestamp", datetime.now(timezone.utc)),
        channel_name=overrides.pop("channel_name", ""),
        device_name=overrides.pop("device_name", ""),
        dvr_name=overrides.pop("dvr_name", ""),
        **overrides,
    )


def _seed(engine, events):
    with get_session(engine) as session:
        for evt in events:
            session.add(evt)
        session.commit()


@pytest.fixture()
def mem_engine():
    # StaticPool ensures asyncio.to_thread workers share the same in-memory connection.
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
                "id": "dvr_test",
                "host": "192.168.1.100",
                "port": 8089,
                "name": "Test DVR",
                "enabled": True,
            }
        ],
        "tz": "America/New_York",
        "api_key": "test-key-12345",
    }
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture()
def client(settings_file, mem_engine, tmp_path):
    history_file = tmp_path / "activity_history.json"
    history_file.write_text("[]")
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main._get_activity_db_engine", return_value=mem_engine),
        patch("ui.backend.main._activity_db_engine", mem_engine),
        patch("ui.backend.main._STORAGE_AVAILABLE", True),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def fallback_client(settings_file):
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main._get_activity_db_engine", return_value=None),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


class TestDbFirstActivityHistory:
    def test_returns_db_rows_not_in_memory_list(self, client, mem_engine):
        evt = _make_event(title="DB Event Alpha")
        _seed(mem_engine, [evt])

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        body = resp.json()
        titles = [i["title"] for i in body["items"]]
        assert "DB Event Alpha" in titles

    def test_response_shape_preserved(self, client, mem_engine):
        evt = _make_event(title="Shape Test", channel_name="ESPN", dvr_id="dvr_shape01")
        _seed(mem_engine, [evt])

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "offset" in body
        assert "limit" in body
        assert body["offset"] == 0
        assert body["limit"] == 50

    def test_item_type_field_maps_from_event_type(self, client, mem_engine):
        evt = _make_event(event_type="recording_event")
        _seed(mem_engine, [evt])

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["type"] == "recording_event"

    def test_pagination_offset_and_limit(self, client, mem_engine):
        events = [_make_event(title=f"Event {i}") for i in range(15)]
        _seed(mem_engine, events)

        resp = client.get("/api/activity-history?offset=5&limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 5
        assert body["offset"] == 5
        assert body["limit"] == 5
        assert body["total"] == 15

    def test_type_filter_channel(self, client, mem_engine):
        events = [
            _make_event(event_type="watching_channel"),
            _make_event(event_type="disk_alert"),
            _make_event(event_type="recording_event"),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/activity-history?type=channel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["type"] == "watching_channel"

    def test_type_filter_recording(self, client, mem_engine):
        events = [
            _make_event(event_type="recording_started"),
            _make_event(event_type="recording_completed"),
            _make_event(event_type="watching_channel"),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/activity-history?type=recording")
        assert resp.status_code == 200
        body = resp.json()
        types = {i["type"] for i in body["items"]}
        assert "watching_channel" not in types
        assert body["total"] == 2

    def test_search_filters_by_title(self, client, mem_engine):
        events = [
            _make_event(title="ESPN Highlights"),
            _make_event(title="HBO Succession"),
            _make_event(title="CNN Breaking"),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/activity-history?search=hbo")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert "HBO" in body["items"][0]["title"]

    def test_search_filters_by_channel_name(self, client, mem_engine):
        events = [
            _make_event(channel_name="Discovery Channel"),
            _make_event(channel_name="Food Network"),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/activity-history?search=discovery")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_sort_desc_default(self, client, mem_engine):
        now = datetime.now(timezone.utc)
        events = [
            _make_event(title="Oldest", timestamp=now - timedelta(hours=2)),
            _make_event(title="Middle", timestamp=now - timedelta(hours=1)),
            _make_event(title="Newest", timestamp=now),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()["items"]]
        assert titles[0] == "Newest"
        assert titles[-1] == "Oldest"

    def test_sort_asc(self, client, mem_engine):
        now = datetime.now(timezone.utc)
        events = [
            _make_event(title="Oldest", timestamp=now - timedelta(hours=2)),
            _make_event(title="Newest", timestamp=now),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/activity-history?sort=asc")
        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()["items"]]
        assert titles[0] == "Oldest"
        assert titles[-1] == "Newest"

    def test_invalid_sort_returns_400(self, client):
        resp = client.get("/api/activity-history?sort=random")
        assert resp.status_code == 400

    def test_csv_export_uses_keyset_order_without_offset(self, client, mem_engine):
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        events = [
            _make_event(title="Third", timestamp=ts),
            _make_event(title="First", timestamp=ts),
            _make_event(title="Second", timestamp=ts),
        ]
        events[0].id = "event-c"
        events[1].id = "event-a"
        events[2].id = "event-b"
        _seed(mem_engine, events)

        def fail_offset(self, *args, **kwargs):
            raise AssertionError("CSV export must not use OFFSET pagination")

        with patch.object(Select, "offset", fail_offset):
            resp = client.get("/api/v1/history/export")

        assert resp.status_code == 200
        rows = list(csv.DictReader(StringIO(resp.text)))
        assert [row["id"] for row in rows] == ["event-a", "event-b", "event-c"]


class TestDbFirstRecentActivity:
    def test_returns_db_rows(self, client, mem_engine):
        evt = _make_event(title="Recent DB Row")
        _seed(mem_engine, [evt])

        resp = client.get("/api/recent-activity")
        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()]
        assert "Recent DB Row" in titles

    def test_hours_filter_excludes_old_events(self, client, mem_engine):
        now = datetime.now(timezone.utc)
        events = [
            _make_event(title="Fresh", timestamp=now - timedelta(hours=1)),
            _make_event(title="Stale", timestamp=now - timedelta(hours=30)),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/recent-activity?hours=24")
        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()]
        assert "Fresh" in titles
        assert "Stale" not in titles

    def test_hours_zero_returns_all(self, client, mem_engine):
        now = datetime.now(timezone.utc)
        events = [
            _make_event(title="Fresh", timestamp=now),
            _make_event(title="Ancient", timestamp=now - timedelta(days=365)),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/recent-activity?hours=0")
        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()]
        assert "Fresh" in titles
        assert "Ancient" in titles

    def test_limit_is_respected(self, client, mem_engine):
        events = [_make_event() for _ in range(20)]
        _seed(mem_engine, events)

        resp = client.get("/api/recent-activity?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) == 5


class TestClearActivityHistoryWithDb:
    def test_clear_removes_db_rows(self, client, mem_engine):
        events = [_make_event() for _ in range(5)]
        _seed(mem_engine, events)

        resp = client.post("/api/clear-activity-history")
        assert resp.status_code == 200

        with get_session(mem_engine) as session:
            remaining = session.exec(select(ActivityEvent)).all()
        assert len(remaining) == 0

    def test_clear_returns_success_message(self, client, mem_engine):
        resp = client.post("/api/clear-activity-history")
        assert resp.status_code == 200
        assert "cleared" in resp.json()["message"].lower()


class TestFallbackBehaviorWhenDbAbsent:
    def test_recent_activity_uses_in_memory_list(self, fallback_client, settings_file):
        from ui.backend.main import AlertHistoryItem

        fake_item = AlertHistoryItem(
            id="fallback-001",
            type="watching_channel",
            title="Fallback Item",
            message="From memory",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with patch("ui.backend.main.ACTIVITY_HISTORY", [fake_item]):
            resp = fallback_client.get("/api/recent-activity")
        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()]
        assert "Fallback Item" in titles

    def test_activity_history_uses_in_memory_list(self, fallback_client, settings_file):
        from ui.backend.main import AlertHistoryItem

        fake_item = AlertHistoryItem(
            id="fallback-002",
            type="disk_alert",
            title="JSON Fallback",
            message="Old path",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with patch("ui.backend.main.ACTIVITY_HISTORY", [fake_item]):
            resp = fallback_client.get("/api/activity-history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert any(i["title"] == "JSON Fallback" for i in body["items"])

    def test_fallback_logs_warning(self, settings_file, tmp_path, caplog):
        import logging

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", True),
            patch("ui.backend.main._ACTIVITY_DB_FILE", tmp_path / "nonexistent.db"),
            patch("ui.backend.main._activity_db_engine", None),
            patch("ui.backend.main._activity_db_warned", False),
            patch("ui.backend.main._STORAGE_AVAILABLE", True),
            caplog.at_level(logging.WARNING, logger="ui.backend.main"),
        ):
            from ui.backend.main import _get_activity_db_engine

            result = _get_activity_db_engine()
        assert result is None
        assert any(
            "fallback" in r.message.lower() or "not found" in r.message.lower()
            for r in caplog.records
        )

    def test_fallback_warning_logged_only_once(self, settings_file, tmp_path):
        warning_count = {"n": 0}

        def counting_warning(msg, *args, **kwargs):
            warning_count["n"] += 1

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main._ACTIVITY_DB_FILE", tmp_path / "nonexistent.db"),
            patch("ui.backend.main._activity_db_engine", None),
            patch("ui.backend.main._activity_db_warned", False),
            patch("ui.backend.main._STORAGE_AVAILABLE", True),
            patch("ui.backend.main.log") as mock_log,
        ):
            mock_log.warning = counting_warning
            from ui.backend.main import _get_activity_db_engine

            _get_activity_db_engine()
            _get_activity_db_engine()
            _get_activity_db_engine()

        assert warning_count["n"] == 1


class TestItemFieldFidelity:
    def test_extra_json_decoded_in_response(self, client, mem_engine):
        payload = {"severity": "warning", "percent_free": 8.5}
        evt = _make_event(extra=json.dumps(payload))
        _seed(mem_engine, [evt])

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["extra"] == payload

    def test_timestamp_is_iso_string(self, client, mem_engine):
        ts = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        evt = _make_event(timestamp=ts)
        _seed(mem_engine, [evt])

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        ts_str = resp.json()["items"][0]["timestamp"]
        assert "2026-03-15" in ts_str

    def test_all_core_fields_present(self, client, mem_engine):
        evt = _make_event(
            event_type="watching_channel",
            channel_name="ESPN",
            channel_number="206",
            device_name="Fire Stick",
            device_ip="10.0.0.5",
            program_title="SportsCenter",
            image_url="https://example.com/img.png",
            stream_source="linear",
            dvr_id="dvr_abc123",
            dvr_name="Living Room",
        )
        _seed(mem_engine, [evt])

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["type"] == "watching_channel"
        assert item["channel_name"] == "ESPN"
        assert item["channel_number"] == "206"
        assert item["device_name"] == "Fire Stick"
        assert item["device_ip"] == "10.0.0.5"
        assert item["program_title"] == "SportsCenter"
        assert item["image_url"] == "https://example.com/img.png"
        assert item["dvr_id"] == "dvr_abc123"
        assert item["dvr_name"] == "Living Room"

    def test_db_backed_history_emits_is_test(self, client, mem_engine):
        evt = _make_event(title="Synthetic disk probe", is_test=True)
        _seed(mem_engine, [evt])

        resp = client.get("/api/activity-history")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["is_test"] is True

    def test_db_backed_recent_activity_emits_is_test(self, client, mem_engine):
        evt = _make_event(title="Recent synthetic probe", is_test=True)
        _seed(mem_engine, [evt])

        resp = client.get("/api/recent-activity")
        assert resp.status_code == 200
        assert resp.json()[0]["is_test"] is True
