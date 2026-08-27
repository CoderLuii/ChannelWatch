"""
ChannelWatch - Channels DVR monitoring tool for real-time notifications.
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "1.0.2"
__app_name__ = "ChannelWatch"


def _reset_sqlmodel_registry_for_selected_bundle() -> bool:
    """Discard image-owned model registrations before a bundle imports models.

    The protocol-3 launcher resolves the active runtime through the immutable
    image's Update Center. That lookup can import image-owned storage models
    before the launcher installs the selected bundle path. The launcher then
    evicts those Python modules, but SQLModel's process-global registry still
    retains their tables and mappers. Reset only for a selected runtime under
    the trusted releases directory so the bundle can register its own models
    without changing ordinary image startup or accepting arbitrary paths.
    """

    configured_app = os.environ.get("CHANNELWATCH_APP_DIR", "").strip()
    if not configured_app:
        return False
    try:
        app_dir = Path(configured_app).resolve()
        image_dir = Path(
            os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app")
        ).resolve()
        releases_dir = (
            Path(os.environ.get("CONFIG_PATH", "/config"))
            / "channelwatch-runtime"
            / "releases"
        ).resolve()
        app_dir.relative_to(releases_dir)
    except (OSError, ValueError):
        return False
    if app_dir == image_dir:
        return False

    from sqlmodel import SQLModel

    registry = getattr(SQLModel, "_sa_registry", None)
    if registry is not None and hasattr(registry, "dispose"):
        registry.dispose()
    SQLModel.metadata.clear()
    return True


_reset_sqlmodel_registry_for_selected_bundle()


# This is intentionally the earliest bundle hook. Historical image launchers
# import the package before executing core.main, so activation failures are
# upgraded to coherent whole-container rollback before any application import
# can fail. The function is a guarded no-op on the v1.0.0+ image and in tools.
from .runtime_launcher import (
    install_historical_launcher_bridge as _install_launcher_bridge,
)

_install_launcher_bridge()
del _install_launcher_bridge
