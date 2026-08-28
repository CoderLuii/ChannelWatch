"""Image-stable process launcher for active app bundles."""

from __future__ import annotations

import argparse
import http.client
import importlib
import json
import os
import runpy
import select
import signal
import socket
import stat as stat_module
import subprocess
import sys
import threading
import time
import traceback
import uuid

# The parser is reachable only over a mode-0600, UID-owned local Unix socket.
import xmlrpc.client  # nosec B411
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.image_metadata import resolve_image_metadata

try:
    import fcntl
except ImportError:  # pragma: no cover - container runtime is POSIX
    fcntl = None


CONFIG_DIR = Path(os.environ.get("CONFIG_PATH", "/config"))
RUNTIME_DIR = CONFIG_DIR / "channelwatch-runtime"
IMAGE_APP_DIR = Path(os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app")).resolve()
IMAGE_STATIC_UI_DIR = IMAGE_APP_DIR / "ui" / "backend" / "static_ui"
ACTIVATION_ID_ENV = "CHANNELWATCH_ACTIVATION_ID"
ACTIVATION_VERSION_ENV = "CHANNELWATCH_ACTIVATION_VERSION"
LAUNCHER_PROTOCOL_ENV = "CHANNELWATCH_LAUNCHER_PROTOCOL"
ACTIVATION_PENDING_FILE = "activation-pending.json"
ACTIVATION_WATCHDOG_INTERVAL_SECONDS = 1.0
ACTIVATION_TIMEOUT_SECONDS = 120
RESTART_REQUIRED_FILE = "restart-required.json"
RESTART_JOURNAL_LOCK_FILE = "restart-required.lock"
PROTOCOL_THREE_HANDOFF_FILE = "restart-services-accepted.json"
PROTOCOL_THREE_HANDOFF_SCHEMA = 1
PROTOCOL_THREE_HANDOFF_FIELDS = {"schema", "journal", "old_processes"}
PROTOCOL_THREE_PROCESS_NAMES = ("core", "ui")
PROTOCOL_THREE_PROCESS_IDENTITY_FIELDS = {"pid", "start"}
PROTOCOL_THREE_RESTART_HELPER_LOCK_FILE = "restart-services.lock"
ACTIVATION_OUTCOME_LOCK_FILE = "activation-outcome.lock"
RESTART_JOURNAL_SCHEMA = 2
RESTART_CONTROL_FILES = (
    "active.json",
    "rollback.json",
    "activation-pending.json",
    "activation-core-ready.json",
    "activation-ui-ready.json",
    "update-job.json",
)
RESTART_JOURNAL_OPERATIONS = {"apply", "manual_rollback", "activation_rollback"}
RESTART_JOURNAL_PHASES = {"commit", "abort"}
RESTART_JOURNAL_FIELDS = {
    "schema",
    "reason",
    "operation",
    "phase",
    "job_id",
    "source_active",
    "replace_activation_state",
    "created_at",
    "control",
}
RESTART_REQUIRED_WATCHDOG_INTERVAL_SECONDS = 1.0
RESTART_REQUIRED_PRELAUNCH_DELAY_SECONDS = 2.0
LAUNCHER_PROTOCOL_RECOVERY_CAPABLE = 3
RECOVERY_MODE_FILE = "official-recovery-mode.json"
SUPERVISOR_SOCKET_FILE = os.environ.get(
    "CHANNELWATCH_SUPERVISOR_SOCKET_FILE",
    os.path.join(
        os.environ.get("CHANNELWATCH_RUNTIME_DIR", "/tmp/channelwatch"),
        "supervisor.sock",
    ),
)
PROTOCOL_THREE_RESTART_ACK_TIMEOUT_SECONDS = 5.0
PROTOCOL_THREE_RESTART_GRACE_SECONDS = 0.25
PROTOCOL_THREE_STOP_BARRIER_TIMEOUT_SECONDS = 10.0
PROTOCOL_THREE_STOP_BARRIER_INTERVAL_SECONDS = 0.05
RUNTIME_CONTROL_MAX_BYTES = 256 * 1024
RECOVERY_MODE_ENV = "CHANNELWATCH_OFFICIAL_RECOVERY_MODE"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[RuntimeLauncher] {message}", file=sys.stderr, flush=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temp_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        fsync_directory(path.parent)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_runtime_json_strict(path: Path, *, label: str) -> Any:
    """Read one bounded, unchanged, single-link runtime control file."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError(f"Safe {label} reads are unavailable.")
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError(f"The {label} cannot be inspected safely.") from exc
    if (
        not stat_module.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or before.st_size < 0
        or before.st_size > RUNTIME_CONTROL_MAX_BYTES
    ):
        raise RuntimeError(f"The {label} is not a trusted bounded regular file.")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"The {label} cannot be opened safely.") from exc
    try:
        opened = os.fstat(fd)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not stat_module.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or opened.st_size > RUNTIME_CONTROL_MAX_BYTES
        ):
            raise RuntimeError(f"The {label} changed before it was opened.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65_536, RUNTIME_CONTROL_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > RUNTIME_CONTROL_MAX_BYTES:
                raise RuntimeError(f"The {label} exceeds the safe size limit.")
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        after_name = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"The {label} changed while it was being read.") from exc
    identity_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_nlink",
        "st_uid",
    )
    if any(
        getattr(before, field) != getattr(opened, field)
        or getattr(opened, field) != getattr(after_fd, field)
        or getattr(after_fd, field) != getattr(after_name, field)
        for field in identity_fields
    ):
        raise RuntimeError(f"The {label} changed while it was being read.")
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"The {label} does not contain valid JSON.") from exc


def restart_required_path() -> Path:
    return RUNTIME_DIR / RESTART_REQUIRED_FILE


def protocol_three_handoff_path() -> Path:
    return RUNTIME_DIR / PROTOCOL_THREE_HANDOFF_FILE


def protocol_three_handoff_present() -> bool:
    try:
        os.lstat(protocol_three_handoff_path())
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


@contextmanager
def _protocol_three_restart_helper_lock(socket_path: Path):
    """Serialize image-owned helpers without sharing the journal lock."""

    if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("Safe restart helper locking is unavailable.")
    try:
        parent_metadata = os.lstat(socket_path.parent)
    except OSError as exc:
        raise RuntimeError("The Supervisor runtime directory is unavailable.") from exc
    if (
        not stat_module.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.geteuid()
        or stat_module.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise RuntimeError("The Supervisor runtime directory is not trusted.")
    lock_path = socket_path.parent / PROTOCOL_THREE_RESTART_HELPER_LOCK_FILE
    try:
        fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except OSError as exc:
        raise RuntimeError("The restart helper lock cannot be opened safely.") from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat_module.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
        ):
            raise RuntimeError(
                "The restart helper lock is not a trusted single-link regular file."
            )
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "A coordinated restart helper is already active."
            ) from exc
        try:
            named_metadata = os.stat(lock_path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError("The restart helper lock changed ownership.") from exc
        if (
            named_metadata.st_dev != metadata.st_dev
            or named_metadata.st_ino != metadata.st_ino
            or not stat_module.S_ISREG(named_metadata.st_mode)
            or named_metadata.st_nlink != 1
            or named_metadata.st_uid != os.geteuid()
        ):
            raise RuntimeError("The restart helper lock changed ownership.")
        yield
        try:
            final_named_metadata = os.stat(lock_path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError("The restart helper lock changed ownership.") from exc
        if (
            final_named_metadata.st_dev != metadata.st_dev
            or final_named_metadata.st_ino != metadata.st_ino
            or not stat_module.S_ISREG(final_named_metadata.st_mode)
            or final_named_metadata.st_nlink != 1
            or final_named_metadata.st_uid != os.geteuid()
        ):
            raise RuntimeError("The restart helper lock changed ownership.")
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def _restart_journal_lock():
    """Serialize journal ownership and replay across image/bundle code."""

    if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("Safe restart journal locking is unavailable.")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_DIR / RESTART_JOURNAL_LOCK_FILE
    try:
        fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        raise RuntimeError("The restart journal lock cannot be opened safely.") from exc
    try:
        metadata = os.fstat(fd)
        if not stat_module.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(
                "The restart journal lock is not a single-link regular file."
            )
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def _activation_outcome_lock():
    """Serialize activation quorum and deadline outcomes across processes."""

    if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("Safe activation outcome locking is unavailable.")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_DIR / ACTIVATION_OUTCOME_LOCK_FILE
    try:
        fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        raise RuntimeError(
            "The activation outcome lock cannot be opened safely."
        ) from exc
    try:
        metadata = os.fstat(fd)
        if not stat_module.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError(
                "The activation outcome lock is not a single-link regular file."
            )
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def restart_journal_present() -> bool:
    """Fail closed for every filesystem object at the journal path."""

    try:
        os.lstat(restart_required_path())
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _control_path(name: str) -> Path:
    if name not in RESTART_CONTROL_FILES:
        raise RuntimeError(f"Unsupported restart control file: {name}.")
    return RUNTIME_DIR / name


def _read_control_state() -> dict[str, dict[str, Any] | None]:
    control: dict[str, dict[str, Any] | None] = {}
    for name in RESTART_CONTROL_FILES:
        path = _control_path(name)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = None
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Restart control file {name} could not be read safely."
            ) from exc
        if payload is not None and not isinstance(payload, dict):
            raise RuntimeError(
                f"Restart control file {name} must contain a JSON object."
            )
        control[name] = payload
    return control


def _validate_restart_journal(journal: Any) -> dict[str, Any]:
    if not isinstance(journal, dict) or journal.get("schema") != RESTART_JOURNAL_SCHEMA:
        raise RuntimeError("Restart journal schema is invalid.")
    if set(journal) != RESTART_JOURNAL_FIELDS:
        raise RuntimeError("Restart journal fields are invalid.")
    if journal.get("operation") not in RESTART_JOURNAL_OPERATIONS:
        raise RuntimeError("Restart journal operation is invalid.")
    if journal.get("phase") not in RESTART_JOURNAL_PHASES:
        raise RuntimeError("Restart journal phase is invalid.")
    if journal.get("replace_activation_state") is not True:
        raise RuntimeError("Restart journal replacement policy is invalid.")
    expected_reason = (
        "activation_rollback"
        if journal.get("operation") == "activation_rollback"
        else "runtime_transition"
    )
    if journal.get("reason") != expected_reason:
        raise RuntimeError("Restart journal reason is invalid.")
    if journal.get("job_id") is not None and not isinstance(journal.get("job_id"), str):
        raise RuntimeError("Restart journal job identity is invalid.")
    if not isinstance(journal.get("created_at"), str) or not journal.get("created_at"):
        raise RuntimeError("Restart journal timestamp is invalid.")
    source_active = journal.get("source_active")
    if source_active is not None and not isinstance(source_active, dict):
        raise RuntimeError("Restart journal source active state is invalid.")
    control = journal.get("control")
    if not isinstance(control, dict) or set(control) != set(RESTART_CONTROL_FILES):
        raise RuntimeError("Restart journal control mapping is invalid.")
    if any(
        value is not None and not isinstance(value, dict) for value in control.values()
    ):
        raise RuntimeError("Restart journal control values are invalid.")
    return journal


def _build_restart_journal(
    *,
    reason: str,
    operation: str,
    phase: str,
    job_id: str | None,
    source_active: dict[str, Any] | None,
    control: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    return _validate_restart_journal(
        {
            "schema": RESTART_JOURNAL_SCHEMA,
            "reason": reason,
            "operation": operation,
            "phase": phase,
            "job_id": job_id,
            "source_active": source_active,
            "replace_activation_state": True,
            "created_at": utc_now(),
            "control": control,
        }
    )


def _load_restart_journal_strict() -> dict[str, Any]:
    path = restart_required_path()
    try:
        payload = _read_runtime_json_strict(path, label="restart journal")
    except FileNotFoundError as exc:
        raise RuntimeError("The expected restart journal no longer exists.") from exc
    return _validate_restart_journal(payload)


def _require_restart_journal_owner(expected: dict[str, Any]) -> dict[str, Any]:
    validated_expected = _validate_restart_journal(expected)
    current = _load_restart_journal_strict()
    if current != validated_expected:
        raise RuntimeError("The restart journal is owned by another generation.")
    return current


def _cleanup_restart_journal_candidates() -> None:
    """Remove only regular internal candidates abandoned by a dead writer."""

    removed = False
    for candidate in RUNTIME_DIR.glob(f".{RESTART_REQUIRED_FILE}.candidate-*"):
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if not stat_module.S_ISREG(metadata.st_mode):
            continue
        try:
            candidate.unlink()
            removed = True
        except FileNotFoundError:
            pass
    if removed:
        fsync_directory(RUNTIME_DIR)


def _cleanup_protocol_three_handoff_candidates() -> None:
    """Remove only regular image-owned handoff candidates."""

    removed = False
    for candidate in RUNTIME_DIR.glob(f".{PROTOCOL_THREE_HANDOFF_FILE}.candidate-*"):
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if not stat_module.S_ISREG(metadata.st_mode):
            continue
        try:
            candidate.unlink()
            removed = True
        except FileNotFoundError:
            pass
    if removed:
        fsync_directory(RUNTIME_DIR)


def _validate_protocol_three_old_processes(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict) or set(value) != set(PROTOCOL_THREE_PROCESS_NAMES):
        raise RuntimeError("The protocol-3 process identities are invalid.")
    validated: dict[str, dict[str, int]] = {}
    for process_name in PROTOCOL_THREE_PROCESS_NAMES:
        identity = value.get(process_name)
        if (
            not isinstance(identity, dict)
            or set(identity) != PROTOCOL_THREE_PROCESS_IDENTITY_FIELDS
        ):
            raise RuntimeError("A protocol-3 process identity is invalid.")
        pid = identity.get("pid")
        started_at = identity.get("start")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or isinstance(started_at, bool)
            or not isinstance(started_at, int)
            or started_at < 0
        ):
            raise RuntimeError("A protocol-3 process identity is invalid.")
        validated[process_name] = {"pid": pid, "start": started_at}
    return validated


def _load_protocol_three_handoff_strict() -> dict[str, Any]:
    path = protocol_three_handoff_path()
    try:
        payload = _read_runtime_json_strict(
            path,
            label="protocol-3 handoff marker",
        )
    except FileNotFoundError as exc:
        raise RuntimeError("The protocol-3 handoff marker does not exist.") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != PROTOCOL_THREE_HANDOFF_FIELDS
        or payload.get("schema") != PROTOCOL_THREE_HANDOFF_SCHEMA
    ):
        raise RuntimeError("The protocol-3 handoff marker is invalid.")
    _validate_restart_journal(payload.get("journal"))
    _validate_protocol_three_old_processes(payload.get("old_processes"))
    return payload


def _publish_protocol_three_handoff_locked(
    journal: dict[str, Any],
    old_processes: dict[str, dict[str, int]],
) -> None:
    validated = _validate_restart_journal(journal)
    marker = {
        "schema": PROTOCOL_THREE_HANDOFF_SCHEMA,
        "journal": validated,
        "old_processes": _validate_protocol_three_old_processes(old_processes),
    }
    path = protocol_three_handoff_path()
    try:
        existing = _load_protocol_three_handoff_strict()
    except RuntimeError:
        try:
            os.lstat(path)
        except FileNotFoundError:
            existing = None
        else:
            raise
    if existing is not None:
        if existing != marker:
            raise RuntimeError(
                "The protocol-3 handoff marker belongs to another journal."
            )
        return

    staged_path = path.with_name(f".{path.name}.candidate-{uuid.uuid4().hex}")
    atomic_write_json(staged_path, marker)
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("Safe protocol-3 marker publication is unavailable.")
        staged_fd = os.open(staged_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            staged_metadata = os.fstat(staged_fd)
            if (
                not stat_module.S_ISREG(staged_metadata.st_mode)
                or staged_metadata.st_nlink != 1
                or staged_metadata.st_uid != os.geteuid()
            ):
                raise RuntimeError(
                    "The staged protocol-3 handoff marker is not trusted."
                )
            os.fchmod(staged_fd, 0o600)
            os.fsync(staged_fd)
        finally:
            os.close(staged_fd)
        try:
            os.link(staged_path, path)
        except FileExistsError as exc:
            raise RuntimeError(
                "Another protocol-3 handoff marker won publication."
            ) from exc
        fsync_directory(RUNTIME_DIR)
    finally:
        try:
            staged_path.unlink()
        except FileNotFoundError:
            pass
        else:
            fsync_directory(RUNTIME_DIR)


def _require_protocol_three_handoff_owner(
    expected_journal: dict[str, Any],
) -> dict[str, Any]:
    marker = _load_protocol_three_handoff_strict()
    if marker["journal"] != _validate_restart_journal(expected_journal):
        raise RuntimeError("The protocol-3 handoff marker changed ownership.")
    return marker


def accept_protocol_three_restart_handoff_if_present(
    *,
    old_processes: dict[str, dict[str, int]] | None = None,
    expected_journal: dict[str, Any] | None = None,
) -> bool:
    """Bind an unchanged journal to a completed Supervisor stop barrier."""

    if not restart_journal_present() and not protocol_three_handoff_present():
        return False
    with _restart_journal_lock():
        _cleanup_restart_journal_candidates()
        _cleanup_protocol_three_handoff_candidates()
        try:
            journal = _load_restart_journal_strict()
        except RuntimeError:
            try:
                os.lstat(restart_required_path())
            except FileNotFoundError:
                marker = _load_protocol_three_handoff_strict()
                _require_protocol_three_handoff_owner(marker["journal"])
                if _read_control_state() != marker["journal"]["control"]:
                    raise RuntimeError(
                        "A stale protocol-3 marker does not match live control state."
                    )
                control_names = set(RESTART_CONTROL_FILES)
                if any(
                    path.name not in control_names
                    for path in RUNTIME_DIR.glob("activation-*.json")
                ):
                    raise RuntimeError(
                        "A stale protocol-3 marker conflicts with activation state."
                    )
                # A complete entrypoint replay can lose power after clearing the
                # journal but before clearing an older helper marker. Remove it
                # only when the committed controls prove that exact replay.
                protocol_three_handoff_path().unlink()
                fsync_directory(RUNTIME_DIR)
                return False
            raise
        if expected_journal is not None and journal != _validate_restart_journal(
            expected_journal
        ):
            raise RuntimeError(
                "The protocol-3 restart journal changed during the stop barrier."
            )
        if old_processes is None:
            raise RuntimeError(
                "Protocol-3 handoff requires the stopped process identities."
            )
        _publish_protocol_three_handoff_locked(journal, old_processes)
        return True


def validate_protocol_three_restart_handoff_if_present() -> dict[str, Any] | None:
    """Validate current handoff state without authorizing child consumption."""

    if not restart_journal_present() and not protocol_three_handoff_present():
        return None
    with _restart_journal_lock():
        _cleanup_restart_journal_candidates()
        _cleanup_protocol_three_handoff_candidates()
        if not restart_journal_present():
            _load_protocol_three_handoff_strict()
            return None
        journal = _load_restart_journal_strict()
        if protocol_three_handoff_present():
            _require_protocol_three_handoff_owner(journal)
        return journal


def _write_restart_journal(journal: dict[str, Any]) -> None:
    """Atomically publish one fully written journal without clobbering."""

    validated = _validate_restart_journal(journal)
    path = restart_required_path()
    with _restart_journal_lock():
        _cleanup_restart_journal_candidates()
        staged_path = path.with_name(f".{path.name}.candidate-{uuid.uuid4().hex}")
        atomic_write_json(staged_path, validated)
        try:
            _restart_transition_checkpoint("journal:before-create")
            try:
                os.link(staged_path, path)
            except FileExistsError as exc:
                raise RuntimeError("Another restart journal won publication.") from exc
            fsync_directory(RUNTIME_DIR)
        finally:
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass
            else:
                fsync_directory(RUNTIME_DIR)
    _restart_transition_checkpoint("journal")


def _restart_transition_checkpoint(phase: str) -> None:
    """No-op fault-injection boundary for restart transaction tests."""

    del phase


def _apply_restart_journal_locked(validated: dict[str, Any]) -> None:
    """Publish one validated journal while its ownership lock is held."""

    _require_restart_journal_owner(validated)
    control = validated["control"]

    for path in RUNTIME_DIR.glob("activation-*.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _restart_transition_checkpoint("activation-state-removed")

    ordered_names = tuple(
        name for name in RESTART_CONTROL_FILES if name != "active.json"
    ) + ("active.json",)
    for name in ordered_names:
        _require_restart_journal_owner(validated)
        path = _control_path(name)
        payload = control[name]
        if payload is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            atomic_write_json(path, payload)
        _restart_transition_checkpoint(f"control:{name}")
    fsync_directory(RUNTIME_DIR)
    _restart_transition_checkpoint("control:fsynced")


def apply_restart_journal(journal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Idempotently publish the exact state recorded by a schema-2 journal."""

    with _restart_journal_lock():
        if journal is None:
            journal = _load_restart_journal_strict()
        validated = _validate_restart_journal(journal)
        _apply_restart_journal_locked(validated)
        return validated


def consume_protocol_three_restart_journal_before_launch() -> bool:
    """Replay and acknowledge one valid service-restart journal atomically.

    Protocol 3 restarts the Supervisor children without replacing the container.
    The first new image-owned child therefore becomes the durable entrypoint for
    the already-accepted transition. It must replay and remove the exact journal
    under one ownership lock before importing the selected application. A second
    child either waits for that transaction or observes the acknowledged absence.
    Invalid or foreign journals remain present and fail closed.
    """

    if image_launcher_protocol() < LAUNCHER_PROTOCOL_RECOVERY_CAPABLE:
        return False
    with _restart_journal_lock():
        _cleanup_restart_journal_candidates()
        _cleanup_protocol_three_handoff_candidates()
        try:
            os.lstat(restart_required_path())
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RuntimeError(
                "The restart journal cannot be inspected before service launch."
            ) from exc
        validated = _load_restart_journal_strict()
        _require_protocol_three_handoff_owner(validated)
        _apply_restart_journal_locked(validated)
        _restart_transition_checkpoint("handoff:before-clear")
        _require_protocol_three_handoff_owner(validated)
        try:
            protocol_three_handoff_path().unlink()
        except FileNotFoundError as exc:
            raise RuntimeError(
                "The protocol-3 handoff marker changed before acknowledgement."
            ) from exc
        fsync_directory(RUNTIME_DIR)
        _restart_transition_checkpoint("handoff:cleared")
        _restart_transition_checkpoint("journal:before-clear")
        _require_restart_journal_owner(validated)
        try:
            restart_required_path().unlink()
        except FileNotFoundError as exc:
            raise RuntimeError(
                "The restart journal changed before service acknowledgement."
            ) from exc
        fsync_directory(RUNTIME_DIR)
    _restart_transition_checkpoint("journal:cleared")
    return True


def _parse_image_version(value: str) -> tuple[int, int, int]:
    parts = value.strip().lstrip("v").split(".")
    if len(parts) != 3 or any(not item.isdigit() for item in parts):
        return (0, 0, 0)
    return tuple(int(item) for item in parts)  # type: ignore[return-value]


def image_launcher_protocol(image_version: str | None = None) -> int:
    resolved_version = image_version
    if resolved_version is None:
        resolved_version = resolve_image_metadata(image_app_dir=IMAGE_APP_DIR).version
    version = _parse_image_version(resolved_version)
    if version <= (0, 9, 9):
        return 0
    if version <= (0, 9, 15):
        return 1
    if version <= (0, 9, 17):
        return 2
    return LAUNCHER_PROTOCOL_RECOVERY_CAPABLE


def install_historical_launcher_bridge() -> bool:
    """Upgrade immutable historical launchers before application imports run.

    v0.9.10-v0.9.15 launchers fall back only the child whose import failed,
    which can strand a new core beside an old UI (or the reverse). v0.9.16-
    v0.9.17 restart coherently but predate exact failed-bundle quarantine.  The
    image-owned launcher is running as ``__main__`` when it imports a bundle,
    so replace only its activation rollback callback with this signed bundle's
    hardened implementation.  Package initialization invokes this hook before
    either ``core.main`` or ``ui.backend.main`` executes.
    """

    protocol = image_launcher_protocol()
    if protocol not in {1, 2}:
        return False
    running_app = os.environ.get("CHANNELWATCH_APP_DIR", "").strip()
    if not running_app:
        return False
    try:
        running_dir = Path(running_app).resolve()
        running_dir.relative_to((RUNTIME_DIR / "releases").resolve())
    except (OSError, ValueError):
        return False
    if running_dir == IMAGE_APP_DIR:
        return False

    historical = sys.modules.get("__main__")
    if historical is None or getattr(
        historical, "_channelwatch_v0918_launcher_bridge", False
    ):
        return bool(historical is not None)
    launcher_file = getattr(historical, "__file__", "")
    try:
        launcher_path = Path(str(launcher_file)).resolve()
    except OSError:
        return False
    if launcher_path != IMAGE_APP_DIR / "core" / "runtime_launcher.py":
        return False
    original = getattr(historical, "rollback_failed_activation", None)
    if not callable(original):
        return False
    if protocol == 2 and not callable(
        getattr(historical, "request_container_restart", None)
    ):
        return False

    def bridged_rollback(error: str, *args: Any, **kwargs: Any) -> None:
        pending = kwargs.get("pending")
        outcome_lock_held = bool(kwargs.get("_outcome_lock_held", False))
        if protocol == 1:
            with _activation_outcome_lock():
                source_control = _read_control_state()
                if not _protocol_one_rollback_already_committed(
                    source_control, running_dir
                ):
                    durable_pending = source_control.get("activation-pending.json")
                    rollback_failed_activation(
                        error,
                        pending=(
                            durable_pending
                            if isinstance(durable_pending, dict)
                            else None
                        ),
                        _outcome_lock_held=True,
                    )
                try:
                    request_container_restart()
                except Exception as exc:
                    failed_job = load_json(RUNTIME_DIR / "update-job.json", {})
                    if isinstance(failed_job, dict):
                        atomic_write_json(
                            RUNTIME_DIR / "update-job.json",
                            {
                                **failed_job,
                                "restart_required": True,
                                "restart_started": False,
                                "restart_error": exc.__class__.__name__,
                                "updated_at": utc_now(),
                            },
                        )
                    log(f"Could not request legacy whole-container restart: {exc}")
            # Never let the immutable caller exec only this child from /app.
            # The exact failed job is the durable retry signal: Supervisor
            # relaunches this pinned child, the early package hook retries the
            # whole-container handoff, and both generations converge together.
            raise SystemExit(75)
        rollback_failed_activation(
            error,
            pending=pending if isinstance(pending, dict) else None,
            _outcome_lock_held=outcome_lock_held,
        )

    historical.rollback_failed_activation = bridged_rollback
    historical._channelwatch_v0918_launcher_bridge = True
    log(
        "Installed the v0.9.18 activation bridge for historical launcher "
        f"protocol {protocol}."
    )
    if protocol == 1:
        _retry_protocol_one_restored_handoff(running_dir)
        bootstrap_protocol_one_activation()
    return True


def _protocol_one_rollback_already_committed(
    control: dict[str, Any], running_dir: Path
) -> bool:
    active = control.get("active.json")
    if isinstance(active, dict):
        try:
            if Path(str(active.get("path") or "")).resolve() == running_dir:
                return False
        except OSError:
            return False
    job = control.get("update-job.json")
    if not isinstance(job, dict):
        return False
    digest = str(job.get("bundle_sha256") or "").strip().lower()
    return bool(
        job.get("operation") == "apply"
        and job.get("status") == "failed"
        and job.get("rollback_applied") is True
        and str(job.get("rolled_back_from") or "").strip().lstrip("v")
        == str(job.get("version") or "").strip().lstrip("v")
        and len(digest) == 64
        and not any(character not in "0123456789abcdef" for character in digest)
    )


def _retry_protocol_one_restored_handoff(running_dir: Path) -> None:
    """Retry Supervisor termination when a stale pinned bundle child restarts."""

    active = load_json(RUNTIME_DIR / "active.json", None)
    if isinstance(active, dict):
        try:
            if Path(str(active.get("path") or "")).resolve() == running_dir:
                return
        except OSError:
            return
    control = _read_control_state()
    if not _protocol_one_rollback_already_committed(control, running_dir):
        return
    job = control["update-job.json"]
    digest = str(job.get("bundle_sha256") or "").strip().lower()
    if (
        job.get("operation") != "apply"
        or job.get("status") != "failed"
        or job.get("rollback_applied") is not True
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        return
    try:
        request_container_restart()
    except Exception as exc:
        atomic_write_json(
            RUNTIME_DIR / "update-job.json",
            {
                **job,
                "restart_required": True,
                "restart_started": False,
                "restart_error": exc.__class__.__name__,
                "updated_at": utc_now(),
            },
        )
        log(f"Legacy rollback restart handoff is still waiting: {exc}")
    # Stale selected code must never proceed after rollback, even if signalling
    # Supervisor was rejected. Autorestart re-enters this bounded retry path.
    raise SystemExit(75)


def bootstrap_protocol_one_activation() -> bool:
    """Create quorum state before a protocol-one bundle imports its app code."""

    if image_launcher_protocol() != 1:
        return False
    selected_value = os.environ.get("CHANNELWATCH_APP_DIR", "").strip()
    if not selected_value:
        return False
    try:
        selected_dir = Path(selected_value).resolve()
        selected_dir.relative_to((RUNTIME_DIR / "releases").resolve())
    except (OSError, ValueError):
        return False
    if selected_dir == IMAGE_APP_DIR:
        return False

    start_watchdog = False
    with _activation_outcome_lock():
        active_path = RUNTIME_DIR / "active.json"
        job_path = RUNTIME_DIR / "update-job.json"
        active = load_json(active_path, None)
        job = load_json(job_path, None)
        if not isinstance(active, dict) or not isinstance(job, dict):
            return False
        try:
            active_dir = Path(str(active.get("path") or "")).resolve()
        except OSError:
            return False
        active_version = str(active.get("version") or "").strip().lstrip("v")
        if active_dir != selected_dir or not active_version:
            return False

        activation_id = str(active.get("activation_id") or "")
        if activation_id:
            pending = load_json(RUNTIME_DIR / ACTIVATION_PENDING_FILE, None)
            if (
                int(active.get("activation_protocol") or 0) != 1
                or not isinstance(pending, dict)
                or not _pending_matches_active(pending, active, selected_dir)
            ):
                return False
            start_watchdog = True
        else:
            if (
                job.get("operation") != "apply"
                or job.get("status") not in {"restarting", "validating"}
                or str(job.get("version") or "").strip().lstrip("v") != active_version
            ):
                return False
            manifest = active.get("manifest")
            manifest = manifest if isinstance(manifest, dict) else {}
            digest = str(manifest.get("bundle_sha256") or "").strip().lower()
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise RuntimeError(
                    "The selected legacy update is missing its signed bundle identity."
                )
            activation_id = uuid.uuid4().hex
            now = datetime.now(timezone.utc)
            job_id = str(job.get("job_id") or "") or uuid.uuid4().hex
            attempt_id = str(job.get("scheduler_attempt_id") or f"activation@{job_id}")
            pending = {
                "job_id": job_id,
                "version": active_version,
                "scheduler_attempt_id": attempt_id,
                "bundle_sha256": digest,
                "activation_id": activation_id,
                "path": str(selected_dir),
                "started_at": now.isoformat().replace("+00:00", "Z"),
                "deadline_at": (now + timedelta(seconds=ACTIVATION_TIMEOUT_SECONDS))
                .isoformat()
                .replace("+00:00", "Z"),
                "adopted_launcher_protocol": 1,
            }
            adopted_active = {
                **active,
                "activation_id": activation_id,
                "activation_protocol": 1,
                "activation_adopted_at": pending["started_at"],
            }
            _remove_activation_state()
            atomic_write_json(RUNTIME_DIR / ACTIVATION_PENDING_FILE, pending)
            atomic_write_json(active_path, adopted_active)
            atomic_write_json(
                job_path,
                {
                    **job,
                    "job_id": job_id,
                    "status": "validating",
                    "message": (
                        "Update started through a legacy launcher; waiting for "
                        "core and UI startup validation."
                    ),
                    "activation_id": activation_id,
                    "adopted_launcher_protocol": 1,
                    "scheduler_attempt_id": attempt_id,
                    "bundle_sha256": digest,
                    "updated_at": utc_now(),
                },
            )
            start_watchdog = True

        os.environ[ACTIVATION_ID_ENV] = activation_id
        os.environ[ACTIVATION_VERSION_ENV] = active_version

    if start_watchdog:
        start_activation_watchdog(selected_dir)
    return start_watchdog


def launcher_protocol_status() -> dict[str, Any]:
    metadata = resolve_image_metadata(image_app_dir=IMAGE_APP_DIR)
    image_version = metadata.version
    protocol = image_launcher_protocol(image_version)
    return {
        "image_version": image_version,
        "launcher_protocol": protocol,
        "recovery_capable": protocol >= LAUNCHER_PROTOCOL_RECOVERY_CAPABLE,
        "recovery_mode": os.environ.get(RECOVERY_MODE_ENV) == "1",
    }


def _image_settings_schema_version() -> int:
    metadata = load_json(IMAGE_APP_DIR / "channelwatch-image.json", {})
    try:
        return int(metadata.get("settings_schema_version") or 7)
    except (AttributeError, TypeError, ValueError):
        return 7


def selected_app_dir() -> Path:
    configured = os.environ.get("CHANNELWATCH_ACTIVE_APP_DIR", "").strip()
    fallback = Path(configured).resolve() if configured else IMAGE_APP_DIR
    protocol = image_launcher_protocol()
    if protocol < LAUNCHER_PROTOCOL_RECOVERY_CAPABLE:
        return fallback
    recovery_path = RUNTIME_DIR / RECOVERY_MODE_FILE
    recovery = load_json(recovery_path, None)
    active = load_json(RUNTIME_DIR / "active.json", None)
    if isinstance(recovery, dict):
        failed_version = str(recovery.get("failed_version") or "").lstrip("v")
        failed_digest = str(recovery.get("failed_bundle_sha256") or "").strip().lower()
        active_version = (
            str(active.get("version") or "").lstrip("v")
            if isinstance(active, dict)
            else ""
        )
        active_digest = ""
        if isinstance(active, dict) and isinstance(active.get("manifest"), dict):
            active_digest = (
                str(active["manifest"].get("bundle_sha256") or "").strip().lower()
            )
        recovery_matches = bool(
            failed_version
            and active_version == failed_version
            and (not failed_digest or active_digest == failed_digest)
        )
        if recovery_matches:
            os.environ[RECOVERY_MODE_ENV] = "1"
            return IMAGE_APP_DIR
        # A newly selected signed version or explicit rollback releases the
        # old recovery hold and lets normal activation resume.
        try:
            recovery_path.unlink()
            fsync_directory(RUNTIME_DIR)
        except FileNotFoundError:
            pass
        os.environ.pop(RECOVERY_MODE_ENV, None)
    try:
        # Protocol 3 resolves the durable selection for each child launch. The
        # entrypoint still pins both Supervisor children and replays journals;
        # this extra image-owned lookup removes stale environment selection as
        # a recovery dead end.
        from core.update_center import resolve_active_app_dir

        selection = resolve_active_app_dir(
            config_dir=CONFIG_DIR,
            image_app_dir=IMAGE_APP_DIR,
            image_version=os.environ.get("CHANNELWATCH_IMAGE_VERSION", "0.0.0"),
            runtime_abi=os.environ.get(
                "CHANNELWATCH_RUNTIME_ABI", "channelwatch-runtime-v1"
            ),
            settings_schema_version=_image_settings_schema_version(),
        )
        return selection.app_dir
    except Exception as exc:
        log(f"Protocol-3 selection failed safely; using pinned runtime: {exc}")
        return fallback


def enter_official_recovery_mode(app_dir: Path) -> bool:
    """Pin protocol-3 children to the image after a post-success bundle crash."""

    if image_launcher_protocol() < LAUNCHER_PROTOCOL_RECOVERY_CAPABLE:
        return False
    active = load_json(RUNTIME_DIR / "active.json", None)
    if not isinstance(active, dict):
        return False
    try:
        active_path = Path(str(active.get("path") or "")).resolve()
    except OSError:
        return False
    if active_path != app_dir.resolve():
        return False
    version = str(active.get("version") or "").strip().lstrip("v")
    digest = (
        str(
            (active.get("manifest") or {}).get("bundle_sha256")
            if isinstance(active.get("manifest"), dict)
            else ""
        )
        .strip()
        .lower()
    )
    if not version:
        return False
    atomic_write_json(
        RUNTIME_DIR / RECOVERY_MODE_FILE,
        {
            "schema": 1,
            "mode": "official_signed_only",
            "failed_version": version,
            "failed_bundle_sha256": digest or None,
            "entered_at": utc_now(),
        },
    )
    os.environ[RECOVERY_MODE_ENV] = "1"
    return True


def selected_static_ui_dir(app_dir: Path) -> Path:
    # Recovery pins the Python runtime and the static frontend to the same
    # immutable image. Supervisor's active-bundle environment is deliberately
    # ignored here; mixing an image backend with a failed bundle's UI could
    # make the only recovery portal unusable.
    if app_dir.resolve() == IMAGE_APP_DIR.resolve():
        return IMAGE_STATIC_UI_DIR
    # Bind the frontend to the same selected runtime as the Python children.
    # Supervisor's environment is generated when the container starts, so its
    # CHANNELWATCH_ACTIVE_STATIC_UI_DIR value can still point at the image (or
    # a previously active bundle) after a protocol-3 in-app update. Trusting
    # that inherited path creates a split runtime: new backend code serves an
    # old frontend. The selected app root is the only authoritative source.
    return app_dir / "ui" / "backend" / "static_ui"


def _evict_image_owned_app_modules(app_dir: Path) -> None:
    """Remove mutable image packages before importing a selected bundle.

    Protocol 3 resolves the active bundle with the image-owned Update Center.
    That lookup necessarily imports ``core`` before the selected application
    path is installed.  Python otherwise keeps those image modules cached and
    a later ``runpy.run_module('core.main')`` can execute a new entrypoint with
    the old package identity.  The launcher itself is running as ``__main__``,
    so removing the mutable application namespaces is safe while keeping the
    image-owned launcher and third-party dependencies resident.
    """

    try:
        selected_is_image = app_dir.resolve() == IMAGE_APP_DIR.resolve()
    except OSError:
        selected_is_image = False
    if selected_is_image:
        return

    mutable_roots = ("core", "ui")
    image_root = IMAGE_APP_DIR.resolve()
    for module_name, module in tuple(sys.modules.items()):
        if not any(
            module_name == root or module_name.startswith(f"{root}.")
            for root in mutable_roots
        ):
            continue
        origins = []
        module_file = getattr(module, "__file__", None)
        if module_file:
            origins.append(module_file)
        module_paths = getattr(module, "__path__", ())
        origins.extend(str(path) for path in module_paths)
        for origin in origins:
            try:
                if Path(origin).resolve().is_relative_to(image_root):
                    sys.modules.pop(module_name, None)
                    break
            except OSError:
                continue
    importlib.invalidate_caches()


def prepare_import_path(app_dir: Path) -> None:
    _evict_image_owned_app_modules(app_dir)
    sys.path = [str(app_dir), *(item for item in sys.path if item != str(app_dir))]
    os.environ["PYTHONPATH"] = str(app_dir)
    os.environ["CHANNELWATCH_APP_DIR"] = str(app_dir)
    os.environ["CW_STATIC_UI_DIR"] = str(selected_static_ui_dir(app_dir))
    os.environ[LAUNCHER_PROTOCOL_ENV] = str(image_launcher_protocol())
    active = load_json(RUNTIME_DIR / "active.json", None)
    try:
        active_path = Path(str(active.get("path") or "")).resolve()
    except (AttributeError, OSError):
        active_path = None
    if isinstance(active, dict) and active_path == app_dir:
        os.environ[ACTIVATION_ID_ENV] = str(active.get("activation_id") or "")
        os.environ[ACTIVATION_VERSION_ENV] = str(active.get("version") or "")
    else:
        os.environ.pop(ACTIVATION_ID_ENV, None)
        os.environ.pop(ACTIVATION_VERSION_ENV, None)
    try:
        os.chdir(app_dir)
    except OSError:
        pass


def _remove_activation_state() -> None:
    # Claim files are part of the activation transaction. Removing them only
    # after a durable success/rollback prevents an abandoned claimant from
    # being mistaken for live work on a future process start.
    for path in RUNTIME_DIR.glob("activation-*.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def rollback_failed_activation(
    error: str,
    *,
    pending: dict[str, Any] | None = None,
    _outcome_lock_held: bool = False,
) -> None:
    if not _outcome_lock_held:
        with _activation_outcome_lock():
            rollback_failed_activation(
                error,
                pending=pending,
                _outcome_lock_held=True,
            )
        return

    source_control = _read_control_state()
    rollback = source_control["rollback.json"]
    current = source_control["active.json"]
    previous = rollback.get("previous_active") if isinstance(rollback, dict) else None
    rolled_back_to = (
        str(previous.get("version") or "previous bundle")
        if isinstance(previous, dict) and previous.get("path")
        else "image"
    )
    job_id = (
        pending.get("job_id")
        if isinstance(pending, dict) and pending.get("job_id")
        else f"activation-failed-{int(datetime.now(timezone.utc).timestamp())}"
    )
    active_manifest = current.get("manifest") if isinstance(current, dict) else None
    active_manifest = active_manifest if isinstance(active_manifest, dict) else {}
    bundle_sha256 = (
        str(
            (pending.get("bundle_sha256") if isinstance(pending, dict) else None)
            or active_manifest.get("bundle_sha256")
            or ""
        )
        .strip()
        .lower()
    )
    scheduler_attempt_id = str(
        (pending.get("scheduler_attempt_id") if isinstance(pending, dict) else None)
        or f"activation@{job_id}"
    )
    job = {
        "job_id": job_id,
        "operation": "apply",
        "status": "failed",
        "version": current.get("version") if isinstance(current, dict) else None,
        "message": "Update activation failed. ChannelWatch rolled back to the previous runtime.",
        "error": error[:2000],
        "rollback_applied": True,
        "rolled_back_from": (
            current.get("version") if isinstance(current, dict) else None
        ),
        "rolled_back_to": rolled_back_to,
        "scheduler_attempt_id": scheduler_attempt_id,
        "bundle_sha256": bundle_sha256 or None,
        "failed_at": utc_now(),
        "updated_at": utc_now(),
    }
    target_control = {
        **source_control,
        "active.json": (
            previous if isinstance(previous, dict) and previous.get("path") else None
        ),
        "activation-pending.json": None,
        "activation-core-ready.json": None,
        "activation-ui-ready.json": None,
        "update-job.json": job,
    }
    if image_launcher_protocol() == 1:
        # Images v0.9.10-v0.9.15 do not replay schema-2 restart journals.
        # Restore subordinate state first and the selected runtime last.
        for name in (
            "activation-pending.json",
            "activation-core-ready.json",
            "activation-ui-ready.json",
            "update-job.json",
            "active.json",
        ):
            path = _control_path(name)
            payload = target_control[name]
            if payload is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                atomic_write_json(path, payload)
        fsync_directory(RUNTIME_DIR)
        _record_failed_activation_quarantine(
            pending=pending,
            active=current,
            job=job,
        )
        return
    journal = _build_restart_journal(
        reason="activation_rollback",
        operation="activation_rollback",
        phase="commit",
        job_id=str(job_id),
        source_active=current if isinstance(current, dict) else None,
        control=target_control,
    )
    # The journal is the first durable transition mutation. A crash after any
    # following boundary is recoverable without ever launching a pinned bundle.
    _write_restart_journal(journal)
    apply_restart_journal(journal)
    _record_failed_activation_quarantine(
        pending=pending,
        active=current,
        job=job,
    )


def _record_failed_activation_quarantine(
    *,
    pending: dict[str, Any] | None,
    active: dict[str, Any] | None,
    job: dict[str, Any],
) -> None:
    """Record policy state only after the rollback control commit is durable."""

    try:
        from core.update_policy import record_failed_activation_quarantine

        recorded = record_failed_activation_quarantine(
            CONFIG_DIR,
            pending=pending,
            active=active,
            job=job,
        )
        if not recorded:
            log(
                "Activation rollback could not bind a signed release identity; "
                "the failed job remains available for administrator review."
            )
    except Exception as quarantine_exc:
        # Rollback always takes precedence over policy bookkeeping. The failed
        # job retains the exact identity whenever it was recoverable, so a
        # subsequent v0.9.18 startup can still reconcile it safely.
        log(f"Could not persist failed-release quarantine: {quarantine_exc}")


def _supervisor_parent_pid() -> int | None:
    """Return the direct parent PID only when it is Supervisor."""

    if os.name == "nt":
        return None
    parent_pid = os.getppid()
    if parent_pid <= 0 or parent_pid == os.getpid():
        return None
    try:
        arguments = [
            item
            for item in Path(f"/proc/{parent_pid}/cmdline").read_bytes().split(b"\x00")
            if item
        ]
    except OSError:
        return None
    executable_is_supervisor = bool(
        arguments and arguments[0].rsplit(b"/", 1)[-1] == b"supervisord"
    )
    module_is_supervisor = any(
        arguments[index] == b"-m" and arguments[index + 1] == b"supervisor.supervisord"
        for index in range(len(arguments) - 1)
    )
    return parent_pid if executable_is_supervisor or module_is_supervisor else None


class _UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unix_socket.settimeout(self.timeout)
        unix_socket.connect(self.socket_path)
        self.sock = unix_socket


class _UnixSocketTransport(xmlrpc.client.Transport):
    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self.socket_path = socket_path

    def make_connection(self, host: str) -> _UnixSocketHTTPConnection:
        return _UnixSocketHTTPConnection(self.socket_path)


def _validated_supervisor_proxy(socket_path: Path) -> xmlrpc.client.ServerProxy:
    metadata = socket_path.lstat()
    if (
        not stat_module.S_ISSOCK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
    ):
        raise RuntimeError("Supervisor control socket is not a trusted runtime socket.")
    proxy = xmlrpc.client.ServerProxy(
        "http://channelwatch-supervisor/RPC2",
        transport=_UnixSocketTransport(str(socket_path)),
        allow_none=True,
    )
    for process_name in ("core", "ui"):
        info = proxy.supervisor.getProcessInfo(process_name)
        if not isinstance(info, dict) or str(info.get("name") or "") != process_name:
            raise RuntimeError("Supervisor returned an invalid process identity.")
    try:
        after = socket_path.lstat()
    except OSError as exc:
        raise RuntimeError(
            "Supervisor control socket changed during authentication."
        ) from exc
    if (
        after.st_dev != metadata.st_dev
        or after.st_ino != metadata.st_ino
        or not stat_module.S_ISSOCK(after.st_mode)
        or after.st_uid != os.geteuid()
        or after.st_nlink != 1
    ):
        raise RuntimeError("Supervisor control socket changed during authentication.")
    return proxy


def _supervisor_process_identity(
    process_name: str,
    info: Any,
) -> dict[str, int]:
    if not isinstance(info, dict) or str(info.get("name") or "") != process_name:
        raise RuntimeError("Supervisor returned an invalid process identity.")
    pid = info.get("pid")
    started_at = info.get("start")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid < 0
        or isinstance(started_at, bool)
        or not isinstance(started_at, int)
        or started_at < 0
    ):
        raise RuntimeError("Supervisor returned an incomplete process identity.")
    return {"pid": pid, "start": started_at}


def _capture_supervisor_processes(
    proxy: xmlrpc.client.ServerProxy,
) -> dict[str, dict[str, int]]:
    captured: dict[str, dict[str, int]] = {}
    for process_name in PROTOCOL_THREE_PROCESS_NAMES:
        info = proxy.supervisor.getProcessInfo(process_name)
        identity = _supervisor_process_identity(process_name, info)
        state = str(info.get("statename") or "").upper()
        if identity["pid"] <= 0 or state != "RUNNING":
            raise RuntimeError(
                f"Supervisor process {process_name} is not running and signalable."
            )
        captured[process_name] = identity
    return captured


def _capture_or_recover_supervisor_processes_for_ordinary_restart(
    proxy: xmlrpc.client.ServerProxy,
) -> dict[str, dict[str, int]]:
    """Recover one stopped child before an ordinary coordinated restart."""

    initial = {
        process_name: proxy.supervisor.getProcessInfo(process_name)
        for process_name in PROTOCOL_THREE_PROCESS_NAMES
    }
    unavailable = [
        process_name
        for process_name, info in initial.items()
        if _supervisor_process_identity(process_name, info)["pid"] <= 0
        or str(info.get("statename") or "").upper() != "RUNNING"
    ]
    if len(unavailable) > 1:
        raise RuntimeError(
            "An ordinary restart cannot recover more than one unavailable service."
        )
    if unavailable:
        process_name = unavailable[0]
        info = initial[process_name]
        state = str(info.get("statename") or "").upper()
        identity = _supervisor_process_identity(process_name, info)
        if identity["pid"] == 0 and state in {"STOPPED", "EXITED", "FATAL"}:
            proxy.supervisor.startProcess(process_name, False)
        elif state not in {"STARTING", "BACKOFF"}:
            raise RuntimeError(
                f"Supervisor process {process_name} cannot be recovered safely."
            )

        deadline = time.monotonic() + PROTOCOL_THREE_STOP_BARRIER_TIMEOUT_SECONDS
        while True:
            info = proxy.supervisor.getProcessInfo(process_name)
            identity = _supervisor_process_identity(process_name, info)
            if (
                identity["pid"] > 0
                and str(info.get("statename") or "").upper() == "RUNNING"
            ):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Supervisor process {process_name} did not recover in time."
                )
            time.sleep(PROTOCOL_THREE_STOP_BARRIER_INTERVAL_SECONDS)
    return _capture_supervisor_processes(proxy)


