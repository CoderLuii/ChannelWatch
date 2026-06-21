import csv
import io
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from core.storage import (
    ActivityEvent,
    NotificationDelivery,
    create_all_tables,
    create_db_engine,
    detect_filesystem,
    configure_journal_mode,
    get_session,
    prune_old_events,
    run_nightly_maintenance,
    vacuum_db,
)
from sqlmodel import select


@pytest.fixture(name="mem_engine")
def mem_engine_fixture():
    engine = create_db_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    create_all_tables(engine)
    yield engine
    engine.dispose()


def _seed_events(engine, count, dvr_id="dvr_test01", days_old=0):
    ts = datetime.now(timezone.utc) - timedelta(days=days_old)
    events = []
    with get_session(engine) as session:
        for _ in range(count):
            evt = ActivityEvent(
                id=str(uuid.uuid4()),
                dvr_id=dvr_id,
                event_type="watching_channel",
                title="Test",
                timestamp=ts,
            )
            session.add(evt)
            events.append(evt)
        session.commit()
    return events


def _seed_deliveries(engine, count, dvr_id="dvr_test01", days_old=0):
    ts = datetime.now(timezone.utc) - timedelta(days=days_old)
    with get_session(engine) as session:
        for _ in range(count):
            session.add(
                NotificationDelivery(
                    dvr_id=dvr_id,
                    provider_type="apprise",
                    delivered=True,
                    delivered_at=ts,
                )
            )
        session.commit()


class TestDetectFilesystem:
    def test_returns_unknown_when_proc_mounts_absent(self, tmp_path):
        with patch("core.storage.database.os.path.exists", return_value=False):
            result = detect_filesystem(str(tmp_path))
        assert result == "unknown"

    def test_returns_native_for_ext4(self, tmp_path):
        fake_mounts = f"sda1 / ext4 rw 0 0\nsda2 {tmp_path} ext4 rw 0 0\n"
        with (
            patch("builtins.open", mock_open(read_data=fake_mounts)),
            patch("core.storage.database.os.path.exists", return_value=True),
        ):
            result = detect_filesystem(str(tmp_path))
        assert result == "native"

    def test_returns_nfs_for_nfs_mount(self, tmp_path):
        fake_mounts = f"server:/share {tmp_path} nfs4 rw 0 0\n"
        with (
            patch("builtins.open", mock_open(read_data=fake_mounts)),
            patch("core.storage.database.os.path.exists", return_value=True),
        ):
            result = detect_filesystem(str(tmp_path))
        assert result == "nfs"

    def test_returns_cifs_for_smb_mount(self, tmp_path):
        fake_mounts = f"//server/share {tmp_path} cifs rw 0 0\n"
        with (
            patch("builtins.open", mock_open(read_data=fake_mounts)),
            patch("core.storage.database.os.path.exists", return_value=True),
        ):
            result = detect_filesystem(str(tmp_path))
        assert result == "cifs"

    def test_returns_fuse_for_s3fs(self, tmp_path):
        fake_mounts = f"s3fs {tmp_path} fuse.s3fs ro 0 0\n"
        with (
            patch("builtins.open", mock_open(read_data=fake_mounts)),
            patch("core.storage.database.os.path.exists", return_value=True),
        ):
            result = detect_filesystem(str(tmp_path))
        assert result == "fuse"

    def test_returns_fuse_for_rclone(self, tmp_path):
        fake_mounts = f"rclone {tmp_path} fuse.rclone rw 0 0\n"
        with (
            patch("builtins.open", mock_open(read_data=fake_mounts)),
            patch("core.storage.database.os.path.exists", return_value=True),
        ):
            result = detect_filesystem(str(tmp_path))
        assert result == "fuse"

    def test_longest_mountpoint_wins(self, tmp_path):
        deeper = str(tmp_path / "data")
        Path(deeper).mkdir()
        fake_mounts = (
            f"sda1 / ext4 rw 0 0\n"
            f"nas:/share {tmp_path} nfs4 rw 0 0\n"
            f"sda2 {deeper} ext4 rw 0 0\n"
        )
        with (
            patch("builtins.open", mock_open(read_data=fake_mounts)),
            patch("core.storage.database.os.path.exists", return_value=True),
        ):
            result = detect_filesystem(deeper)
        assert result == "native"

    def test_unknown_when_no_mount_covers_path(self, tmp_path):
        unrelated = "/mnt/other"
        fake_mounts = f"sda1 {unrelated} ext4 rw 0 0\n"
        with (
            patch("builtins.open", mock_open(read_data=fake_mounts)),
            patch("core.storage.database.os.path.exists", return_value=True),
        ):
            result = detect_filesystem(str(tmp_path))
        assert result == "unknown"


