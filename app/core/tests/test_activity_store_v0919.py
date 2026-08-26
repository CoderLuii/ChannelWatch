import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from core.storage.activity_store import (
    activity_storage_status,
    clear_activity_storage,
    delete_dvr_activity,
    load_recovery_events,
    merge_recovery_journal_into_database,
    persist_activity_event,
    reconcile_activity_history,
)


def _event(event_id: str, dvr_id: str = "dvr-a", kind: str = "watching_channel"):
    return {
        "id": event_id,
        "dvr_id": dvr_id,
        "type": kind,
        "title": "Redacted activity",
        "message": "Redacted message",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _rows(config_dir):
    with sqlite3.connect(config_dir / "channelwatch.db") as connection:
        return connection.execute(
            "SELECT id, dvr_id, event_type FROM activity_event ORDER BY id"
        ).fetchall()


def test_reconcile_imports_json_and_resets_active_journal(tmp_path):
    source = [_event("event-a"), _event("event-b", "dvr-b", "watching_vod")]
    (tmp_path / "activity_history.json").write_text(json.dumps(source))

    result = reconcile_activity_history(tmp_path)

    assert result == {"total": 2, "inserted": 2, "skipped": 0, "errors": 0}
    assert _rows(tmp_path) == [
        ("event-a", "dvr-a", "watching_channel"),
        ("event-b", "dvr-b", "watching_vod"),
    ]
    assert json.loads((tmp_path / "activity_history.json").read_text()) == []
    archives = list(tmp_path.glob("activity_history.json.migrated-v0919-*"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text()) == source
    assert archives[0].stat().st_mode & 0o777 == 0o600


def test_reconcile_is_idempotent_for_existing_uuid(tmp_path):
    event = _event("same-id")
    assert persist_activity_event(event, config_dir=tmp_path)
    (tmp_path / "activity_history.json").write_text(json.dumps([event]))

    result = reconcile_activity_history(tmp_path)

    assert result["inserted"] == 0
    assert result["skipped"] == 1
    assert _rows(tmp_path) == [("same-id", "dvr-a", "watching_channel")]


def test_sqlite_failure_journals_event_and_reports_degraded(tmp_path):
    event = _event("journal-only")
    with patch(
        "core.storage.activity_store._open_engine",
        side_effect=OperationalError("write", {}, RuntimeError("locked")),
    ):
        assert persist_activity_event(event, config_dir=tmp_path)

    assert load_recovery_events(tmp_path) == [event]
    status = activity_storage_status()
    assert status["status"] == "degraded"
    assert status["pending_recovery_events"] == 1


def test_recovery_event_reconciles_after_sqlite_recovers(tmp_path):
    event = _event("recover-me")
    with patch(
        "core.storage.activity_store._open_engine",
        side_effect=OperationalError("write", {}, RuntimeError("locked")),
    ):
        assert persist_activity_event(event, config_dir=tmp_path)

    assert reconcile_activity_history(tmp_path)["inserted"] == 1
    assert _rows(tmp_path) == [("recover-me", "dvr-a", "watching_channel")]
    assert load_recovery_events(tmp_path) == []


def test_delete_dvr_removes_database_and_active_journal_rows(tmp_path):
    assert persist_activity_event(_event("db-a"), config_dir=tmp_path)
    assert persist_activity_event(_event("db-b", "dvr-b"), config_dir=tmp_path)
    journal = [_event("journal-a"), _event("journal-b", "dvr-b")]
    (tmp_path / "activity_history.json").write_text(json.dumps(journal))

    assert delete_dvr_activity("dvr-a", config_dir=tmp_path) == 2

    assert _rows(tmp_path) == [("db-b", "dvr-b", "watching_channel")]
    assert [row["id"] for row in load_recovery_events(tmp_path)] == ["journal-b"]


def test_clear_removes_database_and_active_journal_rows(tmp_path):
    assert persist_activity_event(_event("db-row"), config_dir=tmp_path)
    (tmp_path / "activity_history.json").write_text(
        json.dumps([_event("journal-row")])
    )

    clear_activity_storage(tmp_path)

    assert _rows(tmp_path) == []
    assert load_recovery_events(tmp_path) == []


def test_backup_snapshot_merge_contains_pending_journal_without_mutating_source(
    tmp_path,
):
    live = tmp_path / "live"
    live.mkdir()
    snapshot = tmp_path / "snapshot.db"
    event = _event("pending-backup")
    journal = live / "activity_history.json"
    journal.write_text(json.dumps([event]))

    result = merge_recovery_journal_into_database(snapshot, journal)

    assert result["inserted"] == 1
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT id FROM activity_event").fetchall() == [
            ("pending-backup",)
        ]
    assert json.loads(journal.read_text()) == [event]


def test_invalid_event_is_not_reported_as_persisted(tmp_path):
    assert not persist_activity_event({"id": "missing-type"}, config_dir=tmp_path)
    assert not (tmp_path / "channelwatch.db").exists()