def _old_supervisor_process_is_gone(
    proxy: xmlrpc.client.ServerProxy,
    process_name: str,
    old_identity: dict[str, int],
) -> bool:
    current = _supervisor_process_identity(
        process_name,
        proxy.supervisor.getProcessInfo(process_name),
    )
    return current != old_identity


def _wait_for_supervisor_stop_barrier(
    proxy: xmlrpc.client.ServerProxy,
    old_processes: dict[str, dict[str, int]],
) -> None:
    deadline = time.monotonic() + PROTOCOL_THREE_STOP_BARRIER_TIMEOUT_SECONDS
    while True:
        if all(
            _old_supervisor_process_is_gone(
                proxy,
                process_name,
                old_processes[process_name],
            )
            for process_name in PROTOCOL_THREE_PROCESS_NAMES
        ):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Supervisor did not retire both old process generations in time."
            )
        time.sleep(PROTOCOL_THREE_STOP_BARRIER_INTERVAL_SECONDS)


def _protocol_three_restart_helper_checkpoint(phase: str) -> None:
    """No-op fault-injection boundary for the image-owned restart helper."""

    del phase


def _write_restart_helper_ack(ack_fd: int, value: bytes) -> None:
    try:
        os.write(ack_fd, value)
    finally:
        os.close(ack_fd)


