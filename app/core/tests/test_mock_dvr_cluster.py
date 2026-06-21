import json
import threading
from urllib import error as urlerror
from urllib import request as urlrequest

import pytest


pytest_plugins = ["core.tests.fixtures.mock_dvr_cluster"]


def read_json(url: str):
    with urlrequest.urlopen(url, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def open_sse(url: str):
    request = urlrequest.Request(url, headers={"Accept": "text/event-stream"})
    return urlrequest.urlopen(request, timeout=5.0)


def test_mock_dvr_cluster_spins_up_ten_dvrs(mock_dvr_cluster):
    cluster = mock_dvr_cluster(count=10)

    assert len(cluster) == 10
    assert len({dvr.port for dvr in cluster}) == 10

    versions = [read_json(f"{dvr.base_url}/status")["version"] for dvr in cluster]
    assert versions == [f"mock-{index}.0" for index in range(1, 11)]


def test_mock_dvr_http_endpoints_return_expected_payloads(mock_dvr_cluster):
    cluster = mock_dvr_cluster(
        count=1,
        state_factory=lambda _: {
            "status": {"version": "fixture-2.0", "name": "Fixture DVR"},
            "disk": {"path": "/srv/dvr", "free": 50, "total": 100, "used": 50},
            "channels": [{"number": "7", "name": "Fixture Seven"}],
            "streams": {"count": 2, "active": [{"device": "Living Room"}]},
            "jobs": [{"id": "job-1", "name": "Daily News"}],
            "dvr": {
                "name": "Fixture DVR",
                "path": "/srv/dvr",
                "disk": {"free": 50, "total": 100},
            },
        },
    )
    dvr = cluster[0]

    assert read_json(f"{dvr.base_url}/status") == {
        "version": "fixture-2.0",
        "name": "Fixture DVR",
    }
    assert read_json(f"{dvr.base_url}/disk")["path"] == "/srv/dvr"
    assert read_json(f"{dvr.base_url}/channels")[0]["name"] == "Fixture Seven"
    assert read_json(f"{dvr.base_url}/api/v1/channels")[0]["number"] == "7"
    assert read_json(f"{dvr.base_url}/streams")["count"] == 2
    assert read_json(f"{dvr.base_url}/dvr")["path"] == "/srv/dvr"
    assert read_json(f"{dvr.base_url}/api/v1/jobs")[0]["id"] == "job-1"


def test_mock_dvr_sse_injection_supports_current_and_legacy_paths(mock_dvr_cluster):
    cluster = mock_dvr_cluster(count=1)
    dvr = cluster[0]
    results: dict[str, dict[str, str]] = {}
    errors: list[BaseException] = []

    def consume(label: str, path: str) -> None:
        try:
            with open_sse(f"{dvr.base_url}{path}") as response:
                cluster.wait_for_subscriptions(expected=2)
                response.readline()
                response.readline()
                raw = response.readline().decode("utf-8").strip()
                payload = json.loads(raw.removeprefix("data: "))
                results[label] = payload
        except BaseException as exc:  # pragma: no cover - surfaced in assertion below
            errors.append(exc)

    current = threading.Thread(
        target=consume, args=("current", "/dvr/events/subscribe"), daemon=True
    )
    legacy = threading.Thread(target=consume, args=("legacy", "/events"), daemon=True)
    current.start()
    legacy.start()

    cluster.wait_for_subscriptions(expected=2)
    dvr.inject_event(
        "activities.set",
        name="session-1",
        value="Watching ch100 Test Channel from Test Device (10.0.0.10)",
    )

    current.join(timeout=5)
    legacy.join(timeout=5)

    assert not errors
    assert results["current"]["Type"] == "activities.set"
    assert results["legacy"]["Name"] == "session-1"


def test_mock_dvr_mdns_announce_registers_channels_service(mock_dvr_cluster):
    cluster = mock_dvr_cluster(count=1)
    dvr = cluster[0]

    class FakeZeroconf:
        def __init__(self):
            self.registered = []

        def register_service(self, info):
            self.registered.append(info)

    fake = FakeZeroconf()
    info = dvr.mdns_announce(fake)

    assert fake.registered == [info]
    assert info.type == "_channels_dvr._tcp.local."
    assert info.port == dvr.port
    assert info.properties[b"version"] == b"mock-1.0"


def test_mock_dvr_cluster_teardown_closes_servers(mock_dvr_cluster):
    cluster = mock_dvr_cluster(count=2)
    urls = [f"{dvr.base_url}/status" for dvr in cluster]

    cluster.stop()

    for url in urls:
        with pytest.raises(
            (
                urlerror.URLError,
                ConnectionResetError,
                ConnectionRefusedError,
                TimeoutError,
                OSError,
            )
        ):
            urlrequest.urlopen(url, timeout=0.5)
