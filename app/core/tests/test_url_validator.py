"""Tests for URL validation and SSRF prevention."""

import socket
from unittest.mock import patch

import pytest

from core.helpers.url_validator import build_safe_url_request, is_safe_url, redact_url


def _getaddrinfo_for(*addresses: str):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        results = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
            results.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return results

    return fake_getaddrinfo


class TestBlockPrivateIPs:
    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.1/image.jpg",
            "http://10.255.255.255/image.jpg",
            "http://172.16.0.1/image.jpg",
            "http://172.31.255.255/image.jpg",
            "http://192.168.1.1/image.jpg",
            "http://192.168.0.100/image.jpg",
            "http://127.0.0.1/image.jpg",
            "http://127.0.0.1:8080/image.jpg",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/image.jpg",
        ],
    )
    def test_private_ips_blocked(self, url):
        assert is_safe_url(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "http://8.8.8.8/image.jpg",
            "https://1.1.1.1/image.jpg",
            "https://104.26.10.78/image.jpg",
            "https://93.184.216.34/image.jpg",
        ],
    )
    def test_public_ips_allowed(self, url):
        assert is_safe_url(url) is True


class TestBlockLocalhostHostnames:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/image.jpg",
            "http://localhost:8080/image.jpg",
            "http://localhost.localdomain/image.jpg",
            "http://metadata.google.internal/computeMetadata/v1/",
        ],
    )
    def test_internal_hostnames_blocked(self, url):
        assert is_safe_url(url) is False


class TestBlockNonHTTPSchemes:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/file.txt",
            "gopher://evil.com/",
            "data:text/html,<script>alert(1)</script>",
            "javascript:alert(1)",
        ],
    )
    def test_non_http_schemes_blocked(self, url):
        assert is_safe_url(url) is False


class TestAllowValidPublicURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "https://tmsimg.pbs.org/assets/p1234.jpg",
            "http://example.com/images/logo.png",
            "https://cdn.tvguide.com/images/show.jpg",
            "https://image.tmdb.org/t/p/w500/abc123.jpg",
        ],
    )
    def test_valid_public_urls_allowed(self, url):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=_getaddrinfo_for("93.184.216.34"),
        ):
            assert is_safe_url(url) is True


class TestDnsResolution:
    def test_hostname_resolving_to_public_ip_allowed(self):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=_getaddrinfo_for("93.184.216.34"),
        ):
            assert is_safe_url("https://cdn.example.test/image.jpg") is True

    @pytest.mark.parametrize(
        "address",
        [
            "10.0.0.5",
            "127.0.0.1",
            "169.254.169.254",
            "224.0.0.1",
            "0.0.0.0",
            "::1",
            "fe80::1",
            "ff02::1",
        ],
    )
    def test_hostname_resolving_to_unsafe_ip_blocked(self, address):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=_getaddrinfo_for(address),
        ):
            assert is_safe_url("https://cdn.example.test/image.jpg") is False

    def test_hostname_blocked_when_any_resolved_address_is_unsafe(self):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=_getaddrinfo_for("93.184.216.34", "192.168.1.10"),
        ):
            assert is_safe_url("https://cdn.example.test/image.jpg") is False

    def test_hostname_resolution_errors_fail_closed(self):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=socket.gaierror("no such host"),
        ):
            assert is_safe_url("https://missing.example.test/image.jpg") is False

    def test_build_safe_url_request_rewrites_to_validated_ip_with_host_and_sni(self):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=_getaddrinfo_for("93.184.216.34"),
        ):
            safe = build_safe_url_request(
                "https://example.com:8443/hooks/token?sig=secret"
            )

        assert safe is not None
        assert safe.url == "https://93.184.216.34:8443/hooks/token?sig=secret"
        assert safe.host_header == "example.com:8443"
        assert safe.sni_hostname == "example.com"

    def test_build_safe_url_request_rejects_second_resolution_to_private(self):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=[
                _getaddrinfo_for("93.184.216.34")("example.com", None),
                _getaddrinfo_for("127.0.0.1")("example.com", None),
            ],
        ):
            assert build_safe_url_request("https://example.com/hooks/token") is None


class TestEdgeCases:
    def test_empty_string(self):
        assert is_safe_url("") is False

    def test_none(self):
        assert is_safe_url(None) is False

    def test_whitespace_only(self):
        assert is_safe_url("   ") is False

    def test_no_hostname(self):
        assert is_safe_url("http://") is False

    def test_https_preferred(self):
        with patch(
            "core.helpers.url_validator.socket.getaddrinfo",
            side_effect=_getaddrinfo_for("93.184.216.34"),
        ):
            assert is_safe_url("https://example.com/image.jpg") is True


class TestRedactUrl:
    def test_redact_discord(self):
        assert redact_url("discord://webhook_id/token") == "discord://****"

    def test_redact_mailto(self):
        assert redact_url("mailto://user:pass@smtp.com") == "mailto://****"

    def test_redact_custom(self):
        assert redact_url("slack://tokenA/tokenB/tokenC") == "slack://****"

    def test_redact_no_scheme(self):
        assert redact_url("no-scheme-here") == "****"

    def test_redact_https(self):
        assert redact_url("https://secret.api.com/key/value") == "https://****"
