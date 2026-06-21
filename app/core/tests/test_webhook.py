from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from core.notifications.webhook import WebhookManager


PUBLIC_ADDRINFO = [
    (
        __import__("socket").AF_INET,
        __import__("socket").SOCK_STREAM,
        6,
        "",
        ("93.184.216.34", 0),
    )
]


class _Settings:
    webhooks = [
        {
            "url": "https://example.com/webhook",
            "secret": "super-secret",
            "enabled": True,
        }
    ]


def _make_manager() -> WebhookManager:
    return WebhookManager(_Settings())


def test_build_payload_includes_dvr_fields():
    manager = _make_manager()

    payload = manager._build_payload(
        event_type="notification",
        title="Test title",
        message="Test message",
        kwargs={"dvr_id": "dvr_1234", "dvr_name": "Living Room"},
    )

    assert payload["dvr_id"] == "dvr_1234"
    assert payload["dvr_name"] == "Living Room"
    assert payload["data"]["title"] == "Test title"
    assert payload["data"]["message"] == "Test message"


def test_signature_still_matches_expanded_payload():
    mock_response = MagicMock()
    mock_response.status_code = 204

    manager = _make_manager()
    payload = manager._build_payload(
        event_type="notification",
        title="Test title",
        message="Test message",
        kwargs={"dvr_id": "dvr_1234", "dvr_name": "Living Room"},
    )

    with (
        patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            return_value=PUBLIC_ADDRINFO,
        ),
        patch.object(manager, "_build_payload", return_value=payload),
        patch.object(manager, "_post", return_value=mock_response) as mock_post,
    ):
        assert manager.send_notification(
            "Test title", "Test message", dvr_id="dvr_1234", dvr_name="Living Room"
        )

        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    expected_signature = (
        "sha256=" + hmac.new(b"super-secret", body, hashlib.sha256).hexdigest()
    )

    assert mock_post.called
    assert mock_post.call_args.args[2]["X-ChannelWatch-Signature"] == expected_signature
    sent_body = mock_post.call_args.args[1]
    assert sent_body == body


def test_delivery_logs_redact_secret_url_on_success():
    secret_url = "https://hooks.example.test/services/token-abc?signature=secret"
    settings = type(
        "Settings",
        (),
        {"webhooks": [{"url": secret_url, "secret": "super-secret", "enabled": True}]},
    )()
    mock_response = MagicMock()
    mock_response.status_code = 204
    manager = WebhookManager(settings)

    with (
        patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            return_value=PUBLIC_ADDRINFO,
        ),
        patch.object(manager, "_post", return_value=mock_response),
        patch("core.notifications.webhook.log") as mock_log,
    ):
        assert manager.send_notification("Test title", "Test message")

    logged_messages = "\n".join(call.args[0] for call in mock_log.call_args_list)
    assert "https://****" in logged_messages
    assert secret_url not in logged_messages
    assert "token-abc" not in logged_messages
    assert "signature=secret" not in logged_messages


def test_delivery_allows_public_https_webhook_url():
    mock_response = MagicMock()
    mock_response.status_code = 204
    manager = _make_manager()

    with (
        patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            return_value=PUBLIC_ADDRINFO,
        ),
        patch.object(manager, "_post", return_value=mock_response) as mock_post,
    ):
        assert manager.send_notification("Test title", "Test message")

    assert mock_post.called
    assert mock_post.call_args.args[0] == "https://93.184.216.34/webhook"
    assert mock_post.call_args.args[2]["Host"] == "example.com"
    assert mock_post.call_args.kwargs["sni_hostname"] == "example.com"


def test_delivery_blocks_dns_rebinding_between_validation_and_connect():
    manager = _make_manager()

    with (
        patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=[
                PUBLIC_ADDRINFO,
                [
                    (
                        PUBLIC_ADDRINFO[0][0],
                        PUBLIC_ADDRINFO[0][1],
                        6,
                        "",
                        ("127.0.0.1", 0),
                    )
                ],
            ],
        ),
        patch.object(manager, "_post") as mock_post,
        patch("core.notifications.webhook.log") as mock_log,
    ):
        assert manager.send_notification("Test title", "Test message") is False

    mock_post.assert_not_called()
    logged_messages = "\n".join(call.args[0] for call in mock_log.call_args_list)
    assert "failed safety check before delivery" in logged_messages


