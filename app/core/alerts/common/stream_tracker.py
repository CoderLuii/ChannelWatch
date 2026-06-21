"""Tracks active streaming sessions and maintains accurate stream count information."""

import asyncio
import time
from typing import Dict, Any, Optional
import httpx
import os

from ...helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ...helpers.type_utils import ensure_str
from ...helpers.dvr_connection import build_dvr_base_url

# GLOBALS

CONFIG_PATH = os.getenv("CONFIG_PATH", "/config")

# STREAM TRACKER


class StreamTracker:
    """Tracks active streaming sessions and provides accurate count information."""

    # INITIALIZATION

    def __init__(self, dvr=None, host: Optional[str] = None, port: int = 8089):
        """Initializes the stream tracker with connection settings and state tracking."""
        if dvr is not None:
            self.base_url = dvr.base_url
            self.dvr_id = dvr.id
        else:
            self.base_url = build_dvr_base_url(ensure_str(host), port)
            self.dvr_id = "default"

        self.active_streams: Dict[str, Dict[str, Any]] = {}
        self.device_sessions: Dict[str, str] = {}
        self.lock = asyncio.Lock()

        self.last_count = 0

        self._stream_count_file = os.path.join(
            CONFIG_PATH, f"stream_count_{self.dvr_id}.txt"
        )
        self._write_stream_count(0)

    # FILE MANAGEMENT

    def _write_stream_count(self, count: int):
        """Safely writes the current stream count to the per-DVR file."""
        try:
            with open(self._stream_count_file, "w") as f:
                f.write(str(count))
        except Exception as e:
            log(
                f"StreamTracker: Error writing stream count to {self._stream_count_file}: {e}",
                level=LOG_STANDARD,
            )

    async def _write_stream_count_async(self, count: int) -> None:
        """Persist stream counts without blocking async event processing."""
        await asyncio.to_thread(self._write_stream_count, count)

    # SERVER STATUS

    def update_from_status(self) -> Optional[Dict[str, Any]]:
        """Gets current stream status from DVR server."""
        try:
            response = httpx.get(f"{self.base_url}/dvr", timeout=10)
            if response.status_code == 200:
                return response.json()
            else:
                log(
                    f"Failed to get stream status: HTTP {response.status_code}",
                    level=LOG_STANDARD,
                )
                return None
        except Exception as e:
            log(f"Error getting stream status: {e}", level=LOG_STANDARD)
            return None

    # DEVICE IDENTIFICATION

    def extract_device_name(self, activity_data: Any) -> Optional[str]:
        """Extracts device name from activity data using pattern matching."""
        if not activity_data:
            return None

        activity_str = str(activity_data)

        import re

        device_match = re.search(r"from\s+([^(:]+)", activity_str)
        if device_match:
            return device_match.group(1).strip()

        device_match = re.search(r"Device:\s*([^,]+)", activity_str)
        if device_match:
            return device_match.group(1).strip()

        return None

    # ACTIVITY PROCESSING

    async def process_activity(self, activity_data: Any, session_id: str) -> bool:
        count_changed = False
        new_count = 0
        async with self.lock:
            old_count = len(self.device_sessions)

            activity_str = str(activity_data).lower()

            is_watching = (
                "watching" in activity_str or "recording" in activity_str
            ) and "ch" in activity_str

            device_name = self.extract_device_name(activity_data)

            if is_watching and device_name:
                self.active_streams[session_id] = {
                    "activity": activity_data,
                    "device": device_name,
                    "last_seen": time.time(),
                }
                self.device_sessions[device_name] = session_id
            elif not is_watching:
                if session_id in self.active_streams:
                    session_data = self.active_streams[session_id]
                    device = session_data.get("device")
                    if device:
                        if (
                            device in self.device_sessions
                            and self.device_sessions[device] == session_id
                        ):
                            self.device_sessions.pop(device, None)
                    self.active_streams.pop(session_id, None)

            new_count = len(self.device_sessions)

            if old_count != new_count:
                count_changed = True
                self.last_count = new_count

        if count_changed:
            await self._write_stream_count_async(new_count)

        return count_changed

    # STREAM COUNT

    async def get_stream_count(self) -> int:
        async with self.lock:
            return len(self.device_sessions)

    async def get_stream_change_message(self) -> Optional[str]:
        count = await self.get_stream_count()
        if count != self.last_count:
            if count > self.last_count:
                return f"Total Streams: {count}"
            else:
                return f"Total Streams: {count}"
        return None

    # CLEANUP

    async def cleanup_stale_sessions(self, max_age: int = 300) -> None:
        removed_count = 0
        new_count = 0
        current_time = time.time()
        async with self.lock:
            stale_sessions = []
            devices_to_remove = []
            for session_id, session_data in list(self.active_streams.items()):
                if current_time - session_data["last_seen"] > max_age:
                    stale_sessions.append(session_id)
                    device = session_data.get("device")
                    if device and device in self.device_sessions:
                        if self.device_sessions[device] == session_id:
                            devices_to_remove.append(device)

            for session_id in stale_sessions:
                self.active_streams.pop(session_id, None)
                removed_count += 1

            for device in devices_to_remove:
                self.device_sessions.pop(device, None)

            new_count = len(self.device_sessions)

        if removed_count > 0:
            log(
                f"StreamTracker: Removed {removed_count} stale stream sessions",
                level=LOG_VERBOSE,
            )
            await self._write_stream_count_async(new_count)
