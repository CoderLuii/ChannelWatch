from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlmodel import select
from starlette.testclient import TestClient

from core.storage import (
    NotificationDelivery,
    create_all_tables,
    create_db_engine,
    get_session,
    migrate_delivery_schema,
    insert_delivery_record,
    query_delivery_log,
)
from core.notifications.delivery import CircuitBreaker, deliver_with_retry, RETRY_DELAYS
from core.notifications.notification import APPRISE_DEST_KEYS


@pytest.fixture(name="engine")
def engine_fixture():
    eng = create_db_engine("sqlite:///:memory:")
    create_all_tables(eng)
    migrate_delivery_schema(eng)
    yield eng
    eng.dispose()


@pytest.fixture(name="endpoint_engine")
def endpoint_engine_fixture(tmp_path):
    eng = create_db_engine(f"sqlite:///{tmp_path / 'notification-log.db'}")
    create_all_tables(eng)
    migrate_delivery_schema(eng)
    yield eng
    eng.dispose()


@pytest.fixture(name="notification_log_client")
def notification_log_client_fixture(tmp_path, endpoint_engine):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"api_key":"test-key","tz":"UTC"}', encoding="utf-8")
    history_file = tmp_path / "activity_history.json"
    history_file.write_text("[]", encoding="utf-8")

    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.HISTORY_FILE", history_file),
        patch("ui.backend.main._get_activity_db_engine", return_value=endpoint_engine),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert not cb.is_open("dvr1", "apprise")

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker()
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD - 1):
            just_opened = cb.record_failure("dvr1", "apprise")
            assert not just_opened
            assert not cb.is_open("dvr1", "apprise")
        just_opened = cb.record_failure("dvr1", "apprise")
        assert just_opened
        assert cb.is_open("dvr1", "apprise")

    def test_success_resets_count(self):
        cb = CircuitBreaker()
        for _ in range(4):
            cb.record_failure("dvr1", "apprise")
        cb.record_success("dvr1", "apprise")
        assert not cb.is_open("dvr1", "apprise")
        assert cb.failure_count("dvr1", "apprise") == 0

    def test_circuit_closes_after_open_duration(self):
        cb = CircuitBreaker()
        cb.OPEN_DURATION_SECONDS = 0.05
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            cb.record_failure("dvr1", "webhook")
        time.sleep(0.001)
        assert cb.is_open("dvr1", "webhook")
        time.sleep(0.1)
        assert not cb.is_open("dvr1", "webhook")

    def test_independent_keys(self):
        cb = CircuitBreaker()
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            cb.record_failure("dvr1", "apprise")
        assert cb.is_open("dvr1", "apprise")
        assert not cb.is_open("dvr2", "apprise")
        assert not cb.is_open("dvr1", "webhook")

    def test_failure_count_increments(self):
        cb = CircuitBreaker()
        cb.record_failure("dvr1", "apprise")
        cb.record_failure("dvr1", "apprise")
        assert cb.failure_count("dvr1", "apprise") == 2

    def test_opened_at_none_when_closed(self):
        cb = CircuitBreaker()
        assert cb.opened_at("dvr1", "apprise") is None


