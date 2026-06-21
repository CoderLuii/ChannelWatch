"""Regression tests for supervisor credential handling."""

import importlib.util
import json
import os
import stat
import xmlrpc.client
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient


_REPO_DIR = Path(__file__).resolve().parents[3]
_ENTRYPOINT = _REPO_DIR / "app" / "core" / "docker-entrypoint.py"
_CONF_TEMPLATE = (
    _REPO_DIR / "deploy" / "config" / "supervisor" / "supervisord.conf.template"
)


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location("channelwatch_entrypoint", _ENTRYPOINT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestEntrypointWritesAuthFile:
    def test_entrypoint_writes_auth_file(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "channelwatch" / "supervisor.auth"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(entrypoint, "SUPERVISOR_AUTH_DIR", auth_file.parent)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_AUTH_FILE", auth_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000)

        assert auth_file.exists()
        assert "user=" in auth_file.read_text()
        assert "pass=" in auth_file.read_text()
        rendered = conf_file.read_text()
        assert "__SUPERVISOR_USER__" not in rendered
        assert "__SUPERVISOR_PASS__" not in rendered

        if os.name != "nt":
            assert stat.S_IMODE(auth_file.stat().st_mode) == 0o640

    def test_entrypoint_auth_file_uses_runtime_group_for_custom_pgid(self):
        content = _ENTRYPOINT.read_text()

        assert "chown_path(path, 0, app_gid)" in content
        assert "(SUPERVISOR_AUTH_DIR, 0o750)" in content
        assert "(SUPERVISOR_AUTH_FILE, 0o640)" in content

    def test_rendered_supervisord_config_is_not_world_readable(
        self, tmp_path, monkeypatch
    ):
        auth_file = tmp_path / "channelwatch" / "supervisor.auth"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(entrypoint, "SUPERVISOR_AUTH_DIR", auth_file.parent)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_AUTH_FILE", auth_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000)

        assert "username =" in conf_file.read_text()
        if os.name != "nt":
            assert stat.S_IMODE(conf_file.stat().st_mode) == 0o640


class TestEntrypointDoesNotExportEnvVars:
    def test_entrypoint_does_not_export_env_vars(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "channelwatch" / "supervisor.auth"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.delenv("SUPERVISOR_USER", raising=False)
        monkeypatch.delenv("SUPERVISOR_PASS", raising=False)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_AUTH_DIR", auth_file.parent)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_AUTH_FILE", auth_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000)

        assert os.environ.get("SUPERVISOR_USER") is None
        assert os.environ.get("SUPERVISOR_PASS") is None


class TestMainLazyReadsAuthFile:
    def test_supervisor_auth_file_defaults_to_tmp_runtime_dir(self):
        import ui.backend.main as ui_main

        assert ui_main.SUPERVISOR_AUTH_FILE == os.path.join(
            "/tmp/channelwatch", "supervisor.auth"
        )

    def test_main_lazy_reads_auth_file(self, tmp_path):
        import ui.backend.main as ui_main

        auth_file = tmp_path / "supervisor.auth"
        auth_file.write_text("user=cwuser\npass=cwpass\n")
        with patch.object(ui_main, "SUPERVISOR_AUTH_FILE", str(auth_file)):
            url = ui_main._get_supervisor_url()

        assert url is not None
        assert "cwuser:cwpass" in url
        assert url.endswith("/RPC2")

    def test_supervisor_protocol_errors_are_logged_without_credentials(
        self, tmp_path, capsys
    ):
        import ui.backend.main as ui_main

        auth_file = tmp_path / "supervisor.auth"
        auth_file.write_text("user=cwuser\npass=cwpass\n")
        protocol_error = xmlrpc.client.ProtocolError(
            "http://cwuser:cwpass@127.0.0.1:9001/RPC2",
            401,
            "Unauthorized",
            {},
        )

        with (
            patch.object(ui_main, "SUPERVISOR_AUTH_FILE", str(auth_file)),
            patch(
                "ui.backend.main.xmlrpc.client.ServerProxy", side_effect=protocol_error
            ),
        ):
            assert ui_main.get_supervisor_proxy() is None

        output = capsys.readouterr().out
        assert "ProtocolError 401 Unauthorized" in output
        assert "cwpass" not in output
        assert "cwuser:cwpass" not in output


class TestMainGracefulDegrade:
    def test_main_graceful_degrade_when_file_missing(self, tmp_path):
        import ui.backend.main as ui_main

        missing = str(tmp_path / "no_such_file.auth")

        with patch.object(ui_main, "SUPERVISOR_AUTH_FILE", missing):
            url = ui_main._get_supervisor_url()
        assert url is None

        with patch.object(ui_main, "SUPERVISOR_AUTH_FILE", missing):
            proxy = ui_main.get_supervisor_proxy()
        assert proxy is None


class TestTemplateNoCreds:
    def test_template_keeps_supervisor_runtime_files_off_app_root(self):
        content = _CONF_TEMPLATE.read_text()

        assert "logfile=/dev/null" in content
        assert "logfile_maxbytes=0" in content
        assert "pidfile=/tmp/supervisord.pid" in content
        assert "childlogdir=/tmp" in content

    def test_template_no_creds_in_program_ui_environment(self):
        content = _CONF_TEMPLATE.read_text()

        in_ui_section = False
        section_found = False

        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "[program:ui]":
                in_ui_section = True
                section_found = True
                continue
            if stripped.startswith("[") and stripped.endswith("]") and in_ui_section:
                break
            if in_ui_section and stripped.lower().startswith("environment="):
                assert "SUPERVISOR_USER" not in line
                assert "SUPERVISOR_PASS" not in line
                break

        assert section_found


class TestRestartCoreDegradedResponse:
    def test_restart_core_returns_degraded_response_when_auth_file_missing(
        self, tmp_path
    ):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"dvr_servers": [], "api_key": "test-key"}))
        missing_auth = str(tmp_path / "no_such_file.auth")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "SUPERVISOR_AUTH_FILE", missing_auth),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_AUTH_MISSING"
        assert "Supervisor authentication unavailable" in detail["message"]
        assert "auth file" in detail["message"]
        assert "regenerate the supervisor credentials" in detail["remediation"]


