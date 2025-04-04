"""
RecordingInfoProvider compatibility wrapper around JobInfoProvider.

This module is maintained for backward compatibility.
New code should use JobInfoProvider from job_info module instead.
"""
import warnings
from .job_info import JobInfoProvider

# Issue a deprecation warning
warnings.warn(
    "RecordingInfoProvider is deprecated, use JobInfoProvider instead",
    DeprecationWarning,
    stacklevel=2
)

class RecordingInfoProvider(JobInfoProvider):
    """Deprecated compatibility wrapper around JobInfoProvider.
    
    This class is maintained for backward compatibility.
    New code should use JobInfoProvider from job_info module instead.
    """
    
    def __init__(self, host: str, port: int, cache_ttl: int = 86400):
        """Initialize the recording info provider.
        
        Args:
            host: The Channels DVR server host address
            port: The Channels DVR server port number
            cache_ttl: Cache time-to-live in seconds (default: 24 hours)
        """
        super().__init__(host, port, cache_ttl) 