class TestDeliverWithRetry:
    def test_success_on_first_attempt_emits_sent(self):
        cb = CircuitBreaker()
        calls = []

        def deliver():
            calls.append(1)
            return True

        result = deliver_with_retry(
            dvr_id="dvr1",
            channel="apprise",
            event_type="test",
            provider_type="Apprise",
            channel_id="apprise",
            payload_size=100,
            deliver_fn=deliver,
            circuit_breaker=cb,
            with_retry=True,
        )
        assert result is True
        assert len(calls) == 1
        assert not cb.is_open("dvr1", "apprise")

    def test_success_after_retry_emits_sent(self, engine):
        cb = CircuitBreaker()
        calls = []

        def deliver():
            calls.append(1)
            return len(calls) >= 2

        with patch("core.notifications.delivery.time.sleep"):
            result = deliver_with_retry(
                dvr_id="dvr1",
                channel="apprise",
                event_type="test",
                provider_type="Apprise",
                channel_id="apprise",
                payload_size=50,
                deliver_fn=deliver,
                circuit_breaker=cb,
                db_engine=engine,
                with_retry=True,
            )
        assert result is True
        assert len(calls) == 2
        with get_session(engine) as session:
            rows = session.exec(
                select(NotificationDelivery).order_by(NotificationDelivery.id)
            ).all()
        assert rows[0].status == "retry"
        assert rows[1].status == "sent"

    def test_intermediate_failures_are_retry_final_is_failed(self, engine):
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 100

        def deliver():
            return False

        with patch("core.notifications.delivery.time.sleep"):
            result = deliver_with_retry(
                dvr_id="dvr1",
                channel="apprise",
                event_type="test",
                provider_type="Apprise",
                channel_id="apprise",
                payload_size=50,
                deliver_fn=deliver,
                circuit_breaker=cb,
                db_engine=engine,
                with_retry=True,
            )
        assert result is False
        with get_session(engine) as session:
            rows = session.exec(
                select(NotificationDelivery).order_by(NotificationDelivery.id)
            ).all()
        assert len(rows) == 6
        for row in rows[:-1]:
            assert row.status == "retry", (
                f"Expected retry, got {row.status} for row {row.id}"
            )
        assert rows[-1].status == "failed"

    def test_with_retry_false_single_failure_is_failed_not_retry(self, engine):
        cb = CircuitBreaker()
        calls = []

        def deliver():
            calls.append(1)
            return False

        deliver_with_retry(
            dvr_id="dvr1",
            channel="webhook",
            event_type="test",
            provider_type="webhook",
            channel_id="",
            payload_size=50,
            deliver_fn=deliver,
            circuit_breaker=cb,
            db_engine=engine,
            with_retry=False,
        )
        assert len(calls) == 1
        rows, total = query_delivery_log(engine, status="failed")
        assert total == 1
        retry_rows, retry_total = query_delivery_log(engine, status="retry")
        assert retry_total == 0

    def test_exhausted_retries_returns_false(self):
        cb = CircuitBreaker()

        def deliver():
            return False

        with patch("core.notifications.delivery.time.sleep"):
            result = deliver_with_retry(
                dvr_id="dvr1",
                channel="apprise",
                event_type="test",
                provider_type="Apprise",
                channel_id="apprise",
                payload_size=50,
                deliver_fn=deliver,
                circuit_breaker=cb,
                with_retry=True,
            )
        assert result is False

    def test_skips_when_circuit_open(self):
        cb = CircuitBreaker()
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            cb.record_failure("dvr1", "apprise")
        calls = []

        def deliver():
            calls.append(1)
            return True

        result = deliver_with_retry(
            dvr_id="dvr1",
            channel="apprise",
            event_type="test",
            provider_type="Apprise",
            channel_id="apprise",
            payload_size=50,
            deliver_fn=deliver,
            circuit_breaker=cb,
            with_retry=True,
        )
        assert result is False
        assert len(calls) == 0

    def test_retry_delay_sequence(self):
        cb = CircuitBreaker()
        cb.FAILURE_THRESHOLD = 100
        sleep_calls = []

        def deliver():
            return False

        with patch(
            "core.notifications.delivery.time.sleep",
            side_effect=lambda d: sleep_calls.append(d),
        ):
            deliver_with_retry(
                dvr_id="dvr1",
                channel="apprise",
                event_type="test",
                provider_type="Apprise",
                channel_id="apprise",
                payload_size=50,
                deliver_fn=deliver,
                circuit_breaker=cb,
                with_retry=True,
            )
        assert sleep_calls == RETRY_DELAYS

    def test_exception_in_deliver_fn_counts_as_failure(self):
        cb = CircuitBreaker()

        def deliver():
            raise RuntimeError("boom")

        result = deliver_with_retry(
            dvr_id="dvr1",
            channel="apprise",
            event_type="test",
            provider_type="Apprise",
            channel_id="apprise",
            payload_size=50,
            deliver_fn=deliver,
            circuit_breaker=cb,
            with_retry=False,
        )
        assert result is False
        assert cb.failure_count("dvr1", "apprise") == 1


