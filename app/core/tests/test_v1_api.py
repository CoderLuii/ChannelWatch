import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import httpx
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from core.storage import (
    ActivityEvent,
    create_all_tables,
    create_db_engine,
    get_session,
)


def _make_event(
    event_type: str = "watching_channel",
    dvr_id: str = "dvr_aaa11111",
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
        dvr_name=overrides.pop("dvr_name", "DVR A"),
        **overrides,
    )


def _seed(engine, events):
    with get_session(engine) as session:
        for evt in events:
            session.add(evt)
        session.commit()


TWO_DVR_SETTINGS = {
    "dvr_servers": [
        {
            "id": "dvr_aaa11111",
            "host": "192.168.1.10",
            "port": 8089,
            "name": "Living Room",
            "enabled": True,
        },
        {
            "id": "dvr_bbb22222",
            "host": "192.168.1.20",
            "port": 8089,
            "name": "Bedroom",
            "enabled": True,
        },
    ],
    "tz": "America/New_York",
    "api_key": "test-key-v1",
}


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
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(TWO_DVR_SETTINGS))
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


class TestListDvrs:
    def test_returns_all_active_dvrs(self, client):
        resp = client.get("/api/v1/dvrs")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        ids = {d["id"] for d in body}
        assert "dvr_aaa11111" in ids
        assert "dvr_bbb22222" in ids

    def test_response_fields(self, client):
        resp = client.get("/api/v1/dvrs")
        assert resp.status_code == 200
        dvr = next(d for d in resp.json() if d["id"] == "dvr_aaa11111")
        assert dvr["name"] == "Living Room"
        assert dvr["host"] == "192.168.1.10"
        assert dvr["port"] == 8089
        assert "enabled" in dvr

    def test_deleted_dvrs_excluded(self, settings_file, tmp_path):
        data = json.loads(settings_file.read_text())
        data["dvr_servers"][1]["deleted_at"] = "2026-01-01T00:00:00Z"
        settings_file.write_text(json.dumps(data))
        history_file = tmp_path / "activity_history.json"
        history_file.write_text("[]")
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", True),
            patch("ui.backend.main.HISTORY_FILE", history_file),
            patch("ui.backend.main._get_activity_db_engine", return_value=None),
        ):
            from ui.backend.main import app

            tc = TestClient(app, raise_server_exceptions=False)
            resp = tc.get("/api/v1/dvrs")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == "dvr_aaa11111"


class TestDvrConnectionTest:
    def test_manual_connection_test_accepts_private_lan_host(self, client):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {
            "version": "2026.02.09",
            "FriendlyName": "LAN DVR",
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=status_resp)

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "10.10.25.75", "port": 8089},
            )

        assert resp.status_code == 200
        assert resp.json() == {
            "success": True,
            "name": "LAN DVR",
            "version": "2026.02.09",
        }
        mock_client.get.assert_awaited_once_with(
            "http://10.10.25.75:8089/status", timeout=8.0
        )

    def test_manual_connection_test_rejects_unsafe_metadata_host(self, client):
        mock_client = AsyncMock()

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "169.254.169.254", "port": 8089},
            )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_DVR_TEST_TARGET_REJECTED"
        assert detail["message"] == "Test target rejected: host failed safety check"
        mock_client.get.assert_not_called()

    def test_manual_connection_test_rejects_invalid_port(self, client):
        mock_client = AsyncMock()

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "192.168.1.100", "port": 0},
            )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_DVR_TEST_TARGET_REJECTED"
        assert detail["message"] == "Test target rejected: host failed safety check"
        mock_client.get.assert_not_called()

    @pytest.mark.parametrize("host", ["2001:db8::1", "[2001:db8::1]"])
    def test_manual_connection_test_accepts_ipv6_target(self, client, host):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {
            "version": "2026.03.01",
            "friendly_name": "IPv6 DVR",
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=status_resp)

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": host, "port": 8089},
            )

        assert resp.status_code == 200
        assert resp.json() == {
            "success": True,
            "name": "IPv6 DVR",
            "version": "2026.03.01",
        }
        mock_client.get.assert_awaited_once_with(
            "http://[2001:db8::1]:8089/status", timeout=8.0
        )

    def test_manual_connection_test_non_200_status_returns_success_false(self, client):
        status_resp = MagicMock()
        status_resp.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=status_resp)

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "192.168.1.100", "port": 8089},
            )

        assert resp.status_code == 200
        assert resp.json() == {"success": False, "error": "DVR returned HTTP 503"}

    def test_manual_connection_test_client_exception_returns_success_false(
        self, client
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connect failed"))

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "192.168.1.100", "port": 8089},
            )

        assert resp.status_code == 200
        assert resp.json() == {"success": False, "error": "Could not reach DVR server."}

    def test_manual_connection_test_timeout_returns_sanitized_error(self, client):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("token leaked"))

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "192.168.1.100", "port": 8089},
            )

        assert resp.status_code == 200
        assert resp.json() == {"success": False, "error": "DVR request timed out."}

    def test_manual_connection_test_bad_json_returns_sanitized_error(self, client):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.side_effect = ValueError("token leaked")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=status_resp)

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "192.168.1.100", "port": 8089},
            )

        assert resp.status_code == 200
        assert resp.json() == {
            "success": False,
            "error": "DVR returned an invalid status response.",
        }


