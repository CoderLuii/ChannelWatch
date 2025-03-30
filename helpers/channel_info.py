"""
Helper functions for working with channel information.
"""
from typing import Dict, Any, Optional
import requests
import json
import time
from threading import Lock

from .logging import log, LOG_STANDARD, LOG_VERBOSE
# Import the extraction functions from parsing.py
from .parsing import (
    extract_channel_number,
    extract_channel_name,
    extract_device_name,
    extract_ip_address,
    extract_resolution,
    extract_source_from_session_id
)

class ChannelInfoProvider:
    """Provides information about channels from the Channels DVR API."""
    
    def __init__(self, host, port, cache_ttl=3600):
        """Initialize the channel info provider.
        
        Args:
            host: The Channels DVR host
            port: The Channels DVR port
            cache_ttl: Cache time-to-live in seconds (default: 1 hour)
        """
        self.host = host
        self.port = port
        self.cache_ttl = cache_ttl
        self.channel_cache = {}  # Using the same variable name as in channel_watching.py
        self.channel_cache_timestamp = 0
    
    def get_channel_info(self, channel_number: str) -> Optional[Dict[str, Any]]:
        """Get information for a specific channel.
        
        Args:
            channel_number: The channel number
            
        Returns:
            dict: Channel information, or None if not found
        """
        # First check the cache
        channel_number_str = str(channel_number)
        if channel_number_str in self.channel_cache:
            return self.channel_cache[channel_number_str]
        
        # If not in cache, force a refresh and try again
        self.cache_channels()
        return self.channel_cache.get(channel_number_str)
    
    def get_channel_name(self, channel_number: str) -> Optional[str]:
        """Get the name of a channel.
        
        Args:
            channel_number: The channel number
            
        Returns:
            str: Channel name, or None if not found
        """
        channel_info = self.get_channel_info(channel_number)
        if not channel_info:
            return None
            
        return channel_info.get("name", "Unknown Channel")
    
    def get_channel_logo_url(self, channel_number: str) -> Optional[str]:
        """Get the logo URL for a channel.
        
        Args:
            channel_number: The channel number
            
        Returns:
            str: Channel logo URL, or None if not found
        """
        channel_info = self.get_channel_info(channel_number)
        if not channel_info:
            return None
            
        return channel_info.get("logo_url", "")
    
    def cache_channels(self):
        """Cache channel information at startup for faster lookups later.
        Same implementation as _cache_channels in channel_watching.py."""
        try:
            # Check if cache is still valid
            current_time = time.time()
            if self.channel_cache and (current_time - self.channel_cache_timestamp) < self.cache_ttl:
                return self.channel_cache
            
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
                
                # Update timestamp
                self.channel_cache_timestamp = current_time
                
                # Just one log message with the count
                log(f"Cached Channel Information for {len(self.channel_cache)} channels", level=LOG_STANDARD)
            else:
                log(f"Failed to fetch channels: HTTP {response.status_code}", level=LOG_STANDARD)
                
            return self.channel_cache
        except Exception as e:
            log(f"Error caching channel information: {e}", level=LOG_STANDARD)
            return self.channel_cache