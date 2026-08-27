"""Persistent scheduled-update policy and official recovery primitives."""

from __future__ import annotations

import json
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone, tzinfo
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:  # pragma: no cover - exercised by fail-closed platform tests
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms are unsupported
    fcntl = None

from core.helpers.atomic_io import atomic_write_json
from core.update_catalog import DEFAULT_UPDATE_CATALOG_URL, DeliveryMode
from core.update_center import (
    UPDATE_PUBLIC_KEYS,
    UpdateBundleError,
    UpdateCenterError,
    UpdateLockedError,
    UpdateManager,
    UpdateManifestError,
    compare_versions,
    load_json,
)

POLICY_SCHEMA_VERSION = 1
SCHEDULER_STATE_SCHEMA_VERSION = 1
DEFAULT_MAINTENANCE_WINDOW_START = "03:00"
DEFAULT_MAINTENANCE_WINDOW_MINUTES = 120
FIRST_CHECK_DELAY = timedelta(minutes=5)
CHECK_CADENCE = timedelta(hours=6)
AUTOMATIC_RESTART_GRACE = timedelta(minutes=5)
DRAFT_POSTPONE_DURATION = timedelta(hours=24)
RETRY_DELAYS = (
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
)
ACTIVATION_RECONCILE_DELAY = timedelta(minutes=1)
PENDING_ATTEMPT_PHASES = {"apply_started", "activation_pending"}
PRE_HANDOFF_JOB_STATUSES = {"backing_up", "downloading", "verifying", "applying"}
PENDING_JOB_STATUSES = {"restarting", "validating"}
SUCCESS_JOB_STATUSES = {"success", "current"}
FAILED_JOB_STATUSES = {"failed", "rejected"}
ATTEMPT_PHASES = PENDING_ATTEMPT_PHASES | {"success", "failed", "interrupted"}
ATTEMPT_FIELDS = {
    "version",
    "bundle_sha256",
    "attempted_at",
    "attempt_id",
    "job_id",
    "phase",
    "automatic",
    "clear_hold_on_success",
    "completed_at",
    "terminal_job_status",
    "failure_reason",
    "rollback_applied",
}
MAX_SCHEDULER_HISTORY_ITEMS = 128
MAX_POLICY_STATE_BYTES = 1024 * 1024
SCHEDULER_LOCK_WAIT_SECONDS = 5.0
SCHEDULER_STATE_FIELDS = {
    "schema",
    "created_at",
    "last_check_at",
    "last_success_at",
    "last_failure_at",
    "last_error",
    "next_check_at",
    "postponed_until",
    "retry_count",
    "stable_install_jitter_minutes",
    "quarantines",
    "rollback_holds",
    "observed_release_digests",
    "last_attempt",
    "last_install_attempt_id",
    "last_install_local_date",
    "scheduled_restart_at",
    "scheduled_release_version",
    "scheduled_release_sha256",
    "scheduled_attempt_id",
    "draft_postpone_used",
    "deferred_attempt_id",
    "notification_drain_retry_attempt_id",
    "maintenance_attention_code",
    "maintenance_attention_at",
    "maintenance_attention_message",
}


