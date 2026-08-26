"""Shared durable activity storage for the monitoring core and UI backend.

SQLite is authoritative.  ``activity_history.json`` is retained as a bounded
recovery journal for historical installs, transient SQLite failures, and old
runtime rollback compatibility.  Readers merge both stores by activity id so a
valid journal entry is never hidden merely because the database exists.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlmodel import select

from core.helpers.atomic_io import atomic_write_json, fsync_directory

from .database import create_all_tables, create_db_engine, get_session
from .activity_schema import activity_payload_to_model
from .models import ActivityEvent

try:  # pragma: no cover - Windows is supported for source development only.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

log = logging.getLogger(__name__)

MAX_RECOVERY_EVENTS = 500
LOCK_TIMEOUT_SECONDS = 5.0
SQLITE_BUSY_TIMEOUT_SECONDS = 5.0
_thread_lock = threading.RLock()


@dataclass(frozen=True)
class ActivityStorageStatus:
    status: str = "healthy"
    pending_recovery_events: int = 0
    last_reconciled_at: str | None = None


_status = ActivityStorageStatus()


def _set_status(
    status: str,
    *,
    pending: int = 0,
    reconciled: bool = False,
) -> None:
    global _status
    _status = ActivityStorageStatus(
        status=status,
        pending_recovery_events=max(0, int(pending)),
        last_reconciled_at=(
            datetime.now(timezone.utc).isoformat()
            if reconciled
            else _status.last_reconciled_at
        ),
    )


def activity_storage_status() -> dict[str, Any]:
    """Return non-sensitive authenticated diagnostic state."""

    return asdict(_status)


def _config_dir(config_dir: str | Path | None = None) -> Path:
    if config_dir is not None:
        return Path(config_dir)
    return Path(os.getenv("CONFIG_PATH", "/config"))


def _paths(config_dir: str | Path | None = None) -> tuple[Path, Path, Path]:
    root = _config_dir(config_dir)
    return (
        root / "channelwatch.db",
        root / "activity_history.json",
        root / "channelwatch-runtime" / "activity-storage.lock",
    )


def _database_url(database_path: Path) -> str:
    return f"sqlite:///{database_path}"


def _open_engine(database_path: Path):
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(
        _database_url(database_path),
        poolclass=NullPool,
        connect_args={
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
        },
    )
    create_all_tables(engine)
    return engine


@contextmanager
def activity_storage_lock(
    config_dir: str | Path | None = None,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Serialize journal/database transitions across the core and UI."""

    _database_path, _journal_path, lock_path = _paths(config_dir)
    with _thread_lock:
        if fcntl is None:
            yield
            return

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(lock_path), flags, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RuntimeError("Activity storage lock is not a single-link regular file.")
            if os.name != "nt":
                os.fchmod(fd, 0o600)

            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Timed out waiting for the activity storage lock.")
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _read_journal(journal_path: Path) -> list[dict[str, Any]]:
    if not journal_path.exists():
        return []
    with journal_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Activity recovery journal must contain a JSON array.")
    if any(not isinstance(row, dict) for row in payload):
        raise ValueError("Every activity recovery entry must be a JSON object.")
    return payload


