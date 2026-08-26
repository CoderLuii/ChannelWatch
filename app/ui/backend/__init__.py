"""ChannelWatch UI backend package initialization."""

# Uvicorn imports this package before compiling/executing ``main.py``. Install
# the same guarded bridge as the core package so even an immediate UI import or
# syntax failure is handled by the hardened v0.9.19 activation transaction.
from core.runtime_launcher import (
    install_historical_launcher_bridge as _install_launcher_bridge,
)

_install_launcher_bridge()
del _install_launcher_bridge
