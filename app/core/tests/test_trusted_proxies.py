from contextlib import contextmanager
import socket
import threading
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request
import uvicorn

from ui.backend import main as backend_main


def _request(
    *,
    client_host: str,
    scheme: str = "http",
    headers: dict[str, str] | None = None,
) -> Request:
    encoded_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": scheme,
            "path": "/api/system",
            "raw_path": b"/api/system",
            "query_string": b"",
            "headers": encoded_headers,
            "client": (client_host, 43210),
            "server": ("channelwatch", 8501),
        }
    )


def test_forwarded_headers_are_ignored_by_default():
    request = _request(
        client_host="192.0.2.10",
        headers={
            "X-Forwarded-For": "203.0.113.25",
            "X-Forwarded-Proto": "https",
        },
    )
    with patch("ui.backend.main._TRUSTED_PROXY_NETWORKS", ()):
        assert backend_main._effective_client_host(request) == "192.0.2.10"
        assert backend_main._effective_request_scheme(request) == "http"
        assert backend_main._should_use_secure_cookies(request) is False


def test_trusted_proxy_supplies_validated_client_and_scheme():
    request = _request(
        client_host="10.20.30.40",
        headers={
            "X-Forwarded-For": "203.0.113.25, 10.20.30.40",
            "X-Forwarded-Proto": "https",
        },
    )
    networks = backend_main._parse_trusted_proxy_networks("10.20.30.0/24")
    with patch("ui.backend.main._TRUSTED_PROXY_NETWORKS", networks):
        assert backend_main._effective_client_host(request) == "203.0.113.25"
        assert backend_main._effective_request_scheme(request) == "https"
        assert backend_main._should_use_secure_cookies(request) is True


def test_invalid_forwarded_client_falls_back_to_direct_proxy_identity():
    request = _request(
        client_host="10.20.30.40",
        headers={"Forwarded": "for=_hidden;proto=ftp"},
    )
    networks = backend_main._parse_trusted_proxy_networks("10.20.30.40")
    with patch("ui.backend.main._TRUSTED_PROXY_NETWORKS", networks):
        assert backend_main._effective_client_host(request) == "10.20.30.40"
        assert backend_main._effective_request_scheme(request) == "http"
        assert backend_main._should_use_secure_cookies(request) is False


def test_effective_request_scheme_prefers_validated_trusted_forwarded_scheme():
    request = _request(
        client_host="10.20.30.40",
        scheme="https",
        headers={"Forwarded": "for=203.0.113.25;proto=http"},
    )
    networks = backend_main._parse_trusted_proxy_networks("10.20.30.40")

    with patch("ui.backend.main._TRUSTED_PROXY_NETWORKS", networks):
        assert backend_main._effective_request_scheme(request) == "http"


def test_invalid_trusted_proxy_entries_fail_closed():
    networks = backend_main._parse_trusted_proxy_networks(
        "bad-value, 10.0.0.5, 2001:db8::/32"
    )
    assert len(networks) == 2


@contextmanager
def _real_socket_identity_client(trusted_proxies: str):
    """Serve the production identity helpers behind proxy-disabled Uvicorn."""

    test_app = FastAPI()

    @test_app.get("/identity")
    async def identity(request: Request):
        return {
            "direct_client": request.client.host if request.client else None,
            "raw_scheme": request.url.scheme,
            "effective_client": backend_main._effective_client_host(request),
            "secure_cookie": backend_main._should_use_secure_cookies(request),
        }

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            test_app,
            log_level="error",
            lifespan="off",
            proxy_headers=False,
        )
    )
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="trusted-proxy-real-socket-test",
        daemon=True,
    )

    networks = backend_main._parse_trusted_proxy_networks(trusted_proxies)
    with patch("ui.backend.main._TRUSTED_PROXY_NETWORKS", networks):
        thread.start()
        # The first Uvicorn startup on a cold macOS runner can spend several
        # seconds initializing its event-loop implementation.
        deadline = time.monotonic() + 15.0
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            server.should_exit = True
            thread.join(timeout=2)
            pytest.fail("Uvicorn test server did not start")
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
            assert not thread.is_alive(), "Uvicorn test server did not stop"


@pytest.mark.parametrize(
    (
        "trusted_proxies",
        "headers",
        "expected_client",
        "expected_secure_cookie",
    ),
    [
        (
            "",
            {"X-Forwarded-For": "203.0.113.25", "X-Forwarded-Proto": "https"},
            "127.0.0.1",
            False,
        ),
        (
            "192.0.2.10",
            {"X-Forwarded-For": "203.0.113.25", "X-Forwarded-Proto": "https"},
            "127.0.0.1",
            False,
        ),
        (
            "127.0.0.1",
            {"X-Forwarded-For": "203.0.113.25", "X-Forwarded-Proto": "https"},
            "203.0.113.25",
            True,
        ),
        (
            "127.0.0.0/8",
            {"Forwarded": 'for="[2001:db8::25]";proto=https'},
            "2001:db8::25",
            True,
        ),
        (
            "127.0.0.1",
            {
                "Forwarded": "for=_hidden;proto=ftp, for=203.0.113.25;proto=https",
                "X-Forwarded-For": "not-an-ip, 203.0.113.25",
                "X-Forwarded-Proto": "ftp, https",
            },
            "127.0.0.1",
            False,
        ),
    ],
    ids=("empty", "untrusted", "exact-ip", "cidr", "malformed-chain"),
)
def test_real_socket_proxy_boundary_preserves_direct_peer_until_app_validation(
    trusted_proxies,
    headers,
    expected_client,
    expected_secure_cookie,
):
    with _real_socket_identity_client(trusted_proxies) as client:
        response = client.get("/identity", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    # This pair is the server-level invariant: Uvicorn must not rewrite either
    # value before ChannelWatch evaluates CW_TRUSTED_PROXIES.
    assert payload["direct_client"] == "127.0.0.1"
    assert payload["raw_scheme"] == "http"
    assert payload["effective_client"] == expected_client
    assert payload["secure_cookie"] is expected_secure_cookie
