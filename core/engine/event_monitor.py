"""Monitors and processes Channels DVR server events for alert generation."""
import json
import threading
import time
import requests
from typing import Dict, Any, Optional
from pathlib import Path
import sys

from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

# CORE MONITOR
class EventMonitor:
    """Monitors Channels DVR event stream and dispatches events to alert handlers."""
    
    def __init__(self, host: str, port: int, alert_manager, server_version: Optional[str] = None):
        """Initializes event monitor with server connection parameters and alert system."""
        # Configuration
        self.host = host
        self.port = port
        self.alert_manager = alert_manager
        self.server_version = server_version
        
        self.base_url = f"http://{host}:{port}"
        self.event_url = f"{self.base_url}/events"
        
        # State
        self.running = False
        self.connected = False
        self.monitoring_thread = None
        self.last_message_time = 0
        
        # Keep-alive
        self.ping_interval = 15
        self.last_ping = 0
        self.ping_timeout = 60
        
        # Logging
        self.keep_alive_log_interval = 300
        self.last_keep_alive_log = 0
        self.keep_alive_success_streak = 0
        
        # Statistics
        current_time = time.time()
        self.stats = {
            "start_time": current_time,
            "total_events": 0,
            "previous_total_events": 0,
            "alert_events": 0,
            "filtered_events": 0,
            "error_events": 0,
            "last_status_update": current_time,
            "status_update_interval": 300
        }
    
    # MONITORING
    def start_monitoring(self):
        """Starts the event monitoring thread and manages the main application loop."""
        self.running = True
        
        monitor_thread = threading.Thread(target=self._monitor_events_loop, daemon=True)
        monitor_thread.start()
        
        try:
            while self.running:
                time.sleep(1) 
                
        except KeyboardInterrupt:
            log("KeyboardInterrupt received, shutting down...")
            self.running = False
        finally:
            log("Monitoring loop finished.")
    
    # CONNECTION
    def _monitor_events_loop(self):
        """Manages continuous connection to event stream with reconnection logic."""
        reconnect_delay = 5
        max_reconnect_delay = 60
        
        while self.running:
            try:
                self._monitor_events()
            except Exception as e:
                log(f"Connection error: {e}")
                
            if not self.running:
                break
                
            log(f"Reconnecting in {reconnect_delay}s")
            time.sleep(reconnect_delay)
            
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
    
    def _monitor_events(self):
        """Connects to event stream and processes incoming events in real-time."""
        url = f"{self.base_url}/dvr/events/subscribe"
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
        
        try:
            with requests.Session() as session:
                response = session.get(url, headers=headers, stream=True, timeout=(10, None))
                
                if response.status_code != 200:
                    log(f"Connection failed: HTTP {response.status_code}")
                    self.connected = False
                    return
                
                self.connected = True
                
                ping_thread = threading.Thread(target=self._keep_alive, args=(session,), daemon=True)
                ping_thread.start()
                
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if not self.running:
                        break
                    
                    if not line:
                        continue
                    
                    log(f"Event: {line}", level=LOG_VERBOSE)
                    
                    self._process_event_line(line)
        
        except requests.exceptions.RequestException as e:
            log(f"Network error: {e}")
        except Exception as e:
            log(f"Monitoring error: {e}")
        finally:
            self.connected = False
            log("Connection closed")
    
    # PROCESSING
    def _process_event_line(self, line: str):
        """Parses raw event data from stream and forwards it for processing."""
        try:
            data = json.loads(line)
            self.stats["total_events"] += 1
            self._process_event(data)
        except json.JSONDecodeError:
            if line.startswith('data:'):
                try:
                    data = json.loads(line[5:].strip())
                    self.stats["total_events"] += 1
                    self._process_event(data)
                except:
                    pass
        except Exception as e:
            log(f"Event processing error: {e}")
            self.stats["error_events"] += 1
    
    def _process_event(self, event_data: Dict[str, Any]):
        """Processes parsed event data and routes it to appropriate alert handlers."""
        try:
            self.stats["total_events"] += 1
            
            event_type = event_data.get("Type")
            
            if event_type == "hello":
                return
            
            result = self.alert_manager.process_event(event_type, event_data)
            
            if result:
                self.stats["alert_events"] += 1
            else:
                self.stats["filtered_events"] += 1
                
        except Exception as e:
            self.stats["error_events"] += 1
            log(f"Event processing error: {e}")
    
    # KEEP-ALIVE
    def _keep_alive(self, session):
        """Maintains connection by sending periodic status requests to the server."""
        while self.running and self.connected:
            try:
                time.sleep(self.ping_interval)
                
                if not self.connected:
                    break
                
                current_time = time.time()
                
                response = session.get(f"{self.base_url}/status", timeout=5)
                
                should_log = (
                    self.last_keep_alive_log == 0 or 
                    (current_time - self.last_keep_alive_log) >= self.keep_alive_log_interval or
                    response.status_code != 200
                )
                
                if response.status_code == 200:
                    self.keep_alive_success_streak += 1
                    
                    if should_log:
                        if self.keep_alive_success_streak > 10:
                            log(f"Connection healthy: {self.keep_alive_success_streak} consecutive successful pings", 
                                level=LOG_VERBOSE)
                        else:
                            log(f"Keep-alive ping successful", level=LOG_VERBOSE)
                            
                        self.last_keep_alive_log = current_time
                else:
                    log(f"Keep-alive ping failed: HTTP {response.status_code}", level=LOG_VERBOSE)
                    self.last_keep_alive_log = current_time
                    self.keep_alive_success_streak = 0
                
                self.last_ping = current_time
            except Exception as e:
                log(f"Error in keep_alive loop: {e}", LOG_STANDARD)
                time.sleep(5)