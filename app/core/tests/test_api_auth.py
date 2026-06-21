"""Tests for API authentication and sensitive field masking."""

import json
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient


def _make_settings_file(
    tmp_path,
    *,
    api_key="",
    auth_mode="",
    security_setup_completed=None,
    dvr_servers=None,
):
    settings = {
        "dvr_servers": dvr_servers or [],
        "tz": "America/New_York",
        "api_key": api_key,
        "auth_mode": auth_mode,
    }
    if security_setup_completed is not None:
        settings["security_setup_completed"] = security_setup_completed
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(settings))
    return settings_file


@pytest.fixture
def test_settings_file(tmp_path):
    """Create a temporary settings file with a known API key."""
    settings = {
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
        "api_key": "test-api-key-12345",
        "apprise_pushover": "my-secret-pushover-key@my-secret-pushover-token",
        "apprise_discord": "webhook/secret/token",
        "apprise_email": "user:password@smtp.example.com",
        "apprise_telegram": "bot123/chat456",
        "error_reporting_dsn": "https://public:secret@example.test/1",
    }
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(settings))
    return f


@pytest.fixture
def client(test_settings_file):
    """Create a test client with patched config paths."""
    with (
        patch("ui.backend.config.CONFIG_FILE", test_settings_file),
        patch("ui.backend.config.CONFIG_DIR", test_settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.API_KEY_CACHE", "test-api-key-12345"),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def blank_key_client(tmp_path):
    settings = {
        "dvr_servers": [],
        "tz": "America/New_York",
        "api_key": "",
        "security_setup_completed": False,
    }
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(settings))
    with (
        patch("ui.backend.config.CONFIG_FILE", f),
        patch("ui.backend.config.CONFIG_DIR", f.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.API_KEY_CACHE", ""),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


class TestUnauthenticatedAccess:
    def test_ping_no_auth_required(self, client):
        resp = client.get("/api/ping")
        assert resp.status_code == 200

    def test_health_no_auth_required(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 503

    def test_get_settings_requires_auth_when_api_key_configured(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 401

    def test_get_settings_allowed_for_true_first_run_without_api_key(
        self, blank_key_client
    ):
        resp = blank_key_client.get("/api/settings")
        assert resp.status_code == 200

    def test_get_settings_allowed_for_seeded_dvr_first_run_without_api_key(
        self, tmp_path
    ):
        settings_file = _make_settings_file(
            tmp_path,
            api_key="",
            security_setup_completed=False,
            dvr_servers=[
                {
                    "id": "dvr_seeded",
                    "host": "192.168.1.60",
                    "port": 8089,
                    "name": "Seeded DVR",
                    "enabled": True,
                }
            ],
        )

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", ""),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/settings")

        assert resp.status_code == 200

    def test_legacy_config_without_setup_marker_preserved(self, tmp_path):
        settings = {
            "dvr_servers": [
                {
                    "id": "dvr_legacy",
                    "host": "192.168.1.50",
                    "port": 8089,
                    "name": "Legacy DVR",
                    "enabled": True,
                }
            ],
            "tz": "America/New_York",
            "api_key": "",
        }
        f = tmp_path / "settings.json"
        f.write_text(json.dumps(settings))

        with (
            patch("ui.backend.config.CONFIG_FILE", f),
            patch("ui.backend.config.CONFIG_DIR", f.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", ""),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            setup_resp = client.get("/api/v1/auth/setup-status")
            settings_resp = client.get("/api/settings")

        assert setup_resp.status_code == 200
        assert settings_resp.status_code == 200
        body = setup_resp.json()
        assert body["persisted_mode"] is None
        assert body["effective_mode"] == "none"
        assert body["setup_required"] is False
        saved = json.loads(f.read_text())
        assert "security_setup_completed" not in saved

    def test_persisted_no_auth_keeps_routes_open_without_runtime_override(
        self, tmp_path
    ):
        settings_file = _make_settings_file(
            tmp_path,
            api_key="legacy-key",
            auth_mode="none",
            security_setup_completed=True,
            dvr_servers=[
                {
                    "id": "dvr_open",
                    "host": "192.168.1.70",
                    "port": 8089,
                    "name": "No Auth DVR",
                    "enabled": True,
                }
            ],
        )

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", "legacy-key"),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            settings_resp = client.get("/api/settings")
            about_resp = client.get("/api/about")

        assert settings_resp.status_code == 200
        assert about_resp.status_code == 200

    def test_runtime_override_is_reversible_for_legacy_api_key_boundary(self, tmp_path):
        settings_file = _make_settings_file(
            tmp_path,
            api_key="legacy-key",
            auth_mode="",
            dvr_servers=[],
        )

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", True),
            patch("ui.backend.main.API_KEY_CACHE", "legacy-key"),
        ):
            from ui.backend.main import app

            override_client = TestClient(app, raise_server_exceptions=False)
            override_resp = override_client.get("/api/about")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", "legacy-key"),
        ):
            from ui.backend.main import app

            restarted_client = TestClient(app, raise_server_exceptions=False)
            restarted_resp = restarted_client.get("/api/about")

        assert override_resp.status_code == 200
        assert restarted_resp.status_code == 401
        saved = json.loads(settings_file.read_text())
        assert saved["api_key"] == "legacy-key"
        assert saved["auth_mode"] == ""

    def test_post_settings_requires_auth(self, client):
        resp = client.post("/api/settings", json={"tz": "America/Los_Angeles"})
        assert resp.status_code == 401

    def test_about_requires_auth(self, client):
        resp = client.get("/api/about")
        assert resp.status_code == 401

    def test_system_info_requires_auth(self, client):
        resp = client.get("/api/system-info")
        assert resp.status_code == 401


class TestAuthenticatedAccess:
    def test_post_settings_with_valid_key(self, client, test_settings_file):
        settings = json.loads(test_settings_file.read_text())
        resp = client.post(
            "/api/settings",
            json=settings,
            headers={"X-API-Key": "test-api-key-12345"},
        )
        assert resp.status_code == 200

    def test_invalid_key_rejected(self, client):
        resp = client.get(
            "/api/about",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_about_with_valid_key(self, client):
        resp = client.get(
            "/api/about",
            headers={"X-API-Key": "test-api-key-12345"},
        )
        assert resp.status_code == 200

    def test_post_settings_generates_blank_feed_tokens(
        self, client, test_settings_file, monkeypatch
    ):
        payload = json.loads(test_settings_file.read_text())
        payload.update(
            {
                "ics_feed_enabled": True,
                "ics_feed_token": "   ",
                "rss_feed_enabled": True,
                "rss_feed_token": "",
            }
        )
        generated = iter(["ics-generated", "rss-generated"])
        monkeypatch.setattr(
            "ui.backend.main.secrets.token_urlsafe", lambda _size: next(generated)
        )

        resp = client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "test-api-key-12345"},
        )

        assert resp.status_code == 200
        saved = json.loads(test_settings_file.read_text())
        assert saved["ics_feed_token"] == "ics-generated"
        assert saved["rss_feed_token"] == "rss-generated"

    def test_post_settings_preserves_existing_feed_tokens(
        self, client, test_settings_file, monkeypatch
    ):
        payload = json.loads(test_settings_file.read_text())
        payload.update(
            {
                "ics_feed_enabled": True,
                "ics_feed_token": "keep-ics",
                "rss_feed_enabled": True,
                "rss_feed_token": "keep-rss",
            }
        )
        monkeypatch.setattr(
            "ui.backend.main.secrets.token_urlsafe",
            lambda _size: pytest.fail("token_urlsafe should not be called"),
        )

        resp = client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "test-api-key-12345"},
        )

        assert resp.status_code == 200
        saved = json.loads(test_settings_file.read_text())
        assert saved["ics_feed_token"] == "keep-ics"
        assert saved["rss_feed_token"] == "keep-rss"


class TestSensitiveFieldMasking:
    def test_get_settings_masks_sensitive_fields(self, client):
        resp = client.get("/api/settings", headers={"X-API-Key": "test-api-key-12345"})
        data = resp.json()
        assert data["apprise_pushover"] == "****"
        assert data["apprise_discord"] == "****"
        assert data["apprise_email"] == "****"
        assert data["apprise_telegram"] == "****"
        assert data["error_reporting_dsn"] == "****"

    def test_get_settings_masks_api_key(self, client):
        resp = client.get("/api/settings", headers={"X-API-Key": "test-api-key-12345"})
        data = resp.json()
        assert data["api_key"] == "****"

    def test_get_settings_exposes_non_sensitive_fields(self, client):
        resp = client.get("/api/settings", headers={"X-API-Key": "test-api-key-12345"})
        data = resp.json()
        assert data["dvr_servers"][0]["host"] == "192.168.1.100"
        assert data["tz"] == "America/New_York"

    def test_empty_sensitive_fields_not_masked(self, client, test_settings_file):
        settings = json.loads(test_settings_file.read_text())
        settings["apprise_slack"] = ""
        test_settings_file.write_text(json.dumps(settings))
        resp = client.get("/api/settings", headers={"X-API-Key": "test-api-key-12345"})
        data = resp.json()
        # Empty strings should not be masked
        assert data["apprise_slack"] == ""

    def test_get_settings_redacts_webhook_url_tokens(self, client, test_settings_file):
        settings = json.loads(test_settings_file.read_text())
        settings["webhooks"] = [
            {
                "url": "https://user:pass@hooks.example.test/api/webhooks/abc123/token456?sig=secret",
                "secret": "hmac-secret",
                "enabled": True,
            }
        ]
        test_settings_file.write_text(json.dumps(settings))

        resp = client.get("/api/settings", headers={"X-API-Key": "test-api-key-12345"})
        webhook = resp.json()["webhooks"][0]

        assert webhook["secret"] == "****"
        assert webhook["url"] == "https://hooks.example.test/api/webhooks/****/****"
        assert "user:pass" not in webhook["url"]
        assert "sig=secret" not in webhook["url"]

    def test_post_settings_preserves_masked_webhook_url_by_position(
        self, client, test_settings_file
    ):
        settings = json.loads(test_settings_file.read_text())
        real_url = "https://hooks.example.test/api/webhooks/abc123/token456?sig=secret"
        settings["webhooks"] = [
            {"url": real_url, "secret": "hmac-secret", "enabled": True}
        ]
        test_settings_file.write_text(json.dumps(settings))

        payload = json.loads(test_settings_file.read_text())
        payload["webhooks"][0]["url"] = (
            "https://hooks.example.test/api/webhooks/****/****"
        )
        payload["webhooks"][0]["secret"] = "****"
        resp = client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "test-api-key-12345"},
        )

        assert resp.status_code == 200
        saved = json.loads(test_settings_file.read_text())
        assert saved["webhooks"][0]["url"] == real_url
        assert saved["webhooks"][0]["secret"] == "hmac-secret"


