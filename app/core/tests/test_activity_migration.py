import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import select

from core.storage import (
    ActivityEvent,
    create_db_engine,
    get_session,
    migrate_activity_history,
)


def _make_row(
    row_id: str | None = None,
    activity_type: str = "watching_channel",
    dvr_id: str = "dvr_test01",
    **overrides,
) -> dict:
    base = {
        "id": row_id or str(uuid.uuid4()),
        "type": activity_type,
        "title": "Test Event",
        "message": "Watching ESPN on Fire Stick",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "icon": "tv",
        "channel_name": "ESPN",
        "channel_number": "206",
        "device_name": "Fire Stick",
        "device_ip": "192.168.1.50",
        "program_title": "",
        "image_url": "",
        "stream_source": "",
        "dvr_id": dvr_id,
        "dvr_name": "Living Room DVR",
        "extra": {},
    }
    base.update(overrides)
    return base


def _write_json(path: Path, rows: list) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def json_path(tmp_path):
    return str(tmp_path / "activity_history.json")


class TestMigrationMissingSource:
    def test_no_json_file_returns_zeroed_result(self, db_url, tmp_path):
        result = migrate_activity_history(
            json_path=str(tmp_path / "nonexistent.json"),
            db_url=db_url,
        )
        assert result == {"total": 0, "inserted": 0, "skipped": 0, "errors": 0}

    def test_empty_json_array_returns_zeroed_result(self, db_url, json_path):
        Path(json_path).write_text("[]", encoding="utf-8")
        result = migrate_activity_history(json_path=json_path, db_url=db_url)
        assert result == {"total": 0, "inserted": 0, "skipped": 0, "errors": 0}

    def test_invalid_json_counts_as_error(self, db_url, json_path):
        Path(json_path).write_text("{not valid json", encoding="utf-8")
        result = migrate_activity_history(json_path=json_path, db_url=db_url)
        assert result["errors"] == 1
        assert result["inserted"] == 0

    def test_non_array_json_counts_as_error(self, db_url, json_path):
        Path(json_path).write_text('{"type": "object"}', encoding="utf-8")
        result = migrate_activity_history(json_path=json_path, db_url=db_url)
        assert result["errors"] == 1
        assert result["inserted"] == 0


class TestBasicMigration:
    def test_single_row_inserted(self, db_url, json_path):
        row = _make_row()
        _write_json(Path(json_path), [row])
        result = migrate_activity_history(json_path=json_path, db_url=db_url)

        assert result["total"] == 1
        assert result["inserted"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == 0

    def test_row_present_in_database(self, db_url, json_path):
        row = _make_row(row_id="fixed-id-001")
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == "fixed-id-001")
            ).one()
        engine.dispose()

        assert evt.id == "fixed-id-001"
        assert evt.event_type == "watching_channel"

    def test_missing_id_counts_as_error(self, db_url, json_path):
        row = _make_row()
        row.pop("id")
        _write_json(Path(json_path), [row])
        result = migrate_activity_history(json_path=json_path, db_url=db_url)
        assert result["errors"] == 1
        assert result["inserted"] == 0

    def test_missing_type_counts_as_error(self, db_url, json_path):
        row = _make_row()
        row.pop("type")
        _write_json(Path(json_path), [row])
        result = migrate_activity_history(json_path=json_path, db_url=db_url)
        assert result["errors"] == 1

    def test_non_dict_entry_counts_as_error(self, db_url, json_path):
        _write_json(Path(json_path), [_make_row(), "not-a-dict", None])
        result = migrate_activity_history(json_path=json_path, db_url=db_url)
        assert result["total"] == 3
        assert result["inserted"] == 1
        assert result["errors"] == 2


