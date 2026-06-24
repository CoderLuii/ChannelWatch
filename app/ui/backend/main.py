# IMPORTS
import http.client
import os
import signal
import ipaddress
import socket
from collections import deque
from contextlib import asynccontextmanager
from email.utils import format_datetime
from fastapi import APIRouter as _APIRouter
from fastapi import FastAPI, HTTPException, Response, Request, Depends, Query
from fastapi import UploadFile, File as FastAPIFile
from .error_catalog import ErrorCode, catalog_entry, structured_error
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import UploadFile as StarletteUploadFile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
import asyncio
from pydantic import BaseModel
from . import config as backend_config
from .config import load_settings, save_settings
from .support_report import (
    DEFAULT_REPORT_ENDPOINT,
    DEFAULT_REPORT_MAX_ATTACHMENT_BYTES,
    DEFAULT_REPORT_MAX_BYTES,
    DEFAULT_REPORT_MAX_SCREENSHOTS,
    DEFAULT_REPORT_MAX_TOTAL_ATTACHMENT_BYTES,
    DEFAULT_REPORT_PORTAL_URL,
    REPORT_ALLOWED_ATTACHMENT_TYPES,
    ReportAttachmentInvalid,
    ReportAttachmentSummary,
    ReportAttachmentTooLarge,
    ReportConfigResponse,
    ReportProblemPayload,
    ReportPayloadInvalid,
    ReportPayloadTooLarge,
    ReportPreviewResponse,
    build_offline_report_package,
    parse_report_mode,
    parse_report_payload,
    render_report_preview,
    summarize_report_attachment,
    validate_attachment_limits,
)
from core.helpers.config import CONFIG_DIR as _CORE_CONFIG_DIR, ConfigLoadError
from core.helpers.atomic_io import atomic_write_json
from core.helpers.dvr_connection import build_dvr_base_url
from core.helpers.trusted_destinations import preview_notification_destination_safety
from core.helpers.soft_delete_manager import (
    hard_delete_dvr as _hard_delete_dvr,
    purge_expired_dvrs as _purge_expired_dvrs,
    restore_dvr as _restore_dvr,
    soft_delete_dvr as _soft_delete_dvr,
)
from core.helpers.url_validator import is_safe_url, redact_url
from core.watchdog import load_watchdog_snapshot, summarize_enabled_dvrs
from .schemas import (
    AppSettings,
    WebhookSettings,
    AuthMode,
    EffectiveAuthMode,
    SecurityMode,
    SecurityFeedsStatus,
    SecurityStatusResponse,
    SetupStatusResponse,
    NotificationDestinationSafetyRequest,
    NotificationDestinationSafetyResponse,
)
import secrets
import uuid
import json
from datetime import datetime, timedelta, timezone as _tz
import xmlrpc.client
import httpx
from typing import Optional, List, Dict, Any, cast
import time
import threading
import re
import logging
from contextvars import ContextVar
from xml.sax.saxutils import escape as xml_escape

# LOGGING SETUP
log = logging.getLogger(__name__)


def _sqlite_url_for_path(path: Path) -> str:
    return f"sqlite:///{path}"


def _content_disposition_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe or "download"


class StaticFileFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if (
                record.args
                and isinstance(record.args, (tuple, list))
                and len(record.args) >= 3
            ):
                arg_to_check = record.args[1]
                if isinstance(arg_to_check, str):
                    request_path: str = arg_to_check
                    if request_path.startswith("/_next/static/"):
                        return False
        except (IndexError, TypeError):
            pass
        return True


access_logger = logging.getLogger("uvicorn.access")
access_logger.addFilter(StaticFileFilter())

try:
    from core.helpers.structured_log import set_log_context, clear_log_context

    _STRUCTURED_LOG_AVAILABLE = True
except ImportError:
    _STRUCTURED_LOG_AVAILABLE = False

    def set_log_context(**kwargs):
        return (None, None, None)

    def clear_log_context(tokens=None):
        pass


# CORE APP INTEGRATION
try:
    log.debug("Attempting imports from webui/main.py (PYTHONPATH=/app)")
    from core import __version__, __app_name__
    from core.helpers.config import get_settings as _get_core_settings_sync

    async def get_core_settings() -> Optional[Any]:
        try:
            return _get_core_settings_sync()
        except TypeError:
            return None

    log.debug("Imported core.helpers.config")
    from core.helpers.initialize import initialize_notifications, initialize_alerts

    log.debug("Imported core.helpers.initialize")
    from core.diagnostics import run_test

    log.debug("Imported core.diagnostics")
    CORE_APP_AVAILABLE = True
    log.info("Core app components loaded successfully for testing.")
except ImportError as e:
    log.error(f"Specific ImportError: {e}")
    log.warning(
        f"Could not import core app components for testing: {e}. Test endpoints will be disabled."
    )
    CORE_APP_AVAILABLE = False

    async def get_core_settings() -> Optional[Any]:
        return None

    def initialize_notifications(settings: Any, test_mode: bool) -> Optional[Any]:
        return None

    def initialize_alerts(
        notification_manager: Any, settings: Any, test_mode: bool
    ) -> Optional[Any]:
        return None

    def run_test(
        test_name: str,
        host: str,
        port: int,
        alert_manager: Optional[Any],
        duration: int = 30,
    ) -> bool:
        return False

    __version__ = "N/A"
    __app_name__ = "ChannelWatch"

# DVR VERSION COMPATIBILITY
try:
    from core.dvr_client import (
        check_version_compatibility as _check_dvr_version_compat,
        MIN_TESTED_DVR_VERSION,
        MAX_TESTED_DVR_VERSION,
    )
except ImportError:
    MIN_TESTED_DVR_VERSION = "2024.01.01"
    MAX_TESTED_DVR_VERSION = "2026.04.20"

    def _check_dvr_version_compat(version_str: str) -> dict[str, Any]:
        if not version_str or not isinstance(version_str, str):
            return {
                "version": version_str,
                "parsed": None,
                "compatible": None,
                "warning": f"DVR version '{version_str}' could not be parsed; compatibility is unknown.",
            }
        parts = version_str.strip().split(".")
        if len(parts) < 3:
            return {
                "version": version_str,
                "parsed": None,
                "compatible": None,
                "warning": f"DVR version '{version_str}' could not be parsed; compatibility is unknown.",
            }
        try:
            parsed = (int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return {
                "version": version_str,
                "parsed": None,
                "compatible": None,
                "warning": f"DVR version '{version_str}' could not be parsed; compatibility is unknown.",
            }
        _min = tuple(int(x) for x in MIN_TESTED_DVR_VERSION.split("."))
        _max = tuple(int(x) for x in MAX_TESTED_DVR_VERSION.split("."))
        if parsed < _min:
            return {
                "version": version_str,
                "parsed": parsed,
                "compatible": False,
                "warning": f"DVR version {version_str} is below the tested range ({MIN_TESTED_DVR_VERSION} – {MAX_TESTED_DVR_VERSION}). Some ChannelWatch features may not work correctly.",
            }
        if parsed > _max:
            return {
                "version": version_str,
                "parsed": parsed,
                "compatible": False,
                "warning": f"DVR version {version_str} is above the tested range ({MIN_TESTED_DVR_VERSION} – {MAX_TESTED_DVR_VERSION}). ChannelWatch has not been tested with this version.",
            }
        return {
            "version": version_str,
            "parsed": parsed,
            "compatible": True,
            "warning": None,
        }


# DISCOVERY
try:
    from core.helpers.discovery import (
        scan_for_dvrs as _scan_for_dvrs,
        build_scan_response as _build_scan_response,
    )

    _DISCOVERY_AVAILABLE = True
except ImportError:

    def _scan_for_dvrs(
        timeout: float = 5.0, service_type: str = "_channels_dvr._tcp.local."
    ):  # type: ignore[misc]
        return []

    def _build_scan_response(servers, existing_hosts=None):  # type: ignore[misc]
        return {
            "servers": [],
            "manual_add_available": True,
            "message": "Discovery module unavailable.",
        }

    _DISCOVERY_AVAILABLE = False

# SHARED HTTP CLIENT
_dvr_http_client = httpx.AsyncClient(timeout=30.0)


# APP INITIALIZATION
@asynccontextmanager
async def lifespan(app: FastAPI):
    run_startup_initialization()
    yield
    await _dvr_http_client.aclose()


app = FastAPI(title="ChannelWatch UI Backend", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    _exc_extra: Dict[str, Any] = {}
    _uid = getattr(request.state, "auth_user_id", None)
    if _uid is not None:
        _exc_extra["user_id"] = str(_uid)
    log.exception(
        "Unhandled backend error during %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
        extra=_exc_extra,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


WEBUI_DIR = Path(__file__).resolve().parent
_STATIC_UI_DIR_OVERRIDE = os.environ.get("CW_STATIC_UI_DIR", "").strip()
STATIC_UI_DIR = (
    Path(_STATIC_UI_DIR_OVERRIDE).expanduser()
    if _STATIC_UI_DIR_OVERRIDE
    else WEBUI_DIR / "static_ui"
)

# AUTH CONFIGURATION
CW_DISABLE_AUTH = os.environ.get("CW_DISABLE_AUTH", "").lower() == "true"
API_KEY_CACHE: Optional[str] = None
RBAC_ENABLED: bool = False
AUTH_MODE_CACHE: Optional[EffectiveAuthMode] = None
API_KEY_FALLBACK_ALLOWED: bool = False
_auth_settings_signature: Optional[tuple[str, int, int]] = None
_AUTH_STATE_LOCK = threading.Lock()
_auth_db_engine = None


def _settings_file_signature() -> tuple[str, int, int]:
    settings_path = backend_config.CONFIG_FILE
    try:
        stat = settings_path.stat()
        return str(settings_path), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(settings_path), -1, -1


def _refresh_runtime_auth_state_if_changed() -> None:
    global _auth_settings_signature

    signature = _settings_file_signature()
    with _AUTH_STATE_LOCK:
        if (
            signature == _auth_settings_signature
            and AUTH_MODE_CACHE is not None
            and API_KEY_CACHE is not None
        ):
            return
        settings = load_settings()
        _ = _refresh_runtime_auth_state(settings)
        _auth_settings_signature = signature


async def _get_runtime_auth_snapshot() -> tuple[Optional[EffectiveAuthMode], str, bool]:
    """Return cached auth mode/key state, refreshing when settings changes."""

    await asyncio.to_thread(_refresh_runtime_auth_state_if_changed)
    return AUTH_MODE_CACHE, API_KEY_CACHE or "", API_KEY_FALLBACK_ALLOWED


def _get_user_role_for_auth_check(user_id: int) -> tuple[bool, Optional[str]]:
    engine = _ensure_auth_tables()
    if engine is None:
        return False, None
    from core.storage.auth import get_user_by_id as _gui

    user = _gui(engine, user_id)
    return True, user.role if user is not None else None


# ROLE-BASED ACCESS CONTROL
ROLE_HIERARCHY: Dict[str, int] = {"viewer": 0, "operator": 1, "admin": 2}


def require_role(minimum_role: str):
    async def _role_check(request: Request):
        if CW_DISABLE_AUTH or not RBAC_ENABLED:
            return
        if getattr(request.state, "api_key_authenticated", False):
            return
        user_id = getattr(request.state, "auth_user_id", None)
        if user_id is None:
            raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
        auth_db_available, role = await asyncio.to_thread(
            _get_user_role_for_auth_check, user_id
        )
        if not auth_db_available:
            raise structured_error(ErrorCode.AUTH_DB_UNAVAILABLE)
        if role is None:
            raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
        user_level = ROLE_HIERARCHY.get(role, -1)
        required_level = ROLE_HIERARCHY.get(minimum_role, 0)
        if user_level < required_level:
            raise structured_error(
                ErrorCode.AUTH_FORBIDDEN,
                message=f"Requires {minimum_role} role or higher",
            )

    return Depends(_role_check)


SENSITIVE_FIELDS = {
    "api_key",
    "apprise_pushover",
    "apprise_discord",
    "apprise_email",
    "apprise_email_to",
    "apprise_telegram",
    "apprise_slack",
    "apprise_gotify",
    "apprise_matrix",
    "apprise_custom",
    "error_reporting_dsn",
    "ics_feed_token",
    "rss_feed_token",
    "webhooks.secret",
}

MASKED_SENTINEL = "****"

# Deliberately empty: all SENSITIVE_FIELDS are masked on GET /api/settings;
# POST preserve uses MASKED_SENTINEL.
GET_SETTINGS_UNMASKED_FIELDS: set[str] = set()


def get_api_key() -> str:
    """Get the current API key from cache or settings."""
    global API_KEY_CACHE
    if API_KEY_CACHE:
        return API_KEY_CACHE
    settings = load_settings()
    API_KEY_CACHE = settings.api_key or ""
    return API_KEY_CACHE


async def verify_api_key(request: Request):
    """FastAPI dependency that checks X-API-Key header."""
    if CW_DISABLE_AUTH:
        return
    api_key = get_api_key()
    if not api_key:
        return
    provided_key = request.headers.get("X-API-Key", "")
    if not secrets.compare_digest(provided_key, api_key):
        raise structured_error(ErrorCode.AUTH_INVALID_KEY)


# SECURITY MIDDLEWARE
AUTH_EXEMPT_PATHS = {
    "/api/ping",
    "/api/health",
    "/healthz/ready",
    "/healthz/live",
    "/healthz/startup",
}
AUTH_EXEMPT_ROUTES = {
    ("GET", "/api/v1/security/status"),
    ("GET", "/api/v1/feeds/calendar.ics"),
    ("GET", "/api/v1/calendar.ics"),
    ("GET", "/api/v1/feeds/activity.rss"),
    ("GET", "/api/v1/feed.rss"),
    ("GET", "/api/v1/feeds/activity.atom"),
    ("GET", "/api/v1/feed.atom"),
}
_AUTH_EXEMPT_PREFIXES = {"/api/v1/auth/"}
RATE_LIMIT_EXEMPT_PATHS = {
    "/api/ping",
    "/api/health",
    "/healthz/ready",
    "/healthz/live",
    "/healthz/startup",
    "/metrics",
}
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_READ_REQUESTS = 120
RATE_LIMIT_WRITE_REQUESTS = 30

# CSP: API routes stay strict because inline scripts are irrelevant there. The
# packaged static Next.js UI needs inline script bootstrap chunks emitted by
# `next build`, so only the UI policy permits script/style inline execution.
API_CSP_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' https: data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
)


def _report_endpoint_csp_source() -> Optional[str]:
    raw_endpoint = os.environ.get(
        "CHANNELWATCH_REPORT_ENDPOINT", DEFAULT_REPORT_ENDPOINT
    )
    try:
        parsed = urlsplit(raw_endpoint)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname
    if any(char in host for char in " \t\r\n;"):
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    port_suffix = f":{port}" if port and port != default_port else ""
    return f"{parsed.scheme}://{host}{port_suffix}"


def _ui_csp_policy() -> str:
    connect_sources = ["'self'"]
    report_source = _report_endpoint_csp_source()
    if report_source and report_source not in connect_sources:
        connect_sources.append(report_source)
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' https: data: blob:",
            "font-src 'self' data:",
            f"connect-src {' '.join(connect_sources)}",
            "frame-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
        ]
    )

# CSRF: X-API-Key custom header is the primary CSRF defence for authenticated routes
# (OWASP "Custom Request Headers" pattern: browsers cannot add custom headers to
# cross-origin fetch without CORS preflight; allow_origins=[] rejects all preflights).
# When CW_DISABLE_AUTH=true auth is bypassed, so an explicit Origin check is applied
# to state-changing methods to block naive cross-site form/fetch submissions.
CSRF_PROTECTED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


class InMemoryRateLimiter:
    def __init__(self, window_seconds: int):
        self.window_seconds = window_seconds
        self._requests: Dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow_request(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            request_times = self._requests.get(key)
            if request_times is None:
                request_times = deque()
                self._requests[key] = request_times

            while request_times and request_times[0] <= cutoff:
                request_times.popleft()

            if len(request_times) >= limit:
                return False

            request_times.append(now)
            return True


rate_limiter = InMemoryRateLimiter(window_seconds=RATE_LIMIT_WINDOW_SECONDS)
_SYSTEM_INFO_LIBRARY_CACHE_TTL_SECONDS = 900.0
_SYSTEM_INFO_LIBRARY_CACHE: Dict[str, tuple[float, tuple[int, int, int]]] = {}
_SYSTEM_INFO_LIBRARY_CACHE_LOCK = threading.Lock()
_DVR_VERSION_STATUS_CACHE_TTL_SECONDS = 300.0
_DVR_VERSION_STATUS_CACHE: Dict[str, tuple[float, dict[str, Any]]] = {}
_DVR_VERSION_STATUS_CACHE_LOCK = threading.Lock()
_DVR_PROBE_CONCURRENCY_LIMIT = 5
_SYSTEM_INFO_SKIP_LIBRARY_COUNTS: ContextVar[bool] = ContextVar(
    "system_info_skip_library_counts", default=False
)


async def _read_upload_with_limit(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise structured_error(
                ErrorCode.RESTORE_INVALID_ZIP,
                message="Backup archive exceeds the restore upload size limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _structured_error_response(code: str, message: Optional[str] = None) -> Response:
    entry = catalog_entry(code)
    if entry is None:
        entry = catalog_entry(ErrorCode.UNKNOWN)
    assert entry is not None
    return Response(
        content=json.dumps(
            {
                "detail": {
                    "code": entry.code,
                    "message": message if message is not None else entry.message,
                    "remediation": entry.remediation,
                    "docs_url": entry.docs_url,
                }
            }
        ),
        status_code=entry.http_status,
        media_type="application/json",
    )


def _redact_webhook_url_for_settings(url: str) -> str:
    try:
        parsed = urlsplit(str(url or ""))
    except Exception:
        return MASKED_SENTINEL if url else ""
    if not parsed.scheme or not parsed.netloc:
        return MASKED_SENTINEL if url else ""

    host = parsed.hostname or ""
    if not host:
        return MASKED_SENTINEL
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        netloc = f"{netloc}:{port}"

    segments = [segment for segment in parsed.path.split("/") if segment]
    redacted_segments = []
    for index, segment in enumerate(segments):
        if segment.lower() in {"api", "hooks", "webhook", "webhooks", "services"}:
            redacted_segments.append(quote(segment, safe=""))
        elif len(segment) <= 4 and segment.isalpha():
            redacted_segments.append(quote(segment, safe=""))
        else:
            redacted_segments.append(MASKED_SENTINEL)
    path = "/" + "/".join(redacted_segments) if redacted_segments else ""
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


async def _bounded_dvr_probe_gather(
    items, worker, *, limit: int = _DVR_PROBE_CONCURRENCY_LIMIT
):
    if not items:
        return []

    semaphore = asyncio.Semaphore(max(1, min(limit, len(items))))

    async def _run(item):
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(_run(item) for item in items))


def _build_dvr_version_status(version: Optional[str]) -> dict[str, Any]:
    if not version:
        return {
            "version": None,
            "version_compatible": None,
            "version_warning": None,
        }

    compatibility = _check_dvr_version_compat(version)
    return {
        "version": version,
        "version_compatible": compatibility.get("compatible"),
        "version_warning": compatibility.get("warning"),
    }


def _cache_dvr_version_status(dvr_id: str, version: Optional[str]) -> dict[str, Any]:
    status = _build_dvr_version_status(version)
    if not dvr_id:
        return status

    with _DVR_VERSION_STATUS_CACHE_LOCK:
        _DVR_VERSION_STATUS_CACHE[dvr_id] = (time.monotonic(), status)
    return status


def _get_cached_dvr_version_status(dvr_id: str) -> dict[str, Any]:
    if dvr_id:
        now = time.monotonic()
        with _DVR_VERSION_STATUS_CACHE_LOCK:
            cached = _DVR_VERSION_STATUS_CACHE.get(dvr_id)
            if cached and (now - cached[0]) < _DVR_VERSION_STATUS_CACHE_TTL_SECONDS:
                return dict(cached[1])
    return _build_dvr_version_status(None)


async def _fetch_dvr_library_counts(dvr_url: str) -> tuple[int, int, int]:
    cache_key = dvr_url.rstrip("/")
    now = time.monotonic()
    with _SYSTEM_INFO_LIBRARY_CACHE_LOCK:
        cached = _SYSTEM_INFO_LIBRARY_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _SYSTEM_INFO_LIBRARY_CACHE_TTL_SECONDS:
            return cached[1]

    async def _count(path: str) -> int:
        try:
            resp = await _dvr_http_client.get(f"{dvr_url}{path}", timeout=5)
            if resp.is_success:
                return len(resp.json())
        except Exception:
            pass
        return 0

    s_shows, s_movies, s_episodes = await asyncio.gather(
        _count("/api/v1/shows"),
        _count("/api/v1/movies"),
        _count("/api/v1/episodes"),
    )

    with _SYSTEM_INFO_LIBRARY_CACHE_LOCK:
        _SYSTEM_INFO_LIBRARY_CACHE[cache_key] = (now, (s_shows, s_movies, s_episodes))
    return s_shows, s_movies, s_episodes


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in RATE_LIMIT_EXEMPT_PATHS:
            return await call_next(request)

        if not (path.startswith("/api/") or path == "/metrics"):
            return await call_next(request)

        is_write_request = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        limit = (
            RATE_LIMIT_WRITE_REQUESTS if is_write_request else RATE_LIMIT_READ_REQUESTS
        )
        client_host = (
            request.client.host if request.client and request.client.host else "unknown"
        )
        bucket = "write" if is_write_request else "read"
        key = f"{client_host}:{bucket}"

        if not rate_limiter.allow_request(key, limit):
            return Response(
                content=json.dumps(
                    {
                        "detail": {
                            "code": ErrorCode.RATE_LIMIT_EXCEEDED,
                            "message": "Rate limit exceeded. Please slow down.",
                            "remediation": "Wait a moment and retry the request.",
                            "docs_url": None,
                        }
                    }
                ),
                status_code=429,
                media_type="application/json",
            )

        return await call_next(request)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method
        _is_api_path = path.startswith("/api/")
        _is_auth_path = _is_api_path or path == "/metrics"
        _is_exempt = (
            path in AUTH_EXEMPT_PATHS
            or any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES)
            or (method, path) in AUTH_EXEMPT_ROUTES
        )

        auth_mode: Optional[EffectiveAuthMode] = None
        api_key = ""
        legacy_api_key_fallback = False
        if _is_auth_path and not CW_DISABLE_AUTH and not _is_exempt:
            (
                auth_mode,
                api_key,
                legacy_api_key_fallback,
            ) = await _get_runtime_auth_snapshot()
            api_key = api_key.strip()

        if _is_auth_path and not CW_DISABLE_AUTH and not _is_exempt:
            provided_key = request.headers.get("X-API-Key", "")
            if auth_mode == "api_key":
                if not api_key or not secrets.compare_digest(provided_key, api_key):
                    return _structured_error_response(ErrorCode.AUTH_INVALID_KEY)
                request.state.api_key_authenticated = True
            elif auth_mode == "rbac":
                if (
                    api_key
                    and legacy_api_key_fallback
                    and secrets.compare_digest(provided_key, api_key)
                ):
                    request.state.api_key_authenticated = True
                else:
                    _sc = request.cookies.get("channelwatch_session", "")
                    _us = (
                        await asyncio.to_thread(_lookup_user_session, _sc)
                        if _sc
                        else None
                    )
                    if _us is None:
                        return _structured_error_response(
                            ErrorCode.AUTH_UNAUTHENTICATED
                        )
                    request.state.auth_user_id = _us.user_id
                    request.state.auth_session_csrf = _us.csrf_token

        if RBAC_ENABLED and _is_api_path and not _is_exempt and not CW_DISABLE_AUTH:
            if not hasattr(request.state, "auth_session_csrf"):
                _sc = request.cookies.get("channelwatch_session", "")
                if _sc:
                    _us = await asyncio.to_thread(_lookup_user_session, _sc)
                    if _us is not None:
                        request.state.auth_user_id = _us.user_id
                        request.state.auth_session_csrf = _us.csrf_token

        if (
            RBAC_ENABLED
            and method.upper() in CSRF_PROTECTED_METHODS
            and _is_api_path
            and not _is_exempt
            and hasattr(request.state, "auth_session_csrf")
        ):
            csrf_header = request.headers.get("X-CSRF-Token", "")
            csrf_expected = str(request.state.auth_session_csrf or "")
            if (
                not csrf_header
                or not csrf_expected
                or not secrets.compare_digest(csrf_header, csrf_expected)
            ):
                return _structured_error_response(ErrorCode.AUTH_CSRF_INVALID)

        if (
            CW_DISABLE_AUTH
            and method.upper() in CSRF_PROTECTED_METHODS
            and _is_api_path
            and path not in AUTH_EXEMPT_PATHS
        ):
            origin = request.headers.get("Origin", "")
            if origin:
                host = request.headers.get("Host", "")
                origin_host = origin.split("://", 1)[-1].rstrip("/")
                if origin_host != host:
                    return _structured_error_response(
                        ErrorCode.AUTH_CROSS_SITE_REJECTED
                    )

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            API_CSP_POLICY
            if (
                path.startswith("/api/")
                or path.startswith("/healthz")
                or path == "/metrics"
            )
            else _ui_csp_policy()
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )

        raw_cookies = response.headers.getlist("set-cookie")
        should_secure_cookie = _should_use_secure_cookies(request)
        if raw_cookies:
            del response.headers["set-cookie"]
            for cookie in raw_cookies:
                cl = cookie.lower()
                if "httponly" not in cl:
                    cookie += "; HttpOnly"
                if should_secure_cookie and "secure" not in cl:
                    cookie += "; Secure"
                if "samesite" not in cl:
                    cookie += "; SameSite=Strict"
                response.headers.append("set-cookie", cookie)

        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        tokens = set_log_context(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            clear_log_context(tokens)
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)


