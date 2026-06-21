"""Tests for the settings migration framework."""

import json
import os
from unittest.mock import patch
from dataclasses import dataclass

from core.helpers.migration import (
    defaults_merge,
    detect_version,
    get_dataclass_defaults,
    auto_backup,
    migrate_v0_to_v1,
    migrate_v3_to_v4,
    migrate_v6_to_v7,
    normalize_disk_alert_settings,
    run_migrations,
    migrate_settings,
    _rotate_backups,
    archive_legacy_session_state,
    _adopt_session_state,
    _seed_session_state_from_default,
    CURRENT_SCHEMA_VERSION,
)


def _output_text(capsys, caplog) -> str:
    captured = capsys.readouterr()
    return captured.out + "\n" + caplog.text


# --- Fixtures ---

V07_SETTINGS = {
    "channels_dvr_host": "192.168.1.100",
    "channels_dvr_port": 8089,
    "tz": "America/New_York",
    "log_level": 1,
    "log_retention_days": 7,
    "alert_channel_watching": True,
    "alert_vod_watching": True,
    "alert_disk_space": True,
    "alert_recording_events": True,
    "stream_count": True,
    "cw_channel_name": True,
    "cw_channel_number": True,
    "cw_program_name": True,
    "cw_device_name": True,
    "cw_device_ip": True,
    "cw_stream_source": True,
    "cw_image_source": "PROGRAM",
    "rd_alert_scheduled": True,
    "rd_alert_started": True,
    "rd_alert_completed": True,
    "rd_alert_cancelled": True,
    "rd_program_name": True,
    "rd_program_desc": True,
    "rd_duration": True,
    "rd_channel_name": True,
    "rd_channel_number": True,
    "rd_type": True,
    "vod_title": True,
    "vod_episode_title": True,
    "vod_summary": True,
    "vod_duration": True,
    "vod_progress": True,
    "vod_image": True,
    "vod_rating": True,
    "vod_genres": True,
    "vod_cast": True,
    "vod_device_name": True,
    "vod_device_ip": True,
    "vod_alert_cooldown": 300,
    "vod_significant_threshold": 300,
    "channel_cache_ttl": 86400,
    "program_cache_ttl": 86400,
    "job_cache_ttl": 3600,
    "vod_cache_ttl": 86400,
    "ds_threshold_percent": 10,
    "ds_threshold_gb": 50,
    "apprise_pushover": "",
    "apprise_discord": "",
    "apprise_email": "",
    "apprise_email_to": "",
    "apprise_telegram": "",
    "apprise_slack": "",
    "apprise_gotify": "",
    "apprise_matrix": "",
    "apprise_custom": "",
}

SAMPLE_DEFAULTS = {
    "dvr_servers": [],
    "tz": "America/Los_Angeles",
    "log_level": 1,
    "alert_channel_watching": True,
    "new_field_a": 42,
    "new_field_b": "default_value",
}


# --- detect_version ---


class TestDetectVersion:
    def test_missing_version_returns_zero(self):
        assert detect_version({}) == 0

    def test_missing_version_with_v07_settings(self):
        assert detect_version(V07_SETTINGS) == 0

    def test_explicit_version_returned(self):
        assert detect_version({"_version": 1}) == 1
        assert detect_version({"_version": 5}) == 5

    def test_version_zero_explicit(self):
        assert detect_version({"_version": 0}) == 0


# --- defaults_merge ---


