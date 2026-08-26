from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import stat
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Any, cast

from core.storage.activity_store import merge_recovery_journal_into_database

from core.helpers.atomic_io import (
    _atomic_write_secret_bytes,
    _decrypt_secret_bytes,
    _is_secret_envelope,
    fsync_directory,
    legacy_secret_storage_key_candidates,
    read_regular_file_bytes,
)
from core.helpers.encryption import (
    encrypt_registered_plaintext_credentials,
    validate_protected_credentials,
)
from core.helpers.maintenance_transaction import (
    configuration_maintenance_lock,
    recover_maintenance_transactions,
    replace_config_files_transactionally,
)
from core.helpers.protected_credentials import (
    clear_protected_values_and_disable,
    encrypted_protected_values,
)
from cryptography.fernet import InvalidToken
from pydantic import ValidationError

from .schemas import AppSettings


def _load_current_schema_version() -> int:
    try:
        migration = import_module("core.helpers.migration")
    except ImportError:
        return 7
    return int(getattr(migration, "CURRENT_SCHEMA_VERSION", 7))


CURRENT_SCHEMA_VERSION: int = _load_current_schema_version()

_SENSITIVE_SUBFOLDER = "sensitive_keys"
MAX_RESTORE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_RESTORE_MEMBER_BYTES = 32 * 1024 * 1024
MAX_RESTORE_MANIFEST_BYTES = 1024 * 1024
MAX_RESTORE_TOTAL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_RESTORE_MEMBER_COUNT = 256
MAX_RESTORE_COMPRESSION_RATIO = 500
MAX_SETTINGS_FILE_BYTES = 8 * 1024 * 1024
MAX_STORED_KEY_BYTES = 4096
MIN_COMPRESSION_RATIO_CHECK_BYTES = 1024 * 1024

_SECURITY_WARNING_TEXT = (
    "SECURITY WARNING\n"
    "================\n\n"
    "This folder contains the encryption key used to protect DVR API keys and\n"
    "notification credentials stored in settings.json.\n\n"
    "DO NOT share this backup archive with untrusted parties.\n\n"
    "The encryption key alone does not expose secrets — it only becomes sensitive\n"
    "when paired with settings.json. Together they allow decryption of protected\n"
    "credentials.\n\n"
    "If restoring to a different machine, ensure the destination volume is secured\n"
    "at least as well as the source.\n"
)


class RestoreValidationError(Exception):
    pass


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_settings_schema_version(config_dir: Path) -> int:
    settings_file = config_dir / "settings.json"
    if settings_file.exists() or settings_file.is_symlink():
        try:
            data = json.loads(
                read_regular_file_bytes(
                    settings_file,
                    max_bytes=MAX_SETTINGS_FILE_BYTES,
                ).decode("utf-8-sig")
            )
            if isinstance(data, dict):
                v = data.get("_version")
                if isinstance(v, int):
                    return v
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            pass
    return CURRENT_SCHEMA_VERSION


def _sqlite_snapshot_bytes(
    database_file: Path,
    *,
    activity_journal: Path | None = None,
) -> bytes:
    database_exists = database_file.exists() or database_file.is_symlink()
    source_bytes = read_regular_file_bytes(database_file) if database_exists else b""
    header = source_bytes[:16]
    if database_exists and header != b"SQLite format 3\x00":
        # Historical tests and pre-database installations may contain a
        # placeholder/non-SQLite file. Preserve it instead of fabricating data.
        return source_bytes
    if not database_exists and not (
        activity_journal is not None and activity_journal.exists()
    ):
        return b""
    with tempfile.TemporaryDirectory(prefix="channelwatch-db-backup-") as temp_dir:
        snapshot = Path(temp_dir) / "channelwatch.db"
        if database_exists:
            source = sqlite3.connect(f"file:{database_file}?mode=ro", uri=True)
            destination = sqlite3.connect(str(snapshot))
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        if activity_journal is not None and activity_journal.exists():
            merge_recovery_journal_into_database(snapshot, activity_journal)
        return snapshot.read_bytes()


