import logging
import time
import threading
from datetime import datetime, timezone, timedelta

from sqlalchemy import Engine, text

from .database import get_session

log = logging.getLogger(__name__)

_DEFAULT_RETENTION_DAYS = 90
_MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60


def prune_old_events(
    engine: Engine, retention_days: int = _DEFAULT_RETENTION_DAYS
) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    activity_deleted = 0
    delivery_deleted = 0

    with get_session(engine) as session:
        result = session.execute(
            text("DELETE FROM activity_event WHERE timestamp < :cutoff"),
            {"cutoff": cutoff.isoformat()},
        )
        activity_deleted = result.rowcount or 0

        result = session.execute(
            text("DELETE FROM notification_delivery WHERE delivered_at < :cutoff"),
            {"cutoff": cutoff.isoformat()},
        )
        delivery_deleted = result.rowcount or 0

        session.commit()

    log.info(
        "Retention pruner: deleted %d activity_event rows and %d notification_delivery rows older than %d days",
        activity_deleted,
        delivery_deleted,
        retention_days,
    )
    return {
        "activity_events_deleted": activity_deleted,
        "notification_deliveries_deleted": delivery_deleted,
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
    }


def vacuum_db(engine: Engine) -> None:
    raw_conn = engine.raw_connection()
    try:
        raw_conn.execute("VACUUM")
        log.info("SQLite VACUUM completed")
    finally:
        raw_conn.close()


def run_nightly_maintenance(
    engine: Engine, retention_days: int = _DEFAULT_RETENTION_DAYS
) -> dict:
    prune_result = prune_old_events(engine, retention_days=retention_days)
    vacuum_db(engine)
    return prune_result


def start_maintenance_thread(
    get_engine_fn,
    *,
    retention_days: int = _DEFAULT_RETENTION_DAYS,
    interval_seconds: int = _MAINTENANCE_INTERVAL_SECONDS,
) -> threading.Thread:
    def _loop() -> None:
        time.sleep(interval_seconds)
        while True:
            try:
                engine = get_engine_fn()
                if engine is not None:
                    run_nightly_maintenance(engine, retention_days=retention_days)
            except Exception as exc:
                log.warning("Nightly maintenance error: %s", exc)
            time.sleep(interval_seconds)

    thread = threading.Thread(target=_loop, name="storage-maintenance", daemon=True)
    thread.start()
    log.info(
        "Storage maintenance thread started (retention=%dd, interval=%dh)",
        retention_days,
        interval_seconds // 3600,
    )
    return thread