class _ImmediateThread:
    def __init__(self, target):
        self.target = target
        self.daemon = False

    def start(self):
        self.target()


class TestRestartControlEndpoints:
    def test_restart_core_supervisor_success_updates_start_time(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        server = MagicMock()
        started_after = datetime.now(timezone.utc)

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "CORE_LAST_START_TIME", None),
            patch.object(ui_main, "get_supervisor_proxy", return_value=server),
            patch.object(ui_main.asyncio, "sleep", AsyncMock()),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")
            updated_start = ui_main.CORE_LAST_START_TIME

        assert resp.status_code == 202
        assert resp.json()["message"] == "Restart command sent to process 'core'."
        server.supervisor.stopProcess.assert_called_once_with("core", True)
        server.supervisor.startProcess.assert_called_once_with("core", True)
        assert updated_start is not None
        assert updated_start >= started_after

    def test_restart_core_supervisor_401_fault_returns_401(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        server = MagicMock()
        server.supervisor.stopProcess.side_effect = xmlrpc.client.Fault(
            401, "Unauthorized"
        )

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "get_supervisor_proxy", return_value=server),
            patch.object(ui_main.asyncio, "sleep", AsyncMock()),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")

        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_AUTH_FAILED"
        assert detail["message"] == "Supervisor authentication failed."
        assert "regenerate supervisor credentials" in detail["remediation"]
        server.supervisor.startProcess.assert_not_called()

    def test_restart_core_supervisor_non_401_fault_returns_500(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        server = MagicMock()
        server.supervisor.stopProcess.side_effect = xmlrpc.client.Fault(42, "boom")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "get_supervisor_proxy", return_value=server),
            patch.object(ui_main.asyncio, "sleep", AsyncMock()),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_COMMAND_FAILED"
        assert detail["message"] == "Supervisor command failed: boom"
        assert "supervisord logs" in detail["remediation"]
        server.supervisor.startProcess.assert_not_called()

    def test_restart_container_uses_supervisor_shutdown_when_available(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')
        server = MagicMock()

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "get_supervisor_proxy", return_value=server),
            patch.object(ui_main.time, "sleep", MagicMock()),
            patch("threading.Thread", _ImmediateThread),
            patch.object(ui_main.os, "kill") as kill_mock,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_container")

        assert resp.status_code == 202
        server.supervisor.shutdown.assert_called_once_with()
        kill_mock.assert_not_called()

    def test_restart_container_falls_back_to_pid_one_sigterm(self, tmp_path):
        import signal
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "get_supervisor_proxy", return_value=None),
            patch.object(ui_main.time, "sleep", MagicMock()),
            patch("threading.Thread", _ImmediateThread),
            patch.object(ui_main.os, "kill") as kill_mock,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_container")

        assert resp.status_code == 202
        kill_mock.assert_called_once_with(1, signal.SIGTERM)
