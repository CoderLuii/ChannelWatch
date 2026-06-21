import json
import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlmodel import select

from core.helpers.config import ConfigLoadError
from core.helpers.migration import CURRENT_SCHEMA_VERSION, migrate_settings
from core.storage import (
    ActivityEvent,
    create_all_tables,
    create_db_engine,
    get_session,
    migrate_activity_history,
)
from ui.backend.schemas import AppSettings


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


def test_core_settings_corrupt_json_fails_closed(tmp_path):
    from core.helpers.config import CoreSettings

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{bad json}", encoding="utf-8")

    with (
        patch("core.helpers.config.CONFIG_FILE", settings_file),
        patch("core.helpers.config.CONFIG_DIR", tmp_path),
    ):
        CoreSettings._instance = None
        with pytest.raises(ConfigLoadError, match="Startup is blocked"):
            CoreSettings()


def test_ui_save_settings_atomic_replace_preserves_previous_file_on_failure(tmp_path):
    from ui.backend import config as ui_config

    settings_file = tmp_path / "settings.json"
    original = {"_version": 7, "tz": "UTC", "dvr_servers": []}
    settings_file.write_text(json.dumps(original), encoding="utf-8")

    with (
        patch.object(ui_config, "CONFIG_FILE", settings_file),
        patch.object(ui_config, "CONFIG_DIR", tmp_path),
        patch(
            "core.helpers.atomic_io.os.replace", side_effect=OSError("replace failed")
        ),
    ):
        with pytest.raises(OSError, match="replace failed"):
            ui_config.save_settings(AppSettings(tz="America/New_York"))

    assert json.loads(settings_file.read_text(encoding="utf-8")) == original
    assert (tmp_path / "settings.json.tmp").exists()


def test_ui_save_settings_uses_core_schema_version_for_new_files(tmp_path):
    from ui.backend import config as ui_config

    settings_file = tmp_path / "settings.json"

    with (
        patch.object(ui_config, "CONFIG_FILE", settings_file),
        patch.object(ui_config, "CONFIG_DIR", tmp_path),
    ):
        ui_config.save_settings(AppSettings(tz="UTC"))

    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["_version"] == CURRENT_SCHEMA_VERSION


def test_core_settings_preserves_persisted_rbac_enabled(tmp_path):
    from core.helpers.config import CoreSettings

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "_version": CURRENT_SCHEMA_VERSION,
                "tz": "UTC",
                "dvr_servers": [],
                "rbac_enabled": True,
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("core.helpers.config.CONFIG_FILE", settings_file),
        patch("core.helpers.config.CONFIG_DIR", tmp_path),
    ):
        CoreSettings._instance = None
        settings = CoreSettings()

    assert settings.rbac_enabled is True
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["rbac_enabled"] is True