class TestSensitiveFieldPOSTSentinel:
    def test_post_settings_preserves_when_sentinel_submitted(
        self, client, test_settings_file
    ):
        # preserve loop currently checks ("", None) only — "****" falls through
        # and overwrites the real credential. Passes after adds MASKED_SENTINEL.
        payload = json.loads(test_settings_file.read_text())
        payload["apprise_pushover"] = "****"
        payload["apprise_discord"] = "****"
        payload["error_reporting_dsn"] = "****"
        resp = client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "test-api-key-12345"},
        )
        assert resp.status_code == 200
        saved = json.loads(test_settings_file.read_text())
        assert (
            saved["apprise_pushover"]
            == "my-secret-pushover-key@my-secret-pushover-token"
        )
        assert saved["apprise_discord"] == "webhook/secret/token"
        assert saved["error_reporting_dsn"] == "https://public:secret@example.test/1"

    def test_post_settings_updates_when_new_value_submitted(
        self, client, test_settings_file
    ):
        payload = json.loads(test_settings_file.read_text())
        payload["apprise_pushover"] = "new-key@new-token"
        resp = client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "test-api-key-12345"},
        )
        assert resp.status_code == 200
        saved = json.loads(test_settings_file.read_text())
        assert saved["apprise_pushover"] == "new-key@new-token"

    def test_post_settings_preserves_when_empty_submitted(
        self, client, test_settings_file
    ):
        payload = json.loads(test_settings_file.read_text())
        payload["apprise_pushover"] = ""
        payload["error_reporting_dsn"] = ""
        resp = client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "test-api-key-12345"},
        )
        assert resp.status_code == 200
        saved = json.loads(test_settings_file.read_text())
        assert (
            saved["apprise_pushover"]
            == "my-secret-pushover-key@my-secret-pushover-token"
        )
        assert saved["error_reporting_dsn"] == "https://public:secret@example.test/1"

    def test_post_settings_preserves_when_sensitive_field_omitted(
        self, client, test_settings_file
    ):
        payload = json.loads(test_settings_file.read_text())
        del payload["error_reporting_dsn"]
        resp = client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "test-api-key-12345"},
        )
        assert resp.status_code == 200
        saved = json.loads(test_settings_file.read_text())
        assert saved["error_reporting_dsn"] == "https://public:secret@example.test/1"
