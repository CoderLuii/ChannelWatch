import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from starlette.testclient import TestClient
from sqlmodel import SQLModel

from core.storage.database import create_db_engine
from core.storage.models import User, UserSession
from core.storage.auth import (
    hash_password,
    verify_password,
    generate_token,
    create_session,
    get_session_by_token,
    invalidate_session,
    cleanup_expired_sessions,
    get_user_by_username,
    get_user_by_id,
    reset_password,
)


CANONICAL_AUTH_KEYS = (
    "persisted_mode",
    "effective_mode",
    "setup_required",
    "runtime_auth_override_active",
    "api_key_fallback_active",
    "rbac_enabled",
    "session_auth_available",
    "session_setup_required",
)


def _canonical_auth_slice(payload):
    return {key: payload[key] for key in CANONICAL_AUTH_KEYS}


@pytest.fixture(name="auth_engine")
def auth_engine_fixture():
    from sqlalchemy.pool import StaticPool

    engine = create_db_engine("sqlite:///:memory:", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="seeded_user")
def seeded_user_fixture(auth_engine):
    from core.storage.database import get_session as _gs

    u = User(username="alice", password_hash="", role="admin")
    u.set_password("correcthorse")
    with _gs(auth_engine) as s:
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    return {"id": uid, "username": "alice", "password": "correcthorse", "role": "admin"}