class TestGetDvrDetail:
    def _mock_dvr_client(self, status_code=200, version="2026.01.01", dvr_data=None):
        dvr_data = dvr_data or {
            "ServerStorage": {"Available": 500 * 1024**3, "Total": 2000 * 1024**3}
        }
        status_resp = MagicMock()
        status_resp.status_code = status_code
        status_resp.json.return_value = {"version": version}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = dvr_data
        lib_resp = MagicMock()
        lib_resp.is_success = False
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[status_resp, storage_resp, lib_resp, lib_resp, lib_resp]
        )
        return mock_client

    def test_known_dvr_returns_200(self, client):
        with patch("ui.backend.main._dvr_http_client", self._mock_dvr_client()):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111")
        assert resp.status_code == 200

    def test_unknown_dvr_returns_404(self, client):
        resp = client.get("/api/v1/dvrs/dvr_xxxxxxxx")
        assert resp.status_code == 404

    def test_response_has_id_and_name(self, client):
        with patch("ui.backend.main._dvr_http_client", self._mock_dvr_client()):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "dvr_aaa11111"
        assert body["name"] == "Living Room"
        assert body["host"] == "192.168.1.10"
        assert body["port"] == 8089

    def test_offline_dvr_connected_false(self, client):
        offline_client = AsyncMock()
        offline_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("ui.backend.main._dvr_http_client", offline_client):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False

    def test_disk_fallback_strings_populate_status_fields(self, client):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"version": "2026.01.01"}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = {"disk": {"free": "20 GB", "total": "1 TB"}}
        lib_resp = MagicMock()
        lib_resp.is_success = False
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[status_resp, storage_resp, lib_resp, lib_resp, lib_resp]
        )

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111")

        assert resp.status_code == 200
        body = resp.json()
        assert body["disk_free_gb"] == 20
        assert body["disk_total_gb"] == 1024
        assert body["disk_usage_percent"] == 98


