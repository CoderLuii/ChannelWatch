"""
Core log monitoring functionality.
"""
import os
import time
import re
from channelwatch.helpers.logging import log
from channelwatch.helpers.parsing import simplify_line

class LogMonitor:
    """Monitors a log file for patterns and triggers alerts."""
    
    def __init__(self, log_file_path, alert_manager, interval=10):
        """Initialize the log monitor."""
        self.log_file_path = log_file_path
        self.alert_manager = alert_manager
        self.interval = interval
        
        # Initialize recent alerts tracking for deduplication
        self.recent_alerts = {}
        self.alert_cooldown = 10  # Seconds between similar alerts
        
    def start_monitoring(self):
        """Start monitoring the log file."""
        try:
            log_file = open(self.log_file_path, "r")
        except Exception as e:
            log(f"ERROR: Unable to open log file: {e}")
            return
        
        # Move to end of file to monitor only new entries
        log_file.seek(0, os.SEEK_END)
        
        # Main monitoring loop
        while True:
            try:
                line = log_file.readline()
                if not line:
                    # If no new line, wait for the next interval
                    time.sleep(self.interval)
                    continue

                # Skip empty lines
                line = line.strip()
                if line == "":
                    continue

                # Process the line with the alert manager
                self._process_line(line)
                
            except Exception as e:
                log(f"Error in monitoring loop: {e}")
                time.sleep(self.interval)  # Wait before retrying
    
    def _process_line(self, line):
        """Process a log line and trigger alerts if needed."""
        # Extract timestamp if present at the beginning of the line
        timestamp = None
        timestamp_match = re.match(r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d+)', line)
        if timestamp_match:
            timestamp = timestamp_match.group(1)
            
        # Let the alert manager process the line
        matched_alert = self.alert_manager.process_line(line, timestamp=timestamp)
        
        if matched_alert:
            # Check for duplicate alerts
            simplified_line = simplify_line(line)
            current_time = time.time()
            
            # Skip if we've seen a similar alert recently
            if (simplified_line in self.recent_alerts and 
                current_time - self.recent_alerts[simplified_line] < self.alert_cooldown):
                return
            
            # Update recent alerts
            self.recent_alerts[simplified_line] = current_time
            self.recent_alerts = {k: v for k, v in self.recent_alerts.items() 
                                if current_time - v < self.alert_cooldown}