# BASIC ENDPOINTS
@app.get("/api/ping")
async def ping():
    return {"status": "ok"}


@app.get("/api/health")
async def health():
    summary = await asyncio.to_thread(_get_monitoring_health_summary)
    payload = {
        "status": "ok" if summary["ready"] else "degraded",
        "ready": summary["ready"],
        "dvrs": [
            {
                "id": entry["id"],
                "name": entry["name"],
                "monitoring_status": entry["monitoring_status"],
                "reason": entry["reason"],
            }
            for entry in summary["dvrs"]
        ],
    }
    if summary["ready"]:
        return payload
    return JSONResponse(status_code=503, content=payload)


@app.get("/healthz/live", include_in_schema=False)
async def healthz_live():
    return {"status": "ok"}


@app.get("/healthz/ready", include_in_schema=False)
async def healthz_ready():
    summary = await asyncio.to_thread(_get_monitoring_health_summary)
    payload = {
        "status": "ready" if summary["ready"] else "degraded",
        "ready": summary["ready"],
        "dvrs": [
            {
                "id": entry["id"],
                "name": entry["name"],
                "monitoring_status": entry["monitoring_status"],
                "freshness_status": entry["freshness_status"],
                "connected": entry["connected"],
                **_get_cached_dvr_version_status(entry["id"]),
                "reason": entry["reason"],
                "last_freshness_at": entry["last_freshness_at"],
                "freshness_age_seconds": entry["freshness_age_seconds"],
            }
            for entry in summary["dvrs"]
        ],
        "stale_threshold_seconds": summary["stale_threshold_seconds"],
        "tested_version_range": {
            "min": MIN_TESTED_DVR_VERSION,
            "max": MAX_TESTED_DVR_VERSION,
        },
    }
    if summary["ready"]:
        return payload
    return JSONResponse(status_code=503, content=payload)


@app.get("/healthz/startup", include_in_schema=False)
async def healthz_startup():
    if _STARTUP_COMPLETE:
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "not_ready"})


@app.get("/api/discover-servers", tags=["Information"])
async def discover_servers():
    try:
        raw = await asyncio.to_thread(_scan_for_dvrs, 3.0)
        settings = await _load_settings_async()
        existing = {
            f"{s.get('host', '')}:{s.get('port', 8089)}"
            for s in (getattr(settings, "dvr_servers", None) or [])
            if isinstance(s, dict)
        }
        found = [
            {
                "host": s["host"],
                "port": s["port"],
                "name": s["display_name_suggestion"],
                "version": "",
            }
            for s in raw
            if f"{s['host']}:{s['port']}" not in existing
        ]
        return {"servers": found, "error": None}
    except Exception as exc:
        log.warning("DVR discovery failed: %s", exc.__class__.__name__)
        return {
            "servers": [],
            "error": "DVR discovery failed. Check network access and container logs.",
        }


class _DvrConnectionTestRequest(BaseModel):
    host: str
    port: int = 8089
    api_key: Optional[str] = None


