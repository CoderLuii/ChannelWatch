"""Regression tests for shipped settings, storage, networking, and scale edges."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.helpers.dvr_id import canonical_dvr_id
from core.helpers.migration import (
    CURRENT_SCHEMA_VERSION,
    defaults_merge,
    migrate_settings,
    run_migrations,
)
from core.notifications.rate_limiter import RateLimiter


class TestCorruptSettingsJson:
    """Current behavior: invalid settings input is handled without crashing."""

    def _load_from_file(self, settings_file: Path) -> dict:
        if settings_file.is_file():
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def test_truncated_json_returns_empty_dict_no_crash(self, tmp_path):
        corrupt = tmp_path / "settings.json"
        corrupt.write_text('{"dvr_servers": [{"id": "dvr_abc', encoding="utf-8")
        assert self._load_from_file(corrupt) == {}

    def test_empty_file_returns_empty_dict_no_crash(self, tmp_path):
        empty = tmp_path / "settings.json"
        empty.write_text("", encoding="utf-8")
        assert self._load_from_file(empty) == {}

    def test_non_dict_root_json_returns_empty_dict(self, tmp_path):
        bad = tmp_path / "settings.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        assert self._load_from_file(bad) == {}


class TestMigrationIdempotency:
    def test_run_migrations_is_no_op_at_current_version(self):
        settings = {"_version": CURRENT_SCHEMA_VERSION, "dvr_servers": [], "tz": "UTC"}
        result = run_migrations(
            settings, CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION
        )
        assert result == settings

    def test_full_migration_from_version_zero_reaches_current(self):
        settings: dict = {}
        with patch.dict("os.environ", {"CHANNELS_DVR_HOST": ""}, clear=False):
            result = run_migrations(settings, 0, CURRENT_SCHEMA_VERSION)
        assert result.get("_version") == CURRENT_SCHEMA_VERSION
        assert "dvr_servers" in result

    def test_migrate_settings_on_fresh_directory_completes_cleanly(self, tmp_path):
        settings = {
            "_version": 0,
            "channels_dvr_host": "192.168.1.1",
            "channels_dvr_port": 8089,
        }
        with patch.dict("os.environ", {"CHANNELS_DVR_HOST": ""}, clear=False):
            result = migrate_settings(tmp_path, settings)
        assert result.get("_version") == CURRENT_SCHEMA_VERSION
        assert isinstance(result.get("dvr_servers"), list)

    def test_defaults_merge_preserves_schema_version_metadata(self):
        merged = defaults_merge(
            {"_version": CURRENT_SCHEMA_VERSION, "tz": "UTC"},
            {"dvr_servers": [], "tz": "America/New_York"},
        )

        assert merged["_version"] == CURRENT_SCHEMA_VERSION
        assert merged["tz"] == "UTC"

    def test_migrate_settings_normalizes_blank_dvr_name(self, tmp_path):
        settings = {
            "_version": CURRENT_SCHEMA_VERSION,
            "dvr_servers": [
                {
                    "id": "dvr_blank",
                    "name": "  ",
                    "host": "10.10.25.75",
                    "port": 8089,
                    "enabled": True,
                }
            ],
        }

        result = migrate_settings(tmp_path, settings)

        assert result["_version"] == CURRENT_SCHEMA_VERSION
        assert result["dvr_servers"][0]["name"] == "10.10.25.75"

    def test_core_settings_persists_version_and_repairs_blank_dvr_name(self, tmp_path):
        from core.helpers import config as config_module
        from core.helpers.config import CoreSettings

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "_version": CURRENT_SCHEMA_VERSION,
                    "dvr_servers": [
                        {
                            "id": "dvr_blank",
                            "name": "",
                            "host": "10.10.25.75",
                            "port": 8089,
                            "enabled": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        CoreSettings._instance = None
        try:
            with (
                patch.object(config_module, "CONFIG_FILE", settings_file),
                patch.object(config_module, "CONFIG_DIR", tmp_path),
                patch.dict(
                    "os.environ",
                    {"CHANNELS_DVR_HOST": "", "CHANNELS_DVR_SERVERS": ""},
                    clear=False,
                ),
            ):
                settings_obj = CoreSettings.get()
                assert settings_obj.dvr_servers[0]["name"] == "10.10.25.75"
        finally:
            CoreSettings._instance = None

        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        assert saved["_version"] == CURRENT_SCHEMA_VERSION
        assert saved["dvr_servers"][0]["name"] == "10.10.25.75"

    def test_core_settings_accepts_utf8_bom_settings_json(self, tmp_path):
        from core.helpers import config as config_module
        from core.helpers.config import CoreSettings

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            "\ufeff"
            + json.dumps(
                {
                    "_version": CURRENT_SCHEMA_VERSION,
                    "dvr_servers": [],
                    "tz": "UTC",
                }
            ),
            encoding="utf-8",
        )

        CoreSettings._instance = None
        try:
            with (
                patch.object(config_module, "CONFIG_FILE", settings_file),
                patch.object(config_module, "CONFIG_DIR", tmp_path),
                patch.dict(
                    "os.environ",
                    {"CHANNELS_DVR_HOST": "", "CHANNELS_DVR_SERVERS": ""},
                    clear=False,
                ),
            ):
                settings_obj = CoreSettings.get()
        finally:
            CoreSettings._instance = None

        assert settings_obj.tz == "UTC"


class TestSettingsTempFileHandling:
    def test_valid_settings_json_loads_despite_corrupt_tmp_file(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        tmp_file = tmp_path / "settings.json.tmp"

        good = {"_version": CURRENT_SCHEMA_VERSION, "dvr_servers": [], "tz": "UTC"}
        settings_file.write_text(json.dumps(good), encoding="utf-8")
        tmp_file.write_text('{"_version": 7, "dvr_servers": [', encoding="utf-8")

        loaded = json.loads(settings_file.read_text(encoding="utf-8"))
        assert loaded == good
        assert tmp_file.exists()

    def test_corrupt_tmp_does_not_shadow_valid_settings_json(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"_version": 7, "tz": "UTC"}', encoding="utf-8")
        (tmp_path / "settings.json.tmp").write_text("{INCOMPLETE", encoding="utf-8")

        data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert data["tz"] == "UTC"


class TestIPv6Identifiers:
    def test_ipv6_loopback_bare_and_bracketed_produce_same_id(self):
        assert canonical_dvr_id("::1", 8089) == canonical_dvr_id("[::1]", 8089)

    def test_ipv6_full_address_bare_and_bracketed_produce_same_id(self):
        assert canonical_dvr_id("2001:db8::1", 8089) == canonical_dvr_id(
            "[2001:db8::1]", 8089
        )

    def test_ipv6_address_is_case_insensitive(self):
        assert canonical_dvr_id("2001:DB8::1", 8089) == canonical_dvr_id(
            "2001:db8::1", 8089
        )

    def test_ipv6_link_local_with_zone_id_does_not_crash(self):
        id_bare = canonical_dvr_id("fe80::1%eth0", 8089)
        id_bracketed = canonical_dvr_id("[fe80::1%eth0]", 8089)
        assert id_bare == id_bracketed
        assert id_bare.startswith("dvr_")
        assert len(id_bare) == len("dvr_") + 8

    def test_ipv6_dvr_server_entry_survives_migration(self):
        settings = {
            "_version": 6,
            "dvr_servers": [
                {
                    "id": "dvr_old1234",
                    "host": "2001:db8::1",
                    "port": 8089,
                    "enabled": True,
                }
            ],
        }
        with patch.dict("os.environ", {"CHANNELS_DVR_HOST": ""}, clear=False):
            result = run_migrations(settings, 6, CURRENT_SCHEMA_VERSION)
        dvrs = result.get("dvr_servers", [])
        assert len(dvrs) == 1
        assert dvrs[0]["host"] == "2001:db8::1"
        assert dvrs[0]["id"] == canonical_dvr_id("2001:db8::1", 8089)


class TestDSTTransitions:
    def _make_provider(self, tz: str):
        from core.helpers.program_info import ProgramInfoProvider
        import pytz

        provider = object.__new__(ProgramInfoProvider)
        provider.timezone = tz
        provider.local_tz = pytz.timezone(tz)
        return provider

    def test_spring_forward_utc_timestamp_converts_correctly(self):
        provider = self._make_provider("America/New_York")
        timestamp = provider._parse_xmltv_time("20240310070000 +0000")
        assert timestamp is not None

        from datetime import datetime, timezone as utc_timezone

        dt_back = datetime.fromtimestamp(timestamp, tz=utc_timezone.utc)
        assert (dt_back.year, dt_back.month, dt_back.day, dt_back.hour) == (
            2024,
            3,
            10,
            7,
        )

    def test_fall_back_utc_timestamp_resolves_without_error(self):
        provider = self._make_provider("America/New_York")
        timestamp = provider._parse_xmltv_time("20241103063000 +0000")
        assert timestamp is not None

    def test_xmltv_time_without_tz_suffix_defaults_to_utc_no_crash(self):
        provider = self._make_provider("America/Los_Angeles")
        timestamp = provider._parse_xmltv_time("20240315120000")
        assert timestamp is not None

    def test_two_timestamps_spanning_dst_boundary_remain_ordered(self):
        provider = self._make_provider("America/Chicago")
        before_ts = provider._parse_xmltv_time("20240310075900 +0000")
        after_ts = provider._parse_xmltv_time("20240310080100 +0000")
        assert before_ts is not None
        assert after_ts is not None
        assert before_ts < after_ts


class TestUnicodeInputs:
    UNICODE_NAMES = [
        "ESPN \U0001f3c8",
        "Al Jazeera - \u0642\u0646\u0627\u0629",
        "\u05e2\u05e8\u05d5\u05e5 \u05d4\u05e1\u05e4\u05d5\u05e8\u05d8",
        "\u4e2d\u6587\u65b0\u95fb\u9891\u9053",
        "RTBF (Francais)",
        "\u00d1o\u00f1o TV",
        "\u041a\u0430\u043d\u0430\u043b \u0420\u043e\u0441\u0441\u0438\u0438",
        "Channel #1 & More <> \"quoted\"",
        "Line1\nLine2",
    ]

    def test_unicode_dvr_display_name_survives_migration(self):
        display_name = "Al Jazeera - \u0642\u0646\u0627\u0629"
        settings = {
            "_version": 6,
            "dvr_servers": [
                {
                    "id": "dvr_old1234",
                    "name": display_name,
                    "display_name": display_name,
                    "host": "192.168.1.100",
                    "port": 8089,
                    "enabled": True,
                }
            ],
        }
        with patch.dict("os.environ", {"CHANNELS_DVR_HOST": ""}, clear=False):
            result = run_migrations(settings, 6, CURRENT_SCHEMA_VERSION)
        assert result["dvr_servers"][0]["display_name"] == display_name

    @pytest.mark.parametrize("channel_name", UNICODE_NAMES)
    def test_record_activity_does_not_raise_on_unicode_channel_name(
        self, channel_name, tmp_path
    ):
        from core.helpers import activity_recorder as ar

        history_file = str(tmp_path / "activity_history.json")
        notification_history: dict = {}
        with patch.object(ar, "HISTORY_FILE", history_file):
            ar.record_activity(
                activity_type="watching_channel",
                title=f"Watching {channel_name}",
                message=f"Device is watching {channel_name}",
                channel_name=channel_name,
                device_name="test-device",
                device_ip="192.168.1.50",
                notification_history=notification_history,
                dvr_id="dvr_test0001",
            )

    def test_two_dvrs_same_unicode_channel_produce_distinct_dedup_keys(self, tmp_path):
        from core.helpers import activity_recorder as ar

        shared_history: dict = {}
        channel = "ESPN \U0001f3c8"

        for dvr_id in ("dvr_aaaa1111", "dvr_bbbb2222"):
            history_file = str(tmp_path / f"ah_{dvr_id}.json")
            with patch.object(ar, "HISTORY_FILE", history_file):
                ar.record_activity(
                    activity_type="watching_channel",
                    title=f"Watching {channel}",
                    message="Watching",
                    channel_name=channel,
                    device_name="device-1",
                    device_ip="10.0.0.1",
                    notification_history=shared_history,
                    dvr_id=dvr_id,
                )

        keys_with_channel = [key for key in shared_history if channel in key]
        prefixes = {key.split("-")[0] for key in keys_with_channel}
        assert {"dvr_aaaa1111", "dvr_bbbb2222"}.issubset(prefixes)

    def test_settings_json_with_unicode_dvr_names_roundtrips_through_json(
        self, tmp_path
    ):
        name = "\u0642\u0646\u0627\u0629 \U0001f3c8"
        data = {
            "_version": CURRENT_SCHEMA_VERSION,
            "dvr_servers": [
                {
                    "id": "dvr_a1b2c3d4",
                    "name": name,
                    "host": "192.168.1.1",
                    "port": 8089,
                }
            ],
        }
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        loaded = json.loads(settings_file.read_text(encoding="utf-8"))
        assert loaded["dvr_servers"][0]["name"] == name


class TestFiftyDVRsConfigured:
    @staticmethod
    def _make_fifty_dvr_settings() -> dict:
        dvrs = [
            {
                "id": canonical_dvr_id(f"192.168.{i // 256}.{i % 256}", 8089),
                "name": f"DVR-{i:03d}",
                "host": f"192.168.{i // 256}.{i % 256}",
                "port": 8089,
                "enabled": True,
            }
            for i in range(1, 51)
        ]
        return {"_version": CURRENT_SCHEMA_VERSION, "dvr_servers": dvrs}

    def test_fifty_dvr_settings_migrate_without_crash(self, tmp_path):
        settings = self._make_fifty_dvr_settings()
        result = migrate_settings(tmp_path, settings)
        assert result.get("_version") == CURRENT_SCHEMA_VERSION
        assert len(result.get("dvr_servers", [])) == 50

    def test_fifty_dvrs_json_roundtrip_is_lossless(self, tmp_path):
        settings = self._make_fifty_dvr_settings()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        loaded = json.loads(settings_file.read_text(encoding="utf-8"))
        assert len(loaded["dvr_servers"]) == 50
        assert loaded["dvr_servers"][-1]["name"] == "DVR-050"

    def test_get_dvr_connections_returns_fifty_connections(self, tmp_path):
        from core.helpers import config as config_module
        from core.helpers.config import CoreSettings

        settings = self._make_fifty_dvr_settings()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings), encoding="utf-8")

        CoreSettings._instance = None
        try:
            with (
                patch.object(config_module, "CONFIG_FILE", settings_file),
                patch.object(config_module, "CONFIG_DIR", tmp_path),
                patch.dict(
                    "os.environ",
                    {"CHANNELS_DVR_HOST": "", "CHANNELS_DVR_SERVERS": ""},
                    clear=False,
                ),
            ):
                settings_obj = CoreSettings.get()
                connections = settings_obj.get_dvr_connections()
                assert len(connections) == 50
        finally:
            CoreSettings._instance = None

    def test_fifty_dvrs_triggers_soft_limit_warning(self, tmp_path, capsys):
        from core.helpers import config as config_module
        from core.helpers.config import CoreSettings

        settings = self._make_fifty_dvr_settings()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(settings), encoding="utf-8")

        CoreSettings._instance = None
        try:
            with (
                patch.object(config_module, "CONFIG_FILE", settings_file),
                patch.object(config_module, "CONFIG_DIR", tmp_path),
                patch.dict(
                    "os.environ",
                    {"CHANNELS_DVR_HOST": "", "CHANNELS_DVR_SERVERS": ""},
                    clear=False,
                ),
            ):
                settings_obj = CoreSettings.get()
                connections = settings_obj.get_dvr_connections()
        finally:
            CoreSettings._instance = None

        captured = capsys.readouterr()
        assert len(connections) == 50
        assert (
            "WARNING: ChannelWatch is configured with 50 DVRs "
            "(recommended soft limit: 10). Performance may degrade at high DVR counts."
            in captured.out
        )


class TestOutboundRateLimit:
    def test_outbound_rate_limiter_blocks_after_limit_reached(self):
        limiter = RateLimiter(max_notifications=3, window_seconds=60)
        assert limiter.allow() is True
        assert limiter.allow() is True
        assert limiter.allow() is True
        assert limiter.allow() is False

    def test_outbound_rate_limiter_resets_after_window_expires(self):
        limiter = RateLimiter(max_notifications=2, window_seconds=1)
        assert limiter.allow() is True
        assert limiter.allow() is True
        assert limiter.allow() is False

        time.sleep(1.1)
        assert limiter.allow() is True

    def test_outbound_rate_limiter_is_thread_safe(self):
        limiter = RateLimiter(max_notifications=10, window_seconds=60)
        results: list = []
        lock = threading.Lock()

        def call_allow():
            allowed = limiter.allow()
            with lock:
                results.append(allowed)

        threads = [threading.Thread(target=call_allow) for _ in range(25)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        allowed = sum(1 for result in results if result is True)
        assert allowed <= 10
