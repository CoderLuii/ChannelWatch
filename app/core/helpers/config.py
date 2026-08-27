# Configuration management for ChannelWatch application settings.
import json
import os
from copy import deepcopy
from dataclasses import InitVar, dataclass, field, fields
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from ..notifications.template_engine import TEMPLATE_SETTINGS_DEFAULTS
from .atomic_io import atomic_write_private_json, read_regular_file_bytes
from .dvr_id import dvr_display_name
from .logging import LOG_VERBOSE, log

# PATHS
CONFIG_DIR = Path(os.getenv("CONFIG_PATH", "/config"))
CONFIG_FILE = CONFIG_DIR / "settings.json"
MAX_SETTINGS_FILE_BYTES = 8 * 1024 * 1024


class ConfigLoadError(RuntimeError):
    """Raised when settings.json is corrupt and startup must fail closed."""


def _build_recovery_message(config_file: Path, reason: str) -> str:
    return (
        f"Corrupt ChannelWatch config at {config_file}: {reason}. "
        f"Startup is blocked to avoid silently falling back to defaults. "
        f"Restore {config_file.name} from {config_file.parent / 'backups'} or repair the file and restart."
    )


def _strip_legacy_optional_markers_for_persistence(
    merged_settings: dict[str, Any],
    original_settings: dict[str, Any],
) -> dict[str, Any]:
    """Keep absent legacy marker fields absent until explicitly chosen by the operator."""
    persisted = dict(merged_settings)
    if (
        "security_setup_completed" not in original_settings
        and persisted.get("security_setup_completed") is None
    ):
        persisted.pop("security_setup_completed", None)
    return persisted


def _coerce_dvr_servers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    servers: list[dict[str, Any]] = []
    for index, server in enumerate(value):
        if isinstance(server, dict):
            servers.append(server)
        else:
            print(
                "[Core Config] WARNING: Ignoring malformed dvr_servers "
                f"entry at index {index}: expected object, got {type(server).__name__}",
                flush=True,
            )
    return servers


