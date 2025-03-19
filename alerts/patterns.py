"""
Alert patterns for matching log lines.
"""

# Alert patterns to look for in logs
ALERT_PATTERNS = {
    "Channel-Watching": [
        r"Opened connection to",
        r"Starting live str",
        r"Probed live stream"
    ]
}