"""Standardized output helpers for ChannelWatch diagnostics."""

import requests
from typing import Dict, Any, Optional

from ..helpers.logging import log
from ..helpers.dvr_target import build_safe_dvr_request

_DVR_HTTP_SESSION = requests.Session()
_DVR_HTTP_SESSION.trust_env = False


def print_test_header(test_name: str) -> None:
    """Displays a formatted test header."""
    log(f"🔧 ── {test_name} ──")


def print_result(success: bool, detail: str = "") -> None:
    """Single result line for a test."""
    if success:
        icon = "✅"
        status = "PASS"
    else:
        icon = "❌"
        status = "FAIL"
    suffix = f"  {detail}" if detail else ""
    log(f"{icon} [{status}]{suffix}")


def print_section(label: str) -> None:
    """Section separator for multi-step tests."""
    log(f"  ▸ {label}")


def get_server_info(host: str, port: int) -> Optional[Dict[str, Any]]:
    """Retrieves status information from the server via HTTP request."""
    try:
        request = build_safe_dvr_request(host, port, "/status")
        if request is None:
            return None
        # build_safe_dvr_request pins a fully validated numeric address.
        response = _DVR_HTTP_SESSION.get(  # nosemgrep: python.django.security.injection.ssrf.ssrf-injection-requests.ssrf-injection-requests
            request.url,
            headers={"Host": request.host_header},
            timeout=5,
            allow_redirects=False,
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None