class TestDeliveryPersistence:
    def test_insert_and_query_sent(self, engine):
        insert_delivery_record(
            engine,
            dvr_id="dvr1",
            event_type="watching_channel",
            channel="apprise",
            provider_type="Apprise",
            channel_id="apprise",
            status="sent",
            retry_count=0,
            payload_size=128,
        )
        rows, total = query_delivery_log(engine)
        assert total == 1
        assert rows[0].status == "sent"
        assert rows[0].delivered is True
        assert rows[0].channel == "apprise"
        assert rows[0].event_type == "watching_channel"
        assert rows[0].retry_count == 0
        assert rows[0].payload_size == 128

    def test_insert_retry_status(self, engine):
        insert_delivery_record(
            engine,
            dvr_id="dvr1",
            event_type="test",
            channel="apprise",
            provider_type="Apprise",
            channel_id="apprise",
            status="retry",
            retry_count=1,
            payload_size=50,
            error_message="Connection refused",
        )
        rows, total = query_delivery_log(engine, status="retry")
        assert total == 1
        assert rows[0].status == "retry"
        assert rows[0].delivered is False
        assert rows[0].retry_count == 1

    def test_filter_by_dvr_id(self, engine):
        insert_delivery_record(
            engine,
            dvr_id="dvr1",
            event_type="disk_alert",
            channel="apprise",
            provider_type="Apprise",
            channel_id="apprise",
            status="failed",
            retry_count=5,
            payload_size=64,
        )
        insert_delivery_record(
            engine,
            dvr_id="dvr2",
            event_type="disk_alert",
            channel="apprise",
            provider_type="Apprise",
            channel_id="apprise",
            status="sent",
            retry_count=0,
            payload_size=64,
        )
        rows, total = query_delivery_log(engine, dvr_id="dvr1")
        assert total == 1
        assert rows[0].dvr_id == "dvr1"

    def test_filter_by_channel(self, engine):
        insert_delivery_record(
            engine,
            dvr_id="dvr1",
            event_type="test",
            channel="apprise",
            provider_type="Apprise",
            channel_id="apprise",
            status="sent",
            retry_count=0,
            payload_size=50,
        )
        insert_delivery_record(
            engine,
            dvr_id="dvr1",
            event_type="test",
            channel="webhook",
            provider_type="webhook",
            channel_id="",
            status="failed",
            retry_count=0,
            payload_size=50,
        )
        rows, total = query_delivery_log(engine, channel="webhook")
        assert total == 1
        assert rows[0].channel == "webhook"

    def test_filter_by_status(self, engine):
        for s in ("sent", "retry", "failed", "circuit_open"):
            insert_delivery_record(
                engine,
                dvr_id="dvr1",
                event_type="test",
                channel="apprise",
                provider_type="Apprise",
                channel_id="apprise",
                status=s,
                retry_count=0,
                payload_size=10,
            )
        rows, total = query_delivery_log(engine, status="circuit_open")
        assert total == 1
        assert rows[0].status == "circuit_open"
        rows2, total2 = query_delivery_log(engine, status="retry")
        assert total2 == 1
        assert rows2[0].status == "retry"

    def test_time_range_filter_since(self, engine):
        now = datetime.now(timezone.utc)
        old_ts = now - timedelta(hours=2)
        recent_ts = now - timedelta(minutes=10)

        with get_session(engine) as session:
            session.add(
                NotificationDelivery(
                    dvr_id="dvr1",
                    provider_type="Apprise",
                    channel_id="apprise",
                    channel="apprise",
                    event_type="test",
                    status="sent",
                    delivered=True,
                    delivered_at=old_ts,
                )
            )
            session.add(
                NotificationDelivery(
                    dvr_id="dvr1",
                    provider_type="Apprise",
                    channel_id="apprise",
                    channel="apprise",
                    event_type="test",
                    status="sent",
                    delivered=True,
                    delivered_at=recent_ts,
                )
            )
            session.commit()

        cutoff = now - timedelta(hours=1)
        rows, total = query_delivery_log(engine, since=cutoff)
        assert total == 1
        assert rows[0].delivered_at.replace(tzinfo=timezone.utc) >= cutoff

    def test_time_range_filter_until(self, engine):
        now = datetime.now(timezone.utc)
        old_ts = now - timedelta(hours=3)
        recent_ts = now - timedelta(minutes=5)

        with get_session(engine) as session:
            session.add(
                NotificationDelivery(
                    dvr_id="dvr1",
                    provider_type="Apprise",
                    channel_id="apprise",
                    channel="apprise",
                    event_type="test",
                    status="sent",
                    delivered=True,
                    delivered_at=old_ts,
                )
            )
            session.add(
                NotificationDelivery(
                    dvr_id="dvr1",
                    provider_type="Apprise",
                    channel_id="apprise",
                    channel="apprise",
                    event_type="test",
                    status="sent",
                    delivered=True,
                    delivered_at=recent_ts,
                )
            )
            session.commit()

        cutoff = now - timedelta(hours=1)
        rows, total = query_delivery_log(engine, until=cutoff)
        assert total == 1

    def test_time_range_filter_since_and_until(self, engine):
        now = datetime.now(timezone.utc)
        with get_session(engine) as session:
            for hours_ago in [5, 3, 1]:
                session.add(
                    NotificationDelivery(
                        dvr_id="dvr1",
                        provider_type="Apprise",
                        channel_id="apprise",
                        channel="apprise",
                        event_type="test",
                        status="sent",
                        delivered=True,
                        delivered_at=now - timedelta(hours=hours_ago),
                    )
                )
            session.commit()

        rows, total = query_delivery_log(
            engine,
            since=now - timedelta(hours=4),
            until=now - timedelta(hours=2),
        )
        assert total == 1

    def test_deliver_with_retry_writes_sent_to_db(self, engine):
        cb = CircuitBreaker()

        def deliver():
            return True

        deliver_with_retry(
            dvr_id="dvr1",
            channel="apprise",
            event_type="watching_channel",
            provider_type="Apprise",
            channel_id="apprise",
            payload_size=200,
            deliver_fn=deliver,
            circuit_breaker=cb,
            db_engine=engine,
            with_retry=True,
        )
        with get_session(engine) as session:
            rows = session.exec(select(NotificationDelivery)).all()
        assert len(rows) == 1
        assert rows[0].status == "sent"
        assert rows[0].delivered is True
        assert rows[0].event_type == "watching_channel"

    def test_single_failed_attempt_is_failed_not_retry(self, engine):
        cb = CircuitBreaker()

        def deliver():
            return False

        deliver_with_retry(
            dvr_id="dvr1",
            channel="apprise",
            event_type="test",
            provider_type="Apprise",
            channel_id="apprise",
            payload_size=50,
            deliver_fn=deliver,
            circuit_breaker=cb,
            db_engine=engine,
            with_retry=False,
        )
        rows, total = query_delivery_log(engine, status="failed")
        assert total == 1
        retry_rows, retry_total = query_delivery_log(engine, status="retry")
        assert retry_total == 0

    def test_circuit_open_written_to_db(self, engine):
        cb = CircuitBreaker()
        for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
            cb.record_failure("dvr1", "apprise")

        def deliver():
            return True

        deliver_with_retry(
            dvr_id="dvr1",
            channel="apprise",
            event_type="test",
            provider_type="Apprise",
            channel_id="apprise",
            payload_size=50,
            deliver_fn=deliver,
            circuit_breaker=cb,
            db_engine=engine,
            with_retry=True,
        )
        rows, total = query_delivery_log(engine, status="circuit_open")
        assert total == 1

    def test_pagination(self, engine):
        for i in range(5):
            insert_delivery_record(
                engine,
                dvr_id="dvr1",
                event_type="test",
                channel="apprise",
                provider_type="Apprise",
                channel_id="apprise",
                status="sent",
                retry_count=i,
                payload_size=10,
            )
        rows, total = query_delivery_log(engine, offset=2, limit=2)
        assert total == 5
        assert len(rows) == 2

    def test_delivery_schema_migration_is_idempotent(self, engine):
        migrate_delivery_schema(engine)
        migrate_delivery_schema(engine)
        insert_delivery_record(
            engine,
            dvr_id="dvr1",
            event_type="test",
            channel="apprise",
            provider_type="Apprise",
            channel_id="apprise",
            status="sent",
            retry_count=0,
            payload_size=10,
        )
        rows, total = query_delivery_log(engine)
        assert total == 1

    def test_delivery_schema_migration_creates_delivered_at_index(self, engine):
        migrate_delivery_schema(engine)
        indexes = {
            i["name"] for i in sa_inspect(engine).get_indexes("notification_delivery")
        }
        assert "ix_notification_delivery_delivered_at" in indexes