def restart_supervisor_services(*, socket_path: Path, ack_fd: int) -> int:
    """Restart core and UI from an image-owned helper outside either child."""

    try:
        with _protocol_three_restart_helper_lock(socket_path):
            proxy = _validated_supervisor_proxy(socket_path)
            marker_was_present = protocol_three_handoff_present()
            expected_journal = validate_protocol_three_restart_handoff_if_present()
            if expected_journal is None and marker_was_present:
                # A prior helper and replacement child may have completed while
                # this duplicate was being spawned. Strictly clear only a stale,
                # already-validated marker and acknowledge the no-op.
                accept_protocol_three_restart_handoff_if_present()
                _write_restart_helper_ack(ack_fd, b"ready\n")
                return 0
            if protocol_three_handoff_present():
                # The exact post-barrier marker already authorizes replacement
                # children. Never signal those replacements a second time.
                _write_restart_helper_ack(ack_fd, b"ready\n")
                return 0

            old_processes = (
                _capture_supervisor_processes(proxy)
                if expected_journal is not None
                else _capture_or_recover_supervisor_processes_for_ordinary_restart(
                    proxy
                )
            )
            _protocol_three_restart_helper_checkpoint("identities-captured")

            # Acknowledge only after the image-owned helper owns the operation,
            # authenticated Supervisor, validated the journal, and captured both
            # old generations. A failed pipe write occurs before any signal or
            # handoff marker, so the caller can safely retry or abort.
            _write_restart_helper_ack(ack_fd, b"ready\n")
            time.sleep(PROTOCOL_THREE_RESTART_GRACE_SECONDS)
            _protocol_three_restart_helper_checkpoint("before-signals")

            for process_name in PROTOCOL_THREE_PROCESS_NAMES:
                identity = old_processes[process_name]
                # Revalidate immediately before each fixed-name TERM. A natural
                # restart or PID reuse must never let a duplicate kill a newer
                # process generation.
                current_identity = _supervisor_process_identity(
                    process_name,
                    proxy.supervisor.getProcessInfo(process_name),
                )
                if current_identity != identity:
                    continue
                proxy.supervisor.signalProcess(process_name, "TERM")
                _protocol_three_restart_helper_checkpoint(f"signal-sent:{process_name}")

            _wait_for_supervisor_stop_barrier(proxy, old_processes)
            _protocol_three_restart_helper_checkpoint("stop-barrier-complete")
            if expected_journal is not None:
                accept_protocol_three_restart_handoff_if_present(
                    old_processes=old_processes,
                    expected_journal=expected_journal,
                )
                _protocol_three_restart_helper_checkpoint("handoff-published")
            return 0
    except Exception as exc:
        try:
            _write_restart_helper_ack(
                ack_fd,
                f"error:{exc.__class__.__name__}\n".encode(),
            )
        except OSError:
            pass
        log(f"Coordinated service restart encountered {exc.__class__.__name__}.")
        return 1


