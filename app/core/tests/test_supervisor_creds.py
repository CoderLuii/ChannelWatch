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


class TestEntrypointWritesSupervisorSocketConfig:
    def test_entrypoint_writes_socket_config_without_credentials(self, tmp_path, monkeypatch):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000, 1000)

        assert not (runtime_dir / "supervisor.auth").exists()
        rendered = conf_file.read_text()
        assert str(socket_file) in rendered
        assert "__SUPERVISOR_SOCKET__" not in rendered
        assert "username =" not in rendered
        assert "password =" not in rendered

        if os.name != "nt":
            assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700

    def test_entrypoint_socket_dir_uses_runtime_user_for_custom_ids(self):
        content = _ENTRYPOINT.read_text()

        assert "chown_path(path, app_uid, app_gid)" in content
        assert "(SUPERVISOR_RUNTIME_DIR, 0o700)" in content
        assert "(SUPERVISOR_CONF, 0o640)" in content

    def test_rendered_supervisord_config_is_not_world_readable(
        self, tmp_path, monkeypatch
    ):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000, 1000)

        assert "unix_http_server" in conf_file.read_text()
        if os.name != "nt":
            assert stat.S_IMODE(conf_file.stat().st_mode) == 0o640


class TestEntrypointDoesNotExportEnvVars:
    def test_entrypoint_does_not_export_env_vars(self, tmp_path, monkeypatch):
        runtime_dir = tmp_path / "channelwatch"
        socket_file = runtime_dir / "supervisor.sock"
        conf_file = tmp_path / "supervisord.conf"
        entrypoint = _load_entrypoint()

        monkeypatch.delenv("SUPERVISOR_USER", raising=False)
        monkeypatch.delenv("SUPERVISOR_PASS", raising=False)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_RUNTIME_DIR", runtime_dir)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_SOCKET", socket_file)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_TEMPLATE", _CONF_TEMPLATE)
        monkeypatch.setattr(entrypoint, "SUPERVISOR_CONF", conf_file)

        entrypoint.render_supervisor_config(1000, 1000)

        assert os.environ.get("SUPERVISOR_USER") is None
        assert os.environ.get("SUPERVISOR_PASS") is None


class TestMainUsesSupervisorSocket:
    def test_supervisor_socket_defaults_to_tmp_runtime_dir(self):
        import ui.backend.main as ui_main

        assert ui_main.SUPERVISOR_SOCKET_FILE == os.path.join(
            "/tmp/channelwatch", "supervisor.sock"
        )

    def test_main_creates_socket_transport_without_credentials(self, tmp_path):
        import ui.backend.main as ui_main

        socket_file = tmp_path / "supervisor.sock"
        socket_file.write_text("")
        server = MagicMock()
        with (
            patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", str(socket_file)),
            patch("ui.backend.main.xmlrpc.client.ServerProxy", return_value=server) as proxy,
        ):
            assert ui_main.get_supervisor_proxy() is server

        url = proxy.call_args.args[0]
        transport = proxy.call_args.kwargs["transport"]
        assert url == "http://channelwatch-supervisor/RPC2"
        assert "@" not in url
        assert isinstance(transport, ui_main._UnixSocketTransport)

    def test_supervisor_protocol_errors_are_logged_without_credentials(
        self, tmp_path, capsys
    ):
        import ui.backend.main as ui_main

        socket_file = tmp_path / "supervisor.sock"
        socket_file.write_text("")
        protocol_error = xmlrpc.client.ProtocolError(
            "http://channelwatch-supervisor/RPC2",
            401,
            "Unauthorized",
            {},
        )

        with (
            patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", str(socket_file)),
            patch(
                "ui.backend.main.xmlrpc.client.ServerProxy", side_effect=protocol_error
            ),
        ):
            assert ui_main.get_supervisor_proxy() is None

        output = capsys.readouterr().out
        assert "ProtocolError 401 Unauthorized" in output
        assert "@" not in output


class TestMainGracefulDegrade:
    def test_main_graceful_degrade_when_file_missing(self, tmp_path):
        import ui.backend.main as ui_main

        missing = str(tmp_path / "no_such_file.sock")

        with patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", missing):
            proxy = ui_main.get_supervisor_proxy()
        assert proxy is None


class TestTemplateNoCreds:
    def test_template_keeps_supervisor_runtime_files_off_app_root(self):
        content = _CONF_TEMPLATE.read_text()

        assert "logfile=/dev/null" in content
        assert "logfile_maxbytes=0" in content
        assert "pidfile=/tmp/supervisord.pid" in content
        assert "childlogdir=/tmp" in content
        assert "[unix_http_server]" in content
        assert "inet_http_server" not in content
        assert "username =" not in content
        assert "password =" not in content

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

    def test_template_uses_image_stable_runtime_launcher(self):
        content = _CONF_TEMPLATE.read_text()

        assert "python -u /app/core/runtime_launcher.py core --stay-alive" in content
        assert "python -u /app/core/runtime_launcher.py ui" in content
        assert "CHANNELWATCH_ACTIVE_APP_DIR=__APP_DIR__" in content
        assert "CHANNELWATCH_ACTIVE_STATIC_UI_DIR=__STATIC_UI_DIR__" in content
        assert "directory=/app" in content
        assert "command=uvicorn ui.backend.main:app" not in content


class TestRestartCoreDegradedResponse:
    def test_restart_core_returns_degraded_response_when_socket_missing(
        self, tmp_path
    ):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"dvr_servers": [], "api_key": "test-key"}))
        missing_socket = str(tmp_path / "no_such_file.sock")

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "SUPERVISOR_SOCKET_FILE", missing_socket),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_core")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_AUTH_MISSING"
        assert "Supervisor control socket is unavailable" in detail["message"]
        assert "recreate the local supervisor socket" in detail["remediation"]


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
            patch.object(ui_main, "_can_signal_pid_one_restart", return_value=True),
            patch.object(ui_main.time, "sleep", MagicMock()),
            patch("threading.Thread", _ImmediateThread),
            patch.object(ui_main.os, "kill") as kill_mock,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_container")

        assert resp.status_code == 202
        kill_mock.assert_called_once_with(1, signal.SIGTERM)

    def test_restart_container_returns_503_when_no_supervisor_or_pid_one(self, tmp_path):
        import ui.backend.main as ui_main

        settings_file = tmp_path / "settings.json"
        settings_file.write_text('{"dvr_servers": [], "api_key": "test-key"}')

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", tmp_path),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "get_supervisor_proxy", return_value=None),
            patch.object(ui_main, "_can_signal_pid_one_restart", return_value=False),
            patch("threading.Thread") as thread_mock,
            patch.object(ui_main.os, "kill") as kill_mock,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            resp = client.post("/api/restart_container")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "ERR_SUPERVISOR_NOT_AVAILABLE"
        thread_mock.assert_not_called()
        kill_mock.assert_not_called()
