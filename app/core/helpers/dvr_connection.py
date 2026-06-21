"""DVR server connection configuration."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def format_dvr_http_host(host: Any) -> str:
    """Format a DVR host for use in an HTTP origin."""
    host_str = "" if host is None else str(host).strip()
    if host_str.startswith("[") and host_str.endswith("]"):
        return host_str
    if ":" in host_str:
        return f"[{host_str}]"
    return host_str


def build_dvr_base_url(host: Any, port: Any = 8089) -> str:
    """Build a Channels DVR HTTP origin, bracketing IPv6 literals."""
    try:
        normalized_port = int(port)
    except (TypeError, ValueError):
        normalized_port = 8089
    return f"http://{format_dvr_http_host(host)}:{normalized_port}"


@dataclass
class DVRConnection:
    id: str
    name: str
    host: str
    port: int = 8089
    enabled: bool = True
    api_key: str = ""
    overrides: Optional[Dict[str, Any]] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        """Full HTTP base URL for this DVR server."""
        return build_dvr_base_url(self.host, self.port)

    def __str__(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"
