"""Tests for: backup / restore helpers and endpoints."""

import io
import json
import os
import stat
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from core.helpers.atomic_io import (
    _atomic_read_secret_bytes,
    _encrypt_secret_bytes,
    _is_secret_envelope,
    atomic_write_bytes,
)


def _make_config_dir(tmp_path: Path, *, schema_version: int = 7) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = {"_version": schema_version, "dvr_servers": [], "tz": "UTC"}
    (config_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (config_dir / "channelwatch.db").write_bytes(b"SQLite data placeholder")
    (config_dir / "session_state_dvr_abc.json").write_text('{"last_seen": 1}')
    (config_dir / "session_state_dvr_xyz.json").write_text('{"last_seen": 2}')
    key_file = config_dir / "encryption.key"
    key_file.write_bytes(b"\xde\xad\xbe\xef" * 8)
    if os.name != "nt":
        key_file.chmod(0o600)
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

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
    def test_rejects_symlinked_config_member(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        outside = tmp_path / "outside-settings.json"
        outside.write_text('{"private": true}', encoding="utf-8")
        (config_dir / "settings.json").unlink()
        (config_dir / "settings.json").symlink_to(outside)

        with pytest.raises(PermissionError, match="unsafe file source"):
            create_backup_zip(config_dir)

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
        original_key = _atomic_read_secret_bytes(config_dir / "encryption.key")
        data = create_backup_zip(config_dir)
        zf = zipfile.ZipFile(io.BytesIO(data))
        key_name = next(n for n in zf.namelist() if n.endswith("/encryption.key"))
        assert zf.read(key_name) == original_key

    def test_encrypted_key_backup_keeps_stored_envelope(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        logical_key = (config_dir / "encryption.key").read_bytes()
        atomic_write_bytes(
            config_dir / "encryption.key",
            _encrypt_secret_bytes(logical_key),
        )
        (config_dir / "encryption.key").chmod(0o600)
        stored_key = (config_dir / "encryption.key").read_bytes()

        data = create_backup_zip(config_dir)
        zf = zipfile.ZipFile(io.BytesIO(data))
        key_name = next(n for n in zf.namelist() if n.endswith("/encryption.key"))
        archived_key = zf.read(key_name)

        assert archived_key == stored_key
        assert archived_key != logical_key
        assert _is_secret_envelope(archived_key)

    def test_missing_optional_files_do_not_fail(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings = {"_version": 7, "dvr_servers": [], "tz": "UTC"}
        (config_dir / "settings.json").write_text(json.dumps(settings))
        data = create_backup_zip(config_dir)
        assert zipfile.is_zipfile(io.BytesIO(data))

    def test_outer_maintenance_lock_can_be_reused_without_reacquiring(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip

        config_dir = _make_config_dir(tmp_path)
        with patch(
            "ui.backend.backup_restore.configuration_maintenance_lock",
            side_effect=AssertionError("lock was reacquired"),
        ):
            data = create_backup_zip(config_dir, lock_already_held=True)

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
                    "files": ["settings.json"]
                    + [
                        name[len(f"{prefix}/") :]
                        for name in (extra_members or {})
                        if name.startswith(f"{prefix}/")
                        and not name.endswith("/backup_manifest.json")
                    ],
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
            RestoreValidationError, match="exactly one backup_manifest.json"
        ):
            validate_restore_zip(self._make_zip(include_manifest=False))

    def test_rejects_corrupt_manifest_json(self):
        from ui.backend.backup_restore import (
            validate_restore_zip,
            RestoreValidationError,
        )

        with pytest.raises(RestoreValidationError, match="invalid JSON"):
            validate_restore_zip(self._make_zip(corrupt_manifest=True))

    def test_rejects_non_utf8_manifest(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        prefix = "channelwatch_backup_test"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"{prefix}/backup_manifest.json", b"\xff\xfe")
            zf.writestr(f"{prefix}/settings.json", "{}")

        with pytest.raises(RestoreValidationError, match="invalid JSON"):
            validate_restore_zip(buf.getvalue())

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
            with pytest.raises(RestoreValidationError, match="ambiguous member path"):
                validate_restore_zip(
                    self._make_zip(
                        settings_schema_version=7,
                        extra_members={
                            "channelwatch_backup_test//tmp/escaped.txt": "x"
                        },
                    )
                )

    def test_rejects_duplicate_member_names(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", "{}")
            with pytest.warns(UserWarning):
                zf.writestr(f"{prefix}/settings.json", "{}")
        with pytest.raises(RestoreValidationError, match="duplicate or colliding"):
            validate_restore_zip(buf.getvalue())

    def test_rejects_casefold_and_unicode_path_collisions(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        for second_name in ("SETTINGS.JSON", "se\u0301ssion_state.json"):
            first_name = (
                "settings.json"
                if second_name == "SETTINGS.JSON"
                else "s\u00e9ssion_state.json"
            )
            buf = io.BytesIO()
            prefix = "channelwatch_backup_test"
            with zipfile.ZipFile(buf, "w") as zf:
                manifest = {
                    "backup_schema_version": 1,
                    "settings_schema_version": 7,
                    "files": [first_name, second_name],
                }
                zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
                zf.writestr(f"{prefix}/{first_name}", "{}")
                zf.writestr(f"{prefix}/{second_name}", "{}")
            with pytest.raises(
                RestoreValidationError,
                match="duplicate or colliding|ambiguous member path",
            ):
                validate_restore_zip(buf.getvalue())

    def test_rejects_backslash_and_multiple_manifests(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        for extra_name in (
            "channelwatch_backup_test\\settings.json",
            "other/backup_manifest.json",
        ):
            buf = io.BytesIO()
            prefix = "channelwatch_backup_test"
            with zipfile.ZipFile(buf, "w") as zf:
                manifest = {
                    "backup_schema_version": 1,
                    "settings_schema_version": 7,
                    "files": ["settings.json"],
                }
                zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
                zf.writestr(f"{prefix}/settings.json", "{}")
                zf.writestr(extra_name, "{}")
            with pytest.raises(RestoreValidationError):
                validate_restore_zip(buf.getvalue())

    def test_rejects_symlink_member_external_attributes(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            info = zipfile.ZipInfo(f"{prefix}/settings.json")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "target")
        with pytest.raises(RestoreValidationError, match="not a regular"):
            validate_restore_zip(buf.getvalue())

    @pytest.mark.parametrize(
        "member_type",
        [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK],
    )
    def test_rejects_special_member_external_attributes(self, member_type):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            info = zipfile.ZipInfo(f"{prefix}/settings.json")
            info.create_system = 3
            info.external_attr = (member_type | 0o600) << 16
            zf.writestr(info, "not-a-regular-file")

        with pytest.raises(RestoreValidationError, match="not a regular"):
            validate_restore_zip(buf.getvalue())

    @pytest.mark.parametrize("extra_field_id", [0x000D, 0x756E])
    def test_rejects_unix_link_metadata(self, extra_field_id):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            info = zipfile.ZipInfo(f"{prefix}/settings.json")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.extra = extra_field_id.to_bytes(2, "little") + b"\x00\x00"
            zf.writestr(info, "{}")

        with pytest.raises(RestoreValidationError, match="link metadata"):
            validate_restore_zip(buf.getvalue())

    def test_rejects_malformed_extra_metadata(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            info = zipfile.ZipInfo(f"{prefix}/settings.json")
            info.extra = b"\x01\x00\x00"
            zf.writestr(info, "{}")

        with pytest.raises(RestoreValidationError, match="malformed extra metadata"):
            validate_restore_zip(buf.getvalue())

    def test_rejects_unsupported_compression_method(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_BZIP2) as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", "{}")

        with pytest.raises(RestoreValidationError, match="unsupported compression"):
            validate_restore_zip(buf.getvalue())

    def test_rejects_payload_from_second_top_level_prefix(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", "{}")
            zf.writestr("other/session_state_extra.json", "{}")

        with pytest.raises(RestoreValidationError, match="outside the declared prefix"):
            validate_restore_zip(buf.getvalue())

    @pytest.mark.parametrize(
        "manifest_files",
        [
            ["settings.json", "settings.json"],
            ["settings.json", "session_state_missing.json"],
        ],
    )
    def test_rejects_duplicate_or_inaccurate_manifest_file_list(self, manifest_files):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        buf = io.BytesIO()
        prefix = "channelwatch_backup_test"
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": manifest_files,
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", "{}")

        with pytest.raises(
            RestoreValidationError,
            match="duplicate file entries|files do not match",
        ):
            validate_restore_zip(buf.getvalue())

    def test_rejects_extreme_compression_ratio(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        payload = "0" * (2 * 1024 * 1024)
        prefix = "channelwatch_backup_test"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            manifest = {
                "backup_schema_version": 1,
                "settings_schema_version": 7,
                "files": ["settings.json", "session_state_bomb.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", "{}")
            zf.writestr(f"{prefix}/session_state_bomb.json", payload)
        with pytest.raises(RestoreValidationError, match="compression-ratio"):
            validate_restore_zip(buf.getvalue())

    def test_rejects_forged_key_format_and_manifest_file_list(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        prefix = "channelwatch_backup_test"
        for files, key_format in (
            (["settings.json"], "managed-local-raw-v1"),
            (["settings.json", "sensitive_keys/encryption.key"], "legacy-envelope-v1"),
        ):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                manifest = {
                    "backup_schema_version": 2,
                    "settings_schema_version": 7,
                    "files": files,
                    "encryption_key_format": key_format,
                }
                zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
                zf.writestr(f"{prefix}/settings.json", "{}")
                if "sensitive_keys/encryption.key" in files:
                    zf.writestr(
                        f"{prefix}/sensitive_keys/encryption.key",
                        b"r" * 32,
                    )
            with pytest.raises(
                RestoreValidationError,
                match="files do not match|encryption_key_format",
            ):
                validate_restore_zip(buf.getvalue())

    def test_rejects_invalid_raw_key_length_even_when_manifest_claims_raw(self):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            validate_restore_zip,
        )

        prefix = "channelwatch_backup_test"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 2,
                "settings_schema_version": 7,
                "files": ["settings.json", "sensitive_keys/encryption.key"],
                "encryption_key_format": "managed-local-raw-v1",
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", "{}")
            zf.writestr(f"{prefix}/sensitive_keys/encryption.key", b"too-short")

        with pytest.raises(RestoreValidationError, match="encryption_key_format"):
            validate_restore_zip(buf.getvalue())


class TestRestoreFromZip:
    def test_restore_recovers_older_transaction_before_committing_new_archive(
        self, tmp_path
    ):
        from core.helpers.maintenance_transaction import (
            recover_maintenance_transactions,
        )
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        source_settings = json.loads((source / "settings.json").read_text())
        source_settings["tz"] = "America/New_York"
        (source / "settings.json").write_text(json.dumps(source_settings))
        archive = create_backup_zip(source)
        target = _make_config_dir(tmp_path / "target")

        transaction = target / ".channelwatch-transactions" / "older"
        (transaction / "old").mkdir(parents=True)
        (transaction / "new").mkdir()
        pending_settings = {
            "_version": 7,
            "tz": "America/Chicago",
            "dvr_servers": [],
            "webhooks": [],
        }
        for filename, new_bytes in (
            ("settings.json", json.dumps(pending_settings).encode()),
            ("encryption.key", b"p" * 32),
        ):
            (transaction / "old" / filename).write_bytes((target / filename).read_bytes())
            (transaction / "new" / filename).write_bytes(new_bytes)
        (transaction / "journal.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "state": "committing",
                    "files": ["encryption.key", "settings.json"],
                    "absent_before": [],
                }
            )
        )

        restore_from_zip(archive, target)

        assert recover_maintenance_transactions(target) == 0
        restored = json.loads((target / "settings.json").read_text())
        assert restored["tz"] == "America/New_York"

    @pytest.mark.parametrize(
        ("invalid_settings", "marker"),
        [
            (
                {"_version": 7, "webhooks": "synthetic-secret-not-a-list"},
                "synthetic-secret-not-a-list",
            ),
            (
                {"_version": "synthetic-invalid-version", "webhooks": []},
                "synthetic-invalid-version",
            ),
        ],
    )
    def test_restore_rejects_schema_invalid_settings_before_live_mutation(
        self, tmp_path, invalid_settings, marker
    ):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            create_backup_zip,
            restore_from_zip,
        )

        source = _make_config_dir(tmp_path / "source")
        (source / "settings.json").write_text(
            json.dumps(invalid_settings),
            encoding="utf-8",
        )
        archive = create_backup_zip(source)
        target = _make_config_dir(tmp_path / "target")
        original_settings = (target / "settings.json").read_bytes()
        original_key = (target / "encryption.key").read_bytes()

        with pytest.raises(
            RestoreValidationError,
            match="do not match the supported ChannelWatch schema",
        ) as exc_info:
            restore_from_zip(archive, target)

        assert marker not in str(exc_info.value)
        assert (target / "settings.json").read_bytes() == original_settings
        assert (target / "encryption.key").read_bytes() == original_key
        assert not (target / "backups").exists()

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

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
    def test_restore_refuses_symlinked_private_backup_directory(self, tmp_path):
        from ui.backend.backup_restore import (
            RestoreValidationError,
            create_backup_zip,
            restore_from_zip,
        )

        source = _make_config_dir(tmp_path / "source")
        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text(
            '{"_version": 7, "dvr_servers": []}',
            encoding="utf-8",
        )
        outside = tmp_path / "outside-backups"
        outside.mkdir()
        (target / "backups").symlink_to(outside, target_is_directory=True)

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(
                RestoreValidationError,
                match="backup directory is unsafe",
            ):
                restore_from_zip(create_backup_zip(source), target)

        assert list(outside.iterdir()) == []

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

    def test_encrypted_key_restore_preserves_logical_key(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        logical_key = (source / "encryption.key").read_bytes()
        atomic_write_bytes(
            source / "encryption.key",
            _encrypt_secret_bytes(logical_key),
        )
        (source / "encryption.key").chmod(0o600)
        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text('{"_version": 7, "dvr_servers": []}')

        zip_bytes = create_backup_zip(source)

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(zip_bytes, target)

        assert _atomic_read_secret_bytes(target / "encryption.key") == logical_key
        assert not _is_secret_envelope((target / "encryption.key").read_bytes())

    def test_restore_preserves_dvr_and_webhook_credentials(self, tmp_path):
        from core.helpers.encryption import (
            decrypt_registered_credentials_with_diagnostics,
            encrypt_value,
        )
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        key = (source / "encryption.key").read_bytes()
        protected_settings = {
            "_version": 7,
            "dvr_servers": [
                {"id": "dvr-1", "api_key": encrypt_value("dvr-secret", key)}
            ],
            "webhooks": [
                {
                    "name": "sink",
                    "url": encrypt_value("http://sink.test", key),
                    "secret": encrypt_value("webhook-secret", key),
                }
            ],
        }
        (source / "settings.json").write_text(
            json.dumps(protected_settings),
            encoding="utf-8",
        )
        target = tmp_path / "target"
        target.mkdir()
        (target / "settings.json").write_text(
            '{"_version": 7, "dvr_servers": []}', encoding="utf-8"
        )

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(create_backup_zip(source), target)

        loaded = decrypt_registered_credentials_with_diagnostics(
            json.loads((target / "settings.json").read_text(encoding="utf-8")),
            target / "encryption.key",
        )
        assert loaded.failures == ()
        assert loaded.settings["dvr_servers"][0]["api_key"] == "dvr-secret"
        assert loaded.settings["webhooks"][0]["url"] == "http://sink.test"
        assert loaded.settings["webhooks"][0]["secret"] == "webhook-secret"

    def test_restore_without_archived_key_requires_matching_current_key(self, tmp_path):
        from core.helpers.encryption import encrypt_value
        from ui.backend.backup_restore import RestoreValidationError, restore_from_zip

        logical_key = os.urandom(32)
        prefix = "channelwatch_backup_test"
        settings = {
            "_version": 7,
            "dvr_servers": [
                {"id": "dvr-1", "api_key": encrypt_value("secret", logical_key)}
            ],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            manifest = {
                "backup_schema_version": 2,
                "settings_schema_version": 7,
                "encryption_key_format": "missing",
                "files": ["settings.json"],
            }
            zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest))
            zf.writestr(f"{prefix}/settings.json", json.dumps(settings))

        for current_key, expected_message in (
            (None, "no usable encryption key"),
            (os.urandom(32), "does not open every protected credential"),
        ):
            target = tmp_path / f"target-{expected_message.split()[0]}"
            target.mkdir()
            (target / "settings.json").write_text(
                '{"_version": 7, "dvr_servers": []}', encoding="utf-8"
            )
            if current_key is not None:
                (target / "encryption.key").write_bytes(current_key)
                if os.name != "nt":
                    (target / "encryption.key").chmod(0o600)
            with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
                with pytest.raises(RestoreValidationError, match=expected_message):
                    restore_from_zip(buf.getvalue(), target)

        matching_target = tmp_path / "target-matching"
        matching_target.mkdir()
        (matching_target / "settings.json").write_text(
            '{"_version": 7, "dvr_servers": []}', encoding="utf-8"
        )
        (matching_target / "encryption.key").write_bytes(logical_key)
        if os.name != "nt":
            (matching_target / "encryption.key").chmod(0o600)
        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(buf.getvalue(), matching_target)
        assert (matching_target / "encryption.key").read_bytes() == logical_key

    def test_legacy_backup_accepts_one_time_key_or_explicit_credential_reset(
        self, tmp_path, monkeypatch
    ):
        from core.helpers.encryption import encrypt_value
        from ui.backend.backup_restore import (
            RestoreValidationError,
            create_backup_zip,
            restore_from_zip,
        )

        monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
        monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)
        source = _make_config_dir(tmp_path / "source")
        logical_key = (source / "encryption.key").read_bytes()
        legacy_material = b"legacy-restore-0123456789abcdef0123456789"
        (source / "settings.json").write_text(
            json.dumps(
                {
                    "_version": 7,
                    "dvr_servers": [
                        {
                            "id": "dvr-1",
                            "host": "dvr.lan",
                            "enabled": True,
                            "api_key": encrypt_value("secret", logical_key),
                        },
                        {
                            "id": "dvr-2",
                            "host": "unconfigured.lan",
                            "enabled": True,
                            "api_key": "",
                        },
                    ],
                    "webhooks": [
                        {
                            "name": "configured-sink",
                            "enabled": True,
                            "url": encrypt_value("http://sink.test", logical_key),
                            "secret": encrypt_value("hook-secret", logical_key),
                        },
                        {
                            "name": "unconfigured-sink",
                            "enabled": True,
                            "url": "",
                            "secret": "",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (source / "encryption.key").write_bytes(
            _encrypt_secret_bytes(logical_key, material=legacy_material)
        )
        if os.name != "nt":
            (source / "encryption.key").chmod(0o600)
        backup = create_backup_zip(source)

        blocked = tmp_path / "blocked"
        blocked.mkdir()
        (blocked / "settings.json").write_text(
            '{"_version": 7, "dvr_servers": []}', encoding="utf-8"
        )
        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            with pytest.raises(RestoreValidationError, match="Supply the original"):
                restore_from_zip(backup, blocked)

        recovered = tmp_path / "recovered"
        recovered.mkdir()
        (recovered / "settings.json").write_text(
            '{"_version": 7, "dvr_servers": []}', encoding="utf-8"
        )
        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(
                backup,
                recovered,
                legacy_storage_key=legacy_material,
            )
        assert (recovered / "encryption.key").read_bytes() == logical_key

        reset = tmp_path / "reset"
        reset.mkdir()
        (reset / "settings.json").write_text(
            '{"_version": 7, "dvr_servers": []}', encoding="utf-8"
        )
        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(
                backup,
                reset,
                legacy_storage_key=b"wrong-but-long-enough-0123456789abcdef",
                reset_protected_credentials=True,
            )
        reset_settings = json.loads(
            (reset / "settings.json").read_text(encoding="utf-8")
        )
        assert reset_settings["dvr_servers"][0]["api_key"] == ""
        assert reset_settings["dvr_servers"][0]["enabled"] is False
        assert reset_settings["dvr_servers"][0]["host"] == "dvr.lan"
        assert reset_settings["dvr_servers"][1]["enabled"] is True
        assert reset_settings["dvr_servers"][1]["host"] == "unconfigured.lan"
        assert reset_settings["webhooks"][0]["url"] == ""
        assert reset_settings["webhooks"][0]["secret"] == ""
        assert reset_settings["webhooks"][0]["enabled"] is False
        assert reset_settings["webhooks"][1]["enabled"] is True
        assert len((reset / "encryption.key").read_bytes()) == 32

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
        if os.name != "nt":
            assert snapshots[0].stat().st_mode & 0o777 == 0o600

    def test_pre_restore_snapshots_are_unique_within_same_second(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        target = _make_config_dir(tmp_path / "target")
        zip_bytes = create_backup_zip(source)

        with (
            patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7),
            patch(
                "ui.backend.backup_restore._utc_timestamp",
                return_value="20260824T120000Z",
            ),
            patch(
                "ui.backend.backup_restore._private_snapshot_token",
                side_effect=["collision", "collision", "unique"],
            ),
        ):
            restore_from_zip(zip_bytes, target)
            restore_from_zip(zip_bytes, target)

        snapshots = list((target / "backups").glob("pre-restore.*.zip"))
        assert len(snapshots) == 2
        assert len({path.name for path in snapshots}) == 2

    def test_pre_restore_snapshot_can_rollback_failed_restore(self, tmp_path):
        from ui.backend.backup_restore import create_backup_zip, restore_from_zip

        source = _make_config_dir(tmp_path / "source")
        target = _make_config_dir(tmp_path / "target")

        original_settings = (target / "settings.json").read_text(encoding="utf-8")
        original_db = (target / "channelwatch.db").read_bytes()
        original_session = (target / "session_state_dvr_abc.json").read_text(
            encoding="utf-8"
        )
        original_key = _atomic_read_secret_bytes(target / "encryption.key")

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
        (source / "encryption.key").write_bytes(b"r" * 32)

        zip_bytes = create_backup_zip(source)

        with (
            patch(
                "core.helpers.maintenance_transaction._install_staged_files",
                side_effect=OSError("simulated restore failure after snapshot"),
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
            == "UTC"
        )

        with patch("ui.backend.backup_restore.CURRENT_SCHEMA_VERSION", 7):
            restore_from_zip(snapshot_bytes, target)

        assert json.loads(
            (target / "settings.json").read_text(encoding="utf-8")
        ) == json.loads(original_settings)
        assert (target / "channelwatch.db").read_bytes() == original_db
        assert (target / "session_state_dvr_abc.json").read_text(
            encoding="utf-8"
        ) == original_session
        assert _atomic_read_secret_bytes(target / "encryption.key") == original_key

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