class TestDefaultsMerge:
    def test_empty_saved_returns_all_defaults(self):
        result = defaults_merge({}, SAMPLE_DEFAULTS)
        assert result == SAMPLE_DEFAULTS

    def test_existing_values_not_overwritten(self):
        saved = {"tz": "America/New_York", "log_level": 2}
        result = defaults_merge(saved, SAMPLE_DEFAULTS)
        assert result["tz"] == "America/New_York"
        assert result["log_level"] == 2

    def test_missing_fields_get_defaults(self):
        saved = {"tz": "America/New_York"}
        result = defaults_merge(saved, SAMPLE_DEFAULTS)
        assert result["new_field_a"] == 42
        assert result["new_field_b"] == "default_value"
        assert result["dvr_servers"] == []

    def test_v07_settings_preserve_all_values(self):
        from core.helpers.config import CoreSettings

        defaults = get_dataclass_defaults(CoreSettings)
        result = defaults_merge(V07_SETTINGS, defaults)
        assert result["tz"] == "America/New_York"
        assert result["vod_alert_cooldown"] == 300
        assert result["apprise_discord"] == ""

    def test_settings_loads_with_defaults_filled(self):
        from core.helpers.config import CoreSettings

        defaults = get_dataclass_defaults(CoreSettings)
        partial = {
            "dvr_servers": [
                {
                    "id": "test",
                    "host": "10.0.0.1",
                    "port": 8089,
                    "name": "Test",
                    "enabled": True,
                }
            ],
            "tz": "America/Chicago",
        }
        result = defaults_merge(partial, defaults)
        assert result["dvr_servers"][0]["host"] == "10.0.0.1"
        assert result["tz"] == "America/Chicago"
        assert result["alert_channel_watching"] is True
        assert result["vod_alert_cooldown"] == 300
        assert result["ds_threshold_percent"] == 10
        assert result["ds_warning_threshold_percent"] == 10
        assert result["ds_critical_threshold_percent"] == 5

    def test_unknown_keys_in_saved_are_dropped(self):
        saved = {"tz": "UTC", "unknown_old_key": "leftover"}
        result = defaults_merge(saved, SAMPLE_DEFAULTS)
        assert "unknown_old_key" not in result
        assert result["tz"] == "UTC"


# --- get_dataclass_defaults ---


class TestGetDataclassDefaults:
    def test_extracts_coresettings_defaults(self):
        from core.helpers.config import CoreSettings

        defaults = get_dataclass_defaults(CoreSettings)
        assert defaults["tz"] == "America/Los_Angeles"
        assert defaults["alert_channel_watching"] is True
        assert defaults["vod_alert_cooldown"] == 300
        assert defaults["dvr_servers"] == []
        assert defaults["ds_warning_threshold_percent"] == 10
        assert defaults["ds_critical_threshold_gb"] == 25

    def test_skips_private_fields(self):
        from core.helpers.config import CoreSettings

        defaults = get_dataclass_defaults(CoreSettings)
        assert "_instance" not in defaults

    def test_custom_dataclass(self):
        @dataclass
        class FakeSettings:
            name: str = "default"
            count: int = 10
            _private: str = "hidden"

        defaults = get_dataclass_defaults(FakeSettings)
        assert defaults == {"name": "default", "count": 10}


# --- CoreSettings integration ---


class TestCoreSettingsIntegration:
    def test_v07_file_loads_without_errors(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V07_SETTINGS))

        from core.helpers.config import CoreSettings

        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
        ):
            CoreSettings._instance = None
            settings = CoreSettings()

        assert len(settings.dvr_servers) == 1
        assert settings.dvr_servers[0]["host"] == "192.168.1.100"
        assert settings.tz == "America/New_York"
        assert settings.vod_alert_cooldown == 300
        assert settings.ds_warning_threshold_percent == 10
        assert settings.ds_warning_threshold_gb == 50
        assert settings.ds_critical_threshold_percent == 5
        assert settings.ds_critical_threshold_gb == 25

    def test_partial_file_gets_defaults(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        partial = {
            "dvr_servers": [
                {
                    "id": "dvr_test",
                    "host": "10.0.0.5",
                    "port": 8089,
                    "name": "Test",
                    "enabled": True,
                }
            ],
            "tz": "UTC",
        }
        settings_file.write_text(json.dumps(partial))

        from core.helpers.config import CoreSettings

        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
        ):
            CoreSettings._instance = None
            settings = CoreSettings()

        assert len(settings.dvr_servers) == 1
        assert settings.dvr_servers[0]["host"] == "10.0.0.5"
        assert settings.tz == "UTC"
        assert settings.alert_channel_watching is True
        assert settings.vod_alert_cooldown == 300
        assert settings.ds_warning_threshold_percent == 10
        assert settings.ds_test_route_override == ""

    def test_missing_file_uses_all_defaults(self, tmp_path):
        settings_file = tmp_path / "nonexistent.json"

        from core.helpers.config import CoreSettings

        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
            patch.dict(
                os.environ,
                {"CHANNELS_DVR_HOST": "", "CHANNELS_DVR_PORT": ""},
                clear=False,
            ),
        ):
            CoreSettings._instance = None
            settings = CoreSettings()

        assert settings.dvr_servers == []
        assert settings.tz == "America/Los_Angeles"


# --- Auto-backup ---


