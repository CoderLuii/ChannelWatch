"""
Event and data parsing helper functions.
"""
import re
import json
import ipaddress
from typing import Dict, Any, Optional, List, Tuple

from .logging import log, LOG_VERBOSE

def is_valid_ip_address(text: str) -> bool:
    """Check if a string matches the IPv4 or IPv6 address format using ipaddress module."""
    if not text:
        return False
    try:
        ipaddress.ip_address(text) # Try to parse as an IP address
        return True
    except ValueError:
        # Not a valid IP address (covers IPv4 and IPv6)
        return False

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
        str: Channel number (can include decimals for virtual channels), or None if not found
    """
    try:
        # Debug log the value we're trying to parse
        log(f"Parsing channel number from: {value}", level=LOG_VERBOSE)
        
        # First look for the standard format with possible decimal point
        match = re.search(r'ch(?:annel)?\s*(\d+\.\d+|\d+)', value, re.IGNORECASE)
        if match:
            channel = match.group(1)
            log(f"Extracted channel number: {channel}", level=LOG_VERBOSE)
            return channel
            
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
        # Debug log the value we're trying to parse
        log(f"Parsing channel name from: {value}", level=LOG_VERBOSE)
        
        # Look for name between channel number and 'from'
        # Updated pattern to handle decimal channel numbers
        match = re.search(r'ch(?:annel)?\s*(?:\d+\.\d+|\d+)\s+([^()]+?)(?:\s+from)', value, re.IGNORECASE)
        if match and match.group(1).strip():
            name = match.group(1).strip()
            log(f"Extracted channel name: {name}", level=LOG_VERBOSE)
            return name
        return None
    except Exception as e:
        log(f"Error extracting channel name: {e}", level=LOG_VERBOSE)
        return None

def extract_device_name(value: str) -> Optional[str]:
    """Extract just the device name, ensuring it's not an IP address.
    
    Args:
        value: The activity value string
        
    Returns:
        str: Device name, or None if not found
    """
    try:
        # Regex looks for text after 'from ' that doesn't contain ':' or '('
        # It might capture an IP address if no parentheses are present.
        match = re.search(r'from\s+([^:()]+)', value)
        if match:
            potential_name = match.group(1).strip()
            # Check if the extracted part is actually an IP address
            if not is_valid_ip_address(potential_name):
                return potential_name # It's not an IP, so return it as the name
            else:
                return None # It is an IP, return None
        return None
    except Exception:
        return None

def extract_ip_address(value: str) -> Optional[str]:
    """Extract IP address from event value, checking parentheses first, then directly after 'from '."""
    try:
        # Priority 1: Check for IP inside parentheses: DeviceName (IP.Address)
        match_paren = re.search(r'\(([\d\.]+)\)', value)
        if match_paren:
             potential_ip_paren = match_paren.group(1).strip()
             if is_valid_ip_address(potential_ip_paren):
                  return potential_ip_paren
             
        # Priority 2: Check for IP directly after 'from ': ... from IP.Address
        # Extract the same part that extract_device_name looks at
        match_direct = re.search(r'from\s+([^:()]+)', value)
        if match_direct:
            potential_ip_direct = match_direct.group(1).strip()
            if is_valid_ip_address(potential_ip_direct):
                return potential_ip_direct # Found valid IP directly after 'from'
                
        # No valid IP found in either pattern
        return None
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
        # Debug log the session ID we're parsing
        log(f"Extracting source from session ID: {session_id}", level=LOG_VERBOSE)
        
        # Check for standard format: 6-stream-<SOURCE_TYPE>-...
        parts = session_id.split('-')
        
        # Ensure we have enough parts and it starts with "stream"
        if len(parts) >= 3 and "stream" in parts[1]:
            source_type = parts[2]
            
            # Case 1: M3U sources (e.g., M3U-PrimaryKEMO)
            if source_type.startswith("M3U"):
                if len(parts) > 3:
                    source_name = parts[3]
                    log(f"Extracted M3U source: {source_name}", level=LOG_VERBOSE)
                    return source_name
                return "M3U"
            
            # Case 2: TVE sources (TV Everywhere, e.g., TVE-frontier)
            elif source_type.startswith("TVE"):
                if len(parts) > 3:
                    # Extract just the provider name
                    provider = parts[3].split('_')[0].capitalize()
                    log(f"Extracted TVE source: {provider}", level=LOG_VERBOSE)
                    return f"TVE ({provider})"
                return "TVE"
            
            # Case 3: Tuner ID (e.g., 10B196A5)
            elif re.match(r'^[0-9A-F]+$', source_type, re.IGNORECASE):
                log(f"Extracted tuner source: {source_type}", level=LOG_VERBOSE)
                return f"Tuner ({source_type})"
            
            # Case 4: Any other source type
            else:
                log(f"Extracted other source: {source_type}", level=LOG_VERBOSE)
                return source_type
        
        # For any unrecognized format
        log(f"Unknown session ID format: {session_id}", level=LOG_VERBOSE)
        return "Unknown source"
    except Exception as e:
        log(f"Error extracting source from session ID: {e}", level=LOG_VERBOSE)
        return None