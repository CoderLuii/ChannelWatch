import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from core.helpers.encryption import (
    FERNET_PREFIX,
    EncryptionKeyUnavailableError,
    bootstrap_encryption_key,
    encrypt_value,
    decrypt_value,
    is_fernet_encrypted,
    encrypt_dvr_api_keys,
    decrypt_dvr_api_keys,
)


class TestFernetHelpers:
    def test_encrypt_produces_fernet_prefix(self, tmp_path):
        key = os.urandom(32)
        result = encrypt_value("secret123", key)
        assert result.startswith(FERNET_PREFIX)
        assert "secret123" not in result

    def test_decrypt_roundtrip(self, tmp_path):
        key = os.urandom(32)
        ciphertext = encrypt_value("secret123", key)
        assert decrypt_value(ciphertext, key) == "secret123"

    def test_decrypt_rejects_plaintext(self):
        key = os.urandom(32)
        with pytest.raises(ValueError):
            decrypt_value("plaintext", key)

    def test_is_fernet_encrypted_true(self):
        assert is_fernet_encrypted("fernet:AAAA") is True

    def test_is_fernet_encrypted_false_for_plaintext(self):
        assert is_fernet_encrypted("plaintext") is False
        assert is_fernet_encrypted("") is False
        assert is_fernet_encrypted(None) is False  # type: ignore[arg-type]

    def test_different_keys_cannot_decrypt(self):
        from cryptography.fernet import InvalidToken

        key_a = os.urandom(32)
        key_b = os.urandom(32)
        ciphertext = encrypt_value("secret", key_a)
        with pytest.raises(InvalidToken):
            decrypt_value(ciphertext, key_b)


class TestEncryptDvrApiKeysList:
    def _key_file(self, tmp_path: Path) -> Path:
        kf = tmp_path / "encryption.key"
        bootstrap_encryption_key(kf)
        return kf

    def test_plaintext_key_is_encrypted(self, tmp_path):
        kf = self._key_file(tmp_path)
        servers = [
            {
                "id": "dvr_aaa",
                "host": "192.168.1.1",
                "port": 8089,
                "api_key": "secret123",
            }
        ]
        result = encrypt_dvr_api_keys(servers, kf)
        assert result[0]["api_key"].startswith(FERNET_PREFIX)
        assert "secret123" not in result[0]["api_key"]

    def test_already_encrypted_not_double_encrypted(self, tmp_path):
        kf = self._key_file(tmp_path)
        servers = [{"id": "dvr_aaa", "api_key": "secret"}]
        once = encrypt_dvr_api_keys(servers, kf)
        twice = encrypt_dvr_api_keys(once, kf)
        assert twice[0]["api_key"] == once[0]["api_key"]

    def test_empty_api_key_not_touched(self, tmp_path):
        kf = self._key_file(tmp_path)
        servers = [{"id": "dvr_aaa", "api_key": ""}, {"id": "dvr_bbb"}]
        result = encrypt_dvr_api_keys(servers, kf)
        assert result[0].get("api_key") == ""
        assert (
            "api_key" not in result[1]
            or result[1].get("api_key") is None
            or result[1].get("api_key") == ""
        )

    def test_auto_creates_key_file_if_missing(self, tmp_path):
        kf = tmp_path / "new_subdir" / "encryption.key"
        servers = [{"id": "dvr_aaa", "api_key": "secret"}]
        result = encrypt_dvr_api_keys(servers, kf)
        assert result[0]["api_key"].startswith(FERNET_PREFIX)
        assert kf.exists()

    def test_decrypt_restores_plaintext(self, tmp_path):
        kf = self._key_file(tmp_path)
        servers = [{"id": "dvr_aaa", "api_key": "secret123"}]
        encrypted = encrypt_dvr_api_keys(servers, kf)
        decrypted = decrypt_dvr_api_keys(encrypted, kf)
        assert decrypted[0]["api_key"] == "secret123"

    def test_decrypt_missing_key_file_returns_unchanged(self, tmp_path):
        kf = self._key_file(tmp_path)
        servers = [{"id": "dvr_aaa", "api_key": "secret"}]
        encrypted = encrypt_dvr_api_keys(servers, kf)
        missing = tmp_path / "gone.key"
        result = decrypt_dvr_api_keys(encrypted, missing)
        assert result[0]["api_key"].startswith(FERNET_PREFIX)