class TestNotificationManagerIntegration:
    def test_send_notification_success_persists_sent(self, engine):
        from core.notifications.notification import NotificationManager

        nm = NotificationManager(db_engine=engine)
        mock_provider = MagicMock()
        mock_provider.PROVIDER_TYPE = "Apprise"
        mock_provider.is_configured.return_value = True
        mock_provider.send_notification.return_value = True
        nm.register_provider(mock_provider)

        result = nm.send_notification("Test", "msg", dvr_id="dvr1", event_type="test")
        assert result is True
        rows, total = query_delivery_log(engine, status="sent")
        assert total == 1

    def test_circuit_opens_after_five_failures(self, engine):
        from core.notifications.notification import NotificationManager

        nm = NotificationManager(db_engine=engine)
        mock_provider = MagicMock()
        mock_provider.PROVIDER_TYPE = "Apprise"
        mock_provider.is_configured.return_value = True
        mock_provider.send_notification.return_value = False
        nm.register_provider(mock_provider)

        with patch("core.notifications.delivery.time.sleep"):
            for _ in range(CircuitBreaker.FAILURE_THRESHOLD):
                nm.send_notification("T", "m", dvr_id="dvr1", event_type="test")

        assert nm.circuit_breaker.is_open("dvr1", "apprise")

    def test_webhook_delivery_persists_sent(self, engine):
        from core.notifications.notification import NotificationManager

        nm = NotificationManager(db_engine=engine)
        mock_wm = MagicMock()
        mock_wm.is_configured.return_value = True
        mock_wm.send_notification.return_value = True
        nm.register_webhook_manager(mock_wm)

        nm.send_notification("T", "m", dvr_id="dvr1", event_type="watching_channel")
        rows, total = query_delivery_log(engine, channel="webhook")
        assert total == 1
        assert rows[0].status == "sent"

    def test_send_notification_async_provider_offloads_and_persists_sent(self, engine):
        from core.notifications.notification import NotificationManager

        nm = NotificationManager(db_engine=engine)
        mock_provider = MagicMock()
        mock_provider.PROVIDER_TYPE = "Apprise"
        mock_provider.is_configured.return_value = True
        mock_provider.send_notification.return_value = True
        nm.register_provider(mock_provider)
        offloaded = []

        async def run_in_thread(func, *args, **kwargs):
            offloaded.append(func)
            return func(*args, **kwargs)

        async def run():
            with patch(
                "core.notifications.notification.asyncio.to_thread", run_in_thread
            ):
                return await nm.send_notification_async(
                    "Async Test",
                    "msg",
                    dvr_id="dvr1",
                    event_type="watching_channel",
                    activity_event_id="activity-1",
                )

        result = asyncio.run(run())

        assert result is True
        assert offloaded == [deliver_with_retry]
        kwargs = mock_provider.send_notification.call_args.kwargs
        assert kwargs["allowed_apprise_destinations"] == set(APPRISE_DEST_KEYS)
        rows, total = query_delivery_log(engine, status="sent")
        assert total == 1
        assert rows[0].channel == "apprise"
        assert rows[0].activity_event_id == "activity-1"

    def test_send_notification_async_webhook_offloads_and_persists_sent(self, engine):
        from core.notifications.notification import NotificationManager

        nm = NotificationManager(db_engine=engine)
        mock_wm = MagicMock()
        mock_wm.is_configured.return_value = True
        mock_wm.send_notification.return_value = True
        nm.register_webhook_manager(mock_wm)
        offloaded = []

        async def run_in_thread(func, *args, **kwargs):
            offloaded.append(func)
            return func(*args, **kwargs)

        async def run():
            with patch(
                "core.notifications.notification.asyncio.to_thread", run_in_thread
            ):
                return await nm.send_notification_async(
                    "Webhook Test", "msg", dvr_id="dvr1", event_type="disk_alert"
                )

        result = asyncio.run(run())

        assert result is True
        assert offloaded == [deliver_with_retry]
        mock_wm.send_notification.assert_called_once_with(
            "Webhook Test", "msg", dvr_id="dvr1", event_type="disk_alert"
        )
        rows, total = query_delivery_log(engine, channel="webhook")
        assert total == 1
        assert rows[0].status == "sent"

    def test_send_notification_async_honors_routing_and_allowed_destinations(
        self, engine
    ):
        from core.notifications.notification import NotificationManager

        nm = NotificationManager(db_engine=engine)
        mock_provider = MagicMock()
        mock_provider.PROVIDER_TYPE = "Apprise"
        mock_provider.is_configured.return_value = True
        mock_provider.send_notification.return_value = True
        nm.register_provider(mock_provider)
        mock_wm = MagicMock()
        mock_wm.is_configured.return_value = True
        mock_wm.send_notification.return_value = True
        nm.register_webhook_manager(mock_wm)
        routing = {
            "dvr1": {
                "disk_alert": {
                    "pushover": False,
                    "discord": True,
                    "webhook": False,
                }
            }
        }

        async def run_in_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        async def run():
            with (
                patch(
                    "core.notifications.notification._load_routing_config",
                    return_value=routing,
                ),
                patch(
                    "core.notifications.notification.asyncio.to_thread", run_in_thread
                ),
            ):
                return await nm.send_notification_async(
                    "Routed", "msg", dvr_id="dvr1", event_type="disk_alert"
                )

        result = asyncio.run(run())

        assert result is True
        kwargs = mock_provider.send_notification.call_args.kwargs
        allowed = kwargs["allowed_apprise_destinations"]
        assert "pushover" not in allowed
        assert "discord" in allowed
        mock_wm.send_notification.assert_not_called()
        rows, total = query_delivery_log(engine)
        assert total == 1
        assert rows[0].channel == "apprise"

    def test_send_notification_async_failure_persists_retry_and_failed(self, engine):
        from core.notifications.notification import NotificationManager

        nm = NotificationManager(db_engine=engine)
        mock_provider = MagicMock()
        mock_provider.PROVIDER_TYPE = "Apprise"
        mock_provider.is_configured.return_value = True
        mock_provider.send_notification.return_value = False
        nm.register_provider(mock_provider)

        async def run_in_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        async def run():
            with (
                patch("core.notifications.delivery.time.sleep"),
                patch(
                    "core.notifications.notification.asyncio.to_thread", run_in_thread
                ),
            ):
                return await nm.send_notification_async(
                    "Failing", "msg", dvr_id="dvr1", event_type="disk_alert"
                )

        result = asyncio.run(run())

        assert result is False
        assert (
            mock_provider.send_notification.call_count
            == CircuitBreaker.FAILURE_THRESHOLD
        )
        circuit_rows, circuit_total = query_delivery_log(engine, status="circuit_open")
        retry_rows, retry_total = query_delivery_log(engine, status="retry")
        assert circuit_total == 1
        assert retry_total == CircuitBreaker.FAILURE_THRESHOLD
        assert circuit_rows[0].channel == "apprise"
        assert {row.status for row in retry_rows} == {"retry"}