class TestAutoBackup:
    def test_backup_created(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V07_SETTINGS))
        result = auto_backup(tmp_path, 0, 1)
        assert result is True
        backups = list((tmp_path / "backups").iterdir())
        assert len(backups) == 1
        assert "settings.v0." in backups[0].name

    def test_backup_preserves_content(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V07_SETTINGS))
        auto_backup(tmp_path, 0, 1)
        backup = list((tmp_path / "backups").iterdir())[0]
        assert json.loads(backup.read_text()) == V07_SETTINGS

    def test_backup_rotation_keeps_max_10(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for i in range(12):
            (backup_dir / f"settings-v0-to-v1-{i:04d}.json").write_text("{}")
        _rotate_backups(backup_dir, max_backups=10)
        assert len(list(backup_dir.iterdir())) == 10

    def test_no_backup_when_file_missing(self, tmp_path):
        result = auto_backup(tmp_path, 0, 1)
        assert result is False


# --- Versioned migrations ---


class TestMigrateV0ToV1:
    def test_adds_version_field(self):
        settings = dict(V07_SETTINGS)
        result = migrate_v0_to_v1(settings)
        assert result["_version"] == 1

    def test_adds_new_v08_fields(self):
        settings = dict(V07_SETTINGS)
        result = migrate_v0_to_v1(settings)
        assert result["cw_alert_cooldown"] == 300
        assert result["global_rate_limit"] == 20
        assert result["global_rate_window"] == 300
        assert result["api_key"] == ""

    def test_preserves_existing_values(self):
        settings = dict(V07_SETTINGS)
        settings["cw_alert_cooldown"] = 600
        result = migrate_v0_to_v1(settings)
        assert result["cw_alert_cooldown"] == 600

    def test_idempotent(self):
        settings = dict(V07_SETTINGS)
        first = migrate_v0_to_v1(dict(settings))
        second = migrate_v0_to_v1(dict(first))
        assert first == second


class TestRunMigrations:
    def test_v0_to_v1(self):
        settings = dict(V07_SETTINGS)
        result = run_migrations(settings, 0, 1)
        assert result["_version"] == 1

    def test_already_at_target(self):
        settings = {"_version": 1, "tz": "UTC"}
        result = run_migrations(settings, 1, 1)
        assert result == settings


class TestDiskAlertNormalization:
    def test_normalizes_warning_fields_from_legacy_thresholds(self):
        result = normalize_disk_alert_settings(
            {
                "ds_threshold_percent": 12,
                "ds_threshold_gb": 64,
            }
        )

        assert result["ds_warning_threshold_percent"] == 12
        assert result["ds_warning_threshold_gb"] == 64
        assert result["ds_critical_threshold_percent"] == 5
        assert result["ds_critical_threshold_gb"] == 25
        assert result["ds_startup_grace_seconds"] == 10
        assert result["ds_worsening_delta_gb"] == 1
        assert result["ds_worsening_delta_percent"] == 1.0
        assert result["ds_test_route_override"] == ""

    def test_blank_values_fall_back_safely(self):
        result = normalize_disk_alert_settings(
            {
                "ds_threshold_percent": "",
                "ds_threshold_gb": None,
                "ds_warning_threshold_percent": "",
                "ds_warning_threshold_gb": " ",
                "ds_critical_threshold_percent": None,
                "ds_critical_threshold_gb": "",
                "ds_startup_grace_seconds": "",
                "ds_worsening_delta_gb": None,
                "ds_worsening_delta_percent": "",
                "ds_test_route_override": None,
            }
        )

        assert result["ds_threshold_percent"] == 10
        assert result["ds_threshold_gb"] == 50
        assert result["ds_warning_threshold_percent"] == 10
        assert result["ds_warning_threshold_gb"] == 50
        assert result["ds_critical_threshold_percent"] == 5
        assert result["ds_critical_threshold_gb"] == 25
        assert result["ds_startup_grace_seconds"] == 10
        assert result["ds_worsening_delta_gb"] == 1
        assert result["ds_worsening_delta_percent"] == 1.0
        assert result["ds_test_route_override"] == ""


class TestMigrateV3ToV4:
    def test_seeds_new_disk_alert_fields_from_legacy_values(self):
        settings = {
            "_version": 3,
            "ds_threshold_percent": 15,
            "ds_threshold_gb": 80,
        }

        result = migrate_v3_to_v4(settings)

        assert result["_version"] == 4
        assert result["ds_warning_threshold_percent"] == 15
        assert result["ds_warning_threshold_gb"] == 80
        assert result["ds_critical_threshold_percent"] == 5
        assert result["ds_critical_threshold_gb"] == 25
        assert result["ds_startup_grace_seconds"] == 10
        assert result["ds_worsening_delta_gb"] == 1
        assert result["ds_worsening_delta_percent"] == 1.0
        assert result["ds_test_route_override"] == ""


class TestMigrateSettings:
    def test_full_pipeline_v0(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V07_SETTINGS))
        result = migrate_settings(tmp_path, dict(V07_SETTINGS))
        assert result["_version"] == CURRENT_SCHEMA_VERSION
        assert result["cw_alert_cooldown"] == 300
        assert result["ds_warning_threshold_percent"] == 10
        # Backup should have been created
        assert (tmp_path / "backups").is_dir()

    def test_downgrade_warning_no_crash(self, tmp_path):
        future_settings = {"_version": 99, "tz": "UTC"}
        result = migrate_settings(tmp_path, future_settings)
        assert result["_version"] == 99

    def test_current_version_no_migration(self, tmp_path):
        current = {"_version": CURRENT_SCHEMA_VERSION, "tz": "UTC"}
        result = migrate_settings(tmp_path, dict(current))
        assert result == current
        assert not (tmp_path / "backups").exists()


