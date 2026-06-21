from __future__ import annotations

import os
import time
from typing import Any

try:
    from zeroconf import ServiceBrowser, Zeroconf  # type: ignore[import-untyped]

    _ZEROCONF_AVAILABLE = True
except ImportError:
    ServiceBrowser = None  # type: ignore[assignment,misc]
    Zeroconf = None  # type: ignore[assignment,misc]
    _ZEROCONF_AVAILABLE = False

MDNS_SERVICE_TYPE = "_channels_dvr._tcp.local."
DEFAULT_SCAN_TIMEOUT = 5.0

_MSG_HOST_NET_REQUIRED = (
    "Auto-discovery requires host network mode. "
    "Start the container with --network host or add your DVR manually below."
)
_MSG_NO_DVRS_FOUND = (
    "No DVR servers found on the local network. Add your DVR manually below."
)
_MSG_ALL_CONFIGURED = "All discovered DVR servers are already configured."


def _running_in_container() -> bool:
    return os.path.isfile("/.dockerenv")


def scan_for_dvrs(
    timeout: float = DEFAULT_SCAN_TIMEOUT,
    service_type: str = MDNS_SERVICE_TYPE,
) -> list[dict[str, Any]]:
    """Blocking. MUST be called via asyncio.to_thread — never directly in the event loop."""
    if not _ZEROCONF_AVAILABLE:
        return []

    found: list[dict[str, Any]] = []

    class _Listener:
        def add_service(self, zc: Any, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if info is None:
                return
            addresses = (
                info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
            )
            host: str = addresses[0] if addresses else (info.server or "")
            port: int = int(info.port or 8089)

            props: dict[str, str] = {}
            if info.properties:
                props = {
                    (k.decode() if isinstance(k, bytes) else k): (
                        v.decode() if isinstance(v, bytes) else v
                    )
                    for k, v in info.properties.items()
                }

            bare = name
            suffix = f".{service_type}"
            if bare.endswith(suffix):
                bare = bare[: -len(suffix)]
            elif bare.endswith(service_type):
                bare = bare[: -len(service_type)]

            display = props.get("name") or props.get("friendlyName") or bare
            found.append(
                {
                    "host": host,
                    "port": port,
                    "display_name_suggestion": display,
                }
            )

        def remove_service(self, zc: Any, type_: str, name: str) -> None:
            pass

        def update_service(self, zc: Any, type_: str, name: str) -> None:
            pass

    zc = Zeroconf()
    try:
        listener = _Listener()
        _browser = ServiceBrowser(zc, service_type, listener)
        time.sleep(timeout)
    finally:
        zc.close()

    return found


def build_scan_response(
    servers: list[dict[str, Any]],
    existing_hosts: set[tuple[str, int]] | None = None,
) -> dict[str, Any]:
    excluded: set[tuple[str, int]] = existing_hosts or set()
    new_servers = [s for s in servers if (s["host"], int(s["port"])) not in excluded]

    if new_servers:
        return {"servers": new_servers, "manual_add_available": True, "message": None}

    if servers and not new_servers:
        message: str = _MSG_ALL_CONFIGURED
    elif _running_in_container():
        message = _MSG_HOST_NET_REQUIRED
    else:
        message = _MSG_NO_DVRS_FOUND

    return {
        "servers": [],
        "manual_add_available": True,
        "message": message,
    }
