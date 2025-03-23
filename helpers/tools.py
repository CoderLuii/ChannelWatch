"""
Tools for setup and troubleshooting.
"""
import json
import time
import requests
from typing import Dict, Any, Optional

from .logging import log, LOG_STANDARD, LOG_VERBOSE

def monitor_event_stream(host: str, port: int, duration: int = 30):
    """Monitor the event stream for a specific duration.
    
    Args:
        host: The Channels DVR host
        port: The Channels DVR port
        duration: The duration to monitor in seconds (default: 30)
    """
    base_url = f"http://{host}:{port}"
    url = f"{base_url}/dvr/events/subscribe"
    
    log(f"Monitoring events for {duration} seconds")
    
    headers = {
        "Accept": "text/event-stream",
        "Connection": "keep-alive"
    }
    
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=duration+5)
        
        if response.status_code != 200:
            log(f"Connection failed - HTTP {response.status_code}")
            return
            
        log("Connected to event stream")
        event_count = 0
        
        # Read events for specified duration
        start_time = time.time()
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
                
            event_count += 1
            log(f"Event: {line}")
            
            # Check if duration has elapsed
            if time.time() - start_time >= duration:
                break
            
        log(f"Monitoring complete - {event_count} events received")
    except Exception as e:
        log(f"Monitoring error: {e}")

def test_connectivity(host: str, port: int) -> Optional[str]:
    """Test connectivity to the Channels DVR server.
    
    Args:
        host: The Channels DVR host
        port: The Channels DVR port
        
    Returns:
        str: Server version if connected, None if connection failed
    """
    try:
        log(f"Testing connection to Channels DVR at {host}:{port}")
        resp = requests.get(f"http://{host}:{port}/status", timeout=5)
        if resp.status_code == 200:
            try:
                status = resp.json()
                version = status.get('version', 'Unknown')
                log(f"Connected to server version {version}")
                return version
            except:
                log("Connected, but couldn't parse version information")
                return "Unknown"
        else:
            log(f"Connection failed: HTTP {resp.status_code}")
            return None
    except Exception as e:
        log(f"Connection error: {e}")
        return None