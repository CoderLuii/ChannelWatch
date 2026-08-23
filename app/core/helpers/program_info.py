"""
Program information provider for Channels DVR XMLTV API integration.
"""

import io
import xml.etree.ElementTree as ET
import time
import threading

import httpx
import pytz
from datetime import datetime
from typing import Dict, Any, Optional

from .logging import log, LOG_STANDARD, LOG_VERBOSE
from .type_utils import ensure_str
from .dvr_connection import build_dvr_base_url
from .dvr_target import build_safe_dvr_request


MAX_XMLTV_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_XMLTV_ELEMENTS = 500_000
MAX_XMLTV_DEPTH = 16


# PROGRAM INFO
class ProgramInfoProvider:
    """Manages program guide data retrieval and caching from Channels DVR XMLTV API."""

    def __init__(
        self,
        host: str = "",
        port: int = 8089,
        timezone: str = "America/Los_Angeles",
        cache_ttl: int = 3600,
        dvr=None,
    ):
        """Initializes program info provider with server connection and caching parameters."""
        if dvr is not None:
            self.host = dvr.host
            self.port = dvr.port
            self.base_url = dvr.base_url
        else:
            self.host = host
            self.port = port
            self.base_url = build_dvr_base_url(host, port)
        self.cache_ttl = cache_ttl
        self._allow_test_loopback = bool(
            getattr(dvr, "test_only_allow_loopback", False)
        )
        self.timezone = timezone
        self.local_tz = pytz.timezone(timezone)

        self.program_cache = {}
        self.channel_map = {}
        self.cache_timestamp = 0
        self.cache_lock = threading.Lock()

    # DATA FETCHING
    def _fetch_xmltv_data(self, duration: int = 86400) -> Optional[bytes]:
        """Retrieve bounded XMLTV program guide data from Channels DVR."""
        try:
            request = build_safe_dvr_request(
                self.host,
                self.port,
                "/devices/ANY/guide/xmltv",
                allow_loopback=self._allow_test_loopback,
            )
            if request is None:
                raise httpx.ConnectError("DVR target did not pass safety validation")
            with httpx.stream(
                "GET",
                request.url,
                headers={"Host": request.host_header},
                timeout=30,
                trust_env=False,
            ) as response:
                if response.status_code != 200:
                    log(
                        f"Failed to fetch XMLTV data: HTTP {response.status_code}",
                        level=LOG_STANDARD,
                    )
                    return None

                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_bytes = int(content_length)
                    except ValueError:
                        declared_bytes = -1
                    if declared_bytes > MAX_XMLTV_RESPONSE_BYTES:
                        log(
                            "Rejected XMLTV data because the declared response size "
                            f"exceeds {MAX_XMLTV_RESPONSE_BYTES} bytes",
                            level=LOG_STANDARD,
                        )
                        return None

                chunks: list[bytes] = []
                total_bytes = 0
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_XMLTV_RESPONSE_BYTES:
                        log(
                            "Rejected XMLTV data because the streamed response size "
                            f"exceeds {MAX_XMLTV_RESPONSE_BYTES} bytes",
                            level=LOG_STANDARD,
                        )
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception as e:
            log(f"Error fetching XMLTV data: {e}", level=LOG_STANDARD)
            return None

    def _parse_xmltv_data(self, xml_data: str | bytes) -> bool:
        """Stream a bounded XMLTV document into replacement guide caches."""
        next_program_cache: dict[str, list[dict[str, Any]]] = {}
        next_channel_map: dict[str, str] = {}
        depth = 0
        element_count = 0
        root_element: Optional[ET.Element] = None

        try:
            source = (
                io.BytesIO(xml_data)
                if isinstance(xml_data, bytes)
                else io.StringIO(xml_data)
            )
            for event, element in ET.iterparse(source, events=("start", "end")):
                if event == "start":
                    depth += 1
                    element_count += 1
                    if root_element is None:
                        root_element = element
                    if depth > MAX_XMLTV_DEPTH:
                        raise ValueError(
                            f"XMLTV nesting exceeds the limit of {MAX_XMLTV_DEPTH}"
                        )
                    if element_count > MAX_XMLTV_ELEMENTS:
                        raise ValueError(
                            f"XMLTV element count exceeds the limit of {MAX_XMLTV_ELEMENTS}"
                        )
                    continue

                tag = element.tag.rsplit("}", 1)[-1]
                if depth == 2 and tag == "channel":
                    channel_id = element.get("id")
                    lcn = next(
                        (
                            child
                            for child in element
                            if child.tag.rsplit("}", 1)[-1] == "lcn"
                        ),
                        None,
                    )
                    if channel_id and lcn is not None and lcn.text:
                        next_channel_map[lcn.text] = channel_id
                elif depth == 2 and tag == "programme":
                    channel_id = element.get("channel")
                    start_attr = element.get("start")
                    stop_attr = element.get("stop")
                    start_time = (
                        self._parse_xmltv_time(ensure_str(start_attr))
                        if start_attr
                        else None
                    )
                    stop_time = (
                        self._parse_xmltv_time(ensure_str(stop_attr))
                        if stop_attr
                        else None
                    )
                    if channel_id and start_time and stop_time:
                        children = {
                            child.tag.rsplit("}", 1)[-1]: child for child in element
                        }
                        title = children.get("title")
                        description = children.get("desc")
                        icon = children.get("icon")
                        next_program_cache.setdefault(channel_id, []).append(
                            {
                                "channel_id": channel_id,
                                "start_time": start_time,
                                "stop_time": stop_time,
                                "title": (
                                    title.text
                                    if title is not None and title.text
                                    else "Unknown Program"
                                ),
                                "description": (
                                    description.text
                                    if description is not None and description.text
                                    else ""
                                ),
                                "icon_url": icon.get("src") if icon is not None else None,
                            }
                        )

                if depth == 2:
                    element.clear()
                    if root_element is not None:
                        root_element.clear()
                depth -= 1

            self.program_cache = next_program_cache
            self.channel_map = next_channel_map
            return True
        except Exception as e:
            log(f"Error parsing XMLTV data: {e}", level=LOG_STANDARD)
            return False

    def _parse_xmltv_time(self, time_str: str) -> Optional[int]:
        """Converts XMLTV time format to Unix timestamp in local timezone."""
        try:
            parts = time_str.split(" ")
            time_part = parts[0]
            dt_utc = datetime.strptime(time_part, "%Y%m%d%H%M%S")
            from pytz import UTC

            dt_utc = UTC.localize(dt_utc)
            dt_local = dt_utc.astimezone(self.local_tz)

            return int(dt_local.timestamp())
        except Exception as e:
            log(f"Error parsing XMLTV time: {e}", level=LOG_VERBOSE)
            return None

    # CACHE MANAGEMENT
    def cache_program_data(self) -> int:
        """Updates program guide cache with fresh data from XMLTV API. Returns program count."""
        with self.cache_lock:
            current_time = time.time()
            if (
                self.program_cache
                and (current_time - self.cache_timestamp) < self.cache_ttl
            ):
                return sum(len(programs) for programs in self.program_cache.values())

            xml_data = self._fetch_xmltv_data()
            if not xml_data:
                return 0

            success = self._parse_xmltv_data(xml_data)

            if success:
                self.cache_timestamp = current_time
                program_count = sum(
                    len(programs) for programs in self.program_cache.values()
                )

                return program_count

            return 0

    # PROGRAM LOOKUP
    def get_current_program(
        self, channel_number: str, timestamp: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Retrieves current program information for specified channel and time."""

        if not self.cache_program_data():
            log("Failed to cache program data", level=LOG_VERBOSE)
            return None

        if timestamp is None:
            timestamp = int(time.time())

        channel_id = self.channel_map.get(channel_number)
        if not channel_id:
            log(
                f"Channel ID not found for channel number: {channel_number}",
                level=LOG_VERBOSE,
            )
            return None

        programs = self.program_cache.get(channel_id, [])

        for program in programs:
            start_time = program["start_time"]
            stop_time = program["stop_time"]

            if start_time <= timestamp < stop_time:
                return program

        return None
