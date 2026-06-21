"""Global rate limiter to prevent notification storms regardless of alert type."""

import time
import threading
from collections import deque

from ..helpers.logging import log, LOG_STANDARD


class RateLimiter:
    """Sliding window rate limiter for outbound notifications.

    Tracks notification timestamps in a deque and rejects new notifications
    when the count within the window exceeds the configured maximum.
    """

    def __init__(self, max_notifications: int = 20, window_seconds: int = 300):
        self.max_notifications = max_notifications
        self.window_seconds = window_seconds
        self._timestamps: deque = deque()
        self._suppressed_count = 0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Check whether a notification is allowed under the current rate limit.

        Returns True if the notification should proceed, False if it should
        be suppressed.
        """
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds

            # Evict timestamps outside the window
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) >= self.max_notifications:
                self._suppressed_count += 1
                if self._suppressed_count == 1 or self._suppressed_count % 10 == 0:
                    log(
                        f"Rate limit hit: {self._suppressed_count} notification(s) suppressed "
                        f"(max {self.max_notifications} per {self.window_seconds}s)",
                        level=LOG_STANDARD,
                    )
                return False

            self._timestamps.append(now)
            if self._suppressed_count > 0:
                log(
                    f"Rate limit cleared. {self._suppressed_count} notification(s) were suppressed "
                    f"during the previous window.",
                    level=LOG_STANDARD,
                )
                self._suppressed_count = 0
            return True

    @property
    def suppressed_count(self) -> int:
        """Number of notifications suppressed since the last successful send."""
        with self._lock:
            return self._suppressed_count

    def reset(self):
        """Clear all state. Useful for testing or reconfiguration."""
        with self._lock:
            self._timestamps.clear()
            self._suppressed_count = 0
