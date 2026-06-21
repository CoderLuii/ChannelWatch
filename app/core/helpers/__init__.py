"""Core utility modules for ChannelWatch DVR monitoring and integration."""

# ---------------- DATA PROVIDERS ----------------
from .channel_info import ChannelInfoProvider
from .program_info import ProgramInfoProvider
from .vod_info import VODInfoProvider
from .recording_info import RecordingInfoProvider
from .job_info import JobInfoProvider

# ---------------- LOGGING ----------------
from .logging import log, set_log_level, setup_logging, LOG_STANDARD, LOG_VERBOSE
from .structured_log import set_log_context, clear_log_context, log_context

# ---------------- DATA PARSING ----------------
from .parsing import (
    extract_channel_number,
    extract_channel_name,
    extract_device_name,
    extract_ip_address,
    extract_resolution,
    extract_source_from_session_id,
)

# ---------------- DIAGNOSTIC TOOLS ----------------
from .tools import monitor_event_stream

# ---------------- ACTIVITY RECORDING ----------------
from .activity_recorder import record_activity

# ---------------- PUBLIC API ----------------
__all__ = [
    "ChannelInfoProvider",
    "ProgramInfoProvider",
    "VODInfoProvider",
    "RecordingInfoProvider",
    "JobInfoProvider",
    "log",
    "set_log_level",
    "setup_logging",
    "LOG_STANDARD",
    "LOG_VERBOSE",
    "set_log_context",
    "clear_log_context",
    "log_context",
    "set_log_context",
    "clear_log_context",
    "log_context",
    "extract_channel_number",
    "extract_channel_name",
    "extract_device_name",
    "extract_ip_address",
    "extract_resolution",
    "extract_source_from_session_id",
    "monitor_event_stream",
    "record_activity",
]
