import stat
from pathlib import Path

import pytest

from core.alerts.recording_outcomes import (
    MISSED_GRACE_SECONDS,
    NEGATIVE_CONFIRMATION_SECONDS,
    RecordingOutcomeTracker,
    classify_recording_payload,
)


@pytest.mark.parametrize(
    ("payload", "started", "completion_event", "expected"),
    [
        ({"Failed": True, "Skipped": True, "Completed": True}, False, True, "failed"),
        ({"Dead": True, "Completed": True}, False, True, "failed"),
        ({"Error": "disk write failed", "Completed": True}, False, True, "failed"),
        ({"Skipped": True, "Completed": True}, False, True, "skipped"),
        ({"Cancelled": True}, True, True, "interrupted"),
        ({"Cancelled": True}, False, True, "cancelled"),
        ({"Completed": True}, True, True, "completed"),
        ({"Processed": True}, True, True, "completed"),
        ({}, False, True, "completed"),
        ({}, False, False, None),
    ],
)
def test_recording_outcome_precedence(
    payload, started, completion_event, expected
):
    assert (
        classify_recording_payload(
            payload,
            previously_started=started,
            completion_event=completion_event,
        )
        == expected
    )


def _job(job_id: str, start_time: float, **extra):
    return {
        "id": job_id,
        "name": "Synthetic recording",
        "start_time": start_time,
        "end_time": start_time + 1800,
        "duration": 1800,
        "channels": ["1.1"],
        **extra,
    }


def test_missed_requires_two_reachable_confirmations_at_least_30_seconds_apart(
    tmp_path: Path,
):
    clock = [1_000_000.0]
    tracker = RecordingOutcomeTracker(
        config_dir=tmp_path, dvr_id="dvr-a", now=lambda: clock[0]
    )
    start = clock[0] - MISSED_GRACE_SECONDS - 1
    tracker.observe_scheduled(_job("job-a", start))

    assert tracker.reconcile([], reachable=False) == []
    first = tracker.reconcile([], reachable=True)
    assert first == []
    clock[0] += NEGATIVE_CONFIRMATION_SECONDS - 1
    assert tracker.reconcile([], reachable=True) == []
    clock[0] += 1

    outcomes = tracker.reconcile([], reachable=True)

    assert [(item.job_id, item.outcome) for item in outcomes] == [
        ("job-a", "missed")
    ]
    assert tracker.reconcile([], reachable=True) == []


def test_started_job_is_never_inferred_missed_from_disappearance(tmp_path: Path):
    clock = [2_000_000.0]
    tracker = RecordingOutcomeTracker(
        config_dir=tmp_path, dvr_id="dvr-a", now=lambda: clock[0]
    )
    job = _job("job-a", clock[0] - MISSED_GRACE_SECONDS - 10)
    tracker.observe_scheduled(job)
    tracker.observe_started(job)
    clock[0] += NEGATIVE_CONFIRMATION_SECONDS * 3

    assert tracker.reconcile([], reachable=True) == []


def test_started_job_requires_successful_recording_lookups_before_interruption(
    tmp_path: Path,
):
    clock = [2_100_000.0]
    tracker = RecordingOutcomeTracker(
        config_dir=tmp_path, dvr_id="dvr-a", now=lambda: clock[0]
    )
    job = _job("job-a", clock[0] - 60)
    tracker.observe_started(job)

    assert tracker.started_jobs_missing([]) is True
    assert tracker.reconcile([], recordings=None) == []
    clock[0] += NEGATIVE_CONFIRMATION_SECONDS
    assert tracker.reconcile([], recordings=[]) == []
    clock[0] += NEGATIVE_CONFIRMATION_SECONDS

    outcomes = tracker.reconcile([], recordings=[])

    assert [(item.job_id, item.outcome) for item in outcomes] == [
        ("job-a", "interrupted")
    ]
    assert tracker.reconcile([], recordings=[]) == []


