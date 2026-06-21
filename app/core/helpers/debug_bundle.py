"""
Sanitized debug-bundle generator shared by the CLI and the backend API.

Bundle contents (under a timestamped prefix):
  manifest.json, settings_sanitized.json, logs/app.log, health_snapshot.json

Excluded: encryption.key, channelwatch.db, raw session-state files.
"""

from __future__ import annotations

import io
import json
import platform
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MASKED = "****"

_SENSITIVE_TOP_FIELDS: frozenset[str] = frozenset(
    {
        "api_key",
        "ics_feed_token",
        "rss_feed_token",
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
    }
)

_SENSITIVE_DVR_FIELDS: frozenset[str] = frozenset({"host", "port", "api_key"})

_SENSITIVE_WEBHOOK_FIELDS: frozenset[str] = frozenset({"url", "secret"})

_RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_URL = re.compile(r"https?://[^\s\"'<>]+")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sanitize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _SENSITIVE_TOP_FIELDS:
            result[key] = _MASKED if value else ""
        elif key == "dvr_servers" and isinstance(value, list):
            sanitized = []
            for server in value:
                if isinstance(server, dict):
                    s = dict(server)
                    for field in _SENSITIVE_DVR_FIELDS:
                        if field in s:
                            s[field] = _MASKED
                    sanitized.append(s)
                else:
                    sanitized.append(server)
            result[key] = sanitized
        elif key == "webhooks" and isinstance(value, list):
            sanitized = []
            for webhook in value:
                if isinstance(webhook, dict):
                    w = dict(webhook)
                    for field in _SENSITIVE_WEBHOOK_FIELDS:
                        if field in w:
                            w[field] = _MASKED
                    sanitized.append(w)
                else:
                    sanitized.append(webhook)
            result[key] = sanitized
        else:
            result[key] = value
    return result


def _redact_log_line(line: str) -> str:
    line = _RE_IPV4.sub("[REDACTED_IP]", line)
    line = _RE_URL.sub("[REDACTED_URL]", line)
    return line


def _read_log_tail(config_dir: Path, n_lines: int = 500) -> list[str]:
    log_file = config_dir / "channelwatch.log"
    if n_lines <= 0 or not log_file.exists():
        return []
    try:
        chunks: list[bytes] = []
        newline_count = 0
        with log_file.open("rb") as handle:
            handle.seek(0, io.SEEK_END)
            position = handle.tell()
            while position > 0 and newline_count <= n_lines:
                read_size = min(8192, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")

        content = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
        lines = content.splitlines()
        tail = lines[-n_lines:] if len(lines) > n_lines else lines
        return [_redact_log_line(ln) for ln in tail]
    except OSError:
        return []


def _read_settings_raw(config_dir: Path) -> dict[str, Any]:
    settings_file = config_dir / "settings.json"
    if not settings_file.exists():
        return {}
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _collect_health_snapshot(raw_settings: dict[str, Any]) -> dict[str, Any]:
    """No network calls — safe to run offline from the CLI."""
    servers = raw_settings.get("dvr_servers") or []
    enabled = [
        s
        for s in servers
        if isinstance(s, dict)
        and not s.get("deleted_at")
        and s.get("enabled", True) is not False
    ]
    return {"dvr_count": len(enabled)}


def create_debug_bundle(config_dir: Path) -> bytes:
    """
    Return the bytes of a sanitized debug bundle zip.

    Safety guarantees:
    - Sensitive fields (API keys, credentials, host/port, webhook URLs,
      error_reporting_dsn) are replaced with '****'.
    - encryption.key is excluded entirely.
    - channelwatch.db is excluded entirely.
    - Log lines have IPv4 addresses and full URLs replaced.
    """
    from core import __version__

    ts = _utc_timestamp()
    prefix = f"channelwatch_debug_{ts}"

    raw_settings = _read_settings_raw(config_dir)
    sanitized = _sanitize_settings(raw_settings)
    log_lines = _read_log_tail(config_dir, n_lines=500)
    health = _collect_health_snapshot(raw_settings)

    manifest = {
        "bundle_type": "debug",
        "bundle_schema_version": 1,
        "created_at": ts,
        "created_by": "channelwatch",
        "app_version": __version__,
        "arch": platform.machine(),
        "dvr_count": health["dvr_count"],
        "privacy_note": (
            "This bundle contains ONLY sanitized data. "
            "Sensitive fields (API keys, notification credentials, DVR host/port, "
            "webhook URLs, error_reporting_dsn) are masked with '****'. "
            "The encryption key and raw database are excluded entirely. "
            "Safe to share with ChannelWatch maintainers for troubleshooting."
        ),
        "artifacts": [
            "manifest.json",
            "settings_sanitized.json",
            "logs/app.log",
            "health_snapshot.json",
        ],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{prefix}/manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(
            f"{prefix}/settings_sanitized.json", json.dumps(sanitized, indent=2)
        )
        zf.writestr(f"{prefix}/logs/app.log", "\n".join(log_lines))
        zf.writestr(f"{prefix}/health_snapshot.json", json.dumps(health, indent=2))

    return buf.getvalue()
