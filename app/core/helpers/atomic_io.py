"""Atomic filesystem helpers for durable config and migration writes."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

_SECRET_ENVELOPE_PREFIX = b"channelwatch-secret-v1\n"
_SECRET_STORAGE_KEY_ENV = "CHANNELWATCH_SECRET_STORAGE_KEY"
_SECRET_STORAGE_KEY_FILE_ENV = "CHANNELWATCH_SECRET_STORAGE_KEY_FILE"
_MIN_SECRET_STORAGE_KEY_CHARS = 32


class SecretStorageKeyUnavailableError(RuntimeError):
    """Raised when encrypted local secret storage cannot be used safely."""


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return

    directory = Path(path)
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(
    path: Path, payload_bytes: bytes, *, temp_path: Path | None = None
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = temp_path or destination.with_name(f"{destination.name}.tmp")

    try:
        temp.unlink()
    except FileNotFoundError:
        pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    fd = os.open(str(temp), flags, 0o666)
    try:
        view = memoryview(payload_bytes)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("Atomic write made no progress.")
            view = view[written:]
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(fd)

    os.replace(temp, destination)
    fsync_directory(destination.parent)
    return destination


def _load_secret_storage_key_material() -> bytes:
    key_file = os.getenv(_SECRET_STORAGE_KEY_FILE_ENV, "").strip()
    if key_file:
        try:
            value = Path(key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretStorageKeyUnavailableError(
                f"{_SECRET_STORAGE_KEY_FILE_ENV} points to a key file that cannot be read."
            ) from exc
    else:
        value = os.getenv(_SECRET_STORAGE_KEY_ENV, "").strip()

    if len(value) < _MIN_SECRET_STORAGE_KEY_CHARS:
        raise SecretStorageKeyUnavailableError(
            f"{_SECRET_STORAGE_KEY_ENV} must be set to at least "
            f"{_MIN_SECRET_STORAGE_KEY_CHARS} characters before writing local secrets."
        )
    return value.encode("utf-8")


def _make_secret_storage_fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(_load_secret_storage_key_material()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_secret_bytes(data: bytes) -> bytes:
    token = _make_secret_storage_fernet().encrypt(data)
    return _SECRET_ENVELOPE_PREFIX + token + b"\n"


def _is_secret_envelope(data: bytes) -> bool:
    return data.startswith(_SECRET_ENVELOPE_PREFIX)


def _decrypt_secret_bytes(data: bytes) -> bytes:
    if not _is_secret_envelope(data):
        return data
    token = data[len(_SECRET_ENVELOPE_PREFIX) :].strip()
    if not token:
        raise ValueError("Encrypted secret envelope is empty.")
    return _make_secret_storage_fernet().decrypt(token)


def _atomic_read_secret_bytes(path: Path) -> bytes:
    return _decrypt_secret_bytes(Path(path).read_bytes())


def _atomic_write_secret_bytes(
    path: Path, data: bytes, *, temp_path: Path | None = None
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = temp_path or destination.with_name(f"{destination.name}.tmp")
    encrypted = _encrypt_secret_bytes(data)
    try:
        temp.unlink()
    except FileNotFoundError:
        pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    fd = os.open(str(temp), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temp.chmod(0o600)
        os.replace(temp, destination)
        if os.name != "nt":
            destination.chmod(0o600)
        fsync_directory(destination.parent)
        return destination
    except Exception:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    temp_path: Path | None = None,
) -> Path:
    return atomic_write_bytes(path, text.encode(encoding), temp_path=temp_path)


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    temp_path: Path | None = None,
) -> Path:
    serialized = json.dumps(payload, indent=indent, sort_keys=sort_keys)
    return atomic_write_text(path, serialized, temp_path=temp_path)


def atomic_copy_file(
    source: Path, destination: Path, *, temp_path: Path | None = None
) -> Path:
    return atomic_write_bytes(
        destination, Path(source).read_bytes(), temp_path=temp_path
    )