class TestConfigureJournalMode:
    def test_wal_enabled_on_native_fs(self, tmp_path):
        db_file = str(tmp_path / "test_wal.db")
        engine = create_db_engine(f"sqlite:///{db_file}")
        create_all_tables(engine)
        fake_mounts = f"sda1 {tmp_path} ext4 rw 0 0\n"
        try:
            with (
                patch("builtins.open", mock_open(read_data=fake_mounts)),
                patch("core.storage.database.os.path.exists", return_value=True),
            ):
                mode = configure_journal_mode(engine, db_file)
            assert mode.lower() == "wal"
        finally:
            engine.dispose()

    def test_no_wal_on_nfs(self, tmp_path):
        db_file = str(tmp_path / "test_nfs.db")
        engine = create_db_engine(f"sqlite:///{db_file}")
        create_all_tables(engine)
        fake_mounts = f"nas:/share {tmp_path} nfs4 rw 0 0\n"
        try:
            with (
                patch("builtins.open", mock_open(read_data=fake_mounts)),
                patch("core.storage.database.os.path.exists", return_value=True),
            ):
                mode = configure_journal_mode(engine, db_file)
            assert mode.lower() != "wal"
        finally:
            engine.dispose()

    def test_no_wal_on_cifs(self, tmp_path):
        db_file = str(tmp_path / "test_cifs.db")
        engine = create_db_engine(f"sqlite:///{db_file}")
        create_all_tables(engine)
        fake_mounts = f"//server/share {tmp_path} cifs rw 0 0\n"
        try:
            with (
                patch("builtins.open", mock_open(read_data=fake_mounts)),
                patch("core.storage.database.os.path.exists", return_value=True),
            ):
                mode = configure_journal_mode(engine, db_file)
            assert mode.lower() != "wal"
        finally:
            engine.dispose()

    def test_no_wal_on_fuse(self, tmp_path):
        db_file = str(tmp_path / "test_fuse.db")
        engine = create_db_engine(f"sqlite:///{db_file}")
        create_all_tables(engine)
        fake_mounts = f"sshfs {tmp_path} fuse.sshfs rw 0 0\n"
        try:
            with (
                patch("builtins.open", mock_open(read_data=fake_mounts)),
                patch("core.storage.database.os.path.exists", return_value=True),
            ):
                mode = configure_journal_mode(engine, db_file)
            assert mode.lower() != "wal"
        finally:
            engine.dispose()

    def test_returns_string_mode(self, tmp_path):
        db_file = str(tmp_path / "test_str.db")
        engine = create_db_engine(f"sqlite:///{db_file}")
        create_all_tables(engine)
        try:
            with patch("core.storage.database.os.path.exists", return_value=False):
                mode = configure_journal_mode(engine, db_file)
            assert isinstance(mode, str)
            assert len(mode) > 0
        finally:
            engine.dispose()


