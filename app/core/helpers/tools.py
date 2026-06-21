"""
Diagnostic and monitoring tools for Channels DVR integration.
"""

import time
import httpx

from .logging import log
from .dvr_connection import build_dvr_base_url


# EVENT STREAM
def monitor_event_stream(host: str, port: int, duration: int = 30) -> bool:
    """Captures and logs event stream data from Channels DVR for specified duration."""
    import threading

    base_url = build_dvr_base_url(host, port)
    url = f"{base_url}/dvr/events/subscribe"

    log(f"Monitoring events for {duration} seconds")

    monitor_data = {"running": True, "event_count": 0, "success": False}

    def monitoring_thread():
        try:
            headers = {
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }

            log("Connecting to event stream...")
            timeout = httpx.Timeout(10.0, read=duration + 30)

            with httpx.Client() as client:
                with client.stream(
                    "GET", url, headers=headers, timeout=timeout
                ) as response:
                    if response.status_code != 200:
                        log(f"Connection failed - HTTP {response.status_code}")
                        return

                    log("Connected to event stream")
                    monitor_data["success"] = True

                    for line in response.iter_lines():
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

    thread = threading.Thread(target=monitoring_thread)
    thread.daemon = True
    thread.start()

    time.sleep(duration)

    monitor_data["running"] = False
    time.sleep(0.5)

    log(f"Monitoring complete - {monitor_data['event_count']} events received")

    return bool(monitor_data["success"])
