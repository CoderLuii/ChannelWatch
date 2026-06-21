import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import Engine, func, and_, text
from sqlmodel import Session, select

from .models import NotificationDelivery

log = logging.getLogger(__name__)

_NEW_COLUMNS = [
    ("event_type", "TEXT NOT NULL DEFAULT ''"),
    ("channel", "TEXT NOT NULL DEFAULT ''"),
    ("status", "TEXT NOT NULL DEFAULT 'sent'"),
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("payload_size", "INTEGER NOT NULL DEFAULT 0"),
]

_NEW_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_notification_delivery_delivered_at ON notification_delivery (delivered_at)",
    "CREATE INDEX IF NOT EXISTS ix_notification_delivery_dvr_channel ON notification_delivery (dvr_id, channel)",
    "CREATE INDEX IF NOT EXISTS ix_notification_delivery_status ON notification_delivery (status)",
]


def migrate_delivery_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        existing = {
            row[1]
            for row in conn.execute(
                text("PRAGMA table_info(notification_delivery)")
            ).fetchall()
        }
        for col_name, col_def in _NEW_COLUMNS:
            if col_name not in existing:
                conn.execute(
                    text(
                        f"ALTER TABLE notification_delivery ADD COLUMN {col_name} {col_def}"
                    )
                )
                log.info("Added column %s to notification_delivery", col_name)
        for idx_sql in _NEW_INDEXES:
            conn.execute(text(idx_sql))
        conn.commit()


def insert_delivery_record(
    engine: Engine,
    *,
    dvr_id: str,
    event_type: str,
    channel: str,
    provider_type: str,
    channel_id: str,
    status: str,
    retry_count: int,
    payload_size: int,
    error_message: Optional[str] = None,
    activity_event_id: Optional[str] = None,
) -> None:
    try:
        with Session(engine) as session:
            record = NotificationDelivery(
                dvr_id=dvr_id,
                activity_event_id=activity_event_id,
                provider_type=provider_type,
                channel_id=channel_id,
                delivered=(status == "sent"),
                error_message=error_message,
                event_type=event_type,
                channel=channel,
                status=status,
                retry_count=retry_count,
                payload_size=payload_size,
            )
            session.add(record)
            session.commit()
    except Exception as exc:
        log.warning("Failed to persist delivery record: %s", exc)


def query_delivery_log(
    engine: Engine,
    *,
    dvr_id: Optional[str] = None,
    channel: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    offset: int = 0,
    limit: int = 50,
) -> Tuple[List[NotificationDelivery], int]:
    with Session(engine) as session:
        conditions = []
        if dvr_id:
            conditions.append(NotificationDelivery.dvr_id == dvr_id)
        if channel:
            conditions.append(NotificationDelivery.channel == channel)
        if status:
            conditions.append(NotificationDelivery.status == status)
        if since:
            conditions.append(NotificationDelivery.delivered_at >= since)
        if until:
            conditions.append(NotificationDelivery.delivered_at <= until)

        base_stmt = select(NotificationDelivery)
        count_stmt = select(func.count()).select_from(NotificationDelivery)
        if conditions:
            where = and_(*conditions)
            base_stmt = base_stmt.where(where)
            count_stmt = count_stmt.where(where)

        total = session.exec(count_stmt).one()
        rows = session.exec(
            base_stmt.order_by(NotificationDelivery.delivered_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), int(total)
