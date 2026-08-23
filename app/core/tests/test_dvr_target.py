import socket

import pytest

from core.helpers.dvr_target import build_safe_dvr_request, validate_dvr_target


def _answers(*addresses):
    return [
        (socket.AF_INET6 if ":" in value else socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 0))
        for value in addresses
    ]


@pytest.mark.parametrize(
    ("hostname", "address"),
    [
        ("dvr.lan", "192.168.1.20"),
        ("channels.local", "10.10.25.75"),
        ("channels", "172.20.0.10"),
        ("dvr.tailnet.ts.net", "100.64.10.20"),
        ("channels.example.com", "203.0.113.10"),
        ("channels-v6", "fd00::20"),
    ],
)
def test_supported_dvr_hostnames_resolve_and_pin(monkeypatch, hostname, address):
    # The documentation range is reserved and intentionally rejected; use a
    # genuinely global fixture for the public-name case.
    if address == "203.0.113.10":
        address = "8.8.8.8"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _answers(address))
    target = validate_dvr_target(hostname, 8089)
    assert target is not None
    request = build_safe_dvr_request(hostname, 8089, "/status")
    assert request is not None
    assert address in request.url
    assert request.host_header == f"{hostname}:8089"


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "instance-data.ec2.internal",
        "metadata.aws.internal",
        "metadata.azure.internal",
        "metadata.google.internal",
        "metadata.oraclecloud.com",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "169.254.169.254",
        "224.0.0.1",
        "http://dvr.lan",
        "user@dvr.lan",
        "dvr.lan/path",
        "dvr.lan?query",
        "dvr.lan#fragment",
        "bad_host",
        "dvr.\u0131nternal",
    ],
)
def test_forbidden_dvr_targets_are_rejected(monkeypatch, host):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _answers("192.168.1.20"))
    assert validate_dvr_target(host, 8089) is None


def test_mixed_safe_and_forbidden_dns_answers_fail_closed(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _answers("192.168.1.20", "127.0.0.1"),
    )
    assert validate_dvr_target("dvr.lan", 8089) is None


def test_dns_failure_is_rejected(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert validate_dvr_target("missing.lan", 8089) is None
