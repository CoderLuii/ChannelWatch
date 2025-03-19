"""
Channel-Watching alert implementation.
"""
import re
import time
from datetime import datetime
from threading import Timer

from channelwatch.alerts.base import BaseAlert
from channelwatch.helpers.logging import log

class ChannelWatchingAlert(BaseAlert):
    """Alert for channel watching activity."""
    
    # Alert type name
    ALERT_TYPE = "Channel-Watching"
    
    def __init__(self, notification_manager, max_delay=3):
        """Initialize the Channel-Watching alert."""
        super().__init__(notification_manager)
        self.max_delay = max_delay
        self.current_channel = None
        self.scheduled_alerts = {}
    
    def _format_timestamp(self, timestamp_str):
        """Format a timestamp string to be more readable."""
        try:
            date_part, time_part = timestamp_str.split(" ")
            year, month, day = date_part.split("/")
            
            time_elements = time_part.split(":")
            hour = int(time_elements[0])
            minute = int(time_elements[1])
            
            am_pm = "AM" if hour < 12 else "PM"
            hour = hour % 12
            if hour == 0:
                hour = 12
                
            month_names = ["January", "February", "March", "April", "May", "June", 
                          "July", "August", "September", "October", "November", "December"]
            month_name = month_names[int(month) - 1]
            
            return f"{month_name} {int(day)} at {hour}:{minute:02d} {am_pm}"
        except Exception:
            return timestamp_str
        
    def _handle_match(self, line, timestamp=None):
        """Handle a matched line for Channel-Watching alert."""
        channel = self._extract_channel_number(line)
        if not channel:
            return False
            
        channel_key = f"ch{channel}"
        
        # Check if this is a new channel
        if channel_key != self.current_channel:
            self.current_channel = channel_key
            log(f"Viewer switched to {channel_key}")
            
            # Create a new alert entry if one doesn't exist
            if channel_key not in self.scheduled_alerts:
                formatted_time = self._format_timestamp(timestamp) if timestamp else datetime.now().strftime("%I:%M %p")
                self.scheduled_alerts[channel_key] = {
                    "channel_number": channel,
                    "start_time": formatted_time,
                    "timer": None
                }
        
        # Update alert info based on the log line
        if channel_key in self.scheduled_alerts:
            self._update_alert_info(channel_key, line)
            return True
            
        return False
    
    def _update_alert_info(self, channel_key, line):
        """Update alert information based on the log line."""
        alert_info = self.scheduled_alerts[channel_key]
        
        # Extract channel name
        connection_match = re.search(r'connection to M3U-(\w+) for ch\d+ (.+?)($|\s\()', line)
        if connection_match:
            alert_info["source"] = connection_match.group(1)
            alert_info["channel_name"] = connection_match.group(2).strip()
        
        # Extract IP address
        ip_match = re.search(r'channel \d+ from ([\d\.]+)', line)
        if ip_match:
            alert_info["ip_address"] = ip_match.group(1)
            
        # Extract resolution
        resolution_match = re.search(r'h264 (\d+x\d+)', line)
        if resolution_match:
            alert_info["resolution"] = resolution_match.group(1)
        
        # If we have either a channel name or specific message indicator, consider it ready to send
        is_ready_to_send = (
            alert_info.get("channel_name") is not None or 
            "Starting live str" in line or
            "Probed live stream" in line
        )
        
        # If ready to send, do it immediately
        if is_ready_to_send:
            # Cancel any existing timer
            if alert_info.get("timer") and alert_info["timer"].is_alive():
                alert_info["timer"].cancel()
            
            # Send alert immediately
            self._send_alert(channel_key)
        else:
            # Schedule alert if not already scheduled
            if not alert_info.get("timer") or not alert_info["timer"].is_alive():
                # Cancel any existing timer
                if alert_info.get("timer"):
                    try:
                        alert_info["timer"].cancel()
                    except:
                        pass
                        
                # Schedule new timer
                alert_info["timer"] = Timer(self.max_delay, self._send_alert, args=[channel_key])
                alert_info["timer"].daemon = True
                alert_info["timer"].start()
                log(f"Scheduling alert for {channel_key} in {self.max_delay}s")
    
    def _extract_channel_number(self, line):
        """Extract channel number from a log line."""
        ch_match = re.search(r'ch(?:annel)?\s*(\d+)', line, re.IGNORECASE)
        if ch_match:
            return ch_match.group(1)
        return None
    
    def _send_alert(self, channel_key):
        """Send a Channel-Watching alert."""
        if channel_key not in self.scheduled_alerts:
            return
            
        alert_info = self.scheduled_alerts[channel_key]
        
        # Build the message with available information
        message_parts = []
        
        if alert_info.get("channel_name") and alert_info.get("channel_number"):
            message_parts.append(f"Watching: {alert_info['channel_name']} (ch{alert_info['channel_number']})")
        elif alert_info.get("channel_name"):
            message_parts.append(f"Watching: {alert_info['channel_name']}")
        elif alert_info.get("channel_number"):
            message_parts.append(f"Watching: Channel {alert_info['channel_number']}")
        else:
            message_parts.append("Watching: Live TV")
        
        if alert_info.get("resolution"):
            message_parts.append(f"Resolution: {alert_info['resolution']}")
        
        if alert_info.get("source"):
            message_parts.append(f"Source: {alert_info['source']}")
        
        message = "\n".join(message_parts)
        
        # Send the notification
        success = self.send_alert(message)
        
        # Cleanup - remove this scheduled alert
        del self.scheduled_alerts[channel_key]
        
        # Single log message for successful alerts
        if success:
            channel_name = alert_info.get('channel_name', 'TV')
            log(f"Alert sent: {channel_name} on {channel_key}")