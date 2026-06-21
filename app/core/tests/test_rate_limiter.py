"""Tests for the global notification rate limiter."""

import time

from core.notifications.rate_limiter import RateLimiter


class TestRateLimiterBasic:
    def test_allows_up_to_max(self):
        rl = RateLimiter(max_notifications=20, window_seconds=300)
        for i in range(20):
            assert rl.allow() is True, f"Notification {i + 1} should be allowed"

    def test_blocks_after_max(self):
        rl = RateLimiter(max_notifications=20, window_seconds=300)
        for _ in range(20):
            rl.allow()
        assert rl.allow() is False

    def test_50_rapid_only_20_delivered(self):
        rl = RateLimiter(max_notifications=20, window_seconds=300)
        allowed = sum(1 for _ in range(50) if rl.allow())
        assert allowed == 20

    def test_suppressed_count_tracked(self):
        rl = RateLimiter(max_notifications=5, window_seconds=300)
        for _ in range(5):
            rl.allow()
        for _ in range(10):
            rl.allow()
        assert rl.suppressed_count == 10


class TestRateLimiterWindowExpiry:
    def test_allows_after_window_expires(self):
        rl = RateLimiter(max_notifications=5, window_seconds=300)
        for _ in range(5):
            rl.allow()
        assert rl.allow() is False

        # Simulate all timestamps expiring
        now = time.time()
        rl._timestamps.clear()
        for i in range(5):
            rl._timestamps.append(now - 301)

        assert rl.allow() is True

    def test_sliding_window_partial_expiry(self):
        rl = RateLimiter(max_notifications=5, window_seconds=300)
        now = time.time()

        # 3 old timestamps (outside window) + 2 recent
        rl._timestamps.extend([now - 400, now - 350, now - 310])
        rl._timestamps.extend([now - 10, now - 5])

        # After evicting 3 old ones, only 2 remain, so 3 more should be allowed
        assert rl.allow() is True
        assert rl.allow() is True
        assert rl.allow() is True
        # Now at 5, next should be blocked
        assert rl.allow() is False

    def test_suppressed_count_resets_on_success(self):
        rl = RateLimiter(max_notifications=2, window_seconds=300)
        rl.allow()
        rl.allow()
        rl.allow()  # suppressed
        rl.allow()  # suppressed
        assert rl.suppressed_count == 2

        # Expire all timestamps
        now = time.time()
        rl._timestamps.clear()
        rl._timestamps.extend([now - 400, now - 400])

        rl.allow()  # should succeed and reset suppressed count
        assert rl.suppressed_count == 0


class TestRateLimiterConfigurable:
    def test_custom_limits(self):
        rl = RateLimiter(max_notifications=3, window_seconds=60)
        assert rl.allow() is True
        assert rl.allow() is True
        assert rl.allow() is True
        assert rl.allow() is False

    def test_large_window(self):
        rl = RateLimiter(max_notifications=100, window_seconds=3600)
        allowed = sum(1 for _ in range(150) if rl.allow())
        assert allowed == 100

    def test_reset_clears_state(self):
        rl = RateLimiter(max_notifications=2, window_seconds=300)
        rl.allow()
        rl.allow()
        assert rl.allow() is False
        rl.reset()
        assert rl.allow() is True
