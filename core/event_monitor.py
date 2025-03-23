"""
Core event monitoring functionality.
"""
import json
import threading
import time
import requests
from typing import Dict, Any, Optional

from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE  # Up one level, then to helpers

class EventMonitor:
    """Monitors Channels DVR events and triggers alerts."""
    
    def __init__(self, host: str, port: int, alert_manager, server_version: Optional[str] = None):
        """Initialize the event monitor.
        
        Args:
            host: The Channels DVR host
            port: The Channels DVR port
            alert_manager: The alert manager instance
            server_version: The server version (optional)
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.alert_manager = alert_manager
        self.server_version = server_version
        self.running = False
        self.connected = False
        self.connection_initialized = False
        self.event_count = 0
        self.last_heartbeat = time.time()
        self.heartbeat_interval = 300  # Reduced status updates to every 5 minutes
        
        # Keep track of last ping time
        self.last_ping = time.time()
        self.ping_interval = 15  # Send ping every 15 seconds to keep connection alive
        
        # Advanced statistics tracking
        self.stats = {
            "total_events": 0,
            "alert_events": 0,
            "filtered_events": 0,
            "error_events": 0,
            "last_reset": time.time()
        }
    
    def start_monitoring(self):
        """Start monitoring the Channels DVR events."""
        self.running = True
        
        # Start the event monitoring thread without logging connections again
        monitor_thread = threading.Thread(target=self._monitor_events_loop, daemon=True)
        monitor_thread.start()
        
        # Main thread handles heartbeats
        try:
            while self.running:
                time.sleep(1)
                
                # Output regular status updates
                current_time = time.time()
                if current_time - self.last_heartbeat >= self.heartbeat_interval:
                    if self.connected:
                        # Calculate events per minute
                        elapsed_minutes = (current_time - self.stats["last_reset"]) / 60
                        events_per_minute = round(self.stats["total_events"] / elapsed_minutes if elapsed_minutes > 0 else 0, 1)
                        
                        # Report detailed stats
                        log(f"Status update: Connected to {self.host}:{self.port}, " +
                            f"processed {self.stats['total_events']} events " +
                            f"({self.stats['alert_events']} alerts, " +
                            f"{self.stats['filtered_events']} filtered, " +
                            f"{self.stats['error_events']} errors) " +
                            f"[{events_per_minute} events/min]", LOG_STANDARD)
                        
                        # Reset stats every hour to keep numbers relevant
                        if elapsed_minutes > 60:
                            self.stats = {
                                "total_events": 0,
                                "alert_events": 0,
                                "filtered_events": 0,
                                "error_events": 0,
                                "last_reset": time.time()
                            }
                    else:
                        log(f"Status update: Not connected to {self.host}:{self.port}")
                    self.last_heartbeat = current_time
        except KeyboardInterrupt:
            log("Shutting down...")
            self.running = False
    
    def _monitor_events_loop(self):
        """Main loop for monitoring events with reconnection logic."""
        reconnect_delay = 5  # Start with 5 seconds delay
        max_reconnect_delay = 60  # Maximum 60 seconds delay
        
        while self.running:
            try:
                # Try to connect and process events
                self._monitor_events()
            except Exception as e:
                log(f"Connection error: {e}")
                
            # Only reconnect if still running
            if not self.running:
                break
                
            log(f"Reconnecting in {reconnect_delay}s")
            time.sleep(reconnect_delay)
            
            # Increase reconnect delay with exponential backoff
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
    
    def _monitor_events(self):
        """Connect and process events from the Channels DVR server."""
        url = f"{self.base_url}/dvr/events/subscribe"
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
        
        try:
            # Use a session with keep-alive support
            with requests.Session() as session:
                # Configure a longer read timeout (300 seconds instead of 120)
                response = session.get(url, headers=headers, stream=True, timeout=(10, 300))
                
                if response.status_code != 200:
                    log(f"Connection failed: HTTP {response.status_code}")
                    self.connection_initialized = True
                    self.connected = False
                    return
                
                # Mark as connected and initialized but don't log again
                self.connected = True
                self.connection_initialized = True
                
                # Start a thread to keep the connection alive
                ping_thread = threading.Thread(target=self._keep_alive, args=(session,), daemon=True)
                ping_thread.start()
                
                # Process the event stream
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if not self.running:
                        break
                    
                    # Skip empty lines
                    if not line:
                        continue
                    
                    # Process the line (only log in verbose mode)
                    log(f"Event: {line}", level=LOG_VERBOSE)
                    
                    # Try to parse JSON
                    self._process_event_line(line)
        
        except requests.exceptions.RequestException as e:
            log(f"Network error: {e}")
        except Exception as e:
            log(f"Monitoring error: {e}")
        finally:
            self.connected = False
            log("Connection closed")
    
    def _process_event_line(self, line: str):
        """Process a line from the event stream.
        
        Args:
            line: The event line to process
        """
        try:
            # Try to parse JSON
            data = json.loads(line)
            self.event_count += 1
            self._process_event(data)
        except json.JSONDecodeError:
            # Check for SSE format
            if line.startswith('data:'):
                try:
                    data = json.loads(line[5:].strip())
                    self.event_count += 1
                    self._process_event(data)
                except:
                    pass
        except Exception as e:
            log(f"Event processing error: {e}")
            self.stats["error_events"] += 1
    
    def _keep_alive(self, session):
        """Sends periodic requests to keep the main connection alive.
        
        Args:
            session: The requests session to use
        """
        while self.running and self.connected:
            try:
                # Sleep for a bit
                time.sleep(self.ping_interval)
                
                # Only send keep-alive if the connection is still active
                if not self.connected:
                    break
                
                # Send a ping to the status endpoint (lightweight request)
                response = session.get(f"{self.base_url}/status", timeout=5)
                
                # Verify response (log only in verbose mode)
                if response.status_code == 200:
                    log(f"Keep-alive ping successful", level=LOG_VERBOSE)
                else:
                    log(f"Keep-alive ping failed: HTTP {response.status_code}", level=LOG_VERBOSE)
                
                # Update last ping time
                self.last_ping = time.time()
            except Exception as e:
                # Log errors but continue trying
                log(f"Keep-alive error: {e}", level=LOG_VERBOSE)
    
    def _process_event(self, event_data: Dict[str, Any]):
        """Process a single event."""
        try:
            # Increment the total events counter
            self.stats["total_events"] += 1
            
            event_type = event_data.get("Type")
            
            # Handle hello events - but don't log version again
            if event_type == "hello":
                # We already logged this at connection time
                return
            
            # Process the event through the alert manager
            result = self.alert_manager.process_event(event_type, event_data)
            
            # Track event results
            if result:
                self.stats["alert_events"] += 1
            else:
                self.stats["filtered_events"] += 1
                
        except Exception as e:
            self.stats["error_events"] += 1
            log(f"Event processing error: {e}")