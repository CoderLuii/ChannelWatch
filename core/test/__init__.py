"""Test framework for ChannelWatch."""
from .connectivity.test_server import test_connectivity, test_api_endpoints, test_event_stream
from .alerts.test_channel_watching import test_channel_watching_alert
from .alerts.test_disk_space import test_disk_space_alert
from .alerts.test_vod_watching import test_vod_watching_alert
from .alerts.test_recording_events import test_recording_events_alert
from ..helpers.logging import log

# ---------------- EXPORTED FUNCTIONS ----------------

__all__ = [
    'test_connectivity', 
    'test_api_endpoints', 
    'test_event_stream',
    'test_channel_watching_alert',
    'test_disk_space_alert',
    'test_vod_watching_alert',
    'test_recording_events_alert',
    'run_test'
]

# ---------------- TEST RUNNER ----------------

def run_test(test_name: str, host: str, port: int, alert_manager=None, duration=30) -> bool:
    """Executes a specified test with given parameters and returns the test result."""
    if test_name == 'connectivity':
        return test_connectivity(host, port)
    elif test_name == 'api':
        return test_api_endpoints(host, port)
    elif test_name == 'alert':
        if alert_manager:
            return test_channel_watching_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for alert test")
            return False
    elif test_name in ['Disk-Space', 'ALERT_DISK_SPACE']:
        if alert_manager:
            return test_disk_space_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for disk space alert test")
            return False
    elif test_name in ['Channel-Watching', 'ALERT_CHANNEL_WATCHING']:
        if alert_manager:
            return test_channel_watching_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for channel watching alert test")
            return False
    elif test_name in ['VOD-Watching', 'ALERT_VOD_WATCHING']:
        if alert_manager:
            return test_vod_watching_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for VOD watching alert test")
            return False
    elif test_name in ['Recording-Events', 'ALERT_RECORDING_EVENTS']:
        if alert_manager:
            return test_recording_events_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for recording events alert test")
            return False
    elif test_name == 'event_stream':
        return test_event_stream(host, port, duration)
    else:
        log(f"Unknown test: {test_name}")
        return False