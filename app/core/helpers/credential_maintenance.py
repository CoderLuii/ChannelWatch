"""Transactional protected-credential reset and managed-key rotation."""

from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_io import (
    _atomic_write_secret_bytes,
    fsync_directory,
    read_regular_file_bytes,
)
from .encryption import (
    reencrypt_registered_credentials,
    validate_protected_credentials,
)
from .key_manager import (
    MANAGED_KEY_BYTES,
    MAX_SETTINGS_FILE_BYTES,
    MAX_STORED_KEY_BYTES,
    ensure_managed_key,
)
from .maintenance_transaction import (
    configuration_maintenance_lock,
    recover_maintenance_transactions,
    replace_config_files_transactionally,
)
from .protected_credentials import clear_protected_values_and_disable


@dataclass(frozen=True)
class ProtectedCredentialResetResult:
    cleared_credentials: int
    recovery_snapshot: Path


@dataclass(frozen=True)
class CredentialRotationResult:
    rotated_credentials: int
    recovery_snapshot: Path


def _parse_settings(payload_bytes: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Unable to read settings for credential maintenance."
        ) from exc
    if not isinstance(payload, dict):
        raise TypeError("Settings must contain a JSON object.")
    return payload


def _serialized_settings(settings: dict[str, Any]) -> bytes:
    return json.dumps(settings, indent=2).encode("utf-8")


def _ensure_private_directory(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise PermissionError(
            f"Refusing unsafe private recovery directory: {path}"
        ) from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PermissionError(f"Refusing unsafe private recovery directory: {path}")
    if os.name == "nt":  # pragma: no cover - Windows cannot open directories as fds
        return

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(str(path), flags)
    try:
        opened = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise PermissionError(
                f"Refusing unsafe private recovery directory: {path}"
            )
        os.fchmod(directory_fd, 0o700)
        opened = os.fstat(directory_fd)
        if stat.S_IMODE(opened.st_mode) != 0o700:
            raise PermissionError(
                f"Private recovery directory permissions are unsafe: {path}"
            )
        final_metadata = path.lstat()
        if (
            stat.S_ISLNK(final_metadata.st_mode)
            or not stat.S_ISDIR(final_metadata.st_mode)
            or (final_metadata.st_dev, final_metadata.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise PermissionError(
                f"Private recovery directory changed during verification: {path}"
            )
    finally:
        os.close(directory_fd)


def _private_snapshot_token() -> str:
    return secrets.token_hex(16)


def _create_unique_private_directory(parent: Path, prefix: str) -> Path:
    """Reserve a recovery directory without ever reusing an existing name."""

    for _attempt in range(128):
        candidate = parent / f"{prefix}-{_private_snapshot_token()}"
        try:
            os.mkdir(candidate, 0o700)
        except FileExistsError:
            continue
        _ensure_private_directory(candidate)
        fsync_directory(parent)
        return candidate
    raise FileExistsError("Could not reserve a unique private recovery directory.")


def _private_recovery_snapshot(
    config_dir: Path,
    *,
    purpose: str,
    settings_bytes: bytes,
    stored_key_bytes: bytes | None,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups_dir = config_dir / "backups"
    recovery_dir = backups_dir / "key-recovery"
    _ensure_private_directory(backups_dir)
    _ensure_private_directory(recovery_dir)
    snapshot = _create_unique_private_directory(
        recovery_dir,
        f"{purpose}-{timestamp}",
    )
    _atomic_write_secret_bytes(snapshot / "settings.json", settings_bytes)
    if stored_key_bytes is not None:
        _atomic_write_secret_bytes(snapshot / "encryption.key", stored_key_bytes)
    manifest_bytes = json.dumps(
        {
            "version": 1,
            "purpose": purpose,
            "created_at": timestamp,
            "contains_credentials": True,
        },
        indent=2,
    ).encode("utf-8")
    _atomic_write_secret_bytes(
        snapshot / "manifest.json",
        manifest_bytes,
    )
    fsync_directory(snapshot)
    fsync_directory(snapshot.parent)
    return snapshot


def reset_protected_credentials(
    config_dir: Path,
    *,
    settings_file: Path | None = None,
    key_file: Path | None = None,
) -> ProtectedCredentialResetResult:
    """Clear only registered credentials and install a fresh managed key."""

    config_dir = Path(config_dir)
    settings_path = settings_file or config_dir / "settings.json"
    key_path = key_file or config_dir / "encryption.key"
    with configuration_maintenance_lock(config_dir):
        # Resolve a killed writer before reading the key/settings generation
        # that this destructive operation will snapshot and replace.
        recover_maintenance_transactions(config_dir)
        original_settings = read_regular_file_bytes(
            settings_path,
            max_bytes=MAX_SETTINGS_FILE_BYTES,
        )
        settings = _parse_settings(original_settings)
        original_key = (
            read_regular_file_bytes(key_path, max_bytes=MAX_STORED_KEY_BYTES)
            if key_path.exists() or key_path.is_symlink()
            else None
        )
        snapshot = _private_recovery_snapshot(
            config_dir,
            purpose="protected-credential-reset",
            settings_bytes=original_settings,
            stored_key_bytes=original_key,
        )
        cleared_settings, cleared_count = clear_protected_values_and_disable(settings)
        new_key = os.urandom(MANAGED_KEY_BYTES)
        replace_config_files_transactionally(
            config_dir,
            {
                settings_path.name: _serialized_settings(cleared_settings),
                key_path.name: new_key,
            },
            lock_already_held=True,
        )
    return ProtectedCredentialResetResult(
        cleared_credentials=cleared_count,
        recovery_snapshot=snapshot,
    )


def rotate_managed_encryption_key(
    config_dir: Path,
    *,
    settings_file: Path | None = None,
    key_file: Path | None = None,
) -> CredentialRotationResult:
    """Rotate the managed key and every registered credential transactionally."""

    config_dir = Path(config_dir)
    settings_path = settings_file or config_dir / "settings.json"
    key_path = key_file or config_dir / "encryption.key"
    with configuration_maintenance_lock(config_dir):
        # Rotation must derive its payload from the generation selected by any
        # older durable journal, never from a transient pre-recovery pair.
        recover_maintenance_transactions(config_dir)
        current = ensure_managed_key(key_path, settings_file=settings_path)
        original_settings = read_regular_file_bytes(
            settings_path,
            max_bytes=MAX_SETTINGS_FILE_BYTES,
        )
        settings = _parse_settings(original_settings)
        report = validate_protected_credentials(settings, current.key)
        if not report.all_valid:
            raise RuntimeError(
                "Encryption key rotation refused because protected credentials are unreadable."
            )
        new_key = os.urandom(MANAGED_KEY_BYTES)
        rotated_settings, rotated_count = reencrypt_registered_credentials(
            settings,
            current.key,
            new_key,
        )
        snapshot = _private_recovery_snapshot(
            config_dir,
            purpose="encryption-key-rotation",
            settings_bytes=original_settings,
            stored_key_bytes=read_regular_file_bytes(
                key_path,
                max_bytes=MAX_STORED_KEY_BYTES,
            ),
        )
        replace_config_files_transactionally(
            config_dir,
            {
                settings_path.name: _serialized_settings(rotated_settings),
                key_path.name: new_key,
            },
            lock_already_held=True,
        )
    return CredentialRotationResult(
        rotated_credentials=rotated_count,
        recovery_snapshot=snapshot,
    )
