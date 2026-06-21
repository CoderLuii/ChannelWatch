from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Optional, Literal
from core.notifications.template_engine import TEMPLATE_SETTINGS_DEFAULTS

AuthMode = Literal["api_key", "rbac", "none"]
EffectiveAuthMode = Literal["api_key", "rbac", "none", "setup"]
SecurityMode = Literal[
    "NO_AUTH", "API_KEY_ONLY", "RBAC_WITH_API_KEY_FALLBACK", "RBAC_ONLY"
]


def BoolTrue():
    return Field(default=True)


def StrEmpty():
    return Field(default="")


def IntField(
    default_val: int,
    gt: Optional[int] = None,
    ge: Optional[int] = None,
    lt: Optional[int] = None,
    le: Optional[int] = None,
    description: Optional[str] = None,
):
    return Field(
        default=default_val, gt=gt, ge=ge, lt=lt, le=le, description=description
    )


def FloatField(
    default_val: float,
    gt: Optional[float] = None,
    ge: Optional[float] = None,
    lt: Optional[float] = None,
    le: Optional[float] = None,
    description: Optional[str] = None,
):
    return Field(
        default=default_val, gt=gt, ge=ge, lt=lt, le=le, description=description
    )


class AppSettings(BaseModel):
    dvr_servers: list[dict[str, Any]] = Field(
        default_factory=list, description="List of DVR server configurations"
    )
    tz: str = Field(
        default="America/Los_Angeles",
        description="Timezone (e.g., America/Los_Angeles)",
    )
    log_level: int = IntField(
        1, ge=1, le=2, description="Log level (1=Standard, 2=Verbose)"
    )
    log_retention_days: int = IntField(7, gt=0, description="Days to retain logs")
    history_retention_days: int = IntField(
        90, gt=0, description="Days to retain activity history in database"
    )

    alert_channel_watching: bool = BoolTrue()
    alert_vod_watching: bool = BoolTrue()
    alert_disk_space: bool = BoolTrue()
    alert_recording_events: bool = BoolTrue()

    multi_dvr_v2_enabled: bool = Field(default=True)
    rbac_enabled: bool = Field(default=False)
    auth_mode: AuthMode | Literal[""] = Field(
        default="",
        description="Auth mode: api_key, rbac, none, or empty for legacy auto-detect",
    )
    security_setup_completed: Optional[bool] = Field(default=None)

    stream_count: bool = BoolTrue()
    monitor_stale_seconds: int = IntField(
        300, ge=1, description="Seconds before DVR monitoring is considered stale"
    )

    cw_channel_name: bool = BoolTrue()
    cw_channel_number: bool = BoolTrue()
    cw_program_name: bool = BoolTrue()
    cw_device_name: bool = BoolTrue()
    cw_device_ip: bool = BoolTrue()
    cw_stream_source: bool = BoolTrue()
    cw_image_source: str = Field(
        default="PROGRAM", description="Image source (CHANNEL or PROGRAM)"
    )
    cw_alert_cooldown: int = IntField(
        300, ge=0, description="Channel watching alert cooldown (seconds)"
    )
    cw_template_title: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["cw_template_title"]
    )
    cw_template_body: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["cw_template_body"]
    )
    cw_template_use_default: bool = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["cw_template_use_default"]
    )

    global_rate_limit: int = IntField(
        20, ge=1, description="Max notifications per rate window"
    )
    global_rate_window: int = IntField(
        300, ge=10, description="Rate limit window in seconds"
    )

    stream_card_image: str = Field(
        default="program",
        description="Active streams card image: program, channel, or none",
    )
    recording_card_image: str = Field(
        default="program", description="Upcoming recordings card image: program or none"
    )

    api_key: Optional[str] = StrEmpty()
    ics_feed_enabled: bool = Field(
        default=False, description="Whether the tokenized ICS calendar feed is enabled"
    )
    ics_feed_token: Optional[str] = StrEmpty()
    rss_feed_enabled: bool = Field(
        default=False,
        description="Whether the tokenized RSS and Atom activity feeds are enabled",
    )
    rss_feed_token: Optional[str] = StrEmpty()
    webhooks: list["WebhookSettings"] = Field(
        default_factory=list, description="Outbound webhook destinations"
    )

    @field_validator("cw_image_source")
    def check_image_source(cls, v):
        if v.upper() not in ["CHANNEL", "PROGRAM"]:
            raise ValueError("must be either CHANNEL or PROGRAM")
        return v.upper()

    @field_validator("auth_mode", mode="before")
    @classmethod
    def normalize_auth_mode(cls, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    rd_alert_scheduled: bool = BoolTrue()
    rd_alert_started: bool = BoolTrue()
    rd_alert_completed: bool = BoolTrue()
    rd_alert_cancelled: bool = BoolTrue()
    rd_program_name: bool = BoolTrue()
    rd_program_desc: bool = BoolTrue()
    rd_duration: bool = BoolTrue()
    rd_channel_name: bool = BoolTrue()
    rd_channel_number: bool = BoolTrue()
    rd_type: bool = BoolTrue()
    rd_template_title: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["rd_template_title"]
    )
    rd_template_body: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["rd_template_body"]
    )
    rd_template_use_default: bool = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["rd_template_use_default"]
    )

    vod_title: bool = BoolTrue()
    vod_episode_title: bool = BoolTrue()
    vod_summary: bool = BoolTrue()
    vod_duration: bool = BoolTrue()
    vod_progress: bool = BoolTrue()
    vod_image: bool = BoolTrue()
    vod_rating: bool = BoolTrue()
    vod_genres: bool = BoolTrue()
    vod_cast: bool = BoolTrue()
    vod_device_name: bool = BoolTrue()
    vod_device_ip: bool = BoolTrue()
    vod_alert_cooldown: int = IntField(
        300, ge=0, description="VOD alert cooldown (seconds)"
    )
    vod_significant_threshold: int = IntField(
        300, ge=0, description="VOD significant view threshold (seconds)"
    )
    vod_template_title: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["vod_template_title"]
    )
    vod_template_body: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["vod_template_body"]
    )
    vod_template_use_default: bool = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["vod_template_use_default"]
    )

    channel_cache_ttl: int = IntField(
        86400, ge=0, le=604800, description="Channel cache TTL in seconds (max 7 days)"
    )
    program_cache_ttl: int = IntField(
        86400, ge=0, le=604800, description="Program cache TTL in seconds (max 7 days)"
    )
    job_cache_ttl: int = IntField(
        3600, ge=0, le=604800, description="Job cache TTL in seconds (max 7 days)"
    )
    vod_cache_ttl: int = IntField(
        86400, ge=0, le=604800, description="VOD cache TTL in seconds (max 7 days)"
    )

    ds_threshold_percent: int = IntField(10, ge=0, le=100)
    ds_threshold_gb: int = IntField(50, ge=0)
    ds_warning_threshold_percent: int = IntField(10, ge=0, le=100)
    ds_warning_threshold_gb: int = IntField(50, ge=0)
    ds_critical_threshold_percent: int = IntField(5, ge=0, le=100)
    ds_critical_threshold_gb: int = IntField(25, ge=0)
    ds_alert_cooldown: int = IntField(
        3600, ge=0, description="Disk space alert cooldown (seconds)"
    )
    ds_startup_grace_seconds: int = IntField(
        10, ge=0, description="Disk alert startup grace period in seconds"
    )
    ds_worsening_delta_gb: int = IntField(
        1, ge=0, description="Disk alert worsening threshold in GB"
    )
    ds_worsening_delta_percent: float = FloatField(
        1.0, ge=0, description="Disk alert worsening threshold in percent"
    )
    ds_test_route_override: Optional[str] = StrEmpty()
    ds_template_title: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["ds_template_title"]
    )
    ds_template_body: str = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["ds_template_body"]
    )
    ds_template_use_default: bool = Field(
        default=TEMPLATE_SETTINGS_DEFAULTS["ds_template_use_default"]
    )

    apprise_pushover: Optional[str] = StrEmpty()
    apprise_discord: Optional[str] = StrEmpty()
    apprise_email: Optional[str] = StrEmpty()
    apprise_email_to: Optional[str] = StrEmpty()
    apprise_telegram: Optional[str] = StrEmpty()
    apprise_slack: Optional[str] = StrEmpty()
    apprise_gotify: Optional[str] = StrEmpty()
    apprise_matrix: Optional[str] = StrEmpty()
    apprise_custom: Optional[str] = StrEmpty()

    error_reporting_dsn: Optional[str] = StrEmpty()

    notification_routing: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-DVR per-event-type notification routing: dvr_id->event_type->{apprise,webhook}. Absent keys default to all-enabled.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_disk_alert_settings(cls, data):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)

        def is_blank(value):
            return value is None or (isinstance(value, str) and value.strip() == "")

        def normalize_number(key, default, cast_type, fallback_key=None):
            value = normalized.get(key)
            if not is_blank(value):
                try:
                    normalized[key] = cast_type(value)
                    return normalized[key]
                except (TypeError, ValueError):
                    pass

            if fallback_key is not None:
                fallback_value = normalized.get(fallback_key)
                if not is_blank(fallback_value):
                    try:
                        normalized[key] = cast_type(fallback_value)
                        return normalized[key]
                    except (TypeError, ValueError):
                        pass

            normalized[key] = default
            return normalized[key]

        normalize_number("ds_threshold_percent", 10, int)
        normalize_number("ds_threshold_gb", 50, int)
        normalize_number(
            "ds_warning_threshold_percent", 10, int, fallback_key="ds_threshold_percent"
        )
        normalize_number(
            "ds_warning_threshold_gb", 50, int, fallback_key="ds_threshold_gb"
        )
        normalize_number("ds_critical_threshold_percent", 5, int)
        normalize_number("ds_critical_threshold_gb", 25, int)
        normalize_number("ds_startup_grace_seconds", 10, int)
        normalize_number("ds_worsening_delta_gb", 1, int)
        normalize_number("ds_worsening_delta_percent", 1.0, float)

        if is_blank(normalized.get("ds_test_route_override")):
            normalized["ds_test_route_override"] = ""

        return normalized

    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "description": "Settings for ChannelWatch application. Default values are provided by the model itself.",
            "example": {
                "dvr_servers": [
                    {
                        "id": "dvr_1",
                        "name": "Main DVR",
                        "host": "X.X.X.X",
                        "port": 8089,
                        "enabled": True,
                    }
                ],
                "tz": "America/Los_Angeles",
            },
        },
    }


