"""Utility functions for test execution and result reporting."""
import time
import sys
import requests
from typing import Dict, Any, Optional

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

# TEST OUTPUT

def print_test_header(test_name: str) -> None:
    """Displays a formatted header with the test name surrounded by borders."""
    border = "=" * (len(test_name) + 10)
    log(f"{border}")
    log(f"=== {test_name} ===")
    log(f"{border}")

def print_test_footer(success: bool) -> None:
    """Prints a test result message with success or failure indicator."""
    if success:
        log("✅ Test completed successfully")
    else:
        log("❌ Test failed")

# SERVER COMMUNICATION

def get_server_info(host: str, port: int) -> Optional[Dict[str, Any]]:
    """Retrieves status information from the server via HTTP request."""
    try:
        response = requests.get(f"http://{host}:{port}/status", timeout=5)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None