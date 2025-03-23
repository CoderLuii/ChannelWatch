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

# Only keep the ChannelInfoProvider class here
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
        self.channel_info_cache = {}
        self.channel_cache_timestamp = 0
    
    def get_channel_info(self, channel_number: str) -> Optional[Dict[str, Any]]:
        """Get information for a specific channel.
        
        Args:
            channel_number: The channel number
            
        Returns:
            dict: Channel information, or None if not found
        """
        # Get channel map from cache or fetch from API
        channel_map = self._fetch_channel_info()
        
        # Return channel info if found
        return channel_map.get(channel_number)
    
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
            
        # Try different fields for the name
        if channel_info.get("name"):
            return channel_info["name"]
        elif channel_info.get("callSign"):
            return channel_info["callSign"]
        elif channel_info.get("id"):
            return channel_info["id"]
            
        return None
    
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
            
        # Return logo_url if present
        return channel_info.get("logo_url")
    
    def _fetch_channel_info(self) -> Dict[str, Dict[str, Any]]:
        """Fetch channel information from Channels DVR API.
        
        Returns:
            dict: Dictionary mapping channel numbers to channel data
        """
        try:
            # Return cache if valid
            current_time = time.time()
            if self.channel_info_cache and (current_time - self.channel_cache_timestamp) < self.cache_ttl:
                return self.channel_info_cache
            
            # Fetch fresh data
            url = f"http://{self.host}:{self.port}/api/v1/channels"
            log(f"Fetching channel information from {url}")
            
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                log(f"Failed to fetch channel info: HTTP {response.status_code}")
                return self.channel_info_cache or {}
            
            channels = response.json()
            log(f"Retrieved {len(channels)} channels from API")
            
            # Build channel number -> channel data mapping
            channel_map = {}
            for channel in channels:
                number = str(channel.get("number", ""))
                name = channel.get("name", "")
                if number:
                    # Store complete channel data for richer notifications
                    channel_map[number] = channel
                    log(f"Cached channel {number}: {name}", level=LOG_VERBOSE)
            
            # Update cache
            self.channel_info_cache = channel_map
            self.channel_cache_timestamp = current_time
            
            return channel_map
        except Exception as e:
            log(f"Error fetching channel info: {e}")
            return self.channel_info_cache or {}