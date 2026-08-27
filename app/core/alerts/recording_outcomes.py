"""Durable, DVR-scoped recording outcome reconciliation.

The event stream remains the fastest source for recording transitions.  This
module complements it with a small read-only job snapshot so explicit failures
and skips are not lost and a scheduled job can be declared missed only after
two reachable-DVR confirmations.
"""

from __future__ import annotations

import hashlib
import json
import copy
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from core.helpers.atomic_io import (
    atomic_write_private_json,
    read_regular_file_bytes,
)

STATE_SCHEMA = 1
MISSED_GRACE_SECONDS = 180
NEGATIVE_CONFIRMATION_SECONDS = 30
TERMINAL_RETENTION_SECONDS = 14 * 24 * 60 * 60
MAX_STATE_BYTES = 4 * 1024 * 1024

# Higher numbers win when Channels exposes more than one terminal signal.  A
# completed payload that also contains an error must never replace the failed
# outcome, even when the lower-priority event arrives later.
OUTCOME_PRIORITY = {
    "missed": 1,
    "completed": 2,
    "cancelled": 3,
    "interrupted": 3,
    "skipped": 4,
    "failed": 5,
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return False


def _value(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def _nonempty_error(payload: dict[str, Any]) -> bool:
    value = _value(payload, "error", "Error")
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, False, 0, {}, [])


def classify_recording_payload(
    payload: dict[str, Any],
    *,
    previously_started: bool = False,
    completion_event: bool = False,
) -> str | None:
    """Return one terminal outcome using the documented precedence."""

    failed = _truthy(_value(payload, "failed", "Failed"))
    dead = _truthy(_value(payload, "dead", "Dead"))
    skipped = _truthy(_value(payload, "skipped", "Skipped"))
    cancelled = _truthy(_value(payload, "cancelled", "Cancelled"))
    completed = _truthy(_value(payload, "completed", "Completed"))
    processed = _truthy(_value(payload, "processed", "Processed"))

    if failed or dead or _nonempty_error(payload):
        return "failed"
    if skipped:
        return "skipped"
    if previously_started and cancelled and not completed:
        return "interrupted"
    if cancelled:
        return "cancelled"
    if completed or processed:
        return "completed"
    if completion_event:
        # A recorded-* completion event with a usable recording payload is a
        # successful terminal result unless a higher-precedence flag says
        # otherwise.  This matches historical Channels payloads that expose
        # Processed but omit Completed.
        return "completed"
    return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def _job_identifier(job: dict[str, Any]) -> str:
    return str(
        _value(job, "id", "ID", "job_id", "JobID", "JobId", "jobId") or ""
    ).strip()


def _recording_job_identifier(recording: dict[str, Any]) -> str:
    identifier = _value(recording, "job_id", "JobID", "JobId", "jobId")
    if identifier:
        return str(identifier).strip()
    job = _value(recording, "job", "Job")
    if isinstance(job, dict):
        return _job_identifier(job)
    return ""


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    raw_channels = _value(job, "channels", "Channels")
    channels = raw_channels if isinstance(raw_channels, list) else []
    raw_item = _value(job, "item", "Item")
    item = raw_item if isinstance(raw_item, dict) else {}
    return {
        "name": str(_value(job, "name", "Name") or "Unknown recording")[:500],
        "start_time": _number(_value(job, "start_time", "StartTime")),
        "end_time": _number(_value(job, "end_time", "EndTime")),
        "duration": _number(_value(job, "duration", "Duration")),
        "channel": str(channels[0])[:100] if channels else "",
        "image_url": str(_value(item, "image_url", "ImageURL") or "")[:2000],
    }


@dataclass(frozen=True)
class RecordingOutcome:
    job_id: str
    outcome: str
    snapshot: dict[str, Any]


class RecordingOutcomeTracker:
    """Persist the minimum state needed to deduplicate recording outcomes."""

    def __init__(
        self,
        *,
        config_dir: Path,
        dvr_id: str,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config_dir = Path(config_dir)
        self.dvr_id = str(dvr_id or "default")
        self.now = now
        digest = hashlib.sha256(self.dvr_id.encode("utf-8")).hexdigest()[:20]
        self.path = (
            self.config_dir
            / "channelwatch-runtime"
            / f"recording-outcomes-{digest}.json"
        )
        self._lock = threading.RLock()
        self._load_blocked = False
        self._state = self._load()

    def _empty(self) -> dict[str, Any]:
        return {"schema": STATE_SCHEMA, "dvr_id": self.dvr_id, "jobs": {}}

    def _load(self) -> dict[str, Any]:
        try:
            raw = read_regular_file_bytes(self.path, max_bytes=MAX_STATE_BYTES)
            payload = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return self._empty()
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            # Do not overwrite an unsafe or malformed record automatically.
            # The caller will report persistence degradation if a subsequent
            # state transition cannot be written safely.
            self._load_blocked = True
            return self._empty()
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != STATE_SCHEMA
            or payload.get("dvr_id") != self.dvr_id
            or not isinstance(payload.get("jobs"), dict)
        ):
            self._load_blocked = True
            return self._empty()
        return payload

    def _save(self) -> None:
        if self._load_blocked:
            raise RuntimeError(
                "Existing recording outcome state needs recovery before it can be replaced."
            )
        atomic_write_private_json(self.path, self._state, sort_keys=True)

    def _entry(self, job_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        jobs = self._state["jobs"]
        entry = jobs.setdefault(
            job_id,
            {
                "snapshot": snapshot,
                "first_seen_at": self.now(),
                "last_seen_at": self.now(),
                "started": False,
                "terminal_outcome": None,
                "terminal_at": None,
                "missing_confirmations": 0,
                "last_negative_at": None,
            },
        )
        previous_start = _number(entry.get("snapshot", {}).get("start_time"))
        next_start = _number(snapshot.get("start_time"))
        if previous_start and next_start and previous_start != next_start:
            # Channels can reuse a job ID after a reschedule or after retention
            # cleanup.  A changed start time is a new lifecycle and must not
            # inherit a start or terminal result from the previous one.
            entry.update(
                {
                    "first_seen_at": self.now(),
                    "started": False,
                    "terminal_outcome": None,
                    "terminal_at": None,
                    "missing_confirmations": 0,
                    "last_negative_at": None,
                }
            )
        entry["snapshot"] = snapshot
        entry["last_seen_at"] = self.now()
        return entry

    def observe_scheduled(self, job: dict[str, Any]) -> None:
        job_id = _job_identifier(job)
        if not job_id:
            return
        with self._lock:
            before = copy.deepcopy(self._state)
            try:
                self._entry(job_id, _job_snapshot(job))
                self._save()
            except Exception:
                self._state = before
                raise

    def observe_started(self, job: dict[str, Any]) -> None:
        job_id = _job_identifier(job)
        if not job_id:
            return
        with self._lock:
            before = copy.deepcopy(self._state)
            try:
                entry = self._entry(job_id, _job_snapshot(job))
                entry["started"] = True
                entry["missing_confirmations"] = 0
                entry["last_negative_at"] = None
                self._save()
            except Exception:
                self._state = before
                raise

    @staticmethod
    def _set_terminal(entry: dict[str, Any], outcome: str, now: float) -> bool:
        if outcome not in OUTCOME_PRIORITY:
            raise ValueError(f"Unsupported recording outcome: {outcome}")
        current = entry.get("terminal_outcome")
        # Classification resolves simultaneous DVR flags using precedence
        # before this method is called. Once a terminal result is durably
        # claimed, never publish a contradictory second result for the same
        # lifecycle. Reused IDs are reset by _entry() when the start changes.
        if current in OUTCOME_PRIORITY:
            return False
        entry["terminal_outcome"] = outcome
        entry["terminal_at"] = now
        entry["missing_confirmations"] = 0
        entry["last_negative_at"] = None
        return True

    def mark_terminal(self, job_id: str, outcome: str) -> bool:
        """Atomically claim a terminal result.

        ``True`` means the caller owns publication of this result.  ``False``
        means another event path already claimed a terminal result for this
        lifecycle, so activity and notification delivery must not be duplicated.
        """

        identifier = str(job_id or "").strip()
        if not identifier:
            return False
        with self._lock:
            before = copy.deepcopy(self._state)
            entry = self._state["jobs"].setdefault(
                identifier,
                {
                    "snapshot": {},
                    "first_seen_at": self.now(),
                    "last_seen_at": self.now(),
                    "started": False,
                    "missing_confirmations": 0,
                    "last_negative_at": None,
                },
            )
            claimed = self._set_terminal(entry, outcome, self.now())
            if not claimed:
                return False
            try:
                self._save()
            except Exception:
                self._state = before
                raise
            return True

    def started_jobs_missing(self, jobs: Iterable[dict[str, Any]]) -> bool:
        """Return whether a fresh recordings lookup is needed.

        A missing active job alone is not enough to infer interruption.  The
        caller performs a separate fresh completed-recordings read only when
        this method reports that one is needed.
        """

        observed = {
            identifier
            for job in jobs
            if isinstance(job, dict)
            and (identifier := _job_identifier(job))
        }
        with self._lock:
            return any(
                isinstance(entry, dict)
                and entry.get("started")
                and not entry.get("terminal_outcome")
                and job_id not in observed
                for job_id, entry in self._state["jobs"].items()
            )

    def was_started(self, job_id: str) -> bool:
        """Return whether this lifecycle was durably observed as started."""

        identifier = str(job_id or "").strip()
        if not identifier:
            return False
        with self._lock:
            entry = self._state.get("jobs", {}).get(identifier)
            return bool(isinstance(entry, dict) and entry.get("started"))

    def reconcile(
        self,
        jobs: Iterable[dict[str, Any]],
        *,
        reachable: bool = True,
        recordings: Iterable[dict[str, Any]] | None = None,
    ) -> list[RecordingOutcome]:
        if not reachable:
            return []
        now = self.now()
        outcomes: list[RecordingOutcome] = []
        observed: set[str] = set()
        changed = False
        recordings_by_job: dict[str, dict[str, Any]] = {}
        if recordings is not None:
            recordings_by_job = {
                identifier: recording
                for recording in recordings
                if isinstance(recording, dict)
                and (identifier := _recording_job_identifier(recording))
            }

        with self._lock:
            before = copy.deepcopy(self._state)
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                job_id = _job_identifier(job)
                if not job_id:
                    continue
                observed.add(job_id)
                entry = self._entry(job_id, _job_snapshot(job))
                if entry.get("missing_confirmations") or entry.get("last_negative_at"):
                    entry["missing_confirmations"] = 0
                    entry["last_negative_at"] = None
                    changed = True

                outcome = classify_recording_payload(
                    job, previously_started=bool(entry.get("started"))
                )
                if outcome and self._set_terminal(entry, outcome, now):
                    changed = True
                    outcomes.append(
                        RecordingOutcome(job_id, outcome, dict(entry["snapshot"]))
                    )

            for job_id, entry in list(self._state["jobs"].items()):
                if not isinstance(entry, dict):
                    del self._state["jobs"][job_id]
                    changed = True
                    continue
                terminal_at = _number(entry.get("terminal_at"))
                if entry.get("terminal_outcome"):
                    if terminal_at and now - terminal_at > TERMINAL_RETENTION_SECONDS:
                        del self._state["jobs"][job_id]
                        changed = True
                    continue
                if job_id in observed:
                    continue

                snapshot = entry.get("snapshot")
                if not isinstance(snapshot, dict):
                    continue

                if entry.get("started"):
                    # Interruption requires a second, successful DVR read.  A
                    # missing jobs response is never treated as proof.  If a
                    # completed recording exists, its explicit flags decide
                    # the result and prevent successful short recordings from
                    # being mislabeled as interrupted.
                    if recordings is None:
                        continue
                    recording = recordings_by_job.get(job_id)
                    if recording is not None:
                        outcome = classify_recording_payload(
                            recording,
                            previously_started=True,
                            completion_event=True,
                        )
                        if outcome and self._set_terminal(entry, outcome, now):
                            changed = True
                            outcomes.append(
                                RecordingOutcome(
                                    job_id, outcome, dict(entry["snapshot"])
                                )
                            )
                        continue

                    last_negative = _number(entry.get("last_negative_at"))
                    confirmations = int(entry.get("missing_confirmations") or 0)
                    if confirmations and now - last_negative < NEGATIVE_CONFIRMATION_SECONDS:
                        continue
                    confirmations += 1
                    entry["missing_confirmations"] = confirmations
                    entry["last_negative_at"] = now
                    changed = True
                    if confirmations >= 2 and self._set_terminal(
                        entry, "interrupted", now
                    ):
                        outcomes.append(
                            RecordingOutcome(
                                job_id, "interrupted", dict(entry["snapshot"])
                            )
                        )
                    continue

                start_time = _number(snapshot.get("start_time"))
                if not start_time or now < start_time + MISSED_GRACE_SECONDS:
                    continue
                last_negative = _number(entry.get("last_negative_at"))
                confirmations = int(entry.get("missing_confirmations") or 0)
                if confirmations and now - last_negative < NEGATIVE_CONFIRMATION_SECONDS:
                    continue
                confirmations += 1
                entry["missing_confirmations"] = confirmations
                entry["last_negative_at"] = now
                changed = True
                if confirmations >= 2 and self._set_terminal(entry, "missed", now):
                    outcomes.append(
                        RecordingOutcome(job_id, "missed", dict(snapshot))
                    )

            if changed:
                try:
                    self._save()
                except Exception:
                    self._state = before
                    raise
        return outcomes
