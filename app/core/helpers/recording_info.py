"""
Legacy compatibility wrapper for recording information provider.
"""

import warnings
from .job_info import JobInfoProvider


# LEGACY WRAPPER
class RecordingInfoProvider(JobInfoProvider):
    """Legacy compatibility class for recording information retrieval."""

    def __init__(self, host: str, port: int, cache_ttl: int = 86400):
        """Initializes recording info provider with server connection parameters."""
        warnings.warn(
            "RecordingInfoProvider is deprecated, use JobInfoProvider instead",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(host, port, cache_ttl)
