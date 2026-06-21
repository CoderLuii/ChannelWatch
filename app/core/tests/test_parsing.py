"""Tests for event and data parsing helper functions."""

import pytest

from core.helpers.parsing import (
    extract_channel_number,
    extract_channel_name,
    extract_device_name,
    extract_ip_address,
    extract_resolution,
    extract_source_from_session_id,
    is_valid_ip_address,
    parse_event_data,
    is_watching_event,
)


class TestExtractChannelNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Watching ch7 ABC from Living Room (192.168.1.10)", "7"),
            ("Watching ch13.1 PBS from Bedroom", "13.1"),
            ("Watching ch6001 Custom from Den", "6001"),
            ("Watching channel 42 ESPN from TV", "42"),
            ("No channel info here", None),
            ("", None),
        ],
    )
    def test_extract(self, value, expected):
        assert extract_channel_number(value) == expected


class TestExtractChannelName:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Watching ch7 ABC from Living Room", "ABC"),
            ("Watching ch13.1 ACTION NETWORK from Bedroom", "ACTION NETWORK"),
            ("Watching ch501 HBO MAX from Den", "HBO MAX"),
            ("Watching ch7 from Living Room", None),
            ("No match", None),
            ("", None),
        ],
    )
    def test_extract(self, value, expected):
        assert extract_channel_name(value) == expected


class TestExtractDeviceName:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Watching ch7 ABC from Living Room (192.168.1.10)", "Living Room"),
            ("Watching ch7 ABC from Bedroom TV", "Bedroom TV"),
            ("Watching ch7 ABC from 192.168.1.50", None),
            ("Just channel info ch7 ABC", None),
            ("", None),
        ],
    )
    def test_extract(self, value, expected):
        assert extract_device_name(value) == expected


class TestExtractIpAddress:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Watching ch7 ABC from Living Room (192.168.1.10)", "192.168.1.10"),
            ("Watching ch7 ABC from 10.0.0.5", "10.0.0.5"),
            ("Watching ch7 ABC from Living Room", None),
            ("No IP here", None),
            ("", None),
        ],
    )
    def test_extract(self, value, expected):
        assert extract_ip_address(value) == expected

    def test_parenthesized_ip_preferred(self):
        value = "Watching ch7 ABC from 10.0.0.1 (192.168.1.10)"
        assert extract_ip_address(value) == "192.168.1.10"


class TestExtractResolution:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1080i signal", "1080i"),
            ("720p stream", "720p"),
            ("480i low quality", "480i"),
            ("No resolution", None),
            ("", None),
        ],
    )
    def test_extract(self, value, expected):
        assert extract_resolution(value) == expected


class TestExtractSourceFromSessionId:
    @pytest.mark.parametrize(
        "session_id,expected",
        [
            ("dvr-stream-A1B2C3D4-192.168.1.10", "Tuner (A1B2C3D4)"),
            ("dvr-stream-M3U-MyProvider-192.168.1.10", "MyProvider"),
            ("dvr-stream-M3U", "M3U"),
            ("dvr-stream-TVE-hulu-192.168.1.10", "TVE (Hulu)"),
            ("dvr-stream-TVE", "TVE"),
            ("dvr-stream-CustomSource-192.168.1.10", "CustomSource"),
            ("short", "Unknown source"),
            ("", "Unknown source"),
        ],
    )
    def test_extract(self, session_id, expected):
        assert extract_source_from_session_id(session_id) == expected


class TestIsValidIpAddress:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("192.168.1.1", True),
            ("10.0.0.1", True),
            ("255.255.255.255", True),
            ("not-an-ip", False),
            ("", False),
            ("192.168.1.999", False),
        ],
    )
    def test_validate(self, value, expected):
        assert is_valid_ip_address(value) == expected


class TestParseEventData:
    def test_valid_json(self):
        result = parse_event_data('{"Type": "activities.set", "Value": "test"}')
        assert result == {"Type": "activities.set", "Value": "test"}

    def test_invalid_json(self):
        assert parse_event_data("not json") is None

    def test_none_input(self):
        assert parse_event_data(None) is None


class TestIsWatchingEvent:
    def test_watching_event(self):
        assert (
            is_watching_event("activities.set", {"Value": "Watching ch7 ABC"}) is True
        )

    def test_non_watching_event(self):
        assert (
            is_watching_event("activities.set", {"Value": "Recording started"}) is False
        )

    def test_wrong_event_type(self):
        assert (
            is_watching_event("activities.delete", {"Value": "Watching ch7"}) is False
        )

    def test_empty_value(self):
        assert is_watching_event("activities.set", {"Value": ""}) is False

    def test_missing_value(self):
        assert is_watching_event("activities.set", {}) is False
