"""
Alert formatting and message composition for notifications.
"""
from typing import Dict, Any, List, Optional, Union

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE

class AlertFormatter:
    """
    Formats alert messages for different notification types with consistent styling.
    Supports image attachments, emoji prefixes, and structured message formatting.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the alert formatter with optional configuration.
        
        Args:
            config: Optional configuration dictionary with formatting preferences
        """
        # Default configuration
        self.config = {
            'show_ip': True,              # Show IP addresses in notifications
            'show_source': True,          # Show source information in notifications
            'use_emoji': True,            # Use emoji in notification titles
            'title_prefix': "📺 ",        # Default emoji prefix for TV notifications
            'compact_mode': False,        # Use more compact formatting
            'max_line_length': 100,       # Maximum line length before truncation
        }
        
        # Override with provided configuration
        if config:
            self.config.update(config)
    
    def format_title(self, title: str, emoji: Optional[str] = None) -> str:
        """
        Format a notification title, optionally with an emoji prefix.
        
        Args:
            title: The base title text
            emoji: Optional emoji to prepend (overrides default)
            
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
        
        Args:
            message_parts: Dictionary of message components with values
            order: Optional list specifying the order of components
            
        Returns:
            Formatted message string with line breaks
        """
        if not message_parts:
            return ""
            
        lines = []
        
        # Use specified order or default order
        if not order:
            order = [
                'channel', 'resolution', 'device', 'ip', 'source', 
                'status', 'details', 'time', 'custom'
            ]
        
        # Build message with components in order
        for component in order:
            # Skip components that should be hidden based on config
            if component == 'ip' and not self.config['show_ip']:
                continue
            if component == 'source' and not self.config['show_source']:
                continue
                
            value = message_parts.get(component)
            if value:
                # Format based on component type
                if component == 'channel' and isinstance(value, dict):
                    # Special handling for channel information
                    channel_text = f"Channel: {value.get('number', '')}"
                    if value.get('name'):
                        channel_text += f" - {value['name']}"
                    lines.append(channel_text)
                elif component == 'resolution':
                    lines.append(f"Resolution: {value}")
                elif component == 'device':
                    lines.append(f"Device: {value}")
                elif component == 'source':
                    lines.append(f"Source: {value}")
                elif component == 'ip':
                    lines.append(f"IP: {value}")
                elif component == 'status':
                    lines.append(f"Status: {value}")
                elif component == 'time':
                    lines.append(f"Time: {value}")
                else:
                    # For custom or unknown components, use as-is
                    lines.append(str(value))
        
        # Join all components with line breaks
        return "\n".join(lines)
    
    def format_channel_alert(self, 
                            channel_info: Dict[str, Any],
                            device_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format a channel watching alert with standard components.
        
        Args:
            channel_info: Dictionary with channel information
            device_info: Dictionary with device information
            
        Returns:
            Dictionary with formatted title, message, and image_url
        """
        # Create title
        device_name = device_info.get('name', 'Unknown device')
        channel_name = channel_info.get('name', '')
        channel_number = channel_info.get('number', '')
        
        if channel_name:
            title = self.format_title(f"{channel_name}")
        else:
            title = self.format_title(f"Channel {channel_number}")
            
        # Build message components
        message_parts = {
            'channel': {
                'number': channel_number,
                'name': channel_name
            },
            'device': device_name
        }
        
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
        
        # Log the message (verbose only)
        log(f"Formatted channel alert: {title} - {message}", LOG_VERBOSE)
        
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
        
        Args:
            title: The alert title
            details: Dictionary with alert details
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
        
        Args:
            session_manager: SessionManager instance for history checking
            notification_key: Unique key identifying this notification
            cooldown_seconds: Minimum seconds between identical notifications
            
        Returns:
            bool: True if notification should be sent, False if in cooldown period
        """
        if session_manager.was_notification_sent(notification_key, within_seconds=cooldown_seconds):
            log(f"Skipping notification for {notification_key} (in cooldown period)", LOG_VERBOSE)
            return False
        return True