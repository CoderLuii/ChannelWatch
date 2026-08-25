import os
import sys

import pytest


os.environ.setdefault(
    "CHANNELWATCH_SECRET_STORAGE_KEY",
    "channelwatch-test-secret-storage-key-0001",
)


def _clear_ui_rate_limiter() -> None:
    backend_main = sys.modules.get("ui.backend.main")
    limiter = getattr(backend_main, "rate_limiter", None)
    if limiter is None:
        return
    with limiter._lock:
        limiter._requests.clear()


@pytest.fixture(autouse=True)
def isolate_ui_rate_limiter_between_tests():
    """Prevent unrelated API modules from sharing one in-memory client quota."""

    _clear_ui_rate_limiter()
    yield
    _clear_ui_rate_limiter()
