"""Atomic filesystem helpers for durable config and migration writes."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SECRET_ENVELOPE_PREFIX = b"channelwatch-secret-v1\n"
_SECRET_STORAGE_KEY_ENV = "CHANNELWATCH_SECRET_STORAGE_KEY"
_SECRET_STORAGE_KEY_FILE_ENV = "CHANNELWATCH_SECRET_STORAGE_KEY_FILE"
_MIN_SECRET_STORAGE_KEY_CHARS = 32
_MAX_LEGACY_SECRET_STORAGE_KEY_FILE_BYTES = 4096


class SecretStorageKeyUnavailableError(RuntimeError):
    """Raised when encrypted local secret storage cannot be used safely."""

    def __init__(self, message: str, *, code: str = "secret_storage_key_missing"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SecretStorageKeyStatus:
    """Non-sensitive description of the external secret-storage key input."""

    available: bool
    code: str | None = None
    material: bytes | None = field(default=None, repr=False)


def secret_storage_key_status() -> SecretStorageKeyStatus:
    """Validate key input without exposing its value in logs or API responses."""

    key_file = os.getenv(_SECRET_STORAGE_KEY_FILE_ENV, "").strip()
    if key_file:
        try:
            value = _read_legacy_secret_storage_key_file(Path(key_file))
        except (OSError, UnicodeError, ValueError):
            return SecretStorageKeyStatus(
                available=False,
                code="secret_storage_key_file_unreadable",
            )
    else:
        value = os.getenv(_SECRET_STORAGE_KEY_ENV, "").strip()

    if not value:
        return SecretStorageKeyStatus(
            available=False,
            code="secret_storage_key_missing",
        )
    if len(value) < _MIN_SECRET_STORAGE_KEY_CHARS:
        return SecretStorageKeyStatus(
            available=False,
            code="secret_storage_key_too_short",
        )
    return SecretStorageKeyStatus(available=True, material=value.encode("utf-8"))


def legacy_secret_storage_key_candidates() -> tuple[SecretStorageKeyStatus, ...]:
    """Return legacy envelope-key inputs in deterministic migration order.

    The file input historically took precedence.  v0.9.18 keeps both inputs
    only as migration/recovery sources and may fall back to the environment
    value when the file input cannot open an existing legacy envelope.
    """

    candidates: list[SecretStorageKeyStatus] = []
    key_file = os.getenv(_SECRET_STORAGE_KEY_FILE_ENV, "").strip()
    if key_file:
        try:
            value = _read_legacy_secret_storage_key_file(Path(key_file))
        except (OSError, UnicodeError, ValueError):
            candidates.append(
                SecretStorageKeyStatus(
                    available=False,
                    code="secret_storage_key_file_unreadable",
                )
            )
        else:
            if not value:
                candidates.append(
                    SecretStorageKeyStatus(
                        available=False,
                        code="secret_storage_key_missing",
                    )
                )
            elif len(value) < _MIN_SECRET_STORAGE_KEY_CHARS:
                candidates.append(
                    SecretStorageKeyStatus(
                        available=False,
                        code="secret_storage_key_too_short",
                    )
                )
            else:
                candidates.append(
                    SecretStorageKeyStatus(
                        available=True,
                        material=value.encode("utf-8"),
                    )
                )

    environment_value = os.getenv(_SECRET_STORAGE_KEY_ENV, "").strip()
    if environment_value:
        if len(environment_value) < _MIN_SECRET_STORAGE_KEY_CHARS:
            candidates.append(
                SecretStorageKeyStatus(
                    available=False,
                    code="secret_storage_key_too_short",
                )
            )
        else:
            candidates.append(
                SecretStorageKeyStatus(
                    available=True,
                    material=environment_value.encode("utf-8"),
                )
            )

    if not candidates:
        candidates.append(
            SecretStorageKeyStatus(
                available=False,
                code="secret_storage_key_missing",
            )
        )
    return tuple(candidates)


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
    status = secret_storage_key_status()
    if status.available and status.material is not None:
        return status.material

    if status.code == "secret_storage_key_file_unreadable":
        message = (
            f"{_SECRET_STORAGE_KEY_FILE_ENV} points to a key file that cannot be read."
        )
    else:
        message = (
            f"{_SECRET_STORAGE_KEY_ENV} must be set to at least "
            f"{_MIN_SECRET_STORAGE_KEY_CHARS} characters before writing local secrets."
        )
    raise SecretStorageKeyUnavailableError(
        message,
        code=status.code or "secret_storage_key_missing",
    )


def _make_secret_storage_fernet(material: bytes | None = None):
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(
        material if material is not None else _load_secret_storage_key_material()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_secret_bytes(data: bytes, *, material: bytes | None = None) -> bytes:
    """Build the legacy v1 envelope.

    New v0.9.18 writes never call this helper.  It remains for reading old
    installations, historical fixtures, and downgrade compatibility tests.
    """

    token = _make_secret_storage_fernet(material).encrypt(data)
    return _SECRET_ENVELOPE_PREFIX + token + b"\n"


def _is_secret_envelope(data: bytes) -> bool:
    return data.startswith(_SECRET_ENVELOPE_PREFIX)


def _decrypt_secret_bytes(data: bytes, *, material: bytes | None = None) -> bytes:
    if not _is_secret_envelope(data):
        return data
    token = data[len(_SECRET_ENVELOPE_PREFIX) :].strip()
    if not token:
        raise ValueError("Encrypted secret envelope is empty.")
    return _make_secret_storage_fernet(material).decrypt(token)


def _atomic_read_secret_bytes(path: Path) -> bytes:
    return _decrypt_secret_bytes(read_regular_file_bytes(path))


def read_regular_file_bytes(path: Path, *, max_bytes: int | None = None) -> bytes:
    """Read one stable, single-link regular file without following symlinks."""

    source = Path(path)
    metadata = source.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_nlink", 1) != 1
    ):
        raise PermissionError(f"Refusing unsafe file source: {source}")
    if max_bytes is not None and metadata.st_size > max_bytes:
        raise ValueError(f"File source exceeds the allowed size: {source}")

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(source), flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise PermissionError(f"File source changed while opening: {source}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(f"File source exceeds the allowed size: {source}")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _read_legacy_secret_storage_key_file(path: Path) -> str:
    """Read one bounded, stable legacy migration input without following links."""

    return read_regular_file_bytes(
        path,
        max_bytes=_MAX_LEGACY_SECRET_STORAGE_KEY_FILE_BYTES,
    ).decode("utf-8").strip()


def _validate_private_destination(path: Path) -> None:
    """Refuse to replace a linked or special private-file destination."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PermissionError(f"Refusing unsafe private-file destination: {path}")
    if getattr(metadata, "st_nlink", 1) != 1:
        raise PermissionError(f"Refusing hard-linked private-file destination: {path}")