class TestDvrStreams:
    def _make_dvr_resp(self, activity=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"activity": activity or {}}
        return resp

    def test_unknown_dvr_returns_404(self, client):
        resp = client.get("/api/v1/dvrs/dvr_xxxxxxxx/streams")
        assert resp.status_code == 404

    def test_no_streams_returns_empty(self, client):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=self._make_dvr_resp(activity={}))
        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/streams")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["dvr_id"] == "dvr_aaa11111"
        assert body["dvr_name"] == "Living Room"

    def test_watching_stream_counted(self, client):
        activity = {"s1": "Watching ch5 CBS from Living Room TV (10.0.0.5)"}
        mock_client = AsyncMock()
        ch_resp = MagicMock()
        ch_resp.status_code = 404
        mock_client.get = AsyncMock(
            side_effect=[
                self._make_dvr_resp(activity=activity),
                ch_resp,
            ]
        )
        with (
            patch("ui.backend.main._dvr_http_client", mock_client),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/streams")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1


class TestDvrSystemInfo:
    def _mock_client(self, connected=True):
        status_resp = MagicMock()
        status_resp.status_code = 200 if connected else 500
        status_resp.json.return_value = {"version": "2026.01.01"}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = {
            "ServerStorage": {"Available": 200 * 1024**3, "Total": 1000 * 1024**3}
        }
        lib_resp = MagicMock()
        lib_resp.is_success = True
        lib_resp.json.return_value = [1, 2, 3]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[status_resp, storage_resp, lib_resp, lib_resp, lib_resp]
        )
        return mock_client

    def test_unknown_dvr_returns_404(self, client):
        resp = client.get("/api/v1/dvrs/dvr_xxxxxxxx/system-info")
        assert resp.status_code == 404

    def test_response_shape(self, client):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_client()),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/system-info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dvr_id"] == "dvr_aaa11111"
        assert body["dvr_name"] == "Living Room"
        assert body["host"] == "192.168.1.10"
        assert body["port"] == 8089
        assert "connected" in body
        assert "disk_severity" in body

    def test_disk_totals_computed(self, client):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_client()),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/system-info")
        body = resp.json()
        assert body["disk_total_gb"] is not None
        assert body["disk_free_gb"] is not None
        assert body["disk_usage_gb"] is not None

    def test_disk_fallback_strings_and_cached_library_counts_used(self, client):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"version": "2026.01.01"}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = {"disk": {"free": "20 GB", "total": "1 TB"}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[status_resp, storage_resp])
        library_counts = AsyncMock(return_value=(7, 8, 9))

        with (
            patch("ui.backend.main._dvr_http_client", mock_client),
            patch("ui.backend.main._fetch_dvr_library_counts", library_counts),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/system-info")

        assert resp.status_code == 200
        body = resp.json()
        assert body["disk_free_gb"] == 20
        assert body["disk_total_gb"] == 1024
        assert body["disk_usage_gb"] == 1004
        assert body["disk_usage_percent"] == 98
        assert body["disk_severity"] == "critical"
        assert body["library_shows"] == 7
        assert body["library_movies"] == 8
        assert body["library_episodes"] == 9
        library_counts.assert_awaited_once_with("http://192.168.1.10:8089")


class TestDvrActivityHistory:
    def test_unknown_dvr_returns_404(self, client):
        resp = client.get("/api/v1/dvrs/dvr_xxxxxxxx/activity-history")
        assert resp.status_code == 404

    def test_filters_to_requested_dvr_only(self, client, mem_engine):
        events = [
            _make_event(dvr_id="dvr_aaa11111", title="DVR A Event"),
            _make_event(dvr_id="dvr_bbb22222", title="DVR B Event"),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/v1/dvrs/dvr_aaa11111/activity-history")
        assert resp.status_code == 200
        body = resp.json()
        titles = [i["title"] for i in body["items"]]
        assert "DVR A Event" in titles
        assert "DVR B Event" not in titles

    def test_second_dvr_gets_its_own_events(self, client, mem_engine):
        events = [
            _make_event(dvr_id="dvr_aaa11111", title="DVR A Only"),
            _make_event(dvr_id="dvr_bbb22222", title="DVR B Only"),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/v1/dvrs/dvr_bbb22222/activity-history")
        assert resp.status_code == 200
        body = resp.json()
        titles = [i["title"] for i in body["items"]]
        assert "DVR B Only" in titles
        assert "DVR A Only" not in titles

    def test_pagination_params_honored(self, client, mem_engine):
        events = [_make_event(dvr_id="dvr_aaa11111", title=f"E{i}") for i in range(10)]
        _seed(mem_engine, events)

        resp = client.get("/api/v1/dvrs/dvr_aaa11111/activity-history?offset=3&limit=4")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 4
        assert body["offset"] == 3
        assert body["limit"] == 4
        assert body["total"] == 10

    def test_invalid_sort_returns_400(self, client):
        resp = client.get("/api/v1/dvrs/dvr_aaa11111/activity-history?sort=bogus")
        assert resp.status_code == 400

    def test_type_filter_applied(self, client, mem_engine):
        events = [
            _make_event(dvr_id="dvr_aaa11111", event_type="watching_channel"),
            _make_event(dvr_id="dvr_aaa11111", event_type="disk_alert"),
        ]
        _seed(mem_engine, events)

        resp = client.get("/api/v1/dvrs/dvr_aaa11111/activity-history?type=channel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["type"] == "watching_channel"


class TestDvrUpcomingRecordings:
    def _mock_jobs(self, jobs=None):
        jobs_resp = MagicMock()
        jobs_resp.status_code = 200
        jobs_resp.json.return_value = jobs or []
        channels_resp = MagicMock()
        channels_resp.status_code = 404
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[channels_resp, jobs_resp])
        return mock_client

    def test_unknown_dvr_returns_404(self, client):
        resp = client.get("/api/v1/dvrs/dvr_xxxxxxxx/recordings/upcoming")
        assert resp.status_code == 404

    def test_empty_jobs_returns_empty_list(self, client):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_jobs(jobs=[])),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/recordings/upcoming")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_future_recording_included(self, client):
        future_time = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        jobs = [
            {
                "Time": future_time,
                "Name": "Game of Thrones",
                "Channels": ["5"],
                "ID": "job1",
                "Airing": {},
            }
        ]
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_jobs(jobs=jobs)),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/recordings/upcoming")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["title"] == "Game of Thrones"
        assert body[0]["dvr_id"] == "dvr_aaa11111"

    def test_future_recording_with_string_timestamp_included(self, client):
        future_time = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        jobs = [
            {
                "Time": str(future_time),
                "Name": "String Timestamp Show",
                "Channels": ["5"],
                "ID": "job-string-time",
                "Airing": {},
            }
        ]
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_jobs(jobs=jobs)),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/recordings/upcoming")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["title"] == "String Timestamp Show"
        assert body[0]["start_time"] == future_time

    def test_past_recording_excluded(self, client):
        past_time = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp())
        jobs = [
            {
                "Time": past_time,
                "Name": "Old Show",
                "Channels": ["5"],
                "ID": "job2",
                "Airing": {},
            }
        ]
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_jobs(jobs=jobs)),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/recordings/upcoming")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_v1_uses_alternate_airing_artwork_before_marking_exhausted(self, client):
        future_time = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        jobs = [
            {
                "Time": future_time,
                "Name": "Alternate Art Show",
                "Channels": ["5"],
                "ID": "job-alt-art",
                "Airing": {
                    "Image": "",
                    "thumbnail_url": "https://example.invalid/alt.jpg",
                },
            }
        ]
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_jobs(jobs=jobs)),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/recordings/upcoming")

        assert resp.status_code == 200
        item = resp.json()[0]
        assert item["image"] == "https://example.invalid/alt.jpg"
        assert item["artwork_fallback_exhausted"] is False

    def test_legacy_upcoming_uses_channel_logo_before_marking_exhausted(self, client):
        future_time = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        channels_resp = MagicMock()
        channels_resp.status_code = 200
        channels_resp.json.return_value = [
            {
                "number": "5",
                "name": "Channel 5",
                "logo_url": "https://example.invalid/channel5.png",
            }
        ]
        jobs_resp = MagicMock()
        jobs_resp.status_code = 200
        jobs_resp.json.return_value = [
            {
                "Time": future_time,
                "Name": "Logo Fallback Show",
                "Channels": ["5"],
                "ID": "job-logo-art",
                "Airing": {"Image": ""},
            }
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[channels_resp, jobs_resp])

        with (
            patch("ui.backend.main._dvr_http_client", mock_client),
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[("dvr_aaa11111", "Living Room", "http://dvr.test")],
            ),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/recordings/upcoming")

        assert resp.status_code == 200
        item = resp.json()[0]
        assert item["image"] == "https://example.invalid/channel5.png"
        assert item["artwork_fallback_exhausted"] is False

    def test_legacy_upcoming_skips_malformed_time_and_keeps_valid_jobs(self, client):
        future_time = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        channels_resp = MagicMock()
        channels_resp.status_code = 200
        channels_resp.json.return_value = []
        jobs_resp = MagicMock()
        jobs_resp.status_code = 200
        jobs_resp.json.return_value = [
            {"ID": "bad", "Time": "not-a-number", "Name": "Bad Row"},
            {"ID": "good", "Time": future_time, "Name": "Good Row"},
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[channels_resp, jobs_resp])

        with (
            patch("ui.backend.main._dvr_http_client", mock_client),
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[("dvr_aaa11111", "Living Room", "http://dvr.test")],
            ),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/recordings/upcoming")

        assert resp.status_code == 200
        assert [item["id"] for item in resp.json()] == ["good"]

    def test_legacy_upcoming_tolerates_malformed_duration_and_keeps_later_jobs(
        self, client
    ):
        future_time = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        channels_resp = MagicMock()
        channels_resp.status_code = 200
        channels_resp.json.return_value = []
        jobs_resp = MagicMock()
        jobs_resp.status_code = 200
        jobs_resp.json.return_value = [
            {
                "ID": "bad-duration",
                "Time": future_time,
                "Duration": "not-a-number",
                "Name": "Bad Duration Row",
            },
            {
                "ID": "good",
                "Time": future_time + 3600,
                "Duration": 1800,
                "Name": "Good Row",
            },
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[channels_resp, jobs_resp])

        with (
            patch("ui.backend.main._dvr_http_client", mock_client),
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[("dvr_aaa11111", "Living Room", "http://dvr.test")],
            ),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/recordings/upcoming")

        assert resp.status_code == 200
        body = resp.json()
        assert [item["id"] for item in body] == ["bad-duration", "good"]
        assert body[0]["end_time"] == future_time + 60