class UpdatePolicyStorageError(UpdateCenterError):
    """Unsafe or corrupt policy/state was preserved and rejected."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def channelwatch_timezone() -> tzinfo:
    configured = os.environ.get("TZ", "").strip()
    if configured:
        try:
            return ZoneInfo(configured)
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_window_start(value: str) -> clock_time:
    if len(value) != 5 or value[2] != ":":
        raise ValueError("maintenance_window_start must use 24-hour HH:MM.")
    try:
        parsed = clock_time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("maintenance_window_start must use 24-hour HH:MM.") from exc
    if parsed.second or parsed.microsecond:
        raise ValueError("maintenance_window_start must be minute-aligned.")
    return parsed


@dataclass(frozen=True)
class UpdatePolicy:
    """The complete stable public policy contract used by the UI/API."""

    schema: int = POLICY_SCHEMA_VERSION
    mode: str = "automatic"
    channel: str = "stable"
    maintenance_window_start: str = DEFAULT_MAINTENANCE_WINDOW_START
    maintenance_window_minutes: int = DEFAULT_MAINTENANCE_WINDOW_MINUTES
    timezone_source: str = "channelwatch"

    def validate(self) -> UpdatePolicy:
        if type(self.schema) is not int or self.schema != POLICY_SCHEMA_VERSION:
            raise ValueError("Unsupported update policy schema.")
        if self.mode not in {"automatic", "notify_only"}:
            raise ValueError("mode must be automatic or notify_only.")
        if self.channel != "stable":
            raise ValueError("Only the stable update channel is supported.")
        _parse_window_start(self.maintenance_window_start)
        if (
            type(self.maintenance_window_minutes) is not int
            or not 15 <= self.maintenance_window_minutes <= 12 * 60
        ):
            raise ValueError("maintenance_window_minutes must be 15 through 720.")
        if self.timezone_source != "channelwatch":
            raise ValueError("timezone_source must be channelwatch.")
        return self

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> UpdatePolicy:
        allowed = {item.name for item in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(
                "Unknown update policy fields: " + ", ".join(sorted(unknown))
            )
        return cls(**dict(raw)).validate()


def _safe_json_object(path: Path) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UpdatePolicyStorageError(
            f"Update policy state cannot be inspected safely: {path.name}."
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_POLICY_STATE_BYTES
    ):
        raise UpdatePolicyStorageError(
            f"Refusing unsafe update policy state file: {path.name}."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdatePolicyStorageError(
            f"Update policy state is corrupt and was preserved: {path.name}."
        ) from exc
    if not isinstance(raw, dict):
        raise UpdatePolicyStorageError(
            f"Update policy state must be an object: {path.name}."
        )
    return raw


def _safe_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _safe_json_object(path)
    atomic_write_json(path, dict(payload), sort_keys=True)


class UpdateSchedulerStateLock:
    """Serialize scheduler state through a real kernel-held advisory lock.

    Older releases used the existence of ``update-scheduler.lock`` as the lock
    itself.  If a thread was interrupted after creating that file, the same
    still-running UI process could make the marker appear live forever.  This
    lock uses ``flock`` ownership instead: a leftover file is harmless unless
    another process is actively holding its descriptor.
    """

    def __init__(self, lock_path: Path, *, wait_timeout: float = 0.0):
        self.lock_path = Path(lock_path)
        self.wait_timeout = max(0.0, float(wait_timeout))
        self._fd: int | None = None

    def __enter__(self) -> UpdateSchedulerStateLock:
        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise UpdateLockedError(
                "Safe update scheduler state locking is unavailable."
            )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                self.lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
        except OSError as exc:
            raise UpdateLockedError(
                "The update scheduler state lock cannot be opened safely."
            ) from exc
        try:
            metadata = os.fstat(self._fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise UpdateLockedError(
                    "The update scheduler state lock is not a single-link regular file."
                )
            os.fchmod(self._fd, 0o600)
            deadline = time.monotonic() + self.wait_timeout
            while True:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise UpdateLockedError(
                            "Another update scheduler state operation is already running."
                        ) from exc
                    time.sleep(0.02)
        except Exception:
            os.close(self._fd)
            self._fd = None
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


class UpdatePolicyStore:
    """Atomic policy persistence outside the v0.9 settings schema."""

    def __init__(
        self,
        config_dir: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.runtime_dir = Path(config_dir) / "channelwatch-runtime"
        self.policy_path = self.runtime_dir / "update-policy.json"
        self.state_path = self.runtime_dir / "update-scheduler.json"
        self.legacy_state_lock_path = self.runtime_dir / "update-scheduler.lock"
        self.state_lock_path = self.runtime_dir / "update-scheduler-v2.lock"
        self.clock = clock
        self._lock = threading.RLock()

    def _remove_legacy_state_lock(self) -> None:
        """Remove only the exact safe marker used by pre-v1.0.2 releases.

        The v2 advisory lock is already held when this runs, and v1.0.2 never
        creates the legacy marker.  Runtime activation stops the previous UI
        before the new backend starts, so a remaining regular marker is an
        abandoned compatibility artifact rather than live lock ownership.
        """

        try:
            metadata = self.legacy_state_lock_path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise UpdateLockedError(
                "The legacy update scheduler lock cannot be inspected safely."
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise UpdateLockedError(
                "The legacy update scheduler lock is not a single-link regular file."
            )
        try:
            self.legacy_state_lock_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise UpdateLockedError(
                "The abandoned legacy update scheduler lock could not be cleared."
            ) from exc

    def _state_lock(self) -> UpdateSchedulerStateLock:
        return UpdateSchedulerStateLock(
            self.state_lock_path,
            wait_timeout=SCHEDULER_LOCK_WAIT_SECONDS,
        )

    def get(self) -> UpdatePolicy:
        with self._lock:
            raw = _safe_json_object(self.policy_path)
            if raw is None:
                policy = UpdatePolicy().validate()
                self.runtime_dir.mkdir(parents=True, exist_ok=True)
                _safe_atomic_json(self.policy_path, asdict(policy))
                return policy
            try:
                return UpdatePolicy.from_mapping(raw)
            except (TypeError, ValueError) as exc:
                raise UpdatePolicyStorageError(
                    "Update policy is invalid and was preserved for operator review."
                ) from exc

    def put(self, changes: UpdatePolicy | Mapping[str, Any]) -> UpdatePolicy:
        with self._lock:
            if isinstance(changes, UpdatePolicy):
                policy = changes.validate()
            else:
                current = asdict(self.get())
                current.update(dict(changes))
                policy = UpdatePolicy.from_mapping(current)
            _safe_atomic_json(self.policy_path, asdict(policy))
            return policy

    def _new_state(self) -> dict[str, Any]:
        policy = self.get()
        now = self.clock()
        jitter = secrets.randbelow(policy.maintenance_window_minutes)
        return {
            "schema": SCHEDULER_STATE_SCHEMA_VERSION,
            "created_at": format_timestamp(now),
            "last_check_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "last_error": None,
            "next_check_at": format_timestamp(now + FIRST_CHECK_DELAY),
            "postponed_until": None,
            "retry_count": 0,
            "stable_install_jitter_minutes": jitter,
            "quarantines": {},
            "rollback_holds": {},
            "observed_release_digests": {},
            "last_attempt": None,
            "last_install_attempt_id": None,
            "last_install_local_date": None,
            "scheduled_restart_at": None,
            "scheduled_release_version": None,
            "scheduled_release_sha256": None,
            "scheduled_attempt_id": None,
            "draft_postpone_used": {},
            "deferred_attempt_id": None,
            "notification_drain_retry_attempt_id": None,
            "maintenance_attention_code": None,
            "maintenance_attention_at": None,
            "maintenance_attention_message": None,
        }

    @staticmethod
    def _validate_state(raw: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(raw) - SCHEDULER_STATE_FIELDS
        if unknown:
            raise UpdatePolicyStorageError(
                "Update scheduler state contains unknown fields and was preserved."
            )
        if (
            type(raw.get("schema")) is not int
            or raw.get("schema") != SCHEDULER_STATE_SCHEMA_VERSION
        ):
            raise UpdatePolicyStorageError(
                "Update scheduler state schema is unsupported and was preserved."
            )
        jitter = raw.get("stable_install_jitter_minutes")
        retry_count = raw.get("retry_count", 0)
        if type(jitter) is not int or not 0 <= jitter < 12 * 60:
            raise UpdatePolicyStorageError(
                "Update scheduler jitter is invalid and was preserved."
            )
        if type(retry_count) is not int or retry_count < 0:
            raise UpdatePolicyStorageError(
                "Update scheduler retry state is invalid and was preserved."
            )
        for name in (
            "quarantines",
            "rollback_holds",
            "observed_release_digests",
            "draft_postpone_used",
        ):
            if not isinstance(raw.get(name, {}), dict):
                raise UpdatePolicyStorageError(
                    f"Update scheduler {name} is invalid and was preserved."
                )
            if len(raw.get(name, {})) > MAX_SCHEDULER_HISTORY_ITEMS:
                raise UpdatePolicyStorageError(
                    f"Update scheduler {name} is unbounded and was preserved."
                )
        for name in (
            "created_at",
            "last_check_at",
            "last_success_at",
            "last_failure_at",
            "next_check_at",
            "postponed_until",
            "scheduled_restart_at",
        ):
            value = raw.get(name)
            if (name == "created_at" and value is None) or (
                value is not None and parse_timestamp(value) is None
            ):
                raise UpdatePolicyStorageError(
                    f"Update scheduler {name} is invalid and was preserved."
                )
        for name in (
            "last_error",
            "last_install_attempt_id",
            "last_install_local_date",
            "scheduled_release_version",
            "scheduled_release_sha256",
            "scheduled_attempt_id",
            "deferred_attempt_id",
            "notification_drain_retry_attempt_id",
            "maintenance_attention_code",
            "maintenance_attention_message",
        ):
            if raw.get(name) is not None and not isinstance(raw.get(name), str):
                raise UpdatePolicyStorageError(
                    f"Update scheduler {name} is invalid and was preserved."
                )
        attention_at = raw.get("maintenance_attention_at")
        if attention_at is not None and parse_timestamp(attention_at) is None:
            raise UpdatePolicyStorageError(
                "Update scheduler maintenance attention time is invalid and was preserved."
            )
        attention_values = (
            raw.get("maintenance_attention_code"),
            raw.get("maintenance_attention_at"),
            raw.get("maintenance_attention_message"),
        )
        if any(value is not None for value in attention_values) and not all(
            isinstance(value, str) and value for value in attention_values
        ):
            raise UpdatePolicyStorageError(
                "Update scheduler maintenance attention state is incomplete and was preserved."
            )
        scheduled_values = (
            raw.get("scheduled_restart_at"),
            raw.get("scheduled_release_version"),
            raw.get("scheduled_release_sha256"),
            raw.get("scheduled_attempt_id"),
        )
        if any(value is not None for value in scheduled_values) and not all(
            isinstance(value, str) and value for value in scheduled_values
        ):
            raise UpdatePolicyStorageError(
                "Update scheduler restart countdown is incomplete and was preserved."
            )
        scheduled_digest = str(raw.get("scheduled_release_sha256") or "")
        if scheduled_digest and (
            len(scheduled_digest) != 64
            or any(char not in "0123456789abcdef" for char in scheduled_digest)
        ):
            raise UpdatePolicyStorageError(
                "Update scheduler release digest is invalid and was preserved."
            )
        last_attempt = raw.get("last_attempt")
        if last_attempt is not None and not isinstance(last_attempt, dict):
            raise UpdatePolicyStorageError(
                "Update scheduler last attempt is invalid and was preserved."
            )
        if isinstance(last_attempt, dict):
            unknown_attempt_fields = set(last_attempt) - ATTEMPT_FIELDS
            version = last_attempt.get("version")
            digest = last_attempt.get("bundle_sha256")
            attempted_at = last_attempt.get("attempted_at")
            attempt_id = last_attempt.get("attempt_id")
            job_id = last_attempt.get("job_id")
            phase = last_attempt.get("phase")
            automatic = last_attempt.get("automatic")
            clear_hold = last_attempt.get("clear_hold_on_success")
            if (
                unknown_attempt_fields
                or not isinstance(version, str)
                or not version.strip().lstrip("v")
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or parse_timestamp(attempted_at) is None
                or not isinstance(attempt_id, str)
                or not attempt_id
                or len(attempt_id) > 200
                or not isinstance(job_id, str)
                or not job_id
                or len(job_id) > 200
                or phase not in ATTEMPT_PHASES
                or type(automatic) is not bool
                or type(clear_hold) is not bool
            ):
                raise UpdatePolicyStorageError(
                    "Update scheduler last attempt identity is invalid and was preserved."
                )
            completed_at = last_attempt.get("completed_at")
            terminal_status = last_attempt.get("terminal_job_status")
            if phase in {"success", "failed", "interrupted"}:
                if (
                    parse_timestamp(completed_at) is None
                    or not isinstance(terminal_status, str)
                    or not terminal_status
                ):
                    raise UpdatePolicyStorageError(
                        "Update scheduler terminal attempt is incomplete and was preserved."
                    )
            elif completed_at is not None or terminal_status is not None:
                raise UpdatePolicyStorageError(
                    "Update scheduler pending attempt contains terminal state."
                )
            if last_attempt.get("failure_reason") is not None and not isinstance(
                last_attempt.get("failure_reason"), str
            ):
                raise UpdatePolicyStorageError(
                    "Update scheduler attempt failure reason is invalid."
                )
            if last_attempt.get("rollback_applied") is not None and type(
                last_attempt.get("rollback_applied")
            ) is not bool:
                raise UpdatePolicyStorageError(
                    "Update scheduler attempt rollback state is invalid."
                )
        for version, digest in raw.get("observed_release_digests", {}).items():
            if (
                not isinstance(version, str)
                or not isinstance(digest, str)
                or (
                    len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                )
            ):
                raise UpdatePolicyStorageError(
                    "Update scheduler observed digest is invalid and was preserved."
                )
        for collection_name in ("quarantines", "rollback_holds"):
            for identity, item in raw.get(collection_name, {}).items():
                if not isinstance(identity, str) or not isinstance(item, dict):
                    raise UpdatePolicyStorageError(
                        f"Update scheduler {collection_name} is invalid and was preserved."
                    )
                version = str(item.get("version") or "")
                digest = str(item.get("bundle_sha256") or "")
                if (
                    not version
                    or identity != f"{version}:{digest}"
                    or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                    or not isinstance(item.get("reason"), str)
                    or parse_timestamp(item.get("created_at")) is None
                ):
                    raise UpdatePolicyStorageError(
                        f"Update scheduler {collection_name} is invalid and was preserved."
                    )
        for identity, used_at in raw.get("draft_postpone_used", {}).items():
            if (
                not isinstance(identity, str)
                or not identity
                or parse_timestamp(used_at) is None
            ):
                raise UpdatePolicyStorageError(
                    "Update scheduler draft postpone history is invalid and was preserved."
                )
        return dict(raw)

    def _get_state_unlocked(self) -> dict[str, Any]:
        raw = _safe_json_object(self.state_path)
        if raw is None:
            state = self._new_state()
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            _safe_atomic_json(self.state_path, state)
            return state
        return self._validate_state(raw)

    def _write_state_unlocked(self, normalized: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(normalized)
        normalized["schema"] = SCHEDULER_STATE_SCHEMA_VERSION
        for name in ("quarantines", "rollback_holds"):
            values = dict(normalized.get(name, {}))
            if len(values) > MAX_SCHEDULER_HISTORY_ITEMS:
                ordered = sorted(
                    values.items(),
                    key=lambda item: str(
                        item[1].get("created_at")
                        if isinstance(item[1], dict)
                        else ""
                    ),
                )
                normalized[name] = dict(ordered[-MAX_SCHEDULER_HISTORY_ITEMS:])
        draft_history = dict(normalized.get("draft_postpone_used", {}))
        if len(draft_history) > MAX_SCHEDULER_HISTORY_ITEMS:
            normalized["draft_postpone_used"] = dict(
                sorted(draft_history.items(), key=lambda item: str(item[1]))[
                    -MAX_SCHEDULER_HISTORY_ITEMS:
                ]
            )
        observed = dict(normalized.get("observed_release_digests", {}))
        if len(observed) > MAX_SCHEDULER_HISTORY_ITEMS:
            try:
                ordered_versions = sorted(
                    observed,
                    key=lambda version: tuple(int(part) for part in version.split(".")),
                )
            except (TypeError, ValueError):
                ordered_versions = sorted(observed)
            keep = set(ordered_versions[-MAX_SCHEDULER_HISTORY_ITEMS:])
            normalized["observed_release_digests"] = {
                version: digest
                for version, digest in observed.items()
                if version in keep
            }
        normalized = self._validate_state(normalized)
        _safe_atomic_json(self.state_path, normalized)
        return normalized

    def get_state(self) -> dict[str, Any]:
        with self._lock, self._state_lock():
            self._remove_legacy_state_lock()
            return self._get_state_unlocked()

    def put_state(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock, self._state_lock():
            self._remove_legacy_state_lock()
            return self._write_state_unlocked(
                {**self._get_state_unlocked(), **dict(changes)}
            )

    def transform_state(
        self,
        transform: Callable[[dict[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Apply one cross-process read/modify/write scheduler transition."""

        with self._lock, self._state_lock():
            self._remove_legacy_state_lock()
            current = self._get_state_unlocked()
            return self._write_state_unlocked(transform(dict(current)))


