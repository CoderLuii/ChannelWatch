"""
Diagnostic and monitoring tools for Channels DVR integration.
"""
import json
import time
import requests
from typing import Dict, Any, Optional

from .logging import log, LOG_STANDARD, LOG_VERBOSE

# EVENT STREAM
def monitor_event_stream(host: str, port: int, duration: int = 30) -> bool:
    """Captures and logs event stream data from Channels DVR for specified duration."""
    import threading
    
    base_url = f"http://{host}:{port}"
    url = f"{base_url}/dvr/events/subscribe"
    
    log(f"Monitoring events for {duration} seconds")
    
    monitor_data = {
        "running": True,
        "event_count": 0,
        "success": False
    }
    
    def monitoring_thread():
        try:
            session = requests.Session()
            
            headers = {
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
            
            log("Connecting to event stream...")
            
            response = session.get(
                url, 
                headers=headers, 
                stream=True, 
                timeout=(10, duration + 30)
            )
            
            if response.status_code != 200:
                log(f"Connection failed - HTTP {response.status_code}")
                return
            
            log("Connected to event stream")
            monitor_data["success"] = True
            
            for line in response.iter_lines(decode_unicode=True):
                if not monitor_data["running"]:
                    break
                
                if line:
                    monitor_data["event_count"] += 1
                    log(f"Event: {line}")
                    
        except Exception as e:
            if not monitor_data["success"] or monitor_data["event_count"] == 0:
                error_msg = str(e) if str(e) else "Unknown error occurred"
                if not any(x in error_msg.lower() for x in ["timeout", "connection"]):
                    log(f"Monitoring error: {error_msg}")
        finally:
            if 'response' in locals():
                response.close()
            if 'session' in locals():
                session.close()
    
    thread = threading.Thread(target=monitoring_thread)
    thread.daemon = True
    thread.start()
    
    time.sleep(duration)
    
    monitor_data["running"] = False
    time.sleep(0.5)
    
    log(f"Monitoring complete - {monitor_data['event_count']} events received")
    
    return monitor_data["success"]
