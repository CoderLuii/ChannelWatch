"""Tests for CSP, CSRF, and security header behaviour."""

import json
import pytest
from unittest.mock import patch
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from starlette.testclient import TestClient

from core.storage.database import create_db_engine
from core.storage.models import User


@pytest.fixture
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
        "api_key": "sec-test-key-99999",
    }
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture
def authed_client(settings_file):
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.API_KEY_CACHE", "sec-test-key-99999"),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def noauth_client(settings_file):
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.API_KEY_CACHE", ""),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_engine():
    engine = create_db_engine("sqlite:///:memory:", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def rbac_user(auth_engine):
    from core.storage.database import get_session as _gs

    user = User(username="secadmin", password_hash="", role="admin")
    user.set_password("correcthorse")
    with _gs(auth_engine) as session:
        session.add(user)
        session.commit()


def _make_auth_settings_file(
    tmp_path,
    *,
    api_key="",
    rbac_enabled=False,
    auth_mode="",
    security_setup_completed=None,
):
    data = {
        "dvr_servers": [],
        "tz": "America/New_York",
        "api_key": api_key,
        "rbac_enabled": rbac_enabled,
        "auth_mode": auth_mode,
    }
    if security_setup_completed is not None:
        data["security_setup_completed"] = security_setup_completed
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(data))
    return settings_file


class TestCSPHeaders:
    @staticmethod
    def _directive(csp: str, name: str) -> str:
        return next(
            directive.strip()
            for directive in csp.split(";")
            if directive.strip().startswith(name)
        )

    def test_csp_present_on_api_response(self, authed_client):
        resp = authed_client.get("/api/ping")
        assert "content-security-policy" in resp.headers

    @pytest.mark.parametrize(
        ("path", "headers"),
        [
            ("/api/ping", {}),
            ("/healthz/live", {}),
            ("/metrics", {"X-API-Key": "sec-test-key-99999"}),
        ],
    )
    def test_api_health_and_metrics_csp_keep_strict_script_src(
        self, authed_client, path, headers
    ):
        resp = authed_client.get(path, headers=headers)
        csp = resp.headers["content-security-policy"]
        script_src = self._directive(csp, "script-src")

        assert "'unsafe-inline'" not in script_src
        assert "'unsafe-eval'" not in script_src
        assert script_src == "script-src 'self'"

    def test_csp_has_no_unsafe_eval(self, authed_client):
        resp = authed_client.get("/api/ping")
        csp = resp.headers["content-security-policy"]
        assert "unsafe-eval" not in csp

    def test_static_ui_csp_allows_next_bootstrap_inline_scripts(self, noauth_client):
        resp = noauth_client.get("/")
        csp = resp.headers["content-security-policy"]
        script_src = self._directive(csp, "script-src")

        assert script_src == "script-src 'self' 'unsafe-inline'"
        assert "'unsafe-eval'" not in script_src
        assert "style-src 'self' 'unsafe-inline'" in csp

    def test_csp_restricts_default_src_to_self(self, authed_client):
        resp = authed_client.get("/api/ping")
        csp = resp.headers["content-security-policy"]
        assert "default-src 'self'" in csp

    def test_csp_blocks_object_src(self, authed_client):
        resp = authed_client.get("/api/ping")
        csp = resp.headers["content-security-policy"]
        assert "object-src 'none'" in csp

    def test_csp_restricts_base_uri(self, authed_client):
        resp = authed_client.get("/api/ping")
        csp = resp.headers["content-security-policy"]
        assert "base-uri 'self'" in csp

    def test_csp_restricts_form_action(self, authed_client):
        resp = authed_client.get("/api/ping")
        csp = resp.headers["content-security-policy"]
        assert "form-action 'self'" in csp

    def test_csp_frame_ancestors(self, authed_client):
        resp = authed_client.get("/api/ping")
        csp = resp.headers["content-security-policy"]
        assert "frame-ancestors 'self'" in csp