def _spawn_protocol_three_restart_helper() -> None:
    read_fd, write_fd = os.pipe()
    command = [
        sys.executable,
        str(IMAGE_APP_DIR / "core" / "runtime_launcher.py"),
        "restart-services",
        "--socket",
        SUPERVISOR_SOCKET_FILE,
        "--ack-fd",
        str(write_fd),
    ]
    try:
        # Fixed argv, no shell, and an image-owned executable prevent command injection.
        subprocess.Popen(
            command,  # nosemgrep
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
            close_fds=True,
            pass_fds=(write_fd,),
            start_new_session=True,
        )
    except Exception:
        os.close(read_fd)
        os.close(write_fd)
        raise
    os.close(write_fd)
    try:
        ready, _, _ = select.select(
            [read_fd], [], [], PROTOCOL_THREE_RESTART_ACK_TIMEOUT_SECONDS
        )
        response = os.read(read_fd, 64) if ready else b""
    finally:
        os.close(read_fd)
    if response != b"ready\n":
        raise RuntimeError("Image-owned Supervisor restart helper was not accepted.")


def request_container_restart() -> None:
    """Restart both services coherently for the image launcher generation.

    Protocol 3 uses an image-owned helper and does not depend on Docker's
    container restart policy. Historical launchers still require a complete
    container handoff so their entrypoint can reselect the active runtime.
    """

    if image_launcher_protocol() >= LAUNCHER_PROTOCOL_RECOVERY_CAPABLE:
        _spawn_protocol_three_restart_helper()
        return

    supervisor_pid = _supervisor_parent_pid()
    if supervisor_pid is None:
        raise RuntimeError(
            "Coordinated container restart requires supervisord as the direct parent."
        )
    os.kill(supervisor_pid, signal.SIGTERM)


