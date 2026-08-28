"""ChannelWatch UI backend package initialization."""

from __future__ import annotations

import os
from pathlib import Path


def _bind_selected_static_ui() -> Path:
    """Bind static assets to this exact selected UI backend package.

    Protocol-3 launchers shipped before the static-runtime fix can inherit an
    image-owned ``CHANNELWATCH_ACTIVE_STATIC_UI_DIR`` value from Supervisor.
    They install the selected bundle on ``sys.path`` correctly, but leave
    ``CW_STATIC_UI_DIR`` pointing at the old image. Uvicorn imports this package
    before ``ui.backend.main`` resolves its static directory, making this the
    earliest app-bundle bridge that can keep backend and frontend generations
    together on already-published images.
    """

    static_ui_dir = Path(__file__).resolve().parent / "static_ui"
    os.environ["CW_STATIC_UI_DIR"] = str(static_ui_dir)
    return static_ui_dir


_bind_selected_static_ui()

# Uvicorn imports this package before compiling/executing ``main.py``. Install
# the same guarded bridge as the core package so even an immediate UI import or
# syntax failure is handled by the hardened v0.9.19 activation transaction.
from core.runtime_launcher import (
    install_historical_launcher_bridge as _install_launcher_bridge,
)

_install_launcher_bridge()
del _install_launcher_bridge
