"""
Alert system initialization.
"""
from typing import Dict, Any, Optional, Type
from ..helpers.logging import log
from .channel_watching import ChannelWatchingAlert

# Alert class registry
ALERT_CLASSES = {
    "Channel-Watching": ChannelWatchingAlert,
    # Add new alert types here
}

def get_alert_class(alert_type: str) -> Optional[Type]:
    """Get the alert class for a given alert type.
    
    Args:
        alert_type: The alert type
        
    Returns:
        class: The alert class, or None if not found
    """
    return ALERT_CLASSES.get(alert_type)

def register_alert_class(alert_type: str, alert_class: Type) -> bool:
    """Register a new alert class.
    
    Args:
        alert_type: The alert type
        alert_class: The alert class
        
    Returns:
        bool: True if the alert class was registered, False otherwise
    """
    if alert_type in ALERT_CLASSES:
        log(f"Alert type {alert_type} already registered")
        return False
    
    ALERT_CLASSES[alert_type] = alert_class
    log(f"Registered alert type: {alert_type}")
    return True

def get_available_alert_types() -> Dict[str, str]:
    """Get a dictionary of available alert types and their descriptions.
    
    Returns:
        dict: Dictionary mapping alert types to descriptions
    """
    result = {}
    for alert_type, alert_class in ALERT_CLASSES.items():
        if alert_class:
            result[alert_type] = getattr(alert_class, "DESCRIPTION", "No description")
    
    return result