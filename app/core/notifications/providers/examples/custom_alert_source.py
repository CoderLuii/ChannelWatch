"""v1.1+ PREVIEW STUB. NOT LOADED by the v0.9 plugin runtime.

The notification plugin loader only registers NotificationProvider subclasses.
This file demonstrates the planned AlertSource interface for community feedback only.
"""

from pathlib import Path
import sys
from collections.abc import Iterable
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.notifications.providers.base import AlertSource


__plugin_status__ = "preview-v1.1-not-loaded"


class CustomAlertSource(AlertSource):
    SOURCE_TYPE = "CustomAlertSource"
    DESCRIPTION = "Preview alert-source plugin backed by a mock event stream"

    def __init__(self, event_stream: Optional[Iterable[dict[str, Any]]] = None) -> None:
        self._event_stream = list(event_stream or self._default_event_stream())
        self._callback: Optional[Callable[[dict[str, Any]], None]] = None
        self._subscribed = False

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> bool:
        self._callback = callback
        self._subscribed = True
        for event in self._event_stream:
            self.emit_event(event)
        return True

    def emit_event(self, event: dict[str, Any]) -> bool:
        if not self._subscribed or self._callback is None:
            return False

        self._callback(event)
        return True

    def unsubscribe(self) -> bool:
        self._callback = None
        self._subscribed = False
        return True

    @staticmethod
    def _default_event_stream() -> list[dict[str, Any]]:
        return [
            {
                "event_type": "mock_alert",
                "title": "Preview alert-source event",
                "message": "CustomAlertSource emitted a mock event",
            }
        ]


def _print_event(event: dict[str, Any]) -> None:
    print(
        f"[{event.get('event_type', 'unknown')}] {event.get('title', '')}: {event.get('message', '')}"
    )


if __name__ == "__main__":
    source = CustomAlertSource()
    source.subscribe(_print_event)
    source.unsubscribe()
