"""
Logging helper functions.
"""
import sys

def log(message):
    """Log a message to stdout and flush to ensure it's written immediately.
    
    Args:
        message (str): The message to log
    """
    print(message, flush=True)
    sys.stdout.flush()