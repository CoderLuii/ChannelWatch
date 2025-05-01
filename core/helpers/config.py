# Configuration management for ChannelWatch application settings.
import json
import os
import yaml
from pathlib import Path
from dataclasses import dataclass, fields, field, is_dataclass
from typing import Optional, Any, List, Type
from threading import Lock
# Import custom logger
from .logging import log, LOG_VERBOSE, LOG_STANDARD

# PATHS
CONFIG_DIR = Path(os.getenv("CONFIG_PATH", "/config"))
CONFIG_FILE = CONFIG_DIR / "settings.json"

# SETTINGS
@dataclass
class CoreSettings:
    # Connection
    channels_dvr_host: Optional[str] = None
    channels_dvr_port: int = 8089
    tz: str = "America/Los_Angeles"
    log_level: int = 1
    log_retention_days: int = 7

    # Alert Controls
    alert_channel_watching: bool = True
    alert_vod_watching: bool = True
    alert_disk_space: bool = True
    alert_recording_events: bool = True

    # Stream Monitoring
    stream_count: bool = True

    # Channel-Watching Alert Content
    cw_channel_name: bool = True
    cw_channel_number: bool = True
    cw_program_name: bool = True
    cw_device_name: bool = True
    cw_device_ip: bool = True
    cw_stream_source: bool = True
    cw_image_source: str = "PROGRAM"

    # Recording Events Alert Content
    rd_alert_scheduled: bool = True
    rd_alert_started: bool = True
    rd_alert_completed: bool = True
    rd_alert_cancelled: bool = True
    rd_program_name: bool = True
    rd_program_desc: bool = True
    rd_duration: bool = True
    rd_channel_name: bool = True
    rd_channel_number: bool = True
    rd_type: bool = True

    # VOD Watching Alert Content
    vod_title: bool = True
    vod_episode_title: bool = True
    vod_summary: bool = True
    vod_duration: bool = True
    vod_progress: bool = True
    vod_image: bool = True
    vod_rating: bool = True
    vod_genres: bool = True
    vod_cast: bool = True
    vod_device_name: bool = True
    vod_device_ip: bool = True
    vod_alert_cooldown: int = 300
    vod_significant_threshold: int = 300

    # Cache Duration Settings
    channel_cache_ttl: int = 86400
    program_cache_ttl: int = 86400
    job_cache_ttl: int = 3600
    vod_cache_ttl: int = 86400

    # Disk Space Alert Thresholds
    ds_threshold_percent: int = 10
    ds_threshold_gb: int = 50

    # Notification Service Endpoints
    pushover_user_key: Optional[str] = ""
    pushover_api_token: Optional[str] = ""
    apprise_discord: Optional[str] = ""
    apprise_email: Optional[str] = ""
    apprise_email_to: Optional[str] = ""
    apprise_telegram: Optional[str] = ""
    apprise_slack: Optional[str] = ""
    apprise_gotify: Optional[str] = ""
    apprise_matrix: Optional[str] = ""
    apprise_custom: Optional[str] = ""

    # Singleton
    _instance = None

    def __post_init__(self):
        self._load_and_override()

    def _load_from_file(self) -> dict[str, Any]:
        """Load settings from JSON file or return empty dict if file is missing or invalid."""
        if CONFIG_FILE.is_file():
            try:
                data = json.loads(CONFIG_FILE.read_text())
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError) as e:
                print(f"[Core Config] Warning: Failed to read/parse {CONFIG_FILE}: {e}")
        return {}

    def _override_from_env(self):
        """Apply environment variable overrides for critical connection settings."""
        host_env = os.getenv("CHANNELS_DVR_HOST")
        if host_env:
            print("[Core Config] Info: Overriding host from CHANNELS_DVR_HOST env var.")
            self.channels_dvr_host = host_env

        port_env = os.getenv("CHANNELS_DVR_PORT")
        if port_env:
            try:
                self.channels_dvr_port = int(port_env)
                print(f"[Core Config] Info: Overriding port from CHANNELS_DVR_PORT env var to {self.channels_dvr_port}.")
            except ValueError:
                print(f"[Core Config] Warning: Invalid CHANNELS_DVR_PORT env var: {port_env}")

    def _load_and_override(self):
        """Process configuration from file then apply environment variable overrides."""
        file_settings = self._load_from_file()
        cls_fields = {f.name: f for f in fields(self)}

        for key, value in file_settings.items():
            if key in cls_fields:
                setattr(self, key, value)

        self._override_from_env()

    @classmethod
    def get(cls) -> 'CoreSettings':
        """Retrieve singleton settings instance, initializing on first call."""
        if cls._instance is None:
            with _lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

# ACCESS
def get_settings() -> CoreSettings:
    return CoreSettings.get()

_settings: Optional[CoreSettings] = None 
_lock = Lock()

log("Settings module loaded", level=LOG_VERBOSE)

# Decorator for thread-safe access to settings 