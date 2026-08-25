"""Focused fault-path coverage for v0.9.18 credential durability work."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _restore_zip(
    settings: bytes = b'{"_version": 7, "dvr_servers": [], "webhooks": []}',
    *,
    manifest_updates: dict | None = None,
    members: dict[str, bytes] | None = None,
) -> bytes:
    prefix = "channelwatch_backup_coverage"
    payloads = {"settings.json": settings, **(members or {})}
    manifest = {
        "backup_schema_version": 2,
        "settings_schema_version": 7,
        "encryption_key_format": "missing",
        "files": list(payloads),
    }
    manifest.update(manifest_updates or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            f"{prefix}/backup_manifest.json",
            json.dumps(manifest).encode(),
        )
        for name, value in payloads.items():
            archive.writestr(f"{prefix}/{name}", value)
    return buffer.getvalue()


def _zip_info(*, extra: bytes = b"", flag_bits: int = 0) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo("channelwatch_backup_coverage/settings.json")
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.extra = extra
    info.flag_bits = flag_bits
    return info


def test_sqlite_snapshot_handles_absent_placeholder_and_live_database(tmp_path):
    from ui.backend.backup_restore import _sqlite_snapshot_bytes

    database = tmp_path / "channelwatch.db"
    assert _sqlite_snapshot_bytes(database) == b""
    database.write_bytes(b"historical placeholder")
    assert _sqlite_snapshot_bytes(database) == b"historical placeholder"

    database.unlink()
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (value TEXT)")
    connection.execute("INSERT INTO sample VALUES ('durable')")
    connection.commit()
    connection.close()

    snapshot = _sqlite_snapshot_bytes(database)
    restored = tmp_path / "snapshot.db"
    restored.write_bytes(snapshot)
    check = sqlite3.connect(restored)
    try:
        assert check.execute("SELECT value FROM sample").fetchone() == ("durable",)
    finally:
        check.close()


def test_private_snapshot_reservation_exhaustion_and_binary_flag(tmp_path, monkeypatch):
    from ui.backend import backup_restore

    monkeypatch.setattr(backup_restore.os, "O_BINARY", 0, raising=False)
    reserved = backup_restore._reserve_unique_private_snapshot(tmp_path, "stamp")
    assert reserved.is_file()

    with (
        patch.object(backup_restore.os, "open", side_effect=FileExistsError),
        pytest.raises(FileExistsError, match="unique private restore snapshot"),
    ):
        backup_restore._reserve_unique_private_snapshot(tmp_path, "stamp")


@pytest.mark.parametrize(
    ("info", "message"),
    [
        (_zip_info(flag_bits=1), "is encrypted"),
        (_zip_info(extra=b"\x01\x00\x05\x00x"), "malformed extra metadata"),
        (_zip_info(extra=b"\x01\x00\x01\x00x"), None),
        (_zip_info(extra=b"x"), "malformed extra metadata"),
    ],
)
def test_restore_zip_metadata_faults(info, message):
    from ui.backend.backup_restore import (
        RestoreValidationError,
        _validate_restore_zip_info,
    )

    archive = SimpleNamespace(infolist=lambda: [info])
    if message is None:
        _validate_restore_zip_info(archive)
    else:
        with pytest.raises(RestoreValidationError, match=message):
            _validate_restore_zip_info(archive)


@pytest.mark.parametrize(
    ("manifest_updates", "members", "message"),
    [
        ({"files": "settings.json"}, None, "valid 'files' list"),
        (
            {"files": ["settings.json", "unsupported.txt"]},
            None,
            "unsupported file entry",
        ),
        (
            {
                "backup_schema_version": 1,
                "encryption_key_format": "managed-local-raw-v1",
            },
            None,
            "encryption_key_format does not match",
        ),
    ],
)
def test_restore_manifest_rejects_malformed_file_and_key_metadata(
    manifest_updates, members, message
):
    from ui.backend.backup_restore import RestoreValidationError, validate_restore_zip

    with pytest.raises(RestoreValidationError, match=message):
        validate_restore_zip(
            _restore_zip(manifest_updates=manifest_updates, members=members)
        )


def test_legacy_backup_key_rejects_invalid_raw_and_wrong_envelope_material():
    from ui.backend.backup_restore import (
        RestoreValidationError,
        _decode_legacy_backup_key,
    )

    from core.helpers.atomic_io import _encrypt_secret_bytes

    with pytest.raises(RestoreValidationError, match="invalid managed"):
        _decode_legacy_backup_key(b"short", None)

    envelope = _encrypt_secret_bytes(b"k" * 32, material=b"correct" * 8)
    with pytest.raises(RestoreValidationError, match="legacy protected"):
        _decode_legacy_backup_key(envelope, b"wrong" * 8)


def test_sqlite_restore_integrity_and_current_key_failure_paths(tmp_path):
    from ui.backend import backup_restore

    database = tmp_path / "source.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (value INTEGER)")
    connection.commit()
    connection.close()
    backup_restore._validate_sqlite_restore_member(database.read_bytes())

    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(b"k" * 32)
    with patch.object(
        backup_restore,
        "read_regular_file_bytes",
        side_effect=OSError("unreadable"),
    ):
        assert backup_restore._current_raw_key_if_usable(tmp_path) is None

    key_file.write_bytes(b"invalid")
    assert backup_restore._current_raw_key_if_usable(tmp_path) is None


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (b"not-json", "readable JSON object"),
        (b"[]", "must contain a JSON object"),
    ],
)
def test_restore_rejects_unreadable_or_non_object_settings(tmp_path, settings, message):
    from ui.backend.backup_restore import RestoreValidationError, restore_from_zip

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with pytest.raises(RestoreValidationError, match=message):
        restore_from_zip(_restore_zip(settings), config_dir)


def test_webhook_secret_merge_requires_stable_unique_identities(monkeypatch):
    from ui.backend import config

    existing = {
        "webhooks": [
            {"id": "one", "url": "https://one.invalid", "secret": "first"},
            {"id": "two", "url": "https://two.invalid", "secret": "second"},
        ]
    }
    with pytest.raises(ValueError, match="missing its stable identity"):
        config._merge_webhook_secrets(
            {"webhooks": [{"url": "****", "secret": "****"}]}, existing
        )
    with pytest.raises(ValueError, match="no longer matches"):
        config._merge_webhook_secrets(
            {"webhooks": [{"id": "gone", "url": "****", "secret": "****"}]},
            existing,
        )
    with pytest.raises(ValueError, match="Incoming webhook identities"):
        config._merge_webhook_secrets(
            {
                "webhooks": [
                    {"id": "one", "url": "new", "secret": "new"},
                    {"id": "one", "url": "newer", "secret": "newer"},
                ]
            },
            existing,
        )

    generated = config._merge_webhook_secrets(
        {"webhooks": [{"url": "https://new.invalid", "secret": "new"}]},
        existing,
    )
    assert generated["webhooks"][0]["id"].startswith("webhook_")
    merged = config._merge_webhook_secrets(
        {"webhooks": [{"id": "one", "url": "****", "secret": "****"}]},
        existing,
    )
    assert merged["webhooks"][0]["url"] == "https://one.invalid"
    assert merged["webhooks"][0]["secret"] == "first"

    duplicate_existing = {
        "webhooks": [
            {"id": "same", "url": "one"},
            {"id": "same", "url": "two"},
        ]
    }
    with pytest.raises(ValueError, match="Persisted webhook identities"):
        config._merge_webhook_secrets({"webhooks": []}, duplicate_existing)


def test_ui_load_clears_ciphertext_when_decryptor_is_unavailable(tmp_path):
    from ui.backend import config

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "_version": 7,
                "dvr_servers": [{"id": "dvr", "host": "dvr.lan", "api_key": "fernet:bad"}],
                "webhooks": [{"id": "hook", "url": "fernet:bad", "secret": "fernet:bad"}],
            }
        ),
        encoding="utf-8",
    )
    with (
        patch.object(config, "CONFIG_DIR", tmp_path),
        patch.object(config, "CONFIG_FILE", settings_file),
        patch(
            "core.helpers.encryption.decrypt_registered_credentials_with_diagnostics",
            side_effect=OSError("storage unavailable"),
        ),
    ):
        loaded = config.load_settings()
    assert loaded.dvr_servers[0]["api_key"] == ""
    assert loaded.dvr_servers[0]["enabled"] is False
    assert loaded.webhooks[0].url == ""
    assert loaded.webhooks[0].secret == ""
    assert loaded.webhooks[0].enabled is False


def test_ui_locked_save_requires_initialized_key_and_tolerates_bad_old_json(tmp_path):
    from ui.backend import config
    from ui.backend.schemas import AppSettings

    from core.helpers.encryption import bootstrap_encryption_key

    settings_file = tmp_path / "settings.json"
    with patch.object(config, "CONFIG_DIR", tmp_path), patch.object(
        config, "CONFIG_FILE", settings_file
    ):
        with pytest.raises(RuntimeError, match="Managed key initialization"):
            config.save_settings(AppSettings(), lock_already_held=True)

        bootstrap_encryption_key(tmp_path / "encryption.key")
        settings_file.write_text("not-json", encoding="utf-8")
        config.save_settings(AppSettings(tz="UTC"), lock_already_held=True)
    assert json.loads(settings_file.read_text(encoding="utf-8"))["tz"] == "UTC"


def test_decryptors_clear_ciphertext_when_managed_key_is_unavailable(tmp_path):
    from core.helpers import encryption

    failures: list[str] = []
    with patch.object(
        encryption,
        "bootstrap_encryption_key",
        side_effect=OSError("storage unavailable"),
    ):
        dvrs = encryption.decrypt_dvr_api_keys(
            ["unchanged", {"api_key": "fernet:unreadable"}],
            tmp_path / "encryption.key",
            failure_paths=failures,
        )
        hooks = encryption.decrypt_webhook_credentials(
            ["unchanged", {"url": "fernet:unreadable", "secret": "fernet:also-bad"}],
            tmp_path / "encryption.key",
            failure_paths=failures,
        )
        with pytest.raises(encryption.EncryptionKeyUnavailableError):
            encryption.encrypt_webhook_credentials(
                [{"url": "https://receiver.invalid", "secret": "plain"}],
                tmp_path / "encryption.key",
            )

    assert dvrs[1]["api_key"] == ""
    assert hooks[1] == {"url": "", "secret": ""}
    assert failures == [
        "dvr_servers[1].api_key",
        "webhooks[1].url",
        "webhooks[1].secret",
    ]


@pytest.mark.parametrize(
    ("file_value", "environment_value", "code", "available"),
    [
        (b"", "", "secret_storage_key_missing", False),
        (b"short", "", "secret_storage_key_too_short", False),
        (b"x" * 32, "", None, True),
        (None, "short", "secret_storage_key_too_short", False),
        (None, "x" * 32, None, True),
    ],
)
def test_legacy_storage_key_candidate_matrix(
    tmp_path, monkeypatch, file_value, environment_value, code, available
):
    from core.helpers.atomic_io import legacy_secret_storage_key_candidates

    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", raising=False)
    monkeypatch.delenv("CHANNELWATCH_SECRET_STORAGE_KEY", raising=False)
    if file_value is not None:
        key_file = tmp_path / "legacy-key"
        key_file.write_bytes(file_value)
        monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(key_file))
    if environment_value:
        monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", environment_value)

    candidate = legacy_secret_storage_key_candidates()[0]
    assert candidate.available is available
    assert candidate.code == code


def test_legacy_storage_key_unreadable_file_falls_back_to_environment(
    tmp_path, monkeypatch
):
    from core.helpers.atomic_io import legacy_secret_storage_key_candidates

    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY_FILE", str(tmp_path))
    monkeypatch.setenv("CHANNELWATCH_SECRET_STORAGE_KEY", "e" * 32)
    candidates = legacy_secret_storage_key_candidates()
    assert candidates[0].code == "secret_storage_key_file_unreadable"
    assert candidates[1].available is True


def test_bounded_regular_read_detects_open_swap_and_growth(tmp_path, monkeypatch):
    from core.helpers import atomic_io

    source = tmp_path / "source"
    source.write_bytes(b"")
    monkeypatch.setattr(atomic_io.os, "O_BINARY", 0, raising=False)
    assert atomic_io.read_regular_file_bytes(source) == b""

    metadata = source.lstat()
    changed = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_nlink=1,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino + 1,
    )
    with (
        patch.object(atomic_io.os, "fstat", return_value=changed),
        pytest.raises(PermissionError, match="changed while opening"),
    ):
        atomic_io.read_regular_file_bytes(source)

    with (
        patch.object(atomic_io.os, "read", side_effect=[b"xx", b""]),
        pytest.raises(ValueError, match="exceeds the allowed size"),
    ):
        atomic_io.read_regular_file_bytes(source, max_bytes=1)


@pytest.mark.skipif(os.name == "nt", reason="POSIX link semantics")
def test_private_destination_and_temporary_metadata_fail_closed(tmp_path):
    from core.helpers import atomic_io

    destination = tmp_path / "secret"
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    destination.symlink_to(outside)
    with pytest.raises(PermissionError, match="unsafe private-file destination"):
        atomic_io._atomic_write_secret_bytes(destination, b"new")

    destination.unlink()
    os.link(outside, destination)
    with pytest.raises(PermissionError, match="hard-linked"):
        atomic_io._atomic_write_secret_bytes(destination, b"new")
    destination.unlink()

    unsafe_open = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o600,
        st_nlink=1,
        st_dev=1,
        st_ino=1,
    )
    with (
        patch.object(atomic_io.os, "fstat", return_value=unsafe_open),
        pytest.raises(PermissionError, match="unsafe before install"),
    ):
        atomic_io._atomic_write_secret_bytes(destination, b"new")

    changed_open = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_nlink=1,
        st_dev=1,
        st_ino=1,
    )
    with (
        patch.object(atomic_io.os, "fstat", return_value=changed_open),
        pytest.raises(PermissionError, match="changed before install"),
    ):
        atomic_io._atomic_write_secret_bytes(destination, b"new")
