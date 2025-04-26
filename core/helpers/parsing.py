"""
Event and data parsing helper functions for channel activity monitoring.
"""
import re
import json
import ipaddress
from typing import Dict, Any, Optional, List, Tuple

from .logging import log, LOG_VERBOSE

# IP VALIDATION
def is_valid_ip_address(text: str) -> bool:
    """Validates if a string matches IPv4 or IPv6 address format."""
    if not text:
        return False
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False

# EVENT PARSING
def parse_event_data(data: str) -> Optional[Dict[str, Any]]:
    """Converts event data string to dictionary using JSON parsing."""
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None

def is_watching_event(event_type: str, event_data: Dict[str, Any]) -> bool:
    """Determines if an event represents active channel viewing."""
    if event_type != "activities.set":
        return False
    value = event_data.get("Value", "")
    if not value:
        return False
    return "Watching ch" in value

# CHANNEL INFO
def extract_session_info(value: str) -> Tuple[Optional[str], Optional[str]]:
    """Retrieves channel number and device name from activity value."""
    channel_number = extract_channel_number(value)
    device_name = extract_device_name(value)
    return channel_number, device_name

def extract_channel_number(value: str) -> Optional[str]:
    """Extracts channel number from event value, supporting decimal formats."""
    try:
        match = re.search(r'ch(?:annel)?\s*(\d+\.\d+|\d+)', value, re.IGNORECASE)
        if match:
            channel = match.group(1)
            return channel
        return None
    except Exception as e:
        log(f"Error extracting channel number: {e}", level=LOG_VERBOSE)
        return None

def extract_channel_name(value: str) -> Optional[str]:
    """Extracts channel name from event value between channel number and 'from'."""
    try:
        match = re.search(r'ch(?:annel)?\s*(?:\d+\.\d+|\d+)\s+([^()]+?)(?:\s+from)', value, re.IGNORECASE)
        if match and match.group(1).strip():
            name = match.group(1).strip()
            return name
        return None
    except Exception as e:
        log(f"Error extracting channel name: {e}", level=LOG_VERBOSE)
        return None

def extract_resolution(value: str) -> Optional[str]:
    """Extracts video resolution information from event value."""
    try:
        match = re.search(r'(\d+[pi])', value)
        return match.group(1) if match else None
    except Exception:
        return None

# DEVICE INFO
def extract_device_name(value: str) -> Optional[str]:
    """Extracts device name from event value, excluding IP addresses."""
    try:
        match = re.search(r'from\s+([^:()]+)', value)
        if match:
            potential_name = match.group(1).strip()
            if not is_valid_ip_address(potential_name):
                return potential_name
            else:
                return None
        return None
    except Exception:
        return None

def extract_ip_address(value: str) -> Optional[str]:
    """Extracts IP address from event value, prioritizing parenthetical format."""
    try:
        match_paren = re.search(r'\(([\d\.]+)\)', value)
        if match_paren:
             potential_ip_paren = match_paren.group(1).strip()
             if is_valid_ip_address(potential_ip_paren):
                  return potential_ip_paren
        match_direct = re.search(r'from\s+([^:()]+)', value)
        if match_direct:
            potential_ip_direct = match_direct.group(1).strip()
            if is_valid_ip_address(potential_ip_direct):
                return potential_ip_direct
        return None
    except Exception:
        return None

# SESSION INFO
def extract_source_from_session_id(session_id: str) -> Optional[str]:
    """Extracts source type and details from session ID string."""
    try:
        parts = session_id.split('-')
        if len(parts) >= 3 and "stream" in parts[1]:
            source_type = parts[2]
            if source_type.startswith("M3U"):
                if len(parts) > 3:
                    source_name = parts[3]
                    return source_name
                return "M3U"
            elif source_type.startswith("TVE"):
                if len(parts) > 3:
                    provider = parts[3].split('_')[0].capitalize()
                    return f"TVE ({provider})"
                return "TVE"
            elif re.match(r'^[0-9A-F]+$', source_type, re.IGNORECASE):
                return f"Tuner ({source_type})"
            else:
                return source_type
        return "Unknown source"
    except Exception as e:
        log(f"Error extracting source from session ID: {e}", level=LOG_VERBOSE)
        return None