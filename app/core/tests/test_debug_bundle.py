import io
import json
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch


def _make_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "_version": 7,
        "api_key": "super-secret-key",
        "ics_feed_token": "calendar-feed-secret",
        "rss_feed_token": "rss-feed-secret",
        "apprise_pushover": "pover://token/user",
        "apprise_discord": "discord://token",
        "apprise_email": "",
        "apprise_email_to": "",
        "apprise_telegram": "",
        "apprise_slack": "",
        "apprise_gotify": "",
        "apprise_matrix": "",
        "apprise_custom": "",
        "error_reporting_dsn": "https://abc@glitchtip.example.com/1",
        "tz": "UTC",
        "log_level": 1,
        "dvr_servers": [
            {
                "id": "dvr_abc12345",
                "name": "Living Room",
                "host": "192.168.1.100",
                "port": 8089,
                "api_key": "dvr-secret",
                "enabled": True,
            }
        ],
        "webhooks": [
            {
                "url": "https://hooks.example.com/xyz",
                "secret": "webhook-secret",
                "enabled": True,
            }
        ],
    }
    (config_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    log_lines = [
        "[2026-04-21 10:00:00] Connected to DVR at http://192.168.1.100:8089",
        "[2026-04-21 10:00:01] Normal log line without sensitive data",
        "[2026-04-21 10:00:02] Webhook sent to https://hooks.example.com/xyz",
        "[2026-04-21 10:00:03] Request from peer 10.0.0.5 refused",
    ]
    (config_dir / "channelwatch.log").write_text("\n".join(log_lines), encoding="utf-8")

    (config_dir / "encryption.key").write_bytes(b"\xde\xad\xbe\xef" * 8)
    (config_dir / "channelwatch.db").write_bytes(b"SQLite data")
    (config_dir / "session_state_dvr_abc12345.json").write_text('{"last_seen": 1}')

    return config_dir


def _open_bundle(config_dir: Path) -> zipfile.ZipFile:
    from core.helpers.debug_bundle import create_debug_bundle

    return zipfile.ZipFile(io.BytesIO(create_debug_bundle(config_dir)))


def _read_manifest(config_dir: Path) -> dict[str, Any]:
    zf = _open_bundle(config_dir)
    name = next(n for n in zf.namelist() if n.endswith("/manifest.json"))
    return json.loads(zf.read(name))


class TestBundleLayout:
    def test_bundle_is_valid_zip(self, tmp_path):
        config_dir = _make_config_dir(tmp_path)
        from core.helpers.debug_bundle import create_debug_bundle

        assert zipfile.is_zipfile(io.BytesIO(create_debug_bundle(config_dir)))

    def test_bundle_contains_manifest(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert any(n.endswith("/manifest.json") for n in zf.namelist())

    def test_bundle_contains_settings_sanitized(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert any(n.endswith("/settings_sanitized.json") for n in zf.namelist())

    def test_bundle_contains_log_at_logs_app_log(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert any(n.endswith("/logs/app.log") for n in zf.namelist())

    def test_bundle_does_not_contain_log_tail_txt(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert not any("log_tail.txt" in n for n in zf.namelist())

    def test_bundle_contains_health_snapshot(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert any(n.endswith("/health_snapshot.json") for n in zf.namelist())

    def test_bundle_excludes_encryption_key(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert not any("encryption.key" in n for n in zf.namelist())

    def test_bundle_excludes_raw_database(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert not any("channelwatch.db" in n for n in zf.namelist())

    def test_missing_optional_files_do_not_fail(self, tmp_path):
        config_dir = tmp_path / "empty_config"
        config_dir.mkdir()
        from core.helpers.debug_bundle import create_debug_bundle

        assert zipfile.is_zipfile(io.BytesIO(create_debug_bundle(config_dir)))


class TestManifestContract:
    def test_manifest_bundle_type_is_debug(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert m["bundle_type"] == "debug"

    def test_manifest_has_app_version(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert "app_version" in m
        assert isinstance(m["app_version"], str)
        assert len(m["app_version"]) > 0

    def test_manifest_has_arch(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert "arch" in m
        assert isinstance(m["arch"], str)
        assert len(m["arch"]) > 0

    def test_manifest_has_dvr_count(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert "dvr_count" in m
        assert m["dvr_count"] == 1

    def test_manifest_dvr_count_zero_when_no_servers(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text(json.dumps({"dvr_servers": []}))
        m = _read_manifest(config_dir)
        assert m["dvr_count"] == 0

    def test_manifest_has_no_dvr_identifiers(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        manifest_text = json.dumps(m)
        assert "dvr_abc12345" not in manifest_text
        assert "Living Room" not in manifest_text
        assert "192.168.1.100" not in manifest_text

    def test_manifest_artifacts_list_uses_logs_app_log(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert "logs/app.log" in m["artifacts"]
        assert "log_tail.txt" not in m["artifacts"]

    def test_manifest_artifacts_complete(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert set(m["artifacts"]) == {
            "manifest.json",
            "settings_sanitized.json",
            "logs/app.log",
            "health_snapshot.json",
        }

    def test_manifest_has_privacy_note(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert "privacy_note" in m
        assert len(m["privacy_note"]) > 20

    def test_manifest_has_created_at(self, tmp_path):
        m = _read_manifest(_make_config_dir(tmp_path))
        assert "created_at" in m


class TestHealthSnapshot:
    def _read_health(self, config_dir: Path) -> dict[str, Any]:
        zf = _open_bundle(config_dir)
        name = next(n for n in zf.namelist() if n.endswith("/health_snapshot.json"))
        return json.loads(zf.read(name))

    def test_health_has_dvr_count(self, tmp_path):
        health = self._read_health(_make_config_dir(tmp_path))
        assert "dvr_count" in health
        assert health["dvr_count"] == 1

    def test_health_dvr_count_zero_when_no_servers(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text(json.dumps({"dvr_servers": []}))
        health = self._read_health(config_dir)
        assert health["dvr_count"] == 0

    def test_health_excludes_file_inventory(self, tmp_path):
        health = self._read_health(_make_config_dir(tmp_path))
        assert "config_files_present" not in health
        assert "config_files_missing" not in health
        assert "encryption_key_present" not in health
        assert "session_state_file_count" not in health

    def test_health_excludes_dvr_identifiers(self, tmp_path):
        health_text = json.dumps(self._read_health(_make_config_dir(tmp_path)))
        assert "dvr_abc12345" not in health_text
        assert "Living Room" not in health_text

    def test_health_counts_only_enabled_non_deleted_dvrs(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        settings = {
            "dvr_servers": [
                {"id": "a", "enabled": True},
                {"id": "b", "enabled": False},
                {"id": "c", "enabled": True, "deleted_at": "2026-01-01"},
                {"id": "d", "enabled": True},
            ]
        }
        (config_dir / "settings.json").write_text(json.dumps(settings))
        health = self._read_health(config_dir)
        assert health["dvr_count"] == 2


class TestSettingsSanitization:
    def _read_sanitized_settings(self, config_dir: Path) -> dict[str, Any]:
        zf = _open_bundle(config_dir)
        name = next(n for n in zf.namelist() if n.endswith("/settings_sanitized.json"))
        return json.loads(zf.read(name))

    def test_api_key_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["api_key"] == "****"

    def test_feed_tokens_are_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["ics_feed_token"] == "****"
        assert s["rss_feed_token"] == "****"
        data = json.dumps(s)
        assert "calendar-feed-secret" not in data
        assert "rss-feed-secret" not in data

    def test_apprise_pushover_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["apprise_pushover"] == "****"

    def test_apprise_discord_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["apprise_discord"] == "****"

    def test_error_reporting_dsn_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["error_reporting_dsn"] == "****"

    def test_dvr_host_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        for server in s["dvr_servers"]:
            assert server["host"] == "****"

    def test_dvr_port_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        for server in s["dvr_servers"]:
            assert server["port"] == "****"

    def test_dvr_api_key_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        for server in s["dvr_servers"]:
            assert server["api_key"] == "****"

    def test_dvr_id_and_name_preserved(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        server = s["dvr_servers"][0]
        assert server["id"] == "dvr_abc12345"
        assert server["name"] == "Living Room"

    def test_webhook_url_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        for webhook in s["webhooks"]:
            assert webhook["url"] == "****"

    def test_webhook_secret_is_masked(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        for webhook in s["webhooks"]:
            assert webhook["secret"] == "****"

    def test_webhook_enabled_preserved(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["webhooks"][0]["enabled"] is True

    def test_non_sensitive_fields_preserved(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["tz"] == "UTC"
        assert s["log_level"] == 1
        assert s["_version"] == 7

    def test_empty_sensitive_field_becomes_empty_string(self, tmp_path):
        s = self._read_sanitized_settings(_make_config_dir(tmp_path))
        assert s["apprise_email"] == ""

    def test_real_host_ip_not_present_in_sanitized(self, tmp_path):
        data = json.dumps(self._read_sanitized_settings(_make_config_dir(tmp_path)))
        assert "192.168.1.100" not in data

    def test_real_dsn_not_present_in_sanitized(self, tmp_path):
        data = json.dumps(self._read_sanitized_settings(_make_config_dir(tmp_path)))
        assert "glitchtip.example.com" not in data


class TestLogRedaction:
    def _read_log(self, config_dir: Path) -> str:
        zf = _open_bundle(config_dir)
        name = next(n for n in zf.namelist() if n.endswith("/logs/app.log"))
        return zf.read(name).decode("utf-8")

    def test_log_stored_at_logs_app_log(self, tmp_path):
        zf = _open_bundle(_make_config_dir(tmp_path))
        assert any(n.endswith("/logs/app.log") for n in zf.namelist())

    def test_ipv4_addresses_redacted_in_logs(self, tmp_path):
        log_text = self._read_log(_make_config_dir(tmp_path))
        assert "192.168.1.100" not in log_text
        assert "10.0.0.5" not in log_text
        assert "[REDACTED_IP]" in log_text

    def test_url_host_redacted_in_logs(self, tmp_path):
        log_text = self._read_log(_make_config_dir(tmp_path))
        assert "hooks.example.com" not in log_text
        assert "[REDACTED_URL]" in log_text

    def test_full_url_path_query_fragment_and_userinfo_redacted(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "channelwatch.log").write_text(
            "Webhook failed https://user:pass@discord.com/api/webhooks/123/SECRET_TOKEN?wait=true#frag",
            encoding="utf-8",
        )

        log_text = self._read_log(config_dir)

        assert "SECRET_TOKEN" not in log_text
        assert "/api/webhooks" not in log_text
        assert "user:pass" not in log_text
        assert "wait=true" not in log_text
        assert "#frag" not in log_text
        assert log_text.strip() == "Webhook failed [REDACTED_URL]"

    def test_non_sensitive_log_content_preserved(self, tmp_path):
        log_text = self._read_log(_make_config_dir(tmp_path))
        assert "Normal log line without sensitive data" in log_text

    def test_log_tail_limit_500_lines(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        lines = [f"Line {i}" for i in range(1000)]
        (config_dir / "channelwatch.log").write_text("\n".join(lines), encoding="utf-8")
        from core.helpers.debug_bundle import create_debug_bundle

        zf = zipfile.ZipFile(io.BytesIO(create_debug_bundle(config_dir)))
        name = next(n for n in zf.namelist() if n.endswith("/logs/app.log"))
        content = zf.read(name).decode("utf-8")
        returned_lines = [ln for ln in content.splitlines() if ln]
        assert len(returned_lines) == 500

    def test_log_tail_reads_only_needed_suffix_for_large_log(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        log_file = config_dir / "channelwatch.log"
        lines = [f"Line {i:04d} " + ("x" * 900) for i in range(2000)]
        log_file.write_text("\n".join(lines), encoding="utf-8")
        file_size = log_file.stat().st_size
        bytes_read = 0
        original_open = Path.open

        class CountingReader:
            def __init__(self, wrapped):
                self._wrapped = wrapped

            def __enter__(self):
                self._wrapped.__enter__()
                return self

            def __exit__(self, *args):
                return self._wrapped.__exit__(*args)

            def seek(self, *args):
                return self._wrapped.seek(*args)

            def tell(self):
                return self._wrapped.tell()

            def read(self, size=-1):
                nonlocal bytes_read
                data = self._wrapped.read(size)
                bytes_read += len(data)
                return data

        def counting_open(path, *args, **kwargs):
            opened = original_open(path, *args, **kwargs)
            if path == log_file and "b" in (
                args[0] if args else kwargs.get("mode", "r")
            ):
                return CountingReader(opened)
            return opened

        monkeypatch.setattr(Path, "open", counting_open)

        from core.helpers.debug_bundle import create_debug_bundle

        zf = zipfile.ZipFile(io.BytesIO(create_debug_bundle(config_dir)))
        name = next(n for n in zf.namelist() if n.endswith("/logs/app.log"))
        returned_lines = zf.read(name).decode("utf-8").splitlines()

        assert len(returned_lines) == 500
        assert returned_lines[0].startswith("Line 1500")
        assert bytes_read < file_size

    def test_empty_log_produces_empty_file(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        from core.helpers.debug_bundle import create_debug_bundle

        zf = zipfile.ZipFile(io.BytesIO(create_debug_bundle(config_dir)))
        name = next(n for n in zf.namelist() if n.endswith("/logs/app.log"))
        assert zf.read(name).decode("utf-8").strip() == ""


class TestDsnDefault:
    def test_dsn_defaults_to_empty_in_core_settings(self):
        from core.helpers.config import CoreSettings
        import dataclasses

        defaults = {
            f.name: f.default
            for f in dataclasses.fields(CoreSettings)
            if f.default is not dataclasses.MISSING
        }
        assert "error_reporting_dsn" in defaults
        assert (
            defaults["error_reporting_dsn"] == ""
            or defaults["error_reporting_dsn"] is None
        )

    def test_dsn_defaults_to_empty_in_app_settings(self):
        from ui.backend.schemas import AppSettings

        settings = AppSettings()
        assert (
            settings.error_reporting_dsn == "" or settings.error_reporting_dsn is None
        )


class TestDoctorCli:
    def test_doctor_module_importable(self):
        from core.cli.doctor import build_parser, run

        assert callable(build_parser)
        assert callable(run)

    def test_doctor_parser_has_debug_subcommand(self):
        from core.cli.doctor import build_parser

        parser = build_parser()
        args = parser.parse_args(["debug", "bundle"])
        assert args.command == "debug"
        assert args.debug_command == "bundle"

    def test_doctor_debug_bundle_default_output(self):
        from core.cli.doctor import build_parser

        parser = build_parser()
        args = parser.parse_args(["debug", "bundle"])
        assert args.output == "channelwatch_debug_bundle.zip"

    def test_doctor_debug_bundle_custom_output(self):
        from core.cli.doctor import build_parser

        parser = build_parser()
        args = parser.parse_args(["debug", "bundle", "--output", "/tmp/test.zip"])
        assert args.output == "/tmp/test.zip"

    def test_doctor_debug_bundle_writes_zip(self, tmp_path):
        from core.cli.doctor import run

        out = tmp_path / "bundle.zip"
        with patch.dict("os.environ", {"CONFIG_PATH": str(tmp_path)}):
            (tmp_path / "settings.json").write_text(json.dumps({"dvr_servers": []}))
            run(["debug", "bundle", "--output", str(out)])
        assert out.exists()
        assert zipfile.is_zipfile(out)


class TestBundleEndpoint:
    def test_debug_bundle_endpoint_returns_zip(self, tmp_path):
        config_dir = _make_config_dir(tmp_path / "config")

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = config_dir / "settings.json"

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", config_dir),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.get(
                "/api/v1/debug/bundle",
                headers={"X-API-Key": "super-secret-key"},
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert zipfile.is_zipfile(io.BytesIO(resp.content))

    def test_debug_bundle_endpoint_requires_auth(self, tmp_path):
        config_dir = _make_config_dir(tmp_path / "config")

        from starlette.testclient import TestClient
        import ui.backend.main as ui_main

        settings_file = config_dir / "settings.json"

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "API_KEY_CACHE", "testkey"),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(ui_main, "CONFIG_DIR", config_dir),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.get("/api/v1/debug/bundle")

        assert resp.status_code in (401, 403)
