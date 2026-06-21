"""Runtime diagnostics for ChannelWatch."""

from .connectivity.server import (
    test_connectivity,
    test_api_endpoints,
    test_event_stream,
)
from .alerts.channel_watching import test_channel_watching_alert
from .alerts.disk_space import test_disk_space_alert
from .alerts.vod_watching import test_vod_watching_alert
from .alerts.recording_events import (
    test_recording_events_alert,
    test_recording_scheduled_alert,
    test_recording_started_alert,
    test_recording_completed_alert,
    test_recording_stopped_alert,
    test_recording_cancelled_alert,
)
from ..helpers.logging import log

# ---------------- EXPORTED FUNCTIONS ----------------

__all__ = [
    "test_connectivity",
    "test_api_endpoints",
    "test_event_stream",
    "test_channel_watching_alert",
    "test_disk_space_alert",
    "test_vod_watching_alert",
    "test_recording_events_alert",
    "run_test",
]

# ---------------- DIAGNOSTIC RUNNER ----------------

ALERT_TESTS = {
    "Channel-Watching": test_channel_watching_alert,
    "ALERT_CHANNEL_WATCHING": test_channel_watching_alert,
    "Disk-Space": test_disk_space_alert,
    "ALERT_DISK_SPACE": test_disk_space_alert,
    "VOD-Watching": test_vod_watching_alert,
    "ALERT_VOD_WATCHING": test_vod_watching_alert,
    "Recording-Events": test_recording_events_alert,
    "ALERT_RECORDING_EVENTS": test_recording_events_alert,
    "Recording-Scheduled": test_recording_scheduled_alert,
    "Recording-Started": test_recording_started_alert,
    "Recording-Completed": test_recording_completed_alert,
    "Recording-Stopped": test_recording_stopped_alert,
    "Recording-Cancelled": test_recording_cancelled_alert,
}


def run_test(
    test_name: str, host: str, port: int, alert_manager=None, duration=30
) -> bool:
    """Executes a specified diagnostic with given parameters and returns the result."""
    if test_name == "connectivity":
        return test_connectivity(host, port)
    elif test_name == "api":
        return test_api_endpoints(host, port)
    elif test_name == "event_stream":
        return test_event_stream(host, port, duration)
    elif test_name in ALERT_TESTS:
        if not alert_manager:
            log(f"[FAIL]  alert_manager required for {test_name} test")
            return False
        return ALERT_TESTS[test_name](host, port, alert_manager)
    else:
        log(f"[FAIL]  Unknown test: {test_name}")
        return False
