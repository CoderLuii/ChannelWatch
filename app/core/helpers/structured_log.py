"""Structured JSON formatter and context helpers for LOG_FORMAT=json mode."""

import json
import logging
import contextvars
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional, Tuple

_ctx_dvr_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "log_dvr_id", default=None
)
_ctx_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "log_request_id", default=None
)
_ctx_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "log_user_id", default=None
)

_CONTEXT_VARS: Dict[str, contextvars.ContextVar] = {
    "dvr_id": _ctx_dvr_id,
    "request_id": _ctx_request_id,
    "user_id": _ctx_user_id,
}

_CONTEXT_KEYS = ("dvr_id", "request_id", "user_id")


def set_log_context(
    *,
    dvr_id: Optional[str] = None,
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Tuple:
    """Set per-task/per-thread context fields; returns reset tokens for clear_log_context."""
    return (
        _ctx_dvr_id.set(dvr_id),
        _ctx_request_id.set(request_id),
        _ctx_user_id.set(user_id),
    )


def clear_log_context(tokens: Optional[Tuple] = None) -> None:
    """Reset context vars to None, or restore to prior state via tokens from set_log_context."""
    if tokens is not None:
        _ctx_dvr_id.reset(tokens[0])
        _ctx_request_id.reset(tokens[1])
        _ctx_user_id.reset(tokens[2])
    else:
        _ctx_dvr_id.set(None)
        _ctx_request_id.set(None)
        _ctx_user_id.set(None)


@contextmanager
def log_context(**kwargs: Any) -> Generator[None, None, None]:
    """Context manager: set log context fields and restore them on exit."""
    tokens = set_log_context(**{k: v for k, v in kwargs.items() if k in _CONTEXT_VARS})
    try:
        yield
    finally:
        clear_log_context(tokens)


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON with timestamp/level/module/message plus context fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }

        # contextvars first (lower priority)
        for key, var in _CONTEXT_VARS.items():
            val = var.get()
            if val is not None:
                payload[key] = val

        # extra= at call site overrides contextvars for the same key
        for key in _CONTEXT_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)
