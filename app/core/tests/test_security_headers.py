"""Tests for CSP, CSRF, and security header behaviour."""

import json
import pytest
from unittest.mock import patch
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from starlette.testclient import TestClient

from core.storage.database import create_db_engine
from core.storage.models import User


@pytest.fixture(autouse=True)
def clear_rate_limiter_between_security_tests():
    """Keep this request-heavy module from leaking buckets into later suites."""
    from ui.backend.main import rate_limiter

    with rate_limiter._lock:
        rate_limiter._requests.clear()
    yield
    with rate_limiter._lock:
        rate_limiter._requests.clear()


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
def persisted_noauth_client(tmp_path):
    settings_file = _make_auth_settings_file(
        tmp_path,
        auth_mode="none",
        security_setup_completed=True,
    )
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.API_KEY_CACHE", ""),
        patch("ui.backend.main.AUTH_MODE_CACHE", None),
        patch("ui.backend.main.RBAC_ENABLED", False),
        patch("ui.backend.main._auth_settings_signature", None),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False), settings_file


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
        assert "frame-src 'self'" in csp

    def test_static_ui_csp_allows_configured_report_endpoint(
        self, monkeypatch, noauth_client
    ):
        monkeypatch.setenv(
            "CHANNELWATCH_REPORT_ENDPOINT",
            "https://channelwatch.coderluii.dev/api/reports",
        )

        resp = noauth_client.get("/")
        connect_src = self._directive(
            resp.headers["content-security-policy"], "connect-src"
        )

        assert connect_src == "connect-src 'self' https://channelwatch.coderluii.dev"

    def test_static_ui_csp_does_not_allow_turnstile_for_in_app_reports(
        self, monkeypatch, noauth_client
    ):
        monkeypatch.setenv("CHANNELWATCH_REPORT_TURNSTILE_SITE_KEY", "1x00000000000000000000AA")

        resp = noauth_client.get("/")
        csp = resp.headers["content-security-policy"]

        assert self._directive(csp, "script-src") == "script-src 'self' 'unsafe-inline'"
        assert self._directive(csp, "connect-src") == "connect-src 'self'"
        assert self._directive(csp, "frame-src") == "frame-src 'self'"

        config = noauth_client.get("/api/v1/support/report-config").json()
        assert config["turnstile_site_key"] is None

    def test_static_ui_csp_ignores_malformed_report_endpoint(
        self, monkeypatch, noauth_client
    ):
        monkeypatch.setenv(
            "CHANNELWATCH_REPORT_ENDPOINT",
            "https://example.com:bad/api/reports",
        )

        resp = noauth_client.get("/")
        connect_src = self._directive(
            resp.headers["content-security-policy"], "connect-src"
        )

        assert connect_src == "connect-src 'self'"

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

    def test_cross_origin_post_with_valid_api_key_uses_api_key_csrf_defence(
        self, authed_client, settings_file
    ):
        payload = json.loads(settings_file.read_text())
        resp = authed_client.post(
            "/api/settings",
            json=payload,
            headers={
                "X-API-Key": "sec-test-key-99999",
                "Origin": "http://evil.example.com",
                "Host": "testserver",
            },
        )
        assert resp.status_code == 200


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


