"""SSRF prevention coverage for AppriseProvider.send_notification().

Private-network image URLs must be dropped before notification delivery, and
logs should report the rejection without exposing the raw private host.
"""

from typing import Any, cast
from unittest.mock import patch, MagicMock

import httpx
import pytest

from core.notifications.providers.apprise import AppriseProvider


def _output_text(capsys, caplog) -> str:
    captured = capsys.readouterr()
    return captured.out + "\n" + caplog.text


def _make_discord_provider() -> AppriseProvider:
    provider = AppriseProvider()
    provider.apprise = MagicMock()
    provider.urls = ["discord://123456789/tokenABCDEFGHIJKLMNOPQR"]
    provider.url_entries = [("discord", "discord://123456789/tokenABCDEFGHIJKLMNOPQR")]
    return provider


def _make_provider(entries: list[tuple[str, str]]) -> AppriseProvider:
    provider = AppriseProvider()
    provider.apprise = MagicMock()
    provider.urls = [url for _, url in entries]
    provider.url_entries = entries
    return provider


def _get_discord_embed(mock_post: MagicMock) -> dict[str, Any]:
    payload: dict[str, Any] = mock_post.call_args[1]["json"]
    return payload["embeds"][0]


def _ok_response() -> MagicMock:
    r = MagicMock()
    r.status_code = 204
    return r


