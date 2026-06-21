import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect as sa_inspect, text
from sqlmodel import select

from core.storage import (
    ActivityEvent,
    AlertHistoryRow,
    DvrServer,
    NotificationDelivery,
    StreamSession,
    create_all_tables,
    create_db_engine,
    get_session,
)


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_db_engine("sqlite:///:memory:")
    create_all_tables(engine)
    yield engine
    engine.dispose()


@pytest.fixture(name="seeded_dvr_id")
def seeded_dvr_id_fixture(engine):
    dvr_id = "dvr_test01"
    with get_session(engine) as session:
        session.add(DvrServer(id=dvr_id, name="Test DVR", host="192.168.1.10"))
        session.commit()
    return dvr_id


class TestDvrServer:
    def test_create_and_retrieve(self, engine):
        with get_session(engine) as session:
            session.add(
                DvrServer(id="dvr_a1b2c3d4", name="Living Room", host="192.168.0.5")
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(DvrServer).where(DvrServer.id == "dvr_a1b2c3d4")
            ).one()

        assert row.id == "dvr_a1b2c3d4"
        assert row.name == "Living Room"
        assert row.host == "192.168.0.5"
        assert row.port == 8089
        assert row.enabled is True
        assert row.deleted_at is None
        assert row.overrides == "{}"

    def test_defaults(self, engine):
        with get_session(engine) as session:
            session.add(DvrServer(id="dvr_defaults", name="D", host="10.0.0.1"))
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(DvrServer).where(DvrServer.id == "dvr_defaults")
            ).one()

        assert row.port == 8089
        assert row.enabled is True
        assert isinstance(row.created_at, datetime)
        assert isinstance(row.updated_at, datetime)

    def test_soft_delete_field(self, engine):
        ts = datetime.now(timezone.utc)
        with get_session(engine) as session:
            session.add(
                DvrServer(id="dvr_del", name="D", host="10.0.0.2", deleted_at=ts)
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(select(DvrServer).where(DvrServer.id == "dvr_del")).one()

        assert row.deleted_at is not None

    def test_update(self, engine):
        with get_session(engine) as session:
            session.add(DvrServer(id="dvr_upd", name="Old", host="1.2.3.4"))
            session.commit()

        with get_session(engine) as session:
            row = session.exec(select(DvrServer).where(DvrServer.id == "dvr_upd")).one()
            row.name = "New Name"
            session.add(row)
            session.commit()

        with get_session(engine) as session:
            row = session.exec(select(DvrServer).where(DvrServer.id == "dvr_upd")).one()

        assert row.name == "New Name"


class TestActivityEvent:
    def _make_event(
        self, dvr_id: str, event_type: str = "watching_channel", **extra
    ) -> ActivityEvent:
        return ActivityEvent(
            id=str(uuid.uuid4()),
            dvr_id=dvr_id,
            event_type=event_type,
            title="Test Event",
            **extra,
        )

    def test_create_and_retrieve(self, engine, seeded_dvr_id):
        event_id = str(uuid.uuid4())
        with get_session(engine) as session:
            session.add(
                ActivityEvent(
                    id=event_id,
                    dvr_id=seeded_dvr_id,
                    event_type="watching_channel",
                    title="Watching Channel",
                    message="Watching ESPN on Fire Stick",
                    channel_name="ESPN",
                    device_name="Fire Stick",
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(ActivityEvent).where(ActivityEvent.id == event_id)
            ).one()

        assert row.event_type == "watching_channel"
        assert row.channel_name == "ESPN"
        assert row.device_name == "Fire Stick"
        assert row.is_test is False

    def test_defaults(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            event = self._make_event(seeded_dvr_id)
            session.add(event)
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(ActivityEvent).where(ActivityEvent.dvr_id == seeded_dvr_id)
            ).first()

        assert row.icon == "bell"
        assert row.channel_name == ""
        assert row.is_test is False
        assert row.extra == "{}"

    def test_is_test_flag(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            session.add(self._make_event(seeded_dvr_id, is_test=True))
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(ActivityEvent).where(ActivityEvent.dvr_id == seeded_dvr_id)
            ).first()

        assert row.is_test is True

    def test_multiple_event_types(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            for et in (
                "watching_channel",
                "recording_event",
                "disk_alert",
                "watching_vod",
            ):
                session.add(self._make_event(seeded_dvr_id, event_type=et))
            session.commit()

        with get_session(engine) as session:
            rows = session.exec(
                select(ActivityEvent).where(ActivityEvent.dvr_id == seeded_dvr_id)
            ).all()

        types = {r.event_type for r in rows}
        assert types == {
            "watching_channel",
            "recording_event",
            "disk_alert",
            "watching_vod",
        }

    def test_indexes_exist(self, engine):
        insp = sa_inspect(engine)
        idx_names = {i["name"] for i in insp.get_indexes("activity_event")}
        assert "ix_activity_event_timestamp" in idx_names
        assert "ix_activity_event_dvr_id_timestamp" in idx_names
        assert "ix_activity_event_dvr_id_event_type" in idx_names

    def test_query_by_dvr_id_and_event_type(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            session.add(self._make_event(seeded_dvr_id, event_type="disk_alert"))
            session.add(self._make_event(seeded_dvr_id, event_type="watching_channel"))
            session.commit()

        with get_session(engine) as session:
            rows = session.exec(
                select(ActivityEvent).where(
                    ActivityEvent.dvr_id == seeded_dvr_id,
                    ActivityEvent.event_type == "disk_alert",
                )
            ).all()

        assert len(rows) == 1
        assert rows[0].event_type == "disk_alert"


class TestAlertHistoryRow:
    def test_create_and_retrieve(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            session.add(
                AlertHistoryRow(
                    dvr_id=seeded_dvr_id,
                    alert_type="channel_watching",
                    tracking_key=f"{seeded_dvr_id}-watching_channel-ESPN-FireStick",
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(AlertHistoryRow).where(AlertHistoryRow.dvr_id == seeded_dvr_id)
            ).one()

        assert row.alert_type == "channel_watching"
        assert seeded_dvr_id in row.tracking_key
        assert isinstance(row.notification_sent_at, datetime)

    def test_autoincrement_id(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            for i in range(3):
                session.add(
                    AlertHistoryRow(
                        dvr_id=seeded_dvr_id,
                        alert_type="disk_space",
                        tracking_key=f"key-{i}",
                    )
                )
            session.commit()

        with get_session(engine) as session:
            rows = session.exec(select(AlertHistoryRow)).all()

        ids = [r.id for r in rows]
        assert len(ids) == 3
        assert len(set(ids)) == 3

    def test_index_exists(self, engine):
        insp = sa_inspect(engine)
        idx_names = {i["name"] for i in insp.get_indexes("alert_history_row")}
        assert "ix_alert_history_row_dvr_id_sent_at" in idx_names

    def test_extra_json_field(self, engine, seeded_dvr_id):
        payload = '{"severity": "warning", "percent_free": 8.5}'
        with get_session(engine) as session:
            session.add(
                AlertHistoryRow(
                    dvr_id=seeded_dvr_id,
                    alert_type="disk_space",
                    tracking_key="key-extra",
                    extra=payload,
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(select(AlertHistoryRow)).one()

        assert row.extra == payload


class TestNotificationDelivery:
    def test_create_delivered(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            session.add(
                NotificationDelivery(
                    dvr_id=seeded_dvr_id,
                    provider_type="apprise",
                    channel_id="tgram://bottoken/chatid",
                    delivered=True,
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(select(NotificationDelivery)).one()

        assert row.delivered is True
        assert row.error_message is None
        assert row.provider_type == "apprise"

    def test_create_failed(self, engine, seeded_dvr_id):
        with get_session(engine) as session:
            session.add(
                NotificationDelivery(
                    dvr_id=seeded_dvr_id,
                    provider_type="webhook",
                    channel_id="https://hooks.example.com/abc",
                    delivered=False,
                    error_message="Connection refused",
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(select(NotificationDelivery)).one()

        assert row.delivered is False
        assert row.error_message == "Connection refused"

    def test_activity_event_link(self, engine, seeded_dvr_id):
        event_id = str(uuid.uuid4())
        with get_session(engine) as session:
            session.add(
                ActivityEvent(
                    id=event_id,
                    dvr_id=seeded_dvr_id,
                    event_type="watching_channel",
                    title="T",
                )
            )
            session.add(
                NotificationDelivery(
                    dvr_id=seeded_dvr_id,
                    activity_event_id=event_id,
                    provider_type="apprise",
                    delivered=True,
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(select(NotificationDelivery)).one()

        assert row.activity_event_id == event_id

    def test_index_exists(self, engine):
        insp = sa_inspect(engine)
        idx_names = {i["name"] for i in insp.get_indexes("notification_delivery")}
        assert "ix_notification_delivery_delivered_at" in idx_names
        assert "ix_notification_delivery_dvr_id_delivered_at" in idx_names


class TestStreamSession:
    def test_create_active(self, engine, seeded_dvr_id):
        session_id = str(uuid.uuid4())
        with get_session(engine) as session:
            session.add(
                StreamSession(
                    id=session_id,
                    dvr_id=seeded_dvr_id,
                    device_name="Apple TV",
                    channel_name="ESPN",
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(StreamSession).where(StreamSession.id == session_id)
            ).one()

        assert row.device_name == "Apple TV"
        assert row.channel_name == "ESPN"
        assert row.ended_at is None

    def test_end_session(self, engine, seeded_dvr_id):
        session_id = str(uuid.uuid4())
        ended = datetime.now(timezone.utc)
        with get_session(engine) as db_session:
            db_session.add(
                StreamSession(
                    id=session_id,
                    dvr_id=seeded_dvr_id,
                    device_name="Roku",
                )
            )
            db_session.commit()

        with get_session(engine) as db_session:
            row = db_session.exec(
                select(StreamSession).where(StreamSession.id == session_id)
            ).one()
            row.ended_at = ended
            db_session.add(row)
            db_session.commit()

        with get_session(engine) as db_session:
            row = db_session.exec(
                select(StreamSession).where(StreamSession.id == session_id)
            ).one()

        assert row.ended_at is not None

    def test_activity_data_json(self, engine, seeded_dvr_id):
        payload = '{"activity": "Watching Channel 5 from Fire Stick", "ch": "5"}'
        with get_session(engine) as session:
            session.add(
                StreamSession(
                    id=str(uuid.uuid4()),
                    dvr_id=seeded_dvr_id,
                    device_name="Fire Stick",
                    activity_data=payload,
                )
            )
            session.commit()

        with get_session(engine) as session:
            row = session.exec(
                select(StreamSession).where(StreamSession.dvr_id == seeded_dvr_id)
            ).one()

        assert row.activity_data == payload

    def test_index_exists(self, engine):
        insp = sa_inspect(engine)
        idx_names = {i["name"] for i in insp.get_indexes("stream_session")}
        assert "ix_stream_session_dvr_id_started_at" in idx_names


class TestSchemaCompleteness:
    def test_create_all_adds_query_indexes_to_existing_tables(self):
        engine = create_db_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE activity_event ("
                    "id TEXT PRIMARY KEY, dvr_id TEXT, event_type TEXT, title TEXT, "
                    "timestamp DATETIME)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE notification_delivery ("
                    "id INTEGER PRIMARY KEY, dvr_id TEXT, delivered_at DATETIME)"
                )
            )
            conn.commit()

        create_all_tables(engine)
        insp = sa_inspect(engine)
        assert "ix_activity_event_timestamp" in {
            i["name"] for i in insp.get_indexes("activity_event")
        }
        assert "ix_notification_delivery_delivered_at" in {
            i["name"] for i in insp.get_indexes("notification_delivery")
        }
        engine.dispose()

    def test_all_tables_created(self, engine):
        insp = sa_inspect(engine)
        tables = set(insp.get_table_names())
        expected = {
            "dvr_server",
            "activity_event",
            "alert_history_row",
            "notification_delivery",
            "stream_session",
        }
        assert expected.issubset(tables)

    def test_all_models_importable(self):
        from core.storage import (
            ActivityEvent,
            AlertHistoryRow,
            DvrServer,
            NotificationDelivery,
            StreamSession,
        )

        for cls in (
            DvrServer,
            ActivityEvent,
            AlertHistoryRow,
            NotificationDelivery,
            StreamSession,
        ):
            assert hasattr(cls, "__tablename__")

    def test_all_indexes(self, engine):
        insp = sa_inspect(engine)
        expected_indexes = {
            "activity_event": {
                "ix_activity_event_timestamp",
                "ix_activity_event_dvr_id_timestamp",
                "ix_activity_event_dvr_id_event_type",
            },
            "alert_history_row": {"ix_alert_history_row_dvr_id_sent_at"},
            "notification_delivery": {
                "ix_notification_delivery_delivered_at",
                "ix_notification_delivery_dvr_id_delivered_at",
            },
            "stream_session": {"ix_stream_session_dvr_id_started_at"},
        }
        for table, idx_set in expected_indexes.items():
            actual = {i["name"] for i in insp.get_indexes(table)}
            for name in idx_set:
                assert name in actual, f"Missing index {name!r} on {table!r}"