@pytest.mark.parametrize(
    ("recording", "expected"),
    [
        ({"job_id": "job-a", "Processed": True}, "completed"),
        ({"JobID": "job-a", "Failed": True, "Completed": True}, "failed"),
        ({"JobId": "job-a", "Processed": True}, "completed"),
        ({"job": {"id": "job-a"}, "Skipped": True}, "skipped"),
    ],
)
def test_completed_recording_snapshot_prevents_false_interruption(
    tmp_path: Path, recording: dict, expected: str
):
    tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    tracker.observe_started(_job("job-a", 1))

    outcomes = tracker.reconcile([], recordings=[recording])

    assert [(item.job_id, item.outcome) for item in outcomes] == [
        ("job-a", expected)
    ]


def test_explicit_outcomes_are_deduplicated_across_restart(tmp_path: Path):
    tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    failed_job = _job("job-a", 1, failed=True)

    first = tracker.reconcile([failed_job])
    restarted = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    second = restarted.reconcile([failed_job])

    assert [item.outcome for item in first] == ["failed"]
    assert second == []
    assert stat.S_IMODE(restarted.path.stat().st_mode) == 0o600


def test_started_state_survives_restart_for_terminal_classification(tmp_path: Path):
    tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    tracker.observe_started(_job("job-a", 1))

    restarted = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")

    assert restarted.was_started("job-a") is True
    assert restarted.was_started("missing-job") is False
    assert classify_recording_payload(
        {"Cancelled": True},
        previously_started=restarted.was_started("job-a"),
        completion_event=True,
    ) == "interrupted"


def test_terminal_claim_deduplicates_paths_and_preserves_precedence(tmp_path: Path):
    tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")

    assert tracker.mark_terminal("job-a", "failed") is True
    assert tracker.mark_terminal("job-a", "failed") is False
    assert tracker.mark_terminal("job-a", "completed") is False

    restarted = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    assert restarted.mark_terminal("job-a", "completed") is False


def test_terminal_claim_never_emits_a_contradictory_second_outcome(tmp_path: Path):
    tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")

    assert tracker.mark_terminal("job-a", "completed") is True
    assert tracker.mark_terminal("job-a", "failed") is False
    assert tracker.mark_terminal("job-a", "skipped") is False

    state = tracker.path.read_text(encoding="utf-8")
    assert '"terminal_outcome": "completed"' in state


def test_rescheduled_job_restarts_negative_confirmation(tmp_path: Path):
    clock = [3_000_000.0]
    tracker = RecordingOutcomeTracker(
        config_dir=tmp_path, dvr_id="dvr-a", now=lambda: clock[0]
    )
    old = _job("job-a", clock[0] - MISSED_GRACE_SECONDS - 1)
    tracker.observe_scheduled(old)
    assert tracker.reconcile([]) == []

    future = _job("job-a", clock[0] + 3600)
    assert tracker.reconcile([future]) == []
    assert tracker.reconcile([]) == []


def test_rescheduled_reused_job_id_starts_a_new_terminal_lifecycle(tmp_path: Path):
    tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    original = _job("job-a", 100)
    tracker.observe_started(original)
    assert tracker.mark_terminal("job-a", "completed") is True

    replacement = _job("job-a", 200)
    tracker.observe_scheduled(replacement)

    assert tracker.mark_terminal("job-a", "failed") is True


def test_malformed_state_is_preserved_and_blocks_replacement(tmp_path: Path):
    # Resolve the deterministic filename through one tracker rather than
    # coupling the test to a hand-computed digest.
    tracker = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    state_path = tracker.path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    original = b"{not-json"
    state_path.write_bytes(original)
    blocked = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")

    with pytest.raises(RuntimeError, match="needs recovery"):
        blocked.observe_scheduled(_job("job-a", 10))

    assert state_path.read_bytes() == original


def test_state_is_scoped_by_dvr_identity(tmp_path: Path):
    first = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-a")
    second = RecordingOutcomeTracker(config_dir=tmp_path, dvr_id="dvr-b")
    first.reconcile([_job("shared-job", 1, skipped=True)])

    result = second.reconcile([_job("shared-job", 1, skipped=True)])

    assert [item.outcome for item in result] == ["skipped"]
    assert first.path != second.path
