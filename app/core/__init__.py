"""
ChannelWatch - Channels DVR monitoring tool for real-time notifications.
"""

__version__ = "1.0.0"
__app_name__ = "ChannelWatch"


# This is intentionally the earliest bundle hook. Historical image launchers
# import the package before executing core.main, so activation failures are
# upgraded to coherent whole-container rollback before any application import
# can fail. The function is a guarded no-op on the v1.0.0 image and in tools.
from .runtime_launcher import (
    install_historical_launcher_bridge as _install_launcher_bridge,
)

_install_launcher_bridge()
del _install_launcher_bridge
