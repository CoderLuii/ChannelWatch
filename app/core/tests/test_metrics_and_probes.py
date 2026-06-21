import json
import time
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

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
    "api_key": "test-key-t26",
}


@pytest.fixture()
def settings_file(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(TWO_DVR_SETTINGS))
    return f


@pytest.fixture()
def client(settings_file, tmp_path):
    history_file = tmp_path / "activity_history.json"
    history_file.write_text("[]")
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main._get_activity_db_engine", return_value=None),
        patch("ui.backend.main._activity_db_engine", None),
        patch("ui.backend.main._STORAGE_AVAILABLE", False),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


def _mock_system_info(dvr_status=None):
    from ui.backend.main import SystemInfo, DVRStatus

    return SystemInfo(
        channelwatch_version="0.9.1",
        channels_dvr_host="192.168.1.10",
        channels_dvr_port=8089,
        channels_dvr_server_version=None,
        timezone="America/New_York",
        disk_usage_percent=20.0,
        disk_usage_gb=200.0,
        disk_total_gb=1000.0,
        disk_free_gb=800.0,
        disk_severity="normal",
        log_retention_days=7,
        start_time=None,
        container_start_time="2026-01-01T00:00:00+00:00",
        uptime_data={"days": 0, "hours": 1, "minutes": 0, "seconds": 0},
        core_status="Running",
        library_shows=5,
        library_movies=3,
        library_episodes=10,
        dvr_status=dvr_status
        or [
            DVRStatus(
                id="dvr_aaa11111",
                name="Living Room",
                host="192.168.1.10",
                port=8089,
                connected=True,
                version="2026.01.01",
                version_compatible=True,
                disk_total_gb=500.0,
                disk_free_gb=400.0,
                active_streams=2,
            ),
            DVRStatus(
                id="dvr_bbb22222",
                name="Bedroom",
                host="192.168.1.20",
                port=8089,
                connected=False,
                disk_total_gb=500.0,
                disk_free_gb=400.0,
                active_streams=1,
            ),
        ],
    )


class TestHealthzLive:
    def test_always_returns_200(self, client):
        resp = client.get("/healthz/live")
        assert resp.status_code == 200

    def test_returns_ok_status(self, client):
        resp = client.get("/healthz/live")
        assert resp.json()["status"] == "ok"

    def test_no_auth_required(self, settings_file, tmp_path):
        history_file = tmp_path / "activity_history.json"
        history_file.write_text("[]")
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", "some-key"),
            patch("ui.backend.main.HISTORY_FILE", history_file),
            patch("ui.backend.main._get_activity_db_engine", return_value=None),
        ):
            from ui.backend.main import app

            tc = TestClient(app, raise_server_exceptions=False)
            resp = tc.get("/healthz/live")
        assert resp.status_code == 200


class TestHealthzStartup:
    def test_returns_503_before_startup_complete(self, client):
        with patch("ui.backend.main._STARTUP_COMPLETE", False):
            resp = client.get("/healthz/startup")
        assert resp.status_code == 503

    def test_returns_not_ready_before_startup(self, client):
        with patch("ui.backend.main._STARTUP_COMPLETE", False):
            resp = client.get("/healthz/startup")
        assert resp.json()["status"] == "not_ready"

    def test_returns_200_after_startup_complete(self, client):
        with patch("ui.backend.main._STARTUP_COMPLETE", True):
            resp = client.get("/healthz/startup")
        assert resp.status_code == 200

    def test_returns_ready_after_startup(self, client):
        with patch("ui.backend.main._STARTUP_COMPLETE", True):
            resp = client.get("/healthz/startup")
        assert resp.json()["status"] == "ready"

    def test_no_auth_required(self, settings_file, tmp_path):
        history_file = tmp_path / "activity_history.json"
        history_file.write_text("[]")
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", "some-key"),
            patch("ui.backend.main.HISTORY_FILE", history_file),
            patch("ui.backend.main._get_activity_db_engine", return_value=None),
            patch("ui.backend.main._STARTUP_COMPLETE", True),
        ):
            from ui.backend.main import app

            tc = TestClient(app, raise_server_exceptions=False)
            resp = tc.get("/healthz/startup")
        assert resp.status_code == 200