# --- v6 -> v7 migration ---

V6_SETTINGS = {
    "_version": 6,
    "tz": "America/Los_Angeles",
    "dvr_servers": [],
    "cw_alert_cooldown": 300,
    "global_rate_limit": 20,
    "global_rate_window": 300,
    "api_key": "",
    "webhooks": [],
}


class TestMigrateV6ToV7:
    def test_bumps_version_to_7(self):
        result = migrate_v6_to_v7(dict(V6_SETTINGS))
        assert result["_version"] == 7

    def test_adds_multi_dvr_feature_flag(self):
        result = migrate_v6_to_v7(dict(V6_SETTINGS))
        assert result["multi_dvr_v2_enabled"] is True

    def test_preserves_existing_multi_dvr_flag(self):
        settings = dict(V6_SETTINGS)
        settings["multi_dvr_v2_enabled"] = False
        result = migrate_v6_to_v7(settings)
        assert result["multi_dvr_v2_enabled"] is False

    def test_env_var_carryover_populates_dvr_servers(self):
        settings = dict(V6_SETTINGS)
        with patch.dict(
            os.environ,
            {"CHANNELS_DVR_HOST": "dvr.example.com", "CHANNELS_DVR_PORT": "8089"},
        ):
            result = migrate_v6_to_v7(settings)
        assert len(result["dvr_servers"]) == 1
        assert result["dvr_servers"][0]["host"] == "dvr.example.com"
        assert result["dvr_servers"][0]["port"] == 8089
        assert result["dvr_servers"][0]["enabled"] is True
        assert result["dvr_servers"][0]["id"].startswith("dvr_")

    def test_env_var_carryover_default_port(self):
        settings = dict(V6_SETTINGS)
        with patch.dict(
            os.environ, {"CHANNELS_DVR_HOST": "dvr.example.com"}, clear=False
        ):
            env = dict(os.environ)
            env.pop("CHANNELS_DVR_PORT", None)
            with patch.dict(os.environ, env, clear=True):
                result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"][0]["port"] == 8089

    def test_env_var_warning_emitted(self, capsys):
        settings = dict(V6_SETTINGS)
        with patch.dict(
            os.environ,
            {"CHANNELS_DVR_HOST": "dvr.example.com", "CHANNELS_DVR_PORT": "8089"},
        ):
            migrate_v6_to_v7(settings)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "CHANNELS_DVR_HOST" in captured.out
        assert "CHANNELS_DVR_SERVERS" in captured.out
        assert "dvr_servers" in captured.out
        assert "CW_DVR_1_HOST" not in captured.out
        assert "CHANNELS_DVR_SERVERS" in captured.out
        assert "dvr_servers" in captured.out
        assert "CW_DVR_1_HOST" not in captured.out

    def test_existing_dvr_servers_not_overwritten(self):
        settings = dict(V6_SETTINGS)
        settings["dvr_servers"] = [
            {
                "id": "dvr_abc",
                "host": "existing.dvr",
                "port": 8089,
                "name": "My DVR",
                "enabled": True,
            }
        ]
        with patch.dict(os.environ, {"CHANNELS_DVR_HOST": "different.dvr"}):
            result = migrate_v6_to_v7(settings)
        assert len(result["dvr_servers"]) == 1
        assert result["dvr_servers"][0]["host"] == "existing.dvr"

    def test_no_env_vars_leaves_dvr_servers_empty(self):
        settings = dict(V6_SETTINGS)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CHANNELS_DVR_HOST", "CHANNELS_DVR_PORT")
        }
        with patch.dict(os.environ, env, clear=True):
            result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"] == []

    def test_no_warning_without_env_var(self, capsys):
        settings = dict(V6_SETTINGS)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CHANNELS_DVR_HOST", "CHANNELS_DVR_PORT")
        }
        with patch.dict(os.environ, env, clear=True):
            migrate_v6_to_v7(settings)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out