_DVR_TEST_PRIVATE_LAN_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def _is_private_lan_dvr_host(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False

    return addr.version == 4 and any(
        addr in network for network in _DVR_TEST_PRIVATE_LAN_NETWORKS
    )


def _parse_safe_dvr_ip_literal(
    host: str,
) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    raw = host.strip()
    if raw.startswith("["):
        if not raw.endswith("]"):
            return None
        raw = raw[1:-1]
    if not raw:
        return None
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


def _is_allowed_ipv6_dvr_host(host: str) -> bool:
    addr = _parse_safe_dvr_ip_literal(host)
    if addr is None or addr.version != 6:
        return False
    return not (
        addr.is_link_local
        or addr.is_loopback
        or addr.is_multicast
        or addr.is_unspecified
    )


def _is_safe_dvr_test_target(host: str, port: int) -> bool:
    host = (host or "").strip()
    if not host or not (1 <= port <= 65535):
        return False
    if "://" in host or any(ch in host for ch in ("/", "?", "#", "@")):
        return False

    if ":" in host:
        return _is_allowed_ipv6_dvr_host(host)

    if _is_private_lan_dvr_host(host):
        return True

    safety_url = build_dvr_base_url(host, port)
    if is_safe_url(safety_url):
        return True
    return False


def _safe_dvr_test_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "DVR request timed out."
    if isinstance(exc, httpx.RequestError):
        return "Could not reach DVR server."
    if isinstance(exc, ValueError):
        return "DVR returned an invalid status response."
    return "DVR connection test failed."


@app.post(
    "/api/v1/dvrs/test-connection",
    tags=["DVR Management"],
    dependencies=[require_role("operator")],
)
async def test_dvr_connection(body: _DvrConnectionTestRequest):
    host = body.host.strip()
    safety_url = build_dvr_base_url(host, body.port)
    if not _is_safe_dvr_test_target(host, body.port):
        log.warning("Rejected unsafe DVR test target: %s", redact_url(safety_url))
        raise structured_error(
            ErrorCode.DVR_TEST_TARGET_REJECTED,
            message="Test target rejected: host failed safety check",
        )

    try:
        url = f"{build_dvr_base_url(host, body.port)}/status"
        resp = await _dvr_http_client.get(url, timeout=8.0)
        if resp.status_code != 200:
            return {"success": False, "error": f"DVR returned HTTP {resp.status_code}"}
        data = resp.json()
        version = data.get("version", "")
        name = (
            data.get("FriendlyName")
            or data.get("friendly_name")
            or data.get("Name")
            or host
        )
        return {"success": True, "name": name, "version": version}
    except Exception as exc:
        log.warning("DVR connection test failed: %s", exc.__class__.__name__)
        return {"success": False, "error": _safe_dvr_test_error(exc)}


def _settings_read_requires_auth(settings: AppSettings) -> bool:
    mode = _effective_auth_mode(settings)
    if mode == "api_key":
        return True
    if mode != "rbac":
        return False
    engine = _ensure_auth_tables()
    if engine is None:
        return True
    from core.storage.auth import get_user_count as _guc_settings

    return _guc_settings(engine) > 0


async def _load_settings_async() -> AppSettings:
    return await asyncio.to_thread(load_settings)


async def _settings_read_requires_auth_async(settings: AppSettings) -> bool:
    return await asyncio.to_thread(_settings_read_requires_auth, settings)


def _request_has_valid_session(request: Request) -> bool:
    if not RBAC_ENABLED:
        return False
    token = request.cookies.get("channelwatch_session", "")
    if not token:
        return False
    session = _lookup_user_session(token)
    if session is None:
        return False
    request.state.auth_user_id = session.user_id
    request.state.auth_session_csrf = session.csrf_token
    return True


async def _request_has_valid_session_async(request: Request) -> bool:
    return await asyncio.to_thread(_request_has_valid_session, request)


@app.get("/api/settings", response_model=AppSettings)
async def get_settings_endpoint(request: Request):
    settings = await _load_settings_async()
    if not CW_DISABLE_AUTH and await _settings_read_requires_auth_async(settings):
        mode = _effective_auth_mode(settings)
        api_key = str(getattr(settings, "api_key", "") or "").strip()
        provided_key = request.headers.get("X-API-Key", "")
        authorized = False
        if mode == "api_key":
            authorized = bool(api_key and secrets.compare_digest(provided_key, api_key))
        elif mode == "rbac":
            if api_key and _legacy_api_key_fallback_allowed(settings):
                authorized = bool(secrets.compare_digest(provided_key, api_key))
            if not authorized:
                authorized = await _request_has_valid_session_async(request)
        if not authorized:
            raise structured_error(
                ErrorCode.AUTH_UNAUTHENTICATED,
                message="Authentication required",
            )
    masked_settings = {}
    for field_name in SENSITIVE_FIELDS:
        if "." in field_name or field_name in GET_SETTINGS_UNMASKED_FIELDS:
            continue

        field_value = getattr(settings, field_name, "")
        if field_value not in ("", None):
            masked_settings[field_name] = MASKED_SENTINEL

    if getattr(settings, "webhooks", None):
        masked_webhooks = []
        for webhook in settings.webhooks:
            if isinstance(webhook, BaseModel):
                webhook_data = webhook.model_dump()
            elif isinstance(webhook, dict):
                webhook_data = dict(webhook)
            else:
                continue

            if webhook_data.get("secret"):
                webhook_data["secret"] = MASKED_SENTINEL
            if webhook_data.get("url"):
                webhook_data["url"] = _redact_webhook_url_for_settings(
                    str(webhook_data.get("url") or "")
                )
            masked_webhooks.append(WebhookSettings(**webhook_data))

        masked_settings["webhooks"] = masked_webhooks

    masked_dvr_servers, dvr_keys_masked = _mask_dvr_api_keys(
        getattr(settings, "dvr_servers", None) or []
    )
    if dvr_keys_masked:
        masked_settings["dvr_servers"] = masked_dvr_servers

    if masked_settings:
        settings = settings.model_copy(update=masked_settings)
    return settings


def _mask_dvr_api_keys(dvr_servers: list):
    masked = []
    any_masked = False
    for server in dvr_servers:
        if isinstance(server, dict) and server.get("api_key") not in ("", None):
            server = dict(server)
            server["api_key"] = MASKED_SENTINEL
            any_masked = True
        masked.append(server)
    return masked, any_masked


def _validate_persisted_dvr_servers(settings: AppSettings) -> None:
    for server in getattr(settings, "dvr_servers", None) or []:
        if not isinstance(server, dict):
            continue
        host = str(server.get("host", "") or "").strip()
        if not host:
            continue
        try:
            port = int(server.get("port", 8089) or 8089)
        except (TypeError, ValueError):
            log.warning("Rejected DVR settings target with invalid port.")
            raise structured_error(ErrorCode.DVR_TEST_TARGET_REJECTED)
        if not _is_safe_dvr_test_target(host, port):
            log.warning(
                "Rejected unsafe DVR settings target: %s",
                redact_url(build_dvr_base_url(host, port)),
            )
            raise structured_error(
                ErrorCode.DVR_TEST_TARGET_REJECTED,
            )
        server["host"] = host
        server["port"] = port


@app.post("/api/settings", dependencies=[require_role("operator")])
async def update_settings_endpoint(settings: AppSettings):
    try:
        existing = await _load_settings_async()
        for field_name in SENSITIVE_FIELDS:
            if "." in field_name:
                continue
            incoming = getattr(settings, field_name, "")
            if incoming in ("", None, MASKED_SENTINEL):
                setattr(settings, field_name, getattr(existing, field_name, ""))
        existing_dvr_map = {}
        for s in getattr(existing, "dvr_servers", None) or []:
            if isinstance(s, dict):
                existing_dvr_map[s.get("id", "")] = s
        existing_webhooks = list(getattr(existing, "webhooks", None) or [])
        for index, webhook in enumerate(getattr(settings, "webhooks", None) or []):
            if not isinstance(webhook, BaseModel):
                continue
            incoming_url = str(getattr(webhook, "url", "") or "")
            if MASKED_SENTINEL in incoming_url and index < len(existing_webhooks):
                existing_webhook = existing_webhooks[index]
                if isinstance(existing_webhook, BaseModel):
                    webhook.url = str(getattr(existing_webhook, "url", "") or "")
                elif isinstance(existing_webhook, dict):
                    webhook.url = str(existing_webhook.get("url", "") or "")
        for server in getattr(settings, "dvr_servers", None) or []:
            if not isinstance(server, dict):
                continue
            existing_server = existing_dvr_map.get(server.get("id", ""), {})
            incoming_key = server.get("api_key", "")
            if incoming_key in ("", None, MASKED_SENTINEL):
                existing_key = existing_server.get("api_key", "")
                if existing_key:
                    server["api_key"] = existing_key
            if server.get("overrides"):
                for field_name in SENSITIVE_FIELDS:
                    if field_name in server["overrides"]:
                        if server["overrides"][field_name] is None:
                            del server["overrides"][field_name]
        if settings.ics_feed_enabled:
            settings.ics_feed_token = str(
                getattr(settings, "ics_feed_token", "") or ""
            ).strip()
            if not settings.ics_feed_token:
                settings.ics_feed_token = secrets.token_urlsafe(32)
        if settings.rss_feed_enabled:
            settings.rss_feed_token = str(
                getattr(settings, "rss_feed_token", "") or ""
            ).strip()
            if not settings.rss_feed_token:
                settings.rss_feed_token = secrets.token_urlsafe(32)
        if (
            not settings.api_key
            and existing.api_key
            and str(getattr(settings, "auth_mode", "") or "").strip().lower()
            not in {"rbac", "none"}
        ):
            settings.api_key = existing.api_key
        _validate_persisted_dvr_servers(settings)
        await _save_settings_and_signal_reload_async(settings)
        _refresh_runtime_auth_state(settings)
        return {"message": "Settings saved successfully"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[WebUI API] ERROR: Failed saving settings: {e}")
        raise structured_error(ErrorCode.SETTINGS_SAVE_FAILED)


@app.post(
    "/api/v1/notifications/destination-safety/preview",
    response_model=NotificationDestinationSafetyResponse,
    dependencies=[require_role("operator")],
)
async def preview_notification_destination_safety_endpoint(
    body: NotificationDestinationSafetyRequest,
):
    settings = await _load_settings_async()
    preview = await asyncio.to_thread(
        preview_notification_destination_safety,
        body.url,
        body.source,
        getattr(settings, "trusted_notification_destinations", []),
    )
    return NotificationDestinationSafetyResponse(
        source=preview.source,
        url=redact_url(preview.url),
        normalized=preview.normalized,
        status=preview.status,
        message=preview.message,
        trustable=preview.trustable,
        trusted=preview.trusted,
    )


@app.post("/api/regenerate-api-key", dependencies=[require_role("admin")])
async def regenerate_api_key():
    settings = await _load_settings_async()
    settings.api_key = secrets.token_urlsafe(32)
    if not getattr(settings, "auth_mode", ""):
        settings.auth_mode = "api_key"
    await _save_settings_and_signal_reload_async(settings)
    _refresh_runtime_auth_state(settings)
    return {"api_key": settings.api_key}


@app.get(
    "/api/v1/backup/download", tags=["Backup"], dependencies=[require_role("admin")]
)
async def download_backup():
    from .backup_restore import create_backup_zip

    try:
        zip_bytes = await asyncio.to_thread(create_backup_zip, CONFIG_DIR)
    except Exception as exc:
        log.exception("Backup creation failed: %s", exc)
        raise structured_error(ErrorCode.BACKUP_CREATE_FAILED)
    from datetime import timezone as _tz2

    ts = datetime.now(_tz2.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"channelwatch_backup_{ts}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post(
    "/api/v1/backup/restore", tags=["Backup"], dependencies=[require_role("admin")]
)
async def restore_backup(file: UploadFile = FastAPIFile(...)):
    from .backup_restore import (
        MAX_RESTORE_ARCHIVE_BYTES,
        restore_from_zip,
        RestoreValidationError,
    )

    try:
        zip_bytes = await _read_upload_with_limit(file, MAX_RESTORE_ARCHIVE_BYTES)
        manifest = await asyncio.to_thread(restore_from_zip, zip_bytes, CONFIG_DIR)
    except RestoreValidationError as exc:
        msg = str(exc)
        if "ahead of this installation" in msg:
            raise structured_error(ErrorCode.RESTORE_SCHEMA_AHEAD, message=msg)
        raise structured_error(ErrorCode.RESTORE_INVALID_ZIP, message=msg)
    except Exception as exc:
        log.exception("Restore failed: %s", exc)
        raise structured_error(ErrorCode.RESTORE_FAILED)

    await asyncio.to_thread(_signal_core_hot_reload)
    return {
        "message": "Restore completed. Core process hot-reloaded.",
        "manifest": manifest,
    }


@app.get("/api/v1/debug/bundle", tags=["Debug"], dependencies=[require_role("admin")])
async def download_debug_bundle():
    from .debug_bundle import create_debug_bundle
    from .error_catalog import ErrorCode

    try:
        zip_bytes = await asyncio.to_thread(create_debug_bundle, CONFIG_DIR)
    except Exception as exc:
        log.exception("Debug bundle creation failed: %s", exc)
        raise structured_error(ErrorCode.DEBUG_BUNDLE_CREATE_FAILED)
    from datetime import timezone as _tz3

    ts = datetime.now(_tz3.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"channelwatch_debug_{ts}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _configured_report_max_bytes() -> int:
    raw_value = os.environ.get("CHANNELWATCH_REPORT_MAX_BYTES", "").strip()
    if not raw_value:
        return DEFAULT_REPORT_MAX_BYTES
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_REPORT_MAX_BYTES
    return max(1024, min(parsed, DEFAULT_REPORT_MAX_BYTES))


def _configured_report_max_attachment_bytes() -> int:
    raw_value = os.environ.get("CHANNELWATCH_REPORT_MAX_ATTACHMENT_BYTES", "").strip()
    if not raw_value:
        return DEFAULT_REPORT_MAX_ATTACHMENT_BYTES
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_REPORT_MAX_ATTACHMENT_BYTES
    return max(1024, min(parsed, DEFAULT_REPORT_MAX_ATTACHMENT_BYTES))


def _configured_report_max_total_attachment_bytes() -> int:
    raw_value = os.environ.get(
        "CHANNELWATCH_REPORT_MAX_TOTAL_ATTACHMENT_BYTES", ""
    ).strip()
    if not raw_value:
        return DEFAULT_REPORT_MAX_TOTAL_ATTACHMENT_BYTES
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_REPORT_MAX_TOTAL_ATTACHMENT_BYTES
    return max(1024, min(parsed, DEFAULT_REPORT_MAX_TOTAL_ATTACHMENT_BYTES))


def _configured_report_portal_url() -> str:
    raw_value = os.environ.get("CHANNELWATCH_REPORT_PORTAL_URL", "").strip()
    if not raw_value:
        return DEFAULT_REPORT_PORTAL_URL
    parts = urlsplit(raw_value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return DEFAULT_REPORT_PORTAL_URL
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "", "", ""))


_REPORT_REQUEST_TOO_LARGE_MESSAGE = "Report request exceeds the configured size limit."
_REPORT_PAYLOAD_REQUIRED_MESSAGE = "Report payload is required."
_REPORT_PAYLOAD_TOO_LARGE_MESSAGE = "Report payload exceeds the configured size limit."
_REPORT_FORM_INVALID_MESSAGE = "Report form is invalid."
_REPORT_TOO_MANY_SCREENSHOTS_MESSAGE = "Too many screenshots were attached."
_REPORT_DEBUG_BUNDLE_INVALID_MESSAGE = "Debug bundle attachment is invalid."
_REPORT_ATTACHMENT_TOO_LARGE_MESSAGE = "Attachment exceeds the per-file size limit."
_REPORT_ATTACHMENTS_TOO_LARGE_MESSAGE = "Attachments exceed the total size limit."


def _report_error(code: str, message: str) -> HTTPException:
    return structured_error(code, message=message)


async def _read_report_upload(
    upload: UploadFile,
    *,
    max_attachment_bytes: int,
    max_total_attachment_bytes: int,
    total_read: dict[str, int],
) -> bytes:
    data = bytearray()
    try:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            total_read["bytes"] = total_read.get("bytes", 0) + len(chunk)
            if len(data) > max_attachment_bytes:
                raise _report_error(
                    ErrorCode.SUPPORT_REPORT_ATTACHMENT_TOO_LARGE,
                    _REPORT_ATTACHMENT_TOO_LARGE_MESSAGE,
                )
            if total_read["bytes"] > max_total_attachment_bytes:
                raise _report_error(
                    ErrorCode.SUPPORT_REPORT_ATTACHMENT_TOO_LARGE,
                    _REPORT_ATTACHMENTS_TOO_LARGE_MESSAGE,
                )
    finally:
        await upload.close()
    return bytes(data)


async def _parse_support_report_request(
    request: Request,
) -> tuple[ReportProblemPayload, list[tuple[ReportAttachmentSummary, bytes]]]:
    max_bytes = _configured_report_max_bytes()
    max_attachment_bytes = _configured_report_max_attachment_bytes()
    max_total_attachment_bytes = _configured_report_max_total_attachment_bytes()
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            allowed_request_bytes = max_bytes + max_total_attachment_bytes + 65536
            if int(content_length) > allowed_request_bytes:
                raise _report_error(
                    ErrorCode.SUPPORT_REPORT_REQUEST_TOO_LARGE,
                    _REPORT_REQUEST_TOO_LARGE_MESSAGE,
                )
        except ValueError:
            pass

    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith("multipart/form-data"):
        raw_body = await request.body()
        if len(raw_body) > max_bytes:
            raise _report_error(
                ErrorCode.SUPPORT_REPORT_REQUEST_TOO_LARGE,
                _REPORT_PAYLOAD_TOO_LARGE_MESSAGE,
            )
        try:
            return parse_report_payload(raw_body, max_bytes), []
        except ReportPayloadTooLarge as exc:
            raise _report_error(ErrorCode.SUPPORT_REPORT_REQUEST_TOO_LARGE, str(exc))
        except ReportPayloadInvalid as exc:
            raise _report_error(ErrorCode.SUPPORT_REPORT_PAYLOAD_INVALID, str(exc))

    try:
        form = await request.form()
    except Exception as exc:
        raise _report_error(
            ErrorCode.SUPPORT_REPORT_FORM_INVALID,
            _REPORT_FORM_INVALID_MESSAGE,
        ) from exc

    raw_payload = form.get("payload")
    if not isinstance(raw_payload, str):
        raise _report_error(
            ErrorCode.SUPPORT_REPORT_PAYLOAD_INVALID,
            _REPORT_PAYLOAD_REQUIRED_MESSAGE,
        )

    try:
        payload = parse_report_payload(raw_payload.encode("utf-8"), max_bytes)
    except ReportPayloadTooLarge as exc:
        raise _report_error(ErrorCode.SUPPORT_REPORT_REQUEST_TOO_LARGE, str(exc))
    except ReportPayloadInvalid as exc:
        raise _report_error(ErrorCode.SUPPORT_REPORT_PAYLOAD_INVALID, str(exc))

    attachments: list[tuple[ReportAttachmentSummary, bytes]] = []
    total_read: dict[str, int] = {"bytes": 0}
    screenshot_files = [
        item for item in form.getlist("screenshots") if isinstance(item, StarletteUploadFile)
    ]
    if len(screenshot_files) > DEFAULT_REPORT_MAX_SCREENSHOTS:
        raise _report_error(
            ErrorCode.SUPPORT_REPORT_ATTACHMENT_INVALID,
            _REPORT_TOO_MANY_SCREENSHOTS_MESSAGE,
        )
    debug_bundle = form.get("debug_bundle")
    if debug_bundle is not None and not isinstance(debug_bundle, StarletteUploadFile):
        raise _report_error(
            ErrorCode.SUPPORT_REPORT_ATTACHMENT_INVALID,
            _REPORT_DEBUG_BUNDLE_INVALID_MESSAGE,
        )

    try:
        for upload in screenshot_files:
            content = await _read_report_upload(
                upload,
                max_attachment_bytes=max_attachment_bytes,
                max_total_attachment_bytes=max_total_attachment_bytes,
                total_read=total_read,
            )
            summary = summarize_report_attachment(
                filename=upload.filename,
                content_type=upload.content_type,
                content=content,
                kind="screenshot",
                max_attachment_bytes=max_attachment_bytes,
            )
            attachments.append((summary, content))
        if isinstance(debug_bundle, StarletteUploadFile):
            content = await _read_report_upload(
                debug_bundle,
                max_attachment_bytes=max_attachment_bytes,
                max_total_attachment_bytes=max_total_attachment_bytes,
                total_read=total_read,
            )
            summary = summarize_report_attachment(
                filename=debug_bundle.filename,
                content_type=debug_bundle.content_type,
                content=content,
                kind="debug_bundle",
                max_attachment_bytes=max_attachment_bytes,
            )
            attachments.append((summary, content))
        validate_attachment_limits(
            [summary for summary, _content in attachments],
            max_total_attachment_bytes=max_total_attachment_bytes,
            max_screenshot_count=DEFAULT_REPORT_MAX_SCREENSHOTS,
        )
    except ReportAttachmentTooLarge as exc:
        raise _report_error(ErrorCode.SUPPORT_REPORT_ATTACHMENT_TOO_LARGE, str(exc))
    except ReportAttachmentInvalid as exc:
        raise _report_error(ErrorCode.SUPPORT_REPORT_ATTACHMENT_INVALID, str(exc))

    return payload, attachments


@app.get(
    "/api/v1/support/report-config",
    response_model=ReportConfigResponse,
    tags=["Support"],
    dependencies=[require_role("operator")],
)
async def get_support_report_config():
    return ReportConfigResponse(
        mode=parse_report_mode(os.environ.get("CHANNELWATCH_REPORT_MODE")),
        endpoint=os.environ.get("CHANNELWATCH_REPORT_ENDPOINT", "").strip()
        or DEFAULT_REPORT_ENDPOINT,
        portal_url=_configured_report_portal_url(),
        max_bytes=_configured_report_max_bytes(),
        turnstile_site_key=None,
        attachments_enabled=True,
        max_attachment_bytes=_configured_report_max_attachment_bytes(),
        max_total_attachment_bytes=_configured_report_max_total_attachment_bytes(),
        max_screenshot_count=DEFAULT_REPORT_MAX_SCREENSHOTS,
        allowed_attachment_types=REPORT_ALLOWED_ATTACHMENT_TYPES,
    )


@app.post(
    "/api/v1/support/report-dry-run",
    response_model=ReportPreviewResponse,
    tags=["Support"],
    dependencies=[require_role("operator")],
)
async def submit_support_report_dry_run(request: Request):
    payload, attachment_files = await _parse_support_report_request(request)
    return render_report_preview(
        payload,
        mode="dry-run",
        attachments=[summary for summary, _content in attachment_files],
    )


@app.post(
    "/api/v1/support/offline-package",
    tags=["Support"],
    dependencies=[require_role("operator")],
)
async def download_support_report_offline_package(request: Request):
    payload, attachment_files = await _parse_support_report_request(request)
    package = build_offline_report_package(
        payload,
        attachments=attachment_files,
        portal_url=_configured_report_portal_url(),
    )
    ts = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"channelwatch_support_report_{ts}.zip"
    return Response(
        content=package,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/dvrs/archived")
async def list_archived_dvrs():
    settings = await _load_settings_async()
    servers = getattr(settings, "dvr_servers", None) or []
    archived = [s for s in servers if isinstance(s, dict) and s.get("deleted_at")]
    archived, _ = _mask_dvr_api_keys(archived)
    return {"archived": archived}


@app.post("/api/dvrs/{dvr_id}/soft-delete", dependencies=[require_role("admin")])
async def soft_delete_dvr_endpoint(dvr_id: str):
    settings = await _load_settings_async()
    servers = list(getattr(settings, "dvr_servers", None) or [])
    try:
        found = _soft_delete_dvr(servers, dvr_id)
    except ValueError as exc:
        raise structured_error(ErrorCode.DVR_ALREADY_DELETED, message=str(exc))
    if not found:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    settings.dvr_servers = servers
    await _save_settings_and_signal_reload_async(settings)
    return {"message": f"DVR {dvr_id!r} soft-deleted"}


@app.post("/api/dvrs/{dvr_id}/restore", dependencies=[require_role("admin")])
async def restore_dvr_endpoint(dvr_id: str):
    settings = await _load_settings_async()
    servers = list(getattr(settings, "dvr_servers", None) or [])
    try:
        found = _restore_dvr(servers, dvr_id)
    except ValueError as exc:
        raise structured_error(ErrorCode.DVR_NOT_DELETED, message=str(exc))
    if not found:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    settings.dvr_servers = servers
    await _save_settings_and_signal_reload_async(settings)
    return {"message": f"DVR {dvr_id!r} restored"}


@app.delete("/api/dvrs/{dvr_id}", dependencies=[require_role("admin")])
async def hard_delete_dvr_endpoint(dvr_id: str):
    settings = await _load_settings_async()
    servers = list(getattr(settings, "dvr_servers", None) or [])
    found = _hard_delete_dvr(_CORE_CONFIG_DIR, servers, dvr_id)
    if not found:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    settings.dvr_servers = servers
    await _save_settings_and_signal_reload_async(settings)
    return {"message": f"DVR {dvr_id!r} permanently deleted"}


# INFORMATION MODELS
class AboutInfo(BaseModel):
    app_name: str
    version: str
    developer: str
    description: str
    github_url: str
    dockerhub_url: str


@app.get("/api/about", response_model=AboutInfo, tags=["Information"])
async def get_about_info():
    """Returns information about the ChannelWatch application."""
    return AboutInfo(
        app_name=__app_name__,
        version=__version__,
        developer="CoderLuii",
        description="Channels DVR monitoring tool for real-time notifications.",
        github_url="https://github.com/CoderLuii/ChannelWatch",
        dockerhub_url="https://hub.docker.com/r/coderluii/channelwatch",
    )


APP_START_TIME = datetime.now(_tz.utc)
CORE_LAST_START_TIME = None  # Only set on explicit restart, not initial start
BYTES_PER_GIB = 1024 * 1024 * 1024
_STARTUP_COMPLETE: bool = False
log.debug(f"[WebUI] Application started at {APP_START_TIME.isoformat()}")


class AlertHistoryItem(BaseModel):
    model_config = {"extra": "allow"}
    id: str = ""
    type: str
    title: str
    message: str
    timestamp: str
    icon: str = "bell"
    channel_name: str = ""
    channel_number: str = ""
    device_name: str = ""
    device_ip: str = ""
    program_title: str = ""
    image_url: str = ""
    stream_source: str = ""
    extra: dict = {}
    dvr_id: str = ""
    dvr_name: str = ""
    is_test: bool = False


class ActivityHistoryResponse(BaseModel):
    items: List[AlertHistoryItem]
    total: int
    offset: int
    limit: int


ACTIVITY_HISTORY: List[AlertHistoryItem] = []
_activity_lock = threading.Lock()

CONFIG_DIR = Path(backend_config.CONFIG_DIR)
HISTORY_FILE = CONFIG_DIR / "activity_history.json"

# ACTIVITY DATABASE (T22c) — SQLite/SQLModel layer.
# If channelwatch.db exists the read paths use it; otherwise fall back to JSON with a WARNING.
_ACTIVITY_DB_FILE = CONFIG_DIR / "channelwatch.db"
_ACTIVITY_DB_URL = _sqlite_url_for_path(_ACTIVITY_DB_FILE)
_activity_db_engine = None
_activity_db_warned = False

try:
    from core.storage import create_db_engine as _create_activity_db_engine
    from core.storage.models import ActivityEvent as _ActivityEvent
    from core.storage.database import (
        get_session as _activity_db_get_session,
        configure_journal_mode as _configure_journal_mode,
    )
    from core.storage.maintenance import (
        start_maintenance_thread as _start_maintenance_thread,
    )
    from core.storage.delivery_queries import (
        migrate_delivery_schema as _migrate_delivery_schema,
        query_delivery_log as _query_delivery_log,
    )
    from sqlmodel import select as _sql_select
    from sqlalchemy import (
        func as _sql_func,
        or_ as _sql_or,
        and_ as _sql_and,
        delete as _sql_delete,
    )

    _STORAGE_AVAILABLE = True
except ImportError:
    _STORAGE_AVAILABLE = False
    _create_activity_db_engine = _ActivityEvent = _activity_db_get_session = None  # type: ignore[assignment]
    _configure_journal_mode = _start_maintenance_thread = None  # type: ignore[assignment]
    _migrate_delivery_schema = _query_delivery_log = None  # type: ignore[assignment]
    _sql_select = _sql_func = _sql_or = _sql_and = _sql_delete = None  # type: ignore[assignment]


def _get_activity_db_engine():
    global _activity_db_engine, _activity_db_warned
    if not _STORAGE_AVAILABLE:
        return None
    if _activity_db_engine is not None:
        return _activity_db_engine
    if _ACTIVITY_DB_FILE.exists():
        _activity_db_engine = _create_activity_db_engine(_ACTIVITY_DB_URL)
        if _configure_journal_mode is not None:
            try:
                _configure_journal_mode(_activity_db_engine, str(_ACTIVITY_DB_FILE))
            except Exception as _jm_exc:
                log.warning("Failed to configure SQLite journal mode: %s", _jm_exc)
        log.debug("Activity DB engine initialized from %s", _ACTIVITY_DB_FILE)
        return _activity_db_engine
    if not _activity_db_warned:
        log.warning(
            "Activity database not found at %s; falling back to activity_history.json for "
            "read paths. Run the JSON migration to populate the database.",
            _ACTIVITY_DB_FILE,
        )
        _activity_db_warned = True
    return None


def _ensure_auth_tables():
    global _auth_db_engine
    if not _STORAGE_AVAILABLE:
        return None
    if _auth_db_engine is not None:
        return _auth_db_engine
    try:
        from sqlmodel import SQLModel as _SQLModel
        from core.storage.models import User as _User, UserSession as _UserSession  # noqa: F401

        engine = _create_activity_db_engine(_ACTIVITY_DB_URL)
        _SQLModel.metadata.create_all(engine)
        if _configure_journal_mode is not None:
            try:
                _configure_journal_mode(engine, str(_ACTIVITY_DB_FILE))
            except Exception:
                pass
        _auth_db_engine = engine
    except Exception as exc:
        log.warning("Could not initialize auth tables: %s", exc)
        return None
    return _auth_db_engine


def _lookup_user_session(token: str):
    try:
        engine = _ensure_auth_tables()
        if engine is None:
            return None
        from core.storage.auth import get_session_by_token as _gst

        return _gst(engine, token)
    except Exception:
        return None


def _active_dvr_count(settings: AppSettings) -> int:
    return len(
        [
            server
            for server in (getattr(settings, "dvr_servers", None) or [])
            if isinstance(server, dict) and not server.get("deleted_at")
        ]
    )


def _explicit_auth_mode(settings: AppSettings) -> Optional[AuthMode]:
    raw = str(getattr(settings, "auth_mode", "") or "").strip().lower()
    if raw in {"api_key", "rbac", "none"}:
        return raw  # type: ignore[return-value]
    return None


def _security_setup_marker(settings: AppSettings) -> Optional[bool]:
    marker = getattr(settings, "security_setup_completed", None)
    if isinstance(marker, bool):
        return marker
    return None


def _effective_auth_mode(
    settings: Optional[AppSettings] = None,
) -> Optional[EffectiveAuthMode]:
    settings = settings or load_settings()
    explicit = _explicit_auth_mode(settings)
    if explicit is not None:
        return explicit
    marker = _security_setup_marker(settings)
    if marker is False:
        return "setup"
    if bool(getattr(settings, "rbac_enabled", False)):
        return "rbac"
    if str(getattr(settings, "api_key", "") or "").strip():
        return "api_key"
    if marker is True:
        return "none"
    if _active_dvr_count(settings) > 0:
        return "none"
    return None


def _legacy_api_key_fallback_allowed(settings: Optional[AppSettings] = None) -> bool:
    settings = settings or load_settings()
    return (
        _explicit_auth_mode(settings) is None
        and bool(getattr(settings, "rbac_enabled", False))
        and bool(str(getattr(settings, "api_key", "") or "").strip())
    )


def _refresh_runtime_auth_state(
    settings: Optional[AppSettings] = None,
) -> Optional[EffectiveAuthMode]:
    global API_KEY_CACHE, RBAC_ENABLED, AUTH_MODE_CACHE, API_KEY_FALLBACK_ALLOWED
    settings = settings or load_settings()
    mode = _effective_auth_mode(settings)
    AUTH_MODE_CACHE = mode
    RBAC_ENABLED = mode == "rbac"
    API_KEY_CACHE = str(getattr(settings, "api_key", "") or "")
    API_KEY_FALLBACK_ALLOWED = _legacy_api_key_fallback_allowed(settings)
    return mode


def _setup_required(settings: Optional[AppSettings] = None) -> bool:
    settings = settings or load_settings()
    marker = _security_setup_marker(settings)
    if marker is False:
        return True
    mode = _effective_auth_mode(settings)
    if mode == "setup":
        return True
    if mode == "none":
        return False
    engine = _ensure_auth_tables()
    user_count = 0
    if engine is not None:
        try:
            from core.storage.auth import get_user_count as _guc

            user_count = _guc(engine)
        except Exception:
            user_count = 0
    if mode == "rbac":
        return user_count == 0
    if mode == "api_key":
        explicit = _explicit_auth_mode(settings)
        return explicit == "api_key" and _active_dvr_count(settings) == 0
    return user_count == 0 and _active_dvr_count(settings) == 0


class _ResolvedAuthState(BaseModel):
    persisted_mode: Optional[AuthMode] = None
    configured_mode: Optional[EffectiveAuthMode] = None
    effective_mode: Optional[EffectiveAuthMode] = None
    setup_required: bool
    runtime_auth_override_active: bool
    api_key_fallback_active: bool
    rbac_enabled: bool
    session_auth_available: bool
    session_setup_required: bool
    api_key_configured: bool


def _resolve_auth_state(settings: Optional[AppSettings] = None) -> _ResolvedAuthState:
    settings = settings or load_settings()
    persisted_mode = _explicit_auth_mode(settings)
    configured_mode = _effective_auth_mode(settings)
    effective_mode = "none" if CW_DISABLE_AUTH else configured_mode
    setup_required = _setup_required(settings)
    api_key_configured = bool(str(getattr(settings, "api_key", "") or "").strip())
    rbac_enabled = configured_mode == "rbac"

    return _ResolvedAuthState(
        persisted_mode=persisted_mode,
        configured_mode=configured_mode,
        effective_mode=effective_mode,
        setup_required=setup_required,
        runtime_auth_override_active=CW_DISABLE_AUTH,
        api_key_fallback_active=rbac_enabled
        and api_key_configured
        and _legacy_api_key_fallback_allowed(settings),
        rbac_enabled=rbac_enabled,
        session_auth_available=rbac_enabled,
        session_setup_required=setup_required
        if configured_mode in {"rbac", "setup", None}
        else False,
        api_key_configured=api_key_configured,
    )


def _should_use_secure_cookies(request: Request) -> bool:
    forwarded = request.headers.get("X-Forwarded-Proto", "").strip().lower()
    if forwarded:
        return forwarded == "https"
    return request.url.scheme == "https"


def _compute_security_mode(
    *, rbac_enabled: bool, api_key_configured: bool
) -> SecurityMode:
    if not rbac_enabled and not api_key_configured:
        return "NO_AUTH"
    if not rbac_enabled:
        return "API_KEY_ONLY"
    if api_key_configured:
        return "RBAC_WITH_API_KEY_FALLBACK"
    return "RBAC_ONLY"


def _session_setup_required(*, rbac_enabled: bool) -> bool:
    if not rbac_enabled:
        return False
    engine = _ensure_auth_tables()
    if engine is None:
        return True
    try:
        from core.storage.auth import get_user_count as _guc

        return _guc(engine) == 0
    except Exception:
        return True


def _dvr_api_keys_encrypted_at_rest(settings: AppSettings) -> bool:
    import stat
    from core.helpers.encryption import FERNET_PREFIX

    servers = [
        s for s in (getattr(settings, "dvr_servers", None) or []) if isinstance(s, dict)
    ]
    api_keys = [
        str(server.get("api_key", "") or "")
        for server in servers
        if server.get("api_key") not in ("", None)
    ]
    if not api_keys:
        return True

    if any(not value.startswith(FERNET_PREFIX) for value in api_keys):
        return False

    key_path = CONFIG_DIR / "encryption.key"
    if not key_path.exists():
        return False

    try:
        mode = stat.S_IMODE(key_path.stat().st_mode)
    except OSError:
        return False

    return (mode & 0o077) == 0


def _build_security_status() -> SecurityStatusResponse:
    settings = load_settings()
    auth_state = _resolve_auth_state(settings)
    ics_enabled = bool(
        getattr(settings, "ics_feed_enabled", False)
        and str(getattr(settings, "ics_feed_token", "") or "").strip()
    )
    activity_feed_enabled = bool(
        getattr(settings, "rss_feed_enabled", False)
        and str(getattr(settings, "rss_feed_token", "") or "").strip()
    )

    return SecurityStatusResponse(
        persisted_mode=auth_state.persisted_mode,
        configured_mode=auth_state.configured_mode,
        effective_mode=auth_state.effective_mode,
        setup_required=auth_state.setup_required,
        runtime_auth_override_active=auth_state.runtime_auth_override_active,
        api_key_fallback_active=auth_state.api_key_fallback_active,
        rbac_enabled=auth_state.rbac_enabled,
        session_auth_available=auth_state.session_auth_available,
        session_setup_required=auth_state.session_setup_required,
        security_mode=_compute_security_mode(
            rbac_enabled=auth_state.rbac_enabled,
            api_key_configured=auth_state.api_key_configured
            and (auth_state.configured_mode != "none"),
        ),
        auth_disabled=auth_state.runtime_auth_override_active
        or auth_state.configured_mode == "none",
        api_key_configured=auth_state.api_key_configured,
        encrypted_dvr_api_keys_at_rest=_dvr_api_keys_encrypted_at_rest(settings),
        encryption_key_path=str(CONFIG_DIR / "encryption.key"),
        feeds=SecurityFeedsStatus(
            implemented=True,
            ics_enabled=ics_enabled,
            rss_enabled=activity_feed_enabled,
            atom_enabled=activity_feed_enabled,
        ),
    )


def _bootstrap_admin_from_env():
    _cw_admin_user = os.environ.get("CW_ADMIN_USER", "").strip()
    _cw_admin_pass = os.environ.get("CW_ADMIN_PASS", "").strip()
    if not _cw_admin_user or not _cw_admin_pass:
        return
    try:
        engine = _ensure_auth_tables()
        if engine is None:
            return
        from core.storage.auth import get_user_count as _guc, create_user as _cu

        if _guc(engine) == 0:
            _cu(engine, _cw_admin_user, _cw_admin_pass, role="admin")
            log.info("Admin user %r created from CW_ADMIN_USER env var", _cw_admin_user)
    except Exception as exc:
        log.warning("Admin bootstrap from env vars failed: %s", exc)


def _activity_event_to_item(evt) -> "AlertHistoryItem":
    try:
        extra = json.loads(evt.extra) if evt.extra else {}
    except (ValueError, TypeError):
        extra = {}
    ts = evt.timestamp
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        ts_str = ts.isoformat()
    else:
        ts_str = str(ts)
    return AlertHistoryItem(
        id=evt.id,
        type=evt.event_type,
        title=evt.title,
        message=evt.message or "",
        timestamp=ts_str,
        icon=evt.icon or "bell",
        channel_name=evt.channel_name or "",
        channel_number=evt.channel_number or "",
        device_name=evt.device_name or "",
        device_ip=evt.device_ip or "",
        program_title=evt.program_title or "",
        image_url=evt.image_url or "",
        stream_source=evt.stream_source or "",
        dvr_id=evt.dvr_id or "",
        dvr_name=evt.dvr_name or "",
        is_test=bool(getattr(evt, "is_test", False)),
        extra=extra,
    )


def _query_activity_db(
    engine,
    *,
    offset: int = 0,
    limit: int = 50,
    activity_type: Optional[str] = None,
    search: Optional[str] = None,
    sort_desc: bool = True,
    since: Optional[datetime] = None,
    dvr_id: Optional[str] = None,
):
    conditions = []

    if dvr_id:
        conditions.append(_ActivityEvent.dvr_id == dvr_id)

    if since is not None:
        conditions.append(_ActivityEvent.timestamp >= since)

    if activity_type:
        norm = activity_type.strip().lower()
        if norm and norm != "all":
            grouped = ACTIVITY_TYPE_FILTERS.get(norm)
            if grouped is not None:
                conditions.append(_ActivityEvent.event_type.in_(list(grouped)))
            else:
                conditions.append(_sql_func.lower(_ActivityEvent.event_type) == norm)

    if search:
        s = search.strip().lower()
        if s:
            conditions.append(
                _sql_or(
                    _sql_func.lower(_ActivityEvent.title).contains(s),
                    _sql_func.lower(_ActivityEvent.message).contains(s),
                    _sql_func.lower(_ActivityEvent.event_type).contains(s),
                    _sql_func.lower(_ActivityEvent.channel_name).contains(s),
                    _sql_func.lower(_ActivityEvent.channel_number).contains(s),
                    _sql_func.lower(_ActivityEvent.device_name).contains(s),
                    _sql_func.lower(_ActivityEvent.device_ip).contains(s),
                    _sql_func.lower(_ActivityEvent.program_title).contains(s),
                    _sql_func.lower(_ActivityEvent.stream_source).contains(s),
                    _sql_func.lower(_ActivityEvent.dvr_name).contains(s),
                    _sql_func.lower(_ActivityEvent.extra).contains(s),
                )
            )

    order_col = (
        _ActivityEvent.timestamp.desc() if sort_desc else _ActivityEvent.timestamp.asc()
    )

    with _activity_db_get_session(engine) as session:
        count_stmt = _sql_select(_sql_func.count()).select_from(_ActivityEvent)
        if conditions:
            count_stmt = count_stmt.where(_sql_and(*conditions))
        total = session.exec(count_stmt).one()

        data_stmt = _sql_select(_ActivityEvent)
        if conditions:
            data_stmt = data_stmt.where(_sql_and(*conditions))
        data_stmt = data_stmt.order_by(order_col).offset(offset).limit(limit)
        rows = list(session.exec(data_stmt).all())

    return rows, total


LAST_MODIFIED_TIME = 0


def _parse_history_timestamp(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_tz.utc)
    return parsed.astimezone(_tz.utc)


ACTIVITY_TYPE_FILTERS = {
    "channel": {"watching_channel", "stream_started"},
    "vod": {"watching_vod", "vod_playback"},
    "recording": {
        "recording_event",
        "recording_started",
        "recording_completed",
        "recording_scheduled",
        "recording_stopped",
        "recording_cancelled",
    },
    "recording-events": {
        "recording_event",
        "recording_started",
        "recording_completed",
        "recording_scheduled",
        "recording_stopped",
        "recording_cancelled",
    },
    "disk": {"disk_alert"},
}


def _history_sort_key(item: AlertHistoryItem) -> datetime:
    try:
        return _parse_history_timestamp(item.timestamp)
    except Exception:
        return datetime.min.replace(tzinfo=_tz.utc)


def _activity_matches_type(
    item: AlertHistoryItem, requested_type: Optional[str]
) -> bool:
    if not requested_type:
        return True

    normalized_type = requested_type.strip().lower()
    if not normalized_type or normalized_type == "all":
        return True

    grouped_types = ACTIVITY_TYPE_FILTERS.get(normalized_type)
    if grouped_types is not None:
        return item.type in grouped_types

    return item.type.lower() == normalized_type


def _activity_matches_search(
    item: AlertHistoryItem, search_term: Optional[str]
) -> bool:
    if not search_term:
        return True

    normalized_search = search_term.strip().lower()
    if not normalized_search:
        return True

    searchable_fields = [
        item.title,
        item.message,
        item.type,
        item.channel_name,
        item.channel_number,
        item.device_name,
        item.device_ip,
        item.program_title,
        item.stream_source,
        item.dvr_name,
        json.dumps(item.extra or {}, default=str),
    ]

    return any(
        normalized_search in str(value).lower() for value in searchable_fields if value
    )


def load_alert_history():
    global ACTIVITY_HISTORY, LAST_MODIFIED_TIME

    if os.path.exists(HISTORY_FILE):
        try:
            log.debug(f"[WebUI] Loading activity history from {HISTORY_FILE}")
            with open(HISTORY_FILE, "r") as f:
                items = json.load(f)

            new_history = []
            for item_data in items:
                try:
                    new_history.append(AlertHistoryItem(**item_data))
                except Exception as e:
                    print(f"[WebUI] Error loading activity item: {e}")

            with _activity_lock:
                ACTIVITY_HISTORY = new_history
                LAST_MODIFIED_TIME = os.path.getmtime(HISTORY_FILE)

            log.debug(
                f"[WebUI] Loaded {len(new_history)} activity items from history file"
            )
            return True

        except json.JSONDecodeError as e:
            quarantined_path = _quarantine_malformed_history_file(HISTORY_FILE)
            detail = f"; quarantined at {quarantined_path}" if quarantined_path else ""
            print(f"[WebUI] Error parsing history file: {e}{detail}")
            return False
        except Exception as e:
            print(f"[WebUI] Error loading history file: {e}")
    else:
        try:
            atomic_write_json(HISTORY_FILE, [], indent=2)

            with _activity_lock:
                ACTIVITY_HISTORY = []
                LAST_MODIFIED_TIME = os.path.getmtime(HISTORY_FILE)

        except Exception as e:
            print(f"[WebUI] Error creating activity history file: {e}")
    return True


def _quarantine_malformed_history_file(path: Path) -> Optional[Path]:
    try:
        history_path = Path(path)
        if not history_path.exists():
            return None
        stamp = datetime.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine_path = history_path.with_name(f"{history_path.name}.corrupt-{stamp}")
        counter = 1
        while quarantine_path.exists():
            quarantine_path = history_path.with_name(
                f"{history_path.name}.corrupt-{stamp}-{counter}"
            )
            counter += 1
        os.replace(history_path, quarantine_path)
        return quarantine_path
    except Exception as exc:
        print(f"[WebUI] Error quarantining malformed history file: {exc}")
        return None


def check_history_file_changes():
    """Check if the activity history file has been modified and reload if needed."""
    global LAST_MODIFIED_TIME

    try:
        if os.path.exists(HISTORY_FILE):
            current_mtime = os.path.getmtime(HISTORY_FILE)

            if current_mtime > LAST_MODIFIED_TIME:
                load_alert_history()
                return True
    except Exception as e:
        print(f"[WebUI] Error checking history file changes: {e}")

    return False


load_alert_history()


def history_file_watcher():
    """Thread that monitors the activity history file for changes."""

    while True:
        try:
            check_history_file_changes()

            time.sleep(2)
        except Exception as e:
            print(f"[WebUI] Error in history file watcher: {e}")
            time.sleep(5)


history_file_watcher_thread = threading.Thread(target=history_file_watcher, daemon=True)


def ensure_history_file_watcher_started():
    global history_file_watcher_thread

    if history_file_watcher_thread.is_alive():
        return

    if history_file_watcher_thread.ident is not None:
        history_file_watcher_thread = threading.Thread(
            target=history_file_watcher, daemon=True
        )

    history_file_watcher_thread.start()


def _dvr_purge_loop():
    import time as _time

    while True:
        _time.sleep(86400)
        try:
            _settings = load_settings()
            servers = list(getattr(_settings, "dvr_servers", None) or [])
            purged = _purge_expired_dvrs(_CORE_CONFIG_DIR, servers)
            if purged:
                _settings.dvr_servers = servers
                _save_settings_and_signal_reload(_settings)
                print(
                    f"[WebUI API] Auto-purged {len(purged)} expired archived DVR(s): {purged}"
                )
        except Exception as exc:
            print(f"[WebUI API] DVR purge loop error: {exc}")


def run_startup_initialization():
    if CORE_APP_AVAILABLE:
        try:
            _get_core_settings_sync()
        except ConfigLoadError:
            log.critical("Startup blocked by corrupt settings.json", exc_info=True)
            raise

    settings = load_settings()
    explicit_mode = _explicit_auth_mode(settings)
    if explicit_mode == "api_key" and not settings.api_key:
        settings.api_key = secrets.token_urlsafe(32)
        save_settings(settings)
        print("[WebUI API] Generated new API key on first run")

    _refresh_runtime_auth_state(settings)

    if RBAC_ENABLED:
        _bootstrap_admin_from_env()

    if CORE_APP_AVAILABLE:
        try:
            from core.helpers.logging import setup_logging

            setup_logging(
                str(CONFIG_DIR), retention_days=settings.log_retention_days or 7
            )
        except Exception:
            pass

    if CW_DISABLE_AUTH:
        print(
            "[WebUI API] WARNING: API authentication is disabled (CW_DISABLE_AUTH=true)"
        )

    try:
        _build_update_manager().record_startup_success()
    except Exception as exc:
        log.warning("Could not record Update Center startup success: %s", exc)

    ensure_history_file_watcher_started()

    _purge_thread = threading.Thread(
        target=_dvr_purge_loop, daemon=True, name="dvr-purge"
    )
    _purge_thread.start()

    if _STORAGE_AVAILABLE and _start_maintenance_thread is not None:
        _history_retention = getattr(settings, "history_retention_days", 90) or 90
        _start_maintenance_thread(
            _get_activity_db_engine, retention_days=_history_retention
        )

    if _STORAGE_AVAILABLE and _migrate_delivery_schema is not None:
        _engine = _get_activity_db_engine()
        if _engine is not None:
            try:
                _migrate_delivery_schema(_engine)
            except Exception as _mig_exc:
                log.warning("Failed to migrate delivery schema: %s", _mig_exc)

    global _STARTUP_COMPLETE
    _STARTUP_COMPLETE = True


# SYSTEM INFO
class DVRStatus(BaseModel):
    id: str = ""
    name: str = ""
    host: str = ""
    port: int = 8089
    connected: bool = False
    version: Optional[str] = None
    version_compatible: Optional[bool] = None
    version_warning: Optional[str] = None
    disk_usage_percent: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    active_streams: int = 0
    library_shows: int = 0
    library_movies: int = 0
    library_episodes: int = 0
    monitoring_status: str = "missing"
    monitoring_ready: bool = False
    monitoring_reason: Optional[str] = None
    freshness_status: str = "missing"
    last_freshness_at: Optional[str] = None
    last_event_at: Optional[str] = None
    freshness_age_seconds: Optional[float] = None
    stale_threshold_seconds: Optional[int] = None


class SystemInfo(BaseModel):
    channelwatch_version: str
    channels_dvr_host: Optional[str] = None
    channels_dvr_port: int = 8089
    channels_dvr_server_version: Optional[str] = None
    timezone: str
    disk_usage_percent: Optional[float] = None
    disk_usage_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    disk_severity: str = "normal"
    log_retention_days: Optional[int] = None
    start_time: Optional[str] = None
    container_start_time: Optional[str] = None
    uptime_data: Dict[str, int] = {}
    core_status: str = "Unknown"
    library_shows: int = 0
    library_movies: int = 0
    library_episodes: int = 0
    dvr_status: List[DVRStatus] = []


def _get_dvr_servers():
    """Get list of configured DVR servers as (id, name, base_url) tuples."""
    settings = load_settings()
    return _get_dvr_servers_from_settings(settings)


def _get_dvr_servers_from_settings(settings: Any):
    servers = getattr(settings, "dvr_servers", None) or []
    result = []
    for s in servers:
        if isinstance(s, dict) and s.get("enabled", True) and not s.get("deleted_at"):
            host = s.get("host", "")
            port = s.get("port", 8089)
            result.append(
                (s.get("id", ""), s.get("name", host), build_dvr_base_url(host, port))
            )
    return result


async def _get_dvr_servers_async():
    settings = await _load_settings_async()
    return _get_dvr_servers_from_settings(settings)


def _get_enabled_dvr_records() -> list[dict[str, Any]]:
    settings = load_settings()
    servers = getattr(settings, "dvr_servers", None) or []
    result: list[dict[str, Any]] = []
    for server in servers:
        if (
            isinstance(server, dict)
            and server.get("enabled", True)
            and not server.get("deleted_at")
        ):
            result.append(
                {
                    "id": server.get("id", ""),
                    "name": server.get("name", server.get("host", "")),
                    "host": server.get("host", ""),
                    "port": int(server.get("port", 8089) or 8089),
                }
            )
    return result


def _get_monitoring_health_summary() -> dict[str, Any]:
    enabled_servers = _get_enabled_dvr_records()
    snapshot = load_watchdog_snapshot()
    return summarize_enabled_dvrs(enabled_servers, snapshot)


def _get_primary_dvr_url():
    """Get base URL for the first configured DVR (backward compatible)."""
    servers = _get_dvr_servers()
    if servers:
        return servers[0][2]
    # Fallback for any edge case
    return None


def _get_dvr_server_by_id(dvr_id: str) -> Optional[tuple]:
    for entry in _get_dvr_servers():
        if entry[0] == dvr_id:
            return entry
    return None


async def _get_dvr_server_by_id_async(dvr_id: str) -> Optional[tuple]:
    for entry in await _get_dvr_servers_async():
        if entry[0] == dvr_id:
            return entry
    return None


def _simple_disk_status(free_gb: Optional[float], total_gb: Optional[float]) -> str:
    if free_gb is None or total_gb is None or total_gb <= 0:
        return "unknown"
    free_pct = (free_gb / total_gb) * 100
    if free_pct < 5.0 or free_gb < 25.0:
        return "critical"
    if free_pct < 10.0 or free_gb < 50.0:
        return "warning"
    return "normal"


def _parse_storage_size_gb(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return round(value / BYTES_PER_GIB, 2)
    if isinstance(value, str):
        normalized = value.strip().upper()
        try:
            if normalized.endswith("TB"):
                return float(normalized.removesuffix("TB").strip()) * 1024
            if normalized.endswith("GB"):
                return float(normalized.removesuffix("GB").strip())
        except ValueError:
            return None
    return None


def _parse_dvr_storage(storage_data: Any):
    """Parse Channels DVR storage payloads into percent, total, free, and used GiB."""
    d_percent, d_total, d_free, d_used = None, None, None, None
    if not isinstance(storage_data, dict):
        return d_percent, d_total, d_free, d_used

    if "ServerStorage" in storage_data:
        storage_info = storage_data["ServerStorage"]
        if not isinstance(storage_info, dict):
            return d_percent, d_total, d_free, d_used
        if "Available" in storage_info and "Total" in storage_info:
            avail = storage_info["Available"]
            total = storage_info["Total"]
            if isinstance(avail, (int, float)) and isinstance(total, (int, float)):
                used = total - avail
                d_total = round(total / BYTES_PER_GIB, 2)
                d_free = round(avail / BYTES_PER_GIB, 2)
                d_used = round(used / BYTES_PER_GIB, 2)
                d_percent = round((used / total) * 100) if total else None
    elif "disk" in storage_data:
        disk_info = storage_data["disk"]
        if not isinstance(disk_info, dict):
            return d_percent, d_total, d_free, d_used
        if "free" in disk_info and "total" in disk_info:
            d_free = _parse_storage_size_gb(disk_info.get("free", 0))
            d_total = _parse_storage_size_gb(disk_info.get("total", 0))
            if d_free is not None and d_total is not None:
                d_used = d_total - d_free
                d_percent = round((d_used / d_total) * 100) if d_total else None
    return d_percent, d_total, d_free, d_used


def _read_dvr_session_state_summary(dvr_id: str) -> tuple[Optional[str], Optional[int]]:
    state_file = _CORE_CONFIG_DIR / f"session_state_{dvr_id}.json"
    if not state_file.is_file():
        return None, None
    state_data = json.loads(state_file.read_text())
    session_state_size = sum(
        len(v) if isinstance(v, dict) else 0 for v in state_data.values()
    )
    mtime = state_file.stat().st_mtime
    last_event_at = datetime.fromtimestamp(mtime, tz=_tz.utc).isoformat()
    return last_event_at, session_state_size


def _get_recent_alert_rate(dvr_id: str) -> Optional[float]:
    engine = _get_activity_db_engine()
    if engine is None or not _STORAGE_AVAILABLE:
        return None
    since = datetime.now(_tz.utc) - timedelta(hours=1)
    _, recent_count = _query_activity_db(
        engine, dvr_id=dvr_id, since=since, limit=1, offset=0
    )
    return float(recent_count)


@app.get("/api/system-info", response_model=SystemInfo, tags=["Information"])
async def get_system_info(
    response: Response,
    dvr_id: Optional[str] = Query(
        default=None, description="Limit DVR probes to one DVR"
    ),
):
    global CORE_LAST_START_TIME
    response.headers["X-Deprecated-API"] = "Use /api/v1/"
    if not isinstance(dvr_id, str) or not dvr_id.strip():
        dvr_id = None
    else:
        dvr_id = dvr_id.strip()
    DEFAULT_WARNING_THRESHOLD_PERCENT = 10.0
    DEFAULT_WARNING_THRESHOLD_GB = 50.0
    DEFAULT_CRITICAL_THRESHOLD_PERCENT = 5.0
    DEFAULT_CRITICAL_THRESHOLD_GB = 25.0

    if CORE_APP_AVAILABLE:
        from core.helpers.config import get_settings as _get_core_settings

        settings = await asyncio.to_thread(_get_core_settings)
    else:
        settings = await _load_settings_async()

    disk_usage_percent = None
    disk_usage_gb = None
    disk_total_gb = None
    disk_free_gb = None
    disk_severity = "normal"
    dvr_version = None

    servers = await _get_dvr_servers_async()
    if dvr_id:
        servers = [entry for entry in servers if entry[0] == dvr_id]
    dvr_status_list = []
    monitoring_by_id = {
        entry["id"]: entry
        for entry in (await asyncio.to_thread(_get_monitoring_health_summary))["dvrs"]
    }

    def _matches_threshold(
        *,
        free_percentage: float,
        free_gb: float,
        percent_threshold: float,
        gb_threshold: float,
    ) -> bool:
        return free_percentage < percent_threshold or free_gb < gb_threshold

    def _get_disk_severity(
        *, free_gb: Optional[float], total_gb: Optional[float]
    ) -> str:
        if free_gb is None or total_gb is None or total_gb <= 0:
            return "normal"

        legacy_warning_percent = float(
            getattr(settings, "ds_threshold_percent", DEFAULT_WARNING_THRESHOLD_PERCENT)
        )
        legacy_warning_gb = float(
            getattr(settings, "ds_threshold_gb", DEFAULT_WARNING_THRESHOLD_GB)
        )
        warning_threshold_percent = float(
            getattr(settings, "ds_warning_threshold_percent", legacy_warning_percent)
        )
        warning_threshold_gb = float(
            getattr(settings, "ds_warning_threshold_gb", legacy_warning_gb)
        )
        critical_threshold_percent = float(
            getattr(
                settings,
                "ds_critical_threshold_percent",
                DEFAULT_CRITICAL_THRESHOLD_PERCENT,
            )
        )
        critical_threshold_gb = float(
            getattr(settings, "ds_critical_threshold_gb", DEFAULT_CRITICAL_THRESHOLD_GB)
        )

        free_percentage = (free_gb / total_gb) * 100
        free_bytes = free_gb * BYTES_PER_GIB

        if _matches_threshold(
            free_percentage=free_percentage,
            free_gb=free_bytes / BYTES_PER_GIB,
            percent_threshold=critical_threshold_percent,
            gb_threshold=critical_threshold_gb,
        ):
            return "critical"

        if _matches_threshold(
            free_percentage=free_percentage,
            free_gb=free_bytes / BYTES_PER_GIB,
            percent_threshold=warning_threshold_percent,
            gb_threshold=warning_threshold_gb,
        ):
            return "warning"

        return "normal"

    async def _build_dvr_status(entry):
        dvr_id, dvr_name, dvr_url = entry
        monitor_entry = monitoring_by_id.get(dvr_id, {})
        s_version = None
        s_percent, s_total, s_free = None, None, None
        s_shows, s_movies, s_episodes = 0, 0, 0
        s_active_streams = 0
        s_connected = False

        status_result, storage_result = await asyncio.gather(
            _dvr_http_client.get(f"{dvr_url}/status", timeout=3),
            _dvr_http_client.get(f"{dvr_url}/dvr", timeout=3),
            return_exceptions=True,
        )
        if isinstance(status_result, BaseException) or isinstance(
            storage_result, BaseException
        ):
            error = (
                status_result
                if isinstance(status_result, BaseException)
                else storage_result
            )
            print(
                f"[WebUI API] ERROR: Failed to fetch DVR server information from {dvr_name}: {error}"
            )
        if (
            not isinstance(status_result, BaseException)
            and status_result.status_code == 200
        ):
            s_version = status_result.json().get("version", None)
            s_connected = True
        if (
            not isinstance(storage_result, BaseException)
            and storage_result.status_code == 200
        ):
            storage_payload = storage_result.json()
            s_percent, s_total, s_free, _ = _parse_dvr_storage(storage_payload)
            if isinstance(storage_payload, dict):
                activity = storage_payload.get("activity", {})
                if isinstance(activity, dict):
                    s_active_streams = len(activity)

        if not _SYSTEM_INFO_SKIP_LIBRARY_COUNTS.get():
            try:
                s_shows, s_movies, s_episodes = await _fetch_dvr_library_counts(dvr_url)
            except Exception:
                pass

        s_version_status = _cache_dvr_version_status(dvr_id, s_version)
        return DVRStatus(
            id=dvr_id,
            name=dvr_name,
            host=dvr_url.replace("http://", "").rsplit(":", 1)[0],
            port=int(dvr_url.rsplit(":", 1)[1]),
            connected=s_connected,
            version=s_version_status["version"],
            version_compatible=s_version_status["version_compatible"],
            version_warning=s_version_status["version_warning"],
            disk_usage_percent=s_percent,
            disk_total_gb=s_total,
            disk_free_gb=s_free,
            active_streams=s_active_streams,
            library_shows=s_shows,
            library_movies=s_movies,
            library_episodes=s_episodes,
            monitoring_status=monitor_entry.get("monitoring_status", "missing"),
            monitoring_ready=bool(monitor_entry.get("ready", False)),
            monitoring_reason=monitor_entry.get("reason"),
            freshness_status=monitor_entry.get("freshness_status", "missing"),
            last_freshness_at=monitor_entry.get("last_freshness_at"),
            last_event_at=monitor_entry.get("last_event_at"),
            freshness_age_seconds=monitor_entry.get("freshness_age_seconds"),
            stale_threshold_seconds=monitor_entry.get("stale_threshold_seconds"),
        )

    dvr_status_list = await _bounded_dvr_probe_gather(servers, _build_dvr_status)

    # Aggregate totals from all DVRs
    if dvr_status_list:
        dvr_version = next((d.version for d in dvr_status_list if d.version), None)
        agg_total = sum(d.disk_total_gb or 0 for d in dvr_status_list)
        agg_free = sum(d.disk_free_gb or 0 for d in dvr_status_list)
        agg_used = agg_total - agg_free
        disk_total_gb = round(agg_total, 2) if agg_total else None
        disk_free_gb = round(agg_free, 2) if agg_free else None
        disk_usage_gb = round(agg_used, 2) if agg_total else None
        disk_usage_percent = round((agg_used / agg_total) * 100) if agg_total else None
        disk_severity = _get_disk_severity(free_gb=agg_free, total_gb=agg_total)

    core_status = "Unknown"
    actual_core_start_time = None
    try:
        process_info = await asyncio.to_thread(_get_core_process_info_from_supervisor)
        if process_info:
            core_status = process_info.get("statename", "Unknown").capitalize()
            start_timestamp = process_info.get("start", 0)
            if start_timestamp > 0:
                actual_core_start_time = datetime.fromtimestamp(
                    start_timestamp, tz=_tz.utc
                )
                if (
                    CORE_LAST_START_TIME is None
                    or actual_core_start_time > CORE_LAST_START_TIME
                ):
                    CORE_LAST_START_TIME = actual_core_start_time
            else:
                if core_status not in ("Running", "Starting"):
                    CORE_LAST_START_TIME = APP_START_TIME
        else:
            print(
                "[WebUI API] WARNING: Could not connect to supervisord to get core status."
            )
            core_status = "Error"
    except Exception as e:
        print(
            "[WebUI API] ERROR: Failed to get core status from supervisord: "
            f"{_supervisor_exception_summary(e)}"
        )
        core_status = "Error"

    current_time = datetime.now(_tz.utc)
    uptime_seconds = int((current_time - APP_START_TIME).total_seconds())

    uptime_days = uptime_seconds // (24 * 3600)
    uptime_seconds %= 24 * 3600
    uptime_hours = uptime_seconds // 3600
    uptime_seconds %= 3600
    uptime_minutes = uptime_seconds // 60
    uptime_seconds %= 60

    uptime_data = {
        "days": uptime_days,
        "hours": uptime_hours,
        "minutes": uptime_minutes,
        "seconds": uptime_seconds,
    }

    # Aggregate library totals from all DVRs
    library_shows = sum(d.library_shows for d in dvr_status_list)
    library_movies = sum(d.library_movies for d in dvr_status_list)
    library_episodes = sum(d.library_episodes for d in dvr_status_list)

    _raw_dvr = (getattr(settings, "dvr_servers", None) or []) if settings else []

    return SystemInfo(
        channelwatch_version=__version__,
        channels_dvr_host=_raw_dvr[0].get("host") if _raw_dvr else None,
        channels_dvr_port=_raw_dvr[0].get("port", 8089) if _raw_dvr else 8089,
        channels_dvr_server_version=dvr_version,
        timezone=settings.tz if settings else "America/Los_Angeles",
        disk_usage_percent=disk_usage_percent,
        disk_usage_gb=disk_usage_gb,
        disk_total_gb=disk_total_gb,
        disk_free_gb=disk_free_gb,
        disk_severity=disk_severity,
        log_retention_days=settings.log_retention_days if settings else 7,
        start_time=CORE_LAST_START_TIME.isoformat() if CORE_LAST_START_TIME else None,
        container_start_time=APP_START_TIME.isoformat(),
        uptime_data=uptime_data,
        core_status=core_status,
        library_shows=library_shows,
        library_movies=library_movies,
        library_episodes=library_episodes,
        dvr_status=dvr_status_list,
    )


def _prometheus_escape_label_value(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _prometheus_metric_line(
    name: str, value: Any, labels: Optional[Dict[str, Any]] = None
) -> str:
    if labels:
        label_text = ",".join(
            f'{key}="{_prometheus_escape_label_value(label_value)}"'
            for key, label_value in labels.items()
        )
        return f"{name}{{{label_text}}} {value}"
    return f"{name} {value}"


@app.get("/metrics", include_in_schema=False)
async def metrics():
    token = _SYSTEM_INFO_SKIP_LIBRARY_COUNTS.set(True)
    try:
        system_info = await get_system_info(response=Response())
    finally:
        _SYSTEM_INFO_SKIP_LIBRARY_COUNTS.reset(token)
    per_dvr_stream_counts = {
        dvr.id or dvr.host: dvr.active_streams for dvr in system_info.dvr_status
    }
    active_streams = sum(per_dvr_stream_counts.values())
    uptime_seconds = int((datetime.now(_tz.utc) - APP_START_TIME).total_seconds())

    lines = [
        "# HELP channelwatch_uptime_seconds Seconds since the UI backend started.",
        "# TYPE channelwatch_uptime_seconds gauge",
        _prometheus_metric_line("channelwatch_uptime_seconds", uptime_seconds),
        "# HELP channelwatch_active_streams Number of currently active streams across all configured DVRs.",
        "# TYPE channelwatch_active_streams gauge",
        _prometheus_metric_line("channelwatch_active_streams", active_streams),
        "# HELP channelwatch_core_running Whether the core process is currently running (1=yes, 0=no).",
        "# TYPE channelwatch_core_running gauge",
        _prometheus_metric_line(
            "channelwatch_core_running",
            1 if system_info.core_status == "Running" else 0,
        ),
        "# HELP channelwatch_configured_dvrs Number of enabled DVRs configured in settings.",
        "# TYPE channelwatch_configured_dvrs gauge",
        _prometheus_metric_line(
            "channelwatch_configured_dvrs", len(system_info.dvr_status)
        ),
    ]

    if system_info.disk_free_gb is not None:
        lines.extend(
            [
                "# HELP channelwatch_disk_free_bytes Free DVR storage bytes aggregated across configured DVRs.",
                "# TYPE channelwatch_disk_free_bytes gauge",
                _prometheus_metric_line(
                    "channelwatch_disk_free_bytes",
                    int(system_info.disk_free_gb * BYTES_PER_GIB),
                    {"scope": "all"},
                ),
            ]
        )

    if system_info.disk_total_gb is not None:
        lines.extend(
            [
                "# HELP channelwatch_disk_total_bytes Total DVR storage bytes aggregated across configured DVRs.",
                "# TYPE channelwatch_disk_total_bytes gauge",
                _prometheus_metric_line(
                    "channelwatch_disk_total_bytes",
                    int(system_info.disk_total_gb * BYTES_PER_GIB),
                    {"scope": "all"},
                ),
            ]
        )

    if system_info.disk_usage_gb is not None:
        lines.extend(
            [
                "# HELP channelwatch_disk_used_bytes Used DVR storage bytes aggregated across configured DVRs.",
                "# TYPE channelwatch_disk_used_bytes gauge",
                _prometheus_metric_line(
                    "channelwatch_disk_used_bytes",
                    int(system_info.disk_usage_gb * BYTES_PER_GIB),
                    {"scope": "all"},
                ),
            ]
        )

    lines.extend(
        [
            "# HELP channelwatch_dvr_connected Whether each configured DVR is reachable (1=yes, 0=no).",
            "# TYPE channelwatch_dvr_connected gauge",
        ]
    )
    for dvr in system_info.dvr_status:
        labels = {
            "dvr_id": dvr.id or dvr.host,
            "dvr_name": dvr.name,
            "host": dvr.host,
            "port": dvr.port,
        }
        lines.append(
            _prometheus_metric_line(
                "channelwatch_dvr_connected",
                1 if dvr.connected else 0,
                labels,
            )
        )

        if dvr.disk_free_gb is not None:
            lines.append(
                _prometheus_metric_line(
                    "channelwatch_disk_free_bytes",
                    int(dvr.disk_free_gb * BYTES_PER_GIB),
                    labels,
                )
            )
        if dvr.disk_total_gb is not None:
            lines.append(
                _prometheus_metric_line(
                    "channelwatch_disk_total_bytes",
                    int(dvr.disk_total_gb * BYTES_PER_GIB),
                    labels,
                )
            )
        if dvr.disk_total_gb is not None and dvr.disk_free_gb is not None:
            lines.append(
                _prometheus_metric_line(
                    "channelwatch_disk_used_bytes",
                    int((dvr.disk_total_gb - dvr.disk_free_gb) * BYTES_PER_GIB),
                    labels,
                )
            )
        lines.append(
            _prometheus_metric_line(
                "channelwatch_active_streams",
                per_dvr_stream_counts.get(dvr.id or dvr.host, 0),
                labels,
            )
        )

    dvrs_with_version = [d for d in system_info.dvr_status if d.version]
    if dvrs_with_version:
        lines.extend(
            [
                "# HELP channelwatch_dvr_version_info DVR version and compatibility status (value is always 1).",
                "# TYPE channelwatch_dvr_version_info gauge",
            ]
        )
        for dvr in dvrs_with_version:
            compatible_val = (
                "1"
                if dvr.version_compatible is True
                else "0"
                if dvr.version_compatible is False
                else "unknown"
            )
            lines.append(
                _prometheus_metric_line(
                    "channelwatch_dvr_version_info",
                    1,
                    {
                        "dvr_id": dvr.id or dvr.host,
                        "dvr_name": dvr.name,
                        "version": dvr.version,
                        "compatible": compatible_val,
                    },
                )
            )

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


class RecordingInfo(BaseModel):
    id: str
    title: str
    start_time: int
    end_time: int = 0
    channel: str
    scheduled_time: str
    image: str = ""
    artwork_fallback_exhausted: bool = False
    dvr_id: str = ""
    dvr_name: str = ""


def _format_ics_text(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _format_ics_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=_tz.utc)
    else:
        value = value.astimezone(_tz.utc)
    return value.strftime("%Y%m%dT%H%M%SZ")


def _recording_stop_time(recording: dict[str, Any], start_time: int) -> int:
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    stop_time = _safe_int(recording.get("stop_time", 0) or recording.get("StopTime", 0))
    if stop_time > start_time:
        return stop_time

    airing = (
        recording.get("Airing", {}) if isinstance(recording.get("Airing"), dict) else {}
    )
    duration = _safe_int(
        recording.get("Duration", 0)
        or airing.get("Duration", 0)
        or recording.get("duration", 0)
    )
    if duration > 0:
        return start_time + duration
    return start_time + 60


def _recording_artwork_candidate(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return ""


def _resolve_recording_artwork(
    recording: dict[str, Any], channel_logo: str, rec_pref: str
) -> tuple[str, bool]:
    if rec_pref == "none":
        return "", False

    airing = (
        recording.get("Airing", {}) if isinstance(recording.get("Airing"), dict) else {}
    )
    candidates: list[str] = []
    for source in (recording, airing):
        for key in (
            "image_url",
            "ImageURL",
            "image",
            "Image",
            "icon_url",
            "IconURL",
            "thumbnail_url",
            "ThumbnailURL",
            "thumb",
            "Thumb",
        ):
            candidates.append(_recording_artwork_candidate(source, key))
    candidates.append(channel_logo)

    for candidate in candidates:
        if candidate.strip():
            return candidate, False
    return "", True


def _channel_logo_candidate(channel: dict[str, Any]) -> str:
    for key in (
        "logo_url",
        "LogoURL",
        "image_url",
        "ImageURL",
        "image",
        "Image",
        "icon_url",
        "IconURL",
    ):
        value = channel.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


async def _collect_upcoming_recordings(limit: int = 250) -> list[RecordingInfo]:
    upcoming_recordings: list[RecordingInfo] = []
    if CORE_APP_AVAILABLE:
        from core.helpers.config import get_settings as _get_core_settings

        settings = await asyncio.to_thread(_get_core_settings)
    else:
        settings = await _load_settings_async()

    if not settings:
        return []

    servers = await _get_dvr_servers_async()
    if not servers:
        return []

    from zoneinfo import ZoneInfo

    user_tz = ZoneInfo(settings.tz) if settings and settings.tz else ZoneInfo("UTC")
    current_time = int(datetime.now(_tz.utc).timestamp())
    rec_pref = (
        getattr(settings, "recording_card_image", "program") if settings else "program"
    )

    async def _collect_for_dvr(entry) -> list[RecordingInfo]:
        dvr_id, dvr_name, dvr_url = entry
        dvr_recordings: list[RecordingInfo] = []
        try:
            channel_map = {}
            channel_logo_map = {}
            try:
                channel_response = await _dvr_http_client.get(
                    f"{dvr_url}/api/v1/channels", timeout=5
                )
                if channel_response.status_code == 200:
                    for channel in channel_response.json():
                        channel_number = None
                        if "number" in channel:
                            channel_number = str(channel["number"])
                        ch_name = channel.get("name", "Unknown Channel")
                        ch_logo = _channel_logo_candidate(channel)
                        if channel_number:
                            channel_map[channel_number] = ch_name
                            channel_logo_map[channel_number] = ch_logo
                        if "id" in channel:
                            channel_id = str(channel["id"])
                            if channel_id:
                                channel_map[channel_id] = ch_name
                                channel_logo_map[channel_id] = ch_logo
            except Exception:
                pass

            jobs_response = await _dvr_http_client.get(f"{dvr_url}/dvr/jobs", timeout=5)
            if jobs_response.status_code != 200:
                return []

            for recording in jobs_response.json():
                try:
                    start_time = int(recording.get("Time", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if start_time <= current_time:
                    continue

                title = recording.get("Name", "Untitled Recording")
                channel_name = "Unknown Channel"
                channel_number = None

                if (
                    "Channels" in recording
                    and recording["Channels"]
                    and len(recording["Channels"]) > 0
                ):
                    channel_value = recording["Channels"][0]
                    if channel_value is not None:
                        channel_number = str(channel_value)
                elif "Channel" in recording and recording["Channel"]:
                    channel_value = recording["Channel"]
                    if channel_value is not None:
                        channel_number = str(channel_value)
                elif (
                    "Airing" in recording
                    and isinstance(recording["Airing"], dict)
                    and "Channel" in recording["Airing"]
                ):
                    channel_value = recording["Airing"]["Channel"]
                    if channel_value is not None:
                        channel_number = str(channel_value)

                if channel_number in channel_map:
                    channel_name = channel_map[channel_number]
                elif channel_number:
                    channel_name = f"Channel {channel_number}"

                recording_datetime = datetime.fromtimestamp(start_time, tz=user_tz)
                today = datetime.now(user_tz).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                tomorrow = today + timedelta(days=1)

                if recording_datetime.date() == today.date():
                    date_prefix = "Today"
                elif recording_datetime.date() == tomorrow.date():
                    date_prefix = "Tomorrow"
                else:
                    date_prefix = recording_datetime.strftime("%b %d")

                time_str = recording_datetime.strftime("%I:%M %p")
                scheduled_time = f"{date_prefix} at {time_str}"

                rec_image, artwork_fallback_exhausted = _resolve_recording_artwork(
                    recording,
                    channel_logo_map.get(channel_number or "", ""),
                    rec_pref,
                )

                dvr_recordings.append(
                    RecordingInfo(
                        id=recording.get("ID", ""),
                        title=title,
                        start_time=start_time,
                        end_time=_recording_stop_time(recording, start_time),
                        channel=channel_name,
                        scheduled_time=scheduled_time,
                        image=rec_image,
                        artwork_fallback_exhausted=artwork_fallback_exhausted,
                        dvr_id=dvr_id,
                        dvr_name=dvr_name,
                    )
                )
        except Exception:
            pass
        return dvr_recordings

    for dvr_recordings in await _bounded_dvr_probe_gather(servers, _collect_for_dvr):
        upcoming_recordings.extend(dvr_recordings)

    upcoming_recordings.sort(key=lambda x: x.start_time)
    return upcoming_recordings[:limit]


async def _load_recent_activity_items(
    *, hours: int = 24, limit: int = 250
) -> list[AlertHistoryItem]:
    engine = _get_activity_db_engine()
    if engine is not None:
        since = datetime.now(_tz.utc) - timedelta(hours=hours) if hours > 0 else None
        rows, _ = await asyncio.to_thread(
            _query_activity_db,
            engine,
            offset=0,
            limit=limit,
            sort_desc=True,
            since=since,
        )
        return [_activity_event_to_item(r) for r in rows]

    with _activity_lock:
        history_snapshot = list(ACTIVITY_HISTORY)
    if hours <= 0:
        return history_snapshot[:limit]
    cutoff_time = datetime.now(_tz.utc) - timedelta(hours=hours)
    recent_items = [
        item
        for item in history_snapshot
        if _parse_history_timestamp(item.timestamp) >= cutoff_time
    ]
    return recent_items[:limit]


def _normalize_feed_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_tz.utc)
    return value.astimezone(_tz.utc)


def _xml_text(value: Any) -> str:
    return xml_escape(str(value or ""))


def _xml_attr(value: Any) -> str:
    return xml_escape(str(value or ""), {'"': "&quot;", "'": "&apos;"})


def _activity_feed_item_summary(item: AlertHistoryItem) -> str:
    return f"Activity: {item.title or item.type or 'ChannelWatch event'}"


def _activity_feed_item_description(item: AlertHistoryItem) -> str:
    description_parts = []
    if item.message:
        description_parts.append(item.message)
    if item.channel_name:
        description_parts.append(f"Channel: {item.channel_name}")
    if item.device_name:
        description_parts.append(f"Device: {item.device_name}")
    if item.dvr_name:
        description_parts.append(f"DVR: {item.dvr_name}")
    return "\n".join(description_parts) or (
        item.title or item.type or "ChannelWatch activity"
    )


def _activity_feed_item_id(item: AlertHistoryItem) -> str:
    if item.id:
        return item.id
    return f"{item.type or 'activity'}-{item.timestamp}-{item.title or 'event'}"


def _feed_url(request: Request, path: str) -> str:
    return str(request.base_url).rstrip("/") + path


def _require_tokenized_feed_access(
    settings: Any, *, enabled_field: str, token_field: str, feed_name: str, token: str
) -> None:
    if not getattr(settings, enabled_field, False):
        raise structured_error(
            ErrorCode.FEED_DISABLED, message=f"{feed_name} is disabled"
        )

    configured_token = str(getattr(settings, token_field, "") or "").strip()
    if not configured_token or not secrets.compare_digest(token, configured_token):
        raise structured_error(
            ErrorCode.FEED_TOKEN_INVALID, message=f"Invalid {feed_name} token"
        )


async def _render_activity_rss_feed(request: Request) -> str:
    items = await _load_recent_activity_items(hours=24, limit=250)
    feed_link = _feed_url(request, "/api/activity-history")
    self_link = str(request.url)
    latest = datetime.now(_tz.utc)
    rendered_items: list[str] = []

    for item in items:
        try:
            published_at = _normalize_feed_datetime(
                _parse_history_timestamp(item.timestamp)
            )
        except Exception:
            continue

        latest = max(latest, published_at)
        rendered_items.append(
            "\n".join(
                [
                    "    <item>",
                    f"      <title>{_xml_text(_activity_feed_item_summary(item))}</title>",
                    f"      <description>{_xml_text(_activity_feed_item_description(item))}</description>",
                    f'      <guid isPermaLink="false">{_xml_text(_activity_feed_item_id(item))}</guid>',
                    f"      <pubDate>{_xml_text(format_datetime(published_at))}</pubDate>",
                    f"      <category>{_xml_text(item.type or 'channelwatch')}</category>",
                    "    </item>",
                ]
            )
        )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        "    <title>ChannelWatch Recent Activity</title>",
        "    <description>Recent ChannelWatch activity from the last 24 hours.</description>",
        f"    <link>{_xml_text(feed_link)}</link>",
        f'    <atom:link xmlns:atom="http://www.w3.org/2005/Atom" href="{_xml_attr(self_link)}" rel="self" type="application/rss+xml" />',
        f"    <lastBuildDate>{_xml_text(format_datetime(latest))}</lastBuildDate>",
    ]
    lines.extend(rendered_items)
    lines.extend(["  </channel>", "</rss>"])
    return "\n".join(lines) + "\n"


async def _render_activity_atom_feed(request: Request) -> str:
    items = await _load_recent_activity_items(hours=24, limit=250)
    self_link = str(request.url)
    history_link = _feed_url(request, "/api/activity-history")
    latest = datetime.now(_tz.utc)
    rendered_entries: list[str] = []

    for item in items:
        try:
            updated_at = _normalize_feed_datetime(
                _parse_history_timestamp(item.timestamp)
            )
        except Exception:
            continue

        latest = max(latest, updated_at)
        rendered_entries.append(
            "\n".join(
                [
                    "  <entry>",
                    f"    <id>tag:channelwatch:{_xml_text(_activity_feed_item_id(item))}</id>",
                    f"    <title>{_xml_text(_activity_feed_item_summary(item))}</title>",
                    f"    <updated>{_xml_text(updated_at.isoformat())}</updated>",
                    f"    <summary>{_xml_text(_activity_feed_item_description(item))}</summary>",
                    f'    <category term="{_xml_attr(item.type or "channelwatch")}" />',
                    "  </entry>",
                ]
            )
        )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        "  <id>tag:channelwatch:recent-activity</id>",
        "  <title>ChannelWatch Recent Activity</title>",
        f"  <updated>{_xml_text(latest.isoformat())}</updated>",
        f'  <link rel="self" href="{_xml_attr(self_link)}" />',
        f'  <link rel="alternate" href="{_xml_attr(history_link)}" />',
        "  <author><name>ChannelWatch</name></author>",
        "  <subtitle>Recent ChannelWatch activity from the last 24 hours.</subtitle>",
    ]
    lines.extend(rendered_entries)
    lines.append("</feed>")
    return "\n".join(lines) + "\n"


def _build_ics_event(
    *,
    uid: str,
    stamp: datetime,
    start: datetime,
    end: datetime,
    summary: str,
    description: str,
    categories: str,
) -> str:
    return "\r\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{_format_ics_text(uid)}",
            f"DTSTAMP:{_format_ics_timestamp(stamp)}",
            f"DTSTART:{_format_ics_timestamp(start)}",
            f"DTEND:{_format_ics_timestamp(end)}",
            f"SUMMARY:{_format_ics_text(summary)}",
            f"DESCRIPTION:{_format_ics_text(description)}",
            f"CATEGORIES:{_format_ics_text(categories)}",
            "END:VEVENT",
        ]
    )


async def _render_calendar_feed() -> str:
    now = datetime.now(_tz.utc)
    recordings = await _collect_upcoming_recordings(limit=250)
    recent_activity = await _load_recent_activity_items(hours=24, limit=250)

    events: list[tuple[datetime, str]] = []
    for recording in recordings:
        start = datetime.fromtimestamp(recording.start_time, tz=_tz.utc)
        end = datetime.fromtimestamp(
            recording.end_time or (recording.start_time + 60), tz=_tz.utc
        )
        description_parts = [f"Channel: {recording.channel}"]
        if recording.dvr_name:
            description_parts.append(f"DVR: {recording.dvr_name}")
        description_parts.append(f"Scheduled: {recording.scheduled_time}")
        events.append(
            (
                start,
                _build_ics_event(
                    uid=f"recording-{recording.dvr_id or 'global'}-{recording.id or recording.start_time}@channelwatch",
                    stamp=now,
                    start=start,
                    end=end,
                    summary=f"Recording: {recording.title}",
                    description="\n".join(description_parts),
                    categories="recording,schedule",
                ),
            )
        )

    for item in recent_activity:
        try:
            start = _parse_history_timestamp(item.timestamp)
        except Exception:
            continue
        end = start + timedelta(minutes=1)
        description_parts = []
        if item.message:
            description_parts.append(item.message)
        if item.channel_name:
            description_parts.append(f"Channel: {item.channel_name}")
        if item.device_name:
            description_parts.append(f"Device: {item.device_name}")
        if item.dvr_name:
            description_parts.append(f"DVR: {item.dvr_name}")
        events.append(
            (
                start,
                _build_ics_event(
                    uid=f"activity-{item.id or uuid.uuid4()}@channelwatch",
                    stamp=now,
                    start=start,
                    end=end,
                    summary=f"Activity: {item.title}",
                    description="\n".join(description_parts) or item.title,
                    categories=f"activity,{item.type or 'channelwatch'}",
                ),
            )
        )

    events.sort(key=lambda entry: entry[0])
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ChannelWatch//Calendar Feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ChannelWatch",
    ]
    lines.extend(event for _, event in events)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@app.get("/api/recordings/upcoming", response_model=List[RecordingInfo], tags=["DVR"])
async def get_upcoming_recordings(response: Response, limit: int = 250):
    response.headers["X-Deprecated-API"] = "Use /api/v1/"
    return await _collect_upcoming_recordings(limit=limit)


# Canonical feed URLs live under /api/v1/feeds/. Bare aliases reuse these
# handlers so token/query behavior stays consistent for existing subscribers.
@app.get("/api/v1/feeds/calendar.ics", tags=["Feeds"])
@app.get("/api/v1/calendar.ics", include_in_schema=False)
async def get_calendar_feed(token: str = Query(default="")):
    settings = await _load_settings_async()
    _require_tokenized_feed_access(
        settings,
        enabled_field="ics_feed_enabled",
        token_field="ics_feed_token",
        feed_name="ICS feed",
        token=token,
    )

    return Response(
        content=await _render_calendar_feed(),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


# Canonical feed URLs live under /api/v1/feeds/. Bare aliases reuse these
# handlers so token/query behavior stays consistent for existing subscribers.
@app.get("/api/v1/feeds/activity.rss", tags=["Feeds"])
@app.get("/api/v1/feed.rss", include_in_schema=False)
async def get_activity_rss_feed(request: Request, token: str = Query(default="")):
    settings = await _load_settings_async()
    _require_tokenized_feed_access(
        settings,
        enabled_field="rss_feed_enabled",
        token_field="rss_feed_token",
        feed_name="RSS feed",
        token=token,
    )

    return Response(
        content=await _render_activity_rss_feed(request),
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


# Canonical feed URLs live under /api/v1/feeds/. Bare aliases reuse these
# handlers so token/query behavior stays consistent for existing subscribers.
@app.get("/api/v1/feeds/activity.atom", tags=["Feeds"])
@app.get("/api/v1/feed.atom", include_in_schema=False)
async def get_activity_atom_feed(request: Request, token: str = Query(default="")):
    settings = await _load_settings_async()
    _require_tokenized_feed_access(
        settings,
        enabled_field="rss_feed_enabled",
        token_field="rss_feed_token",
        feed_name="Atom feed",
        token=token,
    )

    return Response(
        content=await _render_activity_atom_feed(request),
        media_type="application/atom+xml; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/recordings/active", response_model=int, tags=["DVR"])
async def get_active_recordings_count():
    """Returns the count of currently active recordings across all DVRs."""
    servers = await _get_dvr_servers_async()
    if not servers:
        return 0

    current_time = int(datetime.now().timestamp())

    async def _count_for_dvr(entry):
        _dvr_id, _dvr_name, dvr_url = entry
        try:
            response = await _dvr_http_client.get(f"{dvr_url}/dvr/jobs", timeout=5)
            if response.status_code == 200:
                active_count = 0
                for recording in response.json():
                    try:
                        start_time = int(recording.get("Time", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    stop_time = _recording_stop_time(recording, start_time)
                    if start_time <= current_time and stop_time > current_time:
                        active_count += 1
                return active_count
        except Exception:
            pass
        return 0

    return sum(await _bounded_dvr_probe_gather(servers, _count_for_dvr))


@app.get("/api/streams/active", response_model=int, tags=["DVR"])
async def get_active_streams_count():
    """Returns the total count of active streams across all DVRs."""
    return sum((await _get_per_dvr_active_stream_counts()).values())


async def _get_per_dvr_active_stream_counts() -> Dict[str, int]:
    servers = await _get_dvr_servers_async()
    if not servers:
        return {}

    async def _count_for_dvr(entry):
        dvr_id, _dvr_name, dvr_url = entry
        try:
            resp = await _dvr_http_client.get(f"{dvr_url}/dvr", timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                return dvr_id, len(data.get("activity", {}))
        except Exception:
            pass
        return dvr_id, 0

    return dict(await _bounded_dvr_probe_gather(servers, _count_for_dvr))


@app.get("/api/streams/details", tags=["DVR"])
async def get_active_streams_details(response: Response):
    response.headers["X-Deprecated-API"] = "Use /api/v1/"
    import time as _time

    servers = await _get_dvr_servers_async()
    if not servers:
        return {
            "total": 0,
            "watching": [],
            "recording": [],
            "subtitle": "No active streams",
            "image": "",
        }

    if CORE_APP_AVAILABLE:
        from core.helpers.config import CoreSettings

        CoreSettings._instance = None
        settings = _get_core_settings_sync()
    else:
        settings = await _load_settings_async()

    stream_pref = (
        getattr(settings, "stream_card_image", "program") if settings else "program"
    )
    watching = []
    recording = []

    async def _collect_streams_for_dvr(entry):
        _dvr_id, _dvr_name, dvr_url = entry
        dvr_watching = []
        dvr_recording = []
        try:
            resp = await _dvr_http_client.get(f"{dvr_url}/dvr", timeout=3)
            if resp.status_code != 200:
                return dvr_watching, dvr_recording

            data = resp.json()
            activity = data.get("activity", {})
            if not activity:
                return dvr_watching, dvr_recording

            channel_images = {}
            channel_logos = {}
            try:
                ch_resp = await _dvr_http_client.get(
                    f"{dvr_url}/api/v1/channels", timeout=3
                )
                if ch_resp.status_code == 200:
                    for ch in ch_resp.json():
                        num = str(ch.get("number", ""))
                        img = (
                            ch.get("image_url", "")
                            or ch.get("logo_url", "")
                            or ch.get("image", "")
                        )
                        if num and img:
                            channel_logos[num] = img
            except Exception:
                pass

            if stream_pref == "program":
                try:
                    guide_resp = await _dvr_http_client.get(
                        f"{dvr_url}/devices/ANY/guide/now", timeout=5
                    )
                    if guide_resp.status_code == 200:
                        now = int(_time.time())
                        for item in guide_resp.json():
                            for airing in item.get("Airings", []):
                                ch = airing.get("Channel", "")
                                start = airing.get("Time", 0)
                                dur = airing.get("Duration", 0)
                                img = airing.get("Image", "")
                                if ch and start <= now <= start + dur:
                                    channel_images[ch] = (
                                        img if img else channel_logos.get(ch, "")
                                    )
                except Exception:
                    pass
                for ch_num, logo in channel_logos.items():
                    if ch_num not in channel_images:
                        channel_images[ch_num] = logo
            elif stream_pref == "channel":
                channel_images = channel_logos

            for sid, val in activity.items():
                if val.startswith("Watching"):
                    device_match = re.search(r"from\s+([^:(]+)", val)
                    channel_match = re.search(
                        r"ch\d+\s+([^:]+?)(?:\s+from|\s*[:(])", val
                    )
                    ch_num_match = re.search(r"ch(\d+)", val)
                    device = (
                        device_match.group(1).strip() if device_match else "Unknown"
                    )
                    channel = (
                        channel_match.group(1).strip() if channel_match else "Unknown"
                    )
                    ch_num = ch_num_match.group(1) if ch_num_match else ""
                    image = channel_images.get(ch_num, "")
                    dvr_watching.append(
                        {"device": device, "channel": channel, "image": image}
                    )
                elif val.startswith("Recording"):
                    title_match = re.search(r"for\s+(.+?)\s+until\s+", val)
                    until_match = re.search(r"until\s+(.+?)(?=:\s*buf|$)", val)
                    title = title_match.group(1) if title_match else "Unknown"
                    until = until_match.group(1).strip() if until_match else ""
                    dvr_recording.append({"title": title, "until": until})
        except Exception:
            pass
        return dvr_watching, dvr_recording

    for dvr_watching, dvr_recording in await _bounded_dvr_probe_gather(
        servers, _collect_streams_for_dvr
    ):
        watching.extend(dvr_watching)
        recording.extend(dvr_recording)

    total = len(watching) + len(recording)

    image = ""
    if watching and watching[0].get("image"):
        image = watching[0]["image"]

    if total == 0:
        subtitle = "No active streams"
    elif total == 1:
        if watching:
            subtitle = f"{watching[0]['device']} watching {watching[0]['channel']}"
        else:
            subtitle = (
                f"Recording {recording[0]['title']} until {recording[0]['until']}"
            )
    elif len(watching) > 0 and len(recording) > 0:
        subtitle = f"{len(watching)} watching, {len(recording)} recording"
    elif len(watching) == 1:
        subtitle = f"{watching[0]['device']} watching {watching[0]['channel']}"
    elif len(watching) > 1:
        subtitle = f"{watching[0]['device']} watching {watching[0]['channel']} +{len(watching) - 1} more"
    elif len(recording) == 1:
        subtitle = f"Recording {recording[0]['title']} until {recording[0]['until']}"
    else:
        subtitle = f"{len(recording)} recordings active"

    return {
        "total": total,
        "watching": watching,
        "recording": recording,
        "subtitle": subtitle,
        "image": image,
    }


# TESTING
class TestResult(BaseModel):
    test_name: str
    success: bool
    message: str


def run_test_background(test_name: str) -> TestResult:
    """Helper function to run a test in the background."""
    message = ""
    success = False
    if not CORE_APP_AVAILABLE:
        return TestResult(
            test_name=test_name,
            success=False,
            message="Core app components not available for testing.",
        )

    # Ensure test output goes to channelwatch.log
    import core.helpers.logging as _logging

    if not _logging.log_handler:
        settings = load_settings()
        _logging.setup_logging(
            str(CONFIG_DIR), retention_days=settings.log_retention_days or 7
        )

    try:
        # Reset singleton so it re-reads from file (picks up UI changes)
        from core.helpers.config import CoreSettings

        CoreSettings._instance = None
        settings = _get_core_settings_sync()

        if settings is None:
            raise ValueError("Failed to load core settings for test.")

        servers = _get_dvr_servers()
        if not servers:
            raise ValueError("DVR Host not configured in settings.")
        dvr_id, dvr_name, base_url = servers[0]
        # Extract host/port from first DVR for test runner
        dvr_connections = (
            settings.get_dvr_connections()
            if hasattr(settings, "get_dvr_connections")
            else []
        )
        test_dvr = dvr_connections[0] if dvr_connections else None
        first_dvr = (
            (settings.dvr_servers or [{}])[0]
            if hasattr(settings, "dvr_servers")
            else {}
        )
        host = first_dvr.get("host", "")
        port = first_dvr.get("port", 8089)
        if not host:
            raise ValueError("DVR Host not configured in settings.")

        if test_name == "Test Connectivity":
            test_key = "connectivity"
            alert_mgr_needed = False
        elif test_name == "Test API Endpoints":
            test_key = "api"
            alert_mgr_needed = False
        elif test_name == "Test Channel Watching Alert":
            test_key = "Channel-Watching"
            alert_mgr_needed = True
        elif test_name == "Test VOD Watching Alert":
            test_key = "VOD-Watching"
            alert_mgr_needed = True
        elif test_name == "Test Disk Space Alert":
            test_key = "Disk-Space"
            alert_mgr_needed = True
        elif test_name == "Test Recording Events Alert":
            test_key = "Recording-Events"
            alert_mgr_needed = True
        elif test_name == "Test Recording Scheduled Alert":
            test_key = "Recording-Scheduled"
            alert_mgr_needed = True
        elif test_name == "Test Recording Started Alert":
            test_key = "Recording-Started"
            alert_mgr_needed = True
        elif test_name == "Test Recording Completed Alert":
            test_key = "Recording-Completed"
            alert_mgr_needed = True
        elif test_name == "Test Recording Stopped Alert":
            test_key = "Recording-Stopped"
            alert_mgr_needed = True
        elif test_name == "Test Recording Cancelled Alert":
            test_key = "Recording-Cancelled"
            alert_mgr_needed = True
        else:
            raise ValueError(f"Unknown test name received: {test_name}")

        from core.diagnostics import run_test

        if alert_mgr_needed:
            from copy import copy as _copy
            from core.helpers.initialize import (
                initialize_notifications,
                initialize_alerts,
            )
            from core.notifications.notification import NotificationManager

            test_settings = _copy(settings)
            if test_dvr and getattr(test_dvr, "overrides", None):
                for key, value in test_dvr.overrides.items():
                    if hasattr(test_settings, key):
                        setattr(test_settings, key, value)

            notification_manager = None
            selected_settings = test_settings

            if test_key == "Disk-Space":
                raw_test_route_override = getattr(
                    test_settings, "ds_test_route_override", ""
                )
                test_route_override = (
                    str(raw_test_route_override).strip()
                    if raw_test_route_override is not None
                    else ""
                )

                if test_route_override:
                    override_settings = _copy(test_settings)
                    for field_name in (
                        "apprise_pushover",
                        "apprise_discord",
                        "apprise_email",
                        "apprise_email_to",
                        "apprise_telegram",
                        "apprise_slack",
                        "apprise_gotify",
                        "apprise_matrix",
                        "apprise_custom",
                    ):
                        setattr(override_settings, field_name, "")

                    override_settings.apprise_custom = test_route_override
                    override_notification_manager = initialize_notifications(
                        override_settings, test_mode=True
                    )

                    if (
                        override_notification_manager
                        and override_notification_manager.get_active_providers()
                    ):
                        notification_manager = override_notification_manager
                        selected_settings = override_settings

            if notification_manager is None:
                notification_manager = initialize_notifications(
                    test_settings, test_mode=True
                )

            if not notification_manager:
                notification_manager = NotificationManager(
                    rate_limit=test_settings.global_rate_limit,
                    rate_window=test_settings.global_rate_window,
                )

            alert_manager = initialize_alerts(
                notification_manager, selected_settings, test_mode=True, dvr=test_dvr
            )
            if not alert_manager:
                return TestResult(
                    test_name=test_name,
                    success=False,
                    message="Failed to initialize alert manager",
                )

            success = run_test(test_key, host, port, alert_manager)
        else:
            success = run_test(test_key, host, port, None)

        message = f"Test '{test_name}' {'succeeded' if success else 'failed'}"

    except Exception as e:
        import traceback

        print(f"[TEST RUNNER] Error running test '{test_name}': {e}", flush=True)
        traceback.print_exc()
        success = False
        message = f"Error running test '{test_name}': {e}. Check container logs."

    return TestResult(test_name=test_name, success=success, message=message)


@app.post(
    "/api/run_test/{test_name_url}",
    response_model=TestResult,
    tags=["Testing"],
    dependencies=[require_role("operator")],
)
async def trigger_test_endpoint(test_name_url: str):
    """Runs a specified test and returns the result."""
    if not CORE_APP_AVAILABLE:
        raise structured_error(
            ErrorCode.CORE_NOT_AVAILABLE,
            message="Core app components not available for testing.",
        )

    test_name = test_name_url.replace("_", " ")

    import asyncio

    result = await asyncio.to_thread(run_test_background, test_name)
    return result


@app.get(
    "/api/recent-activity", response_model=List[AlertHistoryItem], tags=["Activity"]
)
async def get_recent_activity(response: Response, hours: int = 24, limit: int = 250):
    response.headers["X-Deprecated-API"] = "Use /api/v1/"
    try:
        return await _load_recent_activity_items(hours=hours, limit=limit)
    except Exception:
        raise structured_error(ErrorCode.ACTIVITY_FETCH_FAILED)


@app.get(
    "/api/activity-history", response_model=ActivityHistoryResponse, tags=["Activity"]
)
async def get_activity_history(
    response: Response,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    activity_type: Optional[str] = Query(default=None, alias="type"),
    search: Optional[str] = Query(default=None),
    sort_order: str = Query(default="desc", alias="sort"),
):
    response.headers["X-Deprecated-API"] = "Use /api/v1/"
    try:
        normalized_sort = sort_order.lower()
        if normalized_sort not in {"asc", "desc"}:
            raise structured_error(ErrorCode.ACTIVITY_SORT_INVALID)

        engine = _get_activity_db_engine()
        if engine is not None:
            rows, total = await asyncio.to_thread(
                _query_activity_db,
                engine,
                offset=offset,
                limit=limit,
                activity_type=activity_type,
                search=search,
                sort_desc=(normalized_sort == "desc"),
            )
            return ActivityHistoryResponse(
                items=[_activity_event_to_item(r) for r in rows],
                total=total,
                offset=offset,
                limit=limit,
            )

        with _activity_lock:
            history_snapshot = list(ACTIVITY_HISTORY)

        filtered_history = [
            item
            for item in history_snapshot
            if _activity_matches_type(item, activity_type)
            and _activity_matches_search(item, search)
        ]
        filtered_history.sort(key=_history_sort_key, reverse=normalized_sort == "desc")

        paginated_items = filtered_history[offset : offset + limit]
        return ActivityHistoryResponse(
            items=paginated_items,
            total=len(filtered_history),
            offset=offset,
            limit=limit,
        )
    except HTTPException:
        raise
    except Exception:
        raise structured_error(ErrorCode.ACTIVITY_FETCH_FAILED)


@app.post(
    "/api/clear-activity-history",
    tags=["Activity"],
    dependencies=[require_role("operator")],
)
async def clear_activity_history():
    global ACTIVITY_HISTORY
    try:
        with _activity_lock:
            ACTIVITY_HISTORY = []

        def _clear_legacy_history_file():
            atomic_write_json(HISTORY_FILE, [], indent=2)

        await asyncio.to_thread(_clear_legacy_history_file)
        engine = _get_activity_db_engine()
        if engine is not None:

            def _clear_db():
                with _activity_db_get_session(engine) as session:
                    session.execute(_sql_delete(_ActivityEvent))
                    session.commit()

            await asyncio.to_thread(_clear_db)
        return {"message": "Activity history cleared successfully"}
    except Exception as e:
        print(f"[WebUI API] ERROR: Failed to clear activity history: {e}")
        raise structured_error(ErrorCode.ACTIVITY_CLEAR_FAILED)


_CSV_COLUMNS = [
    "id",
    "dvr_id",
    "dvr_name",
    "event_type",
    "title",
    "message",
    "timestamp",
    "channel_name",
    "channel_number",
    "device_name",
    "device_ip",
    "program_title",
    "image_url",
    "stream_source",
    "is_test",
]


def _csv_escape(value: object) -> str:
    s = "" if value is None else str(value)
    if any(ch in s for ch in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


def _iter_csv(engine, dvr_id: Optional[str]):
    yield ",".join(_CSV_COLUMNS) + "\r\n"
    activity_event = _ActivityEvent
    get_session = _activity_db_get_session
    sql_select = _sql_select
    sql_or = _sql_or
    sql_and = _sql_and
    if (
        activity_event is None
        or get_session is None
        or sql_select is None
        or sql_or is None
        or sql_and is None
    ):
        return

    batch_size = 500
    last_timestamp = None
    last_id = None
    while True:
        with get_session(engine) as session:
            stmt = sql_select(activity_event)
            if dvr_id:
                stmt = stmt.where(activity_event.dvr_id == dvr_id)
            if last_timestamp is not None and last_id is not None:
                stmt = stmt.where(
                    sql_or(
                        activity_event.timestamp > last_timestamp,
                        sql_and(
                            activity_event.timestamp == last_timestamp,
                            activity_event.id > last_id,
                        ),
                    )
                )
            stmt = stmt.order_by(
                activity_event.timestamp.asc(), activity_event.id.asc()
            ).limit(batch_size)
            rows = list(session.exec(stmt).all())
        if not rows:
            break
        for evt in rows:
            ts = evt.timestamp
            if isinstance(ts, datetime) and ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            row = [
                evt.id,
                evt.dvr_id,
                evt.dvr_name,
                evt.event_type,
                evt.title,
                evt.message,
                ts.isoformat() if isinstance(ts, datetime) else str(ts),
                evt.channel_name,
                evt.channel_number,
                evt.device_name,
                evt.device_ip,
                evt.program_title,
                evt.image_url,
                evt.stream_source,
                "true" if evt.is_test else "false",
            ]
            yield ",".join(_csv_escape(v) for v in row) + "\r\n"
        last = rows[-1]
        last_timestamp = last.timestamp
        last_id = last.id
        if len(rows) < batch_size:
            break


@app.get("/api/v1/history/export", tags=["Activity"])
async def export_history_csv(
    dvr_id: Optional[str] = Query(default=None),
    format: str = Query(default="csv"),
):
    if format.lower() != "csv":
        raise structured_error(ErrorCode.ACTIVITY_FORMAT_UNSUPPORTED)

    engine = _get_activity_db_engine()
    if engine is None:
        raise structured_error(ErrorCode.ACTIVITY_DB_UNAVAILABLE)

    filename = _content_disposition_filename(
        f"channelwatch-history-{dvr_id or 'all'}.csv"
    )

    def _sync_gen():
        yield from _iter_csv(engine, dvr_id)

    return StreamingResponse(
        _sync_gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class NotificationDeliveryItem(BaseModel):
    id: int = 0
    dvr_id: str = ""
    activity_event_id: Optional[str] = None
    provider_type: str = ""
    channel_id: str = ""
    channel: str = ""
    event_type: str = ""
    status: str = ""
    retry_count: int = 0
    payload_size: int = 0
    error: Optional[str] = None
    sent_at: str = ""


class NotificationLogResponse(BaseModel):
    items: List[NotificationDeliveryItem]
    total: int
    offset: int
    limit: int


def _delivery_row_to_item(row: Any) -> NotificationDeliveryItem:
    sent_at = getattr(row, "delivered_at", None)
    if isinstance(sent_at, datetime):
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=_tz.utc)
        sent_at_str = sent_at.isoformat()
    else:
        sent_at_str = str(sent_at) if sent_at else ""
    raw_status = getattr(row, "status", None)
    if not raw_status or raw_status == "delivered":
        status = "sent" if getattr(row, "delivered", False) else "failed"
    else:
        status = raw_status
    return NotificationDeliveryItem(
        id=row.id or 0,
        dvr_id=row.dvr_id or "",
        activity_event_id=row.activity_event_id,
        provider_type=row.provider_type or "",
        channel_id=row.channel_id or "",
        channel=getattr(row, "channel", "") or "",
        event_type=getattr(row, "event_type", "") or "",
        status=status,
        retry_count=getattr(row, "retry_count", 0) or 0,
        payload_size=getattr(row, "payload_size", 0) or 0,
        error=row.error_message,
        sent_at=sent_at_str,
    )


@app.get(
    "/api/v1/notification-log",
    response_model=NotificationLogResponse,
    tags=["Notifications"],
)
async def get_notification_log(
    dvr_id: Optional[str] = Query(default=None),
    channel: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
    until: Optional[str] = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    engine = _get_activity_db_engine()
    if engine is None or not _STORAGE_AVAILABLE or _query_delivery_log is None:
        return NotificationLogResponse(items=[], total=0, offset=offset, limit=limit)

    def _parse_dt(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            return dt
        except ValueError:
            return None

    rows, total = await asyncio.to_thread(
        _query_delivery_log,
        engine,
        dvr_id=dvr_id or None,
        channel=channel or None,
        status=status or None,
        since=_parse_dt(since),
        until=_parse_dt(until),
        offset=offset,
        limit=limit,
    )
    return NotificationLogResponse(
        items=[_delivery_row_to_item(r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


# LIVE LOGS
LOG_FILE = CONFIG_DIR / "channelwatch.log"
LOG_TAIL_MAX_LINES = 1000


def _tail_log_lines(path: Path, requested_lines: int) -> list[str]:
    line_count = max(1, min(int(requested_lines or 100), LOG_TAIL_MAX_LINES))
    chunk_size = 8192
    chunks: list[bytes] = []
    newline_count = 0

    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        position = f.tell()
        while position > 0 and newline_count <= line_count:
            read_size = min(chunk_size, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    data = b"".join(reversed(chunks))
    if not data:
        return []
    return data.decode(errors="replace").splitlines()[-line_count:]


def _tail_log_lines_if_available(path: Path, requested_lines: int) -> list[str]:
    if not path.is_file():
        return []
    return _tail_log_lines(path, requested_lines)


@app.get("/api/logs", dependencies=[require_role("operator")])
async def get_logs(lines: int = 100):
    """Return the last N lines from the ChannelWatch log file."""
    try:
        return {
            "lines": await asyncio.to_thread(
                _tail_log_lines_if_available, LOG_FILE, lines
            )
        }
    except (OSError, ValueError):
        return {"lines": []}


@app.get("/api/logs/download", dependencies=[require_role("operator")])
async def download_logs():
    """Download the full channelwatch.log file."""
    if not LOG_FILE.is_file():
        raise structured_error(ErrorCode.LOG_NOT_FOUND)
    return FileResponse(
        path=str(LOG_FILE),
        filename="channelwatch.log",
        media_type="text/plain",
    )


# SUPERVISOR INTEGRATION
SUPERVISOR_RUNTIME_DIR = os.environ.get("CHANNELWATCH_RUNTIME_DIR", "/tmp/channelwatch")
SUPERVISOR_SOCKET_FILE = os.environ.get(
    "CHANNELWATCH_SUPERVISOR_SOCKET_FILE",
    os.path.join(SUPERVISOR_RUNTIME_DIR, "supervisor.sock"),
)


class _UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            unix_socket.settimeout(self.timeout)
        unix_socket.connect(self.socket_path)
        self.sock = unix_socket


class _UnixSocketTransport(xmlrpc.client.Transport):
    def __init__(self, socket_path: str):
        super().__init__()
        self.socket_path = socket_path

    def make_connection(self, host):
        return _UnixSocketHTTPConnection(self.socket_path)


def _supervisor_exception_summary(exc: BaseException) -> str:
    if isinstance(exc, xmlrpc.client.ProtocolError):
        return f"ProtocolError {exc.errcode} {exc.errmsg}"
    if isinstance(exc, xmlrpc.client.Fault):
        return f"Fault {exc.faultCode}"
    return exc.__class__.__name__


def get_supervisor_proxy():
    socket_path = SUPERVISOR_SOCKET_FILE
    if not os.path.exists(socket_path):
        print(
            f"[WebUI Supervisor] WARNING: Supervisor socket unavailable"
            f" — socket not found or unreadable: {socket_path}"
        )
        return None
    try:
        server = xmlrpc.client.ServerProxy(
            "http://channelwatch-supervisor/RPC2",
            transport=_UnixSocketTransport(socket_path),
            allow_none=True,
        )
        return server
    except Exception as e:
        print(
            f"Failed to create Supervisor RPC proxy: {_supervisor_exception_summary(e)}"
        )
        return None


def _get_core_process_info_from_supervisor() -> Optional[dict[str, Any]]:
    proxy = get_supervisor_proxy()
    if not proxy:
        return None
    return cast(dict[str, Any], proxy.supervisor.getProcessInfo("core"))


def _signal_core_hot_reload() -> bool:
    server = get_supervisor_proxy()
    if not server:
        print(
            "[WebUI API] WARNING: Settings saved but core hot reload was not triggered (supervisor proxy unavailable)."
        )
        return False

    try:
        process_info = cast(dict[str, Any], server.supervisor.getProcessInfo("core"))
        pid = int(process_info.get("pid") or 0)
        state = str(process_info.get("statename", "Unknown")).upper()
        if pid <= 0 or state not in {"RUNNING", "STARTING"}:
            print(
                "[WebUI API] WARNING: Settings saved but core hot reload was not triggered "
                f"(core state={state!r}, pid={pid})."
            )
            return False

        os.kill(pid, signal.SIGHUP)
        print(
            f"[WebUI API] Info: Sent SIGHUP to core process {pid} for runtime config reload."
        )
        return True
    except ProcessLookupError:
        print(
            "[WebUI API] WARNING: Settings saved but core pid no longer exists for SIGHUP reload."
        )
        return False
    except Exception as e:
        print(
            "[WebUI API] WARNING: Settings saved but failed to signal core hot reload: "
            f"{_supervisor_exception_summary(e)}"
        )
        return False


def _save_settings_and_signal_reload(settings: AppSettings) -> bool:
    save_settings(settings)
    return _signal_core_hot_reload()


async def _save_settings_and_signal_reload_async(settings: AppSettings) -> bool:
    return await asyncio.to_thread(_save_settings_and_signal_reload, settings)


def _can_signal_pid_one_restart() -> bool:
    return os.name != "nt" and os.getpid() == 1


def _schedule_container_restart_for_update() -> bool:
    server = get_supervisor_proxy()
    can_signal_pid_one = _can_signal_pid_one_restart()
    if not server and not can_signal_pid_one:
        return False

    def delayed_restart():
        try:
            time.sleep(2)
            if server:
                server.supervisor.shutdown()
                return
            os.kill(1, signal.SIGTERM)
        except Exception as exc:
            print(
                "[WebUI API] ERROR: Failed to restart ChannelWatch after update: "
                f"{_supervisor_exception_summary(exc)}"
            )

    restart_thread = threading.Thread(target=delayed_restart)
    restart_thread.daemon = True
    restart_thread.start()
    return True


class UpdateApplyRequest(BaseModel):
    version: Optional[str] = None


def _update_center_healthcheck() -> bool:
    static_ui_dir = os.environ.get("CW_STATIC_UI_DIR", "").strip()
    if static_ui_dir and not Path(static_ui_dir).is_dir():
        return False
    return bool(CORE_APP_AVAILABLE)


def _build_update_manager():
    from core.helpers.migration import CURRENT_SCHEMA_VERSION as _SETTINGS_SCHEMA_VERSION
    from core.update_center import UpdateManager
    from .backup_restore import create_backup_zip

    return UpdateManager(
        config_dir=CONFIG_DIR,
        current_version=__version__,
        settings_schema_version=_SETTINGS_SCHEMA_VERSION,
        backup_callable=create_backup_zip,
        restart_callable=_schedule_container_restart_for_update,
        healthcheck_callable=_update_center_healthcheck,
    )


def _raise_update_error(exc: Exception, *, apply: bool = False, rollback: bool = False):
    from core.update_center import (
        UpdateBundleError,
        UpdateCenterError,
        UpdateLockedError,
        UpdateManifestError,
    )

    if isinstance(exc, UpdateLockedError):
        raise structured_error(ErrorCode.UPDATE_LOCKED)
    if isinstance(exc, (UpdateManifestError, UpdateBundleError)):
        raise structured_error(
            ErrorCode.UPDATE_APPLY_FAILED if apply else ErrorCode.UPDATE_CHECK_FAILED,
            message=str(exc),
        )
    if isinstance(exc, UpdateCenterError):
        raise structured_error(
            ErrorCode.UPDATE_ROLLBACK_FAILED if rollback else ErrorCode.UPDATE_APPLY_FAILED,
            message=str(exc),
        )
    raise structured_error(
        ErrorCode.UPDATE_ROLLBACK_FAILED if rollback else ErrorCode.UPDATE_APPLY_FAILED,
        message="Unexpected Update Center failure.",
    )


@app.get(
    "/api/v1/update/status",
    tags=["Updates"],
    dependencies=[require_role("admin")],
)
async def update_status():
    try:
        return await asyncio.to_thread(_build_update_manager().status)
    except Exception as exc:
        log.exception("Update status failed: %s", exc)
        _raise_update_error(exc)


@app.post(
    "/api/v1/update/check",
    tags=["Updates"],
    dependencies=[require_role("admin")],
)
async def update_check():
    try:
        return await asyncio.to_thread(_build_update_manager().check)
    except Exception as exc:
        log.exception("Update check failed: %s", exc)
        _raise_update_error(exc)


@app.post(
    "/api/v1/update/apply",
    status_code=202,
    tags=["Updates"],
    dependencies=[require_role("admin")],
)
async def update_apply(body: UpdateApplyRequest):
    try:
        result = await asyncio.to_thread(_build_update_manager().apply, body.version)
    except Exception as exc:
        log.exception("Update apply failed: %s", exc)
        _raise_update_error(exc, apply=True)

    if result.get("status") == "image_required":
        raise structured_error(ErrorCode.UPDATE_IMAGE_REQUIRED, message=result.get("message"))
    return result


@app.get(
    "/api/v1/update/jobs/{job_id}",
    tags=["Updates"],
    dependencies=[require_role("admin")],
)
async def update_job(job_id: str):
    status = await asyncio.to_thread(_build_update_manager().status)
    job = status.get("last_job")
    if not isinstance(job, dict) or job.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Update job not found.")
    return job


@app.post(
    "/api/v1/update/rollback",
    status_code=202,
    tags=["Updates"],
    dependencies=[require_role("admin")],
)
async def update_rollback():
    try:
        return await asyncio.to_thread(_build_update_manager().rollback)
    except Exception as exc:
        log.exception("Update rollback failed: %s", exc)
        _raise_update_error(exc, rollback=True)


# CONTROL ENDPOINTS
@app.post(
    "/api/restart_container",
    status_code=202,
    tags=["Control"],
    dependencies=[require_role("admin")],
)
async def restart_container():
    """Restart ChannelWatch"""
    server = get_supervisor_proxy()
    can_signal_pid_one = _can_signal_pid_one_restart()
    if not server and not can_signal_pid_one:
        raise structured_error(ErrorCode.SUPERVISOR_NOT_AVAILABLE)

    try:

        def delayed_restart():
            try:
                time.sleep(2)
                if server:
                    server.supervisor.shutdown()
                    return

                os.kill(1, signal.SIGTERM)
            except Exception as e:
                print(
                    "[WebUI API] ERROR: Failed to restart ChannelWatch: "
                    f"{_supervisor_exception_summary(e)}"
                )

        import threading

        restart_thread = threading.Thread(target=delayed_restart)
        restart_thread.daemon = True
        restart_thread.start()

        return {
            "message": "Restart initiated. The application will be unavailable for a few moments."
        }
    except Exception as e:
        print(
            "[WebUI API] ERROR: Failed to prepare ChannelWatch restart: "
            f"{_supervisor_exception_summary(e)}"
        )
        raise structured_error(ErrorCode.RESTART_FAILED)


@app.post(
    "/api/restart_core",
    status_code=202,
    tags=["Control"],
    dependencies=[require_role("admin")],
)
async def restart_core_process():
    """Uses Supervisor's XML-RPC interface to restart the core process."""
    global CORE_LAST_START_TIME
    server = await asyncio.to_thread(get_supervisor_proxy)
    if not server:
        raise structured_error(ErrorCode.SUPERVISOR_AUTH_MISSING)

    try:
        await asyncio.to_thread(server.supervisor.stopProcess, "core", True)
        await asyncio.sleep(1)
        await asyncio.to_thread(server.supervisor.startProcess, "core", True)

        CORE_LAST_START_TIME = datetime.now(_tz.utc)

        return {"message": "Restart command sent to process 'core'."}
    except ConnectionRefusedError:
        print(
            "[WebUI API] ERROR: Connection refused to Supervisor RPC socket. Is it running?"
        )
        raise structured_error(ErrorCode.SUPERVISOR_CONNECT_FAILED)
    except xmlrpc.client.Fault as err:
        if err.faultCode == 401:
            print(
                "[WebUI API] ERROR: Supervisor RPC authentication failed (401 Unauthorized)."
            )
            raise structured_error(ErrorCode.SUPERVISOR_AUTH_FAILED)
        else:
            print(
                f"[WebUI API] ERROR: Supervisor RPC fault: {err.faultCode} {err.faultString}"
            )
            raise structured_error(
                ErrorCode.SUPERVISOR_COMMAND_FAILED,
                message=f"Supervisor command failed: {err.faultString}",
            )
    except Exception as e:
        print(
            "[WebUI API] ERROR: Failed to send restart command via Supervisor: "
            f"{_supervisor_exception_summary(e)}"
        )
        if isinstance(e, AttributeError):
            raise structured_error(ErrorCode.SUPERVISOR_NOT_AVAILABLE)
        raise structured_error(ErrorCode.SUPERVISOR_COMMAND_FAILED)


# V1 DVR ENDPOINTS


class DvrListItem(BaseModel):
    id: str
    name: str
    host: str
    port: int
    enabled: bool = True


class PerDvrSystemInfo(BaseModel):
    dvr_id: str = ""
    dvr_name: str = ""
    host: str = ""
    port: int = 8089
    connected: bool = False
    version: Optional[str] = None
    version_compatible: Optional[bool] = None
    version_warning: Optional[str] = None
    disk_usage_percent: Optional[float] = None
    disk_usage_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    disk_severity: str = "normal"
    library_shows: int = 0
    library_movies: int = 0
    library_episodes: int = 0


class DvrHealthResponse(BaseModel):
    dvr_id: str
    dvr_name: str
    host: str
    port: int
    connected: bool
    version: Optional[str] = None
    version_compatible: Optional[bool] = None
    version_warning: Optional[str] = None
    disk_status: str = "unknown"
    disk_free_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    last_checked: str = ""
    last_event_at: Optional[str] = None
    last_freshness_at: Optional[str] = None
    last_freshness_source: Optional[str] = None
    freshness_age_seconds: Optional[float] = None
    freshness_status: str = "missing"
    monitoring_status: str = "missing"
    monitoring_ready: bool = False
    monitoring_reason: Optional[str] = None
    session_state_size: Optional[int] = None
    recent_alert_rate: Optional[float] = None


@app.get("/api/v1/dvrs", response_model=List[DvrListItem], tags=["DVR V1"])
async def list_dvrs_v1():
    settings = await _load_settings_async()
    servers = getattr(settings, "dvr_servers", None) or []
    result = []
    for s in servers:
        if isinstance(s, dict) and not s.get("deleted_at"):
            result.append(
                DvrListItem(
                    id=s.get("id", ""),
                    name=s.get("name", s.get("host", "")),
                    host=s.get("host", ""),
                    port=s.get("port", 8089),
                    enabled=s.get("enabled", True),
                )
            )
    return result


@app.get("/api/v1/dvrs/{dvr_id}", response_model=DVRStatus, tags=["DVR V1"])
async def get_dvr_v1(dvr_id: str):
    server = await _get_dvr_server_by_id_async(dvr_id)
    if server is None:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    sid, sname, surl = server
    host = surl.replace("http://", "").rsplit(":", 1)[0]
    port = int(surl.rsplit(":", 1)[1])
    s_version = None
    s_percent, s_total, s_free = None, None, None
    s_shows, s_movies, s_episodes = 0, 0, 0
    s_connected = False
    try:
        status_resp = await _dvr_http_client.get(f"{surl}/status", timeout=3)
        if status_resp.status_code == 200:
            s_version = status_resp.json().get("version", None)
            s_connected = True
        storage_resp = await _dvr_http_client.get(f"{surl}/dvr", timeout=3)
        if storage_resp.status_code == 200:
            s_percent, s_total, s_free, _ = _parse_dvr_storage(storage_resp.json())
    except Exception:
        pass
    try:
        s_shows, s_movies, s_episodes = await _fetch_dvr_library_counts(surl)
    except Exception:
        pass
    s_version_status = _cache_dvr_version_status(sid, s_version)
    return DVRStatus(
        id=sid,
        name=sname,
        host=host,
        port=port,
        connected=s_connected,
        version=s_version_status["version"],
        version_compatible=s_version_status["version_compatible"],
        version_warning=s_version_status["version_warning"],
        disk_usage_percent=s_percent,
        disk_total_gb=s_total,
        disk_free_gb=s_free,
        library_shows=s_shows,
        library_movies=s_movies,
        library_episodes=s_episodes,
    )


@app.get("/api/v1/dvrs/{dvr_id}/streams", tags=["DVR V1"])
async def get_dvr_streams_v1(dvr_id: str):
    import re as _re
    import time as _vtime

    server = await _get_dvr_server_by_id_async(dvr_id)
    if server is None:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    sid, sname, surl = server

    if CORE_APP_AVAILABLE:
        from core.helpers.config import CoreSettings

        CoreSettings._instance = None
        settings = await asyncio.to_thread(_get_core_settings_sync)
    else:
        settings = await _load_settings_async()
    stream_pref = (
        getattr(settings, "stream_card_image", "program") if settings else "program"
    )

    watching = []
    recording = []
    try:
        resp = await _dvr_http_client.get(f"{surl}/dvr", timeout=3)
        if resp.status_code != 200:
            return {
                "dvr_id": sid,
                "dvr_name": sname,
                "total": 0,
                "watching": [],
                "recording": [],
                "subtitle": "No active streams",
                "image": "",
            }

        activity = resp.json().get("activity", {})
        channel_images: Dict[str, str] = {}
        channel_logos: Dict[str, str] = {}
        try:
            ch_resp = await _dvr_http_client.get(f"{surl}/api/v1/channels", timeout=3)
            if ch_resp.status_code == 200:
                for ch in ch_resp.json():
                    num = str(ch.get("number", ""))
                    img = (
                        ch.get("image_url", "")
                        or ch.get("logo_url", "")
                        or ch.get("image", "")
                    )
                    if num and img:
                        channel_logos[num] = img
        except Exception:
            pass

        if stream_pref == "program":
            try:
                guide_resp = await _dvr_http_client.get(
                    f"{surl}/devices/ANY/guide/now", timeout=5
                )
                if guide_resp.status_code == 200:
                    now = int(_vtime.time())
                    for item in guide_resp.json():
                        for airing in item.get("Airings", []):
                            ch = airing.get("Channel", "")
                            start = airing.get("Time", 0)
                            dur = airing.get("Duration", 0)
                            img = airing.get("Image", "")
                            if ch and start <= now <= start + dur:
                                channel_images[ch] = (
                                    img if img else channel_logos.get(ch, "")
                                )
            except Exception:
                pass
            for ch_num, logo in channel_logos.items():
                if ch_num not in channel_images:
                    channel_images[ch_num] = logo
        elif stream_pref == "channel":
            channel_images = channel_logos

        for _sid, val in activity.items():
            if val.startswith("Watching"):
                device_match = _re.search(r"from\s+([^:(]+)", val)
                channel_match = _re.search(r"ch\d+\s+([^:]+?)(?:\s+from|\s*[:(])", val)
                ch_num_match = _re.search(r"ch(\d+)", val)
                device = device_match.group(1).strip() if device_match else "Unknown"
                channel = channel_match.group(1).strip() if channel_match else "Unknown"
                ch_num = ch_num_match.group(1) if ch_num_match else ""
                watching.append(
                    {
                        "device": device,
                        "channel": channel,
                        "image": channel_images.get(ch_num, ""),
                    }
                )
            elif val.startswith("Recording"):
                title_match = _re.search(r"for\s+(.+?)\s+until\s+", val)
                until_match = _re.search(r"until\s+(.+?)(?=:\s*buf|$)", val)
                title = title_match.group(1) if title_match else "Unknown"
                until = until_match.group(1).strip() if until_match else ""
                recording.append({"title": title, "until": until})
    except Exception:
        pass

    total = len(watching) + len(recording)
    image = watching[0].get("image", "") if watching else ""
    if total == 0:
        subtitle = "No active streams"
    elif total == 1:
        if watching:
            subtitle = f"{watching[0]['device']} watching {watching[0]['channel']}"
        else:
            subtitle = (
                f"Recording {recording[0]['title']} until {recording[0]['until']}"
            )
    elif len(watching) > 0 and len(recording) > 0:
        subtitle = f"{len(watching)} watching, {len(recording)} recording"
    elif len(watching) == 1:
        subtitle = f"{watching[0]['device']} watching {watching[0]['channel']}"
    elif len(watching) > 1:
        subtitle = f"{watching[0]['device']} watching {watching[0]['channel']} +{len(watching) - 1} more"
    elif len(recording) == 1:
        subtitle = f"Recording {recording[0]['title']} until {recording[0]['until']}"
    else:
        subtitle = f"{len(recording)} recordings active"

    return {
        "dvr_id": sid,
        "dvr_name": sname,
        "total": total,
        "watching": watching,
        "recording": recording,
        "subtitle": subtitle,
        "image": image,
    }


@app.get(
    "/api/v1/dvrs/{dvr_id}/system-info",
    response_model=PerDvrSystemInfo,
    tags=["DVR V1"],
)
async def get_dvr_system_info_v1(dvr_id: str):
    server = await _get_dvr_server_by_id_async(dvr_id)
    if server is None:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    sid, sname, surl = server
    host = surl.replace("http://", "").rsplit(":", 1)[0]
    port = int(surl.rsplit(":", 1)[1])

    if CORE_APP_AVAILABLE:
        from core.helpers.config import get_settings as _get_core_settings

        _cfg = await asyncio.to_thread(_get_core_settings)
    else:
        _cfg = await _load_settings_async()

    s_version = None
    s_percent, s_total, s_free = None, None, None
    s_shows, s_movies, s_episodes = 0, 0, 0
    s_connected = False

    try:
        status_resp = await _dvr_http_client.get(f"{surl}/status", timeout=3)
        if status_resp.status_code == 200:
            s_version = status_resp.json().get("version", None)
            s_connected = True
        storage_resp = await _dvr_http_client.get(f"{surl}/dvr", timeout=3)
        if storage_resp.status_code == 200:
            s_percent, s_total, s_free, _ = _parse_dvr_storage(storage_resp.json())
    except Exception:
        pass

    try:
        s_shows, s_movies, s_episodes = await _fetch_dvr_library_counts(surl)
    except Exception:
        pass

    s_severity = "normal"
    if s_free is not None and s_total is not None and s_total > 0:
        try:
            crit_pct = float(getattr(_cfg, "ds_critical_threshold_percent", 5.0))
            crit_gb = float(getattr(_cfg, "ds_critical_threshold_gb", 25.0))
            warn_pct = float(getattr(_cfg, "ds_warning_threshold_percent", 10.0))
            warn_gb = float(getattr(_cfg, "ds_warning_threshold_gb", 50.0))
            free_pct = (s_free / s_total) * 100
            if free_pct < crit_pct or s_free < crit_gb:
                s_severity = "critical"
            elif free_pct < warn_pct or s_free < warn_gb:
                s_severity = "warning"
        except Exception:
            s_severity = _simple_disk_status(s_free, s_total)

    s_version_status = _cache_dvr_version_status(sid, s_version)
    s_used_gb = (
        round(s_total - s_free, 2)
        if s_total is not None and s_free is not None
        else None
    )
    return PerDvrSystemInfo(
        dvr_id=sid,
        dvr_name=sname,
        host=host,
        port=port,
        connected=s_connected,
        version=s_version_status["version"],
        version_compatible=s_version_status["version_compatible"],
        version_warning=s_version_status["version_warning"],
        disk_usage_percent=s_percent,
        disk_usage_gb=s_used_gb,
        disk_total_gb=s_total,
        disk_free_gb=s_free,
        disk_severity=s_severity,
        library_shows=s_shows,
        library_movies=s_movies,
        library_episodes=s_episodes,
    )


@app.get(
    "/api/v1/dvrs/{dvr_id}/activity-history",
    response_model=ActivityHistoryResponse,
    tags=["DVR V1"],
)
async def get_dvr_activity_history_v1(
    dvr_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    activity_type: Optional[str] = Query(default=None, alias="type"),
    search: Optional[str] = Query(default=None),
    sort_order: str = Query(default="desc", alias="sort"),
):
    server = await _get_dvr_server_by_id_async(dvr_id)
    if server is None:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )

    normalized_sort = sort_order.lower()
    if normalized_sort not in {"asc", "desc"}:
        raise structured_error(ErrorCode.ACTIVITY_SORT_INVALID)

    try:
        engine = _get_activity_db_engine()
        if engine is not None:
            rows, total = await asyncio.to_thread(
                _query_activity_db,
                engine,
                offset=offset,
                limit=limit,
                activity_type=activity_type,
                search=search,
                sort_desc=(normalized_sort == "desc"),
                dvr_id=dvr_id,
            )
            return ActivityHistoryResponse(
                items=[_activity_event_to_item(r) for r in rows],
                total=total,
                offset=offset,
                limit=limit,
            )

        with _activity_lock:
            history_snapshot = list(ACTIVITY_HISTORY)
        filtered = [
            item
            for item in history_snapshot
            if item.dvr_id == dvr_id
            and _activity_matches_type(item, activity_type)
            and _activity_matches_search(item, search)
        ]
        filtered.sort(key=_history_sort_key, reverse=(normalized_sort == "desc"))
        paginated = filtered[offset : offset + limit]
        return ActivityHistoryResponse(
            items=paginated,
            total=len(filtered),
            offset=offset,
            limit=limit,
        )
    except HTTPException:
        raise
    except Exception:
        raise structured_error(ErrorCode.ACTIVITY_FETCH_FAILED)


@app.get(
    "/api/v1/dvrs/{dvr_id}/recordings/upcoming",
    response_model=List[RecordingInfo],
    tags=["DVR V1"],
)
async def get_dvr_upcoming_recordings_v1(dvr_id: str, limit: int = 250):
    server = await _get_dvr_server_by_id_async(dvr_id)
    if server is None:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    sid, sname, surl = server

    if CORE_APP_AVAILABLE:
        from core.helpers.config import get_settings as _get_core_settings

        settings = await asyncio.to_thread(_get_core_settings)
    else:
        settings = await _load_settings_async()

    from zoneinfo import ZoneInfo

    user_tz = ZoneInfo(settings.tz) if settings and settings.tz else ZoneInfo("UTC")
    current_time = int(datetime.now(_tz.utc).timestamp())
    rec_pref = (
        getattr(settings, "recording_card_image", "program") if settings else "program"
    )

    upcoming = []
    try:
        channel_map: Dict[str, str] = {}
        channel_logo_map: Dict[str, str] = {}
        try:
            channel_response = await _dvr_http_client.get(
                f"{surl}/api/v1/channels", timeout=5
            )
            if channel_response.status_code == 200:
                for channel in channel_response.json():
                    channel_number = (
                        str(channel["number"]) if "number" in channel else None
                    )
                    ch_name = channel.get("name", "Unknown Channel")
                    ch_logo = _channel_logo_candidate(channel)
                    if channel_number:
                        channel_map[channel_number] = ch_name
                        channel_logo_map[channel_number] = ch_logo
                    if "id" in channel:
                        channel_map[str(channel["id"])] = ch_name
                        channel_logo_map[str(channel["id"])] = ch_logo
        except Exception:
            pass

        response = await _dvr_http_client.get(f"{surl}/dvr/jobs", timeout=5)
        if response.status_code != 200:
            return []

        for recording in response.json():
            try:
                start_time = int(recording.get("Time", 0) or 0)
            except (TypeError, ValueError):
                continue
            if start_time <= current_time:
                continue
            title = recording.get("Name", "Untitled Recording")
            channel_number = None
            if "Channels" in recording and recording["Channels"]:
                channel_number = str(recording["Channels"][0])
            elif "Channel" in recording and recording["Channel"]:
                channel_number = str(recording["Channel"])
            elif "Airing" in recording and isinstance(recording["Airing"], dict):
                channel_value = recording["Airing"].get("Channel")
                if channel_value is not None:
                    channel_number = str(channel_value)
            channel_name = channel_map.get(
                channel_number,
                f"Channel {channel_number}" if channel_number else "Unknown Channel",
            )

            recording_datetime = datetime.fromtimestamp(start_time, tz=user_tz)
            today = datetime.now(user_tz).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            tomorrow = today + timedelta(days=1)
            if recording_datetime.date() == today.date():
                date_prefix = "Today"
            elif recording_datetime.date() == tomorrow.date():
                date_prefix = "Tomorrow"
            else:
                date_prefix = recording_datetime.strftime("%b %d")
            scheduled_time = (
                f"{date_prefix} at {recording_datetime.strftime('%I:%M %p')}"
            )

            rec_image, artwork_fallback_exhausted = _resolve_recording_artwork(
                recording,
                channel_logo_map.get(channel_number or "", ""),
                rec_pref,
            )

            upcoming.append(
                RecordingInfo(
                    id=recording.get("ID", ""),
                    title=title,
                    start_time=start_time,
                    end_time=_recording_stop_time(recording, start_time),
                    channel=channel_name,
                    scheduled_time=scheduled_time,
                    image=rec_image,
                    artwork_fallback_exhausted=artwork_fallback_exhausted,
                    dvr_id=sid,
                    dvr_name=sname,
                )
            )
    except Exception:
        pass

    upcoming.sort(key=lambda x: x.start_time)
    return upcoming[:limit]


@app.get(
    "/api/v1/dvrs/{dvr_id}/health", response_model=DvrHealthResponse, tags=["DVR V1"]
)
async def get_dvr_health_v1(dvr_id: str):
    server = await _get_dvr_server_by_id_async(dvr_id)
    if server is None:
        raise structured_error(
            ErrorCode.DVR_NOT_FOUND, message=f"DVR {dvr_id!r} not found"
        )
    sid, sname, surl = server
    host = surl.replace("http://", "").rsplit(":", 1)[0]
    port = int(surl.rsplit(":", 1)[1])

    s_version = None
    s_free, s_total = None, None
    s_connected = False

    try:
        status_resp = await _dvr_http_client.get(f"{surl}/status", timeout=3)
        if status_resp.status_code == 200:
            s_version = status_resp.json().get("version", None)
            s_connected = True
        storage_resp = await _dvr_http_client.get(f"{surl}/dvr", timeout=3)
        if storage_resp.status_code == 200:
            _, s_total, s_free, _ = _parse_dvr_storage(storage_resp.json())
    except Exception:
        pass

    s_version_status = _cache_dvr_version_status(sid, s_version)
    monitor_entry = next(
        (
            entry
            for entry in (await asyncio.to_thread(_get_monitoring_health_summary))[
                "dvrs"
            ]
            if entry["id"] == sid
        ),
        {},
    )

    s_last_event_at = None
    s_session_state_size = None
    try:
        s_last_event_at, s_session_state_size = await asyncio.to_thread(
            _read_dvr_session_state_summary, sid
        )
    except Exception:
        pass

    s_last_event_at = monitor_entry.get("last_event_at") or s_last_event_at

    s_recent_alert_rate = None
    try:
        s_recent_alert_rate = await asyncio.to_thread(_get_recent_alert_rate, sid)
    except Exception:
        pass

    return DvrHealthResponse(
        dvr_id=sid,
        dvr_name=sname,
        host=host,
        port=port,
        connected=s_connected,
        version=s_version_status["version"],
        version_compatible=s_version_status["version_compatible"],
        version_warning=s_version_status["version_warning"],
        disk_status=_simple_disk_status(s_free, s_total),
        disk_free_gb=s_free,
        disk_total_gb=s_total,
        last_checked=datetime.now(_tz.utc).isoformat(),
        last_event_at=s_last_event_at,
        last_freshness_at=monitor_entry.get("last_freshness_at"),
        last_freshness_source=monitor_entry.get("last_freshness_source"),
        freshness_age_seconds=monitor_entry.get("freshness_age_seconds"),
        freshness_status=monitor_entry.get("freshness_status", "missing"),
        monitoring_status=monitor_entry.get("monitoring_status", "missing"),
        monitoring_ready=bool(monitor_entry.get("ready", False)),
        monitoring_reason=monitor_entry.get("reason"),
        session_state_size=s_session_state_size,
        recent_alert_rate=s_recent_alert_rate,
    )


@app.post(
    "/api/v1/discovery/scan",
    tags=["Discovery V1"],
    dependencies=[require_role("operator")],
)
async def discovery_scan_v1():
    settings = await _load_settings_async()
    existing_hosts: set[tuple[str, int]] = {
        (s.get("host", ""), int(s.get("port", 8089)))
        for s in (getattr(settings, "dvr_servers", None) or [])
        if isinstance(s, dict) and not s.get("deleted_at")
    }
    servers = await asyncio.to_thread(_scan_for_dvrs, 5.0)
    return _build_scan_response(servers, existing_hosts=existing_hosts)


class _LoginRequest(BaseModel):
    username: str
    password: str


class _WhoAmIResponse(BaseModel):
    authenticated: bool
    rbac_enabled: bool
    username: Optional[str] = None
    role: Optional[str] = None


class _ChangeCredentialsRequest(BaseModel):
    current_password: str
    username: str = ""
    new_password: str = ""


def _auth_login_sync(username: str, password: str):
    if _effective_auth_mode(load_settings()) != "rbac":
        raise structured_error(ErrorCode.AUTH_RBAC_NOT_ENABLED)
    engine = _ensure_auth_tables()
    if engine is None:
        raise structured_error(ErrorCode.AUTH_DB_UNAVAILABLE)
    from core.storage.auth import (
        get_user_by_username as _guu,
        create_session as _cs,
    )

    user = _guu(engine, username)
    if user is None or not user.verify_password(password):
        raise structured_error(ErrorCode.AUTH_CREDENTIALS_INVALID)
    session = _cs(engine, user.id)
    return user.username, user.role, session.token, session.csrf_token


def _auth_logout_sync(token: str) -> None:
    if token and RBAC_ENABLED:
        engine = _ensure_auth_tables()
        if engine is not None:
            from core.storage.auth import invalidate_session as _inv

            _inv(engine, token)


def _auth_whoami_sync(token: str) -> _WhoAmIResponse:
    if _effective_auth_mode(load_settings()) != "rbac":
        return _WhoAmIResponse(authenticated=False, rbac_enabled=False)
    if not token:
        raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
    engine = _ensure_auth_tables()
    if engine is None:
        raise structured_error(ErrorCode.AUTH_DB_UNAVAILABLE)
    from core.storage.auth import (
        get_session_by_token as _gst2,
        get_user_by_id as _gui,
    )

    user_session = _gst2(engine, token)
    if user_session is None:
        raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
    user = _gui(engine, user_session.user_id)
    if user is None:
        raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
    return _WhoAmIResponse(
        authenticated=True,
        rbac_enabled=True,
        username=user.username,
        role=user.role,
    )


def _auth_change_credentials_sync(body: _ChangeCredentialsRequest, user_id: int):
    if _effective_auth_mode(load_settings()) != "rbac":
        raise structured_error(ErrorCode.AUTH_RBAC_NOT_ENABLED)
    engine = _ensure_auth_tables()
    if engine is None:
        raise structured_error(ErrorCode.AUTH_DB_UNAVAILABLE)

    from core.storage.auth import (
        get_user_by_id as _guid,
        verify_password as _verify_password,
        update_user_credentials as _update_creds,
    )

    user = _guid(engine, user_id)
    if user is None:
        raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
    if not body.current_password or not _verify_password(
        body.current_password, user.password_hash
    ):
        raise structured_error(ErrorCode.AUTH_CREDENTIALS_INVALID)

    new_username = body.username.strip()
    new_password = body.new_password.strip()
    if not new_username and not new_password:
        raise structured_error(
            ErrorCode.AUTH_CREDENTIALS_REQUIRED,
            message="No credential changes requested",
            remediation="Provide a new username or a new password.",
        )

    updated = _update_creds(
        engine,
        user_id,
        username=new_username or None,
        password=new_password or None,
    )
    return updated.username if updated else user.username


def _auth_setup_status_sync() -> SetupStatusResponse:
    settings = load_settings()
    auth_state = _resolve_auth_state(settings)
    return SetupStatusResponse(
        persisted_mode=auth_state.persisted_mode,
        configured_mode=auth_state.configured_mode,
        effective_mode=auth_state.effective_mode,
        setup_required=auth_state.setup_required,
        runtime_auth_override_active=auth_state.runtime_auth_override_active,
        api_key_fallback_active=auth_state.api_key_fallback_active,
        rbac_enabled=auth_state.rbac_enabled,
        session_auth_available=auth_state.session_auth_available,
        session_setup_required=auth_state.session_setup_required,
        needs_setup=auth_state.setup_required,
        current_mode=auth_state.configured_mode,
        available_modes=["rbac", "none"],
    )


_auth_router = _APIRouter(prefix="/api/v1/auth", tags=["Auth V1"])


@app.get(
    "/api/v1/security/status",
    response_model=SecurityStatusResponse,
    tags=["Security V1"],
)
async def security_status():
    return await asyncio.to_thread(_build_security_status)


@_auth_router.post("/login")
async def auth_login(body: _LoginRequest, response: Response, request: Request):
    username, role, token, csrf_token = await asyncio.to_thread(
        _auth_login_sync, body.username, body.password
    )
    response.set_cookie(
        key="channelwatch_session",
        value=token,
        httponly=True,
        secure=_should_use_secure_cookies(request),
        samesite="strict",
        max_age=86400,
    )
    return {
        "username": username,
        "role": role,
        "csrf_token": csrf_token,
    }


@_auth_router.post("/logout")
async def auth_logout(request: Request, response: Response):
    token = request.cookies.get("channelwatch_session", "")
    await asyncio.to_thread(_auth_logout_sync, token)
    response.delete_cookie("channelwatch_session")
    return {"message": "Logged out"}


@_auth_router.get("/whoami", response_model=_WhoAmIResponse)
async def auth_whoami(request: Request):
    token = request.cookies.get("channelwatch_session", "")
    return await asyncio.to_thread(_auth_whoami_sync, token)


@_auth_router.post("/change-credentials")
async def auth_change_credentials(body: _ChangeCredentialsRequest, request: Request):
    if not await _request_has_valid_session_async(request):
        raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
    csrf_header = request.headers.get("X-CSRF-Token", "")
    csrf_expected = str(getattr(request.state, "auth_session_csrf", "") or "")
    if (
        not csrf_header
        or not csrf_expected
        or not secrets.compare_digest(csrf_header, csrf_expected)
    ):
        raise structured_error(ErrorCode.AUTH_CSRF_INVALID)
    user_id = getattr(request.state, "auth_user_id", None)
    if user_id is None:
        raise structured_error(ErrorCode.AUTH_UNAUTHENTICATED)
    try:
        username = await asyncio.to_thread(_auth_change_credentials_sync, body, user_id)
    except ValueError as exc:
        raise structured_error(ErrorCode.AUTH_CREDENTIALS_CONFLICT, message=str(exc))

    return {
        "message": "Credentials updated",
        "username": username,
    }


class _SetupRequest(BaseModel):
    mode: AuthMode = "rbac"
    username: str = ""
    password: str = ""


class _AuthSetupResult(BaseModel):
    mode: AuthMode
    settings: AppSettings
    username: str = ""
    token: str = ""
    csrf_token: str = ""


def _auth_setup_sync(body: _SetupRequest) -> _AuthSetupResult:
    settings = load_settings()
    current_mode = _effective_auth_mode(settings)
    if current_mode == "api_key":
        raise structured_error(ErrorCode.AUTH_RBAC_NOT_ENABLED)

    engine = _ensure_auth_tables()
    user_count = 0
    if engine is not None:
        from core.storage.auth import get_user_count as _guc_setup

        user_count = _guc_setup(engine)

    allow_no_auth_bootstrap = current_mode == "none" and user_count == 0
    if not _setup_required(settings) and not allow_no_auth_bootstrap:
        raise structured_error(
            ErrorCode.AUTH_ADMIN_EXISTS, message="Setup already completed"
        )

    if body.mode == "rbac":
        if not body.username or not body.password:
            raise structured_error(ErrorCode.AUTH_CREDENTIALS_REQUIRED)
        if engine is None:
            raise structured_error(ErrorCode.AUTH_DB_UNAVAILABLE)
        from core.storage.auth import (
            get_user_count as _guc3,
            create_user as _cu2,
            get_user_by_username as _guu3,
            create_session as _cs3,
        )

        if _guc3(engine) > 0:
            raise structured_error(ErrorCode.AUTH_ADMIN_EXISTS)
        _cu2(engine, body.username, body.password, role="admin")
        user = _guu3(engine, body.username)
        session = _cs3(engine, user.id)
        settings.rbac_enabled = True
        settings.auth_mode = "rbac"
        settings.security_setup_completed = True
        settings.api_key = ""
        save_settings(settings)
        return _AuthSetupResult(
            mode="rbac",
            settings=settings,
            username=body.username,
            token=session.token,
            csrf_token=session.csrf_token,
        )

    if body.mode == "none":
        settings.rbac_enabled = False
        settings.auth_mode = "none"
        settings.security_setup_completed = True
        settings.api_key = ""
        save_settings(settings)
        return _AuthSetupResult(mode="none", settings=settings)

    raise structured_error(ErrorCode.AUTH_MODE_UNSUPPORTED)


@_auth_router.get("/setup-status", response_model=SetupStatusResponse)
async def auth_setup_status():
    return await asyncio.to_thread(_auth_setup_status_sync)


@_auth_router.post("/setup", status_code=201)
async def auth_setup(body: _SetupRequest, response: Response, request: Request):
    setup_result = await asyncio.to_thread(_auth_setup_sync, body)
    settings = setup_result.settings
    _refresh_runtime_auth_state(settings)
    if setup_result.mode == "rbac":
        response.set_cookie(
            key="channelwatch_session",
            value=setup_result.token,
            httponly=True,
            secure=_should_use_secure_cookies(request),
            samesite="strict",
            max_age=86400,
        )
        return {
            "message": "Admin user created",
            "username": setup_result.username,
            "csrf_token": setup_result.csrf_token,
        }

    if setup_result.mode == "none":
        return {"message": "Authentication disabled by setup choice"}

    raise structured_error(ErrorCode.AUTH_MODE_UNSUPPORTED)


app.include_router(_auth_router)


# STATIC FILE SERVING
STATIC_IMAGES_DIR = WEBUI_DIR / "static" / "images"
if STATIC_IMAGES_DIR.is_dir():
    app.mount("/images", StaticFiles(directory=STATIC_IMAGES_DIR), name="static-images")
else:
    pass

if STATIC_UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_UI_DIR, html=True), name="static-ui")
else:
    print(
        f"[WebUI] WARNING: Static UI directory not found at {STATIC_UI_DIR}. Frontend will not load."
    )

    @app.get("/")
    async def fallback_root():
        return {"message": "Frontend UI not found."}
