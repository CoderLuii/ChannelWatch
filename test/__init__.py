"""
Test framework for ChannelWatch.
"""
from .connectivity.test_server import test_connectivity, test_api_endpoints, test_event_stream
from .alerts.test_channel_watching import test_channel_watching_alert
from .alerts.test_disk_space import test_disk_space_alert
from .alerts.test_vod_watching import test_vod_watching_alert
from ..helpers.logging import log

__all__ = [
    'test_connectivity', 
    'test_api_endpoints', 
    'test_event_stream',
    'test_channel_watching_alert',
    'test_disk_space_alert',
    'test_vod_watching_alert',
    'run_test'
]

def run_test(test_name: str, host: str, port: int, alert_manager=None, duration=30) -> bool:
    """
    Run a specific test by name.
    
    Args:
        test_name: Name of the test to run
        host: The server host
        port: The server port
        alert_manager: Alert manager instance (optional)
        duration: Duration for timed tests (optional)
        
    Returns:
        bool: True if test successful, False otherwise
    """
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
    elif test_name == 'Disk-Space':
        if alert_manager:
            return test_disk_space_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for disk space alert test")
            return False
    elif test_name == 'Channel-Watching':
        if alert_manager:
            return test_channel_watching_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for channel watching alert test")
            return False
    elif test_name == 'VOD-Watching':
        if alert_manager:
            return test_vod_watching_alert(host, port, alert_manager)
        else:
            log("ERROR: Alert manager required for VOD watching alert test")
            return False
    elif test_name == 'event_stream':
        return test_event_stream(host, port, duration)
    else:
        log(f"Unknown test: {test_name}")
        return False