class TestHealthzReady:
    def _clear_version_status_cache(self):
        from ui.backend import main as ui_main

        with ui_main._DVR_VERSION_STATUS_CACHE_LOCK:
            ui_main._DVR_VERSION_STATUS_CACHE.clear()

    def test_returns_503_when_enabled_dvr_is_stale(self, client):
        stale_summary = {
            "ready": False,
            "dvrs": [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "monitoring_status": "stale",
                    "freshness_status": "stale",
                    "connected": True,
                    "reason": "No freshness update for 601s",
                    "last_freshness_at": "2026-01-01T00:00:00+00:00",
                    "freshness_age_seconds": 601.0,
                }
            ],
            "stale_threshold_seconds": 300,
        }
        with patch(
            "ui.backend.main._get_monitoring_health_summary", return_value=stale_summary
        ):
            resp = client.get("/healthz/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"

    def test_returns_200_when_all_enabled_dvrs_are_healthy(self, client):
        healthy_summary = {
            "ready": True,
            "dvrs": [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "monitoring_status": "healthy",
                    "freshness_status": "healthy",
                    "connected": True,
                    "reason": "Freshness updates are current",
                    "last_freshness_at": "2026-01-01T00:00:00+00:00",
                    "freshness_age_seconds": 12.0,
                }
            ],
            "stale_threshold_seconds": 300,
        }
        with patch(
            "ui.backend.main._get_monitoring_health_summary",
            return_value=healthy_summary,
        ):
            resp = client.get("/healthz/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_api_health_returns_503_when_monitoring_is_dead(self, client):
        dead_summary = {
            "ready": False,
            "dvrs": [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "monitoring_status": "dead",
                    "reason": "Monitor task is not alive",
                }
            ],
        }
        with patch(
            "ui.backend.main._get_monitoring_health_summary", return_value=dead_summary
        ):
            resp = client.get("/api/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"

    def test_ready_dvr_entries_include_null_version_fields_when_unprobed(self, client):
        self._clear_version_status_cache()
        healthy_summary = {
            "ready": True,
            "dvrs": [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "monitoring_status": "healthy",
                    "freshness_status": "healthy",
                    "connected": True,
                    "reason": "Freshness updates are current",
                    "last_freshness_at": "2026-01-01T00:00:00+00:00",
                    "freshness_age_seconds": 12.0,
                }
            ],
            "stale_threshold_seconds": 300,
        }
        try:
            with (
                patch(
                    "ui.backend.main._get_monitoring_health_summary",
                    return_value=healthy_summary,
                ),
                patch(
                    "ui.backend.main._dvr_http_client.get", new_callable=AsyncMock
                ) as mock_get,
            ):
                resp = client.get("/healthz/ready")
            assert resp.status_code == 200
            dvr = resp.json()["dvrs"][0]
            assert dvr["version"] is None
            assert dvr["version_compatible"] is None
            assert dvr["version_warning"] is None
            mock_get.assert_not_called()
        finally:
            self._clear_version_status_cache()

    def test_ready_dvr_entries_include_cached_version_status(self, client):
        from ui.backend import main as ui_main

        self._clear_version_status_cache()
        healthy_summary = {
            "ready": True,
            "dvrs": [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "monitoring_status": "healthy",
                    "freshness_status": "healthy",
                    "connected": True,
                    "reason": "Freshness updates are current",
                    "last_freshness_at": "2026-01-01T00:00:00+00:00",
                    "freshness_age_seconds": 12.0,
                }
            ],
            "stale_threshold_seconds": 300,
        }
        try:
            ui_main._cache_dvr_version_status("dvr_aaa11111", "2025.05.13")
            with (
                patch(
                    "ui.backend.main._get_monitoring_health_summary",
                    return_value=healthy_summary,
                ),
                patch(
                    "ui.backend.main._dvr_http_client.get", new_callable=AsyncMock
                ) as mock_get,
            ):
                resp = client.get("/healthz/ready")
            assert resp.status_code == 200
            dvr = resp.json()["dvrs"][0]
            assert dvr["version"] == "2025.05.13"
            assert dvr["version_compatible"] is True
            assert dvr["version_warning"] is None
            mock_get.assert_not_called()
        finally:
            self._clear_version_status_cache()

    def test_ready_dvr_entries_include_cached_version_warning(self, client):
        from ui.backend import main as ui_main

        self._clear_version_status_cache()
        degraded_summary = {
            "ready": False,
            "dvrs": [
                {
                    "id": "dvr_aaa11111",
                    "name": "Living Room",
                    "monitoring_status": "stale",
                    "freshness_status": "stale",
                    "connected": True,
                    "reason": "No freshness update for 601s",
                    "last_freshness_at": "2026-01-01T00:00:00+00:00",
                    "freshness_age_seconds": 601.0,
                }
            ],
            "stale_threshold_seconds": 300,
        }
        try:
            ui_main._cache_dvr_version_status("dvr_aaa11111", "2023.01.01")
            with (
                patch(
                    "ui.backend.main._get_monitoring_health_summary",
                    return_value=degraded_summary,
                ),
                patch(
                    "ui.backend.main._dvr_http_client.get", new_callable=AsyncMock
                ) as mock_get,
            ):
                resp = client.get("/healthz/ready")
            assert resp.status_code == 503
            dvr = resp.json()["dvrs"][0]
            assert dvr["version"] == "2023.01.01"
            assert dvr["version_compatible"] is False
            assert "below the tested range" in dvr["version_warning"]
            assert "2024.01.01" in dvr["version_warning"]
            mock_get.assert_not_called()
        finally:
            self._clear_version_status_cache()


