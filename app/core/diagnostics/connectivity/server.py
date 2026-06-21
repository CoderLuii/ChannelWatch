"""Implements connectivity and API endpoint diagnostics for Channels DVR."""

import requests

from ...helpers.logging import log
from ..output import print_test_header, print_result, print_section

# CONNECTIVITY TESTING


def test_connectivity(host: str, port: int) -> bool:
    """Verifies basic connectivity to the Channels DVR server and event stream."""
    print_test_header("Connectivity Test")

    try:
        log(f"Connecting to {host}:{port}...")

        response = requests.get(f"http://{host}:{port}/status", timeout=5)

        if response.status_code != 200:
            print_result(False, f"Server returned HTTP {response.status_code}")
            return False

        data = response.json()
        version = data.get("version", "Unknown")
        log(f"Server version: {version}")

        log("Testing event stream...")
        event_response = requests.get(
            f"http://{host}:{port}/dvr/events/subscribe",
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=5,
        )

        if event_response.status_code == 200:
            print_result(True, "Server and event stream reachable")
            return True
        else:
            print_result(
                False, f"Event stream returned HTTP {event_response.status_code}"
            )
            return False

    except Exception as e:
        print_result(False, f"Connection error: {e}")
        return False


# API ENDPOINT TESTING


def test_api_endpoints(host: str, port: int) -> bool:
    """Tests multiple API endpoints across various server functionality groups."""
    print_test_header("API Endpoints Test")

    endpoint_groups = {
        "Core System": [
            {"url": "/status", "description": "Server status", "timeout": 5},
            {
                "url": "/dvr/events/subscribe",
                "description": "Event stream",
                "timeout": 5,
                "stream": True,
            },
            {"url": "/dvr", "description": "Storage info", "timeout": 5},
        ],
        "Channel & Program Data": [
            {"url": "/api/v1/channels", "description": "Channel list", "timeout": 5},
            {
                "url": "/devices/ANY/guide/xmltv",
                "description": "Program guide",
                "timeout": 3600,
            },
        ],
        "Recording & VOD Data": [
            {"url": "/api/v1/all", "description": "VOD metadata", "timeout": 10},
        ],
    }

    results = []

    for group_name, endpoints in endpoint_groups.items():
        print_section(group_name)

        for ep in endpoints:
            url = ep["url"]
            timeout = ep["timeout"]
            is_stream = ep.get("stream", False)

            try:
                if is_stream:
                    response = requests.get(
                        f"http://{host}:{port}{url}",
                        headers={"Accept": "text/event-stream"},
                        stream=True,
                        timeout=5,
                    )
                    if response.status_code == 200:
                        response.close()
                else:
                    response = requests.get(
                        f"http://{host}:{port}{url}", timeout=timeout
                    )

                if response.status_code == 200:
                    log(f"  {url:<35} PASS  (HTTP 200)")
                    results.append(True)
                else:
                    log(f"  {url:<35} FAIL  (HTTP {response.status_code})")
                    results.append(False)
            except Exception as e:
                log(f"  {url:<35} FAIL  ({e})")
                results.append(False)

    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    log(f"Results: {passed} passed, {failed} failed")

    if failed == 0:
        print_result(True, "All endpoints reachable")
    else:
        print_result(False, f"{failed} endpoint(s) unreachable")

    return failed == 0


# EVENT STREAM MONITORING


def test_event_stream(host: str, port: int, duration: int) -> bool:
    """Monitors the Channels DVR event stream for the specified duration."""
    from ...helpers.tools import monitor_event_stream

    print_test_header(f"Event Stream Monitor ({duration}s)")

    try:
        success = monitor_event_stream(host, port, duration)
        print_result(
            success, "Stream monitored" if success else "Stream monitoring failed"
        )
        return success
    except Exception as e:
        print_result(False, f"Exception: {e}")
        return False