class TestMigrateV6ToV7Integration:
    def test_v6_backup_naming_convention(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V6_SETTINGS))
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CHANNELS_DVR_HOST", "CHANNELS_DVR_PORT")
        }
        with patch.dict(os.environ, env, clear=True):
            result = migrate_settings(tmp_path, dict(V6_SETTINGS))
        assert result["_version"] == CURRENT_SCHEMA_VERSION
        backup_dir = tmp_path / "backups"
        assert backup_dir.is_dir()
        backups = list(backup_dir.iterdir())
        assert len(backups) == 1
        assert backups[0].name.startswith("settings.v6.")
        assert backups[0].name.endswith(".json")

    def test_full_v6_migration_with_env_var(self, tmp_path, capsys):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V6_SETTINGS))
        with patch.dict(
            os.environ,
            {"CHANNELS_DVR_HOST": "dvr.example.com", "CHANNELS_DVR_PORT": "8089"},
        ):
            result = migrate_settings(tmp_path, dict(V6_SETTINGS))
        assert result["_version"] == CURRENT_SCHEMA_VERSION
        assert result["dvr_servers"][0]["host"] == "dvr.example.com"
        assert result["dvr_servers"][0]["port"] == 8089
        backup_dir = tmp_path / "backups"
        backups = list(backup_dir.iterdir())
        assert any(b.name.startswith("settings.v6.") for b in backups)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "CHANNELS_DVR_HOST" in captured.out
        assert "CHANNELS_DVR_SERVERS" in captured.out
        assert "dvr_servers" in captured.out
        assert "CW_DVR_1_HOST" not in captured.out

    def test_v6_backup_content_preserved(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V6_SETTINGS))
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CHANNELS_DVR_HOST", "CHANNELS_DVR_PORT")
        }
        with patch.dict(os.environ, env, clear=True):
            migrate_settings(tmp_path, dict(V6_SETTINGS))
        backup_dir = tmp_path / "backups"
        backup_file = list(backup_dir.iterdir())[0]
        backup_data = json.loads(backup_file.read_text())
        assert backup_data["_version"] == 6

    def test_v7_no_extra_migration(self, tmp_path):
        v7_settings = dict(V6_SETTINGS)
        v7_settings["_version"] = 7
        v7_settings["multi_dvr_v2_enabled"] = True
        result = migrate_settings(tmp_path, dict(v7_settings))
        assert result["_version"] == 7
        assert not (tmp_path / "backups").exists()


class TestLegacyEnvVarStartupWarning:
    def test_channels_dvr_host_warning_on_startup(self, tmp_path, capsys):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"_version": 7, "dvr_servers": []}))

        from core.helpers.config import CoreSettings

        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
            patch.dict(
                os.environ, {"CHANNELS_DVR_HOST": "my.dvr", "CHANNELS_DVR_PORT": "8089"}
            ),
        ):
            CoreSettings._instance = None
            CoreSettings()

        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "CHANNELS_DVR_HOST" in captured.out

    def test_no_warning_without_legacy_env(self, tmp_path, capsys):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"_version": 7, "dvr_servers": []}))

        from core.helpers.config import CoreSettings

        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in ("CHANNELS_DVR_HOST", "CHANNELS_DVR_PORT", "CHANNELS_DVR_SERVERS")
        }
        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
            patch.dict(os.environ, env, clear=True),
        ):
            CoreSettings._instance = None
            CoreSettings()

        captured = capsys.readouterr()
        assert "WARNING" not in captured.out


# --- TZ env var override ---


