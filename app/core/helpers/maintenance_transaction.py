"""Crash-recoverable transactions for coupled `/config` maintenance writes."""

from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .atomic_io import _atomic_write_secret_bytes, fsync_directory
from .key_manager import managed_key_lock

TRANSACTION_DIRNAME = ".channelwatch-transactions"
JOURNAL_FILENAME = "journal.json"


def _validate_filename(filename: str) -> str:
    candidate = PurePosixPath(filename)
    if (
        not filename
        or candidate.is_absolute()
        or ".." in candidate.parts
        or len(candidate.parts) != 1
    ):
        raise ValueError(f"Unsafe maintenance transaction filename: {filename!r}")
    return filename


@contextmanager
def configuration_maintenance_lock(
    config_dir: Path, *, timeout: float = 30.0
) -> Iterator[None]:
    """Serialize key, backup, restore, rotation, and recovery maintenance."""

    with managed_key_lock(Path(config_dir) / "encryption.key", timeout=timeout):
        yield


def _transaction_root(config_dir: Path) -> Path:
    return Path(config_dir) / TRANSACTION_DIRNAME


def _validate_directory(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PermissionError(f"Unsafe maintenance transaction directory: {path}")


def _read_regular_file(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_nlink", 1) != 1
    ):
        raise PermissionError(f"Unsafe maintenance transaction file: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise PermissionError(f"Maintenance transaction file changed: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_journal(transaction_dir: Path, payload: dict[str, object]) -> None:
    _validate_directory(transaction_dir)
    _atomic_write_secret_bytes(
        transaction_dir / JOURNAL_FILENAME,
        json.dumps(payload, indent=2).encode("utf-8"),
    )


def _load_journal(transaction_dir: Path) -> dict[str, object]:
    _validate_directory(transaction_dir)
    payload = json.loads(
        _read_regular_file(transaction_dir / JOURNAL_FILENAME).decode("utf-8")
    )
    if not isinstance(payload, dict):
        raise TypeError("Maintenance transaction journal must be an object.")
    return payload


def _install_staged_files(
    config_dir: Path, transaction_dir: Path, filenames: tuple[str, ...]
) -> None:
    new_dir = transaction_dir / "new"
    _validate_directory(transaction_dir)
    _validate_directory(new_dir)
    for filename in filenames:
        _atomic_write_secret_bytes(
            Path(config_dir) / filename,
            _read_regular_file(new_dir / filename),
        )


def _restore_old_files(
    config_dir: Path,
    transaction_dir: Path,
    filenames: tuple[str, ...],
    absent_before: set[str],
) -> None:
    old_dir = transaction_dir / "old"
    _validate_directory(transaction_dir)
    _validate_directory(old_dir)
    for filename in filenames:
        destination = Path(config_dir) / filename
        if filename in absent_before:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
            continue
        _atomic_write_secret_bytes(destination, _read_regular_file(old_dir / filename))


def _cleanup_transaction(transaction_dir: Path) -> None:
    _validate_directory(transaction_dir)
    shutil.rmtree(transaction_dir)
    fsync_directory(transaction_dir.parent)


def recover_maintenance_transactions(config_dir: Path) -> int:
    """Recover any interrupted transaction before settings/key consumers start."""

    root = _transaction_root(config_dir)
    if not root.exists() and not root.is_symlink():
        return 0
    recovered = 0
    with configuration_maintenance_lock(config_dir):
        # Core and UI start independently. The first process may remove an
        # empty/recovered root while the second waits for this lock, so recheck
        # only after ownership instead of trusting a stale pre-lock lookup.
        if not root.exists() and not root.is_symlink():
            return 0
        _validate_directory(root)
        for transaction_dir in sorted(root.iterdir()):
            _validate_directory(transaction_dir)
            journal_path = transaction_dir / JOURNAL_FILENAME
            if not journal_path.exists() and not journal_path.is_symlink():
                _cleanup_transaction(transaction_dir)
                continue
            journal = _load_journal(transaction_dir)
            filenames_raw = journal.get("files")
            if not isinstance(filenames_raw, list) or not all(
                isinstance(item, str) for item in filenames_raw
            ):
                raise ValueError("Maintenance transaction journal has invalid files.")
            filenames = tuple(_validate_filename(item) for item in filenames_raw)
            absent_raw = journal.get("absent_before", [])
            if not isinstance(absent_raw, list) or not all(
                isinstance(item, str) for item in absent_raw
            ):
                raise ValueError(
                    "Maintenance transaction journal has invalid absent files."
                )
            absent_before = {_validate_filename(item) for item in absent_raw}
            state = journal.get("state")
            if state in {"committing", "committed"}:
                _install_staged_files(config_dir, transaction_dir, filenames)
            else:
                _restore_old_files(
                    config_dir,
                    transaction_dir,
                    filenames,
                    absent_before,
                )
            _cleanup_transaction(transaction_dir)
            recovered += 1
        try:
            root.rmdir()
        except OSError:
            pass
    return recovered


def replace_config_files_transactionally(
    config_dir: Path,
    replacements: Mapping[str, bytes],
    *,
    lock_already_held: bool = False,
) -> None:
    """Replace related config files as one journaled, restart-recoverable unit."""

    config_dir = Path(config_dir)
    normalized = {
        _validate_filename(filename): bytes(payload)
        for filename, payload in replacements.items()
    }
    if not normalized:
        return

    @contextmanager
    def _maybe_lock() -> Iterator[None]:
        if lock_already_held:
            yield
        else:
            with configuration_maintenance_lock(config_dir):
                yield

    with _maybe_lock():
        root = _transaction_root(config_dir)
        if root.exists() or root.is_symlink():
            _validate_directory(root)
            # A killed sibling process may have released the interprocess lock
            # while leaving a durable transaction behind.  Never create a
            # second generation beside it: its later startup replay could
            # otherwise overwrite this replacement.  The managed-key lock is
            # re-entrant for this thread, so recovery is safe both for direct
            # callers and callers that already own the maintenance lock.
            if any(root.iterdir()):
                recover_maintenance_transactions(config_dir)
            root = _transaction_root(config_dir)
        if not root.exists() and not root.is_symlink():
            root.mkdir(parents=True, exist_ok=False)
        if os.name != "nt":
            root.chmod(0o700)
        transaction_dir = root / uuid.uuid4().hex
        old_dir = transaction_dir / "old"
        new_dir = transaction_dir / "new"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        if os.name != "nt":
            transaction_dir.chmod(0o700)
            old_dir.chmod(0o700)
            new_dir.chmod(0o700)

        filenames = tuple(sorted(normalized))
        absent_before: set[str] = set()
        for filename in filenames:
            destination = config_dir / filename
            if destination.exists() or destination.is_symlink():
                _atomic_write_secret_bytes(
                    old_dir / filename,
                    _read_regular_file(destination),
                )
            else:
                absent_before.add(filename)
            _atomic_write_secret_bytes(new_dir / filename, normalized[filename])

        journal: dict[str, object] = {
            "version": 1,
            "state": "prepared",
            "files": list(filenames),
            "absent_before": sorted(absent_before),
        }
        _write_journal(transaction_dir, journal)
        journal["state"] = "committing"
        _write_journal(transaction_dir, journal)
        try:
            _install_staged_files(config_dir, transaction_dir, filenames)
        except Exception:
            journal["state"] = "rolling_back"
            _write_journal(transaction_dir, journal)
            _restore_old_files(
                config_dir,
                transaction_dir,
                filenames,
                absent_before,
            )
            journal["state"] = "rolled_back"
            _write_journal(transaction_dir, journal)
            _cleanup_transaction(transaction_dir)
            raise
        journal["state"] = "committed"
        _write_journal(transaction_dir, journal)
        _cleanup_transaction(transaction_dir)
        try:
            root.rmdir()
        except OSError:
            pass
