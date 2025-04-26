"""
Program information provider for Channels DVR XMLTV API integration.
"""
import requests
import xml.etree.ElementTree as ET
import time
import threading
import pytz
from datetime import datetime
from typing import Dict, Any, Optional, List, Union, cast

from .logging import log, LOG_STANDARD, LOG_VERBOSE
from .type_utils import ensure_str

# PROGRAM INFO
class ProgramInfoProvider:
    """Manages program guide data retrieval and caching from Channels DVR XMLTV API."""
    
    def __init__(self, host: str, port: int, timezone: str = "America/Los_Angeles", cache_ttl: int = 3600):
        """Initializes program info provider with server connection and caching parameters."""
        self.host = host
        self.port = port
        self.cache_ttl = cache_ttl
        self.timezone = timezone
        self.local_tz = pytz.timezone(timezone)
        
        self.program_cache = {}
        self.channel_map = {}
        self.cache_timestamp = 0
        self.cache_lock = threading.Lock()
    
    # DATA FETCHING
    def _fetch_xmltv_data(self, duration: int = 86400) -> Optional[str]:
        """Retrieves XMLTV program guide data from Channels DVR API."""
        try:
            url = f"http://{self.host}:{self.port}/devices/ANY/guide/xmltv"
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                return response.text
            else:
                log(f"Failed to fetch XMLTV data: HTTP {response.status_code}", level=LOG_STANDARD)
                return None
        except Exception as e:
            log(f"Error fetching XMLTV data: {e}", level=LOG_STANDARD)
            return None
    
    def _parse_xmltv_data(self, xml_data: str) -> bool:
        """Processes XMLTV data and updates program and channel caches."""
        try:
            root = ET.fromstring(xml_data)
            
            channels_count = 0
            for channel in root.findall("./channel"):
                channel_id = channel.get("id")
                lcn = channel.find("lcn")
                
                if lcn is not None and lcn.text:
                    self.channel_map[lcn.text] = channel_id
                    channels_count += 1
            
            programs_count = 0
            time_conversions = 0
            
            for program in root.findall("./programme"):
                channel_id = program.get("channel")
                start_attr = program.get("start")
                start_time = self._parse_xmltv_time(ensure_str(start_attr)) if start_attr else None
                if start_time: time_conversions += 1
                
                stop_attr = program.get("stop")
                stop_time = self._parse_xmltv_time(ensure_str(stop_attr)) if stop_attr else None
                if stop_time: time_conversions += 1
                
                if not channel_id or not start_time or not stop_time:
                    continue
                
                title_elem = program.find("title")
                desc_elem = program.find("desc")
                icon_elem = program.find("icon")
                
                program_info = {
                    "channel_id": channel_id,
                    "start_time": start_time,
                    "stop_time": stop_time,
                    "title": title_elem.text if title_elem is not None else "Unknown Program",
                    "description": desc_elem.text if desc_elem is not None else "",
                    "icon_url": icon_elem.get("src") if icon_elem is not None else None
                }
                
                if channel_id not in self.program_cache:
                    self.program_cache[channel_id] = []
                
                self.program_cache[channel_id].append(program_info)
                programs_count += 1
            
            if time_conversions > 0:
                pass 
            
            return True
        except Exception as e:
            log(f"Error parsing XMLTV data: {e}", level=LOG_STANDARD)
            return False
    
    def _parse_xmltv_time(self, time_str: str) -> Optional[int]:
        """Converts XMLTV time format to Unix timestamp in local timezone."""
        try:
            parts = time_str.split(" ")
            time_part = parts[0]
            tz_part = parts[1] if len(parts) > 1 else "+0000"
            
            dt_utc = datetime.strptime(time_part, "%Y%m%d%H%M%S")
            from pytz import UTC
            dt_utc = UTC.localize(dt_utc)
            dt_local = dt_utc.astimezone(self.local_tz)
            
            return int(dt_local.timestamp())
        except Exception as e:
            log(f"Error parsing XMLTV time: {e}", level=LOG_VERBOSE)
            return None
    
    # CACHE MANAGEMENT
    def cache_program_data(self) -> int:
        """Updates program guide cache with fresh data from XMLTV API. Returns program count."""
        with self.cache_lock:
            current_time = time.time()
            if self.program_cache and (current_time - self.cache_timestamp) < self.cache_ttl:
                return sum(len(programs) for programs in self.program_cache.values())
            
            xml_data = self._fetch_xmltv_data()
            if not xml_data:
                return 0
            
            self.program_cache = {}
            self.channel_map = {}
            
            success = self._parse_xmltv_data(xml_data)
            
            if success:
                self.cache_timestamp = current_time
                program_count = sum(len(programs) for programs in self.program_cache.values())

                return program_count
            
            return 0
    
    # PROGRAM LOOKUP
    def get_current_program(self, channel_number: str, timestamp: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Retrieves current program information for specified channel and time."""
        
        if not self.cache_program_data():
            log(f"Failed to cache program data", level=LOG_VERBOSE)
            return None
        
        if timestamp is None:
            timestamp = int(time.time())

        
        channel_id = self.channel_map.get(channel_number)
        if not channel_id:
            log(f"Channel ID not found for channel number: {channel_number}", level=LOG_VERBOSE)
            return None

        
        programs = self.program_cache.get(channel_id, [])

        
        for program in programs:
            start_time = program["start_time"]
            stop_time = program["stop_time"]
            
            if start_time <= timestamp < stop_time:
                return program
        
        return None 
