"""
Logging helper functions with file rotation.
"""
import os
import sys
import time
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

# Log levels
LOG_STANDARD = 1  # Essential operational information (user-facing, meaningful state changes)
LOG_VERBOSE = 2   # Detailed debugging information (developer-focused, implementation details)

# Global log level
log_level = LOG_STANDARD

# File logging configuration
log_file = None
log_handler = None

def setup_logging(config_path: str, retention_days: int = 7, test_mode: bool = False):
    """Set up file logging with rotation.
    
    Args:
        config_path: The path to the config directory
        retention_days: The number of days to keep log files (default: 7)
        test_mode: Whether we're running in test mode (suppresses setup messages)
    """
    global log_file, log_handler
    
    if not os.path.exists(config_path):
        try:
            os.makedirs(config_path)
        except:
            print(f"Unable to create config directory at {config_path}")
            return
            
    # Set up the log file path directly in the config directory
    log_file = os.path.join(config_path, "channelwatch.log")
    
    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Create a formatter
    formatter = logging.Formatter('[%(asctime)s] %(message)s', 
                                 datefmt='%Y-%m-%d %H:%M:%S')
    
    # Create a rotating file handler
    log_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=retention_days
    )
    log_handler.setFormatter(formatter)
    root_logger.addHandler(log_handler)
    
    if not test_mode:
        log(f"Log file: {log_file} (keeping {retention_days} days)")

def set_log_level(level: int, test_mode: bool = False):
    """Set the global log level.
    
    Args:
        level: The log level (1=standard, 2=verbose)
        test_mode: Whether we're running in test mode (suppresses setup messages)
    """
    global log_level
    log_level = level
    if not test_mode:
        log(f"Log level: {level} ({'Standard' if level == 1 else 'Verbose'})")

def log(message: str, level: int = LOG_STANDARD):
    """Log a message to stdout and to file if configured.
    
    Use LOG_STANDARD (1) for:
    - Application startup and shutdown
    - Connection status changes
    - Important operations (caching completed, features enabled)
    - User-visible events (channels being watched, alerts)
    - Errors that affect functionality
    
    Use LOG_VERBOSE (2) for:
    - Data processing details (parsing, conversions)
    - API request/response details
    - Internal state changes
    - Function entry/exit for key operations
    - Detailed explanations of decisions made by the code
    
    Args:
        message: The message to log
        level: The log level (default: LOG_STANDARD)
    """
    if level <= log_level:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        print(formatted_message, flush=True)
        
        # Also log to file if configured
        if log_handler:
            logging.info(message)