def test_core_settings_skips_malformed_dvr_server_entries(tmp_path):
    from core.helpers.config import CoreSettings

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "_version": CURRENT_SCHEMA_VERSION,
                "tz": "UTC",
                "dvr_servers": [
                    "not-a-dict",
                    {
                        "id": "good",
                        "name": "Good DVR",
                        "host": "192.168.1.50",
                        "port": 8089,
                        "enabled": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("core.helpers.config.CONFIG_FILE", settings_file),
        patch("core.helpers.config.CONFIG_DIR", tmp_path),
    ):
        CoreSettings._instance = None
        settings = CoreSettings()

    connections = settings.get_dvr_connections()
    assert [connection.id for connection in connections] == ["good"]


def test_ui_backend_runtime_paths_follow_config_path(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"_version": CURRENT_SCHEMA_VERSION, "dvr_servers": []}),
        encoding="utf-8",
    )
    source_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["CONFIG_PATH"] = str(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), env.get("PYTHONPATH", "")) if part
    )
    code = """
import json
from unittest.mock import patch
import ui.backend.main as m

settings = m.AppSettings(log_retention_days=3)
with (
    patch.object(m, "load_settings", return_value=settings),
    patch.object(m, "_get_core_settings_sync", return_value=settings),
    patch.object(m, "_refresh_runtime_auth_state"),
    patch.object(m, "_bootstrap_admin_from_env"),
    patch.object(m, "ensure_history_file_watcher_started"),
    patch.object(m.threading, "Thread"),
    patch("core.helpers.logging.setup_logging") as setup_logging,
):
    m.CORE_APP_AVAILABLE = True
    m.RBAC_ENABLED = False
    m._STORAGE_AVAILABLE = False
    m.run_startup_initialization()
    security_path = m._build_security_status().encryption_key_path
    setup_path = setup_logging.call_args.args[0]

print(json.dumps({
    "config_dir": str(m.CONFIG_DIR),
    "history_file": str(m.HISTORY_FILE),
    "activity_db_file": str(m._ACTIVITY_DB_FILE),
    "activity_db_url": m._ACTIVITY_DB_URL,
    "log_file": str(m.LOG_FILE),
    "security_path": security_path,
    "setup_path": setup_path,
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout.splitlines()[-1])

    assert payload["config_dir"] == str(tmp_path)
    assert payload["history_file"] == str(tmp_path / "activity_history.json")
    assert payload["activity_db_file"] == str(tmp_path / "channelwatch.db")
    assert payload["activity_db_url"] == f"sqlite:///{tmp_path / 'channelwatch.db'}"
    assert payload["log_file"] == str(tmp_path / "channelwatch.log")
    assert payload["security_path"] == str(tmp_path / "encryption.key")
    assert payload["setup_path"] == str(tmp_path)


def test_diagnostic_test_logging_fallback_uses_config_path(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"_version": CURRENT_SCHEMA_VERSION, "dvr_servers": []}),
        encoding="utf-8",
    )
    source_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["CONFIG_PATH"] = str(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), env.get("PYTHONPATH", "")) if part
    )
    code = """
import json
from types import SimpleNamespace
from unittest.mock import patch
import ui.backend.main as m

settings = SimpleNamespace(
    dvr_servers=[{
        "id": "dvr_test",
        "host": "127.0.0.1",
        "port": 8089,
        "name": "Test DVR",
        "enabled": True,
    }],
    log_retention_days=3,
    get_dvr_connections=lambda: [],
)
with (
    patch.object(m, "CORE_APP_AVAILABLE", True),
    patch.object(m, "load_settings", return_value=m.AppSettings(log_retention_days=3)),
    patch.object(m, "_get_core_settings_sync", return_value=settings),
    patch.object(
        m,
        "_get_dvr_servers",
        return_value=[("dvr_test", "Test DVR", "http://127.0.0.1:8089")],
    ),
    patch("core.helpers.logging.log_handler", None),
    patch("core.helpers.logging.setup_logging") as setup_logging,
    patch("core.diagnostics.run_test", return_value=True),
):
    result = m.run_test_background("Test Connectivity")

print(json.dumps({
    "success": result.success,
    "setup_path": setup_logging.call_args.args[0],
    "retention_days": setup_logging.call_args.kwargs["retention_days"],
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout.splitlines()[-1])

    assert payload["success"] is True
    assert payload["setup_path"] == str(tmp_path)
    assert payload["setup_path"] != "/config"
    assert payload["retention_days"] == 3


def test_activity_recorder_writes_history_under_config_path(tmp_path, monkeypatch):
    from core.helpers import activity_recorder

    monkeypatch.setenv("CONFIG_PATH", str(tmp_path))
    reloaded = importlib.reload(activity_recorder)
    try:
        assert reloaded.record_activity(
            activity_type="system",
            title="Config path test",
            message="Activity recorder honors CONFIG_PATH",
            notification_history={},
        )
    finally:
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        importlib.reload(activity_recorder)

    history_file = tmp_path / "activity_history.json"
    assert history_file.is_file()
    history = json.loads(history_file.read_text(encoding="utf-8"))
    assert history[0]["title"] == "Config path test"


def test_app_settings_auth_mode_accepts_legacy_empty_and_known_modes():
    assert AppSettings.model_validate({"auth_mode": None}).auth_mode == ""
    assert AppSettings.model_validate({"auth_mode": ""}).auth_mode == ""
    assert AppSettings.model_validate({"auth_mode": " RBAC "}).auth_mode == "rbac"


def test_app_settings_auth_mode_rejects_unknown_values():
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"auth_mode": "password"})


