"""Application-managed encryption-key lifecycle and legacy-envelope migration."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .atomic_io import (
    _atomic_write_secret_bytes,
    _decrypt_secret_bytes,
    _is_secret_envelope,
    legacy_secret_storage_key_candidates,
    read_regular_file_bytes,
)
from .protected_credentials import encrypted_protected_values, iter_protected_values

MANAGED_KEY_BYTES = 32
MANAGED_KEY_MODE = 0o600
MAX_STORED_KEY_BYTES = 4096
MAX_SETTINGS_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
_IN_PROCESS_KEY_LOCK = threading.RLock()
_THREAD_LOCK_STATE = threading.local()


class ManagedKeyUnavailableError(RuntimeError):
    """A stable managed key cannot be loaded or created without data loss."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManagedKeyResult:
    key: bytes = field(repr=False)
    created: bool = False
    migrated_legacy_envelope: bool = False


@dataclass(frozen=True)
class KeyRecoveryStatus:
    """Authenticated, non-sensitive managed-key recovery diagnostics."""

    state: str
    setup_required: bool
    blocker: str | None = None
    key_mode: str = "managed_local"
    encrypted_credentials: int = 0
    unreadable_credentials: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.setup_required and not self.unreadable_credentials


def _settings_file_for(key_file: Path, settings_file: Path | None) -> Path:
    return (
        Path(settings_file)
        if settings_file is not None
        else key_file.parent / "settings.json"
    )


def _read_settings_for_key_guard(settings_file: Path) -> dict[str, object]:
    if not settings_file.exists() and not settings_file.is_symlink():
        return {}
    try:
        payload = json.loads(
            read_regular_file_bytes(
                settings_file,
                max_bytes=MAX_SETTINGS_FILE_BYTES,
            ).decode("utf-8-sig")
        )
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagedKeyUnavailableError(
            "Settings cannot be inspected safely before creating a managed key.",
            code="secret_storage_key_file_unreadable",
        ) from exc
    if not isinstance(payload, dict):
        raise ManagedKeyUnavailableError(
            "Settings must be a JSON object before creating a managed key.",
            code="secret_storage_key_file_unreadable",
        )
    return payload


