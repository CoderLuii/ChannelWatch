import sys
from typing import Optional

from core.notifications.providers.base import NotificationProvider


class ConsoleProvider(NotificationProvider):
    PROVIDER_TYPE = "Console"
    DESCRIPTION = "Writes notifications to stdout (example plugin)"

    def __init__(self) -> None:
        self._configured = False

    def initialize(self, **kwargs) -> bool:
        self._configured = True
        return True

    def is_configured(self) -> bool:
        return self._configured

    def send_notification(
        self,
        title: str,
        message: str,
        image_url: Optional[str] = None,
        **kwargs,
    ) -> bool:
        try:
            dvr_id = kwargs.get("dvr_id", "")
            event_type = kwargs.get("event_type", "")
            prefix = f"[{dvr_id}/{event_type}] " if dvr_id and event_type else ""
            print(
                f"[ConsoleProvider] {prefix}{title}: {message}",
                file=sys.stdout,
                flush=True,
            )
            return True
        except Exception:
            return False