def test_backend_startup_propagates_config_load_error():
    import ui.backend.main as main_mod

    with (
        patch.object(main_mod, "CORE_APP_AVAILABLE", True),
        patch.object(
            main_mod,
            "_get_core_settings_sync",
            side_effect=ConfigLoadError("Corrupt config"),
        ),
        patch.object(main_mod, "load_settings") as mock_load,
    ):
        with pytest.raises(ConfigLoadError, match="Corrupt config"):
            main_mod.run_startup_initialization()

    mock_load.assert_not_called()


def test_migrate_settings_recovers_from_started_journal_via_backup(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"_version": 7, "dvr_servers": [{"id": "partial"}]}),
        encoding="utf-8",
    )

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_file = backup_dir / "settings.v6.20260420_120000.json"
    backup_file.write_text(json.dumps(V6_SETTINGS), encoding="utf-8")

    journal_file = tmp_path / "migration.journal"
    journal_file.write_text(
        json.dumps(
            {
                "step": "schema_migrations",
                "status": "started",
                "from_version": 6,
                "to_version": 7,
                "backup_path": str(backup_file),
            }
        ),
        encoding="utf-8",
    )

    result = migrate_settings(
        tmp_path, {"_version": 7, "dvr_servers": [{"id": "partial"}]}
    )

    assert result["_version"] == CURRENT_SCHEMA_VERSION
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["_version"] == CURRENT_SCHEMA_VERSION
    journal = json.loads(journal_file.read_text(encoding="utf-8"))
    assert journal["status"] == "completed"
    assert journal["step"] == "persist_settings"
    assert Path(journal["backup_path"]).is_file()
    assert Path(journal["backup_path"]).name.startswith("settings.v6.")


def test_db_migration_integrity_failure_does_not_swap_existing_db(tmp_path):
    db_path = tmp_path / "channelwatch.db"
    db_url = f"sqlite:///{db_path}"
    json_path = tmp_path / "activity_history.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "id": "new-row",
                    "type": "watching_channel",
                    "title": "New Event",
                    "message": "msg",
                    "timestamp": "2026-04-20T12:00:00+00:00",
                    "dvr_id": "dvr_test",
                    "dvr_name": "Test DVR",
                    "extra": {},
                }
            ]
        ),
        encoding="utf-8",
    )

    engine = create_db_engine(db_url)
    create_all_tables(engine)
    with get_session(engine) as session:
        session.add(
            ActivityEvent(
                id="existing-row",
                dvr_id="dvr_existing",
                event_type="watching_channel",
                title="Existing",
                message="existing",
            )
        )
        session.commit()
    engine.dispose()

    class _FakeCursor:
        def fetchone(self):
            return ("not ok",)

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _query):
            return _FakeCursor()

    with patch("core.storage.migrate_json.sqlite3.connect", return_value=_FakeConn()):
        with pytest.raises(RuntimeError, match="Integrity check failed"):
            migrate_activity_history(json_path=str(json_path), db_url=db_url)

    verify_engine = create_db_engine(db_url)
    with get_session(verify_engine) as session:
        rows = list(session.exec(select(ActivityEvent)).all())
    verify_engine.dispose()

    assert [row.id for row in rows] == ["existing-row"]
    assert not (tmp_path / "channelwatch.db.new").exists()
