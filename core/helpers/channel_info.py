"""Channel information provider for retrieving and caching Channels DVR channel data."""
from typing import Dict, Any, Optional
import requests
import json
import time
from threading import Lock

from .logging import log, LOG_STANDARD, LOG_VERBOSE
# Import channel data extraction utilities
from .parsing import (
    extract_channel_number,
    extract_channel_name,
    extract_device_name,
    extract_ip_address,
    extract_resolution,
    extract_source_from_session_id
)

# CHANNEL INFO
class ChannelInfoProvider:
    """Service for retrieving and caching channel information from Channels DVR API."""
    
    def __init__(self, host, port, cache_ttl=3600):
        """Initialize channel information provider with connection details and cache settings."""
        self.host = host
        self.port = port
        self.cache_ttl = cache_ttl
        self.channel_cache = {}
        self.channel_cache_timestamp = 0
        self.cache_lock = Lock()
    
    # DATA RETRIEVAL
    def get_channel_info(self, channel_number: str) -> Optional[Dict[str, Any]]:
        """Retrieve complete information for a specific channel number."""
        channel_number_str = str(channel_number)
        if channel_number_str in self.channel_cache:
            return self.channel_cache[channel_number_str]
        
        self.cache_channels()
        return self.channel_cache.get(channel_number_str)
    
    def get_channel_name(self, channel_number: str) -> Optional[str]:
        """Retrieve display name for a specific channel number."""
        channel_info = self.get_channel_info(channel_number)
        if not channel_info:
            return None
            
        return channel_info.get("name", "Unknown Channel")
    
    def get_channel_logo_url(self, channel_number: str) -> Optional[str]:
        """Retrieve logo URL for a specific channel number."""
        channel_info = self.get_channel_info(channel_number)
        if not channel_info:
            return None
            
        return channel_info.get("logo_url", "")
    
    # CACHE MANAGEMENT
    def cache_channels(self) -> int:
        """Updates channel cache from Channels DVR API and returns channel count."""
        with self.cache_lock:
            current_time = time.time()
            if self.channel_cache and (current_time - self.channel_cache_timestamp) < self.cache_ttl:
                return len(self.channel_cache)
            
            response = requests.get(f"http://{self.host}:{self.port}/api/v1/channels", timeout=10)
            if response.status_code == 200:
                channels_data = response.json()
                
                processed_channels = {}
                for channel in channels_data:
                    number = channel.get('number')
                    name = channel.get('name')
                    logo_url = channel.get('logo_url')
                    
                    if number:
                        channel_number_str = str(number)
                        processed_channels[channel_number_str] = {
                            'name': name or "Unknown Channel",
                            'logo_url': logo_url or "",
                            'raw_data': channel
                        }
                
                self.channel_cache = processed_channels
                self.channel_cache_timestamp = current_time

                return len(self.channel_cache)
            else:
                log(f"Failed to fetch channels: HTTP {response.status_code}", level=LOG_STANDARD)
                return 0