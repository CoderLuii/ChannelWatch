"""
VOD metadata provider for Channels DVR content management.
"""
import os
import time
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime

from .logging import log, LOG_VERBOSE, LOG_STANDARD
from .config import CoreSettings
from requests.exceptions import Timeout, RequestException

# VOD INFO
class VODInfoProvider:
    """Manages VOD content metadata retrieval and caching from Channels DVR."""
    
    def __init__(self, host: str, port: int, settings: CoreSettings):
        """Initializes VOD provider with server connection and configuration settings."""
        self.host = host
        self.port = port
        self.settings = settings
        self.cache_ttl = settings.vod_cache_ttl
        self.base_url = f"http://{host}:{port}"
        self.metadata_url = f"{self.base_url}/api/v1/all"
        
        self.metadata_cache: Dict[str, Dict[str, Any]] = {}
        self.last_fetch: float = 0
    
    # METADATA MANAGEMENT
    def _fetch_metadata(self) -> List[Dict[str, Any]]:
        """Retrieves VOD metadata from Channels DVR API endpoint."""
        try:
            response = requests.get(self.metadata_url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"Error fetching VOD metadata: {e}")
            return []
    
    def _update_cache(self):
        """Updates local metadata cache if cache TTL has expired."""
        current_time = time.time()
        
        if current_time - self.last_fetch >= self.cache_ttl:
            log("Updating VOD metadata cache", level=LOG_VERBOSE)
            metadata = self._fetch_metadata()
            
            self.metadata_cache = {
                str(item["id"]): item for item in metadata
            }
            self.last_fetch = current_time
    
    def get_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves metadata for specified VOD file ID from cache, with fallback to update cache and search list."""
        self._update_cache()
        file_id_str = str(file_id)

        metadata = self.metadata_cache.get(file_id_str)
        if metadata:
            log(f"VOD Cache hit for file ID: {file_id_str}", level=LOG_VERBOSE)
            return metadata

        all_metadata = self._fetch_metadata()
        for item in all_metadata:
            if str(item.get("id")) == file_id_str:
                log(f"Found VOD {file_id_str} in metadata list. Updating cache.", level=LOG_VERBOSE)
                self.metadata_cache[file_id_str] = item
                return item

        log(f"No VOD metadata found for file ID: {file_id_str}", level=LOG_VERBOSE)
        return None
    
    # METADATA FORMATTING
    def format_metadata(self, metadata: Dict[str, Any], current_time: Optional[str] = None) -> Dict[str, Any]:
        """Formats VOD metadata according to configuration settings."""
        formatted = {}
        
        settings = self.settings
        
        if settings.vod_title:
            formatted["title"] = metadata.get("title", "")
            
        if settings.vod_episode_title and "episode_title" in metadata:
            formatted["episode_title"] = metadata.get("episode_title", "")
            
        if settings.vod_summary:
            formatted["summary"] = metadata.get("summary", "")
            
        if settings.vod_duration:
            duration = metadata.get("duration", 0)
            formatted["duration"] = self._format_duration(duration)
            
        if settings.vod_progress and current_time:
            formatted["progress"] = current_time
            
        if settings.vod_image:
            formatted["image_url"] = metadata.get("image_url", "")
            
        if settings.vod_rating:
            formatted["rating"] = metadata.get("content_rating", "")
            
        if settings.vod_genres:
            formatted["genres"] = metadata.get("genres", [])
            
        if settings.vod_cast and "cast" in metadata:
            formatted["cast"] = metadata.get("cast", [])
            
        return formatted
    
    def _format_duration(self, duration_seconds: float) -> str:
        """Converts duration in seconds to human-readable time format."""
        hours = int(duration_seconds // 3600)
        minutes = int((duration_seconds % 3600) // 60)
        seconds = int(duration_seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes:02d}m {seconds:02d}s"
        elif minutes > 0:
            return f"{minutes}m {seconds:02d}s"
        else:
            return f"{seconds}s" 