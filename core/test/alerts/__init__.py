"""Tests for various alert types in ChannelWatch."""
from .test_channel_watching import test_channel_watching_alert
from .test_disk_space import test_disk_space_alert
from .test_vod_watching import test_vod_watching_alert
from .test_recording_events import test_recording_events_alert

# ---------------- EXPORTED FUNCTIONS ----------------

__all__ = ['test_channel_watching_alert', 'test_disk_space_alert', 'test_vod_watching_alert', 'test_recording_events_alert']