def _resolve_local_datetime(naive: datetime, zone: tzinfo) -> datetime:
    """Choose the first repeated hour and advance through a DST gap."""

    for minute_shift in range(181):
        shifted = naive + timedelta(minutes=minute_shift)
        candidate = shifted.replace(tzinfo=zone, fold=0)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) == shifted:
            return candidate
    raise UpdatePolicyStorageError(
        "ChannelWatch could not resolve the maintenance window in its timezone."
    )


@dataclass(frozen=True)
class MaintenanceOpportunity:
    local_date: str
    window_start: datetime
    scheduled_at: datetime
    window_end: datetime
    attempt_id: str


def maintenance_opportunity(
    policy: UpdatePolicy,
    now: datetime,
    *,
    jitter_minutes: int,
    zone: tzinfo | None = None,
) -> MaintenanceOpportunity:
    zone = zone or channelwatch_timezone()
    local_now = now.astimezone(zone)
    start_time = _parse_window_start(policy.maintenance_window_start)

    def for_date(local_date: date) -> MaintenanceOpportunity:
        naive_start = datetime.combine(local_date, start_time)
        start = _resolve_local_datetime(naive_start, zone)
        scheduled = _resolve_local_datetime(
            naive_start + timedelta(minutes=jitter_minutes), zone
        )
        end = _resolve_local_datetime(
            naive_start + timedelta(minutes=policy.maintenance_window_minutes), zone
        )
        attempt_id = (
            f"{local_date.isoformat()}@"
            f"{format_timestamp(scheduled.astimezone(timezone.utc))}"
        )
        return MaintenanceOpportunity(
            local_date=local_date.isoformat(),
            window_start=start,
            scheduled_at=scheduled,
            window_end=end,
            attempt_id=attempt_id,
        )

    today = for_date(local_now.date())
    if now.astimezone(timezone.utc) < today.window_end.astimezone(timezone.utc):
        return today
    return for_date(local_now.date() + timedelta(days=1))