def _atomic_write_secret_bytes(
    path: Path, data: bytes, *, temp_path: Path | None = None
) -> Path:
    """Atomically persist application-managed private bytes with mode 0600.

    v0.9.18 deliberately stores the logical encryption key in the persistent
    application configuration instead of requiring an operator-managed wrapper
    key.  Legacy envelope creation is available only through
    :func:`_encrypt_secret_bytes` for compatibility tests.
    """

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_private_destination(destination)
    temp = temp_path or destination.with_name(
        f".{destination.name}.tmp.{os.getpid()}.{secrets.token_hex(6)}"
    )
    try:
        temp.unlink()
    except FileNotFoundError:
        pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    fd = os.open(str(temp), flags, 0o600)
    try:
        opened_metadata: os.stat_result
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
            opened_metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or getattr(opened_metadata, "st_nlink", 1) != 1
                or (
                    os.name != "nt"
                    and stat.S_IMODE(opened_metadata.st_mode) != 0o600
                )
            ):
                raise PermissionError(
                    f"Private temporary file is unsafe before install: {temp}"
                )
        named_metadata = temp.lstat()
        if (
            not stat.S_ISREG(named_metadata.st_mode)
            or getattr(named_metadata, "st_nlink", 1) != 1
            or (named_metadata.st_dev, named_metadata.st_ino)
            != (opened_metadata.st_dev, opened_metadata.st_ino)
        ):
            raise PermissionError(
                f"Private temporary file changed before install: {temp}"
            )
        os.replace(temp, destination)
        installed_metadata = destination.lstat()
        if (
            not stat.S_ISREG(installed_metadata.st_mode)
            or getattr(installed_metadata, "st_nlink", 1) != 1
            or (installed_metadata.st_dev, installed_metadata.st_ino)
            != (opened_metadata.st_dev, opened_metadata.st_ino)
            or (
                os.name != "nt"
                and stat.S_IMODE(installed_metadata.st_mode) != 0o600
            )
        ):
            raise PermissionError(
                f"Installed private file could not be verified safely: {destination}"
            )
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


def atomic_write_private_json(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
) -> Path:
    """Durably write credential-bearing JSON with owner-only permissions."""

    serialized = json.dumps(payload, indent=indent, sort_keys=sort_keys)
    return _atomic_write_secret_bytes(Path(path), serialized.encode("utf-8"))


def atomic_copy_file(
    source: Path, destination: Path, *, temp_path: Path | None = None
) -> Path:
    return atomic_write_bytes(
        destination, read_regular_file_bytes(source), temp_path=temp_path
    )