def load_recovery_events(
    config_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load valid recovery events without exposing malformed rows."""

    _database_path, journal_path, _lock_path = _paths(config_dir)
    try:
        rows = _read_journal(journal_path)
    except (OSError, ValueError, json.JSONDecodeError):
        _set_status("recovery_required")
        return []
    valid: list[dict[str, Any]] = []
    for row in rows:
        if activity_payload_to_model(row) is not None:
            valid.append(row)
    return valid


def _write_journal(journal_path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_json(journal_path, rows[:MAX_RECOVERY_EVENTS], indent=2)
    if os.name != "nt":
        os.chmod(journal_path, 0o600)


def _quarantine_malformed_journal(journal_path: Path) -> Path:
    """Preserve an unreadable journal before replacing the active path."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = journal_path.with_name(f"{journal_path.name}.corrupt-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = journal_path.with_name(
            f"{journal_path.name}.corrupt-{stamp}-{counter}"
        )
        counter += 1
    os.replace(journal_path, candidate)
    if os.name != "nt":
        os.chmod(candidate, 0o600)
    _write_journal(journal_path, [])
    fsync_directory(journal_path.parent)
    return candidate


def _append_recovery_event(journal_path: Path, event: dict[str, Any]) -> bool:
    try:
        rows = _read_journal(journal_path)
    except FileNotFoundError:
        rows = []
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    event_id = str(event.get("id") or "")
    rows = [row for row in rows if str(row.get("id") or "") != event_id]
    rows.insert(0, event)
    _write_journal(journal_path, rows)
    return True


def persist_activity_event(
    event: dict[str, Any],
    *,
    config_dir: str | Path | None = None,
) -> bool:
    """Persist one normalized event, falling back to the recovery journal."""

    model = activity_payload_to_model(event)
    if model is None:
        _set_status("degraded")
        return False
    database_path, journal_path, _lock_path = _paths(config_dir)
    if os.getenv("CHANNELWATCH_CONFIG_READ_ONLY") == "1":
        _set_status("degraded")
        return False

    try:
        with activity_storage_lock(config_dir):
            engine = None
            try:
                engine = _open_engine(database_path)
                with get_session(engine) as session:
                    if session.get(ActivityEvent, model.id) is None:
                        session.add(model)
                        session.commit()
                pending = 0
                if journal_path.exists():
                    try:
                        pending = len(_read_journal(journal_path))
                    except (OSError, ValueError, json.JSONDecodeError):
                        _quarantine_malformed_journal(journal_path)
                        _set_status("recovery_required")
                        return True
                _set_status("degraded" if pending else "healthy", pending=pending)
                return True
            except (OSError, SQLAlchemyError, RuntimeError, ValueError) as exc:
                log.warning(
                    "SQLite activity persistence failed; using the recovery journal (%s).",
                    exc.__class__.__name__,
                )
            finally:
                if engine is not None:
                    engine.dispose()

            saved = _append_recovery_event(journal_path, event)
            pending = len(_read_journal(journal_path)) if saved else 0
            _set_status("degraded" if saved else "recovery_required", pending=pending)
            return saved
    except (OSError, TimeoutError, RuntimeError) as exc:
        log.warning("Activity persistence is unavailable (%s).", exc.__class__.__name__)
        _set_status("recovery_required")
        return False


def _journal_generation(path: Path) -> tuple[int, int, str] | None:
    try:
        metadata = path.stat()
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    return metadata.st_ino, metadata.st_mtime_ns, hashlib.sha256(payload).hexdigest()


def _archive_reconciled_journal(journal_path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = journal_path.with_name(f"{journal_path.name}.migrated-v0919-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = journal_path.with_name(
            f"{journal_path.name}.migrated-v0919-{stamp}-{counter}"
        )
        counter += 1
    os.replace(journal_path, candidate)
    if os.name != "nt":
        os.chmod(candidate, 0o600)
    _write_journal(journal_path, [])
    fsync_directory(journal_path.parent)


def reconcile_activity_history(
    config_dir: str | Path | None = None,
) -> dict[str, int]:
    """Idempotently import the active JSON recovery journal into SQLite."""

    result = {"total": 0, "inserted": 0, "skipped": 0, "errors": 0}
    if os.getenv("CHANNELWATCH_CONFIG_READ_ONLY") == "1":
        pending = len(load_recovery_events(config_dir))
        _set_status("degraded" if pending else "healthy", pending=pending)
        return result

    database_path, journal_path, _lock_path = _paths(config_dir)
    try:
        with activity_storage_lock(config_dir):
            if not journal_path.exists():
                _set_status("healthy", reconciled=True)
                return result
            before = _journal_generation(journal_path)
            try:
                rows = _read_journal(journal_path)
            except (OSError, ValueError, json.JSONDecodeError):
                result["errors"] = 1
                try:
                    _quarantine_malformed_journal(journal_path)
                except OSError:
                    pass
                _set_status("recovery_required")
                return result
            result["total"] = len(rows)
            if not rows:
                _set_status("healthy", reconciled=True)
                return result

            models: list[ActivityEvent] = []
            for row in rows:
                model = activity_payload_to_model(row)
                if model is None:
                    result["errors"] += 1
                else:
                    models.append(model)

            engine = _open_engine(database_path)
            try:
                with get_session(engine) as session:
                    for model in models:
                        if session.get(ActivityEvent, model.id) is not None:
                            result["skipped"] += 1
                            continue
                        session.add(model)
                        result["inserted"] += 1
                    session.commit()
            finally:
                engine.dispose()

            after = _journal_generation(journal_path)
            if result["errors"] == 0 and before == after and after is not None:
                _archive_reconciled_journal(journal_path)
                _set_status("healthy", reconciled=True)
            else:
                _set_status("degraded", pending=len(models), reconciled=True)
            return result
    except (OSError, SQLAlchemyError, TimeoutError, RuntimeError) as exc:
        log.warning("Activity reconciliation failed (%s).", exc.__class__.__name__)
        _set_status("recovery_required")
        result["errors"] += 1
        return result


def clear_activity_storage(config_dir: str | Path | None = None) -> None:
    database_path, journal_path, _lock_path = _paths(config_dir)
    with activity_storage_lock(config_dir):
        if database_path.exists():
            engine = _open_engine(database_path)
            try:
                with get_session(engine) as session:
                    for row in session.exec(select(ActivityEvent)).all():
                        session.delete(row)
                    session.commit()
            finally:
                engine.dispose()
        _write_journal(journal_path, [])
        _set_status("healthy", reconciled=True)


def delete_dvr_activity(
    dvr_id: str,
    *,
    config_dir: str | Path | None = None,
) -> int:
    database_path, journal_path, _lock_path = _paths(config_dir)
    removed = 0
    with activity_storage_lock(config_dir):
        if database_path.exists():
            engine = _open_engine(database_path)
            try:
                with get_session(engine) as session:
                    rows = session.exec(
                        select(ActivityEvent).where(ActivityEvent.dvr_id == dvr_id)
                    ).all()
                    removed += len(rows)
                    for row in rows:
                        session.delete(row)
                    session.commit()
            finally:
                engine.dispose()
        try:
            journal_rows = _read_journal(journal_path)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            journal_rows = []
        kept = [row for row in journal_rows if row.get("dvr_id") != dvr_id]
        removed += len(journal_rows) - len(kept)
        _write_journal(journal_path, kept)
    return removed


def merge_recovery_journal_into_database(
    database_path: str | Path,
    journal_path: str | Path,
) -> dict[str, int]:
    """Merge a journal into a standalone SQLite snapshot without altering it."""

    result = {"total": 0, "inserted": 0, "skipped": 0, "errors": 0}
    source = Path(journal_path)
    if not source.exists():
        return result
    rows = _read_journal(source)
    result["total"] = len(rows)
    models: list[ActivityEvent] = []
    for row in rows:
        model = activity_payload_to_model(row)
        if model is None:
            result["errors"] += 1
        else:
            models.append(model)
    if result["errors"]:
        raise ValueError("Activity recovery journal contains invalid events.")

    engine = _open_engine(Path(database_path))
    try:
        with get_session(engine) as session:
            for model in models:
                if session.get(ActivityEvent, model.id) is not None:
                    result["skipped"] += 1
                    continue
                session.add(model)
                result["inserted"] += 1
            session.commit()
    finally:
        engine.dispose()
    return result