def _private_snapshot_token() -> str:
    return secrets.token_hex(16)


def _reserve_unique_private_snapshot(backups_dir: Path, timestamp: str) -> Path:
    """Reserve one owner-only snapshot name without replacing a collision."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _attempt in range(128):
        candidate = backups_dir / (
            f"pre-restore.{timestamp}.{_private_snapshot_token()}.zip"
        )
        try:
            descriptor = os.open(str(candidate), flags, 0o600)
        except FileExistsError:
            continue
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        fsync_directory(backups_dir)
        return candidate
    raise FileExistsError("Could not reserve a unique private restore snapshot.")


def _create_backup_zip_unlocked(config_dir: Path) -> bytes:
    ts = _utc_timestamp()
    prefix = f"channelwatch_backup_{ts}"
    settings_schema_version = _read_settings_schema_version(config_dir)

    buf = io.BytesIO()
    files_included: list[str] = []

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:

        def _add(
            src: Path,
            arcname: str,
            *,
            max_bytes: int | None = None,
        ) -> None:
            if src.exists() or src.is_symlink():
                zf.writestr(
                    f"{prefix}/{arcname}",
                    read_regular_file_bytes(src, max_bytes=max_bytes),
                )
                files_included.append(arcname)

        def _add_bytes(payload: bytes, arcname: str) -> None:
            zf.writestr(f"{prefix}/{arcname}", payload)
            files_included.append(arcname)

        _add(
            config_dir / "settings.json",
            "settings.json",
            max_bytes=MAX_SETTINGS_FILE_BYTES,
        )
        database_file = config_dir / "channelwatch.db"
        activity_journal = config_dir / "activity_history.json"
        if (
            database_file.exists()
            or database_file.is_symlink()
            or activity_journal.exists()
        ):
            _add_bytes(
                _sqlite_snapshot_bytes(
                    database_file,
                    activity_journal=activity_journal,
                ),
                "channelwatch.db",
            )

        for state_file in sorted(config_dir.glob("session_state_*.json")):
            _add(state_file, state_file.name)

        enc_key = config_dir / "encryption.key"
        key_format = "missing"
        if enc_key.exists() or enc_key.is_symlink():
            stored_key = read_regular_file_bytes(
                enc_key,
                max_bytes=MAX_STORED_KEY_BYTES,
            )
            key_format = (
                "legacy-envelope-v1"
                if _is_secret_envelope(stored_key)
                else "managed-local-raw-v1"
            )
            _add_bytes(stored_key, f"{_SENSITIVE_SUBFOLDER}/encryption.key")
            zf.writestr(
                f"{prefix}/{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt",
                _SECURITY_WARNING_TEXT,
            )
            files_included.append(f"{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt")

        manifest = {
            "backup_schema_version": 2,
            "settings_schema_version": settings_schema_version,
            "encryption_key_format": key_format,
            "created_at": ts,
            "created_by": "channelwatch-ui",
            "files": files_included,
        }
        zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest, indent=2))

    return buf.getvalue()


def create_backup_zip(config_dir: Path, *, lock_already_held: bool = False) -> bytes:
    """Create a key/settings-consistent backup under the maintenance lock."""

    if lock_already_held:
        recover_maintenance_transactions(config_dir)
        return _create_backup_zip_unlocked(config_dir)
    with configuration_maintenance_lock(config_dir):
        recover_maintenance_transactions(config_dir)
        return _create_backup_zip_unlocked(config_dir)


def _validate_zip_member_path(name: str) -> PurePosixPath:
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or "//" in name
        or unicodedata.normalize("NFC", name) != name
    ):
        raise RestoreValidationError(
            f"Backup archive contains an ambiguous member path: {name!r}."
        )
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise RestoreValidationError(
            f"Backup archive contains unsafe member path: {name!r}."
        )
    return path


def _is_allowed_restore_member(rel: str) -> bool:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts:
        return False

    if rel in {"settings.json", "channelwatch.db", "backup_manifest.json"}:
        return True
    if rel.startswith("session_state_") and path.name == rel and rel.endswith(".json"):
        return True
    if rel == f"{_SENSITIVE_SUBFOLDER}/encryption.key":
        return True
    return rel == f"{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt"


def _restore_filename_for_member(rel: str) -> str | None:
    if not rel or rel.endswith("/") or rel == "backup_manifest.json":
        return None
    if rel == f"{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt":
        return None
    if not _is_allowed_restore_member(rel):
        raise RestoreValidationError(
            f"Backup archive contains unsupported restore member path: {rel!r}."
        )

    if rel.startswith(f"{_SENSITIVE_SUBFOLDER}/"):
        filename = rel[len(f"{_SENSITIVE_SUBFOLDER}/") :]
    else:
        filename = rel

    dest_path = PurePosixPath(filename)
    if dest_path.is_absolute() or ".." in dest_path.parts or len(dest_path.parts) != 1:
        raise RestoreValidationError(
            f"Backup archive contains unsafe restore destination: {rel!r}."
        )
    return filename


def _safe_restore_destination(config_dir: Path, filename: str) -> Path:
    config_root = config_dir.resolve()
    dest = (config_root / filename).resolve()
    if dest.parent != config_root:
        raise RestoreValidationError(
            f"Backup archive restore destination escapes config directory: {filename!r}."
        )
    return dest


def _parse_manifest(zf: zipfile.ZipFile) -> tuple[dict[str, Any], str]:
    manifest_paths = [n for n in zf.namelist() if n.endswith("/backup_manifest.json")]
    if len(manifest_paths) != 1:
        raise RestoreValidationError(
            "Backup archive must contain exactly one backup_manifest.json."
        )
    try:
        manifest = json.loads(zf.read(manifest_paths[0]))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise RestoreValidationError(
            f"backup_manifest.json contains invalid JSON: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise RestoreValidationError("backup_manifest.json must contain a JSON object.")
    prefix = manifest_paths[0].rsplit("/backup_manifest.json", 1)[0]
    return cast(dict[str, Any], manifest), prefix


def _validate_restore_zip_info(zf: zipfile.ZipFile) -> None:
    member_count = 0
    total_uncompressed = 0
    canonical_names: set[str] = set()
    for info in zf.infolist():
        _ = _validate_zip_member_path(info.filename)
        if info.flag_bits & 0x1:
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} is encrypted."
            )
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} uses unsupported compression."
            )
        canonical = unicodedata.normalize("NFC", info.filename).casefold()
        if canonical in canonical_names:
            raise RestoreValidationError(
                f"Backup archive contains a duplicate or colliding member: {info.filename!r}."
            )
        canonical_names.add(canonical)

        unix_mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(unix_mode)
        if file_type and (
            (info.is_dir() and file_type != stat.S_IFDIR)
            or (not info.is_dir() and file_type != stat.S_IFREG)
        ):
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} is not a regular file/directory."
            )
        offset = 0
        while offset + 4 <= len(info.extra):
            field_id = int.from_bytes(info.extra[offset : offset + 2], "little")
            field_size = int.from_bytes(info.extra[offset + 2 : offset + 4], "little")
            offset += 4
            if offset + field_size > len(info.extra):
                raise RestoreValidationError(
                    f"Backup archive member {info.filename!r} has malformed extra metadata."
                )
            if field_id in {0x000D, 0x756E}:
                raise RestoreValidationError(
                    f"Backup archive member {info.filename!r} has unsupported link metadata."
                )
            offset += field_size
        if offset != len(info.extra):
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} has malformed extra metadata."
            )
        if not info.is_dir():
            member_count += 1
            total_uncompressed += info.file_size
            if member_count > MAX_RESTORE_MEMBER_COUNT:
                raise RestoreValidationError(
                    "Backup archive exceeds the restore member count limit."
                )
            if total_uncompressed > MAX_RESTORE_TOTAL_UNCOMPRESSED_BYTES:
                raise RestoreValidationError(
                    "Backup archive exceeds the restore total uncompressed size limit."
                )
            if info.file_size >= MIN_COMPRESSION_RATIO_CHECK_BYTES and (
                info.compress_size <= 0
                or (info.file_size / info.compress_size) > MAX_RESTORE_COMPRESSION_RATIO
            ):
                raise RestoreValidationError(
                    f"Backup archive member {info.filename!r} exceeds the compression-ratio limit."
                )
        if info.file_size > MAX_RESTORE_MEMBER_BYTES:
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} exceeds the restore member size limit."
            )
        if info.compress_size > MAX_RESTORE_ARCHIVE_BYTES:
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} exceeds the compressed size limit."
            )
        if info.filename.endswith("/backup_manifest.json") and (
            info.file_size > MAX_RESTORE_MANIFEST_BYTES
        ):
            raise RestoreValidationError(
                "backup_manifest.json exceeds the restore manifest size limit."
            )


def validate_restore_zip(zip_bytes: bytes) -> dict[str, Any]:
    if len(zip_bytes) > MAX_RESTORE_ARCHIVE_BYTES:
        raise RestoreValidationError(
            "Backup archive exceeds the restore upload size limit."
        )

    try:
        buf = io.BytesIO(zip_bytes)
        zf = zipfile.ZipFile(buf, "r")
    except zipfile.BadZipFile as exc:
        raise RestoreValidationError(f"Not a valid zip file: {exc}") from exc

    with zf:
        _validate_restore_zip_info(zf)

        bad_member = zf.testzip()
        if bad_member is not None:
            raise RestoreValidationError(
                f"Archive integrity check failed: member '{bad_member}' has a bad CRC."
            )

        manifest, prefix = _parse_manifest(zf)
        if not prefix or prefix in {".", ".."} or "/" in prefix or "\\" in prefix:
            raise RestoreValidationError(
                "Backup archive has an invalid top-level prefix."
            )

        backup_sv = manifest.get("settings_schema_version")
        if not isinstance(backup_sv, int):
            raise RestoreValidationError(
                "backup_manifest.json is missing a valid 'settings_schema_version' integer field."
            )

        if backup_sv > CURRENT_SCHEMA_VERSION:
            raise RestoreValidationError(
                f"Backup schema version ({backup_sv}) is ahead of this installation "
                f"({CURRENT_SCHEMA_VERSION}). Upgrade ChannelWatch before restoring."
            )

        names = set(zf.namelist())
        prefix_with_slash = f"{prefix}/"
        for name in names:
            if not name.startswith(prefix_with_slash):
                raise RestoreValidationError(
                    f"Backup archive member is outside the declared prefix: {name!r}."
                )
            rel = name[len(prefix_with_slash) :]
            if rel and not rel.endswith("/") and not _is_allowed_restore_member(rel):
                raise RestoreValidationError(
                    f"Backup archive contains unsupported restore member path: {rel!r}."
                )

        if f"{prefix}/settings.json" not in names:
            raise RestoreValidationError(
                "Backup is missing settings.json — archive may be incomplete."
            )

        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list) or not all(
            isinstance(item, str) for item in manifest_files
        ):
            raise RestoreValidationError(
                "backup_manifest.json is missing a valid 'files' list."
            )
        if len(manifest_files) != len(set(manifest_files)):
            raise RestoreValidationError(
                "backup_manifest.json contains duplicate file entries."
            )
        for manifest_file in manifest_files:
            _validate_zip_member_path(manifest_file)
            if not _is_allowed_restore_member(manifest_file):
                raise RestoreValidationError(
                    "backup_manifest.json contains an unsupported file entry."
                )
        actual_files = {
            name[len(prefix_with_slash) :]
            for name in names
            if name.startswith(prefix_with_slash)
            and not name.endswith("/")
            and not name.endswith("/backup_manifest.json")
        }
        if set(manifest_files) != actual_files:
            raise RestoreValidationError(
                "backup_manifest.json files do not match archive payload members."
            )

        key_member = f"{prefix}/{_SENSITIVE_SUBFOLDER}/encryption.key"
        stored_key = zf.read(key_member) if key_member in names else None
        actual_key_format = "missing"
        if stored_key is not None:
            if _is_secret_envelope(stored_key):
                actual_key_format = "legacy-envelope-v1"
            elif len(stored_key) == 32:
                actual_key_format = "managed-local-raw-v1"
            else:
                actual_key_format = "invalid"
        declared_key_format = manifest.get("encryption_key_format")
        backup_schema = manifest.get("backup_schema_version")
        if isinstance(backup_schema, int) and backup_schema >= 2:
            if declared_key_format != actual_key_format:
                raise RestoreValidationError(
                    "backup_manifest.json encryption_key_format does not match the stored key."
                )
        elif (
            declared_key_format is not None and declared_key_format != actual_key_format
        ):
            raise RestoreValidationError(
                "backup_manifest.json encryption_key_format does not match the stored key."
            )

    return manifest


def _decode_legacy_backup_key(
    stored_key: bytes,
    legacy_storage_key: str | bytes | None,
) -> bytes:
    if not _is_secret_envelope(stored_key):
        if len(stored_key) != 32:
            raise RestoreValidationError(
                "Backup contains invalid managed encryption-key material."
            )
        return stored_key

    if legacy_storage_key is None:
        materials = tuple(
            candidate.material
            for candidate in legacy_secret_storage_key_candidates()
            if candidate.available and candidate.material is not None
        )
    else:
        material = (
            legacy_storage_key.encode("utf-8")
            if isinstance(legacy_storage_key, str)
            else bytes(legacy_storage_key)
        )
        materials = (material.strip(),)

    for material in materials:
        try:
            logical_key = _decrypt_secret_bytes(stored_key, material=material)
        except (InvalidToken, ValueError):
            continue
        if len(logical_key) == 32:
            return logical_key
    raise RestoreValidationError(
        "Backup uses legacy protected key material. Supply the original legacy "
        "storage key once, or restore non-secret state and reset protected credentials."
    )


def _validate_sqlite_restore_member(payload: bytes) -> None:
    if payload[:16] != b"SQLite format 3\x00":
        return
    with tempfile.TemporaryDirectory(prefix="channelwatch-db-restore-") as temp_dir:
        database = Path(temp_dir) / "channelwatch.db"
        database.write_bytes(payload)
        connection = sqlite3.connect(str(database))
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    if not result or result[0] != "ok":
        raise RestoreValidationError(
            "Backup database failed SQLite integrity validation."
        )


def _current_raw_key_if_usable(config_dir: Path) -> bytes | None:
    key_file = config_dir / "encryption.key"
    if not key_file.exists() and not key_file.is_symlink():
        return None
    try:
        stored = read_regular_file_bytes(
            key_file,
            max_bytes=MAX_STORED_KEY_BYTES,
        )
    except (OSError, PermissionError):
        return None
    try:
        return _decode_legacy_backup_key(stored, None)
    except RestoreValidationError:
        return None


def _validate_restored_settings_schema(settings: dict[str, Any]) -> None:
    """Reject invalid settings shapes without echoing credential-bearing input."""

    settings_version = settings.get("_version", 0)
    if (
        isinstance(settings_version, bool)
        or not isinstance(settings_version, int)
        or settings_version < 0
        or settings_version > CURRENT_SCHEMA_VERSION
    ):
        raise RestoreValidationError(
            "Backup settings do not match the supported ChannelWatch schema."
        )
    try:
        AppSettings.model_validate(settings)
    except ValidationError:
        raise RestoreValidationError(
            "Backup settings do not match the supported ChannelWatch schema."
        ) from None


def restore_from_zip(
    zip_bytes: bytes,
    config_dir: Path,
    *,
    legacy_storage_key: str | bytes | None = None,
    reset_protected_credentials: bool = False,
) -> dict[str, Any]:
    """Validate, normalize, and transactionally restore a ChannelWatch backup."""

    manifest = validate_restore_zip(zip_bytes)
    config_dir = Path(config_dir)
    with configuration_maintenance_lock(config_dir):
        # An interrupted older replacement owns the current generation until
        # its journal is replayed.  Recover it before selecting the current key
        # or creating the pre-restore snapshot.
        recover_maintenance_transactions(config_dir)
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            manifest_paths = [
                n for n in zf.namelist() if n.endswith("/backup_manifest.json")
            ]
            prefix = manifest_paths[0].rsplit("/backup_manifest.json", 1)[0] + "/"
            members: dict[str, bytes] = {}
            for name in zf.namelist():
                if not name.startswith(prefix):
                    continue
                rel = name[len(prefix) :]
                filename = _restore_filename_for_member(rel)
                if not filename:
                    continue
                _safe_restore_destination(config_dir, filename)
                members[filename] = zf.read(name)

        try:
            restored_settings = json.loads(members["settings.json"].decode("utf-8-sig"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise RestoreValidationError(
                "Backup settings.json is not a readable JSON object."
            ) from exc
        if not isinstance(restored_settings, dict):
            raise RestoreValidationError(
                "Backup settings.json must contain a JSON object."
            )
        _validate_restored_settings_schema(restored_settings)

        stored_key = members.pop("encryption.key", None)
        if reset_protected_credentials:
            restored_settings, _ = clear_protected_values_and_disable(
                restored_settings
            )
            logical_key = os.urandom(32)
        elif stored_key is not None:
            logical_key = _decode_legacy_backup_key(
                stored_key,
                legacy_storage_key,
            )
        else:
            encrypted = encrypted_protected_values(restored_settings)
            current_key = _current_raw_key_if_usable(config_dir)
            if encrypted and current_key is None:
                raise RestoreValidationError(
                    "Backup contains encrypted credentials but no usable encryption key."
                )
            logical_key = current_key or os.urandom(32)

        report = validate_protected_credentials(restored_settings, logical_key)
        if not report.all_valid:
            raise RestoreValidationError(
                "Backup encryption key does not open every protected credential."
            )
        restored_settings = encrypt_registered_plaintext_credentials(
            restored_settings,
            logical_key,
        )
        members["settings.json"] = json.dumps(
            restored_settings,
            indent=2,
        ).encode("utf-8")
        members["encryption.key"] = logical_key

        if "channelwatch.db" in members:
            _validate_sqlite_restore_member(members["channelwatch.db"])

        # The restored database already contains every activity event represented
        # by its backup snapshot.  Clear the current installation's recovery
        # journal in the same transaction so old JSON rows cannot mix with or
        # resurrect over the restored history.
        members["activity_history.json"] = b"[]\n"

        backups_dir = config_dir / "backups"
        try:
            backups_metadata = backups_dir.lstat()
        except FileNotFoundError:
            backups_dir.mkdir(exist_ok=False)
            backups_metadata = backups_dir.lstat()
        if stat.S_ISLNK(backups_metadata.st_mode) or not stat.S_ISDIR(
            backups_metadata.st_mode
        ):
            raise RestoreValidationError(
                "The private pre-restore backup directory is unsafe."
            )
        if os.name != "nt":
            backups_dir.chmod(0o700)
        snapshot_bytes = _create_backup_zip_unlocked(config_dir)
        snapshot_path = _reserve_unique_private_snapshot(
            backups_dir,
            _utc_timestamp(),
        )
        _atomic_write_secret_bytes(snapshot_path, snapshot_bytes)

        replace_config_files_transactionally(
            config_dir,
            members,
            lock_already_held=True,
        )
    return manifest