def handoff_required_restart_before_launch() -> bool:
    """Retry a durable restart handoff and block application launch.

    The sentinel belongs to the next container entrypoint, not to an individual
    core/UI child. Its mere presence is therefore fail-closed even when the JSON
    payload is incomplete or corrupt.
    """

    if not restart_journal_present():
        if (
            image_launcher_protocol() >= LAUNCHER_PROTOCOL_RECOVERY_CAPABLE
            and protocol_three_handoff_present()
        ):
            try:
                accept_protocol_three_restart_handoff_if_present()
            except Exception as exc:
                log(f"Could not clear a stale protocol-3 handoff marker: {exc}")
        return False
    if image_launcher_protocol() >= LAUNCHER_PROTOCOL_RECOVERY_CAPABLE:
        try:
            consumed = consume_protocol_three_restart_journal_before_launch()
        except Exception as exc:
            log(f"Could not acknowledge protocol-3 restart journal safely: {exc}")
        else:
            if consumed:
                log("Acknowledged the protocol-3 restart journal before launch.")
            return False
    try:
        apply_restart_journal()
    except Exception as exc:
        log(f"Could not replay required restart journal safely: {exc}")
    log("A rollback requires a whole-container restart; application launch is blocked.")
    try:
        request_container_restart()
    except Exception as exc:
        log(f"Could not hand off the required whole-container restart: {exc}")
    # Supervisor's default startsecs is one second. Remaining alive beyond that
    # boundary prevents repeated fail-closed child retries from being exhausted
    # into a FATAL state before a transient parent handoff can recover.
    time.sleep(RESTART_REQUIRED_PRELAUNCH_DELAY_SECONDS)
    return True