class TestFieldFidelity:
    def test_is_test_true_preserved(self, db_url, json_path):
        row = _make_row(
            activity_type="disk_alert",
            is_test=True,
            extra={"path": "/dev/sda1"},
        )
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.is_test is True
        assert evt.event_type == "disk_alert"
        assert json.loads(evt.extra) == {"path": "/dev/sda1"}

    def test_is_test_false_by_default(self, db_url, json_path):
        row = _make_row()
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.is_test is False

    def test_dvr_id_preserved(self, db_url, json_path):
        row = _make_row(dvr_id="dvr_ab12cd34")
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.dvr_id == "dvr_ab12cd34"

    def test_extra_dict_serialised_to_json_string(self, db_url, json_path):
        extra_payload = {"severity": "warning", "percent_free": 8.5, "nested": {"k": 1}}
        row = _make_row(activity_type="disk_alert", extra=extra_payload)
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert json.loads(evt.extra) == extra_payload

    def test_timestamp_utc_preserved(self, db_url, json_path):
        ts = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        row = _make_row(timestamp=ts.isoformat())
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        stored = evt.timestamp
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert stored == ts

    def test_all_string_fields_preserved(self, db_url, json_path):
        row = _make_row(
            channel_name="HBO",
            channel_number="301",
            device_name="Apple TV",
            device_ip="10.0.0.5",
            program_title="Succession S4E1",
            image_url="https://example.com/img.png",
            stream_source="linear",
            dvr_name="Bedroom DVR",
        )
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.channel_name == "HBO"
        assert evt.channel_number == "301"
        assert evt.device_name == "Apple TV"
        assert evt.device_ip == "10.0.0.5"
        assert evt.program_title == "Succession S4E1"
        assert evt.image_url == "https://example.com/img.png"
        assert evt.stream_source == "linear"
        assert evt.dvr_name == "Bedroom DVR"

    def test_type_field_mapped_to_event_type(self, db_url, json_path):
        row = _make_row(activity_type="recording_event")
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.event_type == "recording_event"

    def test_event_type_key_also_accepted(self, db_url, json_path):
        row = _make_row()
        row["event_type"] = "watching_vod"
        row.pop("type")
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.event_type == "watching_vod"

    def test_extra_string_passthrough_when_valid_json(self, db_url, json_path):
        extra_str = '{"already": "serialized"}'
        row = _make_row(extra=extra_str)
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.extra == extra_str

    def test_dvr_id_empty_string_when_absent(self, db_url, json_path):
        row = _make_row()
        row.pop("dvr_id")
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.dvr_id == ""


class TestIdempotency:
    def test_rerun_skips_existing_rows(self, db_url, json_path):
        rows = [_make_row() for _ in range(10)]
        _write_json(Path(json_path), rows)

        first = migrate_activity_history(json_path=json_path, db_url=db_url)
        second = migrate_activity_history(json_path=json_path, db_url=db_url)

        assert first["inserted"] == 10
        assert second["inserted"] == 0
        assert second["skipped"] == 10
        assert second["errors"] == 0

    def test_rerun_does_not_duplicate_rows(self, db_url, json_path):
        rows = [_make_row() for _ in range(20)]
        _write_json(Path(json_path), rows)

        migrate_activity_history(json_path=json_path, db_url=db_url)
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            count = len(session.exec(select(ActivityEvent)).all())
        engine.dispose()

        assert count == 20

    def test_partial_then_full_migration(self, db_url, json_path, tmp_path):
        all_rows = [_make_row() for _ in range(50)]
        partial_path = str(tmp_path / "partial.json")

        _write_json(Path(partial_path), all_rows[:20])
        r1 = migrate_activity_history(json_path=partial_path, db_url=db_url)
        assert r1["inserted"] == 20

        _write_json(Path(json_path), all_rows)
        r2 = migrate_activity_history(json_path=json_path, db_url=db_url)
        assert r2["inserted"] == 30
        assert r2["skipped"] == 20

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            count = len(session.exec(select(ActivityEvent)).all())
        engine.dispose()

        assert count == 50

    def test_duplicate_ids_in_source_json_inserted_once(self, db_url, json_path):
        row = _make_row(row_id="dup-id-999")
        _write_json(Path(json_path), [row, row, row])

        result = migrate_activity_history(json_path=json_path, db_url=db_url)

        assert result["total"] == 3
        assert result["inserted"] == 1
        assert result["skipped"] == 2

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            count = len(
                session.exec(
                    select(ActivityEvent).where(ActivityEvent.id == "dup-id-999")
                ).all()
            )
        engine.dispose()

        assert count == 1


