import json
import logging
import os
import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from core.helpers.atomic_io import fsync_directory
from .database import create_db_engine, create_all_tables, get_session
from .models import ActivityEvent

log = logging.getLogger(__name__)

DEFAULT_JSON_PATH = "/config/activity_history.json"
DEFAULT_DB_URL = "sqlite:////config/channelwatch.db"


def _parse_timestamp(ts: object) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    raw = str(ts)
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        log.warning("Cannot parse timestamp %r; falling back to epoch", raw)
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _extra_to_str(extra: object) -> str:
    if isinstance(extra, dict):
        return json.dumps(extra)
    if isinstance(extra, str):
        try:
            json.loads(extra)
            return extra
        except (ValueError, TypeError):
            return json.dumps({"raw": extra})
    return "{}"


def _json_row_to_model(row: dict) -> Optional[ActivityEvent]:
    row_id = (row.get("id") or "").strip()
    # JSON shape uses "type"; ORM column is "event_type".
    event_type = (row.get("type") or row.get("event_type") or "").strip()
    if not row_id or not event_type:
        return None

    return ActivityEvent(
        id=row_id,
        dvr_id=row.get("dvr_id") or "",
        event_type=event_type,
        title=row.get("title") or "",
        message=row.get("message") or "",
        timestamp=_parse_timestamp(row.get("timestamp", datetime.now(timezone.utc))),
        icon=row.get("icon") or "bell",
        channel_name=row.get("channel_name") or "",
        channel_number=row.get("channel_number") or "",
        device_name=row.get("device_name") or "",
        device_ip=row.get("device_ip") or "",
        program_title=row.get("program_title") or "",
        image_url=row.get("image_url") or "",
        stream_source=row.get("stream_source") or "",
        dvr_name=row.get("dvr_name") or "",
        extra=_extra_to_str(row.get("extra", {})),
        is_test=bool(row.get("is_test", False)),
    )


def migrate_activity_history(
    json_path: str = DEFAULT_JSON_PATH,
    db_url: str = DEFAULT_DB_URL,
    *,
    batch_size: int = 500,
) -> dict:
    result: dict[str, int] = {
        "total": 0,
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
    }

    if not os.path.exists(json_path):
        log.info("No activity_history.json at %s; nothing to migrate", json_path)
        return result

    # -- Load source JSON --------------------------------------------------
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to load activity history JSON: %s", exc)
        result["errors"] += 1
        return result

    if not isinstance(raw, list):
        log.error(
            "activity_history.json is not a JSON array (got %s); aborting",
            type(raw).__name__,
        )
        result["errors"] += 1
        return result

    result["total"] = len(raw)
    if result["total"] == 0:
        log.info("activity_history.json is empty; nothing to migrate")
        return result

    db_path: Path | None = None
    new_db_path: Path | None = None
    if db_url.startswith("sqlite:///") and not db_url.endswith(":memory:"):
        db_path = Path(db_url.replace("sqlite:///", "", 1))
        new_db_path = db_path.with_name(f"{db_path.name}.new")
        if new_db_path.exists():
            new_db_path.unlink()
        if db_path.exists():
            shutil.copy2(db_path, new_db_path)
        engine = create_db_engine(f"sqlite:///{new_db_path}", poolclass=NullPool)
    else:
        engine = create_db_engine(db_url)
    create_all_tables(engine)

    # Fetch all existing primary keys in one query so we can skip them
    # without touching the DB on every row.
    with engine.connect() as conn:
        existing_ids: set[str] = {
            row[0] for row in conn.execute(text("SELECT id FROM activity_event"))
        }

    # -- Batch insert ------------------------------------------------------
    batch: list[ActivityEvent] = []

    def _flush() -> None:
        if not batch:
            return
        with get_session(engine) as session:
            for model in batch:
                session.add(model)
            session.commit()
        batch.clear()

    for raw_row in raw:
        if not isinstance(raw_row, dict):
            log.debug("Skipping non-dict entry: %r", raw_row)
            result["errors"] += 1
            continue

        model = _json_row_to_model(raw_row)
        if model is None:
            log.debug("Skipping row missing id/type: %r", raw_row)
            result["errors"] += 1
            continue

        if model.id in existing_ids:
            result["skipped"] += 1
            continue

        # Track within-run to guard against duplicate ids in the source JSON.
        existing_ids.add(model.id)
        batch.append(model)
        result["inserted"] += 1

        if len(batch) >= batch_size:
            _flush()

    _flush()
    engine.dispose()

    if new_db_path is not None and db_path is not None:
        conn = sqlite3.connect(new_db_path)
        try:
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            close = getattr(conn, "close", None)
            if callable(close):
                close()
        if not integrity_row or integrity_row[0] != "ok":
            try:
                new_db_path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning(
                    "Failed to discard migrated database %s after integrity failure: %s",
                    new_db_path,
                    exc,
                )
            raise RuntimeError(
                f"Integrity check failed for migrated database {new_db_path}; discarded the new database and preserved the existing DB: {integrity_row!r}"
            )
        os.replace(new_db_path, db_path)
        fsync_directory(db_path.parent)

    log.info(
        "Activity history migration complete: "
        "total=%d inserted=%d skipped=%d errors=%d",
        result["total"],
        result["inserted"],
        result["skipped"],
        result["errors"],
    )
    return result