class TestDvrHealth:
    def _mock_health_client(self, connected=True):
        status_resp = MagicMock()
        status_resp.status_code = 200 if connected else 500
        status_resp.json.return_value = {"version": "2026.01.01"}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = {
            "ServerStorage": {"Available": 100 * 1024**3, "Total": 500 * 1024**3}
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[status_resp, storage_resp])
        return mock_client

    def test_unknown_dvr_returns_404(self, client):
        resp = client.get("/api/v1/dvrs/dvr_xxxxxxxx/health")
        assert resp.status_code == 404

    def test_connected_dvr_returns_200(self, client):
        with patch("ui.backend.main._dvr_http_client", self._mock_health_client()):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200

    def test_response_fields_present(self, client):
        with patch("ui.backend.main._dvr_http_client", self._mock_health_client()):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dvr_id"] == "dvr_aaa11111"
        assert body["dvr_name"] == "Living Room"
        assert body["host"] == "192.168.1.10"
        assert body["port"] == 8089
        assert "connected" in body
        assert "disk_status" in body
        assert "last_checked" in body

    def test_offline_dvr_shows_disconnected(self, client):
        offline_client = AsyncMock()
        offline_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("ui.backend.main._dvr_http_client", offline_client):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False

    def test_disk_status_normal_when_healthy(self, client):
        with patch("ui.backend.main._dvr_http_client", self._mock_health_client()):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.json()["disk_status"] == "normal"

    def test_disk_status_critical_when_low(self, client):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"version": "2026.01.01"}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = {
            "ServerStorage": {"Available": 5 * 1024**3, "Total": 500 * 1024**3}
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[status_resp, storage_resp])
        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.json()["disk_status"] == "critical"

    def test_disk_fallback_strings_populate_health_fields(self, client):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"version": "2026.01.01"}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = {"disk": {"free": "20 GB", "total": "1 TB"}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[status_resp, storage_resp])

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["disk_free_gb"] == 20
        assert body["disk_total_gb"] == 1024
        assert body["disk_status"] == "critical"

    def test_session_state_and_recent_alert_rate_use_to_thread(self, client):
        calls = []

        async def run_in_thread(func, *args, **kwargs):
            calls.append(func.__name__)
            return func(*args, **kwargs)

        def read_summary(dvr_id):
            assert dvr_id == "dvr_aaa11111"
            return "2026-04-28T01:02:03+00:00", 4

        def recent_alert_rate(dvr_id):
            assert dvr_id == "dvr_aaa11111"
            return 3.0

        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch("ui.backend.main.asyncio.to_thread", run_in_thread),
            patch("ui.backend.main._read_dvr_session_state_summary", read_summary),
            patch("ui.backend.main._get_recent_alert_rate", recent_alert_rate),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_state_size"] == 4
        assert body["last_event_at"] == "2026-04-28T01:02:03+00:00"
        assert body["recent_alert_rate"] == 3.0
        assert "read_summary" in calls
        assert "recent_alert_rate" in calls