class TestSavedJsonEncryption:
    @pytest.fixture()
    def cfg(self, tmp_path):
        settings = {
            "dvr_servers": [
                {
                    "id": "dvr_aaa",
                    "host": "192.168.1.1",
                    "port": 8089,
                    "name": "Test",
                    "enabled": True,
                    "api_key": "secret123",
                },
            ],
            "api_key": "cw-auth-key",
            "_version": 7,
        }
        cfg_file = tmp_path / "settings.json"
        cfg_file.write_text(json.dumps(settings))
        key_file = tmp_path / "encryption.key"
        bootstrap_encryption_key(key_file)
        return {"file": cfg_file, "dir": tmp_path, "key": key_file}

    def test_saved_json_does_not_contain_plaintext_key(self, cfg):
        from ui.backend.config import load_settings, save_settings

        with (
            patch("ui.backend.config.CONFIG_FILE", cfg["file"]),
            patch("ui.backend.config.CONFIG_DIR", cfg["dir"]),
        ):
            settings = load_settings()
            save_settings(settings)
        raw = json.loads(cfg["file"].read_text())
        servers = raw.get("dvr_servers", [])
        assert servers, "dvr_servers must be non-empty"
        stored_key = servers[0].get("api_key", "")
        assert stored_key.startswith(FERNET_PREFIX), (
            f"Expected fernet: prefix, got: {stored_key!r}"
        )
        assert "secret123" not in stored_key

    def test_roundtrip_preserves_real_value(self, cfg):
        from ui.backend.config import load_settings, save_settings

        with (
            patch("ui.backend.config.CONFIG_FILE", cfg["file"]),
            patch("ui.backend.config.CONFIG_DIR", cfg["dir"]),
        ):
            save_settings(load_settings())
            loaded = load_settings()
        servers = loaded.dvr_servers
        assert servers[0].get("api_key") == "secret123"

    def test_save_settings_raises_when_key_unavailable(self, cfg):
        from ui.backend.config import load_settings, save_settings

        with (
            patch("ui.backend.config.CONFIG_FILE", cfg["file"]),
            patch("ui.backend.config.CONFIG_DIR", cfg["dir"]),
            patch(
                "core.helpers.encryption.bootstrap_encryption_key",
                side_effect=PermissionError("bad perms"),
            ),
        ):
            with pytest.raises(EncryptionKeyUnavailableError):
                save_settings(load_settings())

    def test_migration_encrypts_existing_plaintext_keys(self, tmp_path):
        v6_settings = {
            "dvr_servers": [
                {
                    "id": "dvr_old",
                    "host": "10.0.0.1",
                    "port": 8089,
                    "name": "Old DVR",
                    "enabled": True,
                    "api_key": "plainkey",
                },
            ],
            "_version": 6,
        }
        cfg_file = tmp_path / "settings.json"
        cfg_file.write_text(json.dumps(v6_settings))
        key_file = tmp_path / "encryption.key"
        bootstrap_encryption_key(key_file)

        from core.helpers.migration import migrate_settings
        from core.helpers.encryption import decrypt_dvr_api_keys

        result = migrate_settings(tmp_path, v6_settings.copy())

        stored_key = result["dvr_servers"][0].get("api_key", "")
        assert stored_key.startswith(FERNET_PREFIX), (
            f"Expected encrypted after migration, got: {stored_key!r}"
        )
        assert "plainkey" not in stored_key

        decrypted = decrypt_dvr_api_keys(result["dvr_servers"], key_file)
        assert decrypted[0]["api_key"] == "plainkey"

    def test_migration_leaves_empty_api_key_alone(self, tmp_path):
        v6_settings = {
            "dvr_servers": [
                {"id": "dvr_aaa", "host": "10.0.0.1", "port": 8089, "enabled": True},
            ],
            "_version": 6,
        }
        cfg_file = tmp_path / "settings.json"
        cfg_file.write_text(json.dumps(v6_settings))
        bootstrap_encryption_key(tmp_path / "encryption.key")

        from core.helpers.migration import migrate_settings

        result = migrate_settings(tmp_path, v6_settings.copy())
        stored_key = result["dvr_servers"][0].get("api_key", "")
        assert not is_fernet_encrypted(stored_key)


class TestMaskAndPreserveViaApi:
    @pytest.fixture()
    def api_cfg(self, tmp_path):
        settings = {
            "dvr_servers": [
                {
                    "id": "dvr_aaa",
                    "host": "192.168.1.1",
                    "port": 8089,
                    "name": "Test",
                    "enabled": True,
                    "api_key": "secret123",
                },
            ],
            "api_key": "cw-auth",
            "_version": 7,
        }
        cfg_file = tmp_path / "settings.json"
        cfg_file.write_text(json.dumps(settings))
        key_file = tmp_path / "encryption.key"
        bootstrap_encryption_key(key_file)
        return {"file": cfg_file, "dir": tmp_path}

    @pytest.fixture()
    def client(self, api_cfg):
        with (
            patch("ui.backend.config.CONFIG_FILE", api_cfg["file"]),
            patch("ui.backend.config.CONFIG_DIR", api_cfg["dir"]),
            patch("ui.backend.main.CW_DISABLE_AUTH", True),
        ):
            from starlette.testclient import TestClient
            from ui.backend.main import app

            yield TestClient(app, raise_server_exceptions=True)

    def test_get_masks_dvr_api_key(self, client):
        resp = client.get("/api/settings", headers={"X-API-Key": "cw-auth"})
        assert resp.status_code == 200
        servers = resp.json().get("dvr_servers", [])
        assert servers[0].get("api_key") == "****"

    def test_post_with_masked_sentinel_preserves_real_value(self, client, api_cfg):
        resp = client.get("/api/settings", headers={"X-API-Key": "cw-auth"})
        data = resp.json()
        assert data["dvr_servers"][0]["api_key"] == "****"

        resp2 = client.post("/api/settings", json=data)
        assert resp2.status_code == 200

        raw = json.loads(api_cfg["file"].read_text())
        stored_key = raw["dvr_servers"][0].get("api_key", "")
        assert stored_key.startswith(FERNET_PREFIX)
        assert "secret123" not in stored_key

        from core.helpers.encryption import decrypt_dvr_api_keys, ENCRYPTION_KEY_FILE

        decrypted = decrypt_dvr_api_keys(
            raw["dvr_servers"], api_cfg["dir"] / ENCRYPTION_KEY_FILE.name
        )
        assert decrypted[0]["api_key"] == "secret123"
