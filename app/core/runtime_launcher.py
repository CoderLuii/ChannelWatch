"""Image-stable process launcher for active app bundles."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import signal
import stat as stat_module
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
ACTIVATION_PENDING_FILE = "activation-pending.json"
ACTIVATION_WATCHDOG_INTERVAL_SECONDS = 1.0
RESTART_REQUIRED_FILE = "restart-required.json"
RESTART_JOURNAL_LOCK_FILE = "restart-required.lock"
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


def restart_required_path() -> Path:
    return RUNTIME_DIR / RESTART_REQUIRED_FILE


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
        raise RuntimeError("The activation outcome lock cannot be opened safely.") from exc
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
    if any(value is not None and not isinstance(value, dict) for value in control.values()):
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
        before = os.lstat(path)
    except FileNotFoundError as exc:
        raise RuntimeError("The expected restart journal no longer exists.") from exc
    except OSError as exc:
        raise RuntimeError("The restart journal cannot be inspected safely.") from exc
    if not stat_module.S_ISREG(before.st_mode):
        raise RuntimeError("The restart journal is not a regular file.")
    if before.st_nlink != 1:
        raise RuntimeError("The restart journal is hard-linked.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        after = os.lstat(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The restart journal cannot be read safely.") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_nlink,
    ):
        raise RuntimeError("The restart journal changed while it was being read.")
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


def _write_restart_journal(journal: dict[str, Any]) -> None:
    """Atomically publish one fully written journal without clobbering."""

    validated = _validate_restart_journal(journal)
    path = restart_required_path()
    with _restart_journal_lock():
        _cleanup_restart_journal_candidates()
        staged_path = path.with_name(
            f".{path.name}.candidate-{uuid.uuid4().hex}"
        )
        atomic_write_json(staged_path, validated)
        try:
            _restart_transition_checkpoint("journal:before-create")
            try:
                os.link(staged_path, path)
            except FileExistsError as exc:
                raise RuntimeError(
                    "Another restart journal won publication."
                ) from exc
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


def apply_restart_journal(journal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Idempotently publish the exact state recorded by a schema-2 journal."""

    with _restart_journal_lock():
        if journal is None:
            journal = _load_restart_journal_strict()
        validated = _validate_restart_journal(journal)
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
        return validated


def selected_app_dir() -> Path:
    configured = os.environ.get("CHANNELWATCH_ACTIVE_APP_DIR", "").strip()
    return Path(configured).resolve() if configured else IMAGE_APP_DIR


def selected_static_ui_dir(app_dir: Path) -> Path:
    configured = os.environ.get("CHANNELWATCH_ACTIVE_STATIC_UI_DIR", "").strip()
    if configured:
        return Path(configured).resolve()
    if app_dir == IMAGE_APP_DIR:
        return IMAGE_STATIC_UI_DIR
    return app_dir / "ui" / "backend" / "static_ui"


def prepare_import_path(app_dir: Path) -> None:
    sys.path = [str(app_dir), *(item for item in sys.path if item != str(app_dir))]
    os.environ["PYTHONPATH"] = str(app_dir)
    os.environ["CHANNELWATCH_APP_DIR"] = str(app_dir)
    os.environ["CW_STATIC_UI_DIR"] = str(selected_static_ui_dir(app_dir))
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
    job = {
        "job_id": job_id,
        "operation": "apply",
        "status": "failed",
        "version": current.get("version") if isinstance(current, dict) else None,
        "message": "Update activation failed. ChannelWatch rolled back to the previous runtime.",
        "error": error[:2000],
        "rollback_applied": True,
        "rolled_back_from": current.get("version") if isinstance(current, dict) else None,
        "rolled_back_to": rolled_back_to,
        "failed_at": utc_now(),
        "updated_at": utc_now(),
    }
    target_control = {
        **source_control,
        "active.json": (
            previous
            if isinstance(previous, dict) and previous.get("path")
            else None
        ),
        "activation-pending.json": None,
        "activation-core-ready.json": None,
        "activation-ui-ready.json": None,
        "update-job.json": job,
    }
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
        arguments[index] == b"-m"
        and arguments[index + 1] == b"supervisor.supervisord"
        for index in range(len(arguments) - 1)
    )
    return parent_pid if executable_is_supervisor or module_is_supervisor else None


def request_container_restart() -> None:
    """Terminate Supervisor so Docker restarts core and UI as one generation."""

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

    if restart_journal_present() or not (
        RUNTIME_DIR / ACTIVATION_PENDING_FILE
    ).exists():
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
    request_container_restart()
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
            return claim_pending_activation_failure(
                app_dir, _outcome_lock_held=True
            )
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
    parser = argparse.ArgumentParser(description="Launch ChannelWatch from image or active bundle.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    core = subparsers.add_parser("core")
    core.add_argument("app_args", nargs=argparse.REMAINDER)

    ui = subparsers.add_parser("ui")
    ui.add_argument("--host", default="0.0.0.0")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--log-level", default="warning")

    args, unknown_args = parser.parse_known_args(argv)
    if args.mode == "core":
        args.app_args.extend(unknown_args)
    elif unknown_args:
        parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
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
        print(error, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
