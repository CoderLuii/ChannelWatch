import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from core.helpers.atomic_io import _atomic_read_secret_bytes


def _patch_config_paths(tmp_path):
    settings_file = tmp_path / "settings.json"
    return [
        patch("core.helpers.config.CONFIG_DIR", tmp_path),
        patch("core.helpers.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("core.cli.doctor.core_config.CONFIG_DIR", tmp_path),
        patch("core.cli.doctor.core_config.CONFIG_FILE", settings_file),
    ]


class _PatchStack:
    def __init__(self, patchers):
        self.patchers = patchers

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        for patcher in reversed(self.patchers):
            patcher.stop()
        return False


class TestDoctorCliParser:
    def test_parser_has_new_subcommands(self):
        from core.cli.doctor import build_parser

        parser = build_parser()

        assert parser.parse_args(["diagnose"]).command == "diagnose"
        assert parser.parse_args(["config-check"]).command == "config-check"
        assert parser.parse_args(["runtime-info"]).command == "runtime-info"
        assert (
            parser.parse_args(["rotate-encryption-key"]).command
            == "rotate-encryption-key"
        )
        assert (
            parser.parse_args(["reset-admin-password", "--username", "admin"]).command
            == "reset-admin-password"
        )


class TestDoctorRuntimeInfo:
    def test_runtime_info_reports_image_process_and_verified_generation(
        self, tmp_path, capsys, monkeypatch
    ):
        from core.cli.doctor import run

        process = {
            "pid": 123,
            "python_executable": "/venv/bin/python",
            "python_version": "3.14.8",
            "python_prefix": "/venv",
            "dependencies": {"requests": {"loaded": True, "path": "/venv/requests.py"}},
        }
        claim = {
            "path": "/config/channelwatch-runtime/releases/v1.5.4",
            "runtime_generation_id": "a" * 64,
            "runtime_descriptor_sha256": "b" * 64,
            "dependency_inventory_sha256": "c" * 64,
        }
        monkeypatch.setenv("CHANNELWATCH_IMAGE_APP_DIR", str(tmp_path / "image"))
        with (
            patch("core.cli.doctor.core.__file__", str(tmp_path / "image/core/__init__.py")),
            patch("core.cli.doctor.core.__version__", "1.5.3"),
            patch("core.cli.doctor.runtime_process_diagnostics", return_value=process),
            patch("core.cli.doctor.get_verified_runtime_claim", return_value=claim),
        ):
            run(["runtime-info"])

        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "diagnostic_source": "image",
            "application_dir": str((tmp_path / "image").resolve()),
            "application_version": "1.5.3",
            "process_runtime": process,
            "verified_generation": claim,
        }

    def test_runtime_info_does_not_infer_generation_from_selected_app(
        self, tmp_path, capsys, monkeypatch
    ):
        from core.cli.doctor import core as loaded_core
        from core.cli.doctor import run

        monkeypatch.setenv(
            "CHANNELWATCH_IMAGE_APP_DIR",
            str(Path(loaded_core.__file__).resolve().parents[1]),
        )
        monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(tmp_path / "selected"))
        with (
            patch("core.cli.doctor.runtime_process_diagnostics", return_value={}),
            patch("core.cli.doctor.get_verified_runtime_claim", return_value=None),
        ):
            run(["runtime-info"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["diagnostic_source"] == "image"
        assert payload["verified_generation"] is None
        assert "selected" not in json.dumps(payload)

    def test_runtime_info_labels_loaded_selected_application_origin(
        self, tmp_path, capsys, monkeypatch
    ):
        from core.cli.doctor import run

        image_app = tmp_path / "image"
        selected_app = tmp_path / "runtime" / "releases" / "v1.5.4"
        monkeypatch.setenv("CHANNELWATCH_IMAGE_APP_DIR", str(image_app))
        monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(selected_app))
        with (
            patch(
                "core.cli.doctor.core.__file__",
                str(selected_app / "core" / "__init__.py"),
            ),
            patch("core.cli.doctor.core.__version__", "1.5.4"),
            patch("core.cli.doctor.runtime_process_diagnostics", return_value={}),
            patch("core.cli.doctor.get_verified_runtime_claim", return_value=None),
        ):
            run(["runtime-info"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["diagnostic_source"] == "selected_application"
        assert payload["application_dir"] == str(selected_app.resolve())
        assert payload["application_version"] == "1.5.4"


class TestDoctorDiagnose:
    def test_diagnose_successfully_checks_dvr(self, tmp_path, capsys):
        from core.cli.doctor import run

        settings = {
            "_version": 7,
            "dvr_servers": [
                {
                    "id": "dvr_1",
                    "name": "Main DVR",
                    "host": "10.0.0.10",
                    "port": 8089,
                    "enabled": True,
                    "api_key": "secret",
                }
            ],
        }
        (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        status = MagicMock(status_code=200)
        status.json.return_value = {"version": "2026.02.09"}
        auth = MagicMock(status_code=200)

        with (
            _PatchStack(_patch_config_paths(tmp_path)),
            patch("core.cli.doctor.check_server_connectivity", return_value=True),
            patch("core.cli.doctor.httpx.get", side_effect=[status, auth]),
        ):
            run(["diagnose"])

        output = capsys.readouterr().out
        assert "Checking Main DVR (10.0.0.10:8089)..." in output
        assert "OK version: 2026.02.09" in output
        assert "OK auth: Auth check passed" in output
        assert "Diagnosis completed successfully for 1 DVR(s)." in output

    def test_diagnose_reports_actionable_failure_hints(self, tmp_path, capsys):
        from core.cli.doctor import run

        settings = {
            "_version": 7,
            "dvr_servers": [
                {
                    "id": "dvr_1",
                    "name": "Bridge DVR",
                    "host": "localhost",
                    "port": 8089,
                    "enabled": True,
                },
                {
                    "id": "dvr_2",
                    "name": "Auth DVR",
                    "host": "10.0.0.20",
                    "port": 8089,
                    "enabled": True,
                    "api_key": "bad-key",
                },
            ],
        }
        (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        status = MagicMock(status_code=200)
        status.json.return_value = {"version": "2026.02.09"}
        auth = MagicMock(status_code=403)

        with (
            _PatchStack(_patch_config_paths(tmp_path)),
            patch(
                "core.cli.doctor.check_server_connectivity", side_effect=[False, True]
            ),
            patch("core.cli.doctor.httpx.get", side_effect=[status, auth]),
        ):
            with pytest.raises(SystemExit, match="1"):
                run(["diagnose"])

        output = capsys.readouterr().out
        assert "Use the DVR's LAN IP or host.docker.internal instead." in output
        assert "re-enter the DVR API key in Settings" in output
        assert "Diagnosis completed with 2 issue(s)." in output


class TestDoctorConfigCheck:
    def test_config_check_uses_real_loaders(self, tmp_path, capsys):
        from core.cli.doctor import run

        settings = {
            "_version": 7,
            "dvr_servers": [
                {"id": "dvr_1", "host": "10.0.0.10", "port": 8089, "enabled": True}
            ],
        }
        (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        with _PatchStack(_patch_config_paths(tmp_path)):
            run(["config-check"])

        output = capsys.readouterr().out
        assert "Config check passed:" in output
        assert "core loader accepted 1 DVR(s)" in output
        assert "UI schema accepted 1 DVR(s)" in output

    def test_config_check_surfaces_fail_closed_errors(self, tmp_path, capsys):
        from core.cli.doctor import run

        (tmp_path / "settings.json").write_text('{"dvr_servers": ', encoding="utf-8")

        with _PatchStack(_patch_config_paths(tmp_path)):
            with pytest.raises(SystemExit, match="1"):
                run(["config-check"])

        output = capsys.readouterr().out
        assert "Config check failed:" in output
        assert "restore /config/settings.json from /config/backups" in output


class TestDoctorRotateEncryptionKey:
    def test_rotate_encryption_key_reencrypts_all_dvr_api_keys(self, tmp_path, capsys):
        from core.cli.doctor import run
        from core.helpers.encryption import (
            bootstrap_encryption_key,
            decrypt_dvr_api_keys,
            encrypt_dvr_api_keys,
        )

        key_file = tmp_path / "encryption.key"
        old_key = bootstrap_encryption_key(key_file)
        encrypted_servers = encrypt_dvr_api_keys(
            [
                {
                    "id": "dvr_1",
                    "host": "10.0.0.10",
                    "port": 8089,
                    "enabled": True,
                    "api_key": "secret-one",
                },
                {
                    "id": "dvr_2",
                    "host": "10.0.0.11",
                    "port": 8089,
                    "enabled": True,
                    "api_key": "secret-two",
                },
            ],
            key_file,
        )
        old_ciphertexts = [server["api_key"] for server in encrypted_servers]
        (tmp_path / "settings.json").write_text(
            json.dumps({"_version": 7, "dvr_servers": encrypted_servers}),
            encoding="utf-8",
        )

        with _PatchStack(_patch_config_paths(tmp_path)):
            run(["rotate-encryption-key"])

        output = capsys.readouterr().out
        assert (
            "Encryption key rotated successfully. Re-encrypted 2 protected credential(s)."
            in output
        )

        new_key = _atomic_read_secret_bytes(key_file)
        assert new_key != old_key
        recovery_keys = list(
            (tmp_path / "backups" / "key-recovery").glob(
                "encryption-key-rotation-*/encryption.key"
            )
        )
        assert len(recovery_keys) == 1
        if os.name != "nt":
            assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
            assert stat.S_IMODE(recovery_keys[0].stat().st_mode) == 0o600

        raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        new_ciphertexts = [server["api_key"] for server in raw["dvr_servers"]]
        assert new_ciphertexts != old_ciphertexts

        decrypted = decrypt_dvr_api_keys(raw["dvr_servers"], key_file)
        assert [server["api_key"] for server in decrypted] == [
            "secret-one",
            "secret-two",
        ]

    def test_rotate_encryption_key_restores_old_key_when_settings_write_fails(
        self, tmp_path
    ):
        from core.cli.doctor import run
        from core.helpers.encryption import (
            bootstrap_encryption_key,
            decrypt_dvr_api_keys,
            encrypt_dvr_api_keys,
        )

        key_file = tmp_path / "encryption.key"
        old_key = bootstrap_encryption_key(key_file)
        encrypted_servers = encrypt_dvr_api_keys(
            [
                {
                    "id": "dvr_1",
                    "host": "10.0.0.10",
                    "port": 8089,
                    "enabled": True,
                    "api_key": "secret-one",
                }
            ],
            key_file,
        )
        original_settings = {"_version": 7, "dvr_servers": encrypted_servers}
        (tmp_path / "settings.json").write_text(
            json.dumps(original_settings),
            encoding="utf-8",
        )

        def fail_write(*_args, **_kwargs):
            raise RuntimeError("settings write failed")

        with (
            _PatchStack(_patch_config_paths(tmp_path)),
            patch(
                "core.helpers.credential_maintenance.replace_config_files_transactionally",
                side_effect=fail_write,
            ),
        ):
            with pytest.raises(RuntimeError, match="settings write failed"):
                run(["rotate-encryption-key"])

        assert _atomic_read_secret_bytes(key_file) == old_key
        recovery_keys = list(
            (tmp_path / "backups" / "key-recovery").glob(
                "encryption-key-rotation-*/encryption.key"
            )
        )
        assert len(recovery_keys) == 1
        if os.name != "nt":
            assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
            assert stat.S_IMODE(recovery_keys[0].stat().st_mode) == 0o600
        raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        decrypted = decrypt_dvr_api_keys(raw["dvr_servers"], key_file)
        assert [server["api_key"] for server in decrypted] == ["secret-one"]


class TestDoctorResetAdminPassword:
    def test_reset_admin_password_updates_existing_user(self, tmp_path, capsys):
        from core.cli.doctor import run
        from core.storage.database import create_db_engine
        from sqlmodel import SQLModel
        from core.storage.auth import (
            create_user,
            create_session,
            get_session_by_token,
            get_user_by_username,
        )

        settings = {
            "_version": 7,
            "dvr_servers": [],
            "rbac_enabled": True,
            "auth_mode": "rbac",
        }
        (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        engine = create_db_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        created = create_user(engine, "admin", "oldpass", role="admin")
        session = create_session(engine, created.id)

        with (
            _PatchStack(_patch_config_paths(tmp_path)),
            patch("ui.backend.main._ensure_auth_tables", return_value=engine),
        ):
            run(
                ["reset-admin-password", "--username", "admin", "--password", "newpass"]
            )

        output = capsys.readouterr().out
        assert "Password reset successful for admin." in output
        assert "Password reset to the provided value." in output
        assert "newpass" not in output
        user = get_user_by_username(engine, "admin")
        assert user.verify_password("newpass") is True
        assert get_session_by_token(engine, session.token) is None

    def test_reset_admin_password_prompts_without_printing_password(
        self, tmp_path, capsys
    ):
        from core.cli.doctor import run
        from core.storage.database import create_db_engine
        from sqlmodel import SQLModel
        from core.storage.auth import create_user, get_user_by_username

        settings = {
            "_version": 7,
            "dvr_servers": [],
            "rbac_enabled": True,
            "auth_mode": "rbac",
        }
        (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        engine = create_db_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        create_user(engine, "admin", "oldpass", role="admin")

        with (
            _PatchStack(_patch_config_paths(tmp_path)),
            patch("ui.backend.main._ensure_auth_tables", return_value=engine),
            patch("core.cli.doctor.getpass.getpass", side_effect=["promptpass", "promptpass"]),
        ):
            run(["reset-admin-password", "--username", "admin"])

        output = capsys.readouterr().out
        assert "Password reset successful for admin." in output
        assert "promptpass" not in output
        user = get_user_by_username(engine, "admin")
        assert user.verify_password("promptpass") is True
