"""Monitors and processes Channels DVR server events for alert generation."""

import asyncio
import json
import threading
import time
import httpx
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Any, Optional

from ..helpers.logging import log, LOG_STANDARD, LOG_VERBOSE
from ..helpers.dvr_connection import build_dvr_base_url
from ..helpers.dvr_target import build_safe_dvr_request

MAX_RETRY_AFTER_DELAY_SECONDS = 60.0
RECONNECT_SLEEP_SLICE_SECONDS = 1.0


# CORE MONITOR
class EventMonitor:
    """Monitors Channels DVR event stream and dispatches events to alert handlers."""

    def __init__(
        self,
        host: str = "",
        port: int = 8089,
        alert_manager=None,
        server_version: Optional[str] = None,
        dvr=None,
        validation_only: bool = False,
    ):
        """Initializes event monitor with server connection parameters and alert system."""
        # Configuration
        self.dvr = dvr
        if dvr is not None:
            self.host = dvr.host
            self.port = dvr.port
            self.base_url = dvr.base_url
            self.dvr_name = dvr.name
        else:
            self.host = host
            self.port = port
            self.base_url = build_dvr_base_url(host, port)
            self.dvr_name = host
        self.alert_manager = alert_manager
        self.server_version = server_version
        self.event_url = f"{self.base_url}/events"
        self.validation_only = bool(validation_only)
        self._allow_test_loopback = bool(
            getattr(dvr, "test_only_allow_loopback", False)
        )

        # State
        self.running = False
        self.connected = False
        self.monitoring_thread = None
        self.last_message_time = 0
        self.last_event_at = 0.0
        self.last_freshness_at = 0.0
        self.last_freshness_source: Optional[str] = None
        self.watchdog = None
        self._monitor_loop: Optional[asyncio.AbstractEventLoop] = None
        self._active_client: Optional[httpx.AsyncClient] = None
        self._active_response: Optional[httpx.Response] = None
        self._retry_after_delay: Optional[float] = None
        self._attempt_healthy = False
        self._state_loaded = False
        # A monitor attempt is single-use.  The worker thread and the asyncio
        # supervisor can race during startup, so ``running`` alone cannot carry
        # stop intent: a worker that has entered start_monitoring() but has not
        # published ``running = True`` could otherwise overwrite a concurrent
        # stop request.  Serialize the transition and keep stop intent sticky.
        self._lifecycle_lock = threading.Lock()
        self._start_claimed = False
        self._stop_requested = False

        self.alerts_paused: bool = False
        self._connection_status: str = "unknown"
        self._last_seen: float = 0.0

        # Keep-alive
        self.ping_interval = 15
        self.last_ping = 0
        self.ping_timeout = 60

        # Logging
        self.keep_alive_log_interval = 300
        self.last_keep_alive_log = 0
        self.keep_alive_success_streak = 0

        # Statistics
        current_time = time.time()
        self.stats = {
            "start_time": current_time,
            "total_events": 0,
            "previous_total_events": 0,
            "alert_events": 0,
            "filtered_events": 0,
            "error_events": 0,
            "last_status_update": current_time,
            "status_update_interval": 300,
        }

    # OFFLINE STATUS PROPERTY
    @property
    def dvr_status(self) -> Dict[str, Any]:
        """Returns current DVR connection status and last-seen timestamp."""
        return {
            "status": self._connection_status,
            "last_seen": self._last_seen,
        }

    # MONITORING
    def start_monitoring(self):
        """Run event monitoring until shutdown is requested."""
        with self._lifecycle_lock:
            if self._stop_requested or self._start_claimed:
                return
            self._start_claimed = True
            self.running = True
            self._state_loaded = False
        try:
            self._monitor_events_loop()
        except KeyboardInterrupt:
            log("KeyboardInterrupt received, shutting down...")
        finally:
            # ``running`` is authoritative readiness/watchdog state.  The
            # underlying loop may return unexpectedly without calling
            # stop_monitoring(), so never leave a terminated monitor marked
            # active.
            with self._lifecycle_lock:
                self.running = False
            log("Monitoring loop finished.")

    def stop_monitoring(self) -> None:
        """Request monitoring shutdown and wake an idle SSE read if one is active."""
        with self._lifecycle_lock:
            first_request = not self._stop_requested
            self._stop_requested = True
            self.running = False
            loop = self._monitor_loop
        if not first_request:
            return
        if loop is None or not loop.is_running():
            return

        try:
            asyncio.run_coroutine_threadsafe(self._close_active_stream(), loop)
        except RuntimeError:
            pass

    async def _close_active_stream(self) -> None:
        for label, resource in (
            ("response", self._active_response),
            ("client", self._active_client),
        ):
            if resource is None:
                continue
            try:
                await resource.aclose()
            except Exception as exc:
                if self.running:
                    log(
                        f"[{self.dvr_name}] Error closing active SSE {label}: {exc}",
                        LOG_VERBOSE,
                    )

    # CONNECTION
    def _monitor_events_loop(self):
        """Manages continuous connection to event stream with reconnection logic.

        Backoff starts at 1 s and doubles each retry up to 60 s (T20b).
        Sets alerts_paused and dvr_status after every attempt.
        """
        reconnect_delay = (
            1  # T20b: was 5; must start at 1 so sequence is [1,2,4,8,16,32,60]
        )
        max_reconnect_delay = 60

        while self.running:
            self._attempt_healthy = False
            try:
                self._monitor_events()
                if self.connected:
                    self.alerts_paused = False
                    self._connection_status = "online"
                if self.running:
                    raise ConnectionError("DVR event stream ended unexpectedly")
            except Exception as e:
                if not self.running:
                    break
                log(f"[{self.dvr_name}] Connection error: {e}")
                self.alerts_paused = True
                self._connection_status = "offline"
                self._last_seen = time.time()

            if not self.running:
                break

            retry_after_delay = self._consume_retry_after_delay()
            if self._attempt_healthy:
                reconnect_delay = 1
            sleep_delay = (
                retry_after_delay if retry_after_delay is not None else reconnect_delay
            )
            log(f"[{self.dvr_name}] Reconnecting in {sleep_delay}s")
            self._sleep_interruptibly(sleep_delay)

            if retry_after_delay is None:
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    def _monitor_events(self):
        asyncio.run(self._async_monitor_events())

    def _consume_retry_after_delay(self) -> Optional[float]:
        delay = self._retry_after_delay
        self._retry_after_delay = None
        return delay

    def _sleep_interruptibly(self, delay: float) -> None:
        deadline = time.monotonic() + max(0.0, delay)
        while self.running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(RECONNECT_SLEEP_SLICE_SECONDS, remaining))

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        raw = value.strip()
        try:
            seconds = float(raw)
            return min(MAX_RETRY_AFTER_DELAY_SECONDS, max(0.0, seconds))
        except ValueError:
            pass

        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return min(
            MAX_RETRY_AFTER_DELAY_SECONDS,
            max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds()),
        )

    def _record_retry_after_backoff(self, header_value: Optional[str]) -> bool:
        retry_after = self._parse_retry_after(header_value)
        if retry_after is None:
            return False
        self._retry_after_delay = retry_after
        log(
            f"[{self.dvr_name}] DVR API rate-limited; backing off {retry_after:g} seconds",
            level=LOG_STANDARD,
        )
        return True

    def _mark_fresh(self, source: str) -> None:
        current_time = time.time()
        self._attempt_healthy = True
        self.last_freshness_at = current_time
        self.last_freshness_source = source
        if source == "event":
            self.last_event_at = current_time
        if self.watchdog is not None:
            self.watchdog.mark_fresh(self, source, timestamp=current_time)

    async def _async_monitor_events(self):
        self._monitor_loop = asyncio.get_running_loop()
        if self.alert_manager is None and not self.validation_only:
            raise RuntimeError(
                "EventMonitor requires an alert manager before monitoring events"
            )

        safe_stream = await asyncio.to_thread(
            build_safe_dvr_request,
            self.host,
            self.port,
            "/dvr/events/subscribe",
            allow_loopback=self._allow_test_loopback,
        )
        if safe_stream is None:
            raise ConnectionError("DVR target did not pass safety validation")
        url = safe_stream.url
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Host": safe_stream.host_header,
        }
        timeout = httpx.Timeout(connect=10.0, read=None, write=None, pool=None)

        if not self.validation_only and not self._state_loaded:
            await self.alert_manager.load_all_state()
            self._state_loaded = True

        background_tasks = []
        if not self.validation_only:
            background_tasks = list(self.alert_manager.create_background_tasks())
            for alert in self.alert_manager.alert_instances.values():
                if hasattr(alert, "create_background_tasks"):
                    background_tasks.extend(alert.create_background_tasks())

        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                self._active_client = client
                async with client.stream("GET", url, headers=headers) as response:
                    self._active_response = response
                    if response.status_code != 200:
                        if response.status_code == 429:
                            self._record_retry_after_backoff(
                                response.headers.get("Retry-After")
                            )
                        self.connected = False
                        raise ConnectionError(f"HTTP {response.status_code}")

                    self.connected = True
                    self.alerts_paused = False
                    self._connection_status = "online"
                    self._last_seen = time.time()
                    log(f"[{self.dvr_name}] Connected to event stream")

                    keep_alive_task = asyncio.create_task(
                        self._async_keep_alive(client)
                    )

                    try:
                        lines = response.aiter_lines()
                        while self.running:
                            try:
                                line = await lines.__anext__()
                            except StopAsyncIteration:
                                break
                            if not self.running:
                                break
                            if not line:
                                continue
                            log(f"Event: {line}", level=LOG_VERBOSE)
                            await self._process_event_line(line)
                    except httpx.ReadError:
                        if self.running:
                            raise
                    finally:
                        keep_alive_task.cancel()
                        try:
                            await keep_alive_task
                        except asyncio.CancelledError:
                            pass
                        self.connected = False
                        log(f"[{self.dvr_name}] Connection closed")
                    if self.running:
                        raise ConnectionError("DVR event stream ended unexpectedly")
                    self._active_response = None
                self._active_client = None
        finally:
            self._active_response = None
            self._active_client = None
            for task in background_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if not self.validation_only:
                await self.alert_manager.save_all_state()
            if not self.validation_only and not self.running:
                notification_manager = getattr(
                    self.alert_manager, "notification_manager", None
                )
                shutdown_queue = getattr(
                    type(notification_manager), "shutdown_delivery_queue", None
                )
                if callable(shutdown_queue):
                    await asyncio.to_thread(
                        shutdown_queue,
                        notification_manager,
                        drain=False,
                        timeout=1.0,
                    )

    async def _async_keep_alive(self, client: httpx.AsyncClient):
        while self.running and self.connected:
            try:
                await asyncio.sleep(self.ping_interval)

                if not self.connected:
                    break

                current_time = time.time()
                safe_status = await asyncio.to_thread(
                    build_safe_dvr_request,
                    self.host,
                    self.port,
                    "/status",
                    allow_loopback=self._allow_test_loopback,
                )
                if safe_status is None:
                    raise httpx.ConnectError("DVR target did not pass safety validation")
                response = await client.get(
                    safe_status.url,
                    headers={"Host": safe_status.host_header},
                    timeout=5,
                )

                should_log = (
                    self.last_keep_alive_log == 0
                    or (current_time - self.last_keep_alive_log)
                    >= self.keep_alive_log_interval
                    or response.status_code != 200
                )

                if response.status_code == 200:
                    self.keep_alive_success_streak += 1
                    self._connection_status = "online"
                    self._last_seen = current_time
                    self._mark_fresh("poll")
                    if should_log:
                        if self.keep_alive_success_streak > 10:
                            log(
                                f"Connection healthy: {self.keep_alive_success_streak} consecutive successful pings",
                                level=LOG_VERBOSE,
                            )
                        else:
                            log("Keep-alive ping successful", level=LOG_VERBOSE)
                        self.last_keep_alive_log = current_time
                else:
                    log(
                        f"[{self.dvr_name}] Keep-alive ping failed: HTTP {response.status_code}",
                        level=LOG_VERBOSE,
                    )
                    self.last_keep_alive_log = current_time
                    self.keep_alive_success_streak = 0
                    self.alerts_paused = True
                    self._connection_status = "offline"
                    await self._close_active_stream()
                    break

                self.last_ping = current_time
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.running:
                    log(
                        f"[{self.dvr_name}] Error in keep_alive loop: {e}",
                        LOG_STANDARD,
                    )
                    self.alerts_paused = True
                    self._connection_status = "offline"
                    await self._close_active_stream()
                break

    # PROCESSING
    async def _process_event_line(self, line: str):
        try:
            data = json.loads(line)
            self.stats["total_events"] += 1
            self._mark_fresh("event")
            await self._process_event(data)
        except json.JSONDecodeError:
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    self.stats["total_events"] += 1
                    self._mark_fresh("event")
                    await self._process_event(data)
                except Exception:
                    pass
        except Exception as e:
            log(f"Event processing error: {e}")
            self.stats["error_events"] += 1

    async def _process_event(self, event_data: Dict[str, Any]):
        try:
            # Replacement probes validate only that the desired DVR's SSE
            # connection produces fresh data.  The existing healthy monitor
            # remains the sole owner of alerts, activity, and persisted state
            # until the probe has stopped and promotion begins.
            if self.validation_only:
                return
            if self.alert_manager is None:
                raise RuntimeError(
                    "EventMonitor requires an alert manager before processing events"
                )

            event_type = event_data.get("Type")

            if event_type == "hello":
                return

            result = await self.alert_manager.process_event(event_type, event_data)

            if result:
                self.stats["alert_events"] += 1
            else:
                self.stats["filtered_events"] += 1

        except Exception as e:
            self.stats["error_events"] += 1
            log(f"Event processing error: {e}")