class TestMetricsPerDvrLabels:
    def _mock_dvr_activity(self, counts):
        responses = []
        for count in counts:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {
                "activity": {f"s{i}": f"stream{i}" for i in range(count)}
            }
            responses.append(r)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=responses)
        return mock_client

    def test_metrics_endpoint_returns_200(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=3),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([2, 1])),
        ):
            resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_requires_api_key_when_auth_enabled(self, client):
        with (
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.RBAC_ENABLED", False),
            patch(
                "ui.backend.main._get_runtime_auth_snapshot",
                new=AsyncMock(return_value=("api_key", "metrics-key", False)),
            ),
        ):
            resp = client.get("/metrics")

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "ERR_AUTH_INVALID_KEY"

    def test_metrics_accepts_api_key_when_auth_enabled(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.RBAC_ENABLED", False),
            patch(
                "ui.backend.main._get_runtime_auth_snapshot",
                new=AsyncMock(return_value=("api_key", "metrics-key", False)),
            ),
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([2, 1])),
        ):
            resp = client.get("/metrics", headers={"X-API-Key": "metrics-key"})

        assert resp.status_code == 200

    def test_metrics_has_dvr_id_label_on_dvr_connected(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=3),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([2, 1])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        assert 'dvr_id="dvr_aaa11111"' in body
        assert 'dvr_id="dvr_bbb22222"' in body

    def test_metrics_has_dvr_name_label_on_dvr_connected(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=3),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([2, 1])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        assert 'dvr_name="Living Room"' in body
        assert 'dvr_name="Bedroom"' in body

    def test_metrics_escapes_special_characters_in_labels(self, client):
        from ui.backend.main import DVRStatus

        si = _mock_system_info(
            dvr_status=[
                DVRStatus(
                    id='dvr_"quoted"',
                    name='Living\\Room\nMain "DVR"',
                    host="192.168.1.10",
                    port=8089,
                    connected=True,
                    disk_total_gb=1.0,
                    disk_free_gb=0.5,
                )
            ]
        )
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([0])),
        ):
            resp = client.get("/metrics")

        body = resp.text
        assert 'dvr_id="dvr_\\"quoted\\""' in body
        assert 'dvr_name="Living\\\\Room\\nMain \\"DVR\\""' in body

    def test_metrics_emits_dvr_version_info_with_compatibility_label(self, client):
        from ui.backend.main import DVRStatus

        si = _mock_system_info(
            dvr_status=[
                DVRStatus(
                    id="dvr_good",
                    name="Compatible",
                    host="192.168.1.10",
                    port=8089,
                    connected=True,
                    version="2026.02.09",
                    version_compatible=True,
                ),
                DVRStatus(
                    id="dvr_old",
                    name="Incompatible",
                    host="192.168.1.20",
                    port=8089,
                    connected=True,
                    version="2023.01.01",
                    version_compatible=False,
                ),
            ]
        )
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([0, 0])),
        ):
            resp = client.get("/metrics")

        body = resp.text
        assert (
            'channelwatch_dvr_version_info{dvr_id="dvr_good",dvr_name="Compatible",version="2026.02.09",compatible="1"} 1'
            in body
        )
        assert (
            'channelwatch_dvr_version_info{dvr_id="dvr_old",dvr_name="Incompatible",version="2023.01.01",compatible="0"} 1'
            in body
        )

    def test_metrics_has_per_dvr_active_streams(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=3),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([2, 1])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        lines = [
            line
            for line in body.splitlines()
            if "channelwatch_active_streams" in line and "dvr_id=" in line
        ]
        assert len(lines) >= 2

    def test_metrics_active_streams_has_dvr_id_label(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=3),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([2, 1])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        labeled = [
            line
            for line in body.splitlines()
            if "channelwatch_active_streams" in line and 'dvr_id="dvr_aaa11111"' in line
        ]
        assert len(labeled) == 1
        assert labeled[0].endswith(" 2")

    def test_metrics_aggregate_active_streams_preserved(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=3),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([2, 1])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        aggregate_line = next(
            (
                line
                for line in body.splitlines()
                if line.startswith("channelwatch_active_streams ")
                and "dvr_id=" not in line
            ),
            None,
        )
        assert aggregate_line is not None
        assert aggregate_line.endswith(" 3")

    def test_metrics_aggregate_active_streams_reuses_system_info_probe_counts(
        self, client
    ):
        si = _mock_system_info()
        mock_client = self._mock_dvr_activity([2, 1])
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch(
                "ui.backend.main.get_active_streams_count",
                side_effect=AssertionError("duplicate aggregate poll not allowed"),
            ),
            patch("ui.backend.main._dvr_http_client", mock_client),
        ):
            resp = client.get("/metrics")

        body = resp.text
        aggregate_line = next(
            (
                line
                for line in body.splitlines()
                if line.startswith("channelwatch_active_streams ")
                and "dvr_id=" not in line
            ),
            None,
        )
        assert aggregate_line is not None
        assert aggregate_line.endswith(" 3")
        mock_client.get.assert_not_awaited()

    def test_metrics_polls_dvr_endpoint_at_most_once_per_dvr(self, client):
        from ui.backend import main as ui_main

        requests = []

        async def fake_get(url, timeout):
            requests.append(url)
            response = MagicMock()
            response.status_code = 200
            if url.endswith("/status"):
                response.json.return_value = {"version": "2026.01.01"}
            elif url.endswith("/dvr"):
                response.json.return_value = {
                    "ServerStorage": {
                        "Available": 400 * ui_main.BYTES_PER_GIB,
                        "Total": 500 * ui_main.BYTES_PER_GIB,
                    },
                    "activity": {"a": {}, "b": {}},
                }
            else:
                response.json.return_value = {}
            return response

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=fake_get)
        healthy_summary = {
            "dvrs": [
                {"id": "dvr_aaa11111", "ready": True},
                {"id": "dvr_bbb22222", "ready": True},
            ]
        }

        with (
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
            patch("ui.backend.main._dvr_http_client", mock_client),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value=healthy_summary,
            ),
            patch(
                "ui.backend.main._fetch_dvr_library_counts",
                new=AsyncMock(
                    side_effect=AssertionError(
                        "metrics must not fetch dashboard library counts"
                    )
                ),
            ),
            patch(
                "ui.backend.main._get_core_process_info_from_supervisor",
                return_value={"statename": "RUNNING", "start": 0},
            ),
        ):
            resp = client.get("/metrics")

        assert resp.status_code == 200
        dvr_requests = [url for url in requests if url.endswith("/dvr")]
        assert len(dvr_requests) == 2
        assert len(set(dvr_requests)) == 2
        assert 'channelwatch_active_streams{dvr_id="dvr_aaa11111"' in resp.text
        assert 'channelwatch_active_streams{dvr_id="dvr_bbb22222"' in resp.text

    @pytest.mark.parametrize(
        ("free_by_dvr", "expected_severity"),
        [
            ([100, 100], "normal"),
            ([40, 40], "warning"),
            ([10, 10], "critical"),
        ],
    )
    def test_legacy_system_info_aggregates_disk_severity_thresholds(
        self, client, free_by_dvr, expected_severity
    ):
        from ui.backend import main as ui_main

        storage_by_host = {
            "192.168.1.10": free_by_dvr[0],
            "192.168.1.20": free_by_dvr[1],
        }

        async def fake_get(url, timeout):
            response = MagicMock()
            response.status_code = 200
            if url.endswith("/status"):
                response.json.return_value = {"version": "2026.01.01"}
            elif url.endswith("/dvr"):
                host = url.split("//", 1)[1].split(":", 1)[0]
                response.json.return_value = {
                    "ServerStorage": {
                        "Available": storage_by_host[host] * ui_main.BYTES_PER_GIB,
                        "Total": 500 * ui_main.BYTES_PER_GIB,
                    },
                    "activity": {},
                }
            return response

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=fake_get)
        healthy_summary = {
            "dvrs": [
                {"id": "dvr_aaa11111", "ready": True},
                {"id": "dvr_bbb22222", "ready": True},
            ]
        }

        with (
            patch("ui.backend.main.CORE_APP_AVAILABLE", False),
            patch("ui.backend.main._dvr_http_client", mock_client),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value=healthy_summary,
            ),
            patch(
                "ui.backend.main._fetch_dvr_library_counts",
                new=AsyncMock(return_value=(0, 0, 0)),
            ),
            patch(
                "ui.backend.main._get_core_process_info_from_supervisor",
                return_value={"statename": "RUNNING", "start": 0},
            ),
        ):
            resp = client.get("/api/system-info")

        assert resp.status_code == 200
        body = resp.json()
        assert body["disk_total_gb"] == 1000.0
        assert body["disk_free_gb"] == sum(free_by_dvr)
        assert body["disk_severity"] == expected_severity

    def test_metrics_has_per_dvr_disk_total(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=0),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([0, 0])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        per_dvr = [
            line
            for line in body.splitlines()
            if "channelwatch_disk_total_bytes" in line and "dvr_id=" in line
        ]
        assert len(per_dvr) >= 2

    def test_metrics_has_per_dvr_disk_used(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=0),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([0, 0])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        per_dvr = [
            line
            for line in body.splitlines()
            if "channelwatch_disk_used_bytes" in line and "dvr_id=" in line
        ]
        assert len(per_dvr) >= 2

    def test_metrics_aggregate_disk_total_preserved(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=0),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([0, 0])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        agg = [
            line
            for line in body.splitlines()
            if "channelwatch_disk_total_bytes" in line and 'scope="all"' in line
        ]
        assert len(agg) == 1

    def test_metrics_aggregate_disk_used_preserved(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=0),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([0, 0])),
        ):
            resp = client.get("/metrics")
        body = resp.text
        agg = [
            line
            for line in body.splitlines()
            if "channelwatch_disk_used_bytes" in line and 'scope="all"' in line
        ]
        assert len(agg) == 1

    def test_metrics_content_type_is_prometheus(self, client):
        si = _mock_system_info()
        with (
            patch("ui.backend.main.get_system_info", return_value=si),
            patch("ui.backend.main.get_active_streams_count", return_value=0),
            patch("ui.backend.main._dvr_http_client", self._mock_dvr_activity([0, 0])),
        ):
            resp = client.get("/metrics")
        assert "text/plain" in resp.headers.get("content-type", "")