# SETTINGS
@dataclass
class CoreSettings:
    # DVR Servers
    dvr_servers: list[dict[str, Any]] = field(default_factory=list)
    tz: str = "America/Los_Angeles"
    log_level: int = 1
    log_retention_days: int = 7
    history_retention_days: int = 90

    # Feature Flags
    multi_dvr_v2_enabled: bool = True
    auth_mode: str = ""
    rbac_enabled: bool = False
    security_setup_completed: Optional[bool] = None

    # Alert Controls
    alert_channel_watching: bool = True
    alert_vod_watching: bool = True
    alert_disk_space: bool = True
    alert_recording_events: bool = True
    alert_dvr_health: bool = False

    # Stream Monitoring
    stream_count: bool = True
    monitor_stale_seconds: int = 300

    # Channel-Watching Alert Content
    cw_channel_name: bool = True
    cw_channel_number: bool = True
    cw_program_name: bool = True
    cw_device_name: bool = True
    cw_device_ip: bool = True
    cw_stream_source: bool = True
    cw_image_source: str = "PROGRAM"
    cw_alert_cooldown: int = 300
    cw_template_title: str = TEMPLATE_SETTINGS_DEFAULTS["cw_template_title"]
    cw_template_body: str = TEMPLATE_SETTINGS_DEFAULTS["cw_template_body"]
    cw_template_use_default: bool = TEMPLATE_SETTINGS_DEFAULTS[
        "cw_template_use_default"
    ]

    # Global Rate Limiter
    global_rate_limit: int = 20
    global_rate_window: int = 300

    # Display
    stream_card_image: str = "program"
    recording_card_image: str = "program"

    # API Authentication
    api_key: Optional[str] = ""
    ics_feed_enabled: bool = False
    ics_feed_token: Optional[str] = ""
    rss_feed_enabled: bool = False
    rss_feed_token: Optional[str] = ""

    webhooks: list[dict[str, Any]] = field(default_factory=list)
    trusted_notification_destinations: list[dict[str, Any]] = field(
        default_factory=list
    )

    # Recording Events Alert Content
    rd_alert_scheduled: bool = True
    rd_alert_started: bool = True
    rd_alert_completed: bool = True
    rd_alert_cancelled: bool = True
    rd_alert_failed: bool = False
    rd_alert_skipped: bool = False
    rd_alert_missed: bool = False
    rd_alert_interrupted: bool = False
    dvr_alert_unreachable: bool = False
    dvr_alert_recovered: bool = False
    dvr_health_alert_delay_seconds: int = 120
    notification_preferences_version: int = 0
    rd_program_name: bool = True
    rd_program_desc: bool = True
    rd_duration: bool = True
    rd_channel_name: bool = True
    rd_channel_number: bool = True
    rd_type: bool = True
    rd_template_title: str = TEMPLATE_SETTINGS_DEFAULTS["rd_template_title"]
    rd_template_body: str = TEMPLATE_SETTINGS_DEFAULTS["rd_template_body"]
    rd_template_use_default: bool = TEMPLATE_SETTINGS_DEFAULTS[
        "rd_template_use_default"
    ]

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
    vod_template_title: str = TEMPLATE_SETTINGS_DEFAULTS["vod_template_title"]
    vod_template_body: str = TEMPLATE_SETTINGS_DEFAULTS["vod_template_body"]
    vod_template_use_default: bool = TEMPLATE_SETTINGS_DEFAULTS[
        "vod_template_use_default"
    ]

    # Cache Duration Settings
    channel_cache_ttl: int = 86400
    program_cache_ttl: int = 86400
    job_cache_ttl: int = 3600
    vod_cache_ttl: int = 86400

    # Disk Space Alert Thresholds
    ds_threshold_percent: int = 10
    ds_threshold_gb: int = 50
    ds_warning_threshold_percent: int = 10
    ds_warning_threshold_gb: int = 50
    ds_critical_threshold_percent: int = 5
    ds_critical_threshold_gb: int = 25
    ds_alert_cooldown: int = 3600
    ds_startup_grace_seconds: int = 10
    ds_worsening_delta_gb: int = 1
    ds_worsening_delta_percent: float = 1.0
    ds_test_route_override: Optional[str] = ""
    ds_template_title: str = TEMPLATE_SETTINGS_DEFAULTS["ds_template_title"]
    ds_template_body: str = TEMPLATE_SETTINGS_DEFAULTS["ds_template_body"]
    ds_template_use_default: bool = TEMPLATE_SETTINGS_DEFAULTS[
        "ds_template_use_default"
    ]

    apprise_pushover: Optional[str] = ""
    apprise_discord: Optional[str] = ""
    apprise_email: Optional[str] = ""
    apprise_email_to: Optional[str] = ""
    apprise_telegram: Optional[str] = ""
    apprise_slack: Optional[str] = ""
    apprise_gotify: Optional[str] = ""
    apprise_matrix: Optional[str] = ""
    apprise_custom: Optional[str] = ""

    error_reporting_dsn: Optional[str] = ""

    notification_routing: dict[str, Any] = field(default_factory=dict)

    # Non-persisted dependency injection for callers that intentionally use a
    # configuration root other than the process-wide CONFIG_PATH.  The UI
    # backend uses this to keep its settings view and the core settings loader
    # on the same directory; tests use it for disposable configuration roots.
    _config_root: InitVar[Optional[Path]] = None

    # Singleton
    _instance = None

    def __post_init__(self, _config_root: Optional[Path]):
        runtime_config_dir = (
            Path(_config_root) if _config_root is not None else CONFIG_DIR
        )
        self._runtime_config_dir = runtime_config_dir
        self._runtime_config_file = runtime_config_dir / CONFIG_FILE.name
        self._load_and_override()

    def _load_from_file(self) -> dict[str, Any]:
        """Load settings from JSON file or return empty dict if file is missing."""
        config_file = self._runtime_config_file
        if config_file.is_file():
            try:
                data = json.loads(
                    read_regular_file_bytes(
                        config_file, max_bytes=MAX_SETTINGS_FILE_BYTES
                    ).decode("utf-8-sig")
                )
            except json.JSONDecodeError as e:
                raise ConfigLoadError(
                    _build_recovery_message(config_file, f"invalid JSON ({e})")
                ) from e
            except (OSError, ValueError) as e:
                raise ConfigLoadError(
                    _build_recovery_message(config_file, f"read error ({e})")
                ) from e

            if not isinstance(data, dict):
                raise ConfigLoadError(
                    _build_recovery_message(
                        config_file,
                        f"expected a JSON object but found {type(data).__name__}",
                    )
                )
            return data
        return {}

    @staticmethod
    def _make_dvr_id(host, port):
        """Generate a deterministic DVR ID from host:port."""
        from .dvr_id import canonical_dvr_id

        return canonical_dvr_id(host, port)

    def _override_from_env(self):
        """Apply environment variable overrides for critical connection settings."""
        # Build lookup of existing servers by host:port for merge
        existing_by_hp = {}
        for s in self.dvr_servers or []:
            if isinstance(s, dict):
                existing_by_hp[(s.get("host", ""), s.get("port", 8089))] = s

        # Multi-DVR: CHANNELS_DVR_SERVERS=Name@host:port,Name@host:port
        servers_env = os.getenv("CHANNELS_DVR_SERVERS")
        if servers_env:
            parsed = []
            for entry in servers_env.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                name, _, hostport = (
                    entry.rpartition("@") if "@" in entry else ("", "", entry)
                )
                if ":" in hostport:
                    host, port_str = hostport.rsplit(":", 1)
                    try:
                        port = int(port_str)
                    except ValueError:
                        host, port = hostport, 8089
                else:
                    host, port = hostport, 8089
                if not name:
                    name = host
                existing = existing_by_hp.get((host, port))
                parsed.append(
                    {
                        "id": existing["id"]
                        if existing
                        else self._make_dvr_id(host, port),
                        "name": name,
                        "host": host,
                        "port": port,
                        "enabled": True,
                        "overrides": existing.get("overrides", {}) if existing else {},
                    }
                )
            if parsed:
                # Keep manually-added servers not in env vars
                env_keys = {(e["host"], e["port"]) for e in parsed}
                for s in self.dvr_servers or []:
                    if (
                        isinstance(s, dict)
                        and (s.get("host", ""), s.get("port", 8089)) not in env_keys
                    ):
                        parsed.append(s)
                self.dvr_servers = parsed
                print(
                    f"[Core Config] Info: Overriding DVR servers from CHANNELS_DVR_SERVERS env var ({len(parsed)} servers)."
                )

        # Legacy single-server env var (only if CHANNELS_DVR_SERVERS not set)
        if not servers_env:
            host_env = os.getenv("CHANNELS_DVR_HOST")
            if host_env:
                port_env = os.getenv("CHANNELS_DVR_PORT")
                port = 8089
                if port_env:
                    try:
                        port = int(port_env)
                    except ValueError:
                        pass
                existing = existing_by_hp.get((host_env, port))
                new_server = {
                    "id": existing["id"]
                    if existing
                    else self._make_dvr_id(host_env, port),
                    "name": host_env,
                    "host": host_env,
                    "port": port,
                    "enabled": True,
                    "overrides": existing.get("overrides", {}) if existing else {},
                }
                # Keep manually-added servers
                other_servers = [
                    s
                    for s in (self.dvr_servers or [])
                    if isinstance(s, dict)
                    and (s.get("host", ""), s.get("port", 8089)) != (host_env, port)
                ]
                self.dvr_servers = [new_server] + other_servers
                print(
                    "[Core Config] WARNING: CHANNELS_DVR_HOST / CHANNELS_DVR_PORT env vars are "
                    "deprecated and may be removed in a future release. "
                    "Use CHANNELS_DVR_SERVERS, the UI settings page, or persisted dvr_servers in settings.json.",
                    flush=True,
                )

        tz_env = os.getenv("TZ")
        if tz_env:
            print(
                f"[Core Config] Info: Overriding timezone from TZ env var to {tz_env}."
            )
            self.tz = tz_env

    def get_dvr_connections(self):
        from .dvr_connection import DVRConnection

        connections = []
        servers = self.dvr_servers
        if not self.multi_dvr_v2_enabled:
            servers = [server for server in servers if not server.get("deleted_at")][:1]

        for server in servers:
            if server.get("deleted_at"):
                continue
            if server.get("enabled", True):
                connections.append(
                    DVRConnection(
                        id=server.get("id", ""),
                        name=dvr_display_name(
                            server.get("name"),
                            server.get("host"),
                            fallback="Unknown",
                        ),
                        host=server.get("host", ""),
                        port=server.get("port", 8089),
                        enabled=True,
                        api_key=server.get("api_key", "") or "",
                        overrides=server.get("overrides") or {},
                    )
                )
        return connections

    def _load_and_override(self):
        """Process configuration from file then apply environment variable overrides."""
        from .maintenance_transaction import configuration_maintenance_lock

        # Settings, their encryption key, restore, rotation, and updater
        # backups form one consistency unit. Readers take the same lock so no
        # monitor can observe a mixed key/settings pair mid-transaction.
        with configuration_maintenance_lock(self._runtime_config_dir):
            self._load_and_override_locked()

    def _load_and_override_locked(self):
        """Load configuration while the shared `/config` lock is held."""
        from .migration import (
            defaults_merge,
            get_dataclass_defaults,
            migrate_settings,
            normalize_disk_alert_settings,
        )

        file_settings = self._load_from_file()
        original_file_settings = deepcopy(file_settings)
        old_version = file_settings.get("_version", 0)

        # Run full migration pipeline (backup, recovery, versioned migrations)
        file_settings = migrate_settings(self._runtime_config_dir, file_settings)

        # Merge with dataclass defaults (catches any new fields not in migrations)
        defaults = get_dataclass_defaults(type(self))
        merged = defaults_merge(file_settings, defaults)
        merged = normalize_disk_alert_settings(merged)
        persisted_merged = _strip_legacy_optional_markers_for_persistence(
            merged, file_settings
        )

        from .encryption import (
            ENCRYPTION_KEY_FILE,
            decrypt_registered_credentials_with_diagnostics,
            encrypt_dvr_api_keys,
            encrypt_webhook_credentials,
        )
        from .protected_credentials import (
            is_protected_ciphertext,
            iter_protected_values,
            preserve_failed_ciphertexts,
            publish_protected_credential_failures,
        )

        key_file = self._runtime_config_dir / ENCRYPTION_KEY_FILE.name
        diagnostic = decrypt_registered_credentials_with_diagnostics(
            persisted_merged,
            key_file,
        )
        persisted_merged = preserve_failed_ciphertexts(
            persisted_merged,
            original_file_settings,
            diagnostic.failures,
        )
        protected_field_migration_required = any(
            not is_protected_ciphertext(item.value)
            for item in iter_protected_values(persisted_merged)
        )
        persisted_merged["dvr_servers"] = encrypt_dvr_api_keys(
            persisted_merged.get("dvr_servers") or [],
            key_file,
        )
        persisted_merged["webhooks"] = encrypt_webhook_credentials(
            persisted_merged.get("webhooks") or [],
            key_file,
        )

        # Persist merged defaults after successful migration using atomic replacement.
        new_version = persisted_merged.get("_version", 0)
        if self._runtime_config_file.is_file() and (
            new_version > old_version or persisted_merged != file_settings
        ):
            try:
                atomic_write_private_json(
                    self._runtime_config_file,
                    persisted_merged,
                )
            except OSError as exc:
                if new_version > old_version or protected_field_migration_required:
                    raise ConfigLoadError(
                        "A required settings or protected-credential migration "
                        "could not be persisted safely. Startup is blocked; repair "
                        "/config write access and restart."
                    ) from exc
                log(
                    "Settings defaults could not be persisted; continuing with the "
                    "readable current-schema configuration in memory."
                )

        protected = decrypt_registered_credentials_with_diagnostics(
            persisted_merged,
            key_file,
        )
        publish_protected_credential_failures(protected.failures)
        persisted_merged = protected.settings
        persisted_merged["dvr_servers"] = _coerce_dvr_servers(
            persisted_merged.get("dvr_servers") or []
        )

        cls_fields = {f.name: f for f in fields(self)}
        for key, value in persisted_merged.items():
            if key in cls_fields:
                setattr(self, key, value)

        self._override_from_env()
        self._warn_if_dvr_soft_limit_exceeded()

    def _warn_if_dvr_soft_limit_exceeded(self):
        enabled_count = sum(
            1
            for server in self.dvr_servers or []
            if isinstance(server, dict)
            and not server.get("deleted_at")
            and server.get("enabled", True)
        )
        if enabled_count > 10:
            print(
                f"[Core Config] WARNING: ChannelWatch is configured with {enabled_count} DVRs "
                "(recommended soft limit: 10). Performance may degrade at high DVR counts.",
                flush=True,
            )

    @classmethod
    def get(cls) -> "CoreSettings":
        """Retrieve singleton settings instance, initializing on first call."""
        if cls._instance is None:
            with _lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


_lock = Lock()


# ACCESS
def get_settings(*, config_dir: Optional[Path] = None) -> CoreSettings:
    """Return settings for the process root or an explicitly supplied root.

    The process-wide configuration keeps the historical singleton behavior.
    An explicit root is intentionally uncached so a caller cannot poison the
    production singleton with a disposable or alternate configuration path.
    """
    if config_dir is None or Path(config_dir) == CONFIG_DIR:
        return CoreSettings.get()
    return CoreSettings(_config_root=Path(config_dir))


log("Settings module loaded", level=LOG_VERBOSE)

# Decorator for thread-safe access to settings