class TestLargeScale:
    def test_10k_row_migration_count_and_speed(self, db_url, json_path):
        import time

        rows = [_make_row(dvr_id=f"dvr_{i % 5:02d}") for i in range(10_000)]
        _write_json(Path(json_path), rows)

        start = time.monotonic()
        result = migrate_activity_history(json_path=json_path, db_url=db_url)
        elapsed = time.monotonic() - start

        assert result["total"] == 10_000
        assert result["inserted"] == 10_000
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert elapsed < 30.0, f"Migration too slow: {elapsed:.1f}s"

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            count = len(session.exec(select(ActivityEvent)).all())
        engine.dispose()

        assert count == 10_000

    def test_10k_row_idempotent_rerun(self, db_url, json_path):
        rows = [_make_row() for _ in range(10_000)]
        _write_json(Path(json_path), rows)

        migrate_activity_history(json_path=json_path, db_url=db_url)
        second = migrate_activity_history(json_path=json_path, db_url=db_url)

        assert second["inserted"] == 0
        assert second["skipped"] == 10_000

    def test_10k_fidelity_spot_check(self, db_url, json_path):
        fixed_id = str(uuid.uuid4())
        fixed_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [_make_row() for _ in range(9_999)]
        sentinel = _make_row(
            row_id=fixed_id,
            dvr_id="dvr_sentinel",
            activity_type="disk_alert",
            is_test=True,
            extra={"sentinel": True, "value": 42},
            timestamp=fixed_ts.isoformat(),
        )
        rows.append(sentinel)
        _write_json(Path(json_path), rows)
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == fixed_id)
            ).one()
        engine.dispose()

        assert evt.dvr_id == "dvr_sentinel"
        assert evt.event_type == "disk_alert"
        assert evt.is_test is True
        assert json.loads(evt.extra) == {"sentinel": True, "value": 42}

        stored_ts = evt.timestamp
        if stored_ts.tzinfo is None:
            stored_ts = stored_ts.replace(tzinfo=timezone.utc)
        assert stored_ts == fixed_ts


class TestEdgeCases:
    def test_timestamp_without_timezone_assumed_utc(self, db_url, json_path):
        row = _make_row(timestamp="2024-03-15T08:00:00")
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        assert evt.timestamp is not None

    def test_malformed_timestamp_uses_epoch(self, db_url, json_path):
        row = _make_row(timestamp="not-a-date")
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        stored = evt.timestamp
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert stored.year == 1970

    def test_session_state_files_not_touched(self, tmp_path):
        session_file = tmp_path / "session_state_dvr_test.json"
        session_file.write_text('{"key": "value"}', encoding="utf-8")

        json_file = str(tmp_path / "activity_history.json")
        db_url = f"sqlite:///{tmp_path / 'test.db'}"
        _write_json(Path(json_file), [_make_row()])

        migrate_activity_history(json_path=json_file, db_url=db_url)

        assert session_file.read_text(encoding="utf-8") == '{"key": "value"}'

    def test_extra_invalid_string_wrapped(self, db_url, json_path):
        row = _make_row(extra="not json at all")
        row_id = row["id"]
        _write_json(Path(json_path), [row])
        migrate_activity_history(json_path=json_path, db_url=db_url)

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            evt = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == row_id)
            ).one()
        engine.dispose()

        parsed = json.loads(evt.extra)
        assert parsed == {"raw": "not json at all"}

    def test_custom_batch_size_produces_correct_count(self, db_url, json_path):
        rows = [_make_row() for _ in range(55)]
        _write_json(Path(json_path), rows)

        result = migrate_activity_history(
            json_path=json_path, db_url=db_url, batch_size=10
        )

        assert result["inserted"] == 55

        engine = create_db_engine(db_url)
        with get_session(engine) as session:
            count = len(session.exec(select(ActivityEvent)).all())
        engine.dispose()

        assert count == 55
