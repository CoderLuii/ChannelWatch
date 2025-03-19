"""
Alert system initialization.
"""
from channelwatch.helpers.logging import log

# Import alert modules
try:
    from channelwatch.alerts.channel_watching import ChannelWatchingAlert
except ImportError as e:
    log(f"Error importing ChannelWatchingAlert: {e}")
    ChannelWatchingAlert = None

# Alert class registry
ALERT_CLASSES = {
    "Channel-Watching": ChannelWatchingAlert
}

def get_alert_class(alert_type):
    """Get the alert class for a given alert type.
    
    Args:
        alert_type (str): The alert type
        
    Returns:
        class: The alert class, or None if not found
    """
    return ALERT_CLASSES.get(alert_type)