class TestPruneOldEvents:
    def test_prune_removes_old_activity_events(self, mem_engine):
        _seed_events(mem_engine, 5, days_old=100)
        _seed_events(mem_engine, 3, days_old=0)

        result = prune_old_events(mem_engine, retention_days=90)

        assert result["activity_events_deleted"] == 5
        with get_session(mem_engine) as session:
            remaining = session.exec(select(ActivityEvent)).all()
        assert len(remaining) == 3

    def test_prune_removes_old_notification_deliveries(self, mem_engine):
        _seed_deliveries(mem_engine, 4, days_old=100)
        _seed_deliveries(mem_engine, 2, days_old=0)

        result = prune_old_events(mem_engine, retention_days=90)

        assert result["notification_deliveries_deleted"] == 4
        with get_session(mem_engine) as session:
            remaining = session.exec(select(NotificationDelivery)).all()
        assert len(remaining) == 2

    def test_prune_keeps_recent_events(self, mem_engine):
        _seed_events(mem_engine, 10, days_old=0)

        result = prune_old_events(mem_engine, retention_days=90)

        assert result["activity_events_deleted"] == 0
        with get_session(mem_engine) as session:
            remaining = session.exec(select(ActivityEvent)).all()
        assert len(remaining) == 10

    def test_prune_returns_result_dict(self, mem_engine):
        result = prune_old_events(mem_engine, retention_days=30)

        assert "activity_events_deleted" in result
        assert "notification_deliveries_deleted" in result
        assert "retention_days" in result
        assert result["retention_days"] == 30
        assert "cutoff" in result

    def test_prune_boundary_exact(self, mem_engine):
        cutoff_plus_one = datetime.now(timezone.utc) - timedelta(days=90, seconds=1)
        with get_session(mem_engine) as session:
            session.add(
                ActivityEvent(
                    id=str(uuid.uuid4()),
                    dvr_id="dvr_test",
                    event_type="disk_alert",
                    title="Old boundary",
                    timestamp=cutoff_plus_one,
                )
            )
            session.commit()

        result = prune_old_events(mem_engine, retention_days=90)

        assert result["activity_events_deleted"] == 1

    def test_prune_empty_db_no_error(self, mem_engine):
        result = prune_old_events(mem_engine, retention_days=90)

        assert result["activity_events_deleted"] == 0
        assert result["notification_deliveries_deleted"] == 0


class TestVacuumDb:
    def test_vacuum_runs_without_error(self, tmp_path):
        db_path = tmp_path / "vacuum_test.db"
        engine = create_db_engine(f"sqlite:///{db_path}")
        create_all_tables(engine)
        _seed_events(engine, 10)
        prune_old_events(engine, retention_days=0)

        vacuum_db(engine)
        engine.dispose()

    def test_vacuum_on_file_db(self, tmp_path):
        db_path = tmp_path / "test_vac.db"
        engine = create_db_engine(f"sqlite:///{db_path}")
        create_all_tables(engine)
        _seed_events(engine, 5)

        vacuum_db(engine)

        with get_session(engine) as session:
            count = len(session.exec(select(ActivityEvent)).all())
        assert count == 5
        engine.dispose()


class TestRunNightlyMaintenance:
    def test_combines_prune_and_vacuum(self, mem_engine):
        _seed_events(mem_engine, 3, days_old=100)
        _seed_events(mem_engine, 2, days_old=0)

        result = run_nightly_maintenance(mem_engine, retention_days=90)

        assert result["activity_events_deleted"] == 3
        with get_session(mem_engine) as session:
            remaining = session.exec(select(ActivityEvent)).all()
        assert len(remaining) == 2


@pytest.fixture()
def api_settings_file(tmp_path):
    data = {
        "dvr_servers": [
            {
                "id": "dvr_test",
                "host": "192.168.1.100",
                "port": 8089,
                "name": "Test DVR",
                "enabled": True,
            }
        ],
        "tz": "America/New_York",
        "api_key": "test-key-12345",
    }
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(data))
    return f


@pytest.fixture()
def export_client(api_settings_file, tmp_path):
    engine = create_db_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    create_all_tables(engine)
    history_file = tmp_path / "activity_history.json"
    history_file.write_text("[]")
    with (
        patch("ui.backend.config.CONFIG_FILE", api_settings_file),
        patch("ui.backend.config.CONFIG_DIR", api_settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main._get_activity_db_engine", return_value=engine),
        patch("ui.backend.main._activity_db_engine", engine),
        patch("ui.backend.main._STORAGE_AVAILABLE", True),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False), engine


class TestCsvExportEndpoint:
    def test_returns_csv_content_type(self, export_client):
        client, engine = export_client
        _seed_events(engine, 1)

        resp = client.get("/api/v1/history/export?format=csv")

        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_content_disposition_attachment(self, export_client):
        client, engine = export_client
        _seed_events(engine, 1)

        resp = client.get("/api/v1/history/export?format=csv")

        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")

    def test_csv_has_header_row(self, export_client):
        client, engine = export_client

        resp = client.get("/api/v1/history/export?format=csv")

        assert resp.status_code == 200
        lines = resp.text.splitlines()
        assert len(lines) >= 1
        header = lines[0]
        for col in ("id", "dvr_id", "event_type", "title", "timestamp"):
            assert col in header

    def test_csv_contains_seeded_rows(self, export_client):
        client, engine = export_client
        _seed_events(engine, 5, dvr_id="dvr_export01")

        resp = client.get("/api/v1/history/export?format=csv")

        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 5
        assert all(r["dvr_id"] == "dvr_export01" for r in rows)

    def test_csv_dvr_id_filter(self, export_client):
        client, engine = export_client
        _seed_events(engine, 3, dvr_id="dvr_A")
        _seed_events(engine, 2, dvr_id="dvr_B")

        resp = client.get("/api/v1/history/export?format=csv&dvr_id=dvr_A")

        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 3
        assert all(r["dvr_id"] == "dvr_A" for r in rows)

    def test_csv_empty_history_returns_header_only(self, export_client):
        client, engine = export_client

        resp = client.get("/api/v1/history/export?format=csv")

        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert rows == []

    def test_invalid_format_returns_400(self, export_client):
        client, _ = export_client

        resp = client.get("/api/v1/history/export?format=json")

        assert resp.status_code == 400

    def test_filename_includes_dvr_id(self, export_client):
        client, engine = export_client
        _seed_events(engine, 1, dvr_id="dvr_abc")

        resp = client.get("/api/v1/history/export?format=csv&dvr_id=dvr_abc")

        assert resp.status_code == 200
        assert "dvr_abc" in resp.headers.get("content-disposition", "")

    def test_filename_sanitizes_control_quotes_and_crlf(self, export_client):
        client, engine = export_client
        _seed_events(engine, 1, dvr_id="safe")

        resp = client.get(
            "/api/v1/history/export?format=csv&dvr_id=x%22%0D%0ASet-Cookie:%20bad=1"
        )

        assert resp.status_code == 200
        header = resp.headers.get("content-disposition", "")
        assert "\r" not in header
        assert "\n" not in header
        assert '"' in header
        filename = header.split('filename="', 1)[1].rstrip('"')
        assert '"' not in filename
        assert "Set-Cookie" in filename

    def test_all_dvrs_filename_when_no_filter(self, export_client):
        client, engine = export_client

        resp = client.get("/api/v1/history/export?format=csv")

        assert resp.status_code == 200
        assert "all" in resp.headers.get("content-disposition", "")

    def test_csv_fields_correctly_populated(self, export_client):
        client, engine = export_client
        with get_session(engine) as session:
            session.add(
                ActivityEvent(
                    id="fixed-export-id",
                    dvr_id="dvr_field_test",
                    dvr_name="Living Room DVR",
                    event_type="recording_event",
                    title="Test Recording",
                    message="A test message",
                    channel_name="HBO",
                    channel_number="301",
                    device_name="Apple TV",
                    is_test=True,
                )
            )
            session.commit()

        resp = client.get("/api/v1/history/export?format=csv&dvr_id=dvr_field_test")

        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == "fixed-export-id"
        assert row["dvr_name"] == "Living Room DVR"
        assert row["event_type"] == "recording_event"
        assert row["channel_name"] == "HBO"
        assert row["is_test"] == "true"

    def test_csv_escapes_commas_in_title(self, export_client):
        client, engine = export_client
        with get_session(engine) as session:
            session.add(
                ActivityEvent(
                    id="escape-test-id",
                    dvr_id="dvr_escape",
                    event_type="watching_channel",
                    title="Breaking News, Live",
                )
            )
            session.commit()

        resp = client.get("/api/v1/history/export?format=csv&dvr_id=dvr_escape")

        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["title"] == "Breaking News, Live"