class TestDeprecationHeaders:
    def test_system_info_has_deprecation_header(self, client):
        with (
            patch("ui.backend.main._get_dvr_servers", return_value=[]),
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
            patch("ui.backend.main.get_supervisor_proxy", return_value=None),
        ):
            resp = client.get("/api/system-info")
        assert resp.headers.get("x-deprecated-api") == "Use /api/v1/"

    def test_recordings_upcoming_has_deprecation_header(self, client):
        with (
            patch("ui.backend.main._get_dvr_servers", return_value=[]),
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/recordings/upcoming")
        assert resp.headers.get("x-deprecated-api") == "Use /api/v1/"

    def test_streams_details_has_deprecation_header(self, client):
        with (
            patch("ui.backend.main._get_dvr_servers", return_value=[]),
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            resp = client.get("/api/streams/details")
        assert resp.headers.get("x-deprecated-api") == "Use /api/v1/"

    def test_recent_activity_has_deprecation_header(self, client):
        resp = client.get("/api/recent-activity")
        assert resp.headers.get("x-deprecated-api") == "Use /api/v1/"

    def test_activity_history_has_deprecation_header(self, client):
        resp = client.get("/api/activity-history")
        assert resp.headers.get("x-deprecated-api") == "Use /api/v1/"

    def test_v1_routes_have_no_deprecation_header(self, client):
        resp = client.get("/api/v1/dvrs")
        assert "x-deprecated-api" not in resp.headers

    def test_legacy_endpoints_still_return_200(self, client):
        with (
            patch("ui.backend.main._get_dvr_servers", return_value=[]),
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
        ):
            assert client.get("/api/recordings/upcoming").status_code == 200
            assert client.get("/api/streams/details").status_code == 200
        assert client.get("/api/activity-history").status_code == 200
        assert client.get("/api/recent-activity").status_code == 200


class TestSimpleDiskStatus:
    def test_normal(self):
        from ui.backend.main import _simple_disk_status

        assert _simple_disk_status(200.0, 500.0) == "normal"

    def test_warning_by_percent(self):
        from ui.backend.main import _simple_disk_status

        assert _simple_disk_status(45.0, 500.0) == "warning"

    def test_critical_by_gb(self):
        from ui.backend.main import _simple_disk_status

        assert _simple_disk_status(10.0, 500.0) == "critical"

    def test_unknown_when_none(self):
        from ui.backend.main import _simple_disk_status

        assert _simple_disk_status(None, None) == "unknown"
        assert _simple_disk_status(10.0, None) == "unknown"
        assert _simple_disk_status(None, 100.0) == "unknown"

    def test_unknown_when_total_zero(self):
        from ui.backend.main import _simple_disk_status

        assert _simple_disk_status(0.0, 0.0) == "unknown"
