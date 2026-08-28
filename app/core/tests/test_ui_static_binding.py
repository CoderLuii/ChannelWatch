from __future__ import annotations

import importlib
import os
from pathlib import Path


def test_ui_backend_package_replaces_stale_image_static_path(monkeypatch):
    monkeypatch.setenv(
        "CHANNELWATCH_ACTIVE_STATIC_UI_DIR",
        "/app/ui/backend/static_ui",
    )
    monkeypatch.setenv("CW_STATIC_UI_DIR", "/app/ui/backend/static_ui")

    import ui.backend

    reloaded = importlib.reload(ui.backend)
    expected = Path(reloaded.__file__).resolve().parent / "static_ui"

    assert os.environ["CW_STATIC_UI_DIR"] == str(expected)
    assert reloaded._bind_selected_static_ui() == expected
