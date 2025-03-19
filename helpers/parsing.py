"""
Log parsing helper functions.
"""
import re

def match_pattern(pattern, line):
    """Check if a line matches a pattern.
    
    Args:
        pattern (str): Regex pattern to match
        line (str): The log line to check
        
    Returns:
        bool: True if matched, False otherwise
    """
    return re.search(pattern, line, re.IGNORECASE) is not None

def simplify_line(line):
    """Create a simplified version of a log line (for deduplication).
    
    Args:
        line (str): The log line to simplify
        
    Returns:
        str: Simplified line
    """
    return re.sub(r'\d+', 'X', line)  # Replace numbers with X