def _safe_open_existing(path: Path) -> tuple[int, os.stat_result]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManagedKeyUnavailableError(
            "The managed key cannot be inspected.",
            code="secret_storage_key_file_unreadable",
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ManagedKeyUnavailableError(
            "The managed key path is not a regular file.",
            code="secret_storage_key_file_unreadable",
        )
    if getattr(metadata, "st_nlink", 1) != 1:
        raise ManagedKeyUnavailableError(
            "The managed key must not be hard linked.",
            code="secret_storage_key_file_unreadable",
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ManagedKeyUnavailableError(
            "The managed key cannot be read.",
            code="secret_storage_key_file_unreadable",
        ) from exc
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_nlink", 1) != 1:
        os.close(fd)
        raise ManagedKeyUnavailableError(
            "The managed key changed while it was being opened.",
            code="secret_storage_key_file_unreadable",
        )
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(fd)
        raise ManagedKeyUnavailableError(
            "The managed key changed while it was being opened.",
            code="secret_storage_key_file_unreadable",
        )
    return fd, opened


def _read_and_secure_existing(path: Path) -> bytes:
    fd, metadata = _safe_open_existing(path)
    try:
        if metadata.st_size > MAX_STORED_KEY_BYTES:
            raise ManagedKeyUnavailableError(
                "The managed key file exceeds the maximum supported size.",
                code="secret_storage_key_file_unreadable",
            )
        if os.name != "nt":
            mode = stat.S_IMODE(metadata.st_mode)
            if mode & 0o077:
                current_uid = (
                    os.geteuid() if hasattr(os, "geteuid") else metadata.st_uid
                )
                if current_uid != 0 and metadata.st_uid != current_uid:
                    raise ManagedKeyUnavailableError(
                        "The managed key permissions cannot be repaired safely.",
                        code="secret_storage_key_file_unreadable",
                    )
                try:
                    os.fchmod(fd, MANAGED_KEY_MODE)
                except OSError as exc:
                    raise ManagedKeyUnavailableError(
                        "The managed key permissions cannot be repaired safely.",
                        code="secret_storage_key_file_unreadable",
                    ) from exc
                if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
                    raise ManagedKeyUnavailableError(
                        "The filesystem did not enforce private key permissions.",
                        code="secret_storage_key_file_unreadable",
                    )

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_STORED_KEY_BYTES:
                raise ManagedKeyUnavailableError(
                    "The managed key file exceeds the maximum supported size.",
                    code="secret_storage_key_file_unreadable",
                )
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _acquire_platform_lock(fd: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
    else:
        import fcntl

        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
    raise ManagedKeyUnavailableError(
        "Timed out waiting for the managed-key lock.",
        code="secret_storage_key_file_unreadable",
    )


def _release_platform_lock(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _managed_key_lock_once(
    key_file: Path, *, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    key_file = Path(key_file)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = key_file.parent / ".encryption-key.lock"
    try:
        metadata = lock_path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_nlink", 1) != 1
    ):
        raise ManagedKeyUnavailableError(
            "The managed-key lock path is unsafe.",
            code="secret_storage_key_file_unreadable",
        )

    common_flags = 0
    if hasattr(os, "O_BINARY"):
        common_flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        common_flags |= os.O_NOFOLLOW
    opened_read_only = False
    try:
        if metadata is None:
            try:
                fd = os.open(
                    str(lock_path),
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | common_flags,
                    MANAGED_KEY_MODE,
                )
                metadata = os.fstat(fd)
            except FileExistsError:
                metadata = lock_path.lstat()
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISREG(metadata.st_mode)
                    or getattr(metadata, "st_nlink", 1) != 1
                ):
                    raise ManagedKeyUnavailableError(
                        "The managed-key lock path is unsafe.",
                        code="secret_storage_key_file_unreadable",
                    )
                try:
                    fd = os.open(str(lock_path), os.O_RDWR | common_flags)
                except OSError:
                    if os.name == "nt":  # pragma: no cover
                        raise
                    fd = os.open(str(lock_path), os.O_RDONLY | common_flags)
                    opened_read_only = True
        else:
            try:
                fd = os.open(str(lock_path), os.O_RDWR | common_flags)
            except OSError:
                if (
                    os.name == "nt"
                ):  # pragma: no cover - Windows locks need write access
                    raise
                fd = os.open(str(lock_path), os.O_RDONLY | common_flags)
                opened_read_only = True
    except OSError as exc:
        raise ManagedKeyUnavailableError(
            "The managed-key lock cannot be opened.",
            code="secret_storage_key_file_unreadable",
        ) from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or (
                metadata is not None
                and (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            )
        ):
            raise ManagedKeyUnavailableError(
                "The managed-key lock changed while it was being opened.",
                code="secret_storage_key_file_unreadable",
            )
        if os.name != "nt":
            current_mode = stat.S_IMODE(opened.st_mode)
            if opened_read_only:
                if current_mode != MANAGED_KEY_MODE:
                    raise ManagedKeyUnavailableError(
                        "The read-only managed-key lock must have mode 0600.",
                        code="secret_storage_key_file_unreadable",
                    )
            elif current_mode != MANAGED_KEY_MODE:
                os.fchmod(fd, MANAGED_KEY_MODE)
                if stat.S_IMODE(os.fstat(fd).st_mode) != MANAGED_KEY_MODE:
                    raise ManagedKeyUnavailableError(
                        "The managed-key lock permissions could not be secured.",
                        code="secret_storage_key_file_unreadable",
                    )
        _acquire_platform_lock(fd, timeout)
        try:
            yield
        finally:
            _release_platform_lock(fd)
    finally:
        os.close(fd)


@contextmanager
def managed_key_lock(
    key_file: Path, *, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Take a process-safe lock, allowing same-thread nesting for one config."""

    path = Path(key_file)
    identity = os.path.abspath(str(path.parent / ".encryption-key.lock"))
    if not _IN_PROCESS_KEY_LOCK.acquire(timeout=max(0.0, timeout)):
        raise ManagedKeyUnavailableError(
            "Timed out waiting for the in-process managed-key lock.",
            code="secret_storage_key_file_unreadable",
        )
    try:
        depths = getattr(_THREAD_LOCK_STATE, "depths", None)
        if depths is None:
            depths = {}
            _THREAD_LOCK_STATE.depths = depths
        depth = int(depths.get(identity, 0))
        if depth:
            depths[identity] = depth + 1
            try:
                yield
            finally:
                if depths[identity] == 1:
                    del depths[identity]
                else:
                    depths[identity] -= 1
            return

        depths[identity] = 1
        try:
            with _managed_key_lock_once(path, timeout=timeout):
                yield
        finally:
            del depths[identity]
    finally:
        _IN_PROCESS_KEY_LOCK.release()


def _cleanup_stale_key_temps(key_file: Path) -> None:
    pattern = f".{key_file.name}.tmp.*"
    current_uid = os.geteuid() if hasattr(os, "geteuid") else None
    for candidate in key_file.parent.glob(pattern):
        try:
            metadata = candidate.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or getattr(metadata, "st_nlink", 1) != 1
            ):
                continue
            if current_uid is not None and metadata.st_uid != current_uid:
                continue
            candidate.unlink()
        except OSError:
            continue


def _legacy_failure_code() -> str:
    candidates = legacy_secret_storage_key_candidates()
    if any(candidate.available for candidate in candidates):
        return "secret_storage_key_mismatch"
    codes = tuple(candidate.code for candidate in candidates if candidate.code)
    if "secret_storage_key_file_unreadable" in codes:
        return "secret_storage_key_file_unreadable"
    if "secret_storage_key_too_short" in codes:
        return "secret_storage_key_too_short"
    return "secret_storage_key_missing"


def _open_legacy_envelope(
    stored: bytes, explicit_material: bytes | None = None
) -> bytes:
    if explicit_material is not None:
        materials = (explicit_material,)
    else:
        materials = tuple(
            candidate.material
            for candidate in legacy_secret_storage_key_candidates()
            if candidate.available and candidate.material is not None
        )
    for material in materials:
        try:
            logical_key = _decrypt_secret_bytes(stored, material=material)
        except (InvalidToken, ValueError):
            continue
        if len(logical_key) == MANAGED_KEY_BYTES:
            return logical_key
    raise ManagedKeyUnavailableError(
        "The legacy protected key cannot be opened.",
        code=(
            "secret_storage_key_mismatch"
            if explicit_material is not None
            else _legacy_failure_code()
        ),
    )


def ensure_managed_key(
    key_file: Path,
    *,
    settings_file: Path | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> ManagedKeyResult:
    """Load, create, or migrate the persistent application-managed key."""

    path = Path(key_file)
    with managed_key_lock(path, timeout=lock_timeout):
        _cleanup_stale_key_temps(path)
        if path.exists() or path.is_symlink():
            stored = _read_and_secure_existing(path)
            if not _is_secret_envelope(stored):
                if len(stored) != MANAGED_KEY_BYTES:
                    raise ManagedKeyUnavailableError(
                        "The managed key has an invalid length.",
                        code="secret_storage_key_mismatch",
                    )
                return ManagedKeyResult(key=stored)
            logical_key = _open_legacy_envelope(stored)
            _atomic_write_secret_bytes(path, logical_key)
            if _read_and_secure_existing(path) != logical_key:
                raise ManagedKeyUnavailableError(
                    "The migrated managed key could not be verified.",
                    code="secret_storage_key_file_unreadable",
                )
            return ManagedKeyResult(
                key=logical_key,
                migrated_legacy_envelope=True,
            )

        settings_path = _settings_file_for(path, settings_file)
        settings = _read_settings_for_key_guard(settings_path)
        if encrypted_protected_values(settings):
            raise ManagedKeyUnavailableError(
                "Encrypted credentials exist but their managed key is missing.",
                code="secret_storage_key_missing",
            )
        logical_key = os.urandom(MANAGED_KEY_BYTES)
        if tuple(iter_protected_values(settings)):
            # A historical plaintext configuration must never gain a key in one
            # durable step and encrypted settings in a later one.  Journal both
            # files as a single recoverable initialization transaction.
            from .encryption import encrypt_registered_plaintext_credentials
            from .maintenance_transaction import replace_config_files_transactionally

            encrypted_settings = encrypt_registered_plaintext_credentials(
                settings,
                logical_key,
            )
            replace_config_files_transactionally(
                path.parent,
                {
                    path.name: logical_key,
                    settings_path.name: json.dumps(
                        encrypted_settings,
                        indent=2,
                    ).encode("utf-8"),
                },
                lock_already_held=True,
            )
        else:
            _atomic_write_secret_bytes(path, logical_key)
        if _read_and_secure_existing(path) != logical_key:
            raise ManagedKeyUnavailableError(
                "The new managed key could not be verified.",
                code="secret_storage_key_file_unreadable",
            )
        return ManagedKeyResult(key=logical_key, created=True)


def recover_legacy_envelope(
    key_file: Path,
    legacy_storage_key: str | bytes,
    *,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> ManagedKeyResult:
    """Use a legacy wrapping key once, then convert to managed local storage."""

    material = (
        legacy_storage_key.encode("utf-8")
        if isinstance(legacy_storage_key, str)
        else bytes(legacy_storage_key)
    )
    if len(material.strip()) < 32:
        raise ManagedKeyUnavailableError(
            "The legacy storage key is too short.",
            code="secret_storage_key_too_short",
        )
    path = Path(key_file)
    with managed_key_lock(path, timeout=lock_timeout):
        stored = _read_and_secure_existing(path)
        if not _is_secret_envelope(stored):
            if len(stored) != MANAGED_KEY_BYTES:
                raise ManagedKeyUnavailableError(
                    "The managed key has an invalid length.",
                    code="secret_storage_key_mismatch",
                )
            return ManagedKeyResult(key=stored)
        logical_key = _open_legacy_envelope(stored, material.strip())
        _atomic_write_secret_bytes(path, logical_key)
        if _read_and_secure_existing(path) != logical_key:
            raise ManagedKeyUnavailableError(
                "The recovered managed key could not be verified.",
                code="secret_storage_key_file_unreadable",
            )
        return ManagedKeyResult(key=logical_key, migrated_legacy_envelope=True)


def candidate_decrypts_all_protected_values(
    settings: dict[str, object], candidate_key: bytes
) -> bool:
    encrypted = encrypted_protected_values(settings)
    if not encrypted or len(candidate_key) != MANAGED_KEY_BYTES:
        return False
    fernet = Fernet(base64.urlsafe_b64encode(candidate_key))
    try:
        for item in encrypted:
            fernet.decrypt(item.value[len("fernet:") :].encode("ascii"))
    except (InvalidToken, UnicodeError, ValueError):
        return False
    return True


def install_recovered_raw_key(
    key_file: Path,
    candidate_key: bytes,
    *,
    settings_file: Path | None = None,
) -> ManagedKeyResult:
    """Install a historical raw key only after proving it opens existing data."""

    path = Path(key_file)
    candidate = bytes(candidate_key)
    if len(candidate) != MANAGED_KEY_BYTES:
        raise ManagedKeyUnavailableError(
            "The recovery key must contain exactly 32 bytes.",
            code="secret_storage_key_mismatch",
        )
    with managed_key_lock(path):
        if path.exists() or path.is_symlink():
            raise ManagedKeyUnavailableError(
                "A managed key already exists; it was not overwritten.",
                code="secret_storage_key_mismatch",
            )
        settings = _read_settings_for_key_guard(
            _settings_file_for(path, settings_file)
        )
        if not candidate_decrypts_all_protected_values(settings, candidate):
            raise ManagedKeyUnavailableError(
                "The recovery key does not open every protected credential.",
                code="secret_storage_key_mismatch",
            )
        _atomic_write_secret_bytes(path, candidate)
    return ManagedKeyResult(key=candidate, created=True)


def inspect_key_recovery_status(
    key_file: Path,
    *,
    settings_file: Path | None = None,
) -> KeyRecoveryStatus:
    """Return authenticated recovery detail without returning key material."""

    path = Path(key_file)
    settings_path = _settings_file_for(path, settings_file)
    try:
        result = ensure_managed_key(path, settings_file=settings_path)
    except ManagedKeyUnavailableError as exc:
        try:
            stored = _read_and_secure_existing(path)
        except ManagedKeyUnavailableError:
            stored = None
        if stored is None:
            mode = "unreadable" if path.exists() or path.is_symlink() else "missing"
        elif _is_secret_envelope(stored):
            mode = "legacy_envelope"
        else:
            mode = "invalid"
        if not path.exists() and not path.is_symlink():
            mode = "missing"
        state = (
            "legacy_recovery_required"
            if mode in {"legacy_envelope", "missing", "invalid"}
            and exc.code != "secret_storage_key_file_unreadable"
            else "storage_unavailable"
        )
        return KeyRecoveryStatus(
            state=state,
            setup_required=True,
            blocker=exc.code,
            key_mode=mode,
        )
    except OSError:
        return KeyRecoveryStatus(
            state="storage_unavailable",
            setup_required=True,
            blocker="secret_storage_key_file_unreadable",
            key_mode="unreadable",
        )

    try:
        settings = _read_settings_for_key_guard(settings_path)
    except ManagedKeyUnavailableError as exc:
        return KeyRecoveryStatus(
            state="storage_unavailable",
            setup_required=True,
            blocker=exc.code,
        )
    from .encryption import validate_protected_credentials

    report = validate_protected_credentials(settings, result.key)
    if report.failures:
        return KeyRecoveryStatus(
            state="protected_credentials_need_attention",
            setup_required=False,
            key_mode="managed_local",
            encrypted_credentials=report.encrypted_count,
            unreadable_credentials=report.failures,
        )
    return KeyRecoveryStatus(
        state="ready",
        setup_required=False,
        key_mode="managed_local",
        encrypted_credentials=report.encrypted_count,
    )


async def wait_for_managed_key_ready(
    shutdown_event: asyncio.Event,
    wake_event: asyncio.Event,
    key_file: Path,
    *,
    settings_file: Path | None = None,
    initial_delay_seconds: float = 1.0,
    maximum_delay_seconds: float = 30.0,
    on_status: Callable[[KeyRecoveryStatus], None] | None = None,
) -> ManagedKeyResult | None:
    """Retry key preparation until ready, explicitly woken, or shut down."""

    delay = max(0.05, initial_delay_seconds)
    maximum_delay = max(delay, maximum_delay_seconds)
    while not shutdown_event.is_set():
        try:
            result = ensure_managed_key(key_file, settings_file=settings_file)
        except ManagedKeyUnavailableError:
            if on_status is not None:
                on_status(
                    inspect_key_recovery_status(
                        key_file,
                        settings_file=settings_file,
                    )
                )
        else:
            return result

        wake_wait = asyncio.create_task(wake_event.wait())
        shutdown_wait = asyncio.create_task(shutdown_event.wait())
        timer = asyncio.create_task(asyncio.sleep(delay))
        done, pending = await asyncio.wait(
            {wake_wait, shutdown_wait, timer},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if shutdown_wait in done and shutdown_event.is_set():
            return None
        if wake_wait in done:
            wake_event.clear()
            delay = max(0.05, initial_delay_seconds)
        else:
            delay = min(maximum_delay, delay * 2)
    return None
