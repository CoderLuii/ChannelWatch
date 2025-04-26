from pydantic import BaseModel, Field, field_validator
from typing import Optional, List

def BoolTrue():
    return Field(default=True)

def StrEmpty():
     return Field(default="")

def IntField(default_val: int, gt: Optional[int] = None, ge: Optional[int] = None, lt: Optional[int] = None, le: Optional[int] = None, description: Optional[str] = None):
     return Field(default=default_val, gt=gt, ge=ge, lt=lt, le=le, description=description)

class AppSettings(BaseModel):
    channels_dvr_host: Optional[str] = Field(
        default=None, 
        description="Channels DVR hostname or IP"
    )
    channels_dvr_port: int = IntField(8089, gt=0, lt=65536, description="Channels DVR port")
    tz: str = Field(default="America/Los_Angeles", description="Timezone (e.g., America/Los_Angeles)")
    log_level: int = IntField(1, ge=1, le=2, description="Log level (1=Standard, 2=Verbose)")
    log_retention_days: int = IntField(7, gt=0, description="Days to retain logs")

    alert_channel_watching: bool = BoolTrue()
    alert_vod_watching: bool = BoolTrue()
    alert_disk_space: bool = BoolTrue()
    alert_recording_events: bool = BoolTrue()

    stream_count: bool = BoolTrue()

    cw_channel_name: bool = BoolTrue()
    cw_channel_number: bool = BoolTrue()
    cw_program_name: bool = BoolTrue()
    cw_device_name: bool = BoolTrue()
    cw_device_ip: bool = BoolTrue()
    cw_stream_source: bool = BoolTrue()
    cw_image_source: str = Field(default="PROGRAM", description="Image source (CHANNEL or PROGRAM)")
    
    @field_validator('cw_image_source')
    def check_image_source(cls, v):
        if v.upper() not in ["CHANNEL", "PROGRAM"]:
            raise ValueError('must be either CHANNEL or PROGRAM')
        return v.upper()

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
    vod_alert_cooldown: int = IntField(300, ge=0, description="VOD alert cooldown (seconds)")
    vod_significant_threshold: int = IntField(300, ge=0, description="VOD significant view threshold (seconds)")

    channel_cache_ttl: int = IntField(86400, ge=0)
    program_cache_ttl: int = IntField(86400, ge=0)
    job_cache_ttl: int = IntField(3600, ge=0)
    vod_cache_ttl: int = IntField(86400, ge=0)

    ds_threshold_percent: int = IntField(10, ge=0, le=100)
    ds_threshold_gb: int = IntField(50, ge=0)

    pushover_user_key: Optional[str] = StrEmpty()
    pushover_api_token: Optional[str] = StrEmpty()
    apprise_discord: Optional[str] = StrEmpty() 
    apprise_email: Optional[str] = StrEmpty()
    apprise_email_to: Optional[str] = StrEmpty()
    apprise_telegram: Optional[str] = StrEmpty()
    apprise_slack: Optional[str] = StrEmpty()
    apprise_gotify: Optional[str] = StrEmpty()
    apprise_matrix: Optional[str] = StrEmpty()
    apprise_mqtt: Optional[str] = StrEmpty()
    apprise_custom: Optional[str] = StrEmpty()
    
    model_config = {
        "json_schema_extra": {
            "description": "Settings for ChannelWatch application. Default values are provided by the model itself.",
            "example": {
                "channels_dvr_host": "X.X.X.X",
                "channels_dvr_port": 8089,
                "tz": "America/Los_Angeles"
            }
        }
    } 