class TestSecurityHeaders:
    def test_x_content_type_options(self, authed_client):
        resp = authed_client.get("/api/ping")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self, authed_client):
        resp = authed_client.get("/api/ping")
        assert resp.headers.get("x-frame-options") == "SAMEORIGIN"

    def test_referrer_policy(self, authed_client):
        resp = authed_client.get("/api/ping")
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy_present(self, authed_client):
        resp = authed_client.get("/api/ping")
        pp = resp.headers.get("permissions-policy", "")
        assert "camera=()" in pp
        assert "microphone=()" in pp
        assert "geolocation=()" in pp


class TestCSRFWithAuthEnabled:
    def test_post_without_origin_and_with_valid_key_succeeds(
        self, authed_client, settings_file
    ):
        payload = json.loads(settings_file.read_text())
        resp = authed_client.post(
            "/api/settings",
            json=payload,
            headers={"X-API-Key": "sec-test-key-99999"},
        )
        assert resp.status_code == 200

    def test_post_with_matching_origin_and_valid_key_succeeds(
        self, authed_client, settings_file
    ):
        payload = json.loads(settings_file.read_text())
        resp = authed_client.post(
            "/api/settings",
            json=payload,
            headers={
                "X-API-Key": "sec-test-key-99999",
                "Origin": "http://testserver",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 200

    def test_cross_origin_post_without_api_key_rejected_401(self, authed_client):
        resp = authed_client.post(
            "/api/settings",
            json={},
            headers={
                "Origin": "http://evil.example.com",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 401


class TestCSRFWithAuthDisabled:
    def test_same_origin_post_allowed(self, noauth_client, settings_file):
        payload = json.loads(settings_file.read_text())
        resp = noauth_client.post(
            "/api/settings",
            json=payload,
            headers={
                "Origin": "http://testserver",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 200

    def test_no_origin_header_post_allowed(self, noauth_client, settings_file):
        payload = json.loads(settings_file.read_text())
        resp = noauth_client.post("/api/settings", json=payload)
        assert resp.status_code == 200

    def test_cross_origin_post_rejected_403(self, noauth_client):
        resp = noauth_client.post(
            "/api/clear-activity-history",
            headers={
                "Origin": "http://evil.example.com",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 403

    def test_cross_origin_delete_rejected_403(self, noauth_client):
        resp = noauth_client.delete(
            "/api/dvrs/some-id",
            headers={
                "Origin": "http://attacker.net",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 403

    def test_get_is_not_csrf_protected(self, noauth_client):
        resp = noauth_client.get(
            "/api/ping",
            headers={
                "Origin": "http://evil.example.com",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 200

    def test_csrf_check_does_not_apply_to_exempt_paths(self, noauth_client):
        resp = noauth_client.get(
            "/api/health",
            headers={
                "Origin": "http://evil.example.com",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 503


class TestSessionCookieHardening:
    def test_rbac_login_sets_httponly_strict_session_cookie(
        self, tmp_path, auth_engine, rbac_user
    ):
        settings_file = _make_auth_settings_file(
            tmp_path,
            rbac_enabled=True,
            auth_mode="rbac",
            security_setup_completed=True,
        )

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.RBAC_ENABLED", True),
            patch("ui.backend.main._auth_db_engine", auth_engine),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "secadmin", "password": "correcthorse"},
            )

        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "channelwatch_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie

    def test_setup_to_rbac_sets_httponly_strict_session_cookie(
        self, tmp_path, auth_engine
    ):
        settings_file = _make_auth_settings_file(
            tmp_path,
            rbac_enabled=False,
            auth_mode="",
            security_setup_completed=False,
        )

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.RBAC_ENABLED", False),
            patch("ui.backend.main._auth_db_engine", auth_engine),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/setup",
                json={
                    "mode": "rbac",
                    "username": "bootstrap_admin",
                    "password": "setup-pass",
                },
            )

        assert resp.status_code == 201
        set_cookie = resp.headers.get("set-cookie", "")
        assert "channelwatch_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie
