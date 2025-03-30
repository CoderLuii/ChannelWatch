"""
Helper functions for working with program information.
"""
import requests
import xml.etree.ElementTree as ET
import time
import threading
import pytz
from datetime import datetime
from typing import Dict, Any, Optional, List

from .logging import log, LOG_STANDARD, LOG_VERBOSE

class ProgramInfoProvider:
    """Provides program information from the Channels DVR XMLTV API."""
    
    def __init__(self, host: str, port: int, timezone: str = "America/New_York", cache_ttl: int = 3600):
        """Initialize the program info provider.
        
        Args:
            host: The Channels DVR host
            port: The Channels DVR port
            timezone: The timezone for time conversions
            cache_ttl: Cache time-to-live in seconds (default: 1 hour)
        """
        self.host = host
        self.port = port
        self.cache_ttl = cache_ttl
        self.timezone = timezone
        self.local_tz = pytz.timezone(timezone)
        
        # Cache data
        self.program_cache = {}
        self.channel_map = {}  # Maps channel numbers to channel IDs
        self.cache_timestamp = 0
        self.cache_lock = threading.Lock()
    
    def _fetch_xmltv_data(self, duration: int = 86400) -> Optional[str]:
        """Fetch XMLTV data from the API.
        
        Args:
            duration: Number of seconds of program data to fetch (default: 24 hours)
            
        Returns:
            str: XMLTV data or None if failed
        """
        try:
            # Log the duration in hours for better readability
            hours = duration // 3600
            log(f"Fetching program guide data for {hours} hours", level=LOG_VERBOSE)
            
            url = f"http://{self.host}:{self.port}/devices/ANY/guide/xmltv"
            response = requests.get(url, timeout=30)  # Increased timeout for larger data fetch
            
            if response.status_code == 200:
                return response.text
            else:
                log(f"Failed to fetch XMLTV data: HTTP {response.status_code}", level=LOG_STANDARD)
                return None
        except Exception as e:
            log(f"Error fetching XMLTV data: {e}", level=LOG_STANDARD)
            return None
    
    def _parse_xmltv_data(self, xml_data: str) -> bool:
        """Parse XMLTV data and update cache.
        
        Args:
            xml_data: The XML data string
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            root = ET.fromstring(xml_data)
            
            # Process channel information
            channels_count = 0
            for channel in root.findall("./channel"):
                channel_id = channel.get("id")
                lcn = channel.find("lcn")
                
                if lcn is not None and lcn.text:
                    self.channel_map[lcn.text] = channel_id
                    channels_count += 1
            
            # Process program information
            programs_count = 0
            time_conversions = 0
            
            for program in root.findall("./programme"):
                channel_id = program.get("channel")
                start_time = self._parse_xmltv_time(program.get("start"))
                if start_time: time_conversions += 1
                
                stop_time = self._parse_xmltv_time(program.get("stop"))
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
                
                # Store in cache by channel and time range
                if channel_id not in self.program_cache:
                    self.program_cache[channel_id] = []
                
                self.program_cache[channel_id].append(program_info)
                programs_count += 1
            
            # Log summary information instead of individual time conversions
            if time_conversions > 0:
                log(f"Processed {time_conversions} time conversions for {programs_count} programs", level=LOG_VERBOSE)
            
            return True
        except Exception as e:
            log(f"Error parsing XMLTV data: {e}", level=LOG_STANDARD)
            return False
    
    def _parse_xmltv_time(self, time_str: str) -> Optional[int]:
        """Parse XMLTV time format to Unix timestamp.
        
        Args:
            time_str: XMLTV time string (e.g., "20250327090000 +0000")
            
        Returns:
            int: Unix timestamp or None if parsing failed
        """
        try:
            # No need to log each time string parsing
            # Only log counts or errors
            
            # Split time and timezone
            parts = time_str.split(" ")
            time_part = parts[0]
            tz_part = parts[1] if len(parts) > 1 else "+0000"
            
            # Parse the datetime (in UTC)
            dt_utc = datetime.strptime(time_part, "%Y%m%d%H%M%S")
            
            # Add timezone info to make it aware
            from pytz import UTC
            dt_utc = UTC.localize(dt_utc)
            
            # Convert to user's timezone
            dt_local = dt_utc.astimezone(self.local_tz)
            
            timestamp = int(dt_local.timestamp())
            # Remove per-conversion logging
            
            return timestamp
        except Exception as e:
            log(f"Error parsing XMLTV time: {e}", level=LOG_VERBOSE)
            return None
    
    def cache_program_data(self) -> bool:
        """Cache program data from the XMLTV API.
        
        Returns:
            bool: True if successful, False otherwise
        """
        with self.cache_lock:
            # Check if cache is still valid
            current_time = time.time()
            if self.program_cache and (current_time - self.cache_timestamp) < self.cache_ttl:
                return True
            
            # Log the start of caching process
            start_time = time.time()
            log(f"Pre-caching program information from /devices/ANY/guide/xmltv", level=LOG_STANDARD)
            
            # Fetch and parse XMLTV data
            xml_data = self._fetch_xmltv_data()
            if not xml_data:
                return False
            
            # Clear existing cache
            self.program_cache = {}
            self.channel_map = {}
            
            # Parse data
            success = self._parse_xmltv_data(xml_data)
            
            if success:
                self.cache_timestamp = current_time
                
                # Calculate total programs and data fetched
                program_count = sum(len(programs) for programs in self.program_cache.values())
                end_time = time.time()
                processing_time = end_time - start_time
                
                log(f"Cached Program Information for {len(self.channel_map)} channels ({program_count} programs) in {processing_time:.1f} seconds", 
                    level=LOG_STANDARD)
            
            return success
    
    def get_current_program(self, channel_number: str, timestamp: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get the current program for a channel at a specific time.
        
        Args:
            channel_number: The channel number
            timestamp: Optional timestamp (defaults to current time)
            
        Returns:
            dict: Program information or None if not found
        """
        # Debug log the input
        log(f"Looking up program for channel {channel_number}", level=LOG_VERBOSE)
        
        # Ensure cache is up to date
        if not self.cache_program_data():
            log(f"Failed to cache program data", level=LOG_VERBOSE)
            return None
        
        # Use current time if no timestamp provided
        if timestamp is None:
            timestamp = int(time.time())
        
        # Format readable current time for debugging
        current_time_str = datetime.fromtimestamp(timestamp, self.local_tz).strftime('%Y-%m-%d %H:%M:%S %Z')
        log(f"Current time for program lookup: {current_time_str} (timestamp: {timestamp})", level=LOG_VERBOSE)
        
        # Get channel ID from channel number
        channel_id = self.channel_map.get(channel_number)
        if not channel_id:
            log(f"Channel ID not found for channel number: {channel_number}", level=LOG_VERBOSE)
            return None
        
        log(f"Found channel ID {channel_id} for channel number {channel_number}", level=LOG_VERBOSE)
        
        # Find program that matches the time range
        programs = self.program_cache.get(channel_id, [])
        log(f"Found {len(programs)} programs for channel {channel_number}", level=LOG_VERBOSE)
        
        for program in programs:
            start_time = program["start_time"]
            stop_time = program["stop_time"]
            
            # Format readable times for debugging
            start_time_str = datetime.fromtimestamp(start_time, self.local_tz).strftime('%Y-%m-%d %H:%M:%S %Z')
            stop_time_str = datetime.fromtimestamp(stop_time, self.local_tz).strftime('%Y-%m-%d %H:%M:%S %Z')
            
            log(f"Checking program: {program['title']} ({start_time_str} to {stop_time_str})", level=LOG_VERBOSE)
            
            if start_time <= timestamp < stop_time:
                # Return the program without logging it at standard level
                return program
        
        log(f"No matching program found for channel {channel_number}", level=LOG_VERBOSE)
        return None 