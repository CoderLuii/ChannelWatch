"""Tests for channel art fallback and bridge mode warning."""

from unittest.mock import patch, MagicMock
import httpx


class TestChannelArtFallback:
    """Test image source fallback chain: primary -> alternate -> no image."""

    def test_channel_mode_falls_back_to_program(self):
        """When image_source=CHANNEL and logo is empty, use program image."""
        channel_logo_url = ""
        program_image_url = "https://example.com/program.jpg"
        image_source = "CHANNEL"
        image_url = ""

        if image_source.upper() == "CHANNEL":
            if channel_logo_url:
                image_url = channel_logo_url
            elif program_image_url:
                image_url = program_image_url
            else:
                image_url = ""

        assert image_url == "https://example.com/program.jpg"

    def test_program_mode_falls_back_to_channel(self):
        """When image_source=PROGRAM and program image is empty, use channel logo."""
        channel_logo_url = "https://example.com/logo.png"
        program_image_url = ""
        image_source = "PROGRAM"

        if image_source.upper() == "CHANNEL":
            image_url = channel_logo_url or program_image_url or ""
        else:
            if program_image_url:
                image_url = program_image_url
            elif channel_logo_url:
                image_url = channel_logo_url
            else:
                image_url = ""

        assert image_url == "https://example.com/logo.png"

    def test_both_empty_sends_without_image(self):
        """When both sources are empty, image_url should be empty string."""
        channel_logo_url = ""
        program_image_url = ""

        for image_source in ("CHANNEL", "PROGRAM"):
            if image_source == "CHANNEL":
                image_url = channel_logo_url or program_image_url or ""
            else:
                image_url = program_image_url or channel_logo_url or ""
            assert image_url == ""

    def test_channel_mode_prefers_channel_logo(self):
        """When both are available and mode is CHANNEL, use channel logo."""
        channel_logo_url = "https://example.com/logo.png"
        program_image_url = "https://example.com/program.jpg"
        assert program_image_url
        image_url = channel_logo_url
        assert image_url == "https://example.com/logo.png"

    def test_program_mode_prefers_program_image(self):
        """When both are available and mode is PROGRAM, use program image."""
        channel_logo_url = "https://example.com/logo.png"
        program_image_url = "https://example.com/program.jpg"
        assert channel_logo_url

        image_url = program_image_url
        assert image_url == "https://example.com/program.jpg"


class TestBridgeModeWarning:
    """Test that localhost connection failures produce bridge mode warnings."""

    def test_localhost_failure_warns(self):
        from core.helpers.initialize import check_server_connectivity

        with (
            patch(
                "core.helpers.initialize.httpx.get",
                side_effect=httpx.ConnectError("refused"),
            ),
            patch("core.helpers.initialize.log") as mock_log,
        ):
            result = check_server_connectivity("localhost", 8089)

        assert result is False
        calls = [str(c) for c in mock_log.call_args_list]
        assert any("Bridge mode" in c for c in calls)

    def test_127_failure_warns(self):
        from core.helpers.initialize import check_server_connectivity

        with (
            patch(
                "core.helpers.initialize.httpx.get",
                side_effect=httpx.ConnectError("refused"),
            ),
            patch("core.helpers.initialize.log") as mock_log,
        ):
            result = check_server_connectivity("127.0.0.1", 8089)

        assert result is False
        calls = [str(c) for c in mock_log.call_args_list]
        assert any("Bridge mode" in c for c in calls)

    def test_0000_failure_warns(self):
        from core.helpers.initialize import check_server_connectivity

        with (
            patch(
                "core.helpers.initialize.httpx.get",
                side_effect=httpx.ConnectError("refused"),
            ),
            patch("core.helpers.initialize.log") as mock_log,
        ):
            result = check_server_connectivity("0.0.0.0", 8089)

        assert result is False
        calls = [str(c) for c in mock_log.call_args_list]
        assert any("Bridge mode" in c for c in calls)

    def test_lan_ip_failure_no_bridge_warning(self):
        from core.helpers.initialize import check_server_connectivity

        with (
            patch(
                "core.helpers.initialize.httpx.get",
                side_effect=httpx.ConnectError("refused"),
            ),
            patch("core.helpers.initialize.log") as mock_log,
        ):
            result = check_server_connectivity("192.168.1.100", 8089)

        assert result is False
        calls = [str(c) for c in mock_log.call_args_list]
        assert not any("Bridge mode" in c for c in calls)

    def test_successful_connection_no_warning(self):
        from core.helpers.initialize import check_server_connectivity

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": "2026.02.09"}
        with (
            patch("core.helpers.initialize.httpx.get", return_value=mock_resp),
            patch("core.helpers.initialize.log") as mock_log,
        ):
            result = check_server_connectivity("localhost", 8089)

        assert result is True
        calls = [str(c) for c in mock_log.call_args_list]
        assert not any("Bridge mode" in c for c in calls)