class TestActiveRecordingsCount:
    def _jobs_response(self, jobs):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = jobs
        return response

    def test_counts_jobs_with_stop_time_variant(self, client):
        now = int(time.time())
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=self._jobs_response(
                [
                    {"Time": now - 120, "StopTime": now + 120},
                    {"Time": now - 120, "StopTime": now - 1},
                ]
            )
        )

        with (
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[("dvr_aaa11111", "Living Room", "http://dvr.local")],
            ),
            patch("ui.backend.main._dvr_http_client", mock_client),
        ):
            resp = client.get("/api/recordings/active")

        assert resp.status_code == 200
        assert resp.json() == 1

    def test_counts_jobs_with_duration_variants(self, client):
        now = int(time.time())
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=self._jobs_response(
                [
                    {"Time": now - 120, "Duration": 300},
                    {"Time": now - 120, "Airing": {"Duration": 300}},
                    {"Time": now - 120, "duration": 300},
                    {"Time": now - 120, "Duration": 60},
                ]
            )
        )

        with (
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[("dvr_aaa11111", "Living Room", "http://dvr.local")],
            ),
            patch("ui.backend.main._dvr_http_client", mock_client),
        ):
            resp = client.get("/api/recordings/active")

        assert resp.status_code == 200
        assert resp.json() == 3

    def test_polls_multiple_dvrs_with_bounded_fanout(self, client):
        now = int(time.time())
        mock_client = AsyncMock()
        in_flight = 0
        overlapped = False

        async def slow_jobs(url, **kwargs):
            nonlocal in_flight, overlapped
            in_flight += 1
            if in_flight > 1:
                overlapped = True
            await __import__("asyncio").sleep(0.05)
            in_flight -= 1
            return self._jobs_response([{"Time": now - 120, "StopTime": now + 120}])

        mock_client.get = AsyncMock(side_effect=slow_jobs)

        with (
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[
                    ("dvr_aaa11111", "Living Room", "http://dvr-a.local"),
                    ("dvr_bbb22222", "Bedroom", "http://dvr-b.local"),
                ],
            ),
            patch("ui.backend.main._dvr_http_client", mock_client),
        ):
            resp = client.get("/api/recordings/active")

        assert resp.status_code == 200
        assert resp.json() == 2
        assert overlapped is True

    def test_malformed_duration_row_does_not_drop_later_active_jobs(self, client):
        now = int(time.time())
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=self._jobs_response(
                [
                    {"Time": now - 120, "Duration": "not-a-number"},
                    {"Time": now - 120, "Duration": 300},
                ]
            )
        )

        with (
            patch(
                "ui.backend.main._get_dvr_servers_async",
                new_callable=AsyncMock,
                return_value=[("dvr_aaa11111", "Living Room", "http://dvr.local")],
            ),
            patch("ui.backend.main._dvr_http_client", mock_client),
        ):
            resp = client.get("/api/recordings/active")

        assert resp.status_code == 200
        assert resp.json() == 1

    def test_health_routes_offload_monitoring_summary(self, client):
        healthy_summary = {
            "ready": True,
            "dvrs": [],
            "stale_threshold_seconds": 300,
        }
        calls = []

        async def run_in_thread(func, *args, **kwargs):
            calls.append(func)
            return func(*args, **kwargs)

        with (
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value=healthy_summary,
            ) as mock_summary,
            patch("ui.backend.main.asyncio.to_thread", side_effect=run_in_thread),
        ):
            health_resp = client.get("/api/health")
            ready_resp = client.get("/healthz/ready")

        assert health_resp.status_code == 200
        assert ready_resp.status_code == 200
        assert calls == [mock_summary, mock_summary]


