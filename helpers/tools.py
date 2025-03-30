"""
Tools for setup and troubleshooting.
"""
import json
import time
import requests
from typing import Dict, Any, Optional

from .logging import log, LOG_STANDARD, LOG_VERBOSE

def monitor_event_stream(host: str, port: int, duration: int = 30) -> bool:
    """Monitor the event stream for a specific duration.
    
    Args:
        host: The Channels DVR host
        port: The Channels DVR port
        duration: The duration to monitor in seconds (default: 30)
        
    Returns:
        bool: True if monitoring was successful, False otherwise
    """
    import threading
    
    base_url = f"http://{host}:{port}"
    url = f"{base_url}/dvr/events/subscribe"
    
    log(f"Monitoring events for {duration} seconds")
    
    # Shared values between threads
    monitor_data = {
        "running": True,
        "event_count": 0,
        "success": False
    }
    
    # Thread that does the actual monitoring
    def monitoring_thread():
        try:
            # Use a session with keep-alive to maintain connection
            session = requests.Session()
            
            # Configure request with very long timeout
            headers = {
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
            
            log("Connecting to event stream...")
            
            # Use a much longer timeout (connect timeout, read timeout)
            # The timeout needs to exceed the test duration
            response = session.get(
                url, 
                headers=headers, 
                stream=True, 
                timeout=(10, duration + 30)  # Read timeout longer than test duration
            )
            
            if response.status_code != 200:
                log(f"Connection failed - HTTP {response.status_code}")
                return
            
            log("Connected to event stream")
            monitor_data["success"] = True
            
            # Process events until the end of the test
            for line in response.iter_lines(decode_unicode=True):
                if not monitor_data["running"]:
                    break
                
                if line:
                    monitor_data["event_count"] += 1
                    log(f"Event: {line}")
                    
        except Exception as e:
            # If we already had successful connection and received some events,
            # don't mark the test as failed just because of a late error
            if not monitor_data["success"] or monitor_data["event_count"] == 0:
                # Only log if we never connected successfully
                error_msg = str(e) if str(e) else "Unknown error occurred"
                if not any(x in error_msg.lower() for x in ["timeout", "connection"]):
                    log(f"Monitoring error: {error_msg}")
        finally:
            # Always clean up resources
            if 'response' in locals():
                response.close()
            if 'session' in locals():
                session.close()
    
    # Start the monitoring thread
    thread = threading.Thread(target=monitoring_thread)
    thread.daemon = True
    thread.start()
    
    # Wait for the specified duration
    time.sleep(duration)
    
    # Signal the thread to stop and give it a moment to clean up
    monitor_data["running"] = False
    time.sleep(0.5)
    
    # Log results
    log(f"Monitoring complete - {monitor_data['event_count']} events received")
    
    return monitor_data["success"]