def test_delivery_revalidates_destination_before_posting_to_reduce_dns_rebinding_risk():
    manager = _make_manager()

    with (
        patch(
            "core.notifications.webhook.is_safe_url",
            return_value=True,
        ),
        patch("core.notifications.webhook.build_safe_url_request", return_value=None),
        patch.object(manager, "_post") as mock_post,
        patch("core.notifications.webhook.log") as mock_log,
    ):
        assert manager.send_notification("Test title", "Test message") is False

    mock_post.assert_not_called()
    logged_messages = "\n".join(call.args[0] for call in mock_log.call_args_list)
    assert "failed safety check before delivery" in logged_messages


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://127.0.0.1/webhook",
        "https://169.254.169.254/latest/meta-data/",
        "https://192.168.1.10/webhook",
        "https://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_delivery_blocks_unsafe_webhook_urls_before_posting(unsafe_url: str):
    settings = type(
        "Settings",
        (),
        {"webhooks": [{"url": unsafe_url, "secret": "super-secret", "enabled": True}]},
    )()
    manager = WebhookManager(settings)

    with (
        patch.object(manager, "_post") as mock_post,
        patch("core.notifications.webhook.log") as mock_log,
    ):
        assert manager.send_notification("Test title", "Test message") is False

    mock_post.assert_not_called()
    logged_messages = "\n".join(call.args[0] for call in mock_log.call_args_list)
    assert "destination failed safety check" in logged_messages
    assert "https://****" in logged_messages
    assert unsafe_url not in logged_messages


def test_delivery_logs_redact_secret_url_on_skip_failure_timeout_and_error():
    secret_url = "https://hooks.example.test/services/token-abc?signature=secret"
    settings = type(
        "Settings",
        (),
        {"webhooks": [{"url": secret_url, "secret": "super-secret", "enabled": True}]},
    )()

    failed_response = MagicMock()
    failed_response.status_code = 500

    scenarios = [
        ({"url": secret_url, "secret": "", "enabled": True}, None),
        (
            {"url": secret_url, "secret": "super-secret", "enabled": True},
            failed_response,
        ),
        (
            {"url": secret_url, "secret": "super-secret", "enabled": True},
            httpx.TimeoutException("timed out"),
        ),
        (
            {"url": secret_url, "secret": "super-secret", "enabled": True},
            httpx.RequestError(f"failed while posting to {secret_url}"),
        ),
    ]

    for webhook, outcome in scenarios:
        manager = WebhookManager(settings)
        if isinstance(outcome, Exception):
            post_patch = patch.object(manager, "_post", side_effect=outcome)
        elif outcome is None:
            post_patch = patch.object(manager, "_post")
        else:
            post_patch = patch.object(manager, "_post", return_value=outcome)

        with (
            patch(
                "core.helpers.url_validator.socket.getaddrinfo",
                return_value=[
                    (
                        __import__("socket").AF_INET,
                        __import__("socket").SOCK_STREAM,
                        6,
                        "",
                        ("93.184.216.34", 0),
                    )
                ],
            ),
            post_patch,
            patch("core.notifications.webhook.time.sleep"),
            patch("core.notifications.webhook.log") as mock_log,
        ):
            assert (
                manager._deliver_webhook(
                    webhook,
                    manager._build_payload("notification", "Title", "Message", {}),
                )
                is False
            )

        logged_messages = "\n".join(call.args[0] for call in mock_log.call_args_list)
        assert "https://****" in logged_messages
        assert secret_url not in logged_messages
        assert "token-abc" not in logged_messages
        assert "signature=secret" not in logged_messages


def test_send_notification_delivers_enabled_webhooks_concurrently():
    settings = type(
        "Settings",
        (),
        {
            "webhooks": [
                {
                    "url": "https://one.example.test/webhook",
                    "secret": "super-secret-one",
                    "enabled": True,
                },
                {
                    "url": "https://two.example.test/webhook",
                    "secret": "super-secret-two",
                    "enabled": True,
                },
            ]
        },
    )()
    manager = WebhookManager(settings)
    in_flight = 0
    overlapped = False
    lock = threading.Lock()
    response = MagicMock()
    response.status_code = 204

    def slow_post(url, body, headers, **kwargs):
        nonlocal in_flight, overlapped
        with lock:
            in_flight += 1
            if in_flight > 1:
                overlapped = True
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return response

    with (
        patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            return_value=[
                (
                    __import__("socket").AF_INET,
                    __import__("socket").SOCK_STREAM,
                    6,
                    "",
                    ("93.184.216.34", 0),
                )
            ],
        ),
        patch.object(manager, "_post", side_effect=slow_post) as mock_post,
    ):
        assert manager.send_notification("Test title", "Test message") is True

    assert mock_post.call_count == 2
    assert overlapped is True
