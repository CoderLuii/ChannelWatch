"""Tests for: backup / restore helpers and endpoints."""

import io
import json
import os
import stat
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_config_dir(tmp_path: Path, *, schema_version: int = 7) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = {"_version": schema_version, "dvr_servers": [], "tz": "UTC"}
    (config_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (config_dir / "channelwatch.db").write_bytes(b"SQLite data placeholder")
    (config_dir / "session_state_dvr_abc.json").write_text('{"last_seen": 1}')
    (config_dir / "session_state_dvr_xyz.json").write_text('{"last_seen": 2}')
    (config_dir / "encryption.key").write_bytes(b"\xde\xad\xbe\xef" * 8)
    return config_dir


class TestCreateBackupZip:
    def test_zip_contains_settings_json(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        data = create_backup_zip(config_dir)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(n.endswith("/settings.json") for n in names)

    def test_zip_contains_channelwatch_db(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        data = create_backup_zip(config_dir)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(n.endswith("/channelwatch.db") for n in names)

    def test_zip_contains_all_session_state_files(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        data = create_backup_zip(config_dir)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(n.endswith("/session_state_dvr_abc.json") for n in names)
        assert any(n.endswith("/session_state_dvr_xyz.json") for n in names)

    def test_encryption_key_in_sensitive_subfolder(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, _SENSITIVE_SUBFOLDER

        config_dir = _make_config_dir(tmp_path)
        data = create_backup_zip(config_dir)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(f"/{_SENSITIVE_SUBFOLDER}/encryption.key" in n for n in names)

    def test_security_warning_alongside_encryption_key(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, _SENSITIVE_SUBFOLDER

        config_dir = _make_config_dir(tmp_path)
        data = create_backup_zip(config_dir)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert any(f"/{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING" in n for n in names)

    def test_backup_manifest_present_and_valid(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        data = create_backup_zip(config_dir)
        zf = zipfile.ZipFile(io.BytesIO(data))
        manifest_names = [
            n for n in zf.namelist() if n.endswith("/backup_manifest.json")
        ]
        assert len(manifest_names) == 1
        manifest = json.loads(zf.read(manifest_names[0]))
        assert isinstance(manifest["settings_schema_version"], int)
        assert isinstance(manifest["backup_schema_version"], int)
        assert isinstance(manifest["files"], list)

    def test_manifest_records_settings_schema_version(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path, schema_version=7)
        data = create_backup_zip(config_dir)
        zf = zipfile.ZipFile(io.BytesIO(data))
        manifest_name = next(
            n for n in zf.namelist() if n.endswith("/backup_manifest.json")
        )
        manifest = json.loads(zf.read(manifest_name))
        assert manifest["settings_schema_version"] == 7

    def test_encryption_key_bytes_preserved(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        original_key = (config_dir / "encryption.key").read_bytes()
        data = create_backup_zip(config_dir)
        zf = zipfile.ZipFile(io.BytesIO(data))
        key_name = next(n for n in zf.namelist() if n.endswith("/encryption.key"))
        assert zf.read(key_name) == original_key

    def test_missing_optional_files_do_not_fail(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings = {"_version": 7, "dvr_servers": [], "tz": "UTC"}
        (config_dir / "settings.json").write_text(json.dumps(settings))
        data = create_backup_zip(config_dir)
        assert zipfile.is_zipfile(io.BytesIO(data))


def _corrupt_zip_member_data(zip_bytes: bytes) -> bytes:
    import struct

    ba = bytearray(zip_bytes)
    sig = b"PK\x03\x04"
    idx = ba.find(sig)
    if idx == -1:
        raise ValueError("No local file entry found")
    fname_len = struct.unpack_from("<H", ba, idx + 26)[0]
    extra_len = struct.unpack_from("<H", ba, idx + 28)[0]
    data_start = idx + 30 + fname_len + extra_len
    compressed_size = struct.unpack_from("<I", ba, idx + 18)[0]
    if compressed_size > 0 and data_start < len(ba):
        ba[data_start] ^= 0xFF
    return bytes(ba)


class TestValidateRestoreZip:
    def _make_zip(
        self,
        *,
        settings_schema_version: int = 7,
        include_settings: bool = True,
        include_manifest: bool = True,
        corrupt_manifest: bool = False,
        corrupt_zip: bool = False,
        extra_members: dict[str, str] | None = None,
    ) -> bytes:
        if corrupt_zip:
            return b"not a zip"

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            if include_manifest:
                manifest = {
                    "backup_schema_version": 1,
                    "settings_schema_version": settings_schema_version,
                    "created_at": "20260420T000000Z",
                    "created_by": "test",
                    "files": ["settings.json"],
                }
                zf.writestr(
                    f"{prefix}/backup_manifest.json",
                    b"not json" if corrupt_manifest else json.dumps(manifest).encode(),
                )
            if include_settings:
                zf.writestr(
                    f"{prefix}/settings.json",
                    json.dumps({"_version": settings_schema_version}),
                )
            for name, contents in (extra_members or {}).items():
                zf.writestr(name, contents)
        return buf.getvalue()

    def test_valid_zip_returns_manifest(self):
        from ui.backend.backup_restore import validate_restore_zip

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            manifest = validate_restore_zip(self._make_zip(settings_schema_version=7))
        assert manifest["settings_schema_version"] == 7

    def test_rejects_corrupt_zip(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with pytest.raises(RestoreValidationError, match="valid zip"):
            validate_restore_zip(self._make_zip(corrupt_zip=True))

    def test_rejects_upload_over_archive_size_limit(self):
        from ui.backend import backup_restore
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch.object(backup_restore, "MAX_RESTORE_ARCHIVE_BYTES", 10):
            with pytest.raises(RestoreValidationError, match="upload size limit"):
                validate_restore_zip(b"x" * 11)

    def test_rejects_missing_manifest(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with pytest.raises(
            RestoreValidationError, match="backup_manifest.json not found"
        ):
            validate_restore_zip(self._make_zip(include_manifest=False))

    def test_rejects_corrupt_manifest_json(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with pytest.raises(RestoreValidationError, match="invalid JSON"):
            validate_restore_zip(self._make_zip(corrupt_manifest=True))

    def test_rejects_corrupt_member_data(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        valid_zip = self._make_zip(settings_schema_version=7)
        bad_zip = _corrupt_zip_member_data(valid_zip)
        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(RestoreValidationError, match="integrity check failed"):
                validate_restore_zip(bad_zip)

    def test_rejects_missing_settings_json(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(RestoreValidationError, match="missing settings.json"):
                validate_restore_zip(
                    self._make_zip(settings_schema_version=7, include_settings=False)
                )

    def test_rejects_member_over_size_limit_before_crc_walk(self):
        from ui.backend import backup_restore
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch.object(backup_restore, "MAX_RESTORE_MEMBER_BYTES", 8):
            with patch.object(zipfile.ZipFile, "testzip") as mock_testzip:
                with pytest.raises(RestoreValidationError, match="member size limit"):
                    validate_restore_zip(
                        self._make_zip(
                            settings_schema_version=7,
                            extra_members={
                                "channelwatch_backup_test/session_state_big.json": "x"
                                * 9
                            },
                        )
                    )

        mock_testzip.assert_not_called()

    def test_rejects_manifest_over_size_limit_before_crc_walk(self):
        from ui.backend import backup_restore
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch.object(backup_restore, "MAX_RESTORE_MANIFEST_BYTES", 8):
            with patch.object(zipfile.ZipFile, "testzip") as mock_testzip:
                with pytest.raises(RestoreValidationError, match="manifest size limit"):
                    validate_restore_zip(self._make_zip(settings_schema_version=7))

        mock_testzip.assert_not_called()

    def test_rejects_total_uncompressed_size_before_crc_walk(self):
        from ui.backend import backup_restore
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch.object(backup_restore, "MAX_RESTORE_TOTAL_UNCOMPRESSED_BYTES", 64):
            with patch.object(zipfile.ZipFile, "testzip") as mock_testzip:
                with pytest.raises(
                    RestoreValidationError, match="total uncompressed size limit"
                ):
                    validate_restore_zip(
                        self._make_zip(
                            settings_schema_version=7,
                            extra_members={
                                "channelwatch_backup_test/session_state_big.json": "x"
                                * 65
                            },
                        )
                    )

        mock_testzip.assert_not_called()

    def test_rejects_member_count_before_crc_walk(self):
        from ui.backend import backup_restore
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        extra_members = {
            f"channelwatch_backup_test/session_state_{idx}.json": "{}"
            for idx in range(3)
        }
        with patch.object(backup_restore, "MAX_RESTORE_MEMBER_COUNT", 2):
            with patch.object(zipfile.ZipFile, "testzip") as mock_testzip:
                with pytest.raises(RestoreValidationError, match="member count limit"):
                    validate_restore_zip(
                        self._make_zip(
                            settings_schema_version=7,
                            extra_members=extra_members,
                        )
                    )

        mock_testzip.assert_not_called()

    def test_rejects_schema_version_ahead_of_app(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(
                RestoreValidationError, match="ahead of this installation"
            ):
                validate_restore_zip(self._make_zip(settings_schema_version=99))

    def test_allows_same_schema_version(self):
        from ui.backend.backup_restore import validate_restore_zip

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            manifest = validate_restore_zip(self._make_zip(settings_schema_version=7))
        assert manifest is not None

    def test_allows_older_schema_version(self):
        from ui.backend.backup_restore import validate_restore_zip

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            manifest = validate_restore_zip(self._make_zip(settings_schema_version=5))
        assert manifest["settings_schema_version"] == 5

    @pytest.mark.parametrize(
        "member_name",
        [
            "channelwatch_backup_test/../escaped.txt",
            "channelwatch_backup_test/nested/../../escaped.txt",
            "/tmp/escaped.txt",
        ],
    )
    def test_rejects_unsafe_member_paths(self, member_name):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(RestoreValidationError, match="unsafe member path"):
                validate_restore_zip(
                    self._make_zip(
                        settings_schema_version=7,
                        extra_members={member_name: "escaped"},
                    )
                )

    def test_rejects_absolute_restore_path_after_prefix(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(
                RestoreValidationError, match="unsupported restore member path"
            ):
                validate_restore_zip(
                    self._make_zip(
                        settings_schema_version=7,
                        extra_members={
                            "channelwatch_backup_test//tmp/escaped.txt": "x"
                        },
                    )
                )


class TestRestoreFromZip:
    def test_restore_rejects_traversal_before_writing_outside_config(self, tmp_path):
        from ui.backend.backup_restore import restore_from_zip, RestoreValidationError

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "created_at": "20260420T000000Z",
                "created_by": "test",
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", '{"_version": 7}')
            zf.writestr(f"{prefix}/../escaped.txt", "escaped")

        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text('{"_version": 7, "dvr_servers": []}')

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(RestoreValidationError, match="unsafe member path"):
                restore_from_zip(buf.getvalue(), target)

        assert not (tmp_path / "escaped.txt").exists()
        assert not (target / "backups").exists()

    def test_files_written_to_config_dir(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text('{"_version": 7, "dvr_servers": []}')

        zip_bytes = create_backup_zip(source)

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(zip_bytes, target)

        assert (target / "settings.json").exists()
        assert (target / "channelwatch.db").exists()

    def test_session_state_files_restored(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text('{"_version": 7, "dvr_servers": []}')

        zip_bytes = create_backup_zip(source)

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(zip_bytes, target)

        assert (target / "session_state_dvr_abc.json").exists()
        assert (target / "session_state_dvr_xyz.json").exists()

    def test_encryption_key_restored_to_root(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text('{"_version": 7, "dvr_servers": []}')

        zip_bytes = create_backup_zip(source)

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(zip_bytes, target)

        assert (target / "encryption.key").exists()
        assert not (target / "sensitive_keys").exists()
        if os.name == "nt":
            return
        assert stat.S_IMODE((target / "encryption.key").stat().st_mode) == 0o600

    def test_pre_restore_snapshot_created(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text('{"_version": 7, "dvr_servers": []}')

        zip_bytes = create_backup_zip(source)

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(zip_bytes, target)

        snapshots = list((target / "backups").glob("pre-restore.*.zip"))
        assert len(snapshots) == 1
        assert zipfile.is_zipfile(snapshots[0])

    def test_pre_restore_snapshot_can_rollback_failed_restore(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        target = _make_config_dir(tmp_path / "target")

        original_settings = (target / "settings.json").read_text(encoding="utf-8")
        original_db = (target / "channelwatch.db").read_bytes()
        original_session = (target / "session_state_dvr_abc.json").read_text(
            encoding="utf-8"
        )
        original_key = (target / "encryption.key").read_bytes()

        (source / "settings.json").write_text(
            json.dumps(
                {
                    "_version": 7,
                    "dvr_servers": [{"name": "restored"}],
                    "tz": "US/Eastern",
                }
            ),
            encoding="utf-8",
        )
        (source / "channelwatch.db").write_bytes(b"restored db bytes")
        (source / "session_state_dvr_abc.json").write_text(
            '{"last_seen": 999}', encoding="utf-8"
        )
        (source / "encryption.key").write_bytes(b"restored-key-material")

        zip_bytes = create_backup_zip(source)

        real_atomic_write = __import__(
            "core.helpers.atomic_io", fromlist=["atomic_write_bytes"]
        ).atomic_write_bytes
        write_calls = {"count": 0}

        def fail_mid_restore(path, data):
            real_atomic_write(path, data)
            if Path(path).name == "settings.json":
                write_calls["count"] += 1
                if write_calls["count"] == 1:
                    raise OSError("simulated restore failure after snapshot")

        with (
            patch(
                "core.helpers.atomic_io.atomic_write_bytes",
                side_effect=fail_mid_restore,
            ),
            patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7),
        ):
            with pytest.raises(OSError, match="simulated restore failure"):
                restore_from_zip(zip_bytes, target)

        snapshots = list((target / "backups").glob("pre-restore.*.zip"))
        assert len(snapshots) == 1
        snapshot_bytes = snapshots[0].read_bytes()

        assert (
            json.loads((target / "settings.json").read_text(encoding="utf-8"))["tz"]
            == "US/Eastern"
        )

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(snapshot_bytes, target)

        assert (target / "settings.json").read_text(
            encoding="utf-8"
        ) == original_settings
        assert (target / "channelwatch.db").read_bytes() == original_db
        assert (target / "session_state_dvr_abc.json").read_text(
            encoding="utf-8"
        ) == original_session
        assert (target / "encryption.key").read_bytes() == original_key

    def test_restore_raises_on_ahead_version(self, tmp_path):
        from ui.backend.backup_restore import restore_from_zip, RestoreValidationError

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 99,
                "created_at": "20260420T000000Z",
                "created_by": "test",
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(
                f"{prefix}/settings.json", '{"_version": 99, "dvr_servers": []}'
            )
        zip_bytes = buf.getvalue()

        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text('{"_version": 7, "dvr_servers": []}')

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(
                RestoreValidationError, match="ahead of this installation"
            ):
                restore_from_zip(zip_bytes, target)

    def test_settings_content_restored_correctly(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        original_settings = json.loads((source / "settings.json").read_text())

        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text(
            '{"_version": 7, "dvr_servers": [], "tz": "US/Pacific"}'
        )

        zip_bytes = create_backup_zip(source)

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(zip_bytes, target)

        restored_settings = json.loads((target / "settings.json").read_text())
        assert restored_settings["tz"] == original_settings["tz"]


class TestBackupEndpoints:
    def _make_app_client(self, tmp_path: Path):
        import json as _json

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            _json.dumps({"_version": 7, "dvr_servers": [], "api_key": "test-key"})
        )
        (tmp_path / "channelwatch.db").write_bytes(b"db content")

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "API_KEY_CACHE", "test-key"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", tmp_path),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            yield client

    def test_download_returns_zip(self, tmp_path):
        config_dir = _make_config_dir(tmp_path / "config")

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = config_dir / "settings.json"

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", config_dir),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.get(
                "/api/v1/backup/download", headers={"X-API-Key": "testkey"}
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert zipfile.is_zipfile(io.BytesIO(resp.content))

    def test_download_create_failure_returns_structured_error(self, tmp_path):
        config_dir = _make_config_dir(tmp_path / "config")

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = config_dir / "settings.json"

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", config_dir),
            patch(
                "ui.backend.backup_restore.create_backup_zip",
                side_effect=RuntimeError("boom"),
            ),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.get(
                "/api/v1/backup/download", headers={"X-API-Key": "testkey"}
            )

        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "ERR_BACKUP_CREATE_FAILED"

    def test_restore_rejects_ahead_version(self, tmp_path):
        config_dir = _make_config_dir(tmp_path / "config")

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 99,
                "created_at": "20260420T000000Z",
                "created_by": "test",
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(
                f"{prefix}/settings.json", '{"_version": 99, "dvr_servers": []}'
            )
        zip_bytes = buf.getvalue()

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = config_dir / "settings.json"

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", config_dir),
            patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/backup/restore",
                files={"file": ("backup.zip", zip_bytes, "application/zip")},
                headers={"X-API-Key": "testkey"},
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_RESTORE_SCHEMA_AHEAD"

    def test_restore_invalid_zip_returns_structured_error(self, tmp_path):
        config_dir = _make_config_dir(tmp_path / "config")

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = config_dir / "settings.json"

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", config_dir),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/backup/restore",
                files={"file": ("backup.zip", b"not a zip", "application/zip")},
                headers={"X-API-Key": "testkey"},
            )

        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "ERR_RESTORE_INVALID_ZIP"

    def test_restore_generic_failure_returns_structured_error(self, tmp_path):
        config_dir = _make_config_dir(tmp_path / "config")

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = config_dir / "settings.json"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("channelwatch_backup_test/backup_manifest.json", "{}")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", config_dir),
            patch(
                "ui.backend.backup_restore.restore_from_zip",
                side_effect=RuntimeError("disk failed"),
            ),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/backup/restore",
                files={"file": ("backup.zip", buf.getvalue(), "application/zip")},
                headers={"X-API-Key": "testkey"},
            )

        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "ERR_RESTORE_FAILED"

    def test_restore_succeeds_and_hot_reloads(self, tmp_path):
        source = _make_config_dir(tmp_path / "source")
        target = _make_config_dir(tmp_path / "target")

        from ui.backend.backup_restore import create_backup_zip

        zip_bytes = create_backup_zip(source)

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = target / "settings.json"

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", target),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", target),
            patch.object(
                ui_main, "_signal_core_hot_reload", return_value=True
            ) as mock_reload,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/backup/restore",
                files={"file": ("backup.zip", zip_bytes, "application/zip")},
                headers={"X-API-Key": "testkey"},
            )

        assert resp.status_code == 200
        assert "manifest" in resp.json()
        mock_reload.assert_called_once()
