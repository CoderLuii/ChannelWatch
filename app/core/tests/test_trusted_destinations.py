import socket
from unittest.mock import patch

from core.helpers.trusted_destinations import (
    is_trusted_notification_destination,
    preview_notification_destination_safety,
)


def _getaddrinfo_for(*addresses: str):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        results = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
            results.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return results

    return fake_getaddrinfo


def test_private_ip_webhook_is_trustable_but_not_trusted_by_default():
    preview = preview_notification_destination_safety(
        "http://192.168.1.20:9000/channelwatch",
        "webhook",
        [],
    )

    assert preview.status == "local_untrusted"
    assert preview.trustable is True
    assert preview.trusted is False
    assert preview.normalized == {
        "source": "webhook",
        "scheme": "http",
        "host": "192.168.1.20",
        "port": 9000,
    }


def test_exact_trust_match_requires_source_scheme_host_and_port():
    trusted = [
        {
            "source": "webhook",
            "scheme": "http",
            "host": "192.168.1.20",
            "port": 9000,
        }
    ]

    assert (
        is_trusted_notification_destination(
            "http://192.168.1.20:9000/channelwatch",
            "webhook",
            trusted,
        )
        is True
    )
    assert (
        is_trusted_notification_destination(
            "http://192.168.1.20:9001/channelwatch",
            "webhook",
            trusted,
        )
        is False
    )
    assert (
        is_trusted_notification_destination(
            "json://192.168.1.20:9000/channelwatch",
            "apprise_custom",
            trusted,
        )
        is False
    )


def test_metadata_and_loopback_are_not_trustable():
    for url in (
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:9000/channelwatch",
    ):
        preview = preview_notification_destination_safety(url, "webhook", [])

        assert preview.trustable is False
        assert preview.trusted is False
        assert preview.status.startswith("blocked_")


def test_hostname_resolving_to_private_lan_is_trustable():
    with patch(
        "core.helpers.trusted_destinations.socket.getaddrinfo",
        side_effect=_getaddrinfo_for("192.168.1.20"),
    ):
        preview = preview_notification_destination_safety(
            "http://mattermost.lan:8065/hooks/token",
            "webhook",
            [],
        )

    assert preview.status == "local_untrusted"
    assert preview.trustable is True
    assert preview.normalized == {
        "source": "webhook",
        "scheme": "http",
        "host": "mattermost.lan",
        "port": 8065,
    }


def test_dns_resolution_failure_is_not_trustable():
    with patch(
        "core.helpers.trusted_destinations.socket.getaddrinfo",
        side_effect=socket.gaierror("missing"),
    ):
        preview = preview_notification_destination_safety(
            "http://missing.local:8065/hooks/token",
            "webhook",
            [],
        )

    assert preview.status == "blocked_resolution_failed"
    assert preview.trustable is False

