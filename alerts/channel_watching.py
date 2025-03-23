"""
Channel-Watching alert implementation.
"""
import threading
import time
from typing import Dict, Any, Optional

from .base import BaseAlert  # Same directory
from .common.session_manager import SessionManager  # Common utilities
from .common.alert_formatter import AlertFormatter  # Alert formatter
from .common.cleanup_mixin import CleanupMixin  # Cleanup mixin
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE  # Parent directory's helpers subdir
from ..helpers.parsing import (  # Parent directory's helpers subdir
    extract_channel_number,
    extract_channel_name,
    extract_device_name,
    extract_ip_address,
    extract_resolution,
    extract_source_from_session_id
)

# Global lock to prevent concurrent processing of events
event_lock = threading.Lock()

class ChannelWatchingAlert(BaseAlert, CleanupMixin):
    """Alert for channel watching activity."""
    
    # Alert type name
    ALERT_TYPE = "Channel-Watching"
    DESCRIPTION = "Notifications when someone is watching TV"
    
    def __init__(self, notification_manager):
        """Initialize the Channel-Watching alert."""
        # Call parent inits
        BaseAlert.__init__(self, notification_manager)
        CleanupMixin.__init__(self)
        
        # Initialize session manager for tracking sessions, events, and notifications
        self.session_manager = SessionManager()
        
        # Initialize alert formatter with configuration
        self.alert_formatter = AlertFormatter(config={
            'show_ip': True,
            'show_source': True,
            'use_emoji': True,
            'title_prefix': "📺 ",
        })
        
        # Explicitly store time module reference to avoid 'time not defined' errors
        self.time_module = time
        
        # Configuration
        self.alert_cooldown = 5  # Seconds between alerts for the same channel-device
        
        # Get host/port from environment variables
        from os import environ
        self.host = environ.get("CHANNELS_DVR_HOST")
        self.port = int(environ.get("CHANNELS_DVR_PORT", "8089"))
        
        # Channel cache parameters
        self.channel_cache = {}
        
        # Configure cleanup to run automatically
        self.configure_cleanup(
            enabled=True,
            interval=3600,  # Run cleanup every hour
            auto_cleanup=True  # Run in background thread
        )
        
    # Channel caching happens in main.py after server connection is established
    def _cache_channels(self):
        """Cache channel information at startup for faster lookups later."""
        try:
            import requests
            
            # Single log message with URL included
            log(f"Pre-caching channel information from /api/v1/channels", level=LOG_STANDARD)
            
            # Direct API call to get all channels at once
            response = requests.get(f"http://{self.host}:{self.port}/api/v1/channels", timeout=10)
            
            if response.status_code == 200:
                channels_data = response.json()
                
                # Process and cache each channel
                for channel in channels_data:
                    number = channel.get('number')
                    name = channel.get('name')
                    logo_url = channel.get('logo_url')
                    
                    if number:
                        channel_number_str = str(number)
                        # Store both name and logo_url in the cache
                        self.channel_cache[channel_number_str] = {
                            'name': name or "Unknown Channel",
                            'logo_url': logo_url or ""
                        }
                
                # Just one log message with the count
                log(f"Cached information for {len(self.channel_cache)} channels", level=LOG_STANDARD)
            else:
                log(f"Failed to fetch channels: HTTP {response.status_code}", level=LOG_STANDARD)
                
        except Exception as e:
            log(f"Error caching channel information: {e}", level=LOG_STANDARD)
    
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determine if this alert should handle the given event.
        
        Args:
            event_type: The type of the event
            event_data: The event data dictionary
            
        Returns:
            bool: True if this alert should handle the event, False otherwise
        """
        return self._is_watching_event(event_type, event_data)
    
    def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Handle a channel watching event."""
        with event_lock:
            try:
                # Extract basic info for tracking
                value = event_data.get("Value", "")
                channel_number = extract_channel_number(value)
                device_name = extract_device_name(value)
                
                if not channel_number or not device_name:
                    return False
                
                # Generate a robust tracking key using only channel number and device
                tracking_key = f"ch{channel_number}-{device_name}"
                
                # Also use session ID for enhanced tracking
                session_id = event_data.get("Name", "")
                
                log(f"Processing event for {tracking_key} (session: {session_id})", level=LOG_VERBOSE)
                
                # Check if this event is already being processed
                if self.session_manager.is_event_processing(tracking_key):
                    log(f"Skipping duplicate event for {tracking_key} - already processing", level=LOG_VERBOSE)
                    return False
                
                # Check for cooldown period using the formatter
                if not self.alert_formatter.should_send_notification(
                        self.session_manager, 
                        tracking_key, 
                        self.alert_cooldown):
                    return False
                
                # Mark this event as being processed
                self.session_manager.mark_event_processing(tracking_key)
                
                try:
                    # Process the event and send an alert
                    success = self._process_watching_event(event_data, tracking_key)
                    
                    return success
                finally:
                    # Always clean up processing marker
                    self.session_manager.complete_event_processing(tracking_key)
            except Exception as e:
                log(f"Error processing event: {e}")
                return False
    
    def _is_watching_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Check if this is a viewing event we should handle.
        
        Args:
            event_type: The type of the event
            event_data: The event data dictionary
            
        Returns:
            bool: True if this is a watching event, False otherwise
        """
        # First check if this is an activities.set event with a value
        if (event_type != "activities.set" or
            "Value" not in event_data or
            not event_data.get("Value")):
            return False
        
        # Get the value text
        value = event_data.get("Value", "")
        
        # Detailed logging of event processing
        log(f"Checking event: {event_type} - Value: {value[:50]}...", level=LOG_VERBOSE)
        
        # Check for various patterns that indicate channel watching
        return (
            "Watching ch" in value or
            ("channel" in value.lower() and "watching" in value.lower())
        )
    
    def _process_watching_event(self, event_data: Dict[str, Any], tracking_key: str) -> bool:
        """Process a watching event and send an alert with complete information."""
        try:
            session_id = event_data.get("Name", "")
            value = event_data.get("Value", "")
            
            # Extract basic channel information
            channel_number = extract_channel_number(value)
            if not channel_number:
                return False
                
            # Extract device name
            device_name = extract_device_name(value)
            if not device_name:
                device_name = "Unknown device"
            
            # Check if we're already tracking this session with the SAME channel
            if self.session_manager.has_session(session_id):
                session_data = self.session_manager.get_session(session_id)
                old_channel_info = session_data.get("channel_info", {})
                old_channel_number = old_channel_info.get("number")
                
                # If it's the same channel, just update the timestamp and skip notification
                if old_channel_number == channel_number:
                    # Update the timestamp to prevent session from being considered stale
                    self.session_manager.add_session(
                        session_id,
                        channel_info=old_channel_info,
                        tracking_key=tracking_key
                    )
                    log(f"Still watching channel {channel_number} on {device_name} - update only", LOG_VERBOSE)
                    return False
            
            # Check if this device is already watching a different channel
            # If so, process the exit for that channel first
            current_time = self.time_module.time()
            device_sessions = []
            
            # Find any active sessions for this device
            for active_session_id, session_data in self.session_manager.active_sessions.items():
                if active_session_id != session_id:  # Not the current session
                    active_channel_info = session_data.get("channel_info", {})
                    active_device = active_channel_info.get("device", "")
                    
                    # If this is the same device but a different channel/session
                    if active_device == device_name:
                        device_sessions.append(active_session_id)
            
            # Process exits for any previous sessions from this device
            for old_session_id in device_sessions:
                # Log the exit before processing the new channel
                old_session_data = self.session_manager.get_session(old_session_id)
                if old_session_data:
                    old_channel_info = old_session_data.get("channel_info", {})
                    
                    log(
                        f"Exited {old_channel_info.get('name','Unknown')} "
                        f"(Ch{old_channel_info.get('number','')}) - Device: {old_channel_info.get('device','N/A')}, "
                        f"IP: {old_channel_info.get('ip','N/A')}, Source: {old_channel_info.get('source','N/A')}",
                        level=LOG_STANDARD
                    )
                    
                    # Remove the old session
                    self.session_manager.remove_session(old_session_id)
            
            # Create a comprehensive channel info object with all available information
            channel_info = {}
            channel_info["number"] = channel_number
            
            # Rest of existing code to build channel_info...
            channel_name = extract_channel_name(value)
            if channel_name:
                channel_info["name"] = channel_name
            
            # Add device info
            if device_name:
                channel_info["device"] = device_name
            else:
                channel_info["device"] = "Unknown device"
            
            # Add IP address if available
            ip_address = extract_ip_address(value)
            if ip_address:
                channel_info["ip"] = ip_address
            
            # Add source from session ID
            source = extract_source_from_session_id(session_id)
            if source:
                channel_info["source"] = source
            else:
                channel_info["source"] = "Unknown source"
            
            # Add resolution if available
            resolution = extract_resolution(value)
            if resolution:
                channel_info["resolution"] = resolution
            
            # Look up channel name if not present in event
            if not channel_info.get("name") or not channel_info.get("logo_url"):
                # First check our cache
                channel_number_str = str(channel_number)
                if channel_number_str in self.channel_cache:
                    cached_info = self.channel_cache[channel_number_str]
                    if isinstance(cached_info, dict):
                        # New cache format with name and logo_url
                        if not channel_info.get("name") and cached_info.get('name'):
                            channel_info["name"] = cached_info['name']
                        if cached_info.get('logo_url'):
                            channel_info["logo_url"] = cached_info['logo_url']
                    else:
                        # Backward compatibility with old cache format (string)
                        if not channel_info.get("name"):
                            channel_info["name"] = cached_info
                else:
                    # Fall back to API lookup if not in cache
                    if not channel_info.get("name"):
                        channel_info["name"] = "Unknown Channel"
            
            # Send notification with complete information
            success = self._send_alert(channel_info)
            
            if success:
                # Mark this channel-device combo as notified
                self.session_manager.record_notification(tracking_key)
                
                # Store in active sessions
                self.session_manager.add_session(
                    session_id,
                    channel_info=channel_info,
                    tracking_key=tracking_key,
                )
                
                return True
            
            return False
        except Exception as e:
            log(f"Error processing watching event: {e}")
            return False
    
    def _send_alert(self, channel_info: Dict[str, Any]) -> bool:
        """Format and send an alert for a channel viewing event."""
        try:
            # Use our alert formatter to create a well-formatted message
            device_info = {
                'name': channel_info.get('device', 'Unknown device'),
                'ip_address': channel_info.get('ip', None),
                'resolution': channel_info.get('resolution', None),
                'source': channel_info.get('source', None)
            }
            
            # Format channel name and number for title
            channel_name = channel_info.get('name', 'Unknown Channel')
            channel_number = channel_info.get('number', '')
            
            # Set title to "Channels DVR - Watching TV"
            title = "Channels DVR - Watching TV"
            
            # Build message body with proper structure
            message_parts = []
            
            # Channel name with emoji as first line
            message_parts.append(f"📺 {channel_name}")
            
            # Channel number
            message_parts.append(f"Channel: {channel_number}")
            
            # Device name
            message_parts.append(f"Device: {device_info['name']}")
            
            # IP address (if enabled)
            if device_info.get('ip_address'):
                message_parts.append(f"IP: {device_info['ip_address']}")
            
            # Source (if enabled)
            if device_info.get('source'):
                message_parts.append(f"Source: {device_info['source']}")
            
            # Resolution (if available)
            if device_info.get('resolution'):
                message_parts.append(f"Resolution: {device_info['resolution']}")
            
            # Join all parts with line breaks
            message = "\n".join(message_parts)
            
            # Log activity
            log(
                f"Watching {channel_info.get('name','Unknown')} "
                f"(Ch{channel_info.get('number','')}) - Device: {channel_info.get('device','N/A')}, "
                f"IP: {channel_info.get('ip','N/A')}, Source: {channel_info.get('source','N/A')}",
                level=LOG_STANDARD
            )
            
            # Send the alert via BaseAlert.send_alert
            return super().send_alert(
                title=title, 
                message=message,
                image_url=channel_info.get('logo_url')
            )
        except Exception as e:
            log(f"Error sending alert: {e}")
            return False
    
    def process_end_event(self, session_id: str) -> None:
        """Handle end of viewing session.
        
        Args:
            session_id: The ID of the session that ended
        """
        try:
            # Get session data from the session manager
            session_data = self.session_manager.get_session(session_id)
            
            if session_data:
                # Get channel name for logging
                channel_info = session_data.get("channel_info", {})
                channel_name = channel_info.get("name", "Unknown channel")
                channel_number = channel_info.get("number", "")
                
                log(
                    f"Exited {channel_info.get('name','Unknown')} "
                    f"(Ch{channel_info.get('number','')}) - Device: {channel_info.get('device','N/A')}, "
                    f"IP: {channel_info.get('ip','N/A')}, Source: {channel_info.get('source','N/A')}",
                    level=LOG_STANDARD
                )
                
                # Remove from active sessions
                self.session_manager.remove_session(session_id)
        except Exception as e:
            log(f"Error processing end event: {e}")
    
    def run_cleanup(self) -> None:
        """Implement specific cleanup logic using the CleanupMixin."""
        try:
            # Clean up stale sessions
            removed_sessions = self.cleanup_dict_by_time(
                self.session_manager.active_sessions,
                ttl=14400,  # 4 hours
                timestamp_key='timestamp'
            )
            
            # Clean up processing events
            removed_events = self.cleanup_dict_by_timestamp(
                self.session_manager.processing_events,
                ttl=300  # 5 minutes
            )
            
            # Clean up notification history
            removed_notifications = self.cleanup_dict_by_timestamp(
                self.session_manager.notification_history,
                ttl=86400  # 24 hours
            )
            
            # Log the results
            self.log_cleanup_results(
                component="ChannelWatchingAlert",
                removed={
                    "sessions": len(removed_sessions),
                    "events": len(removed_events),
                    "notifications": len(removed_notifications)
                }
            )
        except Exception as e:
            log(f"Error in cleanup: {e}")
    
    def cleanup(self) -> None:
        """Clean up stale session data to prevent memory leaks."""
        try:
            # Use the run_cleanup method from the CleanupMixin
            self.run_cleanup()
        except Exception as e:
            log(f"Error cleaning up sessions: {e}")
            
    def __del__(self):
        """Clean up resources when instance is deleted."""
        try:
            # Stop the auto-cleanup thread if running
            self.stop_cleanup()
        except:
            pass