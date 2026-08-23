"""DVR-specific destination validation and DNS pinning.

Channels DVR is intentionally a LAN service, so this policy is separate from
the general outbound URL SSRF policy used for untrusted notification and image
URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
import socket
from typing import Iterable

from .dvr_connection import format_dvr_http_host


_PRIVATE_V4 = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE_V6 = ipaddress.ip_network("fc00::/7")
_BLOCKED_HOSTNAMES = {
    "instance-data.ec2.internal",
    "localhost",
    "localhost.localdomain",
    "metadata.aws.internal",
    "metadata.azure.internal",
    "metadata.google.internal",
    "metadata.google",
    "metadata.oraclecloud.com",
    "instance-data",
}
_DNS_LABEL = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE | re.ASCII,
)


@dataclass(frozen=True)
class ValidatedDvrTarget:
    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class SafeDvrRequest:
    url: str
    host_header: str
    connect_address: str


def _normalize_host(host: object) -> str:
    value = "" if host is None else str(host).strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value.rstrip(".")


def _allowed_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_loopback: bool = False,
) -> bool:
    if allow_loopback and address.is_loopback:
        return True
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return False
    if address.version == 4:
        return bool(
            address.is_global
            or address in _TAILSCALE_V4
            or any(address in network for network in _PRIVATE_V4)
        )
    return bool(address.is_global or address in _PRIVATE_V6)


def _valid_hostname(host: str) -> bool:
    if not host or len(host) > 253 or host.lower() in _BLOCKED_HOSTNAMES:
        return False
    if any(value in host for value in ("://", "/", "?", "#", "@", "[", "]")):
        return False
    return all(_DNS_LABEL.fullmatch(label) for label in host.split("."))


def _resolve(host: str) -> Iterable[str]:
    return {
        str(result[4][0]).split("%", 1)[0]
        for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    }


def validate_dvr_target(
    host: object,
    port: object = 8089,
    *,
    allow_loopback: bool = False,
) -> ValidatedDvrTarget | None:
    normalized = _normalize_host(host)
    try:
        normalized_port = int(port)
    except (TypeError, ValueError):
        return None
    if not normalized or not 1 <= normalized_port <= 65535:
        return None

    try:
        literal = ipaddress.ip_address(normalized.split("%", 1)[0])
    except ValueError:
        if not _valid_hostname(normalized):
            return None
        try:
            raw_addresses = _resolve(normalized)
        except (socket.gaierror, OSError, UnicodeError):
            return None
        if not raw_addresses:
            return None
        parsed = []
        try:
            for raw in raw_addresses:
                address = ipaddress.ip_address(raw)
                if not _allowed_address(address, allow_loopback=allow_loopback):
                    return None
                parsed.append(address.compressed)
        except ValueError:
            return None
        addresses = tuple(sorted(set(parsed)))
    else:
        if not _allowed_address(literal, allow_loopback=allow_loopback):
            return None
        addresses = (literal.compressed,)

    return ValidatedDvrTarget(
        host=normalized,
        port=normalized_port,
        addresses=addresses,
    )


def build_safe_dvr_request(
    host: object,
    port: object = 8089,
    path: str = "",
    *,
    allow_loopback: bool = False,
) -> SafeDvrRequest | None:
    target = validate_dvr_target(host, port, allow_loopback=allow_loopback)
    if target is None:
        return None
    normalized_path = path if path.startswith("/") or not path else f"/{path}"
    address = target.addresses[0]
    connect_host = format_dvr_http_host(address)
    original_host = format_dvr_http_host(target.host)
    return SafeDvrRequest(
        url=f"http://{connect_host}:{target.port}{normalized_path}",
        host_header=f"{original_host}:{target.port}",
        connect_address=address,
    )