class TestCSRFWithPersistedNoAuth:
    def test_cross_origin_restart_rejected_with_structured_error(
        self, persisted_noauth_client
    ):
        client, _settings_file = persisted_noauth_client
        resp = client.post(
            "/api/restart_container",
            headers={
                "Origin": "http://evil.example.com",
                "Host": "testserver",
            },
        )

        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_AUTH_CROSS_SITE_REJECTED"
        assert detail["message"] == "Cross-site request rejected."
        assert detail["remediation"]

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({}, id="no-origin-cli-client"),
            pytest.param(
                {"Origin": "http://testserver", "Host": "testserver"},
                id="same-origin-browser",
            ),
        ],
    )
    def test_settings_write_allows_nonbrowser_or_same_origin_client(
        self, persisted_noauth_client, headers
    ):
        client, settings_file = persisted_noauth_client
        payload = json.loads(settings_file.read_text())

        resp = client.post("/api/settings", json=payload, headers=headers)

        assert resp.status_code == 200

    def test_cross_origin_health_get_remains_exempt(self, persisted_noauth_client):
        client, _settings_file = persisted_noauth_client

        resp = client.get(
            "/healthz/live",
            headers={
                "Origin": "http://evil.example.com",
                "Host": "testserver",
            },
        )

        assert resp.status_code == 200

    def test_https_request_rejects_http_origin_for_same_host(
        self, persisted_noauth_client
    ):
        _client, _settings_file = persisted_noauth_client
        from ui.backend.main import app

        client = TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        resp = client.post(
            "/api/restart_container",
            headers={"Origin": "http://testserver"},
        )

        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_AUTH_CROSS_SITE_REJECTED"
        assert detail["message"] == "Cross-site request rejected."
        assert detail["remediation"]

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({}, id="no-origin-cli-client"),
            pytest.param(
                {"Origin": "https://testserver"},
                id="matching-https-origin",
            ),
        ],
    )
    def test_https_request_allows_no_origin_or_matching_https_origin(
        self, persisted_noauth_client, headers
    ):
        _client, settings_file = persisted_noauth_client
        from ui.backend.main import app

        client = TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        payload = json.loads(settings_file.read_text())

        resp = client.post("/api/settings", json=payload, headers=headers)

        assert resp.status_code == 200

    @pytest.mark.parametrize(
        ("origin", "expected_status"),
        [
            pytest.param("http://testserver", 403, id="external-scheme-mismatch"),
            pytest.param("https://testserver", 200, id="external-scheme-match"),
        ],
    )
    def test_trusted_proxy_scheme_is_used_for_origin_comparison(
        self, persisted_noauth_client, origin, expected_status
    ):
        _client, settings_file = persisted_noauth_client
        from ui.backend import main as backend_main

        networks = backend_main._parse_trusted_proxy_networks("10.20.30.40")
        payload = json.loads(settings_file.read_text())
        with patch("ui.backend.main._TRUSTED_PROXY_NETWORKS", networks):
            client = TestClient(
                backend_main.app,
                base_url="http://testserver",
                raise_server_exceptions=False,
                client=("10.20.30.40", 50000),
            )
            resp = client.post(
                "/api/settings",
                json=payload,
                headers={
                    "Origin": origin,
                    "X-Forwarded-Proto": "https",
                },
            )

        assert resp.status_code == expected_status

    def test_untrusted_forwarded_scheme_is_ignored_for_origin_comparison(
        self, persisted_noauth_client
    ):
        _client, settings_file = persisted_noauth_client
        from ui.backend import main as backend_main

        payload = json.loads(settings_file.read_text())
        with patch("ui.backend.main._TRUSTED_PROXY_NETWORKS", ()):
            client = TestClient(
                backend_main.app,
                base_url="http://testserver",
                raise_server_exceptions=False,
                client=("192.0.2.10", 50000),
            )
            resp = client.post(
                "/api/settings",
                json=payload,
                headers={
                    "Origin": "http://testserver",
                    "X-Forwarded-Proto": "https",
                },
            )

        assert resp.status_code == 200


