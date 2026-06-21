"""
VOD metadata provider for Channels DVR content management.
"""

import time
import httpx
from typing import Dict, Any, Optional, List

from .logging import log, LOG_VERBOSE
from .config import CoreSettings
from .dvr_connection import build_dvr_base_url


def _metadata_value(metadata: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return value
    return default


def _metadata_id(metadata: Dict[str, Any]) -> Optional[str]:
    value = _metadata_value(metadata, "id", "ID", default=None)
    return str(value) if value not in (None, "") else None


# VOD INFO
class VODInfoProvider:
    """Manages VOD content metadata retrieval and caching from Channels DVR."""

    def __init__(
        self,
        host: str = "",
        port: int = 8089,
        settings: Optional[CoreSettings] = None,
        dvr=None,
    ):
        """Initializes VOD provider with server connection and configuration settings."""
        if dvr is not None:
            self.host = dvr.host
            self.port = dvr.port
            self.base_url = dvr.base_url
        else:
            self.host = host
            self.port = port
            self.base_url = build_dvr_base_url(host, port)
        self.settings = settings
        self.cache_ttl = settings.vod_cache_ttl if settings else 86400
        self.metadata_url = f"{self.base_url}/api/v1/all"

        self.metadata_cache: Dict[str, Dict[str, Any]] = {}
        self.last_fetch: float = 0

    # METADATA MANAGEMENT
    def _fetch_metadata(self) -> List[Dict[str, Any]]:
        """Retrieves VOD metadata from Channels DVR API endpoint."""
        try:
            response = httpx.get(self.metadata_url, timeout=10)
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
                item_id: item
                for item in metadata
                if (item_id := _metadata_id(item)) is not None
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
            if _metadata_id(item) == file_id_str:
                log(
                    f"Found VOD {file_id_str} in metadata list. Updating cache.",
                    level=LOG_VERBOSE,
                )
                self.metadata_cache[file_id_str] = item
                return item

        log(f"No VOD metadata found for file ID: {file_id_str}", level=LOG_VERBOSE)
        return None

    # METADATA FORMATTING
    def format_metadata(
        self, metadata: Dict[str, Any], current_time: Optional[str] = None
    ) -> Dict[str, Any]:
        """Formats VOD metadata according to configuration settings."""
        formatted = {}

        settings = self.settings
        if settings is None:
            return formatted

        if settings.vod_title:
            formatted["title"] = _metadata_value(metadata, "title", "Title")

        episode_title = _metadata_value(metadata, "episode_title", "EpisodeTitle")
        if settings.vod_episode_title and episode_title:
            formatted["episode_title"] = episode_title

        if settings.vod_summary:
            formatted["summary"] = _metadata_value(metadata, "summary", "Summary")

        if settings.vod_duration:
            duration = _metadata_value(metadata, "duration", "Duration", default=0)
            formatted["duration"] = self._format_duration(duration)

        if settings.vod_progress and current_time:
            formatted["progress"] = current_time

        if settings.vod_image:
            formatted["image_url"] = _metadata_value(
                metadata, "image_url", "Image", "image"
            )

        if settings.vod_rating:
            formatted["rating"] = _metadata_value(
                metadata, "content_rating", "ContentRating"
            )

        if settings.vod_genres:
            formatted["genres"] = _metadata_value(
                metadata, "genres", "Genres", default=[]
            )

        cast = _metadata_value(metadata, "cast", "Cast", default=[])
        if settings.vod_cast and cast:
            formatted["cast"] = cast

        return formatted

    def _format_duration(self, duration_seconds: float) -> str:
        """Converts duration in seconds to human-readable time format."""
        duration_seconds = float(duration_seconds or 0)
        hours = int(duration_seconds // 3600)
        minutes = int((duration_seconds % 3600) // 60)
        seconds = int(duration_seconds % 60)

        if hours > 0:
            return f"{hours}h {minutes:02d}m {seconds:02d}s"
        elif minutes > 0:
            return f"{minutes}m {seconds:02d}s"
        else:
            return f"{seconds}s"
