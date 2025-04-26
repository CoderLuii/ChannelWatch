"""VOD-Watching alert implementation for monitoring DVR content viewing activity."""
import threading
import time
import os
import re
import ipaddress
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import pytz

from .base import BaseAlert
from .common.session_manager import SessionManager
from .common.alert_formatter import AlertFormatter
from .common.cleanup_mixin import CleanupMixin
from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.parsing import extract_device_name
from ..helpers.vod_info import VODInfoProvider
from ..helpers.config import CoreSettings
from ..helpers.type_utils import ensure_str
from ..helpers.activity_recorder import record_vod_watching

# GLOBALS

event_lock = threading.Lock()
IP_ADDRESS_REGEX = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')

# UTILITY FUNCTIONS

def is_valid_ip_address(text: str) -> bool:
    """Checks if a string matches the IPv4 address format using ipaddress module."""
    if not text:
        return False
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False

def format_duration(seconds: int) -> str:
    """Formats duration in a clean, human-readable way with consistent spacing."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60
    
    if hours > 0:
        return f"{hours}h {minutes:02d}m {remaining_seconds:02d}s"
    else:
        return f"{minutes}m {remaining_seconds:02d}s"

def format_progress(current: str, total: str) -> str:
    """Formats progress in a clean way."""
    try:
        def parse_time(t):
            if any(x in t for x in ['h', 'm', 's']):
                formatted = format_timestamp(t)
                hours = 0
                minutes = 0
                seconds = 0
                
                parts = formatted.split()
                for part in parts:
                    if 'h' in part:
                        hours = int(part.replace('h', ''))
                    elif 'm' in part:
                        minutes = int(part.replace('m', ''))
                    elif 's' in part:
                        seconds = int(part.replace('s', ''))
                
                return hours * 3600 + minutes * 60 + seconds
            else:
                parts = t.split(":")
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    return h * 3600 + m * 60 + s
                elif len(parts) == 2:
                    m, s = map(int, parts)
                    return m * 60 + s
                return int(parts[0])
        
        current_seconds = parse_time(current)
        total_seconds = parse_time(total)
        
        current_formatted = format_duration(current_seconds)
        total_formatted = format_duration(total_seconds)
        
        return f"Duration: {current_formatted} / {total_formatted}"
    except Exception as e:
        log(f"Progress formatting error: {e}", level=LOG_VERBOSE)
        return f"Duration: {current} / {total}"

def format_timestamp(timestamp_str: str) -> str:
    """Formats a timestamp string to ensure proper spacing between units."""
    if not timestamp_str:
        return ""
    
    if " " in timestamp_str:
        return timestamp_str
    
    hours = 0
    minutes = 0
    seconds = 0
    
    hour_match = re.search(r'(\d+)h', timestamp_str)
    if hour_match:
        hours = int(hour_match.group(1))
    
    minute_match = re.search(r'(\d+)m', timestamp_str)
    if minute_match:
        minutes = int(minute_match.group(1))
    
    second_match = re.search(r'(\d+)s', timestamp_str)
    if second_match:
        seconds = int(second_match.group(1))
    
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    elif minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    else:
        return f"{seconds}s"

# DEVICE IDENTIFICATION

def extract_clean_device_name(value: str) -> Optional[str]:
    """Extracts just the device name without timestamp or IP address."""
    if not value:
        return None

    if " from " in value:
        parts = value.split(" from ")
        if len(parts) < 2:
            return None

        device_part = parts[1].strip()

        if " at " in device_part:
            device_part = device_part.split(" at ")[0].strip()

        if device_part.startswith("(") and device_part.endswith(")"):
            device_part = device_part[1:-1].strip()
            
        is_ip = is_valid_ip_address(device_part)
        
        if is_ip:
            return None

        return device_part

    return None

def extract_ip_address(value: str) -> str:
    """Extracts the IP address from the event value."""
    if not value:
        return ""
    
    if " from " in value:
        parts = value.split(" from ")[1].split(" at ")[0].strip()
        if re.match(r'^\d+\.\d+\.\d+\.\d+$', parts):
            return parts
    
    if "(" in value and ")" in value:
        open_paren = value.rfind("(")
        close_paren = value.rfind(")")
        if open_paren != -1 and close_paren != -1 and open_paren < close_paren:
            ip_candidate = value[open_paren+1:close_paren].strip()
            if re.match(r'^\d+\.\d+\.\d+\.\d+$', ip_candidate):
                return ip_candidate
    
    ip_match = re.search(r'\d+\.\d+\.\d+\.\d+', value)
    if ip_match:
        return ip_match.group(0)
    
    return ""

# VOD WATCHING

class VODWatchingAlert(BaseAlert, CleanupMixin):
    """Monitors and alerts on VOD/DVR content watching activity."""
    
    ALERT_TYPE = "VOD-Watching"
    DESCRIPTION = "Notifications when someone is watching DVR content"
    
    def __init__(self, notification_manager, settings: CoreSettings):
        """Initializes the VOD-Watching alert with notification manager and settings."""
        BaseAlert.__init__(self, notification_manager)
        CleanupMixin.__init__(self)
        
        self.settings = settings
        self.session_manager = SessionManager()
        
        host = settings.channels_dvr_host
        port = settings.channels_dvr_port
        self.timezone = settings.tz 
        
        self.vod_provider = VODInfoProvider(ensure_str(host), port, settings)
        
        show_device_name = settings.vod_device_name
        show_device_ip = settings.vod_device_ip
        
        self.alert_formatter = AlertFormatter(config={
            'show_device_name': show_device_name,
            'show_ip': show_device_ip,
            'use_emoji': True,
            'title_prefix': "🎬 ",
        })
        
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        self.identifier_ip_cache: Dict[str, str] = {}
        
        self.alert_cooldown = settings.vod_alert_cooldown
        
        self.significant_threshold = settings.vod_significant_threshold
        
        self.configure_cleanup(
            enabled=True,
            interval=3600,
            auto_cleanup=True
        )
    
    # INITIALIZATION
    
    def _cache_vod_metadata(self):
        """Updates VOD information cache for processing viewer sessions. Returns item count."""
        try:
            metadata = self.vod_provider._fetch_metadata()
            if metadata:
                self.vod_metadata = {
                    item.get("id", ""): item 
                    for item in metadata 
                    if item.get("id")
                }
                self.vod_provider.last_fetch = time.time()

                return len(metadata)
            else:
                log("No VOD metadata found to cache", level=LOG_STANDARD)
                return 0
        except Exception as e:
            log(f"Error caching VOD metadata: {e}", level=LOG_STANDARD)
            return 0
    
    def _cache_channels(self):
        """Caches channel information at startup for faster lookups later."""
        self._cache_vod_metadata()
    
    # EVENT PROCESSING
    
    def _should_handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determines if this alert should handle the given event."""
        event_name = event_data.get("Name", "")
        
        is_file_event = (
            event_name.startswith("6-file-") or 
            event_name.startswith("7-file") or
            (event_name.startswith("7-") and "file" in event_name)
        )
        
        if not is_file_event:
            return False
            
        if event_type != "activities.set" or "Value" not in event_data:
            return False
            
        value = event_data.get("Value", "")
        
        return (not value) or (("Watching" in value or "Streaming" in value) and "at" in value)
    
    def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Handles a VOD watching event and manages session tracking."""
        with event_lock:
            try:
                value = event_data.get("Value", "")
                event_name = event_data.get("Name", "")
                
                name_parts = event_name.split("-")
                
                file_id = ""
                session_identifier = ""
                
                if len(name_parts) >= 3:
                    if name_parts[1] == "file":
                        file_id = name_parts[2]
                        session_identifier = "-".join(name_parts[3:]) if len(name_parts) > 3 else ""
                    
                    elif name_parts[1].startswith("file"):
                        file_id = name_parts[1][4:]
                        
                        if len(name_parts) > 2:
                           session_identifier = "-".join(name_parts[2:])
                
                if not file_id:
                    file_match = re.search(r'file-?(\\d+)', event_name)
                    if file_match:
                        file_id = file_match.group(1)
                
                if not session_identifier:
                     id_match = re.search(r'file\\d+-([a-zA-Z0-9\\.\\-]+)', event_name)
                     if id_match:
                         session_identifier = id_match.group(1)
                
                if not file_id or not session_identifier:
                    log(f"Could not extract file ID or session identifier from event Name: {event_name}", level=LOG_VERBOSE)
                    return False
                
                device_name_candidate = extract_clean_device_name(value)
                ip_address_from_value = extract_ip_address(value)
                
                session_key = f"vod{file_id}-{session_identifier}"
                
                log(f"Extracted file ID: {file_id}, Identifier: {session_identifier}, SessionKey: {session_key}", level=LOG_VERBOSE)
                log(f"Initial parse from Value - Device Name Candidate: {device_name_candidate}, IP Address: {ip_address_from_value}", level=LOG_VERBOSE)
                
                if value:
                    sessions_to_remove = []
                    for existing_key, existing_data in self.active_sessions.items():
                        if (existing_data.get("session_identifier") == session_identifier and 
                            existing_data.get("file_id") != file_id):
                            sessions_to_remove.append(existing_key)
                            log(f"DEBUG {session_key}: Detected switch. Marking old session '{existing_key}' for removal.", level=LOG_VERBOSE)
                    
                    for key_to_remove in sessions_to_remove:
                        if key_to_remove in self.active_sessions:
                            del self.active_sessions[key_to_remove]
                            log(f"DEBUG {session_key}: Removed old session '{key_to_remove}' due to VOD switch.", level=LOG_VERBOSE)
                 
                if not value:
                    log(f"VOD End Event: Checking for session key '{session_key}'", level=LOG_VERBOSE)
                    session_exists = session_key in self.active_sessions
                    log(f"VOD End Event: Session key '{session_key}' exists? {session_exists}", level=LOG_VERBOSE)
                    if session_exists:
                        log(f"VOD End Event: Deleting session key '{session_key}'", level=LOG_VERBOSE)
                        del self.active_sessions[session_key]
                    else:
                        log(f"VOD End Event: Session key '{session_key}' not found in active_sessions.", level=LOG_VERBOSE)
                    return False
                
                if "Streaming" in value and " at " not in value:
                    log(f"Streaming event detected for {session_key}, waiting for timestamped update.", level=LOG_VERBOSE)
                    if session_key not in self.active_sessions:
                         self.active_sessions[session_key] = {
                            "timestamp": "Streaming",
                            "last_update": time.time(),
                            "last_notification": 0,
                            "device": device_name_candidate or "Unknown",
                            "file_id": file_id,
                            "ip": ip_address_from_value or "Unknown",
                            "session_identifier": session_identifier
                        }
                    else:
                         self.active_sessions[session_key]["timestamp"] = "Streaming"
                         self.active_sessions[session_key]["last_update"] = time.time()
                    return False
                
                current_timestamp = ""
                if " at " in value:
                    current_timestamp = value.split(" at ")[-1].strip()
                else:
                    log(f"Skipping event for {session_key} due to missing timestamp in Value: {value}", level=LOG_VERBOSE)
                    return False
                
                is_new_session = session_key not in self.active_sessions
                last_notification_time = self.active_sessions.get(session_key, {}).get("last_notification", 0)
                current_time = time.time()
                
                if not is_new_session and (current_time - last_notification_time < self.alert_cooldown):
                    last_timestamp = self.active_sessions[session_key].get("timestamp", "0s")
                    log(f"Cooldown active for session {session_key}. Updating timestamp: {last_timestamp} -> {current_timestamp}", level=LOG_VERBOSE)
                    self.active_sessions[session_key].update({
                        "timestamp": current_timestamp,
                        "last_update": current_time
                    })
                    return False
                
                bypass_cooldown = False
                if not is_new_session and (current_time - last_notification_time >= self.alert_cooldown):
                     pass
                elif not is_new_session and self.significant_threshold > 0:
                    last_timestamp_str = self.active_sessions[session_key].get("timestamp", "0s")
                    try:
                        last_seconds = self._parse_timestamp_to_seconds(last_timestamp_str)
                        current_seconds = self._parse_timestamp_to_seconds(current_timestamp)
                        if abs(current_seconds - last_seconds) >= self.significant_threshold:
                            log(f"Significant progress detected for {session_key}. Bypassing cooldown.", level=LOG_VERBOSE)
                            bypass_cooldown = True
                    except ValueError:
                        log(f"Could not parse timestamps for significant progress check: {last_timestamp_str}, {current_timestamp}", level=LOG_VERBOSE)
                
                should_process_alert = is_new_session or (current_time - last_notification_time >= self.alert_cooldown) or bypass_cooldown
                
                if should_process_alert:
                    log(f"VOD Start/Update: Attempting to add session key '{session_key}'", level=LOG_VERBOSE)
                    success, determined_ip_to_store = self._process_watching_event(event_data, session_key, file_id, session_identifier)
                    
                    if success:
                        self.active_sessions[session_key] = {
                            "timestamp": current_timestamp,
                            "last_update": current_time,
                            "last_notification": current_time,
                            "device": device_name_candidate or "Unknown",
                            "file_id": file_id,
                            "ip": ip_address_from_value or "Unknown",
                            "session_identifier": session_identifier
                        }
                        log(f"VOD Start/Update: Successfully added session key '{session_key}'", level=LOG_VERBOSE)

                        if determined_ip_to_store:
                             self.identifier_ip_cache[session_identifier] = determined_ip_to_store
                             log_ip_storage = f"Stored IP {determined_ip_to_store} for identifier {session_identifier}."
                        else:
                             log_ip_storage = "No valid IP determined to store."
                             
                        log_prefix = "New" if is_new_session else "Updated"
                        log(f"{log_prefix} VOD session processed for alert: {session_key} at {current_timestamp}. {log_ip_storage}", level=LOG_VERBOSE)

                        return success
                    else:
                         if session_key in self.active_sessions:
                              self.active_sessions[session_key]['last_update'] = current_time
                         log(f"Processing alert for {session_key} returned False. No notification sent.", level=LOG_VERBOSE)
                    
                    return success
                else:
                     if not is_new_session:
                         self.active_sessions[session_key].update({
                             "timestamp": current_timestamp,
                             "last_update": current_time
                         })
                     log(f"Conditions not met to process alert for session {session_key}. Silently updating.", level=LOG_VERBOSE)
                     return False
                
            except Exception as e:
                log(f"Error processing event in _handle_event: {e}")
                import traceback
                log(traceback.format_exc(), level=LOG_VERBOSE)
                return False
    
    # NOTIFICATION MANAGEMENT
    
    def _process_watching_event(self, event_data: Dict[str, Any], session_key: str, file_id: str, session_identifier: str) -> Tuple[bool, Optional[str]]:
        """Processes a watching event and sends an alert with complete information."""
        try:
            value = event_data.get("Value", "")
            
            if not value or " at " not in value:
                 log(f"Skipping processing for {session_key}: Invalid or incomplete 'Value' field (missing ' at '): {value}", level=LOG_VERBOSE)
                 return False, None
 
            current_time_str = value.split(" at ")[-1].strip()
            
            device_name_from_val = extract_clean_device_name(value)
            ip_from_val = extract_ip_address(value)
            ip_from_id = session_identifier if is_valid_ip_address(session_identifier) else ""
            
            final_device_name = "Unknown Device"
            if self.settings.vod_device_name and device_name_from_val:
                final_device_name = device_name_from_val

            final_device_ip = "Unknown IP"
            preferred_ip = ""
            
            if ip_from_val:
                preferred_ip = ip_from_val 
            elif ip_from_id:
                preferred_ip = ip_from_id
            else:
                ip_from_cache = self.identifier_ip_cache.get(session_identifier)
                if ip_from_cache:
                    log(f"DEBUG {session_key}: No IP found in current event. Using cached IP '{ip_from_cache}' for identifier '{session_identifier}'", level=LOG_VERBOSE)
                    preferred_ip = ip_from_cache
                else:
                     log(f"DEBUG {session_key}: No IP found in current event or cache for identifier '{session_identifier}'.", level=LOG_VERBOSE)
            
            if self.settings.vod_device_ip and preferred_ip:
                final_device_ip = preferred_ip
                
            determined_ip_for_storage = final_device_ip if final_device_ip != "Unknown IP" else None

            metadata = self.vod_provider.get_metadata(file_id)
            if not metadata:
                log(f"No metadata found for file ID: {file_id} in session {session_key}", level=LOG_VERBOSE)
                return False, None

            formatted_metadata = self.vod_provider.format_metadata(metadata, current_time_str)

            if formatted_metadata.get("progress"):
                formatted_metadata["progress"] = format_timestamp(formatted_metadata["progress"])

            message_parts = []

            title_parts = []
            if formatted_metadata.get("title"):
                title_parts.append(formatted_metadata["title"])
                if metadata.get("Year"):
                     title_parts.append(f"({metadata['Year']})")
                if formatted_metadata.get("episode_title"):
                    title_parts.append(f"- {formatted_metadata['episode_title']}")
            if title_parts:
                message_parts.append(" ".join(title_parts))

            if formatted_metadata.get("duration"):
                progress = formatted_metadata.get("progress", "")
                if progress and formatted_metadata["duration"]:
                    progress_str = format_progress(progress, formatted_metadata["duration"])
                    message_parts.append(progress_str)

            if self.settings.vod_device_name:
                 message_parts.append(f"Device Name: {final_device_name}")
            
            if self.settings.vod_device_ip:
                 message_parts.append(f"Device IP: {final_device_ip}")

            if formatted_metadata.get("summary"):
                message_parts.append(f"\n{formatted_metadata['summary']}\n")

            info_sections = []

            rating_genre_parts = []
            if formatted_metadata.get("rating"):
                rating_genre_parts.append(f"Rating: {formatted_metadata['rating']}")
            if formatted_metadata.get("genres"):
                rating_genre_parts.append(f"Genres: {', '.join(formatted_metadata['genres'])}")
            if rating_genre_parts:
                info_sections.append(" · ".join(rating_genre_parts))

            if formatted_metadata.get("cast"):
                cast_list = formatted_metadata["cast"][:3]
                if len(formatted_metadata["cast"]) > 3:
                    cast_list.append("...")
                info_sections.append(f"Cast: {', '.join(cast_list)}")

            if info_sections:
                message_parts.append("\n".join(info_sections))

            message = "\n".join(part for part in message_parts if part)

            log_device_identifier = final_device_name if final_device_name != "Unknown Device" else final_device_ip if final_device_ip != "Unknown IP" else session_identifier
            log(f'Watching {formatted_metadata.get("title", "Unknown VOD")} - Device: {log_device_identifier}', level=LOG_STANDARD)

            record_vod_watching(
                content_name=formatted_metadata.get("title", "Unknown VOD"),
                device_name=final_device_name,
                device_ip=final_device_ip
            )
            
            alert_sent = self.send_alert(
                title=f"{self.alert_formatter.config.get('title_prefix', '')}Channels DVR - Watching DVR Content",
                message=message,
                image_url=formatted_metadata.get("image_url")
            )
            
            return alert_sent, determined_ip_for_storage

        except Exception as e:
            log(f"Error processing watching event for {session_key}: {e}")
            import traceback
            log(traceback.format_exc(), level=LOG_VERBOSE)
            return False, None
    
    # CLEANUP
    
    def cleanup(self):
        """Cleans up stale sessions and IP cache entries."""
        current_time = time.time()
        
        stale_sessions = []
        for key, session in self.active_sessions.items():
            if current_time - session["last_update"] > 3600:
                stale_sessions.append(key)
        
        for key in stale_sessions:
            log(f"Cleaned up stale VOD session: {key}", level=LOG_VERBOSE)
            del self.active_sessions[key]
            
        active_identifiers = set(session.get("session_identifier") for session in self.active_sessions.values())
        
        stale_cached_identifiers = []
        for identifier in self.identifier_ip_cache:
            if identifier not in active_identifiers:
                stale_cached_identifiers.append(identifier)
                
        for identifier in stale_cached_identifiers:
             if identifier in self.identifier_ip_cache:
                 del self.identifier_ip_cache[identifier]
                 log(f"Cleaned up stale IP cache entry for identifier: {identifier}", level=LOG_VERBOSE)
    
    # UTILITIES
    
    def _parse_timestamp_to_seconds(self, timestamp: str) -> int:
        """Parses a timestamp string into seconds."""
        try:
            if not timestamp:
                return 0
                
            if any(x in timestamp for x in ['h', 'm', 's']):
                formatted = format_timestamp(timestamp)
                hours = 0
                minutes = 0
                seconds = 0
                
                parts = formatted.split()
                for part in parts:
                    if 'h' in part:
                        hours = int(part.replace('h', ''))
                    elif 'm' in part:
                        minutes = int(part.replace('m', ''))
                    elif 's' in part:
                        seconds = int(part.replace('s', ''))
                
                return hours * 3600 + minutes * 60 + seconds
            else:
                parts = timestamp.split(":")
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    return h * 3600 + m * 60 + s
                elif len(parts) == 2:
                    m, s = map(int, parts)
                    return m * 60 + s
                else:
                    return int(parts[0])
        except Exception as e:
            log(f"Error parsing timestamp '{timestamp}': {e}", level=LOG_VERBOSE)
            return 0 