class TestDvrHealthEnhancedFields:
    def _mock_health_client(self):
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"version": "2026.01.01"}
        storage_resp = MagicMock()
        storage_resp.status_code = 200
        storage_resp.json.return_value = {
            "ServerStorage": {"Available": 400 * 1024**3, "Total": 500 * 1024**3}
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[status_resp, storage_resp])
        return mock_client

    def test_health_response_includes_last_event_at_field(self, client):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert "last_event_at" in resp.json()

    def test_health_response_includes_session_state_size_field(self, client):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert "session_state_size" in resp.json()

    def test_health_response_includes_recent_alert_rate_field(self, client):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert "recent_alert_rate" in resp.json()

    def test_last_event_at_populated_from_session_state_file(self, client, tmp_path):
        state_file = tmp_path / "session_state_dvr_aaa11111.json"
        state_file.write_text(
            json.dumps(
                {
                    "channel_watching": {"dev1": {"channel": "5"}},
                }
            )
        )
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
            patch("ui.backend.main._CORE_CONFIG_DIR", tmp_path),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert resp.json()["last_event_at"] is not None

    def test_last_event_at_prefers_watchdog_freshness_state(self, client):
        monitor_summary = {
            "dvrs": [
                {
                    "id": "dvr_aaa11111",
                    "last_event_at": "2026-01-02T03:04:05+00:00",
                    "last_freshness_at": "2026-01-02T03:04:10+00:00",
                    "last_freshness_source": "poll",
                    "freshness_age_seconds": 12.0,
                    "freshness_status": "healthy",
                    "monitoring_status": "healthy",
                    "ready": True,
                    "reason": "Freshness updates are current",
                }
            ]
        }
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value=monitor_summary,
            ),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_event_at"] == "2026-01-02T03:04:05+00:00"
        assert body["last_freshness_at"] == "2026-01-02T03:04:10+00:00"
        assert body["monitoring_status"] == "healthy"

    def test_session_state_size_counts_entries(self, client, tmp_path):
        state_file = tmp_path / "session_state_dvr_aaa11111.json"
        state_file.write_text(
            json.dumps(
                {
                    "channel_watching": {"dev1": {}, "dev2": {}},
                    "recording_events": {"job1": {}},
                }
            )
        )
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
            patch("ui.backend.main._CORE_CONFIG_DIR", tmp_path),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert resp.json()["session_state_size"] == 3

    def test_session_state_size_none_when_no_file(self, client, tmp_path):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
            patch("ui.backend.main._CORE_CONFIG_DIR", tmp_path),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert resp.json()["session_state_size"] is None

    def test_recent_alert_rate_none_when_no_db(self, client):
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
            patch("ui.backend.main._get_activity_db_engine", return_value=None),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert resp.json()["recent_alert_rate"] is None

    def test_recent_alert_rate_computed_from_db(self, client, tmp_path, mem_engine):
        from datetime import datetime, timezone, timedelta
        import uuid
        from core.storage import get_session
        from core.storage.models import ActivityEvent

        recent_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
        with get_session(mem_engine) as session:
            for i in range(4):
                session.add(
                    ActivityEvent(
                        id=str(uuid.uuid4()),
                        dvr_id="dvr_aaa11111",
                        event_type="watching_channel",
                        title=f"E{i}",
                        message="",
                        timestamp=recent_ts,
                    )
                )
            session.commit()
        with (
            patch("ui.backend.main._dvr_http_client", self._mock_health_client()),
            patch(
                "ui.backend.main._get_monitoring_health_summary",
                return_value={"dvrs": []},
            ),
            patch("ui.backend.main._get_activity_db_engine", return_value=mem_engine),
            patch("ui.backend.main._STORAGE_AVAILABLE", True),
        ):
            resp = client.get("/api/v1/dvrs/dvr_aaa11111/health")
        assert resp.status_code == 200
        assert resp.json()["recent_alert_rate"] == 4.0


@pytest.fixture()
def mem_engine():
    from sqlalchemy.pool import StaticPool
    from core.storage import create_db_engine, create_all_tables

    engine = create_db_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    create_all_tables(engine)
    yield engine
    engine.dispose()