def _restart_required_watchdog_loop() -> None:
    """Stop a running sibling when another process commits rollback."""

    while True:
        if restart_journal_present():
            log("Detected a rollback requiring a whole-container restart.")
            try:
                apply_restart_journal()
            except Exception as exc:
                log(f"Could not replay required restart journal safely: {exc}")
            parent_handoff_started = False
            try:
                request_container_restart()
                parent_handoff_started = True
            except Exception as exc:
                log(
                    "Could not hand off the required whole-container restart; "
                    f"terminating this child so its next launch retries: {exc}"
                )
            if image_launcher_protocol() >= LAUNCHER_PROTOCOL_RECOVERY_CAPABLE:
                # The protocol-3 acknowledgement precedes the helper's fixed
                # TERM signals. Stay alive so a helper failure before either
                # signal leaves this watchdog available to retry; Supervisor
                # ends and autorestarts this generation once the helper acts.
                time.sleep(RESTART_REQUIRED_WATCHDOG_INTERVAL_SECONDS)
                continue
            try:
                # Stop stale code promptly even after Supervisor accepts its
                # own SIGTERM; the parent shutdown and child termination are
                # deliberately independent fail-closed operations.
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception as terminate_exc:
                log(
                    "Could not terminate this child after restart handoff "
                    f"attempt: {terminate_exc}"
                )
                if not parent_handoff_started:
                    time.sleep(RESTART_REQUIRED_WATCHDOG_INTERVAL_SECONDS)
                    continue
            return
        time.sleep(RESTART_REQUIRED_WATCHDOG_INTERVAL_SECONDS)


