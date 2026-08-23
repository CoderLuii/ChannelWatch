from unittest.mock import patch

import pytest

from core.helpers.program_info import ProgramInfoProvider


VALID_XMLTV = b"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="channel-1"><lcn>7.1</lcn></channel>
  <programme channel="channel-1" start="20260822090000 +0000" stop="20260822100000 +0000">
    <title>Morning News</title>
    <desc>Local headlines</desc>
    <icon src="https://example.invalid/icon.png" />
  </programme>
</tv>
"""


class _StreamResponse:
    def __init__(self, chunks, *, status_code=200, headers=None):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = headers or {}
        self.iterated = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def iter_bytes(self, chunk_size):
        assert chunk_size == 64 * 1024
        self.iterated = True
        yield from self._chunks


@pytest.fixture
def provider():
    return ProgramInfoProvider(host="192.168.1.10", port=8089)


def test_fetch_rejects_declared_oversized_xmltv_before_streaming(provider):
    response = _StreamResponse([], headers={"content-length": "9"})
    with (
        patch("core.helpers.program_info.MAX_XMLTV_RESPONSE_BYTES", 8),
        patch("core.helpers.program_info.httpx.stream", return_value=response),
    ):
        assert provider._fetch_xmltv_data() is None
    assert response.iterated is False


def test_fetch_rejects_chunked_xmltv_that_crosses_the_limit(provider):
    response = _StreamResponse([b"1234", b"56789"])
    with (
        patch("core.helpers.program_info.MAX_XMLTV_RESPONSE_BYTES", 8),
        patch("core.helpers.program_info.httpx.stream", return_value=response),
    ):
        assert provider._fetch_xmltv_data() is None


def test_fetch_returns_bounded_bytes_without_decoding_ambiguity(provider):
    response = _StreamResponse([VALID_XMLTV[:40], VALID_XMLTV[40:]])
    with patch("core.helpers.program_info.httpx.stream", return_value=response):
        assert provider._fetch_xmltv_data() == VALID_XMLTV


def test_parse_valid_xmltv_replaces_caches_atomically(provider):
    provider.program_cache = {"old": [{"title": "stale"}]}
    provider.channel_map = {"99": "old"}

    assert provider._parse_xmltv_data(VALID_XMLTV) is True

    assert provider.channel_map == {"7.1": "channel-1"}
    assert list(provider.program_cache) == ["channel-1"]
    assert provider.program_cache["channel-1"][0]["title"] == "Morning News"


def test_parse_rejects_excessive_depth_without_replacing_cache(provider):
    provider.program_cache = {"old": [{"title": "retained"}]}
    provider.channel_map = {"99": "old"}
    nested = "<tv><a><b><c /></b></a></tv>"

    with patch("core.helpers.program_info.MAX_XMLTV_DEPTH", 3):
        assert provider._parse_xmltv_data(nested) is False

    assert provider.channel_map == {"99": "old"}
    assert provider.program_cache["old"][0]["title"] == "retained"


def test_parse_rejects_excessive_elements_without_replacing_cache(provider):
    provider.program_cache = {"old": [{"title": "retained"}]}
    provider.channel_map = {"99": "old"}

    with patch("core.helpers.program_info.MAX_XMLTV_ELEMENTS", 2):
        assert provider._parse_xmltv_data("<tv><channel /><channel /></tv>") is False

    assert provider.channel_map == {"99": "old"}
    assert provider.program_cache["old"][0]["title"] == "retained"


def test_parse_rejects_malformed_xml_without_partial_cache_replacement(provider):
    provider.program_cache = {"old": [{"title": "retained"}]}
    provider.channel_map = {"99": "old"}

    assert provider._parse_xmltv_data("<tv><channel>") is False

    assert provider.channel_map == {"99": "old"}
    assert provider.program_cache["old"][0]["title"] == "retained"