class TestTZEnvOverride:
    def test_tz_env_overrides_settings(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"tz": "America/Los_Angeles"}))

        from core.helpers.config import CoreSettings

        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
            patch.dict(os.environ, {"TZ": "America/Chicago"}),
        ):
            CoreSettings._instance = None
            settings = CoreSettings()

        assert settings.tz == "America/Chicago"

    def test_no_tz_env_uses_settings(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"tz": "Europe/London"}))

        from core.helpers.config import CoreSettings

        with (
            patch("core.helpers.config.CONFIG_FILE", settings_file),
            patch("core.helpers.config.CONFIG_DIR", tmp_path),
            patch.dict(os.environ, {}, clear=True),
        ):
            CoreSettings._instance = None
            settings = CoreSettings()

        assert settings.tz == "Europe/London"


def _clean_env():
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("CHANNELS_DVR_HOST", "CHANNELS_DVR_PORT", "CHANNELS_DVR_NAME")
    }


class TestMigrateV6ToV7CanonicalIds:
    def test_existing_dvr_id_canonicalized(self):
        from core.helpers.dvr_id import canonical_dvr_id

        settings = dict(V6_SETTINGS)
        settings["dvr_servers"] = [
            {
                "id": "dvr_noncanon",
                "host": "192.168.1.100",
                "port": 8089,
                "name": "Test",
                "enabled": True,
            }
        ]
        with patch.dict(os.environ, _clean_env(), clear=True):
            result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"][0]["id"] == canonical_dvr_id("192.168.1.100", 8089)

    def test_canonical_id_already_correct_unchanged(self):
        from core.helpers.dvr_id import canonical_dvr_id

        cid = canonical_dvr_id("10.0.0.5", 8089)
        settings = dict(V6_SETTINGS)
        settings["dvr_servers"] = [
            {
                "id": cid,
                "host": "10.0.0.5",
                "port": 8089,
                "name": "DVR",
                "enabled": True,
            }
        ]
        with patch.dict(os.environ, _clean_env(), clear=True):
            result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"][0]["id"] == cid

    def test_multiple_dvrs_all_canonicalized(self):
        from core.helpers.dvr_id import canonical_dvr_id

        settings = dict(V6_SETTINGS)
        settings["dvr_servers"] = [
            {
                "id": "dvr_old1",
                "host": "10.0.0.1",
                "port": 8089,
                "name": "A",
                "enabled": True,
            },
            {
                "id": "dvr_old2",
                "host": "10.0.0.2",
                "port": 8089,
                "name": "B",
                "enabled": True,
            },
        ]
        with patch.dict(os.environ, _clean_env(), clear=True):
            result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"][0]["id"] == canonical_dvr_id("10.0.0.1", 8089)
        assert result["dvr_servers"][1]["id"] == canonical_dvr_id("10.0.0.2", 8089)

    def test_channels_dvr_name_sets_display_name(self):
        settings = dict(V6_SETTINGS)
        with patch.dict(
            os.environ,
            {
                "CHANNELS_DVR_HOST": "dvr.local",
                "CHANNELS_DVR_PORT": "8089",
                "CHANNELS_DVR_NAME": "Living Room DVR",
            },
        ):
            result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"][0]["display_name"] == "Living Room DVR"

    def test_channels_dvr_name_sets_name_field(self):
        settings = dict(V6_SETTINGS)
        with patch.dict(
            os.environ,
            {
                "CHANNELS_DVR_HOST": "dvr.local",
                "CHANNELS_DVR_PORT": "8089",
                "CHANNELS_DVR_NAME": "Basement DVR",
            },
        ):
            result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"][0]["name"] == "Basement DVR"

    def test_no_channels_dvr_name_no_display_name_key(self):
        settings = dict(V6_SETTINGS)
        env = {
            **_clean_env(),
            "CHANNELS_DVR_HOST": "dvr.local",
            "CHANNELS_DVR_PORT": "8089",
        }
        with patch.dict(os.environ, env, clear=True):
            result = migrate_v6_to_v7(settings)
        assert "display_name" not in result["dvr_servers"][0]

    def test_per_dvr_info_log_for_existing_server(self, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        settings = dict(V6_SETTINGS)
        settings["dvr_servers"] = [
            {
                "id": "dvr_old",
                "host": "dvr.local",
                "port": 8089,
                "name": "My DVR",
                "enabled": True,
            }
        ]
        with patch.dict(os.environ, _clean_env(), clear=True):
            migrate_v6_to_v7(settings)
        output = _output_text(capsys, caplog)
        assert "Migrated DVR" in output
        assert "dvr.local" in output
        assert "dvr_id=" in output

    def test_per_dvr_info_log_for_env_var_server(self, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        settings = dict(V6_SETTINGS)
        with patch.dict(
            os.environ,
            {
                "CHANNELS_DVR_HOST": "dvr.example.com",
                "CHANNELS_DVR_PORT": "8089",
            },
        ):
            migrate_v6_to_v7(settings)
        output = _output_text(capsys, caplog)
        assert "Migrated DVR" in output
        assert "dvr.example.com" in output
        assert "dvr_id=" in output

    def test_per_dvr_info_log_contains_display_name(self, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        settings = dict(V6_SETTINGS)
        settings["dvr_servers"] = [
            {
                "id": "dvr_old",
                "host": "dvr.local",
                "port": 8089,
                "name": "My DVR",
                "display_name": "Fancy Name",
                "enabled": True,
            }
        ]
        with patch.dict(os.environ, _clean_env(), clear=True):
            migrate_v6_to_v7(settings)
        assert "Fancy Name" in _output_text(capsys, caplog)

    def test_dvr_without_host_skipped(self):
        settings = dict(V6_SETTINGS)
        settings["dvr_servers"] = [
            {
                "id": "dvr_nhost",
                "host": "",
                "port": 8089,
                "name": "Broken",
                "enabled": True,
            }
        ]
        with patch.dict(os.environ, _clean_env(), clear=True):
            result = migrate_v6_to_v7(settings)
        assert result["dvr_servers"][0]["id"] == "dvr_nhost"


class TestArchiveLegacySessionState:
    def test_absent_file_returns_false(self, tmp_path):
        assert archive_legacy_session_state(tmp_path) is False

    def test_present_file_returns_true(self, tmp_path):
        (tmp_path / "session_state_default.json").write_text("{}")
        assert archive_legacy_session_state(tmp_path) is True

    def test_file_moved_to_backups(self, tmp_path):
        (tmp_path / "session_state_default.json").write_text('{"x": 1}')
        archive_legacy_session_state(tmp_path)
        assert not (tmp_path / "session_state_default.json").exists()
        backups = list((tmp_path / "backups").iterdir())
        assert len(backups) == 1
        assert backups[0].name.startswith("session_state_default.")
        assert backups[0].name.endswith(".json")

    def test_content_preserved(self, tmp_path):
        (tmp_path / "session_state_default.json").write_text('{"state": "old"}')
        archive_legacy_session_state(tmp_path)
        backup = list((tmp_path / "backups").iterdir())[0]
        assert json.loads(backup.read_text()) == {"state": "old"}

    def test_info_log_emitted(self, tmp_path, capsys, caplog):
        caplog.set_level("INFO", logger="channelwatch")
        (tmp_path / "session_state_default.json").write_text("{}")
        archive_legacy_session_state(tmp_path)
        assert "Archived legacy session state" in _output_text(capsys, caplog)


class TestAdoptSessionState:
    def test_copies_when_id_changed(self, tmp_path):
        (tmp_path / "session_state_dvr_old.json").write_text('{"k": 1}')
        _adopt_session_state(tmp_path, "dvr_old", "dvr_new")
        assert (tmp_path / "session_state_dvr_new.json").exists()
        assert json.loads((tmp_path / "session_state_dvr_new.json").read_text()) == {
            "k": 1
        }

    def test_skips_when_ids_equal(self, tmp_path):
        (tmp_path / "session_state_dvr_abc.json").write_text("{}")
        _adopt_session_state(tmp_path, "dvr_abc", "dvr_abc")
        assert len(list(tmp_path.iterdir())) == 1

    def test_skips_when_old_id_empty(self, tmp_path):
        _adopt_session_state(tmp_path, "", "dvr_new")
        assert not (tmp_path / "session_state_dvr_new.json").exists()

    def test_skips_when_new_file_already_exists(self, tmp_path):
        (tmp_path / "session_state_dvr_old.json").write_text('{"k": 1}')
        (tmp_path / "session_state_dvr_new.json").write_text('{"k": 2}')
        _adopt_session_state(tmp_path, "dvr_old", "dvr_new")
        assert json.loads((tmp_path / "session_state_dvr_new.json").read_text()) == {
            "k": 2
        }


class TestSeedSessionStateFromDefault:
    def test_seeds_when_target_absent(self, tmp_path):
        (tmp_path / "session_state_default.json").write_text('{"data": "legacy"}')
        _seed_session_state_from_default(tmp_path, "dvr_abc")
        assert (tmp_path / "session_state_dvr_abc.json").exists()
        assert json.loads((tmp_path / "session_state_dvr_abc.json").read_text()) == {
            "data": "legacy"
        }

    def test_skips_when_target_exists(self, tmp_path):
        (tmp_path / "session_state_default.json").write_text('{"data": "legacy"}')
        (tmp_path / "session_state_dvr_abc.json").write_text('{"data": "existing"}')
        _seed_session_state_from_default(tmp_path, "dvr_abc")
        assert json.loads((tmp_path / "session_state_dvr_abc.json").read_text()) == {
            "data": "existing"
        }

    def test_skips_when_no_default(self, tmp_path):
        _seed_session_state_from_default(tmp_path, "dvr_abc")
        assert not (tmp_path / "session_state_dvr_abc.json").exists()

    def test_default_file_not_removed(self, tmp_path):
        (tmp_path / "session_state_default.json").write_text("{}")
        _seed_session_state_from_default(tmp_path, "dvr_abc")
        assert (tmp_path / "session_state_default.json").exists()


class TestMigrateV7SessionStateIntegration:
    def test_default_archived_and_seeded_on_v6_migration(self, tmp_path):
        from core.helpers.dvr_id import canonical_dvr_id

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V6_SETTINGS))
        (tmp_path / "session_state_default.json").write_text('{"legacy": true}')
        env = {
            **_clean_env(),
            "CHANNELS_DVR_HOST": "10.0.0.1",
            "CHANNELS_DVR_PORT": "8089",
        }
        with patch.dict(os.environ, env, clear=True):
            migrate_settings(tmp_path, dict(V6_SETTINGS))
        cid = canonical_dvr_id("10.0.0.1", 8089)
        assert (tmp_path / f"session_state_{cid}.json").exists()
        assert not (tmp_path / "session_state_default.json").exists()
        assert any(
            f.name.startswith("session_state_default.")
            for f in (tmp_path / "backups").iterdir()
        )

    def test_old_id_state_adopted_on_v6_migration(self, tmp_path):
        from core.helpers.dvr_id import canonical_dvr_id

        cid = canonical_dvr_id("10.0.0.2", 8089)
        settings_file = tmp_path / "settings.json"
        v6 = dict(V6_SETTINGS)
        v6["dvr_servers"] = [
            {
                "id": "dvr_oldid",
                "host": "10.0.0.2",
                "port": 8089,
                "name": "DVR",
                "enabled": True,
            }
        ]
        settings_file.write_text(json.dumps(v6))
        (tmp_path / "session_state_dvr_oldid.json").write_text('{"sessions": 3}')
        with patch.dict(os.environ, _clean_env(), clear=True):
            result = migrate_settings(tmp_path, dict(v6))
        assert result["dvr_servers"][0]["id"] == cid
        assert (tmp_path / f"session_state_{cid}.json").exists()
        assert json.loads((tmp_path / f"session_state_{cid}.json").read_text()) == {
            "sessions": 3
        }

    def test_no_default_file_no_error(self, tmp_path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V6_SETTINGS))
        with patch.dict(os.environ, _clean_env(), clear=True):
            result = migrate_settings(tmp_path, dict(V6_SETTINGS))
        assert result["_version"] == CURRENT_SCHEMA_VERSION

    def test_session_archival_skipped_at_current_version(self, tmp_path):
        (tmp_path / "session_state_default.json").write_text("{}")
        v7 = dict(V6_SETTINGS)
        v7["_version"] = 7
        v7["multi_dvr_v2_enabled"] = True
        migrate_settings(tmp_path, dict(v7))
        assert (tmp_path / "session_state_default.json").exists()

    def test_full_v07_to_v7_pipeline_canonical_ids(self, tmp_path):
        from core.helpers.dvr_id import canonical_dvr_id

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(V07_SETTINGS))
        with patch.dict(os.environ, _clean_env(), clear=True):
            result = migrate_settings(tmp_path, dict(V07_SETTINGS))
        assert result["_version"] == CURRENT_SCHEMA_VERSION
        assert len(result["dvr_servers"]) == 1
        expected_id = canonical_dvr_id("192.168.1.100", 8089)
        assert result["dvr_servers"][0]["id"] == expected_id