class WebhookSettings(BaseModel):
    url: str = Field(default="", description="Destination URL for webhook delivery")
    secret: str = Field(
        default="", description="Shared secret used for HMAC-SHA256 signing"
    )
    enabled: bool = Field(default=False, description="Whether this webhook is active")

    @field_validator("url", "secret", mode="before")
    @classmethod
    def normalize_strings(cls, value):
        if value is None:
            return ""
        return str(value)


class AuthStateContract(BaseModel):
    persisted_mode: Optional[AuthMode] = None
    configured_mode: Optional[EffectiveAuthMode] = None
    effective_mode: Optional[EffectiveAuthMode] = None
    setup_required: bool
    runtime_auth_override_active: bool
    api_key_fallback_active: bool
    rbac_enabled: bool
    session_auth_available: bool
    session_setup_required: bool


class SecurityFeedsStatus(BaseModel):
    implemented: bool
    ics_enabled: bool
    rss_enabled: bool
    atom_enabled: bool


class SecurityStatusResponse(AuthStateContract):
    security_mode: SecurityMode
    auth_disabled: bool
    api_key_configured: bool
    encrypted_dvr_api_keys_at_rest: bool
    encryption_key_path: str
    feeds: SecurityFeedsStatus


class SetupStatusResponse(AuthStateContract):
    needs_setup: bool
    current_mode: Optional[EffectiveAuthMode] = None
    available_modes: list[AuthMode]


AppSettings.model_rebuild()
