"""
Common utilities for test functionality.
"""
import time
import sys
import requests
from typing import Dict, Any, Optional

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

def print_test_header(test_name: str) -> None:
    """Print a formatted header for test output.
    
    Args:
        test_name: The name of the test
    """
    border = "=" * (len(test_name) + 10)
    log(f"{border}")
    log(f"=== {test_name} ===")
    log(f"{border}")

def print_test_footer(success: bool) -> None:
    """Print a formatted footer for test output.
    
    Args:
        success: Whether the test was successful
    """
    if success:
        log("✅ Test completed successfully")
    else:
        log("❌ Test failed")

def get_server_info(host: str, port: int) -> Optional[Dict[str, Any]]:
    """Get server information.
    
    Args:
        host: The server host
        port: The server port
        
    Returns:
        dict: Server information, or None if not available
    """
    try:
        response = requests.get(f"http://{host}:{port}/status", timeout=5)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None