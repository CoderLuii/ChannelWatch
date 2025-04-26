"""Logging system with file rotation and configurable verbosity levels."""
import os
import sys
import time
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Tuple, Dict, Any, cast

# LOG LEVELS
LOG_STANDARD = 1
LOG_VERBOSE = 2

# GLOBALS
log_level = LOG_STANDARD
log_file = None
log_handler = None

# SETUP
def setup_logging(config_path: str, retention_days: int = 7, test_mode: bool = False):
    """Configure file-based logging with daily rotation and retention policy."""
    global log_file, log_handler
    
    if not os.path.exists(config_path):
        try:
            os.makedirs(config_path)
        except:
            print(f"Unable to create config directory at {config_path}")
            return
            
    log_file = os.path.join(config_path, "channelwatch.log")
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('[%(asctime)s] %(message)s', 
                                 datefmt='%Y-%m-%d %H:%M:%S')
    
    log_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=retention_days
    )
    log_handler.setFormatter(formatter)
    root_logger.addHandler(log_handler)

# LEVEL MANAGEMENT
def set_log_level(level: int, test_mode: bool = False):
    """Update global logging verbosity level with optional test mode suppression."""
    global log_level
    log_level = level

# LOGGING
def log(message: str, level: int = LOG_STANDARD):
    """Output message to console and log file with timestamp and appropriate verbosity filtering."""
    if level <= log_level:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = "[CORE] "
        formatted_message = f"[{timestamp}] {prefix}{message}"
        print(formatted_message, flush=True)
        
        if log_handler:
            record = logging.LogRecord(
                 name='channelwatch',
                 level=logging.INFO,
                 pathname='', 
                 lineno=0,
                 msg=prefix + message, 
                 args=(), 
                 exc_info=None, 
                 func=''
            )
            log_handler.handle(record)