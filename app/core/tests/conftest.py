import os
import shlex
import sys
from pathlib import Path

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


@pytest.fixture
def immutable_image_python(tmp_path: Path) -> Path:
    """Provide an immutable test-owned launcher for the current interpreter."""

    interpreter = Path(sys.executable).resolve(strict=True)
    library_dir = interpreter.parent.parent / "lib"
    launcher = tmp_path / "image-python"
    launcher.write_text(
        "#!/bin/sh\n"
        f"export LD_LIBRARY_PATH={shlex.quote(str(library_dir))}\n"
        f"exec {shlex.quote(str(interpreter))} \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher
