import json
import os
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient
from sqlmodel import SQLModel

from core.storage.database import create_db_engine
from core.storage.models import User
from core.storage.auth import (
    get_user_by_username,
    get_user_count,
    create_user,
)


@pytest.fixture(autouse=True)
def clear_rate_limiter():
    from ui.backend.main import rate_limiter

    rate_limiter._requests.clear()
    yield
    rate_limiter._requests.clear()


@pytest.fixture(name="auth_engine")
def auth_engine_fixture():
    from sqlalchemy.pool import StaticPool

    engine = create_db_engine("sqlite:///:memory:", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _make_settings(
    tmp_path,
    rbac_enabled=True,
    api_key="test-key",
    auth_mode="",
    security_setup_completed=None,
):
    data = {
        "api_key": api_key,
        "rbac_enabled": rbac_enabled,
        "auth_mode": auth_mode,
        "dvr_servers": [],
    }
    if security_setup_completed is not None:
        data["security_setup_completed"] = security_setup_completed
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(data))
    return f


def _seed_user(engine, username, password, role):
    from core.storage.database import get_session as _gs

    u = User(username=username, password_hash="", role=role)
    u.set_password(password)
    with _gs(engine) as s:
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    return {"id": uid, "username": username, "password": password, "role": role}


@pytest.fixture(name="rbac_client")
def rbac_client_fixture(tmp_path, auth_engine):
    f = _make_settings(tmp_path, rbac_enabled=True, api_key="rbac-key")
    with (
        patch("ui.backend.config.CONFIG_FILE", f),
        patch("ui.backend.config.CONFIG_DIR", f.parent),
        patch("ui.backend.main.API_KEY_CACHE", "rbac-key"),
        patch("ui.backend.main.RBAC_ENABLED", True),
        patch("ui.backend.main._auth_db_engine", auth_engine),
        patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture(name="viewer_user")
def viewer_user_fixture(auth_engine):
    return _seed_user(auth_engine, "viewer_alice", "pass1", "viewer")


@pytest.fixture(name="operator_user")
def operator_user_fixture(auth_engine):
    return _seed_user(auth_engine, "op_bob", "pass2", "operator")


@pytest.fixture(name="admin_user")
def admin_user_fixture(auth_engine):
    return _seed_user(auth_engine, "admin_carol", "pass3", "admin")


def _login(client, username, password):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    return resp.cookies.get("channelwatch_session"), resp.json()["csrf_token"]


class TestViewerForbiddenOnWrites:
    def test_viewer_403_on_post_settings(self, rbac_client, viewer_user, auth_engine):
        token, csrf = _login(rbac_client, "viewer_alice", "pass1")
        resp = rbac_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_AUTH_FORBIDDEN"
        assert detail["message"] == "Requires operator role or higher"
        assert "sufficient permissions" in detail["remediation"]

    def test_viewer_403_on_clear_activity_history(
        self, rbac_client, viewer_user, auth_engine
    ):
        token, csrf = _login(rbac_client, "viewer_alice", "pass1")
        resp = rbac_client.post(
            "/api/clear-activity-history",
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    def test_viewer_403_on_restart_container(
        self, rbac_client, viewer_user, auth_engine
    ):
        token, csrf = _login(rbac_client, "viewer_alice", "pass1")
        resp = rbac_client.post(
            "/api/restart_container",
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    def test_viewer_403_on_regenerate_api_key(
        self, rbac_client, viewer_user, auth_engine
    ):
        token, csrf = _login(rbac_client, "viewer_alice", "pass1")
        resp = rbac_client.post(
            "/api/regenerate-api-key",
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    def test_viewer_403_on_dvr_soft_delete(self, rbac_client, viewer_user, auth_engine):
        token, csrf = _login(rbac_client, "viewer_alice", "pass1")
        resp = rbac_client.post(
            "/api/dvrs/fake-id/soft-delete",
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403


class TestOperatorAllowedOnOperatorRoutes:
    def test_operator_200_on_post_settings(
        self, rbac_client, operator_user, auth_engine
    ):
        token, csrf = _login(rbac_client, "op_bob", "pass2")
        resp = rbac_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200

    def test_operator_403_on_admin_only_routes(
        self, rbac_client, operator_user, auth_engine
    ):
        token, csrf = _login(rbac_client, "op_bob", "pass2")
        resp = rbac_client.post(
            "/api/regenerate-api-key",
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403


class TestAdminAllowedOnAllRoutes:
    def test_admin_200_on_post_settings(self, rbac_client, admin_user, auth_engine):
        token, csrf = _login(rbac_client, "admin_carol", "pass3")
        resp = rbac_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200

    def test_admin_passes_role_check_on_clear_activity_history(
        self, rbac_client, admin_user, auth_engine
    ):
        token, csrf = _login(rbac_client, "admin_carol", "pass3")
        resp = rbac_client.post(
            "/api/clear-activity-history",
            cookies={"channelwatch_session": token},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code != 403, (
            f"Role check incorrectly blocked admin: {resp.json()}"
        )


class TestApiKeyBypassesRoleCheck:
    def test_api_key_allows_post_settings_without_session(self, rbac_client):
        resp = rbac_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            headers={"X-API-Key": "rbac-key"},
        )
        assert resp.status_code == 200

    def test_api_key_allows_regenerate_key(self, rbac_client):
        resp = rbac_client.post(
            "/api/regenerate-api-key",
            headers={"X-API-Key": "rbac-key"},
        )
        assert resp.status_code == 200


class TestSetupStatusEndpoint:
    def test_setup_status_needs_setup_when_no_users(self, rbac_client, auth_engine):
        resp = rbac_client.get("/api/v1/auth/setup-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rbac_enabled"] is True
        assert data["needs_setup"] is True
        assert data["setup_required"] is True
        assert data["current_mode"] == "rbac"
        assert data["available_modes"] == ["rbac", "none"]

    def test_setup_status_no_setup_needed_after_user_created(
        self, rbac_client, auth_engine, viewer_user
    ):
        resp = rbac_client.get("/api/v1/auth/setup-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_setup"] is False

    def test_setup_status_when_rbac_off(self, tmp_path):
        f = _make_settings(tmp_path, rbac_enabled=False, api_key="off-key")
        with (
            patch("ui.backend.config.CONFIG_FILE", f),
            patch("ui.backend.config.CONFIG_DIR", f.parent),
            patch("ui.backend.main.API_KEY_CACHE", "off-key"),
            patch("ui.backend.main.RBAC_ENABLED", False),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/auth/setup-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rbac_enabled"] is False
        assert data["needs_setup"] is False


class TestSetupEndpoint:
    def test_setup_creates_admin_when_no_users(self, rbac_client, auth_engine):
        resp = rbac_client.post(
            "/api/v1/auth/setup",
            json={"username": "firstadmin", "password": "securepass"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "firstadmin"
        user = get_user_by_username(auth_engine, "firstadmin")
        assert user is not None
        assert user.role == "admin"

    def test_setup_rejected_when_users_exist(
        self, rbac_client, auth_engine, admin_user
    ):
        resp = rbac_client.post(
            "/api/v1/auth/setup",
            json={"username": "another", "password": "anotherpass"},
        )
        assert resp.status_code == 409

    def test_setup_rejected_when_rbac_off(self, tmp_path):
        f = _make_settings(tmp_path, rbac_enabled=False, api_key="off-key2")
        with (
            patch("ui.backend.config.CONFIG_FILE", f),
            patch("ui.backend.config.CONFIG_DIR", f.parent),
            patch("ui.backend.main.API_KEY_CACHE", "off-key2"),
            patch("ui.backend.main.RBAC_ENABLED", False),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/setup",
                json={"username": "admin", "password": "pass"},
            )
        assert resp.status_code == 501

    def test_setup_can_select_no_auth_mode_for_fresh_install(self, tmp_path):
        f = _make_settings(tmp_path, rbac_enabled=False, api_key="")
        with (
            patch("ui.backend.config.CONFIG_FILE", f),
            patch("ui.backend.config.CONFIG_DIR", f.parent),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.RBAC_ENABLED", False),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/auth/setup",
                json={"mode": "none"},
            )
        assert resp.status_code == 201
        saved = json.loads(f.read_text())
        assert saved["auth_mode"] == "none"
        assert saved["rbac_enabled"] is False
        assert saved["api_key"] == ""

    def test_setup_can_transition_persisted_no_auth_back_to_secure_rbac(
        self, tmp_path, auth_engine
    ):
        f = _make_settings(
            tmp_path,
            rbac_enabled=False,
            api_key="",
            auth_mode="none",
            security_setup_completed=True,
        )
        with (
            patch("ui.backend.config.CONFIG_FILE", f),
            patch("ui.backend.config.CONFIG_DIR", f.parent),
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
                    "username": "secure_admin",
                    "password": "securepass",
                },
            )

            whoami_resp = client.get(
                "/api/v1/auth/whoami",
                cookies={
                    "channelwatch_session": resp.cookies.get("channelwatch_session", "")
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["username"] == "secure_admin"
        assert body["csrf_token"]
        assert resp.cookies.get("channelwatch_session")
        saved = json.loads(f.read_text())
        assert saved["auth_mode"] == "rbac"
        assert saved["rbac_enabled"] is True
        assert saved["security_setup_completed"] is True
        assert saved["api_key"] == ""
        assert get_user_by_username(auth_engine, "secure_admin") is not None
        assert whoami_resp.status_code == 200
        assert whoami_resp.json()["username"] == "secure_admin"
        assert whoami_resp.json()["rbac_enabled"] is True


class TestEnvVarBootstrap:
    def test_env_bootstrap_creates_admin_when_no_users(self, auth_engine):
        with (
            patch("ui.backend.main.RBAC_ENABLED", True),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
            patch.dict(
                os.environ, {"CW_ADMIN_USER": "envadmin", "CW_ADMIN_PASS": "envpass"}
            ),
        ):
            from ui.backend.main import _bootstrap_admin_from_env

            _bootstrap_admin_from_env()
        assert get_user_count(auth_engine) == 1
        user = get_user_by_username(auth_engine, "envadmin")
        assert user is not None
        assert user.role == "admin"
        assert user.verify_password("envpass") is True

    def test_env_bootstrap_skips_when_user_exists(self, auth_engine):
        _seed_user(auth_engine, "existing", "pass", "admin")
        with (
            patch("ui.backend.main.RBAC_ENABLED", True),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
            patch.dict(
                os.environ, {"CW_ADMIN_USER": "newadmin", "CW_ADMIN_PASS": "newpass"}
            ),
        ):
            from ui.backend.main import _bootstrap_admin_from_env

            _bootstrap_admin_from_env()
        assert get_user_count(auth_engine) == 1
        assert get_user_by_username(auth_engine, "newadmin") is None

    def test_env_bootstrap_skips_when_vars_absent(self, auth_engine):
        env_without_cw = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CW_ADMIN_USER", "CW_ADMIN_PASS")
        }
        with (
            patch("ui.backend.main.RBAC_ENABLED", True),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
            patch.dict(os.environ, env_without_cw, clear=True),
        ):
            from ui.backend.main import _bootstrap_admin_from_env

            _bootstrap_admin_from_env()
        assert get_user_count(auth_engine) == 0


class TestStorageHelpers:
    def test_get_user_count_empty(self, auth_engine):
        assert get_user_count(auth_engine) == 0

    def test_get_user_count_after_creation(self, auth_engine):
        create_user(auth_engine, "u1", "p1", role="viewer")
        create_user(auth_engine, "u2", "p2", role="admin")
        assert get_user_count(auth_engine) == 2

    def test_create_user_duplicate_raises(self, auth_engine):
        create_user(auth_engine, "dupuser", "pass", role="viewer")
        with pytest.raises(ValueError, match="already exists"):
            create_user(auth_engine, "dupuser", "other", role="admin")

    def test_create_user_role_stored_correctly(self, auth_engine):
        create_user(auth_engine, "optest", "pass", role="operator")
        u = get_user_by_username(auth_engine, "optest")
        assert u is not None
        assert u.role == "operator"