def release_identity(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    version = str(payload.get("version") or "").strip().lstrip("v")
    digest = str(payload.get("bundle_sha256") or "").strip().lower()
    return version, digest, f"{version}:{digest or 'image'}"


def record_failed_activation_quarantine(
    config_dir: Path,
    *,
    pending: Mapping[str, Any] | None,
    active: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    clock: Callable[[], datetime] = utc_now,
) -> bool:
    """Persist a failed activation identity even after rollback to an old image.

    Historical v0.9.10-v0.9.17 portals predate the automatic-update policy and
    therefore cannot create a scheduler attempt before selecting a v0.9.18
    bundle. The v0.9.18 launcher is the first code shared by both children, so
    it records the exact signed version/digest while it owns the activation
    outcome lock and before it returns control to the historical image.
    """

    pending = pending if isinstance(pending, Mapping) else {}
    active = active if isinstance(active, Mapping) else {}
    manifest = active.get("manifest")
    manifest = manifest if isinstance(manifest, Mapping) else {}
    version = str(
        pending.get("version") or active.get("version") or job.get("version") or ""
    ).strip().lstrip("v")
    digest = str(
        pending.get("bundle_sha256")
        or manifest.get("bundle_sha256")
        or job.get("bundle_sha256")
        or ""
    ).strip().lower()
    job_version = str(job.get("version") or "").strip().lstrip("v")
    rolled_back_from = str(job.get("rolled_back_from") or "").strip().lstrip(
        "v"
    )
    job_digest = str(job.get("bundle_sha256") or "").strip().lower()
    if (
        not version
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or job.get("operation") != "apply"
        or job.get("status") != "failed"
        or job.get("rollback_applied") is not True
        or job_version != version
        or rolled_back_from != version
        or job_digest != digest
        or not str(job.get("rolled_back_to") or "").strip()
    ):
        return False

    now = clock().astimezone(timezone.utc)
    completed_at = format_timestamp(now)
    job_id = str(pending.get("job_id") or job.get("job_id") or secrets.token_hex(16))
    attempt_id = str(
        pending.get("scheduler_attempt_id") or f"activation@{job_id}"
    )[:200]
    attempted_at = str(pending.get("started_at") or "")
    if parse_timestamp(attempted_at) is None:
        attempted_at = completed_at or format_timestamp(now)
    identity = f"{version}:{digest}"
    message = "Update activation failed and ChannelWatch rolled back safely."

    def transition(state: dict[str, Any]) -> Mapping[str, Any]:
        previous_attempt = state.get("last_attempt")
        matching_attempt = isinstance(previous_attempt, dict) and all(
            str(previous_attempt.get(field) or "") == expected
            for field, expected in (
                ("version", version),
                ("bundle_sha256", digest),
                ("job_id", job_id),
            )
        )
        if matching_attempt:
            attempt = dict(previous_attempt)
        else:
            attempt = {
                "version": version,
                "bundle_sha256": digest,
                "attempted_at": attempted_at,
                "attempt_id": attempt_id,
                "job_id": job_id,
                "automatic": False,
                "clear_hold_on_success": False,
            }
        attempt.update(
            {
                "phase": "failed",
                "completed_at": completed_at,
                "terminal_job_status": "failed",
                "failure_reason": message,
                "rollback_applied": True,
            }
        )
        quarantines = dict(state.get("quarantines") or {})
        quarantines[identity] = {
            "version": version,
            "bundle_sha256": digest,
            "reason": "activation_failed",
            "created_at": completed_at,
        }
        result = {
            **state,
            "quarantines": quarantines,
            "last_failure_at": completed_at,
            "last_error": message,
            "retry_count": 0,
            "next_check_at": format_timestamp(now + CHECK_CADENCE),
            "maintenance_attention_code": "update-activation-failed",
            "maintenance_attention_at": completed_at,
            "maintenance_attention_message": message,
            "scheduled_restart_at": None,
            "scheduled_release_version": None,
            "scheduled_release_sha256": None,
            "scheduled_attempt_id": None,
        }
        # A historical launcher can finish rollback while a separate, newer
        # scheduler operation owns ``last_attempt``. Preserve that attempt;
        # exact-digest quarantine is independent and must never steal or
        # rewrite another operation's identity.
        if matching_attempt or not isinstance(previous_attempt, dict):
            result["last_attempt"] = attempt
        else:
            result["maintenance_attention_code"] = (
                "update-activation-failed-concurrent-attempt"
            )
            result["maintenance_attention_message"] = (
                "A failed historical activation was quarantined while another "
                "update attempt remained authoritative."
            )
        return result

    UpdatePolicyStore(config_dir, clock=clock).transform_state(transition)
    return True


class UpdateAutomationService:
    """Single-instance scheduled checker/installer with durable decisions."""

    def __init__(
        self,
        *,
        config_dir: Path,
        manager_factory: Callable[[], UpdateManager],
        poll_seconds: float = 30.0,
        clock: Callable[[], datetime] = utc_now,
        timezone_provider: Callable[[], tzinfo] = channelwatch_timezone,
        maintenance_lock: (Callable[[], AbstractContextManager[Any]] | None) = None,
        install_preflight: (
            Callable[[Mapping[str, Any]], Mapping[str, Any]] | None
        ) = None,
        drain_notification_queue: Callable[[float], bool] | None = None,
        resume_notification_queue: Callable[[], Any] | None = None,
        recovery_state_provider: Callable[[], bool] | None = None,
        notification_drain_timeout: float = 20.0,
    ):
        self.config_dir = Path(config_dir)
        self.store = UpdatePolicyStore(config_dir, clock=clock)
        self.manager_factory = manager_factory
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.clock = clock
        self.timezone_provider = timezone_provider
        self.maintenance_lock = maintenance_lock
        self.install_preflight = install_preflight
        self.drain_notification_queue = drain_notification_queue
        self.resume_notification_queue = resume_notification_queue
        self.recovery_state_provider = recovery_state_provider
        self.notification_drain_timeout = max(0.1, notification_drain_timeout)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()

    def _recovery_mode(self) -> bool:
        if self.recovery_state_provider is None:
            return False
        try:
            return bool(self.recovery_state_provider())
        except (OSError, RuntimeError, ValueError):
            # A broken recovery-state probe must narrow selection to the
            # official recovery-compatible subset, never widen it.
            return True

    def get_policy(self) -> dict[str, Any]:
        return asdict(self.store.get())

    def get_policy_view(self) -> dict[str, Any]:
        """Return the API view without adding fields to the persisted policy."""

        policy = self.store.get()
        state = self.store.get_state()
        opportunity = self._opportunity(policy, state, self.clock())
        return {
            **asdict(policy),
            "postponed_until": state.get("postponed_until"),
            "last_check_at": state.get("last_check_at"),
            "next_check_at": state.get("next_check_at"),
            "next_attempt_at": format_timestamp(opportunity.scheduled_at),
            "last_error": (
                state.get("maintenance_attention_message")
                or state.get("last_error")
            ),
            "attention_required": bool(state.get("maintenance_attention_code")),
            "attention_code": state.get("maintenance_attention_code"),
            "attention_since": state.get("maintenance_attention_at"),
            "scheduled_restart_at": state.get("scheduled_restart_at"),
            "scheduled_release_version": state.get("scheduled_release_version"),
            "scheduled_release_sha256": state.get("scheduled_release_sha256"),
            "postpone_available": self._draft_postpone_available(state),
        }

    def put_policy(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        result = asdict(self.store.put(changes))
        if result["mode"] != "automatic":
            self._clear_scheduled_restart()
        self._wake.set()
        return result

    def status(self) -> dict[str, Any]:
        return {
            "policy": self.get_policy(),
            "scheduler": self.store.get_state(),
            "running": bool(self._thread and self._thread.is_alive()),
            "official_catalog_url": DEFAULT_UPDATE_CATALOG_URL,
        }

    @staticmethod
    def _scheduled_identity(state: Mapping[str, Any]) -> str | None:
        version = str(state.get("scheduled_release_version") or "")
        digest = str(state.get("scheduled_release_sha256") or "")
        attempt_id = str(state.get("scheduled_attempt_id") or "")
        if not version or not digest or not attempt_id:
            return None
        return f"{version}:{digest}:{attempt_id}"

    def _draft_postpone_available(self, state: Mapping[str, Any]) -> bool:
        identity = self._scheduled_identity(state)
        used = state.get("draft_postpone_used")
        return bool(identity and isinstance(used, dict) and identity not in used)

    def _clear_scheduled_restart(self) -> dict[str, Any]:
        return self.store.put_state(
            {
                "scheduled_restart_at": None,
                "scheduled_release_version": None,
                "scheduled_release_sha256": None,
                "scheduled_attempt_id": None,
            }
        )

    def postpone(
        self,
        *,
        until: datetime | None = None,
        minutes: int | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if until is not None and minutes is not None:
            raise ValueError("Specify either until or minutes, not both.")
        now = self.clock()
        state = self.store.get_state()
        if reason == "dirty_report_draft":
            if until is not None or minutes is not None:
                raise ValueError(
                    "Draft protection uses the fixed 24-hour postpone period."
                )
            identity = self._scheduled_identity(state)
            if not identity or not self._draft_postpone_available(state):
                raise ValueError(
                    "The draft postpone was already used or no restart is scheduled."
                )
            used = dict(state["draft_postpone_used"])
            used[identity] = format_timestamp(now)
            until = now + DRAFT_POSTPONE_DURATION
        elif reason not in {None, "administrator"}:
            raise ValueError("Unsupported update postpone reason.")
        if until is None:
            duration = 24 * 60 if minutes is None else int(minutes)
            if not 15 <= duration <= 30 * 24 * 60:
                raise ValueError(
                    "Postpone duration must be 15 minutes through 30 days."
                )
            until = now + timedelta(minutes=duration)
        if until.tzinfo is None or until <= now:
            raise ValueError("Postpone time must be a future timezone-aware time.")
        changes: dict[str, Any] = {
            "postponed_until": format_timestamp(until),
            "scheduled_restart_at": None,
            "scheduled_release_version": None,
            "scheduled_release_sha256": None,
            "scheduled_attempt_id": None,
        }
        if reason == "dirty_report_draft":
            changes["draft_postpone_used"] = used
        state = self.store.put_state(changes)
        self._wake.set()
        return state

    def retry_now(self) -> dict[str, Any]:
        state = self.store.put_state(
            {
                "next_check_at": format_timestamp(self.clock()),
                "retry_count": 0,
                "last_error": None,
                "deferred_attempt_id": None,
                "notification_drain_retry_attempt_id": None,
                "maintenance_attention_code": None,
                "maintenance_attention_at": None,
                "maintenance_attention_message": None,
            }
        )
        self._wake.set()
        return state

    @staticmethod
    def _pending_attempt(state: Mapping[str, Any]) -> dict[str, Any] | None:
        attempt = state.get("last_attempt")
        if not isinstance(attempt, dict):
            return None
        if str(attempt.get("phase") or "") not in PENDING_ATTEMPT_PHASES:
            return None
        return dict(attempt)

    def _record_attempt_terminal_error(
        self,
        *,
        error: Exception | str,
        now: datetime,
        job: Mapping[str, Any] | None = None,
    ) -> None:
        """Close only a scheduler attempt that was durably marked in progress."""

        state = self.store.get_state()
        attempt = self._pending_attempt(state)
        if attempt is None:
            return
        attempt.update(
            {
                "phase": "failed",
                "completed_at": format_timestamp(now),
                "terminal_job_status": (
                    str(job.get("status") or "failed") if job else "failed"
                ),
                "failure_reason": str(error)[:200],
            }
        )
        if job and job.get("job_id"):
            attempt["job_id"] = str(job["job_id"])
        self.store.put_state({"last_attempt": attempt})

    def _hold_ambiguous_attempt(
        self,
        *,
        attempt: Mapping[str, Any],
        now: datetime,
        message: str,
    ) -> dict[str, Any]:
        """Keep an uncertain activation fail-closed without misattribution."""

        return self.store.put_state(
            {
                "last_attempt": dict(attempt),
                "next_check_at": format_timestamp(now + ACTIVATION_RECONCILE_DELAY),
                "last_error": message,
                "maintenance_attention_code": "activation-outcome-ambiguous",
                "maintenance_attention_at": format_timestamp(now),
                "maintenance_attention_message": message,
                "scheduled_restart_at": None,
                "scheduled_release_version": None,
                "scheduled_release_sha256": None,
                "scheduled_attempt_id": None,
            }
        )

    @staticmethod
    def _job_matches_attempt(
        job: Mapping[str, Any], attempt: Mapping[str, Any]
    ) -> bool:
        version, digest, _identity = release_identity(attempt)
        return bool(
            str(attempt.get("job_id") or "")
            and str(job.get("job_id") or "") == str(attempt.get("job_id") or "")
            and str(job.get("operation") or "") == "apply"
            and str(job.get("version") or "").strip().lstrip("v") == version
            and str(job.get("bundle_sha256") or "").strip().lower() == digest
            and str(job.get("scheduler_attempt_id") or "")
            == str(attempt.get("attempt_id") or "")
        )

    def _reconcile_pending_attempt(
        self,
        *,
        manager: UpdateManager,
        state: Mapping[str, Any],
        now: datetime,
    ) -> str | None:
        """Consume the durable activation outcome before another check overwrites it."""

        attempt = self._pending_attempt(state)
        if attempt is None:
            return None
        version, digest, identity = release_identity(attempt)
        if not version or len(digest) != 64:
            self._hold_ambiguous_attempt(
                attempt=attempt,
                now=now,
                message=(
                    "The pending update identity is incomplete. Automatic updates "
                    "remain paused for administrator review."
                ),
            )
            return "activation-outcome-ambiguous"
        try:
            manager_status = manager.status()
        except (OSError, RuntimeError, ValueError, UpdateCenterError):
            self._hold_ambiguous_attempt(
                attempt=attempt,
                now=now,
                message=(
                    "The pending update outcome could not be read safely. Automatic "
                    "updates remain paused for administrator review."
                ),
            )
            return "activation-outcome-ambiguous"
        job = manager_status.get("last_job")
        if not isinstance(job, dict):
            self._hold_ambiguous_attempt(
                attempt=attempt,
                now=now,
                message=(
                    "The pending update has no durable activation result. Automatic "
                    "updates remain paused for administrator review."
                ),
            )
            return "activation-outcome-ambiguous"

        if not self._job_matches_attempt(job, attempt):
            self._hold_ambiguous_attempt(
                attempt=attempt,
                now=now,
                message=(
                    "A different Update Center job replaced the pending activation "
                    "result. Automatic updates remain paused for administrator review."
                ),
            )
            return "activation-outcome-ambiguous"

        job_status = str(job.get("status") or "").strip().lower()
        if job_status in PRE_HANDOFF_JOB_STATUSES:
            message = (
                "The previous update process stopped before activation handoff. "
                "The existing runtime remains selected and the update may be retried."
            )
            attempt.update(
                {
                    "phase": "interrupted",
                    "completed_at": format_timestamp(now),
                    "terminal_job_status": job_status,
                    "failure_reason": message,
                }
            )
            self.store.put_state({"last_attempt": attempt})
            self._record_failure(error=message, now=now, payload=attempt)
            return "apply-interrupted"
        if job_status in PENDING_JOB_STATUSES:
            attempt["phase"] = "activation_pending"
            self.store.put_state(
                {
                    "last_attempt": attempt,
                    "next_check_at": format_timestamp(
                        now + ACTIVATION_RECONCILE_DELAY
                    ),
                    "maintenance_attention_code": None,
                    "maintenance_attention_at": None,
                    "maintenance_attention_message": None,
                    "scheduled_restart_at": None,
                    "scheduled_release_version": None,
                    "scheduled_release_sha256": None,
                    "scheduled_attempt_id": None,
                }
            )
            return "activation-pending"

        completed_at = format_timestamp(now)
        attempt.update(
            {
                "completed_at": completed_at,
                "terminal_job_status": job_status,
            }
        )
        if job_status in SUCCESS_JOB_STATUSES:
            attempt["phase"] = "success"
            current = self.store.get_state()
            changes: dict[str, Any] = {
                "last_attempt": attempt,
                "last_success_at": completed_at,
                "last_error": None,
                "retry_count": 0,
                "next_check_at": format_timestamp(now + CHECK_CADENCE),
                "maintenance_attention_code": None,
                "maintenance_attention_at": None,
                "maintenance_attention_message": None,
                "scheduled_restart_at": None,
                "scheduled_release_version": None,
                "scheduled_release_sha256": None,
                "scheduled_attempt_id": None,
            }
            if attempt.get("clear_hold_on_success") is True:
                quarantines = dict(current["quarantines"])
                rollback_holds = dict(current["rollback_holds"])
                quarantines.pop(identity, None)
                rollback_holds.pop(identity, None)
                changes.update(
                    {
                        "quarantines": quarantines,
                        "rollback_holds": rollback_holds,
                    }
                )
            self.store.put_state(changes)
            return "activation-succeeded"

        if job_status in FAILED_JOB_STATUSES:
            message = str(
                job.get("error")
                or job.get("message")
                or "Update activation failed and was rolled back."
            )[:500]
            attempt.update(
                {
                    "phase": "failed",
                    "failure_reason": message[:200],
                    "rollback_applied": bool(job.get("rollback_applied")),
                }
            )
            current = self.store.get_state()
            quarantines = dict(current["quarantines"])
            quarantines[identity] = {
                "version": version,
                "bundle_sha256": digest,
                "reason": message[:200],
                "created_at": completed_at,
            }
            self.store.put_state(
                {
                    "last_attempt": attempt,
                    "quarantines": quarantines,
                    "last_failure_at": completed_at,
                    "last_error": message,
                    "retry_count": 0,
                    "next_check_at": format_timestamp(now + CHECK_CADENCE),
                    "maintenance_attention_code": "update-activation-failed",
                    "maintenance_attention_at": completed_at,
                    "maintenance_attention_message": message,
                    "scheduled_restart_at": None,
                    "scheduled_release_version": None,
                    "scheduled_release_sha256": None,
                    "scheduled_attempt_id": None,
                }
            )
            return "activation-failed"

        self._hold_ambiguous_attempt(
            attempt=attempt,
            now=now,
            message=(
                "The pending update has an unrecognized activation result. "
                "Automatic updates remain paused for administrator review."
            ),
        )
        return "activation-outcome-ambiguous"

    def _reconcile_apply_exception(
        self,
        *,
        manager: UpdateManager,
        error: Exception,
        now: datetime,
        payload: Mapping[str, Any] | None,
        quarantine: bool,
    ) -> str:
        """Preserve an activation handed off just before apply raised."""

        state = self.store.get_state()
        attempt = self._pending_attempt(state)
        if attempt is None:
            self._record_failure(
                error=error,
                now=now,
                payload=payload,
                quarantine=quarantine,
            )
            return "verification-failed" if quarantine else "retry-scheduled"
        try:
            manager_status = manager.status()
        except (OSError, RuntimeError, ValueError, UpdateCenterError):
            self._hold_ambiguous_attempt(
                attempt=attempt,
                now=now,
                message=(
                    "The update call failed after its durable outcome became "
                    "unreadable. Automatic updates remain paused for review."
                ),
            )
            return "activation-outcome-ambiguous"
        job = manager_status.get("last_job")
        if isinstance(job, dict) and self._job_matches_attempt(job, attempt):
            result = self._reconcile_pending_attempt(
                manager=manager,
                state=state,
                now=now,
            )
            if result == "apply-interrupted" and quarantine:
                self._record_failure(
                    error=error,
                    now=now,
                    payload=payload,
                    quarantine=True,
                )
            return result or "activation-outcome-ambiguous"

        # The caller-assigned job ID was never published, so no activation
        # handoff can later complete under this attempt identity.
        self._record_attempt_terminal_error(error=error, now=now)
        self._record_failure(
            error=error,
            now=now,
            payload=payload,
            quarantine=quarantine,
        )
        return "verification-failed" if quarantine else "retry-scheduled"

    def apply_release(self, *, version: str | None = None) -> dict[str, Any]:
        """Apply one exact signed release and retain its activation identity."""

        if not self._run_lock.acquire(blocking=False):
            raise UpdateLockedError("Another update operation is already in progress.")
        try:
            return self._apply_release_locked(version=version)
        finally:
            self._run_lock.release()

    def _apply_release_locked(self, *, version: str | None) -> dict[str, Any]:
        manager = self.manager_factory()
        recovery_mode = self._recovery_mode()
        checked = manager.check(recovery=recovery_mode)
        payload = checked.get("latest")
        if not isinstance(payload, dict):
            raise UpdateManifestError("Update apply found no signed release.")
        selected_version, selected_digest, identity = release_identity(payload)
        requested_version = str(version or selected_version).strip().lstrip("v")
        if requested_version != selected_version:
            raise UpdateManifestError(
                "The requested release is not the current signed selection."
            )
        if len(selected_digest) != 64:
            raise UpdateManifestError("The signed release digest is invalid.")
        if checked.get("update_available") is not True or checked.get(
            "image_required"
        ) is True:
            return manager.apply(selected_version, recovery=recovery_mode)

        state = self.store.get_state()
        if self._pending_attempt(state) is not None:
            raise UpdateLockedError(
                "A prior update activation must reach a durable outcome before applying."
            )
        if identity in state["quarantines"] or identity in state["rollback_holds"]:
            raise UpdateLockedError(
                "This exact release is held after a failure or rollback. Use the "
                "explicit Retry action to install it again."
            )

        now = self.clock()
        job_id = secrets.token_hex(16)
        attempt = {
            "version": selected_version,
            "bundle_sha256": selected_digest,
            "attempted_at": format_timestamp(now),
            "attempt_id": f"manual-apply@{format_timestamp(now)}#{job_id[:8]}",
            "job_id": job_id,
            "phase": "apply_started",
            "automatic": False,
            "clear_hold_on_success": False,
        }
        self.store.put_state({"last_attempt": attempt})
        try:
            result = manager.apply(
                selected_version,
                recovery=recovery_mode,
                job_id=job_id,
                scheduler_attempt_id=attempt["attempt_id"],
                expected_bundle_sha256=selected_digest,
            )
        except Exception as exc:
            self._reconcile_apply_exception(
                manager=manager,
                error=exc,
                now=now,
                payload=payload,
                quarantine=isinstance(exc, (UpdateManifestError, UpdateBundleError)),
            )
            raise

        if not self._job_matches_attempt(result, attempt):
            self._hold_ambiguous_attempt(
                attempt=attempt,
                now=now,
                message=(
                    "Update Center returned a different durable job identity. "
                    "The manual update remains paused for administrator review."
                ),
            )
            raise UpdateCenterError("Update apply returned an unexpected job identity.")

        result_status = str(result.get("status") or "").strip().lower()
        if result_status in PENDING_JOB_STATUSES:
            attempt["phase"] = "activation_pending"
            self.store.put_state(
                {
                    "last_attempt": attempt,
                    "next_check_at": format_timestamp(
                        now + ACTIVATION_RECONCILE_DELAY
                    ),
                }
            )
        elif result_status in SUCCESS_JOB_STATUSES:
            attempt.update(
                {
                    "phase": "success",
                    "completed_at": format_timestamp(now),
                    "terminal_job_status": result_status,
                }
            )
            self.store.put_state(
                {
                    "last_attempt": attempt,
                    "last_success_at": format_timestamp(now),
                    "last_error": None,
                }
            )
        elif result_status in FAILED_JOB_STATUSES:
            self._reconcile_pending_attempt(
                manager=manager,
                state=self.store.get_state(),
                now=now,
            )
        else:
            self._record_attempt_terminal_error(
                error=str(result.get("message") or result_status or "failed"),
                now=now,
                job=result,
            )
        return result

    def rollback_release(self) -> dict[str, Any]:
        """Roll back and persist the exact reinstall hold under one lock."""

        if not self._run_lock.acquire(blocking=False):
            raise UpdateLockedError("Another update operation is already in progress.")
        try:
            manager = self.manager_factory()
            before = manager.status()
            active = before.get("active_bundle")
            held_identity: tuple[str, str] | None = None
            hold_created = False
            if isinstance(active, dict):
                manifest = active.get("manifest")
                digest = (
                    str(manifest.get("bundle_sha256") or "")
                    if isinstance(manifest, dict)
                    else ""
                )
                version = str(active.get("version") or "")
                normalized_version, normalized_digest, identity = release_identity(
                    {"version": version, "bundle_sha256": digest}
                )
                if normalized_version and len(normalized_digest) == 64:
                    # Persist the hold before asking the launcher to switch. A
                    # crash after rollback begins must not make the rejected
                    # release eligible for the next automatic window.
                    if identity not in self.store.get_state()["rollback_holds"]:
                        self._record_rollback_hold_locked(
                            version=normalized_version,
                            bundle_sha256=normalized_digest,
                        )
                        hold_created = True
                    held_identity = (normalized_version, normalized_digest)
            result = manager.rollback()
            if (
                hold_created
                and held_identity is not None
                and result.get("status") not in {"restarting", "success"}
                and result.get("rollback_applied") is not True
            ):
                self.clear_release_hold(
                    version=held_identity[0],
                    bundle_sha256=held_identity[1],
                )
            return result
        finally:
            self._run_lock.release()

    def retry_release(self, *, version: str, bundle_sha256: str) -> dict[str, Any]:
        """Administrator retry that clears and applies one exact signed asset."""

        if not self._run_lock.acquire(blocking=False):
            raise UpdateLockedError("Another update operation is already in progress.")
        try:
            return self._retry_release_locked(
                version=version,
                bundle_sha256=bundle_sha256,
            )
        finally:
            self._run_lock.release()

    def _retry_release_locked(
        self, *, version: str, bundle_sha256: str
    ) -> dict[str, Any]:

        normalized_version, digest, _ = release_identity(
            {"version": version, "bundle_sha256": bundle_sha256}
        )
        if not normalized_version or len(digest) != 64:
            raise ValueError("Retry requires an exact version and SHA-256.")
        manager = self.manager_factory()
        recovery_mode = self._recovery_mode()
        checked = manager.check(recovery=recovery_mode)
        payload = checked.get("latest")
        if not isinstance(payload, dict):
            raise UpdateManifestError("Update retry found no signed release.")
        selected_version, selected_digest, _ = release_identity(payload)
        if (selected_version, selected_digest) != (normalized_version, digest):
            raise UpdateManifestError(
                "The signed release changed; review the new version and digest before retrying."
            )
        state = self.store.get_state()
        if self._pending_attempt(state) is not None:
            raise UpdateLockedError(
                "A prior update activation must reach a durable outcome before retrying."
            )
        now = self.clock()
        job_id = secrets.token_hex(16)
        attempt = {
            "version": normalized_version,
            "bundle_sha256": selected_digest,
            "attempted_at": format_timestamp(now),
            "attempt_id": f"manual-retry@{format_timestamp(now)}",
            "job_id": job_id,
            "phase": "apply_started",
            "automatic": False,
            "clear_hold_on_success": True,
        }
        self.store.put_state({"last_attempt": attempt})
        # apply() performs another signed catalog fetch and requires the same
        # cached version+digest, closing the check/apply change window.
        try:
            result = manager.apply(
                normalized_version,
                recovery=recovery_mode,
                job_id=job_id,
                scheduler_attempt_id=attempt["attempt_id"],
                expected_bundle_sha256=selected_digest,
            )
        except Exception as exc:
            outcome = self._reconcile_apply_exception(
                manager=manager,
                error=exc,
                now=now,
                payload=payload,
                quarantine=isinstance(exc, (UpdateManifestError, UpdateBundleError)),
            )
            if outcome in {"activation-pending", "activation-succeeded"}:
                durable = manager.status().get("last_job")
                if isinstance(durable, dict):
                    return durable
            raise
        result_status = str(result.get("status") or "").lower()
        if str(result.get("job_id") or "") != job_id or not self._job_matches_attempt(
            result, attempt
        ):
            self._hold_ambiguous_attempt(
                attempt=attempt,
                now=now,
                message=(
                    "Update Center returned a different durable job identity. "
                    "The retry remains paused for administrator review."
                ),
            )
            raise UpdateCenterError("Update retry returned an unexpected job identity.")
        if result_status in PENDING_JOB_STATUSES:
            attempt["phase"] = "activation_pending"
            self.store.put_state(
                {
                    "last_attempt": attempt,
                    "next_check_at": format_timestamp(
                        now + ACTIVATION_RECONCILE_DELAY
                    ),
                }
            )
        elif result_status in SUCCESS_JOB_STATUSES:
            attempt.update(
                {
                    "phase": "success",
                    "completed_at": format_timestamp(now),
                    "terminal_job_status": result_status,
                }
            )
            self.store.put_state(
                {
                    "last_attempt": attempt,
                    "last_success_at": format_timestamp(now),
                    "last_error": None,
                }
            )
            self.clear_release_hold(
                version=normalized_version, bundle_sha256=selected_digest
            )
        else:
            self._record_attempt_terminal_error(error=result_status or "failed", now=now)
        return result

    def record_rollback_hold(
        self, *, version: str, bundle_sha256: str, reason: str = "manual_rollback"
    ) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            raise UpdateLockedError("Another update operation is already in progress.")
        try:
            return self._record_rollback_hold_locked(
                version=version,
                bundle_sha256=bundle_sha256,
                reason=reason,
            )
        finally:
            self._run_lock.release()

    def _record_rollback_hold_locked(
        self, *, version: str, bundle_sha256: str, reason: str = "manual_rollback"
    ) -> dict[str, Any]:
        state = self.store.get_state()
        normalized_version, digest, identity = release_identity(
            {"version": version, "bundle_sha256": bundle_sha256}
        )
        if not normalized_version or len(digest) != 64:
            raise ValueError("Rollback holds require an exact version and SHA-256.")
        holds = dict(state["rollback_holds"])
        holds[identity] = {
            "version": normalized_version,
            "bundle_sha256": digest,
            "reason": reason[:200],
            "created_at": format_timestamp(self.clock()),
        }
        changes: dict[str, Any] = {"rollback_holds": holds}
        if (
            state.get("scheduled_release_version") == normalized_version
            and state.get("scheduled_release_sha256") == digest
        ):
            changes.update(
                {
                    "scheduled_restart_at": None,
                    "scheduled_release_version": None,
                    "scheduled_release_sha256": None,
                    "scheduled_attempt_id": None,
                }
            )
        return self.store.put_state(changes)

    def clear_release_hold(self, *, version: str, bundle_sha256: str) -> dict[str, Any]:
        state = self.store.get_state()
        _, _, identity = release_identity(
            {"version": version, "bundle_sha256": bundle_sha256}
        )
        quarantines = dict(state["quarantines"])
        rollback_holds = dict(state["rollback_holds"])
        quarantines.pop(identity, None)
        rollback_holds.pop(identity, None)
        return self.store.put_state(
            {"quarantines": quarantines, "rollback_holds": rollback_holds}
        )

    def _observe_release(
        self, payload: Mapping[str, Any], state: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        version, digest, identity = release_identity(payload)
        observed = dict(state["observed_release_digests"])
        previous_digest = str(observed.get(version) or "")
        if previous_digest and previous_digest != digest:
            quarantines = dict(state["quarantines"])
            quarantines[identity] = {
                "version": version,
                "bundle_sha256": digest,
                "reason": "published_release_digest_changed",
                "created_at": format_timestamp(self.clock()),
            }
            return self.store.put_state({"quarantines": quarantines}), identity
        if version and digest and not previous_digest:
            observed[version] = digest
            state = self.store.put_state({"observed_release_digests": observed})
        return state, None

    def _opportunity(
        self, policy: UpdatePolicy, state: Mapping[str, Any], now: datetime
    ) -> MaintenanceOpportunity:
        jitter = int(state["stable_install_jitter_minutes"])
        jitter %= policy.maintenance_window_minutes
        return maintenance_opportunity(
            policy,
            now,
            jitter_minutes=jitter,
            zone=self.timezone_provider(),
        )

    def _eligible(
        self,
        payload: Mapping[str, Any],
        *,
        policy: UpdatePolicy,
        state: dict[str, Any],
        now: datetime,
    ) -> tuple[bool, str, MaintenanceOpportunity]:
        version, _digest, identity = release_identity(payload)
        opportunity = self._opportunity(policy, state, now)
        local_now = now.astimezone(self.timezone_provider())
        now_utc = now.astimezone(timezone.utc)
        if policy.mode != "automatic":
            return False, "notify-only", opportunity
        if payload.get("automatic_install_allowed") is not True:
            return False, "release-disallows-automatic-install", opportunity
        allowed_after = parse_timestamp(payload.get("automatic_install_after"))
        if allowed_after is None or now < allowed_after:
            return False, "automatic-install-delay-active", opportunity
        if str(payload.get("revocation_state") or "active") != "active":
            return False, "release-revoked", opportunity
        if str(payload.get("delivery_mode") or "") == DeliveryMode.IMAGE_REQUIRED.value:
            return False, "container-image-required", opportunity
        if identity in state["quarantines"]:
            return False, "release-quarantined", opportunity
        if identity in state["rollback_holds"]:
            return False, "manual-rollback-hold", opportunity
        postponed_until = parse_timestamp(state.get("postponed_until"))
        if postponed_until and now < postponed_until:
            return False, "postponed", opportunity
        if state.get("last_install_local_date") == local_now.date().isoformat():
            return False, "daily-install-limit", opportunity
        if state.get("last_install_attempt_id") == opportunity.attempt_id:
            return False, "maintenance-attempt-already-used", opportunity
        if state.get("deferred_attempt_id") == opportunity.attempt_id:
            return False, "maintenance-attempt-deferred", opportunity
        if not (
            opportunity.scheduled_at.astimezone(timezone.utc)
            <= now_utc
            < opportunity.window_end.astimezone(timezone.utc)
        ):
            return False, "outside-maintenance-window", opportunity
        if not version:
            return False, "release-identity-missing", opportunity
        return True, "eligible", opportunity

    def _record_failure(
        self,
        *,
        error: Exception | str,
        now: datetime,
        payload: Mapping[str, Any] | None = None,
        quarantine: bool = False,
    ) -> dict[str, Any]:
        state = self.store.get_state()
        retry_count = int(state.get("retry_count") or 0) + 1
        if retry_count <= len(RETRY_DELAYS):
            next_check = now + RETRY_DELAYS[retry_count - 1]
        else:
            # The current opportunity's jittered scheduled time is necessarily
            # in the past once an attempt has already failed inside its window.
            # Asking for that timestamp again would make the scheduler retry on
            # every poll until the window closes. Advance beyond this window so
            # the exhausted 15-minute/one-hour/six-hour sequence resumes at the
            # next daily opportunity instead.
            current_opportunity = self._opportunity(self.store.get(), state, now)
            next_check = self._opportunity(
                self.store.get(),
                state,
                current_opportunity.window_end.astimezone(timezone.utc)
                + timedelta(seconds=1),
            ).scheduled_at
        changes: dict[str, Any] = {
            "last_failure_at": format_timestamp(now),
            "last_error": str(error)[:500],
            "retry_count": retry_count,
            "next_check_at": format_timestamp(next_check),
        }
        if quarantine and payload:
            version, digest, identity = release_identity(payload)
            quarantines = dict(state["quarantines"])
            quarantines[identity] = {
                "version": version,
                "bundle_sha256": digest,
                "reason": str(error)[:200],
                "created_at": format_timestamp(now),
            }
            changes["quarantines"] = quarantines
        return self.store.put_state(changes)

    def _preflight(self, payload: Mapping[str, Any]) -> tuple[bool, str]:
        if self.install_preflight is None:
            return True, "ready"
        result = self.install_preflight(payload)
        if result.get("free_space_ok") is not True:
            return False, "insufficient-free-space"
        if result.get("private_backup_ok") is not True:
            return False, "private-backup-unavailable"
        if result.get("maintenance_transactions_ok") is not True:
            return False, "unresolved-maintenance-transaction"
        return True, "ready"

    def _repeats_failed_recovery_release(
        self, payload: Mapping[str, Any]
    ) -> bool:
        """Exclude the exact asset that forced protocol-3 image recovery."""

        marker = load_json(
            self.config_dir / "channelwatch-runtime" / "official-recovery-mode.json",
            None,
        )
        if not isinstance(marker, dict):
            return False
        failed_version = str(marker.get("failed_version") or "").lstrip("v")
        failed_digest = str(marker.get("failed_bundle_sha256") or "").strip().lower()
        version, digest, _identity = release_identity(payload)
        return bool(
            failed_version
            and failed_version == version
            and (not failed_digest or failed_digest == digest)
        )

    def _defer_maintenance_attempt(
        self,
        *,
        policy: UpdatePolicy,
        state: Mapping[str, Any],
        now: datetime,
        opportunity: MaintenanceOpportunity,
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        """Consume this window and retain an operator-visible reason."""

        next_opportunity = self._opportunity(
            policy,
            state,
            opportunity.window_end.astimezone(timezone.utc)
            + timedelta(seconds=1),
        )
        return self.store.put_state(
            {
                "last_failure_at": format_timestamp(now),
                "last_error": message,
                "retry_count": 0,
                "next_check_at": format_timestamp(
                    next_opportunity.scheduled_at.astimezone(timezone.utc)
                ),
                "deferred_attempt_id": opportunity.attempt_id,
                "notification_drain_retry_attempt_id": None,
                "maintenance_attention_code": reason,
                "maintenance_attention_at": format_timestamp(now),
                "maintenance_attention_message": message,
                "scheduled_restart_at": None,
                "scheduled_release_version": None,
                "scheduled_release_sha256": None,
                "scheduled_attempt_id": None,
            }
        )

    def run_once(self, *, force_check: bool = False) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {"status": "busy", **self.status()}
        try:
            now = self.clock()
            policy = self.store.get()
            state = self.store.get_state()
            manager = self.manager_factory()
            reconciliation = self._reconcile_pending_attempt(
                manager=manager,
                state=state,
                now=now,
            )
            if reconciliation is not None:
                return {"status": reconciliation, **self.status()}
            next_check = parse_timestamp(state.get("next_check_at"))
            if not force_check and next_check and now < next_check:
                return {"status": "waiting", **self.status()}

            recovery_mode = self._recovery_mode()
            payload: Mapping[str, Any] | None = None
            try:
                checked = manager.check(recovery=recovery_mode)
                payload = checked.get("latest")
                if not isinstance(payload, dict):
                    raise UpdateManifestError(
                        "Update check returned no signed release."
                    )
                state = self.store.put_state(
                    {
                        "last_check_at": format_timestamp(now),
                        "last_error": None,
                        "retry_count": 0,
                        "next_check_at": format_timestamp(now + CHECK_CADENCE),
                    }
                )
                if recovery_mode and self._repeats_failed_recovery_release(payload):
                    self._clear_scheduled_restart()
                    self.store.put_state(
                        {
                            "last_error": (
                                "Automatic recovery is waiting for a newer signed "
                                "release than the asset that entered recovery mode."
                            ),
                            "next_check_at": format_timestamp(now + CHECK_CADENCE),
                        }
                    )
                    return {
                        "status": "recovery-waiting-newer-release",
                        **self.status(),
                    }
                state, digest_conflict = self._observe_release(payload, state)
                if digest_conflict:
                    self._clear_scheduled_restart()
                    return {"status": "quarantined", **self.status()}
                if (
                    checked.get("image_required") is True
                    and bool(checked.get("update_available"))
                ):
                    self.store.put_state(
                        {
                            "next_check_at": format_timestamp(now + CHECK_CADENCE),
                            "scheduled_restart_at": None,
                            "scheduled_release_version": None,
                            "scheduled_release_sha256": None,
                            "scheduled_attempt_id": None,
                            "maintenance_attention_code": "container-image-required",
                            "maintenance_attention_at": format_timestamp(now),
                            "maintenance_attention_message": (
                                "This signed release requires a compatible container "
                                "image. Pull and recreate ChannelWatch while preserving "
                                "/config. Automatic app installation was not attempted."
                            ),
                        }
                    )
                    return {
                        "status": "container-image-required",
                        "release": dict(payload),
                        **self.status(),
                    }
                if not bool(checked.get("update_available")):
                    self.store.put_state(
                        {
                            "last_success_at": format_timestamp(now),
                            "scheduled_restart_at": None,
                            "scheduled_release_version": None,
                            "scheduled_release_sha256": None,
                            "scheduled_attempt_id": None,
                        }
                    )
                    return {"status": "current", **self.status()}

                eligible, reason, opportunity = self._eligible(
                    payload, policy=policy, state=state, now=now
                )
                if not eligible:
                    if reason == "outside-maintenance-window":
                        cadence = now + CHECK_CADENCE
                        next_check = min(
                            cadence,
                            opportunity.scheduled_at.astimezone(timezone.utc),
                        )
                        self.store.put_state(
                            {
                                "next_check_at": format_timestamp(next_check),
                                "scheduled_restart_at": None,
                                "scheduled_release_version": None,
                                "scheduled_release_sha256": None,
                                "scheduled_attempt_id": None,
                            }
                        )
                    else:
                        self._clear_scheduled_restart()
                    return {"status": reason, "release": dict(payload), **self.status()}

                version, digest, _release_key = release_identity(payload)
                scheduled_at = parse_timestamp(state.get("scheduled_restart_at"))
                countdown_matches = (
                    state.get("scheduled_release_version") == version
                    and state.get("scheduled_release_sha256") == digest
                    and state.get("scheduled_attempt_id") == opportunity.attempt_id
                    and scheduled_at is not None
                )
                if not countdown_matches:
                    scheduled_at = now + AUTOMATIC_RESTART_GRACE
                    if scheduled_at.astimezone(
                        timezone.utc
                    ) >= opportunity.window_end.astimezone(timezone.utc):
                        self.store.put_state(
                            {
                                "next_check_at": format_timestamp(
                                    opportunity.window_end.astimezone(timezone.utc)
                                ),
                                "scheduled_restart_at": None,
                                "scheduled_release_version": None,
                                "scheduled_release_sha256": None,
                                "scheduled_attempt_id": None,
                            }
                        )
                        return {
                            "status": "maintenance-window-too-short",
                            **self.status(),
                        }
                    self.store.put_state(
                        {
                            "scheduled_restart_at": format_timestamp(scheduled_at),
                            "scheduled_release_version": version,
                            "scheduled_release_sha256": digest,
                            "scheduled_attempt_id": opportunity.attempt_id,
                            "next_check_at": format_timestamp(scheduled_at),
                        }
                    )
                    return {"status": "restart-countdown", **self.status()}
                if scheduled_at is not None and now < scheduled_at:
                    self.store.put_state(
                        {"next_check_at": format_timestamp(scheduled_at)}
                    )
                    return {"status": "restart-countdown", **self.status()}

                if manager.runtime_transition_pending():
                    self.store.put_state(
                        {"next_check_at": format_timestamp(now + RETRY_DELAYS[0])}
                    )
                    return {"status": "runtime-transition-pending", **self.status()}
                preflight_ok, preflight_reason = self._preflight(payload)
                if not preflight_ok:
                    preflight_messages = {
                        "insufficient-free-space": (
                            "Automatic update deferred until the next maintenance "
                            "window because /config does not have enough free space."
                        ),
                        "private-backup-unavailable": (
                            "Automatic update deferred until the next maintenance "
                            "window because a private pre-update backup cannot be created."
                        ),
                        "unresolved-maintenance-transaction": (
                            "Automatic update deferred until the next maintenance "
                            "window because protected configuration maintenance is unresolved."
                        ),
                    }
                    self._defer_maintenance_attempt(
                        policy=policy,
                        state=state,
                        now=now,
                        opportunity=opportunity,
                        reason=preflight_reason,
                        message=preflight_messages[preflight_reason],
                    )
                    return {"status": preflight_reason, **self.status()}
                drain_ready = True
                if self.drain_notification_queue is not None:
                    try:
                        drain_ready = bool(
                            self.drain_notification_queue(
                                self.notification_drain_timeout
                            )
                        )
                    except (OSError, RuntimeError, TimeoutError, ValueError):
                        drain_ready = False
                if not drain_ready:
                    retry_at = now + RETRY_DELAYS[0]
                    first_failure = (
                        state.get("notification_drain_retry_attempt_id")
                        != opportunity.attempt_id
                    )
                    if first_failure and retry_at.astimezone(
                        timezone.utc
                    ) < opportunity.window_end.astimezone(timezone.utc):
                        self.store.put_state(
                            {
                                "last_failure_at": format_timestamp(now),
                                "last_error": (
                                    "Automatic update is waiting for the notification "
                                    "queue to drain; one retry is scheduled in this window."
                                ),
                                "notification_drain_retry_attempt_id": (
                                    opportunity.attempt_id
                                ),
                                "next_check_at": format_timestamp(retry_at),
                            }
                        )
                        return {
                            "status": "notification-queue-busy",
                            **self.status(),
                        }
                    self._defer_maintenance_attempt(
                        policy=policy,
                        state=state,
                        now=now,
                        opportunity=opportunity,
                        reason="notification-queue-busy",
                        message=(
                            "Automatic update deferred until the next maintenance "
                            "window because the core notification queue did not drain."
                        ),
                    )
                    return {
                        "status": "notification-queue-deferred",
                        **self.status(),
                    }

                drain_held = self.drain_notification_queue is not None
                job_id = secrets.token_hex(16)
                scheduler_attempt = {
                    "version": version,
                    "bundle_sha256": digest,
                    "attempted_at": format_timestamp(now),
                    "attempt_id": opportunity.attempt_id,
                    "job_id": job_id,
                    "phase": "apply_started",
                    "automatic": True,
                    "clear_hold_on_success": False,
                }
                try:
                    maintenance_context = (
                        self.maintenance_lock()
                        if self.maintenance_lock is not None
                        else nullcontext()
                    )
                    with maintenance_context:
                        if manager.runtime_transition_pending():
                            raise UpdateLockedError(
                                "A runtime transition started before automatic apply."
                            )
                        self.store.put_state(
                            {"last_attempt": scheduler_attempt}
                        )
                        job = manager.apply(
                            str(payload.get("version") or ""),
                            recovery=recovery_mode,
                            maintenance_lock_held=self.maintenance_lock is not None,
                            job_id=job_id,
                            scheduler_attempt_id=opportunity.attempt_id,
                            expected_bundle_sha256=digest,
                        )
                finally:
                    if drain_held and self.resume_notification_queue is not None:
                        try:
                            self.resume_notification_queue()
                        except (OSError, RuntimeError, ValueError):
                            # The core-side drain request has its own bounded
                            # lease, so an IPC cleanup error cannot strand queue
                            # acceptance indefinitely or mask the apply result.
                            pass
                job_status = str(job.get("status") or "").lower()
                if not self._job_matches_attempt(job, scheduler_attempt):
                    self._hold_ambiguous_attempt(
                        attempt=scheduler_attempt,
                        now=now,
                        message=(
                            "Update Center returned a different durable job identity. "
                            "Automatic updates remain paused for administrator review."
                        ),
                    )
                    return {
                        "status": "activation-outcome-ambiguous",
                        **self.status(),
                    }
                if job_status not in PENDING_JOB_STATUSES | SUCCESS_JOB_STATUSES:
                    self._record_attempt_terminal_error(
                        error=str(job.get("message") or "Update apply failed."),
                        now=now,
                        job=job,
                    )
                    self._record_failure(
                        error=str(job.get("message") or "Update apply failed."),
                        now=now,
                        payload=payload,
                        quarantine=True,
                    )
                    return {"status": "apply-failed", "job": job, **self.status()}
                state = self.store.get_state()
                last_attempt = dict(state.get("last_attempt") or {})
                if job.get("job_id"):
                    last_attempt["job_id"] = str(job["job_id"])
                last_attempt["phase"] = (
                    "activation_pending"
                    if job_status in PENDING_JOB_STATUSES
                    else "success"
                )
                changes: dict[str, Any] = {
                    "last_attempt": last_attempt,
                    "last_install_attempt_id": opportunity.attempt_id,
                    "last_install_local_date": opportunity.local_date,
                    "last_error": None,
                    "deferred_attempt_id": None,
                    "notification_drain_retry_attempt_id": None,
                    "maintenance_attention_code": None,
                    "maintenance_attention_at": None,
                    "maintenance_attention_message": None,
                    "scheduled_restart_at": None,
                    "scheduled_release_version": None,
                    "scheduled_release_sha256": None,
                    "scheduled_attempt_id": None,
                }
                if job_status in SUCCESS_JOB_STATUSES:
                    last_attempt.update(
                        {
                            "completed_at": format_timestamp(now),
                            "terminal_job_status": job_status,
                        }
                    )
                    changes["last_success_at"] = format_timestamp(now)
                    changes["next_check_at"] = format_timestamp(now + CHECK_CADENCE)
                else:
                    # Restart acceptance is not activation success. Reconcile
                    # the durable Update Center job before any later check can
                    # overwrite it.
                    changes["next_check_at"] = format_timestamp(
                        now + ACTIVATION_RECONCILE_DELAY
                    )
                self.store.put_state(changes)
                return {"status": "install-started", "job": job, **self.status()}
            except (UpdateManifestError, UpdateBundleError) as exc:
                outcome = self._reconcile_apply_exception(
                    manager=manager,
                    error=exc,
                    now=now,
                    payload=payload,
                    quarantine=payload is not None,
                )
                return {"status": outcome, **self.status()}
            except (OSError, UpdateCenterError) as exc:
                outcome = self._reconcile_apply_exception(
                    manager=manager,
                    error=exc,
                    now=now,
                    payload=payload or {},
                    quarantine=False,
                )
                return {"status": outcome, **self.status()}
        finally:
            self._run_lock.release()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except (OSError, UpdateCenterError, ValueError):
                pass
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="channelwatch-update-scheduler",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> bool:
        thread = self._thread
        if thread is None:
            return True
        self._stop.set()
        self._wake.set()
        thread.join(max(0.0, timeout))
        return not thread.is_alive()


def _public_recovery_status(status: Mapping[str, Any]) -> dict[str, Any]:
    latest = status.get("latest")
    public_latest = None
    if isinstance(latest, dict):
        public_latest = {
            key: latest.get(key)
            for key in (
                "version",
                "version_tag",
                "delivery_mode",
                "image_required",
                "image_refresh_recommended",
                "recommended_image_version",
                "highlights",
            )
        }
    return {
        "current_version": status.get("current_version"),
        "update_available": bool(status.get("update_available")),
        "image_required": bool(status.get("image_required")),
        "latest": public_latest,
    }


class OfficialRecoveryUpdateService:
    """Fail-safe updater restricted to the compiled-in official signed feed."""

    def __init__(
        self,
        *,
        config_dir: Path,
        current_version: str,
        runtime_abi: str,
        settings_schema_version: int,
        image_version: str | None = None,
        launcher_protocol: int | None = None,
        fetcher: Callable[[str, int], bytes] | None = None,
        backup_callable: Callable[[Path], bytes] | None = None,
        restart_callable: Callable[[], bool] | None = None,
        healthcheck_callable: Callable[[], bool] | None = None,
        maintenance_lock: Callable[[], AbstractContextManager[Any]] | None = None,
    ):
        self.manager = UpdateManager(
            config_dir=config_dir,
            current_version=current_version,
            runtime_abi=runtime_abi,
            settings_schema_version=settings_schema_version,
            public_keys=UPDATE_PUBLIC_KEYS,
            manifest_url=DEFAULT_UPDATE_CATALOG_URL,
            image_version=image_version,
            launcher_protocol=launcher_protocol,
            fetcher=fetcher,
            backup_callable=backup_callable,
            restart_callable=restart_callable,
            healthcheck_callable=healthcheck_callable,
            maintenance_lock=maintenance_lock,
        )

    def _repeats_failed_release(self, status: Mapping[str, Any]) -> bool:
        marker = load_json(
            self.manager.runtime_dir / "official-recovery-mode.json", None
        )
        latest = status.get("latest")
        if not isinstance(marker, dict) or not isinstance(latest, dict):
            return False
        failed_version = str(marker.get("failed_version") or "").lstrip("v")
        failed_digest = str(marker.get("failed_bundle_sha256") or "").strip().lower()
        release_version = str(latest.get("version") or "").lstrip("v")
        release_digest = str(latest.get("bundle_sha256") or "").strip().lower()
        return bool(
            failed_version
            and failed_version == release_version
            and (not failed_digest or failed_digest == release_digest)
        )

    def _public_status(self, status: Mapping[str, Any]) -> dict[str, Any]:
        public = _public_recovery_status(status)
        repeats_failure = self._repeats_failed_release(status)
        if repeats_failure:
            public.update(
                {
                    "latest": None,
                    "update_available": False,
                    "recovery_waiting_for_newer_release": True,
                }
            )
        else:
            public["recovery_waiting_for_newer_release"] = False
        return public

    def status(self) -> dict[str, Any]:
        cached = self.manager.status()
        repeats_failure = self._repeats_failed_release(cached)
        # A cached ordinary Update Center selection has not been rechecked
        # under recovery-only eligibility. Public GET therefore advertises no
        # target until the explicit recovery check verifies the signed catalog.
        cached = {
            **cached,
            "latest": None,
            "update_available": False,
            "image_required": False,
        }
        return {
            **_public_recovery_status(cached),
            "recovery_waiting_for_newer_release": repeats_failure,
            "mode": "official-signed-recovery",
        }

    def check(self) -> dict[str, Any]:
        return {
            **self._public_status(self.manager.check(recovery=True)),
            "mode": "official-signed-recovery",
        }

    def apply(self, version: str | None = None) -> dict[str, Any]:
        if (
            version is not None
            and compare_versions(version, self.manager.current_version) <= 0
        ):
            raise UpdateManifestError("Recovery updates cannot downgrade ChannelWatch.")
        checked = self.manager.check(recovery=True)
        if self._repeats_failed_release(checked):
            raise UpdateManifestError(
                "The newest official update is the same release that entered "
                "recovery mode. Wait for a newer signed release before retrying."
            )
        result = self.manager.apply(version, recovery=True)
        return {
            key: result.get(key)
            for key in (
                "job_id",
                "operation",
                "status",
                "version",
                "message",
                "updated_at",
            )
        }