class TestCSRFWithFirstRunSetup:
    @pytest.mark.parametrize(
        ("base_url", "origin"),
        [
            pytest.param(
                "http://testserver",
                "http://evil.example.com",
                id="different-host",
            ),
            pytest.param(
                "https://testserver",
                "http://testserver",
                id="same-host-scheme-downgrade",
            ),
        ],
    )
    def test_cross_origin_first_admin_setup_is_rejected(
        self, tmp_path, auth_engine, base_url, origin
    ):
        settings_file = _make_auth_settings_file(
            tmp_path,
            auth_mode="",
            security_setup_completed=False,
        )
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.AUTH_MODE_CACHE", None),
            patch("ui.backend.main.RBAC_ENABLED", False),
            patch("ui.backend.main._auth_settings_signature", None),
            patch("ui.backend.main._auth_db_engine", auth_engine),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            from ui.backend.main import app

            client = TestClient(
                app,
                base_url=base_url,
                raise_server_exceptions=False,
            )
            resp = client.post(
                "/api/v1/auth/setup",
                json={
                    "mode": "rbac",
                    "username": "cross_site_admin",
                    "password": "securepass",
                },
                headers={
                    "Origin": origin,
                    "Host": "testserver",
                },
            )

        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "ERR_AUTH_CROSS_SITE_REJECTED"
        assert json.loads(settings_file.read_text())["auth_mode"] == ""

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({}, id="no-origin-cli-client"),
            pytest.param(
                {"Origin": "http://testserver", "Host": "testserver"},
                id="same-origin-browser",
            ),
        ],
    )
    def test_first_admin_setup_allows_nonbrowser_or_same_origin_client(
        self, tmp_path, auth_engine, headers
    ):
        settings_file = _make_auth_settings_file(
            tmp_path,
            auth_mode="",
            security_setup_completed=False,
        )
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.AUTH_MODE_CACHE", None),
            patch("ui.backend.main.RBAC_ENABLED", False),
            patch("ui.backend.main._auth_settings_signature", None),
            patch("ui.backend.main._auth_db_engine", auth_engine),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/setup",
                json={
                    "mode": "rbac",
                    "username": "first_admin",
                    "password": "securepass",
                },
                headers=headers,
            )

        assert resp.status_code == 201
        assert resp.json()["username"] == "first_admin"
        assert json.loads(settings_file.read_text())["auth_mode"] == "rbac"

    def test_cross_origin_recovery_setup_is_rejected(self, tmp_path, auth_engine):
        from core.storage.auth import get_user_count

        settings_file = _make_auth_settings_file(
            tmp_path,
            rbac_enabled=True,
            auth_mode="rbac",
            security_setup_completed=True,
        )
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.AUTH_MODE_CACHE", None),
            patch("ui.backend.main.RBAC_ENABLED", True),
            patch("ui.backend.main._auth_settings_signature", None),
            patch("ui.backend.main._auth_db_engine", auth_engine),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            setup_status = client.get("/api/v1/auth/setup-status")
            resp = client.post(
                "/api/v1/auth/setup",
                json={
                    "mode": "rbac",
                    "username": "cross_site_recovery_admin",
                    "password": "securepass",
                },
                headers={
                    "Origin": "http://evil.example.com",
                    "Host": "testserver",
                },
            )

        assert setup_status.status_code == 200
        assert setup_status.json()["effective_mode"] == "rbac"
        assert setup_status.json()["setup_required"] is True
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "ERR_AUTH_CROSS_SITE_REJECTED"
        assert get_user_count(auth_engine) == 0

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({}, id="no-origin-recovery-client"),
            pytest.param(
                {"Origin": "http://testserver", "Host": "testserver"},
                id="same-origin-recovery-browser",
            ),
        ],
    )
    def test_recovery_setup_allows_nonbrowser_or_same_origin_client(
        self, tmp_path, auth_engine, headers
    ):
        from core.storage.auth import get_user_count

        settings_file = _make_auth_settings_file(
            tmp_path,
            rbac_enabled=True,
            auth_mode="rbac",
            security_setup_completed=True,
        )
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.AUTH_MODE_CACHE", None),
            patch("ui.backend.main.RBAC_ENABLED", True),
            patch("ui.backend.main._auth_settings_signature", None),
            patch("ui.backend.main._auth_db_engine", auth_engine),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/setup",
                json={
                    "mode": "rbac",
                    "username": "recovered_admin",
                    "password": "securepass",
                },
                headers=headers,
            )

        assert resp.status_code == 201
        assert resp.json()["username"] == "recovered_admin"
        assert get_user_count(auth_engine) == 1


class TestCSRFWithRbacAuth:
    def test_cross_origin_write_with_session_and_csrf_keeps_rbac_behavior(
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
            patch("ui.backend.main.CW_DISABLE_AUTH", False),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.AUTH_MODE_CACHE", None),
            patch("ui.backend.main.RBAC_ENABLED", True),
            patch("ui.backend.main._auth_settings_signature", None),
            patch("ui.backend.main._auth_db_engine", auth_engine),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "secadmin", "password": "correcthorse"},
            )
            assert login.status_code == 200
            payload = json.loads(settings_file.read_text())
            resp = client.post(
                "/api/settings",
                json=payload,
                headers={
                    "X-CSRF-Token": login.json()["csrf_token"],
                    "Origin": "http://evil.example.com",
                    "Host": "testserver",
                },
            )

        assert resp.status_code == 200


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