def start_restart_required_watchdog() -> threading.Thread:
    """Watch for a rollback sentinel created after this child starts."""

    thread = threading.Thread(
        target=_restart_required_watchdog_loop,
        daemon=True,
        name="restart-required-watchdog",
    )
    thread.start()
    return thread


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _marker_matches_pending(component: str, pending: dict[str, Any]) -> bool:
    marker = load_json(RUNTIME_DIR / f"activation-{component}-ready.json", {})
    return isinstance(marker, dict) and all(
        marker.get(key) == value
        for key, value in {
            "component": component,
            "version": str(pending.get("version") or "").strip().lstrip("v"),
            "activation_id": str(pending.get("activation_id") or ""),
            "path": str(pending.get("path") or ""),
            "healthy": True,
        }.items()
    )


def _pending_matches_active(
    pending: dict[str, Any], active: dict[str, Any], app_dir: Path | None = None
) -> bool:
    if not all(
        str(pending.get(key) or "") == str(active.get(key) or "")
        for key in ("activation_id", "version", "path")
    ) or not str(active.get("activation_id") or ""):
        return False
    if app_dir is None:
        return True
    try:
        return Path(str(pending.get("path") or "")).resolve() == app_dir.resolve()
    except OSError:
        return False


def _claim_pending(pending: dict[str, Any], *, claimant: str) -> Path | None:
    activation_id = str(pending.get("activation_id") or "unknown")
    claim_path = RUNTIME_DIR / f"activation-{claimant}-{activation_id}.json"
    pending_path = RUNTIME_DIR / ACTIVATION_PENDING_FILE
    if not pending_path.is_file():
        return None
    try:
        os.replace(pending_path, claim_path)
    except FileNotFoundError:
        return None
    fsync_directory(RUNTIME_DIR)
    claimed = load_json(claim_path, None)
    if not isinstance(claimed, dict) or any(
        str(claimed.get(key) or "") != str(pending.get(key) or "")
        for key in ("activation_id", "version", "path")
    ):
        return None
    return claim_path


def recover_claimed_activation(app_dir: Path) -> bool:
    """Restore pending state left behind by an interrupted claimant.

    Claimant-specific files are the durable post-rename transaction record.
    Only a claim matching the selected runtime generation can be recovered.
    """

    pending_path = RUNTIME_DIR / ACTIVATION_PENDING_FILE
    active = load_json(RUNTIME_DIR / "active.json", None)
    if not isinstance(active, dict):
        return False

    pending = load_json(pending_path, None)
    if isinstance(pending, dict) and _pending_matches_active(pending, active, app_dir):
        return True

    candidates = list(
        path
        for path in sorted(RUNTIME_DIR.glob("activation-*.json"))
        if path.name
        not in {
            ACTIVATION_PENDING_FILE,
            "activation-core-ready.json",
            "activation-ui-ready.json",
        }
    )
    for candidate_path in candidates:
        candidate = load_json(candidate_path, None)
        if not isinstance(candidate, dict):
            continue
        if not _pending_matches_active(candidate, active, app_dir):
            continue
        try:
            # A same-directory hard link publishes canonical pending state
            # atomically without overwriting it. A late recoverer can therefore
            # never recreate pending after another process claimed/completed it.
            os.link(candidate_path, pending_path)
        except (FileExistsError, FileNotFoundError):
            recovered = load_json(pending_path, None)
            return bool(
                isinstance(recovered, dict)
                and _pending_matches_active(recovered, active, app_dir)
            )
        except OSError as exc:
            log(f"Could not publish recovered activation state: {exc}")
            return False
        try:
            candidate_path.unlink()
        except FileNotFoundError:
            pass
        fsync_directory(RUNTIME_DIR)
        recovered = load_json(pending_path, None)
        if not isinstance(recovered, dict) or not _pending_matches_active(
            recovered, active, app_dir
        ):
            return False
        log(
            "Recovered interrupted update activation "
            f"{candidate.get('activation_id')} from {candidate_path.name}."
        )
        return True
    return False


