"""
Alert formatting and message composition for notifications.
"""
from typing import Dict, Any, List, Optional, Union

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

class AlertFormatter:
    """
    Formats alert messages for different notification types with consistent styling.
    
    This class provides flexible, configurable formatting for alert notifications,
    handling various content types, image attachments, and styling preferences.
    It centralizes notification formatting to ensure consistent alerts across
    different alert types.
    
    Key features:
    - Configurable display options (show/hide components)
    - Support for channel, device, and program information
    - Image URL handling for thumbnails/logos
    - Emoji support for visual indicators
    - Component-based message assembly
    
    Attributes:
        config: Dictionary of formatting preferences controlling what appears in notifications
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the alert formatter with optional configuration.
        
        Args:
            config: Optional configuration dictionary with formatting preferences.
                   Can include any of the keys in the default config below.
        """
        # Default configuration
        self.config = {
            'show_channel_name': True,    # Show channel name in notifications
            'show_channel_number': True,  # Show channel number in notifications
            'show_program_name': True,    # Show program name in notifications
            'show_device_name': True,     # Show device name in notifications
            'show_ip': True,              # Show IP addresses in notifications
            'show_source': True,          # Show source information in notifications
            'use_emoji': True,            # Use emoji in notification titles
            'title_prefix': "",           # Default empty prefix for notifications
            'compact_mode': False,        # Use more compact formatting
            'max_line_length': 100,       # Maximum line length before truncation
        }
        
        # Override with provided configuration
        if config:
            self.config.update(config)
    
    def format_title(self, title: str, emoji: Optional[str] = None) -> str:
        """
        Format a notification title, optionally with an emoji prefix.
        
        Creates a standardized notification title, optionally with emoji prefix.
        Respects the use_emoji configuration setting.
        
        Args:
            title: The base title text to use
            emoji: Optional emoji to prepend (overrides default prefix if provided)
            
        Returns:
            Formatted title string
        """
        # Skip emoji if disabled
        if not self.config['use_emoji']:
            return title
            
        # Use provided emoji or default
        prefix = emoji if emoji else self.config['title_prefix']
        
        # Combine prefix and title
        return f"{prefix}{title}"
    
    def format_message(self, 
                       message_parts: Dict[str, Any],
                       order: Optional[List[str]] = None) -> str:
        """
        Format a notification message from component parts.
        
        This method formats a structured notification message from individual components.
        It respects configuration settings about which components should be displayed,
        handles nested channel information, and assembles the final message in the
        specified order.
        
        Components are automatically formatted with appropriate labels and emojis.
        For example, a device component will be formatted as "Device: {name}".
        
        Args:
            message_parts: Dictionary of message components with their values.
                           Supported keys:
                           - 'channel': Dict with 'name', 'number', 'program_title', 'stream_count'
                           - 'device': Device name
                           - 'ip': IP address
                           - 'source': Stream source
                           - 'resolution': Stream resolution
                           - 'status', 'details', 'time', 'custom': Other components
            order: Optional list specifying the order of components in the message.
                   If not provided, uses a default order.
            
        Returns:
            Formatted message string with line breaks
        """
        if not message_parts:
            return ""
            
        lines = []
        
        # Debug the message parts
        log(f"Formatting message with parts: {message_parts}", level=LOG_VERBOSE)
            
        # Use specified order or default order
        if not order:
            order = [
                'channel', 'program', 'resolution', 'device', 'ip', 'source', 
                'status', 'details', 'time', 'custom'
            ]
        
        # Store stream count for adding after source
        stream_count = None
        if ('channel' in message_parts and 
            isinstance(message_parts['channel'], dict) and 
            'stream_count' in message_parts['channel']):
            stream_count = message_parts['channel']['stream_count']
            
        # Build message with components in order
        for component in order:
            # Skip components that should be hidden based on config
            if component == 'channel' and not (self.config['show_channel_name'] or self.config['show_channel_number']):
                continue
            if component == 'ip' and not self.config['show_ip']:
                continue
            if component == 'source' and not self.config['show_source']:
                continue
            if component == 'device' and not self.config['show_device_name']:
                continue
                
            value = message_parts.get(component)
            if value:
                # Format based on component type
                if component == 'channel' and isinstance(value, dict):
                    # Special handling for channel information
                    if value.get('name') and self.config['show_channel_name']:
                        lines.append(f"📺 {value['name']}")
                    if value.get('number') and self.config['show_channel_number']:
                        lines.append(f"Channel: {value['number']}")
                    # Display program title if available
                    if value.get('program_title') and self.config['show_program_name']:
                        lines.append(f"Program: {value['program_title']}")
                        log(f"Added program line: Program: {value['program_title']}", level=LOG_VERBOSE)
                elif component == 'resolution':
                    lines.append(f"Resolution: {value}")
                elif component == 'device' and self.config['show_device_name']:
                    lines.append(f"Device: {value}")
                elif component == 'source' and self.config['show_source']:
                    lines.append(f"Source: {value}")
                    # Add stream count after source if enabled
                    if stream_count is not None:
                        lines.append(f"Total Streams: {stream_count}")
                elif component == 'ip' and self.config['show_ip']:
                    lines.append(f"Device IP: {value}")
                elif component == 'status':
                    lines.append(f"Status: {value}")
                elif component == 'time':
                    lines.append(f"Time: {value}")
                else:
                    # For custom or unknown components, use as-is
                    lines.append(str(value))
        
        # Join all components with line breaks
        result = "\n".join(lines)
        log(f"Final formatted message: {result}", level=LOG_VERBOSE)
        return result
    
    def format_channel_alert(self, 
                            channel_info: Dict[str, Any],
                            device_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format a channel watching alert with standard components.
        
        Creates a formatted alert for a channel watching event, including
        channel information, device details, and optional program information.
        The alert includes a title, message body, and optional image URL.
        
        Args:
            channel_info: Dictionary with channel information containing:
                - 'number': Channel number
                - 'name': Channel name
                - 'logo_url': URL to channel logo (optional)
                - 'program_title': Title of current program (optional)
                - 'stream_count': Number of active streams (optional)
            
            device_info: Dictionary with device information containing:
                - 'name': Device name
                - 'ip_address': Device IP address (optional)
                - 'source': Stream source (optional)
                - 'resolution': Stream resolution (optional)
            
        Returns:
            Dictionary with formatted title, message, and image_url
        """
        # Create title - always "Channels DVR - Watching TV"
        title = "Channels DVR - Watching TV"
            
        # Build message components
        message_parts = {
            'channel': {
                'number': channel_info.get('number', ''),
                'name': channel_info.get('name', '')
            },
            'device': device_info.get('name', 'Unknown device')
        }
        
        # Debug log to inspect the inputs
        log(f"Format alert with channel_info: {channel_info}, device_info: {device_info}", level=LOG_VERBOSE)
        
        # Add program title if available
        if 'program_title' in channel_info:
            log(f"Adding program title to message: {channel_info['program_title']}", level=LOG_VERBOSE)
            message_parts['channel']['program_title'] = channel_info['program_title']
        
        # Add stream count to channel info if available
        if 'stream_count' in channel_info:
            message_parts['channel']['stream_count'] = channel_info['stream_count']
        
        # Add optional components if available
        if device_info.get('ip_address'):
            message_parts['ip'] = device_info['ip_address']
            
        if device_info.get('source'):
            message_parts['source'] = device_info['source']
            
        if device_info.get('resolution'):
            message_parts['resolution'] = device_info['resolution']
            
        # Format the message
        message = self.format_message(message_parts)
        
        # Get image URL if available
        image_url = channel_info.get('logo_url') or channel_info.get('image_url')
        
        return {
            'title': title,
            'message': message,
            'image_url': image_url
        }
    
    def format_generic_alert(self,
                            title: str,
                            details: Dict[str, Any],
                            emoji: Optional[str] = None) -> Dict[str, Any]:
        """
        Format a generic alert with flexible components.
        
        Creates a generic alert format for alerts that don't fit the channel watching
        pattern. Can be used for system alerts, error notifications, etc.
        
        Args:
            title: The alert title
            details: Dictionary with alert details to be included in the message
                    Key-value pairs will be formatted as separate lines
            emoji: Optional emoji for the title
            
        Returns:
            Dictionary with formatted title, message, and optional image_url
        """
        # Format title
        formatted_title = self.format_title(title, emoji)
        
        # Format message from details
        message = self.format_message(details)
        
        # Extract image URL if present
        image_url = details.get('image_url')
        
        return {
            'title': formatted_title,
            'message': message,
            'image_url': image_url
        }
    
    def should_send_notification(self, session_manager, notification_key: str, cooldown_seconds: int = 3600) -> bool:
        """
        Check if a notification should be sent based on cooldown period.
        
        Prevents notification flooding by checking if a similar notification
        was sent recently. Uses the session manager to track notification history.
        
        Args:
            session_manager: SessionManager instance for history checking
            notification_key: Unique key identifying this notification (typically channel-device)
            cooldown_seconds: Minimum seconds between identical notifications (default: 1 hour)
            
        Returns:
            bool: True if notification should be sent, False if in cooldown period
        """
        if session_manager.was_notification_sent(notification_key, within_seconds=cooldown_seconds):
            log(f"Skipping notification for {notification_key} (in cooldown period)", LOG_VERBOSE)
            return False
        return True