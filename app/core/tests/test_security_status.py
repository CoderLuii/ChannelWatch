import json
import os
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlmodel import SQLModel
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from core.storage.database import create_db_engine, get_session
from core.storage.models import User


CANONICAL_AUTH_KEYS = (
    "persisted_mode",
    "configured_mode",
    "effective_mode",
    "setup_required",
    "runtime_auth_override_active",
    "api_key_fallback_active",
    "rbac_enabled",
    "session_auth_available",
    "session_setup_required",
)


def _make_settings_file(
    tmp_path,
    *,
    rbac_enabled: bool,
    api_key: str,
    auth_mode: str = "",
    dvr_servers=None,
    security_setup_completed=None,
):
    data = {
        "api_key": api_key,
        "rbac_enabled": rbac_enabled,
        "auth_mode": auth_mode,
        "ics_feed_enabled": False,
        "ics_feed_token": "",
        "rss_feed_enabled": False,
        "rss_feed_token": "",
        "dvr_servers": dvr_servers or [],
    }
    if security_setup_completed is not None:
        data["security_setup_completed"] = security_setup_completed
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(data))
    return settings_file


def _persist_encrypted_dvr_key(
    settings_file,
    *,
    persisted_key: bytes | None = None,
    token_key: bytes | None = None,
    token: str | None = None,
    key_mode: int = 0o600,
):
    persisted_key = persisted_key or os.urandom(32)
    token_key = token_key or persisted_key
    encrypted = token or (
        "fernet:"
        + Fernet(urlsafe_b64encode(token_key))
        .encrypt(b"persisted-dvr-secret")
        .decode("ascii")
    )
    payload = json.loads(settings_file.read_text())
    payload["dvr_servers"] = [{"id": "dvr_main", "api_key": encrypted}]
    settings_file.write_text(json.dumps(payload))
    key_file = settings_file.parent / "encryption.key"
    key_file.write_bytes(persisted_key)
    key_file.chmod(key_mode)
    return key_file


def _canonical_auth_slice(payload):
    return {key: payload[key] for key in CANONICAL_AUTH_KEYS}