class TestBcryptHelpers:
    def test_hash_returns_string(self):
        h = hash_password("secret")
        assert isinstance(h, str) and len(h) > 10

    def test_verify_correct_password(self):
        h = hash_password("correct")
        assert verify_password("correct", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_hashes_differ_for_same_input(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2

    def test_user_set_and_verify_password(self):
        u = User(username="test", password_hash="", role="viewer")
        u.set_password("mypassword")
        assert u.verify_password("mypassword") is True
        assert u.verify_password("wrongpassword") is False


class TestSessionCRUD:
    def test_create_session_returns_token(self, auth_engine, seeded_user):
        sess = create_session(auth_engine, seeded_user["id"])
        assert len(sess.token) == 64
        assert len(sess.csrf_token) == 64
        assert sess.user_id == seeded_user["id"]
        assert sess.expires_at > datetime.now(timezone.utc)

    def test_get_session_by_token(self, auth_engine, seeded_user):
        sess = create_session(auth_engine, seeded_user["id"])
        found = get_session_by_token(auth_engine, sess.token)
        assert found is not None
        assert found.user_id == seeded_user["id"]
        assert found.csrf_token == sess.csrf_token

    def test_missing_token_returns_none(self, auth_engine):
        assert get_session_by_token(auth_engine, "nonexistent" * 4) is None

    def test_expired_session_returns_none(self, auth_engine, seeded_user):
        sess = create_session(auth_engine, seeded_user["id"], expiry_seconds=1)
        from core.storage.database import get_session as _gs

        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        with _gs(auth_engine) as s:
            row = s.get(UserSession, sess.id)
            row.expires_at = past
            s.add(row)
            s.commit()
        assert get_session_by_token(auth_engine, sess.token) is None

    def test_invalidate_session(self, auth_engine, seeded_user):
        sess = create_session(auth_engine, seeded_user["id"])
        result = invalidate_session(auth_engine, sess.token)
        assert result is True
        assert get_session_by_token(auth_engine, sess.token) is None

    def test_invalidate_missing_token_returns_false(self, auth_engine):
        assert invalidate_session(auth_engine, "ghost" * 13) is False

    def test_cleanup_removes_expired(self, auth_engine, seeded_user):
        sess1 = create_session(auth_engine, seeded_user["id"])
        sess2 = create_session(auth_engine, seeded_user["id"])
        from core.storage.database import get_session as _gs

        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        with _gs(auth_engine) as s:
            row = s.get(UserSession, sess2.id)
            row.expires_at = past
            s.add(row)
            s.commit()
        removed = cleanup_expired_sessions(auth_engine)
        assert removed == 1
        assert get_session_by_token(auth_engine, sess1.token) is not None
        assert get_session_by_token(auth_engine, sess2.token) is None

    def test_get_user_by_username(self, auth_engine, seeded_user):
        u = get_user_by_username(auth_engine, "alice")
        assert u is not None
        assert u.username == "alice"
        assert u.role == "admin"

    def test_get_user_by_id(self, auth_engine, seeded_user):
        u = get_user_by_id(auth_engine, seeded_user["id"])
        assert u is not None
        assert u.username == "alice"

    def test_generate_token_length(self):
        t = generate_token()
        assert len(t) == 64
        assert t != generate_token()


def _make_settings_file(
    tmp_path,
    rbac_enabled=False,
    api_key="test-key-123",
    dvr_servers=None,
    security_setup_completed=None,
    auth_mode="",
):
    data = {
        "api_key": api_key,
        "rbac_enabled": rbac_enabled,
        "auth_mode": auth_mode,
        "dvr_servers": dvr_servers or [],
    }
    if security_setup_completed is not None:
        data["security_setup_completed"] = security_setup_completed
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture(name="rbac_off_client")
def rbac_off_client_fixture(tmp_path):
    f = _make_settings_file(tmp_path, rbac_enabled=False, api_key="off-key")
    with (
        patch("ui.backend.config.CONFIG_FILE", f),
        patch("ui.backend.config.CONFIG_DIR", f.parent),
        patch("ui.backend.main.API_KEY_CACHE", "off-key"),
        patch("ui.backend.main.RBAC_ENABLED", False),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from ui.backend import main as backend_main

    with backend_main.rate_limiter._lock:
        backend_main.rate_limiter._requests.clear()
    yield


@pytest.fixture(name="rbac_on_client")
def rbac_on_client_fixture(tmp_path, auth_engine):
    f = _make_settings_file(tmp_path, rbac_enabled=True, api_key="on-key")
    with (
        patch("ui.backend.config.CONFIG_FILE", f),
        patch("ui.backend.config.CONFIG_DIR", f.parent),
        patch("ui.backend.main.API_KEY_CACHE", "on-key"),
        patch("ui.backend.main.RBAC_ENABLED", True),
        patch("ui.backend.main._auth_db_engine", auth_engine),
        patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture(name="rbac_user")
def rbac_user_fixture(auth_engine):
    from core.storage.database import get_session as _gs

    u = User(username="bob", password_hash="", role="viewer")
    u.set_password("hunter2")
    with _gs(auth_engine) as s:
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    return {"id": uid, "username": "bob", "password": "hunter2", "role": "viewer"}


class TestRbacOff:
    def test_api_key_auth_preserved_get(self, rbac_off_client):
        resp = rbac_off_client.get("/api/about", headers={"X-API-Key": "off-key"})
        assert resp.status_code == 200

    def test_no_api_key_rejected(self, rbac_off_client):
        resp = rbac_off_client.get("/api/about")
        assert resp.status_code == 401

    def test_wrong_api_key_rejected(self, rbac_off_client):
        resp = rbac_off_client.get("/api/about", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_whoami_returns_rbac_disabled(self, rbac_off_client):
        resp = rbac_off_client.get("/api/v1/auth/whoami")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rbac_enabled"] is False
        assert data["authenticated"] is False

    def test_login_returns_501_when_rbac_off(self, rbac_off_client):
        resp = rbac_off_client.post(
            "/api/v1/auth/login",
            json={"username": "any", "password": "any"},
        )
        assert resp.status_code == 501

    def test_session_cookie_ignored_when_rbac_off(self, rbac_off_client):
        resp = rbac_off_client.get(
            "/api/about",
            cookies={"channelwatch_session": "fake-token"},
        )
        assert resp.status_code == 401

    def test_setup_and_security_status_never_diverge(self, rbac_off_client):
        setup_resp = rbac_off_client.get("/api/v1/auth/setup-status")
        security_resp = rbac_off_client.get("/api/v1/security/status")

        assert setup_resp.status_code == 200
        assert security_resp.status_code == 200

        setup_data = setup_resp.json()
        security_data = security_resp.json()

        assert _canonical_auth_slice(setup_data) == _canonical_auth_slice(security_data)
        assert setup_data["persisted_mode"] is None
        assert setup_data["effective_mode"] == "api_key"
        assert setup_data["runtime_auth_override_active"] is False

    def test_seeded_dvr_fresh_install_requires_setup(self, tmp_path):
        f = _make_settings_file(
            tmp_path,
            rbac_enabled=False,
            api_key="",
            dvr_servers=[
                {
                    "id": "dvr_seeded",
                    "host": "192.168.1.60",
                    "port": 8089,
                    "name": "Seeded DVR",
                    "enabled": True,
                }
            ],
            security_setup_completed=False,
        )
        with (
            patch("ui.backend.config.CONFIG_FILE", f),
            patch("ui.backend.config.CONFIG_DIR", f.parent),
            patch("ui.backend.main.API_KEY_CACHE", ""),
            patch("ui.backend.main.RBAC_ENABLED", False),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            setup_resp = client.get("/api/v1/auth/setup-status")
            security_resp = client.get("/api/v1/security/status")

        assert setup_resp.status_code == 200
        assert security_resp.status_code == 200

        setup_data = setup_resp.json()
        security_data = security_resp.json()
        assert _canonical_auth_slice(setup_data) == _canonical_auth_slice(security_data)
        assert setup_data["persisted_mode"] is None
        assert setup_data["effective_mode"] == "setup"
        assert setup_data["setup_required"] is True
        assert setup_data["needs_setup"] is True
        assert setup_data["current_mode"] == "setup"


class TestLoginLogoutWhoami:
    def test_login_bad_credentials(self, rbac_on_client, rbac_user):
        resp = rbac_on_client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_login_unknown_user(self, rbac_on_client):
        resp = rbac_on_client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "pass"},
        )
        assert resp.status_code == 401

    def test_login_success_returns_csrf_and_cookie(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        resp = rbac_on_client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "hunter2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "csrf_token" in body
        assert body["username"] == "bob"
        assert body["role"] == "viewer"
        assert len(body["csrf_token"]) == 64
        assert "channelwatch_session" in resp.cookies

    def test_whoami_requires_valid_session(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        login_resp = rbac_on_client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "hunter2"},
        )
        assert login_resp.status_code == 200
        session_token = login_resp.cookies.get("channelwatch_session")
        assert session_token

        whoami_resp = rbac_on_client.get(
            "/api/v1/auth/whoami",
            cookies={"channelwatch_session": session_token},
        )
        assert whoami_resp.status_code == 200
        data = whoami_resp.json()
        assert data["authenticated"] is True
        assert data["username"] == "bob"
        assert data["role"] == "viewer"
        assert data["rbac_enabled"] is True

    def test_whoami_no_cookie_returns_401(self, rbac_on_client):
        resp = rbac_on_client.get("/api/v1/auth/whoami")
        assert resp.status_code == 401

    def test_whoami_bad_token_returns_401(self, rbac_on_client):
        resp = rbac_on_client.get(
            "/api/v1/auth/whoami",
            cookies={"channelwatch_session": "badtoken"},
        )
        assert resp.status_code == 401

    def test_logout_invalidates_session(self, rbac_on_client, rbac_user, auth_engine):
        login_resp = rbac_on_client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "hunter2"},
        )
        session_token = login_resp.cookies.get("channelwatch_session")

        logout_resp = rbac_on_client.post(
            "/api/v1/auth/logout",
            cookies={"channelwatch_session": session_token},
        )
        assert logout_resp.status_code == 200

        whoami_resp = rbac_on_client.get(
            "/api/v1/auth/whoami",
            cookies={"channelwatch_session": session_token},
        )
        assert whoami_resp.status_code == 401


class TestAdminPasswordRecovery:
    def test_password_reset_invalidates_active_sessions(
        self, auth_engine, rbac_admin_user
    ):
        original_session = create_session(auth_engine, rbac_admin_user["id"])

        assert (
            reset_password(auth_engine, rbac_admin_user["username"], "rotatedpass")
            is True
        )

        updated_user = get_user_by_username(auth_engine, rbac_admin_user["username"])
        assert updated_user is not None
        assert updated_user.verify_password("rotatedpass") is True
        assert get_session_by_token(auth_engine, original_session.token) is None

    def test_password_reset_invalidates_existing_whoami_session_cookie(
        self, tmp_path, auth_engine, rbac_admin_user
    ):
        settings_file = _make_settings_file(
            tmp_path,
            rbac_enabled=True,
            api_key="",
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

            login_resp = client.post(
                "/api/v1/auth/login",
                json={
                    "username": rbac_admin_user["username"],
                    "password": rbac_admin_user["password"],
                },
            )
            session_token = login_resp.cookies.get("channelwatch_session")

            before_reset_resp = client.get(
                "/api/v1/auth/whoami",
                cookies={"channelwatch_session": session_token},
            )

            assert (
                reset_password(auth_engine, rbac_admin_user["username"], "rotatedpass")
                is True
            )

            after_reset_resp = client.get(
                "/api/v1/auth/whoami",
                cookies={"channelwatch_session": session_token},
            )

        assert login_resp.status_code == 200
        assert before_reset_resp.status_code == 200
        assert after_reset_resp.status_code == 401

    @pytest.mark.parametrize(
        ("settings_kwargs", "expected_message"),
        [
            (
                {
                    "rbac_enabled": False,
                    "api_key": "",
                    "auth_mode": "",
                    "security_setup_completed": None,
                },
                "Password reset unavailable because secure login setup has not been completed.",
            ),
            (
                {
                    "rbac_enabled": False,
                    "api_key": "shared-key",
                    "auth_mode": "none",
                    "security_setup_completed": True,
                },
                "Password reset unavailable because this install is not using RBAC login.",
            ),
        ],
    )
    def test_reset_admin_password_refuses_noauth_or_setup_required(
        self, tmp_path, auth_engine, capsys, settings_kwargs, expected_message
    ):
        from core.cli.doctor import run

        _make_settings_file(tmp_path, dvr_servers=[], **settings_kwargs)

        with (
            patch("core.cli.doctor.core_config.CONFIG_DIR", tmp_path),
            patch(
                "core.cli.doctor.core_config.CONFIG_FILE", tmp_path / "settings.json"
            ),
            patch("ui.backend.config.CONFIG_FILE", tmp_path / "settings.json"),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
        ):
            with pytest.raises(SystemExit) as excinfo:
                run(
                    [
                        "reset-admin-password",
                        "--username",
                        "admin_dave",
                        "--password",
                        "newpass",
                    ]
                )

        output = capsys.readouterr().out
        assert excinfo.value.code == 1
        assert expected_message in output
        assert (
            "Open the ChannelWatch web UI and finish secure-login setup from the Security page."
            in output
        )


@pytest.fixture(name="rbac_admin_user")
def rbac_admin_user_fixture(auth_engine):
    from core.storage.database import get_session as _gs

    u = User(username="admin_dave", password_hash="", role="admin")
    u.set_password("adminpass")
    with _gs(auth_engine) as s:
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    return {
        "id": uid,
        "username": "admin_dave",
        "password": "adminpass",
        "role": "admin",
    }


@pytest.fixture(name="rbac_operator_user")
def rbac_operator_user_fixture(auth_engine):
    from core.storage.database import get_session as _gs

    u = User(username="ops_olivia", password_hash="", role="operator")
    u.set_password("securepass")
    with _gs(auth_engine) as s:
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    return {
        "id": uid,
        "username": "ops_olivia",
        "password": "securepass",
        "role": "operator",
    }


class TestChangeCredentialsRoute:
    def _login_and_get_tokens(self, client, username, password):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        return resp.cookies.get("channelwatch_session"), resp.json()["csrf_token"]

    def test_change_credentials_accepts_valid_session_and_csrf(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client, rbac_user["username"], rbac_user["password"]
        )

        resp = rbac_on_client.post(
            "/api/v1/auth/change-credentials",
            json={
                "current_password": rbac_user["password"],
                "username": "bob_renamed",
                "new_password": "",
            },
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": csrf_token},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "message": "Credentials updated",
            "username": "bob_renamed",
        }
        assert get_user_by_username(auth_engine, "bob_renamed") is not None

    def test_change_credentials_rejects_missing_or_bad_csrf(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        session_token, _csrf_token = self._login_and_get_tokens(
            rbac_on_client, rbac_user["username"], rbac_user["password"]
        )

        resp = rbac_on_client.post(
            "/api/v1/auth/change-credentials",
            json={
                "current_password": rbac_user["password"],
                "username": "csrf_should_not_apply",
                "new_password": "",
            },
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": "wrong"},
        )

        assert resp.status_code == 403
        assert get_user_by_username(auth_engine, "csrf_should_not_apply") is None

    def test_change_credentials_rejects_invalid_session(
        self, rbac_on_client, rbac_user
    ):
        resp = rbac_on_client.post(
            "/api/v1/auth/change-credentials",
            json={
                "current_password": rbac_user["password"],
                "username": "nobody",
                "new_password": "",
            },
            cookies={"channelwatch_session": "not-a-session"},
            headers={"X-CSRF-Token": "not-a-csrf"},
        )

        assert resp.status_code == 401

    def test_change_credentials_rejects_wrong_current_password_without_mutation(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client, rbac_user["username"], rbac_user["password"]
        )

        resp = rbac_on_client.post(
            "/api/v1/auth/change-credentials",
            json={
                "current_password": "wrongpassword",
                "username": "stolen_session_name",
                "new_password": "newpass",
            },
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": csrf_token},
        )

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "ERR_AUTH_CREDENTIALS_INVALID"
        assert get_user_by_username(auth_engine, "stolen_session_name") is None
        assert get_user_by_username(auth_engine, rbac_user["username"]) is not None

        login_resp = rbac_on_client.post(
            "/api/v1/auth/login",
            json={"username": rbac_user["username"], "password": rbac_user["password"]},
        )
        assert login_resp.status_code == 200


class TestLogEndpointRbac:
    def _login_and_get_tokens(self, client, username, password):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        return resp.cookies.get("channelwatch_session"), resp.json()["csrf_token"]

    def test_viewer_cannot_tail_or_download_logs(
        self, rbac_on_client, rbac_user, tmp_path
    ):
        session_token, _ = self._login_and_get_tokens(
            rbac_on_client, rbac_user["username"], rbac_user["password"]
        )
        log_file = tmp_path / "channelwatch.log"
        log_file.write_text("secret-ish log line\n", encoding="utf-8")

        with patch("ui.backend.main.LOG_FILE", log_file):
            tail_resp = rbac_on_client.get(
                "/api/logs",
                cookies={"channelwatch_session": session_token},
            )
            download_resp = rbac_on_client.get(
                "/api/logs/download",
                cookies={"channelwatch_session": session_token},
            )

        assert tail_resp.status_code == 403
        assert download_resp.status_code == 403

    @pytest.mark.parametrize("fixture_name", ["rbac_operator_user", "rbac_admin_user"])
    def test_operator_and_admin_can_tail_logs(
        self, request, rbac_on_client, fixture_name, tmp_path
    ):
        user = request.getfixturevalue(fixture_name)
        session_token, _ = self._login_and_get_tokens(
            rbac_on_client, user["username"], user["password"]
        )
        log_file = tmp_path / "channelwatch.log"
        log_file.write_text("ops log line\n", encoding="utf-8")

        with patch("ui.backend.main.LOG_FILE", log_file):
            resp = rbac_on_client.get(
                "/api/logs?lines=1",
                cookies={"channelwatch_session": session_token},
            )

        assert resp.status_code == 200
        assert resp.json()["lines"] == ["ops log line"]


class TestPersistedDvrHostSafety:
    def _login_and_get_tokens(self, client, username, password):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        return resp.cookies.get("channelwatch_session"), resp.json()["csrf_token"]

    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "169.254.169.254",
            "metadata.google.internal",
            "http://192.168.1.10",
            "user@example.com",
            "192.168.1.10/path",
            "192.168.1.10?debug=true",
            "192.168.1.10#frag",
            "[fe80::1]",
        ],
    )
    def test_settings_rejects_unsafe_persisted_dvr_hosts(
        self, rbac_on_client, rbac_admin_user, host
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client,
            rbac_admin_user["username"],
            rbac_admin_user["password"],
        )
        resp = rbac_on_client.post(
            "/api/settings",
            json={
                "dvr_servers": [
                    {
                        "id": "bad",
                        "name": "Bad",
                        "host": host,
                        "port": 8089,
                        "enabled": True,
                    }
                ]
            },
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": csrf_token},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_DVR_TEST_TARGET_REJECTED"
        assert "rejected" in detail["message"]

    def test_settings_accepts_lan_persisted_dvr_host(
        self, rbac_on_client, rbac_admin_user
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client,
            rbac_admin_user["username"],
            rbac_admin_user["password"],
        )
        resp = rbac_on_client.post(
            "/api/settings",
            json={
                "dvr_servers": [
                    {
                        "id": "lan",
                        "name": "LAN DVR",
                        "host": "192.168.1.10",
                        "port": 8089,
                        "enabled": True,
                    }
                ]
            },
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": csrf_token},
        )

        assert resp.status_code == 200

    @pytest.mark.parametrize("host", ["2001:db8::1", "[2001:db8::1]"])
    def test_settings_accepts_ipv6_persisted_dvr_hosts(
        self, rbac_on_client, rbac_admin_user, host
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client,
            rbac_admin_user["username"],
            rbac_admin_user["password"],
        )
        resp = rbac_on_client.post(
            "/api/settings",
            json={
                "dvr_servers": [
                    {
                        "id": "ipv6",
                        "name": "IPv6 DVR",
                        "host": host,
                        "port": 8089,
                        "enabled": True,
                    }
                ]
            },
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": csrf_token},
        )

        assert resp.status_code == 200


class _DvrStatusResponse:
    status_code = 200

    def json(self):
        return {"version": "2026.01.01", "FriendlyName": "Public DVR"}


class _DvrStatusClient:
    def __init__(self):
        self.requests = []

    async def get(self, url, timeout):
        self.requests.append((url, timeout))
        return _DvrStatusResponse()


class TestDvrConnectionSecurity:
    def _login_and_get_tokens(self, client, username, password):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        return resp.cookies.get("channelwatch_session"), resp.json()["csrf_token"]

    def test_dvr_test_connection_requires_operator_role(
        self, rbac_on_client, rbac_user, rbac_admin_user
    ):
        viewer_session, viewer_csrf = self._login_and_get_tokens(
            rbac_on_client, rbac_user["username"], rbac_user["password"]
        )
        mock_client = _DvrStatusClient()

        with patch("ui.backend.main._dvr_http_client", mock_client):
            viewer_resp = rbac_on_client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "example.com", "port": 8089},
                cookies={"channelwatch_session": viewer_session},
                headers={"X-CSRF-Token": viewer_csrf},
            )

            admin_session, admin_csrf = self._login_and_get_tokens(
                rbac_on_client,
                rbac_admin_user["username"],
                rbac_admin_user["password"],
            )
            admin_resp = rbac_on_client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "example.com", "port": 8089},
                cookies={"channelwatch_session": admin_session},
                headers={"X-CSRF-Token": admin_csrf},
            )

        assert viewer_resp.status_code == 403
        assert admin_resp.status_code == 200
        assert admin_resp.json()["success"] is True
        assert mock_client.requests == [("http://example.com:8089/status", 8.0)]

    def test_dvr_test_connection_accepts_private_lan_host(
        self, rbac_on_client, rbac_admin_user
    ):
        admin_session, admin_csrf = self._login_and_get_tokens(
            rbac_on_client,
            rbac_admin_user["username"],
            rbac_admin_user["password"],
        )
        mock_client = _DvrStatusClient()

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = rbac_on_client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "192.168.1.1", "port": 8089},
                cookies={"channelwatch_session": admin_session},
                headers={"X-CSRF-Token": admin_csrf},
            )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert mock_client.requests == [("http://192.168.1.1:8089/status", 8.0)]

    @pytest.mark.parametrize("host", ["2001:db8::1", "[2001:db8::1]"])
    def test_dvr_test_connection_accepts_ipv6_hosts(
        self, rbac_on_client, rbac_admin_user, host
    ):
        admin_session, admin_csrf = self._login_and_get_tokens(
            rbac_on_client,
            rbac_admin_user["username"],
            rbac_admin_user["password"],
        )
        mock_client = _DvrStatusClient()

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = rbac_on_client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": host, "port": 8089},
                cookies={"channelwatch_session": admin_session},
                headers={"X-CSRF-Token": admin_csrf},
            )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert mock_client.requests == [("http://[2001:db8::1]:8089/status", 8.0)]

    def test_dvr_test_connection_rejects_link_local_metadata_host(
        self, rbac_on_client, rbac_admin_user
    ):
        admin_session, admin_csrf = self._login_and_get_tokens(
            rbac_on_client,
            rbac_admin_user["username"],
            rbac_admin_user["password"],
        )
        mock_client = _DvrStatusClient()

        with patch("ui.backend.main._dvr_http_client", mock_client):
            resp = rbac_on_client.post(
                "/api/v1/dvrs/test-connection",
                json={"host": "169.254.169.254", "port": 8089},
                cookies={"channelwatch_session": admin_session},
                headers={"X-CSRF-Token": admin_csrf},
            )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_DVR_TEST_TARGET_REJECTED"
        assert detail["message"] == "Test target rejected: host failed safety check"
        assert mock_client.requests == []


