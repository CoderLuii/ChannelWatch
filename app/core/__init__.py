"""
ChannelWatch - Channels DVR monitoring tool for real-time notifications.
"""

from __future__ import annotations

import sys as _sys


_sys.dont_write_bytecode = True
_dependency_worker = getattr(
    _sys, "_channelwatch_runtime_worker_validated", None
) is not None
del _sys

import os
from pathlib import Path

__version__ = "1.5.0"
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


def _package_is_image_owned() -> bool:
    try:
        package_app = Path(__file__).resolve().parent.parent
        image_app = Path(
            os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app")
        ).resolve()
    except OSError:
        return False
    return package_app == image_app


if not _dependency_worker:
    _reset_sqlmodel_registry_for_selected_bundle()


# This is intentionally the earliest bundle hook. Historical image launchers
# import the package before executing core.main, so activation failures are
# upgraded to coherent whole-container rollback before any application import
# can fail. The function is a guarded no-op on the v1.0.0+ image and in tools.
if not _dependency_worker and not _package_is_image_owned():
    from .runtime_launcher import (
        install_historical_launcher_bridge as _install_launcher_bridge,
    )

    _install_launcher_bridge()
    del _install_launcher_bridge
del _package_is_image_owned
del _dependency_worker
