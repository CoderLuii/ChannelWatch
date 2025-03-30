"""
VOD information provider for fetching and caching VOD metadata.
"""
import os
import time
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime

from .logging import log, LOG_VERBOSE

class VODInfoProvider:
    """Provider for VOD/DVR content metadata."""
    
    def __init__(self, host: str, port: int, cache_ttl: int = 86400):
        """
        Initialize the VOD info provider.
        
        Args:
            host: The Channels DVR host
            port: The Channels DVR port
            cache_ttl: Cache time-to-live in seconds (default: 24 hours)
        """
        self.host = host
        self.port = port
        self.cache_ttl = cache_ttl
        self.base_url = f"http://{host}:{port}"
        self.metadata_url = f"{self.base_url}/api/v1/all"
        
        # Cache storage
        self.metadata_cache: Dict[str, Dict[str, Any]] = {}
        self.last_fetch: float = 0
        
        # Load configuration from environment
        self._load_config()
    
    def _load_config(self):
        """Load configuration from environment variables."""
        # Default to showing all fields
        self.show_title = os.getenv("VOD_TITLE", "TRUE").upper() == "TRUE"
        self.show_episode_title = os.getenv("VOD_EPISODE_TITLE", "TRUE").upper() == "TRUE"
        self.show_summary = os.getenv("VOD_SUMMARY", "TRUE").upper() == "TRUE"
        self.show_duration = os.getenv("VOD_DURATION", "TRUE").upper() == "TRUE"
        self.show_progress = os.getenv("VOD_PROGRESS", "TRUE").upper() == "TRUE"
        self.show_image = os.getenv("VOD_IMAGE", "TRUE").upper() == "TRUE"
        self.show_rating = os.getenv("VOD_RATING", "TRUE").upper() == "TRUE"
        self.show_genres = os.getenv("VOD_GENRES", "TRUE").upper() == "TRUE"
        self.show_cast = os.getenv("VOD_CAST", "TRUE").upper() == "TRUE"
        
        # Cache configuration
        self.cache_ttl = int(os.getenv("VOD_CACHE_TTL", str(self.cache_ttl)))
    
    def _fetch_metadata(self) -> List[Dict[str, Any]]:
        """
        Fetch metadata from the Channels DVR API.
        
        Returns:
            List of metadata dictionaries
        """
        try:
            response = requests.get(self.metadata_url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log(f"Error fetching VOD metadata: {e}")
            return []
    
    def _update_cache(self):
        """Update the metadata cache if needed."""
        current_time = time.time()
        
        # Check if cache needs updating
        if current_time - self.last_fetch >= self.cache_ttl:
            log("Updating VOD metadata cache", level=LOG_VERBOSE)
            metadata = self._fetch_metadata()
            
            # Update cache with new data
            self.metadata_cache = {
                str(item["id"]): item for item in metadata
            }
            self.last_fetch = current_time
    
    def get_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a specific file ID.
        
        Args:
            file_id: The file ID to get metadata for
            
        Returns:
            Dictionary containing metadata or None if not found
        """
        self._update_cache()
        return self.metadata_cache.get(str(file_id))
    
    def format_metadata(self, metadata: Dict[str, Any], current_time: Optional[str] = None) -> Dict[str, Any]:
        """
        Format metadata based on configuration settings.
        
        Args:
            metadata: The raw metadata dictionary
            current_time: Current playback time (e.g., "1h15m42s")
            
        Returns:
            Dictionary containing formatted metadata
        """
        formatted = {}
        
        if self.show_title:
            formatted["title"] = metadata.get("title", "")
            
        if self.show_episode_title and "episode_title" in metadata:
            formatted["episode_title"] = metadata.get("episode_title", "")
            
        if self.show_summary:
            formatted["summary"] = metadata.get("summary", "")
            
        if self.show_duration:
            duration = metadata.get("duration", 0)
            formatted["duration"] = self._format_duration(duration)
            
        if self.show_progress and current_time:
            formatted["progress"] = current_time
            
        if self.show_image:
            formatted["image_url"] = metadata.get("image_url", "")
            
        if self.show_rating:
            formatted["rating"] = metadata.get("content_rating", "")
            
        if self.show_genres:
            formatted["genres"] = metadata.get("genres", [])
            
        if self.show_cast and "cast" in metadata:
            formatted["cast"] = metadata.get("cast", [])
            
        return formatted
    
    def _format_duration(self, duration_seconds: float) -> str:
        """Format duration in seconds to human-readable string."""
        hours = int(duration_seconds // 3600)
        minutes = int((duration_seconds % 3600) // 60)
        seconds = int(duration_seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes:02d}m {seconds:02d}s"
        elif minutes > 0:
            return f"{minutes}m {seconds:02d}s"
        else:
            return f"{seconds}s" 