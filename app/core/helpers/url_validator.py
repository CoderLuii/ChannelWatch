"""URL validation to prevent SSRF attacks on image fetching."""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse
from typing import Optional, cast

from .logging import log, LOG_STANDARD

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
}


@dataclass(frozen=True)
class SafeUrlRequest:
    """A URL rewritten to a validated address plus headers/extensions to preserve origin."""

    url: str
    host_header: str
    sni_hostname: Optional[str]


def _is_blocked_ip(host: str) -> bool:
    """Return True when an IP address is not safe for outbound fetches."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False

    return (
        not addr.is_global
        or addr.is_private
        or addr.is_reserved
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolve_hostname(hostname: str) -> set[str]:
    """Resolve a hostname to candidate IP addresses for SSRF validation."""
    results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return {cast(str, result[4][0]) for result in results}


def _format_host_for_netloc(host: str) -> str:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{addr.compressed}]" if addr.version == 6 else addr.compressed


def _host_header(parsed) -> str:
    hostname = parsed.hostname or ""
    host = _format_host_for_netloc(hostname)
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port and port != default_port:
        return f"{host}:{port}"
    return host


def build_safe_url_request(url: str) -> Optional[SafeUrlRequest]:
    """Resolve once and rewrite a URL so the HTTP client connects to a safe IP.

    This binds SSRF validation to the outbound connection. The returned URL uses
    the validated address, while ``Host`` and HTTPS SNI preserve the original
    hostname for virtual hosts and certificate validation.
    """
    if not is_safe_url(url):
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            resolved_addresses = _resolve_hostname(hostname)
        except (socket.gaierror, OSError):
            return None
        if not resolved_addresses or any(
            _is_blocked_ip(addr) for addr in resolved_addresses
        ):
            return None
        connect_host = sorted(resolved_addresses)[0]
        sni_hostname = hostname if parsed.scheme.lower() == "https" else None
    else:
        connect_host = hostname
        sni_hostname = None

    try:
        port = parsed.port
    except ValueError:
        return None
    port_text = f":{port}" if port else ""
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    rewritten_netloc = f"{userinfo}{_format_host_for_netloc(connect_host)}{port_text}"
    rewritten_url = urlunparse(
        (
            parsed.scheme,
            rewritten_netloc,
            parsed.path or "",
            parsed.params or "",
            parsed.query or "",
            parsed.fragment or "",
        )
    )
    return SafeUrlRequest(
        url=rewritten_url,
        host_header=_host_header(parsed),
        sni_hostname=sni_hostname,
    )


def is_safe_url(url: Optional[str]) -> bool:
    """Validate that a URL is safe to fetch (not pointing to internal resources).

    Blocks:
    - Private IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x, 0.0.0.0)
    - Non-HTTP/HTTPS schemes (file://, ftp://, gopher://, etc.)
    - Localhost and common internal hostnames
    - URLs without a hostname

    Returns True if the URL is safe to fetch, False otherwise.
    """
    if not url or not url.strip():
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Block non-HTTP schemes
    if parsed.scheme.lower() not in ("http", "https"):
        log(f"URL blocked: non-HTTP scheme '{parsed.scheme}'", level=LOG_STANDARD)
        return False

    hostname = parsed.hostname
    if not hostname:
        log("URL blocked: no hostname", level=LOG_STANDARD)
        return False

    hostname_lower = hostname.lower()

    # Block known internal hostnames
    if hostname_lower in BLOCKED_HOSTNAMES:
        log(f"URL blocked: internal hostname '{hostname_lower}'", level=LOG_STANDARD)
        return False

    # Block private/reserved IP address literals before DNS resolution.
    if _is_blocked_ip(hostname):
        log(f"URL blocked: private/reserved IP '{hostname}'", level=LOG_STANDARD)
        return False

    try:
        resolved_addresses = _resolve_hostname(hostname)
    except socket.gaierror:
        log(
            f"URL blocked: hostname resolution failed for '{hostname}'",
            level=LOG_STANDARD,
        )
        return False
    except OSError:
        log(
            f"URL blocked: hostname resolution failed for '{hostname}'",
            level=LOG_STANDARD,
        )
        return False

    if not resolved_addresses:
        log(
            f"URL blocked: hostname resolved no addresses for '{hostname}'",
            level=LOG_STANDARD,
        )
        return False

    for address in resolved_addresses:
        if _is_blocked_ip(address):
            log(
                f"URL blocked: hostname '{hostname}' resolves to private/reserved IP '{address}'",
                level=LOG_STANDARD,
            )
            return False

    return True


def redact_url(url: str) -> str:
    """Redact credentials and path from a URL, keeping only the scheme.

    Example: 'discord://webhook_id/token' -> 'discord://****'
    """
    if "://" in url:
        scheme = url.split("://")[0]
        return f"{scheme}://****"
    return "****"
