"""Trusted local notification destination handling."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Literal, Optional
from urllib.parse import urlparse, urlunparse

from .url_validator import (
    BLOCKED_HOSTNAMES,
    SafeUrlRequest,
    _host_header,
    _is_blocked_ip,
    _resolve_hostname,
    is_safe_url,
)

NotificationDestinationSource = Literal["apprise_custom", "webhook"]

HTTP_STYLE_APPRISE_SCHEMES = {
    "json": "http",
    "form": "http",
    "xml": "http",
    "jsons": "https",
    "forms": "https",
    "xmls": "https",
}

TRUSTABLE_BLOCK_REASONS = {
    "private_ip",
    "private_dns",
}

HARD_BLOCK_REASONS = {
    "blocked_hostname",
    "loopback",
    "link_local",
    "metadata",
    "multicast",
    "reserved",
    "unspecified",
}


@dataclass(frozen=True)
class NormalizedNotificationDestination:
    source: NotificationDestinationSource
    original_url: str
    validation_url: str
    scheme: str
    host: str
    port: int


@dataclass(frozen=True)
class DestinationSafetyPreview:
    source: str
    url: str
    normalized: dict[str, Any] | None
    status: str
    message: str
    trustable: bool
    trusted: bool


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _entry_value(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def _entry_matches(
    entry: Any,
    normalized: NormalizedNotificationDestination,
) -> bool:
    try:
        entry_port = int(_entry_value(entry, "port"))
    except (TypeError, ValueError):
        return False

    return (
        str(_entry_value(entry, "source") or "").strip() == normalized.source
        and str(_entry_value(entry, "scheme") or "").strip().lower()
        == normalized.scheme
        and _normalize_host(str(_entry_value(entry, "host") or ""))
        == normalized.host
        and entry_port == normalized.port
    )


def trusted_destination_dict(
    normalized: NormalizedNotificationDestination,
) -> dict[str, Any]:
    return {
        "source": normalized.source,
        "scheme": normalized.scheme,
        "host": normalized.host,
        "port": normalized.port,
    }


def normalize_notification_destination(
    source: NotificationDestinationSource | str,
    url: str,
) -> Optional[NormalizedNotificationDestination]:
    raw_url = str(url or "").strip()
    if not raw_url:
        return None

    if source not in ("apprise_custom", "webhook"):
        return None

    try:
        parsed = urlparse(raw_url)
    except Exception:
        return None

    parsed_scheme = parsed.scheme.lower()
    if source == "apprise_custom":
        normalized_source: NotificationDestinationSource = "apprise_custom"
        scheme = HTTP_STYLE_APPRISE_SCHEMES.get(parsed_scheme)
        if not scheme:
            return None
    elif parsed_scheme in ("http", "https"):
        normalized_source = "webhook"
        scheme = parsed_scheme
    else:
        return None

    if not parsed.hostname:
        return None

    try:
        port = parsed.port or _default_port(scheme)
    except ValueError:
        return None

    host = _normalize_host(parsed.hostname)
    validation_url = urlunparse(
        (
            scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.params or "",
            parsed.query or "",
            parsed.fragment or "",
        )
    )
    return NormalizedNotificationDestination(
        source=normalized_source,
        original_url=raw_url,
        validation_url=validation_url,
        scheme=scheme,
        host=host,
        port=port,
    )


def _classify_ip_address(host: str) -> str:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return "hostname"

    if addr.compressed == "169.254.169.254":
        return "metadata"
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link_local"
    if addr.is_multicast:
        return "multicast"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_reserved:
        return "reserved"
    if addr.is_private:
        return "private_ip"
    if not addr.is_global:
        return "reserved"
    return "public"


def _classify_destination(normalized: NormalizedNotificationDestination) -> str:
    if normalized.host in BLOCKED_HOSTNAMES:
        return "metadata" if "metadata" in normalized.host else "blocked_hostname"

    ip_reason = _classify_ip_address(normalized.host)
    if ip_reason != "hostname":
        return ip_reason

    try:
        addresses = _resolve_hostname(normalized.host)
    except (socket.gaierror, OSError):
        return "resolution_failed"

    if not addresses:
        return "resolution_failed"

    reasons = {_classify_ip_address(address) for address in addresses}
    hard_reasons = reasons & HARD_BLOCK_REASONS
    if hard_reasons:
        if "metadata" in hard_reasons:
            return "metadata"
        return sorted(hard_reasons)[0]
    if "private_ip" in reasons:
        return "private_dns"
    if any(_is_blocked_ip(address) for address in addresses):
        return "reserved"
    return "public"


def is_trusted_notification_destination(
    url: str,
    source: NotificationDestinationSource | str,
    trusted_destinations: Any,
) -> bool:
    normalized = normalize_notification_destination(source, url)
    if normalized is None:
        return False

    if _classify_destination(normalized) not in TRUSTABLE_BLOCK_REASONS:
        return False

    if not isinstance(trusted_destinations, list):
        return False

    return any(_entry_matches(entry, normalized) for entry in trusted_destinations)


def preview_notification_destination_safety(
    url: str,
    source: NotificationDestinationSource | str,
    trusted_destinations: Any,
) -> DestinationSafetyPreview:
    normalized = normalize_notification_destination(source, url)
    if normalized is None:
        return DestinationSafetyPreview(
            source=str(source),
            url=str(url or ""),
            normalized=None,
            status="unsupported",
            message="This destination type does not use the trusted-local flow.",
            trustable=False,
            trusted=False,
        )

    normalized_dict = trusted_destination_dict(normalized)
    trusted = is_trusted_notification_destination(url, source, trusted_destinations)

    if is_safe_url(normalized.validation_url):
        return DestinationSafetyPreview(
            source=normalized.source,
            url=normalized.original_url,
            normalized=normalized_dict,
            status="public_safe",
            message="This destination passes the standard public URL safety check.",
            trustable=False,
            trusted=False,
        )

    reason = _classify_destination(normalized)
    trustable = reason in TRUSTABLE_BLOCK_REASONS
    if trusted:
        status = "trusted_local"
        message = "This exact local destination is trusted for notification delivery."
    elif trustable:
        status = "local_untrusted"
        message = (
            "This appears to be a private LAN notification destination. "
            "Trust this exact scheme, host, and port before delivery."
        )
    else:
        status = f"blocked_{reason}"
        message = "This destination is blocked by the safety policy and cannot be trusted."

    return DestinationSafetyPreview(
        source=normalized.source,
        url=normalized.original_url,
        normalized=normalized_dict,
        status=status,
        message=message,
        trustable=trustable,
        trusted=trusted,
    )


def build_trusted_notification_request(
    url: str,
    source: NotificationDestinationSource | str,
    trusted_destinations: Any,
) -> Optional[SafeUrlRequest]:
    normalized = normalize_notification_destination(source, url)
    if normalized is None:
        return None
    if is_safe_url(normalized.validation_url):
        return None
    if not is_trusted_notification_destination(url, source, trusted_destinations):
        return None

    parsed = urlparse(normalized.validation_url)
    return SafeUrlRequest(
        url=normalized.validation_url,
        host_header=_host_header(parsed),
        sni_hostname=None,
    )