def _complete_activation_from_markers(pending: dict[str, Any]) -> bool:
    """Recover the success record if both components won the readiness race."""

    active = load_json(RUNTIME_DIR / "active.json", None)
    if not isinstance(active, dict) or not _pending_matches_active(pending, active):
        # A different state transition won after this watchdog claimed the
        # pending file. Never publish success for a runtime that is no longer
        # selected; retain the claim as a forensic/recovery record.
        return False
    job_path = RUNTIME_DIR / "update-job.json"
    job = load_json(job_path, {})
    atomic_write_json(
        job_path,
        {
            **(job if isinstance(job, dict) else {}),
            "job_id": pending.get("job_id"),
            "operation": "apply",
            "version": str(pending.get("version") or "").strip().lstrip("v"),
            "status": "success",
            "message": "Update activated and ChannelWatch started successfully.",
            "validated_at": utc_now(),
            "updated_at": utc_now(),
        },
    )
    _remove_activation_state()
    return True


def enforce_activation_deadline(*, now: datetime | None = None) -> bool:
    """Commit exactly one startup quorum or deadline outcome."""

    if (
        restart_journal_present()
        or not (RUNTIME_DIR / ACTIVATION_PENDING_FILE).exists()
    ):
        return False
    with _activation_outcome_lock():
        return _enforce_activation_deadline_locked(now=now)


def _enforce_activation_deadline_locked(*, now: datetime | None = None) -> bool:
    """Rollback an activation that missed its core/UI readiness deadline."""

    if restart_journal_present():
        return False
    pending_path = RUNTIME_DIR / ACTIVATION_PENDING_FILE
    pending = load_json(pending_path, None)
    if not isinstance(pending, dict):
        return False
    active = load_json(RUNTIME_DIR / "active.json", None)
    if not isinstance(active, dict) or not _pending_matches_active(pending, active):
        # Never let stale validation metadata act on a different runtime.
        try:
            pending_path.unlink()
        except FileNotFoundError:
            pass
        return False

    deadline = _parse_utc(pending.get("deadline_at"))
    current_time = now or datetime.now(timezone.utc)
    if deadline is not None and current_time < deadline:
        return False

    if all(_marker_matches_pending(component, pending) for component in ("core", "ui")):
        claim = _claim_pending(pending, claimant="completed-watchdog")
        if claim is None:
            return False
        try:
            if not _complete_activation_from_markers(pending):
                return False
        except Exception:
            if not pending_path.exists():
                os.replace(claim, pending_path)
            raise
        else:
            try:
                claim.unlink()
            except FileNotFoundError:
                pass
        return False

    claim = _claim_pending(pending, claimant="failed-watchdog")
    if claim is None:
        return False
    try:
        current_active = load_json(RUNTIME_DIR / "active.json", None)
        if not isinstance(current_active, dict) or not _pending_matches_active(
            pending, current_active
        ):
            return False
        rollback_failed_activation(
            "Update activation deadline expired before both core and UI reported readiness.",
            pending=pending,
            _outcome_lock_held=True,
        )
    except Exception:
        pending_path = RUNTIME_DIR / ACTIVATION_PENDING_FILE
        if (
            not restart_journal_present()
            and claim.exists()
            and not pending_path.exists()
        ):
            os.replace(claim, pending_path)
        raise
    else:
        try:
            claim.unlink()
        except FileNotFoundError:
            pass
    try:
        request_container_restart()
    except Exception:
        if image_launcher_protocol() == 1:
            # The exact failed job remains a durable handoff retry signal. Stop
            # this stale child so Supervisor immediately relaunches it through
            # the package bootstrap, which retries terminating the parent.
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception as terminate_exc:
                log(f"Could not terminate stale legacy bundle child: {terminate_exc}")
        raise
    return True


def _activation_watchdog_loop() -> None:
    while (RUNTIME_DIR / ACTIVATION_PENDING_FILE).exists():
        try:
            if enforce_activation_deadline():
                return
        except Exception as exc:
            # A watchdog failure must not crash an otherwise healthy service.
            # The other component's watchdog or the next container start gets
            # another chance to finish the same durable transition.
            log(f"Activation watchdog error: {exc}")
        time.sleep(ACTIVATION_WATCHDOG_INTERVAL_SECONDS)


def start_activation_watchdog(app_dir: Path) -> threading.Thread | None:
    if restart_journal_present() or not RUNTIME_DIR.is_dir():
        return None
    if not any(RUNTIME_DIR.glob("activation-*.json")):
        return None
    with _activation_outcome_lock():
        if restart_journal_present():
            return None
        recover_claimed_activation(app_dir)
        pending = load_json(RUNTIME_DIR / ACTIVATION_PENDING_FILE, None)
        active = load_json(RUNTIME_DIR / "active.json", None)
        if (
            not isinstance(pending, dict)
            or not isinstance(active, dict)
            or not _pending_matches_active(pending, active, app_dir)
        ):
            return None
    thread = threading.Thread(
        target=_activation_watchdog_loop,
        daemon=True,
        name="activation-watchdog",
    )
    thread.start()
    return thread


def is_pending_activation(app_dir: Path) -> bool:
    if restart_journal_present() or not RUNTIME_DIR.is_dir():
        return False
    if not any(RUNTIME_DIR.glob("activation-*.json")):
        return False
    with _activation_outcome_lock():
        if restart_journal_present():
            return False
        recover_claimed_activation(app_dir)
        pending = load_json(RUNTIME_DIR / ACTIVATION_PENDING_FILE, None)
        active = load_json(RUNTIME_DIR / "active.json", None)
        return (
            isinstance(pending, dict)
            and isinstance(active, dict)
            and _pending_matches_active(pending, active, app_dir)
        )


def claim_pending_activation_failure(
    app_dir: Path, *, _outcome_lock_held: bool = False
) -> Path | None:
    if not RUNTIME_DIR.is_dir() or not any(RUNTIME_DIR.glob("activation-*.json")):
        return None
    if not _outcome_lock_held:
        with _activation_outcome_lock():
            return claim_pending_activation_failure(app_dir, _outcome_lock_held=True)
    if restart_journal_present():
        return None
    recover_claimed_activation(app_dir)
    pending = load_json(RUNTIME_DIR / ACTIVATION_PENDING_FILE, None)
    active = load_json(RUNTIME_DIR / "active.json", None)
    if (
        not isinstance(pending, dict)
        or not isinstance(active, dict)
        or not _pending_matches_active(pending, active, app_dir)
    ):
        return None
    return _claim_pending(pending, claimant=f"failed-launcher-{os.getpid()}")


def run_core(args: argparse.Namespace) -> None:
    sys.argv = ["python -m core.main", *args.app_args]
    runpy.run_module("core.main", run_name="__main__")


def run_ui(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "ui.backend.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        # Forwarded headers are interpreted exclusively by ChannelWatch after
        # validating the direct peer against CW_TRUSTED_PROXIES.
        proxy_headers=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch ChannelWatch from image or active bundle."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    core = subparsers.add_parser("core")
    core.add_argument("app_args", nargs=argparse.REMAINDER)

    ui = subparsers.add_parser("ui")
    ui.add_argument("--host", default="0.0.0.0")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--log-level", default="warning")

    restart_services = subparsers.add_parser("restart-services")
    restart_services.add_argument("--socket", required=True)
    restart_services.add_argument("--ack-fd", required=True, type=int)

    args, unknown_args = parser.parse_known_args(argv)
    if args.mode == "core":
        args.app_args.extend(unknown_args)
    elif unknown_args:
        parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
    if args.mode == "restart-services":
        return restart_supervisor_services(
            socket_path=Path(args.socket),
            ack_fd=args.ack_fd,
        )
    if handoff_required_restart_before_launch():
        return 1
    start_restart_required_watchdog()
    app_dir = selected_app_dir()
    prepare_import_path(app_dir)
    start_activation_watchdog(app_dir)
    log(f"Launching {args.mode} from {app_dir}")

    try:
        if args.mode == "core":
            run_core(args)
        elif args.mode == "ui":
            run_ui(args)
        return 0
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        error = traceback.format_exc()
        claim = None
        if app_dir != IMAGE_APP_DIR:
            with _activation_outcome_lock():
                claim = claim_pending_activation_failure(
                    app_dir, _outcome_lock_held=True
                )
                if claim is not None:
                    log(
                        "Active bundle failed during startup; rolling back and restarting all services."
                    )
                    try:
                        rollback_failed_activation(
                            error,
                            pending=load_json(claim, None),
                            _outcome_lock_held=True,
                        )
                    except Exception:
                        pending_path = RUNTIME_DIR / ACTIVATION_PENDING_FILE
                        if (
                            not restart_journal_present()
                            and claim.exists()
                            and not pending_path.exists()
                        ):
                            os.replace(claim, pending_path)
                        raise
                    else:
                        try:
                            claim.unlink()
                        except FileNotFoundError:
                            pass
        if claim is not None:
            try:
                request_container_restart()
            except Exception as restart_exc:
                log(f"Could not request coordinated container restart: {restart_exc}")
        elif isinstance(exc, SystemExit):
            raise
        elif app_dir != IMAGE_APP_DIR and enter_official_recovery_mode(app_dir):
            log(
                "An active bundle failed after activation; entering official-signed "
                "image recovery mode and restarting all services."
            )
            try:
                request_container_restart()
            except Exception as restart_exc:
                log(f"Could not request recovery container restart: {restart_exc}")
        print(error, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