class TestNotificationLogEndpoint:
    def test_notification_log_endpoint_filters_and_serializes_rows(
        self, notification_log_client, endpoint_engine
    ):
        now = datetime.now(timezone.utc)
        with get_session(endpoint_engine) as session:
            session.add(
                NotificationDelivery(
                    dvr_id="dvr-main",
                    activity_event_id="activity-1",
                    provider_type="Apprise",
                    channel_id="email",
                    channel="apprise",
                    event_type="watching_channel",
                    status="sent",
                    retry_count=1,
                    payload_size=256,
                    delivered=True,
                    delivered_at=now - timedelta(minutes=5),
                )
            )
            session.add(
                NotificationDelivery(
                    dvr_id="dvr-other",
                    provider_type="webhook",
                    channel_id="webhook",
                    channel="webhook",
                    event_type="disk_alert",
                    status="failed",
                    retry_count=0,
                    payload_size=128,
                    delivered=False,
                    delivered_at=now - timedelta(hours=2),
                )
            )
            session.commit()

        response = notification_log_client.get(
            "/api/v1/notification-log",
            params={
                "dvr_id": "dvr-main",
                "channel": "apprise",
                "status": "sent",
                "since": (now - timedelta(hours=1)).isoformat(),
                "until": (now + timedelta(minutes=1)).isoformat(),
                "offset": 0,
                "limit": 25,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["offset"] == 0
        assert body["limit"] == 25
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["dvr_id"] == "dvr-main"
        assert item["activity_event_id"] == "activity-1"
        assert item["provider_type"] == "Apprise"
        assert item["channel_id"] == "email"
        assert item["channel"] == "apprise"
        assert item["event_type"] == "watching_channel"
        assert item["status"] == "sent"
        assert item["retry_count"] == 1
        assert item["payload_size"] == 256
        assert item["sent_at"]

    def test_notification_log_endpoint_returns_empty_when_storage_unavailable(
        self, tmp_path
    ):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"api_key":"test-key","tz":"UTC"}', encoding="utf-8")
        history_file = tmp_path / "activity_history.json"
        history_file.write_text("[]", encoding="utf-8")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
            patch("ui.backend.main.CW_DISABLE_AUTH", True),
            patch("ui.backend.main.HISTORY_FILE", history_file),
            patch("ui.backend.main._get_activity_db_engine", return_value=None),
        ):
            from ui.backend.main import app

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/notification-log?offset=3&limit=10")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "offset": 3, "limit": 10}