@pytest.fixture()
def auth_engine():
    engine = create_db_engine("sqlite:///:memory:", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@contextmanager
def _make_client(
    settings_file,
    *,
    api_key_cache: str,
    rbac_enabled: bool,
    auth_disabled: bool,
    auth_engine=None,
):
    patches = [
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.API_KEY_CACHE", api_key_cache),
        patch("ui.backend.main.RBAC_ENABLED", rbac_enabled),
        patch("ui.backend.main.CW_DISABLE_AUTH", auth_disabled),
    ]
    if auth_engine is not None:
        patches.extend(
            [
                patch("ui.backend.main._auth_db_engine", auth_engine),
                patch("ui.backend.main._ensure_auth_tables", return_value=auth_engine),
            ]
        )

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        if len(patches) > 5:
            with patches[5], patches[6]:
                from ui.backend.main import app

                yield TestClient(app, raise_server_exceptions=False)
        else:
            from ui.backend.main import app

            yield TestClient(app, raise_server_exceptions=False)


class TestSecurityStatusEndpoint:
    @pytest.mark.parametrize(
        ("case_name", "settings_kwargs", "auth_disabled", "seed_user", "expected"),
        [
            (
                "fresh_setup",
                {
                    "rbac_enabled": False,
                    "api_key": "",
                    "auth_mode": "",
                    "dvr_servers": [],
                },
                False,
                False,
                {
                    "persisted_mode": None,
                    "configured_mode": None,
                    "effective_mode": None,
                    "setup_required": True,
                    "runtime_auth_override_active": False,
                    "api_key_fallback_active": False,
                    "rbac_enabled": False,
                    "session_auth_available": False,
                    "session_setup_required": True,
                },
            ),
            (
                "seeded_dvr_fresh_setup",
                {
                    "rbac_enabled": False,
                    "api_key": "",
                    "auth_mode": "",
                    "security_setup_completed": False,
                    "dvr_servers": [
                        {
                            "id": "dvr_seeded",
                            "host": "192.168.1.60",
                            "port": 8089,
                            "name": "Seeded DVR",
                            "enabled": True,
                        }
                    ],
                },
                False,
                False,
                {
                    "persisted_mode": None,
                    "configured_mode": "setup",
                    "effective_mode": "setup",
                    "setup_required": True,
                    "runtime_auth_override_active": False,
                    "api_key_fallback_active": False,
                    "rbac_enabled": False,
                    "session_auth_available": False,
                    "session_setup_required": True,
                },
            ),
            (
                "legacy_api_key_boundary",
                {
                    "rbac_enabled": False,
                    "api_key": "shared-key",
                    "auth_mode": "",
                    "dvr_servers": [],
                },
                False,
                False,
                {
                    "persisted_mode": None,
                    "configured_mode": "api_key",
                    "effective_mode": "api_key",
                    "setup_required": False,
                    "runtime_auth_override_active": False,
                    "api_key_fallback_active": False,
                    "rbac_enabled": False,
                    "session_auth_available": False,
                    "session_setup_required": False,
                },
            ),
            (
                "persisted_no_auth",
                {
                    "rbac_enabled": False,
                    "api_key": "shared-key",
                    "auth_mode": "none",
                    "security_setup_completed": True,
                    "dvr_servers": [],
                },
                False,
                False,
                {
                    "persisted_mode": "none",
                    "configured_mode": "none",
                    "effective_mode": "none",
                    "setup_required": False,
                    "runtime_auth_override_active": False,
                    "api_key_fallback_active": False,
                    "rbac_enabled": False,
                    "session_auth_available": False,
                    "session_setup_required": False,
                },
            ),
            (
                "rbac_ready",
                {
                    "rbac_enabled": True,
                    "api_key": "",
                    "auth_mode": "rbac",
                    "security_setup_completed": True,
                    "dvr_servers": [],
                },
                False,
                True,
                {
                    "persisted_mode": "rbac",
                    "configured_mode": "rbac",
                    "effective_mode": "rbac",
                    "setup_required": False,
                    "runtime_auth_override_active": False,
                    "api_key_fallback_active": False,
                    "rbac_enabled": True,
                    "session_auth_available": True,
                    "session_setup_required": False,
                },
            ),
            (
                "runtime_override_break_glass",
                {
                    "rbac_enabled": True,
                    "api_key": "",
                    "auth_mode": "rbac",
                    "security_setup_completed": True,
                    "dvr_servers": [],
                },
                True,
                True,
                {
                    "persisted_mode": "rbac",
                    "configured_mode": "rbac",
                    "effective_mode": "none",
                    "setup_required": False,
                    "runtime_auth_override_active": True,
                    "api_key_fallback_active": False,
                    "rbac_enabled": True,
                    "session_auth_available": True,
                    "session_setup_required": False,
                },
            ),
        ],
    )
    def test_canonical_auth_state_matrix(
        self,
        tmp_path,
        auth_engine,
        case_name,
        settings_kwargs,
        auth_disabled,
        seed_user,
        expected,
    ):
        settings_file = _make_settings_file(tmp_path, **settings_kwargs)
        if seed_user:
            with get_session(auth_engine) as session:
                user = User(
                    username=f"{case_name}_user", password_hash="", role="admin"
                )
                user.set_password("correcthorse")
                session.add(user)
                session.commit()

        with _make_client(
            settings_file,
            api_key_cache=settings_kwargs["api_key"],
            rbac_enabled=settings_kwargs["rbac_enabled"],
            auth_disabled=auth_disabled,
            auth_engine=auth_engine,
        ) as client:
            setup_response = client.get("/api/v1/auth/setup-status")
            security_response = client.get("/api/v1/security/status")

        assert setup_response.status_code == 200
        assert security_response.status_code == 200

        setup_body = setup_response.json()
        security_body = security_response.json()

        assert _canonical_auth_slice(setup_body) == expected
        assert _canonical_auth_slice(security_body) == expected
        assert setup_body["needs_setup"] is expected["setup_required"]

    def test_reports_api_key_only_when_rbac_disabled(self, tmp_path):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )

        with _make_client(
            settings_file,
            api_key_cache="shared-key",
            rbac_enabled=False,
            auth_disabled=False,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        body = response.json()
        assert body["security_mode"] == "API_KEY_ONLY"
        assert body["rbac_enabled"] is False
        assert body["api_key_configured"] is True
        assert body["api_key_fallback_active"] is False
        assert body["session_auth_available"] is False
        assert body["session_setup_required"] is False
        assert body["feeds"] == {
            "implemented": True,
            "ics_enabled": False,
            "rss_enabled": False,
            "atom_enabled": False,
        }

    def test_reports_activity_feed_status_when_rss_atom_token_is_configured(
        self, tmp_path
    ):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )
        payload = json.loads(settings_file.read_text())
        payload["rss_feed_enabled"] = True
        payload["rss_feed_token"] = "activity-feed-token"
        settings_file.write_text(json.dumps(payload))

        with _make_client(
            settings_file,
            api_key_cache="shared-key",
            rbac_enabled=False,
            auth_disabled=False,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        body = response.json()
        assert body["feeds"]["rss_enabled"] is True
        assert body["feeds"]["atom_enabled"] is True

    def test_reports_encryption_at_rest_false_for_plaintext_dvr_keys(self, tmp_path):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )
        payload = json.loads(settings_file.read_text())
        payload["dvr_servers"] = [{"id": "dvr_main", "api_key": "plaintext-key"}]
        settings_file.write_text(json.dumps(payload))

        with _make_client(
            settings_file,
            api_key_cache="shared-key",
            rbac_enabled=False,
            auth_disabled=False,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        assert response.json()["encrypted_dvr_api_keys_at_rest"] is False

    def test_reports_encryption_at_rest_true_for_valid_persisted_ciphertext(
        self, tmp_path
    ):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )
        _persist_encrypted_dvr_key(settings_file)

        with _make_client(
            settings_file,
            api_key_cache="shared-key",
            rbac_enabled=False,
            auth_disabled=False,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        assert response.json()["encrypted_dvr_api_keys_at_rest"] is True

    @pytest.mark.parametrize(
        ("case", "key_mode"),
        [
            ("corrupt", 0o600),
            ("missing", 0o600),
            ("short", 0o600),
            ("wrong", 0o600),
        ],
    )
    def test_reports_encryption_at_rest_false_for_invalid_persisted_state(
        self, tmp_path, case, key_mode
    ):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )
        if case == "corrupt":
            _persist_encrypted_dvr_key(
                settings_file,
                token="fernet:not-a-valid-token",
            )
        elif case == "short":
            _persist_encrypted_dvr_key(
                settings_file,
                persisted_key=b"x" * 31,
                token_key=os.urandom(32),
            )
        elif case == "wrong":
            _persist_encrypted_dvr_key(
                settings_file,
                persisted_key=os.urandom(32),
                token_key=os.urandom(32),
            )
        else:
            key_file = _persist_encrypted_dvr_key(
                settings_file,
                key_mode=key_mode,
            )
            if case == "missing":
                key_file.unlink()

        with _make_client(
            settings_file,
            api_key_cache="shared-key",
            rbac_enabled=False,
            auth_disabled=False,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        assert response.json()["encrypted_dvr_api_keys_at_rest"] is False

    def test_encryption_at_rest_rejects_loose_key_permissions(self, tmp_path):
        from ui.backend import main as backend_main

        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )
        _persist_encrypted_dvr_key(settings_file, key_mode=0o644)

        with patch("ui.backend.config.CONFIG_FILE", settings_file):
            assert backend_main._dvr_api_keys_encrypted_at_rest() is False

    @pytest.mark.parametrize("unsafe_path", ["settings", "key"])
    def test_encryption_at_rest_rejects_unsafe_paths(
        self, tmp_path, unsafe_path
    ):
        from ui.backend import main as backend_main

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        real_settings = _make_settings_file(
            real_dir, rbac_enabled=False, api_key="shared-key"
        )
        _persist_encrypted_dvr_key(real_settings)
        settings_file = real_settings
        if unsafe_path == "settings":
            settings_file = tmp_path / "settings.json"
            settings_file.symlink_to(real_settings)
        else:
            key_file = real_settings.parent / "encryption.key"
            key_target = real_settings.parent / "real-encryption.key"
            key_file.rename(key_target)
            key_file.symlink_to(key_target)

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        ):
            assert backend_main._dvr_api_keys_encrypted_at_rest() is False

    def test_encryption_at_rest_rejects_malformed_persisted_settings(self, tmp_path):
        from ui.backend import main as backend_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text("not-json")
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
        ):
            assert backend_main._dvr_api_keys_encrypted_at_rest() is False

    def test_encryption_at_rest_is_vacuously_true_without_dvr_credentials(
        self, tmp_path
    ):
        from ui.backend import main as backend_main

        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )
        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
        ):
            assert backend_main._dvr_api_keys_encrypted_at_rest() is True

    def test_reports_rbac_with_api_key_fallback_when_both_exist(
        self, tmp_path, auth_engine
    ):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=True, api_key="shared-key"
        )

        with _make_client(
            settings_file,
            api_key_cache="shared-key",
            rbac_enabled=True,
            auth_disabled=False,
            auth_engine=auth_engine,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        body = response.json()
        assert body["security_mode"] == "RBAC_WITH_API_KEY_FALLBACK"
        assert body["rbac_enabled"] is True
        assert body["api_key_configured"] is True
        assert body["api_key_fallback_active"] is True
        assert body["session_auth_available"] is True
        assert body["session_setup_required"] is True

    def test_reports_rbac_only_when_shared_api_key_is_blank(
        self, tmp_path, auth_engine
    ):
        settings_file = _make_settings_file(tmp_path, rbac_enabled=True, api_key="")
        with get_session(auth_engine) as session:
            user = User(username="alice", password_hash="", role="admin")
            user.set_password("correcthorse")
            session.add(user)
            session.commit()

        with _make_client(
            settings_file,
            api_key_cache="",
            rbac_enabled=True,
            auth_disabled=False,
            auth_engine=auth_engine,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        body = response.json()
        assert body["security_mode"] == "RBAC_ONLY"
        assert body["api_key_configured"] is False
        assert body["api_key_fallback_active"] is False
        assert body["session_auth_available"] is True
        assert body["session_setup_required"] is False

    def test_reports_auth_disabled_override_explicitly(self, tmp_path):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=False, api_key="shared-key"
        )

        with _make_client(
            settings_file,
            api_key_cache="shared-key",
            rbac_enabled=False,
            auth_disabled=True,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        body = response.json()
        assert body["security_mode"] == "API_KEY_ONLY"
        assert body["auth_disabled"] is True
        assert body["persisted_mode"] is None
        assert body["configured_mode"] == "api_key"
        assert body["effective_mode"] == "none"
        assert body["runtime_auth_override_active"] is True

    def test_disable_auth_runtime_override_does_not_persist(
        self, tmp_path, auth_engine
    ):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=True, api_key="", auth_mode="rbac"
        )
        with get_session(auth_engine) as session:
            user = User(username="operator", password_hash="", role="admin")
            user.set_password("correcthorse")
            session.add(user)
            session.commit()

        with _make_client(
            settings_file,
            api_key_cache="",
            rbac_enabled=True,
            auth_disabled=True,
            auth_engine=auth_engine,
        ) as client:
            response = client.get("/api/v1/security/status")

        assert response.status_code == 200
        body = response.json()
        persisted_settings = json.loads(settings_file.read_text())

        assert body["persisted_mode"] == "rbac"
        assert body["configured_mode"] == "rbac"
        assert body["effective_mode"] == "none"
        assert body["runtime_auth_override_active"] is True
        assert body["security_mode"] == "RBAC_ONLY"
        assert body["auth_disabled"] is True
        assert persisted_settings["auth_mode"] == "rbac"
        assert persisted_settings["rbac_enabled"] is True
        assert persisted_settings["api_key"] == ""

    def test_disable_auth_override_restores_prior_mode_after_restart(
        self, tmp_path, auth_engine
    ):
        settings_file = _make_settings_file(
            tmp_path, rbac_enabled=True, api_key="", auth_mode="rbac"
        )
        with get_session(auth_engine) as session:
            user = User(username="admin", password_hash="", role="admin")
            user.set_password("correcthorse")
            session.add(user)
            session.commit()

        with _make_client(
            settings_file,
            api_key_cache="",
            rbac_enabled=True,
            auth_disabled=True,
            auth_engine=auth_engine,
        ) as override_client:
            override_response = override_client.get("/api/v1/security/status")

        with _make_client(
            settings_file,
            api_key_cache="",
            rbac_enabled=True,
            auth_disabled=False,
            auth_engine=auth_engine,
        ) as restarted_client:
            restarted_response = restarted_client.get("/api/v1/security/status")

        assert override_response.status_code == 200
        assert restarted_response.status_code == 200

        override_body = override_response.json()
        restarted_body = restarted_response.json()

        assert override_body["configured_mode"] == "rbac"
        assert override_body["effective_mode"] == "none"
        assert override_body["runtime_auth_override_active"] is True

        assert restarted_body["persisted_mode"] == "rbac"
        assert restarted_body["configured_mode"] == "rbac"
        assert restarted_body["effective_mode"] == "rbac"
        assert restarted_body["runtime_auth_override_active"] is False
        assert restarted_body["security_mode"] == "RBAC_ONLY"
        assert restarted_body["api_key_fallback_active"] is False
        assert restarted_body["auth_disabled"] is False
