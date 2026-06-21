import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

RETRY_DELAYS = [2, 4, 8, 16, 32]

_db_engine: Any = None
_db_engine_lock = threading.Lock()


def _get_delivery_db_engine() -> Any:
    global _db_engine
    if _db_engine is not None:
        return _db_engine
    with _db_engine_lock:
        if _db_engine is not None:
            return _db_engine
        db_path = os.environ.get("CHANNELWATCH_DB", "/config/channelwatch.db")
        if not os.path.exists(db_path):
            return None
        try:
            from ..storage.database import create_db_engine
            from ..storage.delivery_queries import migrate_delivery_schema

            engine = create_db_engine(f"sqlite:///{db_path}")
            migrate_delivery_schema(engine)
            _db_engine = engine
            return _db_engine
        except Exception as exc:
            log.warning("Could not initialise delivery DB engine: %s", exc)
            return None


class CircuitBreaker:
    FAILURE_THRESHOLD = 5
    OPEN_DURATION_SECONDS = 300

    def __init__(self) -> None:
        self._state: dict = {}
        self._lock = threading.Lock()

    def _key(self, dvr_id: str, channel: str) -> tuple:
        return (dvr_id or "", channel or "")

    def is_open(self, dvr_id: str, channel: str) -> bool:
        key = self._key(dvr_id, channel)
        with self._lock:
            state = self._state.get(key)
            if not state or state.get("opened_at") is None:
                return False
            if time.monotonic() - state["opened_at"] > self.OPEN_DURATION_SECONDS:
                state["opened_at"] = None
                state["failure_count"] = 0
                return False
            return True

    def record_failure(self, dvr_id: str, channel: str) -> bool:
        key = self._key(dvr_id, channel)
        with self._lock:
            state = self._state.setdefault(key, {"failure_count": 0, "opened_at": None})
            state["failure_count"] = state.get("failure_count", 0) + 1
            if (
                state.get("opened_at") is None
                and state["failure_count"] >= self.FAILURE_THRESHOLD
            ):
                state["opened_at"] = time.monotonic()
                return True
            return False

    def record_success(self, dvr_id: str, channel: str) -> None:
        key = self._key(dvr_id, channel)
        with self._lock:
            if key in self._state:
                self._state[key] = {"failure_count": 0, "opened_at": None}

    def failure_count(self, dvr_id: str, channel: str) -> int:
        key = self._key(dvr_id, channel)
        with self._lock:
            return self._state.get(key, {}).get("failure_count", 0)

    def opened_at(self, dvr_id: str, channel: str) -> Optional[float]:
        key = self._key(dvr_id, channel)
        with self._lock:
            return self._state.get(key, {}).get("opened_at")


def estimate_payload_size(title: str, message: str, **kwargs: Any) -> int:
    try:
        payload = {"title": title, "message": message}
        for k in ("image_url", "dvr_id", "dvr_name", "event_type"):
            v = kwargs.get(k)
            if v:
                payload[k] = v
        return len(json.dumps(payload).encode("utf-8"))
    except Exception:
        return 0


def _persist(
    *,
    dvr_id: str,
    event_type: str,
    channel: str,
    provider_type: str,
    channel_id: str,
    status: str,
    retry_count: int,
    payload_size: int,
    error_message: Optional[str],
    activity_event_id: Optional[str] = None,
    db_engine: Any = None,
) -> None:
    engine = db_engine if db_engine is not None else _get_delivery_db_engine()
    if engine is None:
        return
    try:
        from ..storage.delivery_queries import insert_delivery_record

        insert_delivery_record(
            engine,
            dvr_id=dvr_id,
            event_type=event_type,
            channel=channel,
            provider_type=provider_type,
            channel_id=channel_id,
            status=status,
            retry_count=retry_count,
            payload_size=payload_size,
            error_message=error_message,
            activity_event_id=activity_event_id,
        )
    except Exception as exc:
        log.warning("Failed to persist delivery record: %s", exc)


def deliver_with_retry(
    *,
    dvr_id: str,
    channel: str,
    event_type: str,
    provider_type: str,
    channel_id: str,
    payload_size: int,
    deliver_fn: Callable[[], bool],
    circuit_breaker: CircuitBreaker,
    db_engine: Any = None,
    activity_event_id: Optional[str] = None,
    with_retry: bool = True,
) -> bool:
    delays = RETRY_DELAYS if with_retry else []
    attempts = [0] + delays
    last_index = len(attempts) - 1

    for retry_count, delay in enumerate(attempts):
        if circuit_breaker.is_open(dvr_id, channel):
            _persist(
                dvr_id=dvr_id,
                event_type=event_type,
                channel=channel,
                provider_type=provider_type,
                channel_id=channel_id,
                status="circuit_open",
                retry_count=retry_count,
                payload_size=payload_size,
                error_message="Circuit open — delivery skipped",
                activity_event_id=activity_event_id,
                db_engine=db_engine,
            )
            log.debug(
                "Circuit open for (%s, %s): skipping delivery attempt %d",
                dvr_id,
                channel,
                retry_count,
            )
            return False

        if delay > 0:
            time.sleep(delay)

        error_msg: Optional[str] = None
        success = False
        try:
            success = bool(deliver_fn())
        except Exception as exc:
            error_msg = str(exc)
            log.warning(
                "Delivery exception (%s/%s attempt %d): %s",
                dvr_id,
                channel,
                retry_count,
                exc,
            )

        if success:
            circuit_breaker.record_success(dvr_id, channel)
            _persist(
                dvr_id=dvr_id,
                event_type=event_type,
                channel=channel,
                provider_type=provider_type,
                channel_id=channel_id,
                status="sent",
                retry_count=retry_count,
                payload_size=payload_size,
                error_message=None,
                activity_event_id=activity_event_id,
                db_engine=db_engine,
            )
            return True

        just_opened = circuit_breaker.record_failure(dvr_id, channel)
        if just_opened:
            log.warning(
                "Circuit opened for (%s, %s) after %d failures",
                dvr_id,
                channel,
                circuit_breaker.FAILURE_THRESHOLD,
            )

        is_last = retry_count == last_index
        _persist(
            dvr_id=dvr_id,
            event_type=event_type,
            channel=channel,
            provider_type=provider_type,
            channel_id=channel_id,
            status="failed" if is_last else "retry",
            retry_count=retry_count,
            payload_size=payload_size,
            error_message=error_msg,
            activity_event_id=activity_event_id,
            db_engine=db_engine,
        )

    return False