class TestCSRF:
    def _login_and_get_tokens(self, client, username, password):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        return resp.cookies.get("channelwatch_session"), resp.json()["csrf_token"]

    def test_csrf_required_for_post_with_session_auth(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client, "bob", "hunter2"
        )
        resp = rbac_on_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            cookies={"channelwatch_session": session_token},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "ERR_AUTH_CSRF_INVALID"

    def test_csrf_valid_token_allows_request_for_admin(
        self, rbac_on_client, rbac_admin_user, auth_engine, tmp_path
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client, "admin_dave", "adminpass"
        )
        resp = rbac_on_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

    def test_csrf_wrong_token_rejected(self, rbac_on_client, rbac_user, auth_engine):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client, "bob", "hunter2"
        )
        resp = rbac_on_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            cookies={"channelwatch_session": session_token},
            headers={"X-CSRF-Token": "wrongtoken"},
        )
        assert resp.status_code == 403

    def test_csrf_token_validation_uses_compare_digest(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        session_token, csrf_token = self._login_and_get_tokens(
            rbac_on_client, "bob", "hunter2"
        )

        with patch(
            "ui.backend.main.secrets.compare_digest",
            side_effect=lambda provided, expected: provided == expected,
        ) as compare_digest:
            resp = rbac_on_client.post(
                "/api/settings",
                json={"dvr_servers": []},
                cookies={"channelwatch_session": session_token},
                headers={"X-CSRF-Token": csrf_token},
            )

        assert resp.status_code == 403
        assert any(
            args == (csrf_token, csrf_token)
            for args, _kwargs in compare_digest.call_args_list
        )

    def test_api_key_auth_skips_csrf_check(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        resp = rbac_on_client.post(
            "/api/settings",
            json={"dvr_servers": []},
            headers={"X-API-Key": "on-key"},
        )
        assert resp.status_code == 200

    def test_get_requests_do_not_require_csrf(
        self, rbac_on_client, rbac_user, auth_engine
    ):
        session_token, _ = self._login_and_get_tokens(rbac_on_client, "bob", "hunter2")
        resp = rbac_on_client.get(
            "/api/v1/auth/whoami",
            cookies={"channelwatch_session": session_token},
        )
        assert resp.status_code == 200


class TestStorageSchemaCompleteness:
    def test_user_and_session_tables_exist(self, auth_engine):
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(auth_engine)
        tables = set(insp.get_table_names())
        assert "user" in tables
        assert "user_session" in tables

    def test_user_session_indexes(self, auth_engine):
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(auth_engine)
        idx = {i["name"] for i in insp.get_indexes("user_session")}
        assert "ix_user_session_token" in idx
        assert "ix_user_session_user_id_expires" in idx
