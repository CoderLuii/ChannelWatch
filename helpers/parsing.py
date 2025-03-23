"""
Event and data parsing helper functions.
"""
import re
import json
from typing import Dict, Any, Optional, List, Tuple

from .logging import log, LOG_VERBOSE

def parse_event_data(data: str) -> Optional[Dict[str, Any]]:
    """Parse event data from string to dictionary.
    
    Args:
        data: The event data string
        
    Returns:
        dict: Parsed event data, or None if parsing failed
    """
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None

def parse_sse_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a Server-Sent Events line.
    
    Args:
        line: The SSE line to parse
        
    Returns:
        dict: Parsed event data, or None if parsing failed
    """
    try:
        if line.startswith('data:'):
            data = line[5:].strip()
            return json.loads(data)
        return None
    except Exception:
        return None

def extract_session_info(value: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract session information from an activity value.
    
    Args:
        value: The activity value string
        
    Returns:
        tuple: (channel_number, device_name) or (None, None) if not found
    """
    channel_number = extract_channel_number(value)
    device_name = extract_device_name(value)
    
    return channel_number, device_name

def extract_channel_number(value: str) -> Optional[str]:
    """Extract channel number from event value.
    
    Args:
        value: The activity value string
        
    Returns:
        str: Channel number, or None if not found
    """
    try:
        # First look for the standard format
        match = re.search(r'Watching ch(\d+)', value)
        if match:
            return match.group(1)
        
        # Alternative pattern in case channel info is formatted differently
        alt_match = re.search(r'ch(?:annel)?\s*(\d+)', value, re.IGNORECASE)
        if alt_match:
            return alt_match.group(1)
            
        return None
    except Exception as e:
        log(f"Error extracting channel number: {e}", level=LOG_VERBOSE)
        return None

def extract_channel_name(value: str) -> Optional[str]:
    """Extract channel name from event value if present.
    
    Args:
        value: The activity value string
        
    Returns:
        str: Channel name, or None if not found
    """
    try:
        # Look for name between channel number and 'from'
        match = re.search(r'Watching ch\d+\s+([^()]+?)(?:\s+from)', value)
        if match and match.group(1).strip():
            return match.group(1).strip()
        return None
    except Exception:
        return None

def extract_device_name(value: str) -> Optional[str]:
    """Extract just the device name without IP address.
    
    Args:
        value: The activity value string
        
    Returns:
        str: Device name, or None if not found
    """
    try:
        match = re.search(r'from\s+([^:()]+)', value)
        if match:
            return match.group(1).strip()
        return None
    except Exception:
        return None

def extract_ip_address(value: str) -> Optional[str]:
    """Extract IP address from event value.
    
    Args:
        value: The activity value string
        
    Returns:
        str: IP address, or None if not found
    """
    try:
        match = re.search(r'\(([\d\.]+)\)', value)
        return match.group(1) if match else None
    except Exception:
        return None

def is_watching_event(event_type: str, event_data: Dict[str, Any]) -> bool:
    """Check if an event represents someone watching a channel.
    
    Args:
        event_type: The type of the event
        event_data: The event data dictionary
        
    Returns:
        bool: True if this is a watching event, False otherwise
    """
    if event_type != "activities.set":
        return False
        
    value = event_data.get("Value", "")
    if not value:
        return False
        
    return "Watching ch" in value

def extract_resolution(value: str) -> Optional[str]:
    """Extract resolution information from event value.
    
    Args:
        value: The activity value string
        
    Returns:
        str: Resolution, or None if not found
    """
    try:
        # Look for resolution pattern (e.g., 1080i, 720p)
        match = re.search(r'(\d+[pi])', value)
        return match.group(1) if match else None
    except Exception:
        return None

def extract_source_from_session_id(session_id: str) -> Optional[str]:
    """Extract source information from session ID.
    
    Args:
        session_id: The session ID
        
    Returns:
        str: Source information, or None if not found
    """
    try:
        match = re.search(r'M3U-(\w+)', session_id)
        return match.group(1) if match else None
    except Exception:
        return None