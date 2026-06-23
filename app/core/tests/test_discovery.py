import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient


def _make_mock_info(
    host="192.168.1.10", port=8089, name_prop="Living Room", server="dvr.local."
):
    info = MagicMock()
    info.parsed_addresses.return_value = [host]
    info.port = port
    info.server = server
    info.properties = {b"name": name_prop.encode()}
    return info


class _ImmediateBrowser:
    def __init__(self, service_name: str, host: str, port: int, name_prop: str):
        self._service_name = service_name
        self._host = host
        self._port = port
        self._name_prop = name_prop

    def __call__(self, zc, service_type, listener):
        info = _make_mock_info(self._host, self._port, self._name_prop)
        zc.get_service_info.return_value = info
        listener.add_service(zc, service_type, self._service_name)
        return MagicMock()


class _SilentBrowser:
    def __call__(self, zc, service_type, listener):
        return MagicMock()


class TestScanForDvrs:
    def test_host_mode_returns_discovered_dvr(self):
        from core.helpers.discovery import scan_for_dvrs

        fake_zc = MagicMock()
        browser_factory = _ImmediateBrowser(
            "Living Room._channels_dvr._tcp.local.",
            "192.168.1.10",
            8089,
            "Living Room",
        )

        with (
            patch("core.helpers.discovery.Zeroconf", return_value=fake_zc),
            patch("core.helpers.discovery.ServiceBrowser", browser_factory),
            patch("core.helpers.discovery.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            results = scan_for_dvrs(timeout=0.0)

        assert len(results) == 1
        assert results[0]["host"] == "192.168.1.10"
        assert results[0]["port"] == 8089
        assert results[0]["display_name_suggestion"] == "Living Room"

    def test_bridge_mode_returns_empty_list(self):
        from core.helpers.discovery import scan_for_dvrs

        fake_zc = MagicMock()

        with (
            patch("core.helpers.discovery.Zeroconf", return_value=fake_zc),
            patch("core.helpers.discovery.ServiceBrowser", _SilentBrowser()),
            patch("core.helpers.discovery.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            results = scan_for_dvrs(timeout=0.0)

        assert results == []

    def test_zeroconf_unavailable_returns_empty(self):
        from core.helpers import discovery as disc_module

        with patch.object(disc_module, "_ZEROCONF_AVAILABLE", False):
            results = disc_module.scan_for_dvrs(timeout=0.0)

        assert results == []

    def test_service_info_none_is_skipped(self):
        from core.helpers.discovery import scan_for_dvrs

        fake_zc = MagicMock()
        fake_zc.get_service_info.return_value = None

        class BrowserThatAddsNullService:
            def __call__(self, zc, service_type, listener):
                listener.add_service(
                    zc, service_type, "BadDVR._channels_dvr._tcp.local."
                )
                return MagicMock()

        with (
            patch("core.helpers.discovery.Zeroconf", return_value=fake_zc),
            patch(
                "core.helpers.discovery.ServiceBrowser", BrowserThatAddsNullService()
            ),
            patch("core.helpers.discovery.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            results = scan_for_dvrs(timeout=0.0)

        assert results == []

    def test_display_name_strips_service_type_suffix(self):
        from core.helpers.discovery import scan_for_dvrs, MDNS_SERVICE_TYPE

        fake_zc = MagicMock()
        info = MagicMock()
        info.parsed_addresses.return_value = ["10.0.0.1"]
        info.port = 8089
        info.properties = {}
        fake_zc.get_service_info.return_value = info

        service_name = f"My DVR.{MDNS_SERVICE_TYPE}"

        class BrowserWithNoNameProp:
            def __call__(self, zc, service_type, listener):
                listener.add_service(zc, service_type, service_name)
                return MagicMock()

        with (
            patch("core.helpers.discovery.Zeroconf", return_value=fake_zc),
            patch("core.helpers.discovery.ServiceBrowser", BrowserWithNoNameProp()),
            patch("core.helpers.discovery.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            results = scan_for_dvrs(timeout=0.0)

        assert len(results) == 1
        assert results[0]["display_name_suggestion"] == "My DVR"


class TestBuildScanResponse:
    def test_found_servers_returned_directly(self):
        from core.helpers.discovery import build_scan_response

        servers = [
            {
                "host": "192.168.1.10",
                "port": 8089,
                "display_name_suggestion": "Home DVR",
            }
        ]
        resp = build_scan_response(servers)

        assert resp["servers"] == servers
        assert resp["manual_add_available"] is True
        assert resp["message"] is None

    def test_existing_servers_excluded(self):
        from core.helpers.discovery import build_scan_response

        servers = [
            {
                "host": "192.168.1.10",
                "port": 8089,
                "display_name_suggestion": "Home DVR",
            },
            {
                "host": "192.168.1.20",
                "port": 8089,
                "display_name_suggestion": "Bedroom DVR",
            },
        ]
        resp = build_scan_response(servers, existing_hosts={("192.168.1.10", 8089)})

        assert len(resp["servers"]) == 1
        assert resp["servers"][0]["host"] == "192.168.1.20"

    def test_all_configured_message_when_all_excluded(self):
        from core.helpers.discovery import build_scan_response, _MSG_ALL_CONFIGURED

        servers = [
            {
                "host": "192.168.1.10",
                "port": 8089,
                "display_name_suggestion": "Home DVR",
            }
        ]
        resp = build_scan_response(servers, existing_hosts={("192.168.1.10", 8089)})

        assert resp["servers"] == []
        assert resp["manual_add_available"] is True
        assert resp["message"] == _MSG_ALL_CONFIGURED

    def test_bridge_mode_message_inside_container(self):
        from core.helpers.discovery import build_scan_response, _MSG_HOST_NET_REQUIRED

        with patch("core.helpers.discovery._running_in_container", return_value=True):
            resp = build_scan_response([])

        assert resp["servers"] == []
        assert resp["manual_add_available"] is True
        assert resp["message"] == _MSG_HOST_NET_REQUIRED

    def test_generic_message_outside_container(self):
        from core.helpers.discovery import build_scan_response, _MSG_NO_DVRS_FOUND

        with patch("core.helpers.discovery._running_in_container", return_value=False):
            resp = build_scan_response([])

        assert resp["message"] == _MSG_NO_DVRS_FOUND

    def test_manual_add_always_available(self):
        from core.helpers.discovery import build_scan_response

        for servers in ([], [{"host": "x", "port": 1, "display_name_suggestion": "X"}]):
            resp = build_scan_response(servers)
            assert resp["manual_add_available"] is True


SETTINGS_DATA = {
    "dvr_servers": [
        {
            "id": "dvr_aaa11111",
            "host": "192.168.1.10",
            "port": 8089,
            "name": "Existing DVR",
            "enabled": True,
        },
    ],
    "tz": "America/New_York",
    "api_key": "",
}


@pytest.fixture()
def settings_file(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(SETTINGS_DATA))
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
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


class TestDiscoveryScanEndpoint:
    def test_host_mode_returns_discovered_dvr(self, client):
        found = [
            {"host": "192.168.1.99", "port": 8089, "display_name_suggestion": "New DVR"}
        ]
        with patch("ui.backend.main._scan_for_dvrs", return_value=found):
            resp = client.post("/api/v1/discovery/scan")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["servers"]) == 1
        assert body["servers"][0]["host"] == "192.168.1.99"
        assert body["manual_add_available"] is True
        assert body["message"] is None

    def test_already_configured_dvr_excluded(self, client):
        found = [
            {
                "host": "192.168.1.10",
                "port": 8089,
                "display_name_suggestion": "Existing DVR",
            }
        ]
        with patch("ui.backend.main._scan_for_dvrs", return_value=found):
            resp = client.post("/api/v1/discovery/scan")

        assert resp.status_code == 200
        body = resp.json()
        assert body["servers"] == []
        assert body["manual_add_available"] is True
        assert body["message"] is not None

    def test_bridge_mode_empty_graceful_response(self, client):
        with (
            patch("ui.backend.main._scan_for_dvrs", return_value=[]),
            patch("core.helpers.discovery._running_in_container", return_value=True),
        ):
            resp = client.post("/api/v1/discovery/scan")

        assert resp.status_code == 200
        body = resp.json()
        assert body["servers"] == []
        assert body["manual_add_available"] is True
        assert (
            "host network" in body["message"].lower()
            or "manually" in body["message"].lower()
        )

    def test_non_container_empty_graceful_response(self, client):
        with (
            patch("ui.backend.main._scan_for_dvrs", return_value=[]),
            patch("core.helpers.discovery._running_in_container", return_value=False),
        ):
            resp = client.post("/api/v1/discovery/scan")

        assert resp.status_code == 200
        body = resp.json()
        assert body["servers"] == []
        assert body["manual_add_available"] is True
        assert "manually" in body["message"].lower()

    def test_endpoint_requires_no_auth_when_disabled(self, client):
        with patch("ui.backend.main._scan_for_dvrs", return_value=[]):
            resp = client.post("/api/v1/discovery/scan")
        assert resp.status_code == 200

    def test_legacy_endpoint_still_works(self, client):
        with patch("ui.backend.main._scan_for_dvrs", return_value=[]):
            resp = client.get("/api/discover-servers")
        assert resp.status_code == 200
        body = resp.json()
        assert "servers" in body
        assert "error" in body

    def test_legacy_endpoint_returns_sanitized_error(self, client):
        with patch(
            "ui.backend.main._scan_for_dvrs",
            side_effect=RuntimeError("token leaked"),
        ):
            resp = client.get("/api/discover-servers")
        assert resp.status_code == 200
        assert resp.json() == {
            "servers": [],
            "error": "DVR discovery failed. Check network access and container logs.",
        }
