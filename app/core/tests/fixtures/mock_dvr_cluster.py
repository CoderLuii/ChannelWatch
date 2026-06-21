"""Reusable mock Channels DVR cluster for pytest.

Provides lightweight threaded HTTP servers with JSON endpoints, SSE event
streaming, and an mDNS registration hook suitable for multi-DVR tests.
"""

from __future__ import annotations

import ipaddress
import json
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import request as urlrequest

import pytest


GIB = 1024**3
DEFAULT_SERVICE_TYPE = "_channels_dvr._tcp.local."


def _pick_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _decode_query_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


@dataclass(slots=True)
class MockDVR:
    """Threaded stand-in for one Channels DVR instance."""

    name: str
    host: str = "127.0.0.1"
    port: int = 0
    api_key: str = ""
    state: dict[str, Any] | None = None
    server_version: str = "mock-1.0"
    dvr_id: str | None = None
    mdns_service_type: str = DEFAULT_SERVICE_TYPE
    mdns_properties: dict[str, str] = field(default_factory=dict)
    _server: ThreadingHTTPServer = field(init=False, repr=False)
    _thread: threading.Thread = field(init=False, repr=False)
    _subscriber_queues: set[queue.Queue[dict[str, Any]]] = field(init=False, repr=False)
    _subscriber_lock: threading.Lock = field(init=False, repr=False)
    _subscription_count: int = field(init=False, repr=False)
    _running: bool = field(init=False, repr=False)
    _closed: bool = field(init=False, repr=False)
    _registered_mdns_info: Any | None = field(init=False, repr=False)
    _owned_zeroconf: Any | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.port == 0:
            self.port = _pick_free_port(self.host)
        if self.dvr_id is None:
            safe_name = self.name.lower().replace(" ", "_")
            self.dvr_id = f"mock_{safe_name}_{self.port}"

        defaults = self._build_default_state()
        if self.state:
            defaults.update(self.state)
        self.state = defaults

        self._server = self._make_server()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._subscriber_queues: set[queue.Queue[dict[str, Any]]] = set()
        self._subscriber_lock = threading.Lock()
        self._subscription_count = 0
        self._running = False
        self._closed = False
        self._registered_mdns_info: Any | None = None
        self._owned_zeroconf: Any | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def subscription_count(self) -> int:
        with self._subscriber_lock:
            return self._subscription_count

    def _build_default_state(self) -> dict[str, Any]:
        channels = [
            {
                "number": "100",
                "name": f"{self.name} Channel 100",
                "logo_url": f"http://{self.host}:{self.port}/assets/channel-100.png",
            }
        ]
        streams = {"count": 0, "active": []}
        disk = {
            "path": f"/shares/{self.name.replace(' ', '_')}",
            "free": 120 * GIB,
            "total": 200 * GIB,
            "used": 80 * GIB,
        }
        return {
            "status": {"version": self.server_version, "name": self.name},
            "disk": disk,
            "channels": channels,
            "streams": streams,
            "jobs": [],
            "dvr": {
                "name": self.name,
                "path": disk["path"],
                "disk": disk,
                "streams": streams,
            },
        }

    def _make_server(self) -> ThreadingHTTPServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_json(self, payload: Any, status: int = 200) -> None:
                body = _json_bytes(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _serve_sse(self) -> None:
                event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
                owner._register_subscriber(event_queue)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    hello = owner.inject_event(
                        "hello", name="hello", value="ready", broadcast=False
                    )
                    self.wfile.write(b"data: " + _json_bytes(hello) + b"\n\n")
                    self.wfile.flush()
                    while owner._running:
                        try:
                            payload = event_queue.get(timeout=0.5)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        self.wfile.write(b"data: " + _json_bytes(payload) + b"\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    pass
                finally:
                    owner._unregister_subscriber(event_queue)

            def do_GET(self) -> None:
                if self.path in ("/events", "/dvr/events/subscribe"):
                    self._serve_sse()
                    return

                if self.path == "/status":
                    self._send_json(owner.state["status"])
                    return

                if self.path == "/disk":
                    self._send_json(owner.state["disk"])
                    return

                if self.path in ("/channels", "/api/v1/channels"):
                    self._send_json(owner.state["channels"])
                    return

                if self.path == "/streams":
                    self._send_json(owner.state["streams"])
                    return

                if self.path == "/dvr":
                    self._send_json(owner.state["dvr"])
                    return

                if self.path == "/api/v1/jobs":
                    self._send_json(owner.state["jobs"])
                    return

                self.send_error(404)

        return ThreadingHTTPServer((self.host, self.port), Handler)

    def _register_subscriber(
        self, subscriber_queue: queue.Queue[dict[str, Any]]
    ) -> None:
        with self._subscriber_lock:
            self._subscriber_queues.add(subscriber_queue)
            self._subscription_count += 1

    def _unregister_subscriber(
        self, subscriber_queue: queue.Queue[dict[str, Any]]
    ) -> None:
        with self._subscriber_lock:
            self._subscriber_queues.discard(subscriber_queue)
            self._subscription_count = max(0, self._subscription_count - 1)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread.start()
        self.wait_until_ready()

    def wait_until_ready(self, timeout: float = 5.0) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                with urlrequest.urlopen(
                    f"{self.base_url}/status", timeout=0.5
                ) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.05)
        raise TimeoutError(
            f"Timed out waiting for mock DVR {self.name} on {self.base_url}"
        )

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._running = False
        self.stop_mdns_announce()
        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def set_state(self, **updates: Any) -> None:
        self.state.update(updates)

    def inject_event(
        self,
        event_type: str,
        *,
        name: str = "",
        value: Any = "",
        broadcast: bool = True,
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {"Type": event_type, "Name": name, "Value": value, **extra}
        if broadcast:
            with self._subscriber_lock:
                subscribers = list(self._subscriber_queues)
            for subscriber in subscribers:
                subscriber.put(payload)
        return payload

    def build_mdns_service_info(self) -> Any:
        from zeroconf import ServiceInfo

        address = ipaddress.ip_address(self.host).packed
        properties = {
            "version": self.state.get("status", {}).get("version", self.server_version),
            "name": self.name,
            **self.mdns_properties,
        }
        encoded_properties = {
            key.encode("utf-8"): _decode_query_value(value).encode("utf-8")
            for key, value in properties.items()
        }
        service_name = f"{self.name}.{self.mdns_service_type}"
        return ServiceInfo(
            type_=self.mdns_service_type,
            name=service_name,
            addresses=[address],
            port=self.port,
            server=f"{self.name.replace(' ', '-')}.local.",
            properties=encoded_properties,
        )

    def mdns_announce(self, zeroconf_instance: Any | None = None) -> Any:
        from zeroconf import Zeroconf

        if self._registered_mdns_info is not None:
            return self._registered_mdns_info

        if zeroconf_instance is None:
            zeroconf_instance = Zeroconf()
            self._owned_zeroconf = zeroconf_instance

        info = self.build_mdns_service_info()
        zeroconf_instance.register_service(info)
        self._registered_mdns_info = info
        return info

    def stop_mdns_announce(self) -> None:
        if self._registered_mdns_info is not None and self._owned_zeroconf is not None:
            try:
                self._owned_zeroconf.unregister_service(self._registered_mdns_info)
            finally:
                self._owned_zeroconf.close()
        self._registered_mdns_info = None
        self._owned_zeroconf = None


@dataclass(slots=True)
class MockDVRCluster:
    """Collection of MockDVR instances with coordinated lifecycle."""

    dvrs: list[MockDVR]

    @classmethod
    def start_cluster(
        cls,
        count: int,
        *,
        host: str = "127.0.0.1",
        port_start: int | None = None,
        api_key: str = "",
        state_factory: Any | None = None,
    ) -> "MockDVRCluster":
        if count <= 0:
            raise ValueError("MockDVRCluster requires count >= 1")

        dvrs: list[MockDVR] = []
        for index in range(count):
            state = state_factory(index) if state_factory is not None else None
            port = 0 if port_start is None else port_start + index
            dvr = MockDVR(
                name=f"Mock DVR {index + 1}",
                host=host,
                port=port,
                api_key=api_key,
                state=state,
                server_version=f"mock-{index + 1}.0",
                dvr_id=f"mock_dvr_{index + 1}",
            )
            dvr.start()
            dvrs.append(dvr)
        return cls(dvrs=dvrs)

    def __iter__(self):
        return iter(self.dvrs)

    def __len__(self) -> int:
        return len(self.dvrs)

    def __getitem__(self, item: int) -> MockDVR:
        return self.dvrs[item]

    @property
    def base_urls(self) -> list[str]:
        return [dvr.base_url for dvr in self.dvrs]

    def stop(self) -> None:
        for dvr in reversed(self.dvrs):
            dvr.stop()

    def wait_for_subscriptions(self, expected: int, timeout: float = 5.0) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if sum(dvr.subscription_count for dvr in self.dvrs) >= expected:
                return
            time.sleep(0.05)
        raise TimeoutError(f"Timed out waiting for {expected} SSE subscriptions")


@pytest.fixture
def mock_dvr_cluster():
    """Create and auto-teardown a cluster of mock DVR servers.

    Usage:
        cluster = mock_dvr_cluster(count=3)
    """

    clusters: list[MockDVRCluster] = []

    def _factory(count: int, **kwargs: Any) -> MockDVRCluster:
        cluster = MockDVRCluster.start_cluster(count=count, **kwargs)
        clusters.append(cluster)
        return cluster

    try:
        yield _factory
    finally:
        for cluster in reversed(clusters):
            cluster.stop()