class TestAppriseSSRFImageDrop:
    @patch("httpx.post")
    def test_private_ip_image_dropped_notification_delivered(
        self, mock_post: MagicMock
    ) -> None:
        mock_post.return_value = _ok_response()

        provider = _make_discord_provider()
        result = provider.send_notification(
            "Recording started",
            "Show is now recording.",
            image_url="http://192.168.1.1/logo.png",
        )

        assert mock_post.called
        assert result is True
        embed = _get_discord_embed(mock_post)
        assert "image" not in embed

    @patch("httpx.post")
    def test_metadata_endpoint_image_dropped(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _ok_response()

        provider = _make_discord_provider()
        result = provider.send_notification(
            "Recording started",
            "Show is now recording.",
            image_url="http://169.254.169.254/latest/meta-data/",
        )

        assert mock_post.called
        assert result is True
        embed = _get_discord_embed(mock_post)
        assert "image" not in embed

    @patch("httpx.post")
    def test_localhost_image_dropped(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _ok_response()

        provider = _make_discord_provider()
        result = provider.send_notification(
            "Recording started",
            "Show is now recording.",
            image_url="http://localhost/image.jpg",
        )

        assert mock_post.called
        assert result is True
        embed = _get_discord_embed(mock_post)
        assert "image" not in embed


class TestAppriseSSRFRegression:
    @patch("httpx.post")
    def test_public_image_delivered_unchanged(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _ok_response()

        public_url = "https://cdn.example.com/show-artwork.png"
        provider = _make_discord_provider()
        with patch(
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
        ):
            result = provider.send_notification(
                "Recording started",
                "Show is now recording.",
                image_url=public_url,
            )

        assert mock_post.called
        assert result is True
        embed = _get_discord_embed(mock_post)
        assert "image" in embed
        assert embed["image"]["url"] == public_url

    @patch("httpx.post")
    def test_discord_image_dropped_when_delivery_revalidation_fails(
        self, mock_post: MagicMock
    ) -> None:
        mock_post.return_value = _ok_response()

        provider = _make_discord_provider()
        with patch(
            "core.notifications.providers.apprise.is_safe_url",
            side_effect=[True, False],
        ):
            result = provider.send_notification(
                "Recording started",
                "Show is now recording.",
                image_url="https://cdn.example.com/show-artwork.png",
            )

        assert result is True
        embed = _get_discord_embed(mock_post)
        assert "image" not in embed

    def test_other_apprise_image_attach_dropped_when_delivery_revalidation_fails(self):
        provider = _make_provider([("custom", "json://example.com/token")])
        apprise_mod = MagicMock()
        other_apprise = MagicMock()
        other_apprise.notify.return_value = True
        apprise_mod.Apprise.return_value = other_apprise

        with (
            patch("importlib.import_module", return_value=apprise_mod),
            patch(
                "core.notifications.providers.apprise.is_safe_url",
                side_effect=[True, False],
            ),
        ):
            result = provider.send_notification(
                "Recording started",
                "Show is now recording.",
                image_url="https://cdn.example.com/show-artwork.png",
            )

        assert result is True
        assert other_apprise.notify.call_args.kwargs["attach"] is None

    def test_other_apprise_attachment_allows_public_literal_ip_after_revalidation(self):
        provider = _make_provider([("custom", "json://example.com/token")])
        apprise_mod = MagicMock()
        other_apprise = MagicMock()
        other_apprise.notify.return_value = True
        apprise_mod.Apprise.return_value = other_apprise

        public_ip_url = "https://93.184.216.34/show-artwork.png"
        with (
            patch("importlib.import_module", return_value=apprise_mod),
            patch(
                "core.notifications.providers.apprise.is_safe_url", return_value=True
            ),
        ):
            result = provider.send_notification(
                "Recording started",
                "Show is now recording.",
                image_url=public_ip_url,
            )

        assert result is True
        assert other_apprise.notify.call_args.kwargs["attach"] == [public_ip_url]

    @pytest.mark.parametrize(
        "destination",
        [
            "json://127.0.0.1:8080/hooks/channelwatch",
            "json://169.254.169.254/latest/meta-data",
        ],
    )
    def test_custom_http_style_destination_rejected_before_apprise_add(
        self, destination: str
    ) -> None:
        provider = _make_provider([("custom", destination)])
        apprise_mod = MagicMock()
        other_apprise = MagicMock()
        apprise_mod.Apprise.return_value = other_apprise

        with (
            patch("importlib.import_module", return_value=apprise_mod),
            patch(
                "core.notifications.providers.apprise.is_safe_url", return_value=True
            ),
        ):
            result = provider.send_notification("Recording started", "Show failed.")

        assert result is False
        other_apprise.add.assert_not_called()
        other_apprise.notify.assert_not_called()

    def test_custom_dns_rebinding_destination_rejected_before_apprise_add(self) -> None:
        provider = _make_provider([("custom", "jsons://notify.example.test/hook")])
        apprise_mod = MagicMock()
        other_apprise = MagicMock()
        apprise_mod.Apprise.return_value = other_apprise

        with (
            patch("importlib.import_module", return_value=apprise_mod),
            patch(
                "core.helpers.url_validator.socket.getaddrinfo",
                return_value=[
                    (
                        __import__("socket").AF_INET,
                        __import__("socket").SOCK_STREAM,
                        6,
                        "",
                        ("93.184.216.34", 0),
                    ),
                    (
                        __import__("socket").AF_INET,
                        __import__("socket").SOCK_STREAM,
                        6,
                        "",
                        ("127.0.0.1", 0),
                    ),
                ],
            ),
        ):
            result = provider.send_notification("Recording started", "Show failed.")

        assert result is False
        other_apprise.add.assert_not_called()
        other_apprise.notify.assert_not_called()


class TestAppriseSSRFLogging:
    @patch("httpx.post")
    def test_log_message_redacts_and_includes_reason(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="channelwatch")
        mock_post.return_value = _ok_response()

        provider = _make_discord_provider()
        provider.send_notification(
            "Recording started",
            "Show is now recording.",
            image_url="http://192.168.1.1/logo.png",
        )

        output = _output_text(capsys, caplog)
        ssrf_lines = [line for line in output.splitlines() if "SSRF" in line]
        assert ssrf_lines, "Expected SSRF warning in log output"
        ssrf_log = "\n".join(ssrf_lines)
        assert "is_safe_url" in ssrf_log
        assert "192.168.1.1" not in ssrf_log

    @patch("httpx.post")
    def test_discord_request_error_does_not_log_webhook_token(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="channelwatch")
        token = "tokenABCDEFGHIJKLMNOPQR"
        webhook_url = f"https://discord.com/api/webhooks/123456789/{token}"
        request = httpx.Request("POST", webhook_url)
        mock_post.side_effect = httpx.RequestError(
            f"failed while posting to {webhook_url}", request=request
        )

        provider = _make_discord_provider()
        result = provider.send_notification(
            "Recording started",
            "Show is now recording.",
        )

        assert result is False
        output = _output_text(capsys, caplog)
        assert token not in output
        assert webhook_url not in output
        assert "RequestError" in output


class TestAppriseDeliverySuccessSemantics:
    def test_initialize_exception_logs_service_only_not_token_url(
        self,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="channelwatch")
        token = "super_secret_token_pass_0008"
        settings = type(
            "Settings",
            (),
            {
                "apprise_pushover": "",
                "apprise_discord": f"https://discord.com/api/webhooks/webhook_id/{token}",
                "apprise_email": "",
                "apprise_email_to": "",
                "apprise_telegram": "",
                "apprise_slack": "",
                "apprise_gotify": "",
                "apprise_matrix": "",
                "apprise_custom": "",
            },
        )()
        apprise_mod = MagicMock()
        fake_apprise = MagicMock()
        fake_apprise.add.side_effect = RuntimeError(
            f"backend rejected destination discord://webhook_id/{token}"
        )
        apprise_mod.Apprise.return_value = fake_apprise

        provider = AppriseProvider()
        with patch("importlib.import_module", return_value=apprise_mod):
            result = provider.initialize(cast(Any, settings))

        assert result is False
        output = _output_text(capsys, caplog)
        assert token not in output
        assert f"discord://webhook_id/{token}" not in output
        assert "RuntimeError" in output
        assert "discord" in output

    def test_other_service_exception_redacts_custom_url_token(
        self,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="channelwatch")
        token = "secret-token-pass-0006"
        custom_url = f"pover://example.invalid/path?token={token}"
        provider = _make_provider([("custom", custom_url)])
        apprise_mod = MagicMock()
        fake_apprise = MagicMock()
        fake_apprise.add.side_effect = RuntimeError(f"failed to add {custom_url}")
        apprise_mod.Apprise.return_value = fake_apprise

        with patch("importlib.import_module", return_value=apprise_mod):
            result = provider.send_notification("Recording started", "Show failed.")

        assert result is False
        output = _output_text(capsys, caplog)
        assert token not in output
        assert custom_url not in output
        assert "RuntimeError" in output
        assert "pover" in output

    def test_other_only_failed_notify_returns_false(self) -> None:
        provider = _make_provider([("pushover", "pover://abc")])
        apprise_mod = MagicMock()
        fake_apprise = MagicMock()
        fake_apprise.notify.return_value = False
        apprise_mod.Apprise.return_value = fake_apprise

        with patch("importlib.import_module", return_value=apprise_mod):
            result = provider.send_notification("Recording started", "Show failed.")

        assert result is False
        fake_apprise.add.assert_called_once_with("pover://abc")
        fake_apprise.notify.assert_called_once()

    @patch("httpx.post")
    def test_mixed_destinations_succeed_when_one_attempt_succeeds(
        self, mock_post: MagicMock
    ) -> None:
        mock_post.return_value = _ok_response()
        provider = _make_provider(
            [
                ("discord", "discord://123456789/tokenABCDEFGHIJKLMNOPQR"),
                ("pushover", "pover://abc"),
            ]
        )
        apprise_mod = MagicMock()
        fake_apprise = MagicMock()
        fake_apprise.notify.return_value = False
        apprise_mod.Apprise.return_value = fake_apprise

        with patch("importlib.import_module", return_value=apprise_mod):
            result = provider.send_notification("Recording started", "Show partial.")

        assert result is True
        assert mock_post.called
        fake_apprise.notify.assert_called_once()
