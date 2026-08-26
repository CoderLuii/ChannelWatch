import asyncio
import importlib
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import select
from ui.backend.schemas import AppSettings

from core.helpers.config import ConfigLoadError
from core.helpers.migration import CURRENT_SCHEMA_VERSION, migrate_settings
from core.storage import (
    ActivityEvent,
    create_all_tables,
    create_db_engine,
    get_session,
    migrate_activity_history,
)
from core.update_center import UpdateRestartError

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


def test_runtime_umask_keeps_atomic_config_replacements_private(tmp_path):
    from core.helpers.atomic_io import atomic_write_json

    original_umask = os.umask(0o027)
    try:
        settings_file = tmp_path / "settings.json"
        runtime_file = tmp_path / "channelwatch-runtime" / "active.json"

        atomic_write_json(settings_file, {"api_key": "test-only"})
        atomic_write_json(runtime_file, {"version": "0.9.16"})

        assert stat.S_IMODE(settings_file.stat().st_mode) == 0o640
        assert stat.S_IMODE(runtime_file.parent.stat().st_mode) == 0o750
        assert stat.S_IMODE(runtime_file.stat().st_mode) == 0o640
    finally:
        os.umask(original_umask)


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


def test_ui_schema_error_does_not_echo_invalid_secret_bearing_input(tmp_path):
    import traceback

    from ui.backend import config as ui_config

    marker = "CW_SYNTHETIC_SECRET_MUST_NOT_REACH_LOGS"
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"_version": 7, "webhooks": [marker]}),
        encoding="utf-8",
    )

    with (
        patch.object(ui_config, "CONFIG_DIR", tmp_path),
        patch.object(ui_config, "CONFIG_FILE", settings_file),
        pytest.raises(ConfigLoadError) as exc_info,
    ):
        ui_config.load_settings()

    rendered = "".join(traceback.format_exception(exc_info.value))
    assert marker not in str(exc_info.value)
    assert marker not in rendered
    assert "webhooks.0" in str(exc_info.value)


def test_explicit_core_settings_root_is_disposable_and_does_not_poison_singleton(
    tmp_path,
):
    from core.helpers.config import CoreSettings, get_settings

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "_version": CURRENT_SCHEMA_VERSION,
                "tz": "UTC",
                "dvr_servers": [],
            }
        ),
        encoding="utf-8",
    )

    CoreSettings._instance = None
    settings = get_settings(config_dir=tmp_path)

    assert settings.tz == "UTC"
    assert settings._runtime_config_dir == tmp_path
    assert CoreSettings._instance is None
    assert (tmp_path / ".encryption-key.lock").is_file()


def test_ui_save_settings_atomic_replace_preserves_previous_file_on_failure(tmp_path):
    from ui.backend import config as ui_config

    from core.helpers.encryption import bootstrap_encryption_key

    settings_file = tmp_path / "settings.json"
    original = {"_version": 7, "tz": "UTC", "dvr_servers": []}
    settings_file.write_text(json.dumps(original), encoding="utf-8")
    # Isolate the settings replacement boundary. Managed-key creation has its
    # own fault-injection coverage and otherwise legitimately reaches the
    # patched atomic rename first on a fresh v0.9.18 configuration.
    bootstrap_encryption_key(tmp_path / "encryption.key", settings_file=settings_file)

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
    assert not list(tmp_path.glob(".settings.json.tmp-*"))


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


def test_ui_load_never_observes_mixed_key_and_settings_during_transaction(tmp_path):
    from ui.backend import config as ui_config

    from core.helpers import maintenance_transaction
    from core.helpers.atomic_io import _atomic_write_secret_bytes
    from core.helpers.encryption import encrypt_value

    settings_file = tmp_path / "settings.json"
    key_file = tmp_path / "encryption.key"
    old_key = os.urandom(32)
    new_key = os.urandom(32)
    _atomic_write_secret_bytes(key_file, old_key)
    settings_file.write_text(
        json.dumps(
            {
                "_version": CURRENT_SCHEMA_VERSION,
                "tz": "UTC",
                "dvr_servers": [
                    {
                        "id": "dvr-1",
                        "host": "dvr.lan",
                        "enabled": True,
                        "api_key": encrypt_value("old-secret", old_key),
                    }
                ],
                "webhooks": [],
            }
        ),
        encoding="utf-8",
    )
    new_settings = json.dumps(
        {
            "_version": CURRENT_SCHEMA_VERSION,
            "tz": "UTC",
            "dvr_servers": [
                {
                    "id": "dvr-1",
                    "host": "dvr.lan",
                    "enabled": True,
                    "api_key": encrypt_value("new-secret", new_key),
                }
            ],
            "webhooks": [],
        },
        indent=2,
    ).encode("utf-8")

    mixed_pair_installed = threading.Event()
    allow_transaction_to_finish = threading.Event()
    real_secret_write = maintenance_transaction._atomic_write_secret_bytes

    def pause_after_new_key_install(path, data, *, temp_path=None):
        result = real_secret_write(path, data, temp_path=temp_path)
        if Path(path) == key_file and data == new_key:
            mixed_pair_installed.set()
            if not allow_transaction_to_finish.wait(timeout=5):
                raise TimeoutError("test did not release the transaction")
        return result

    loaded: list[AppSettings] = []
    reader_finished = threading.Event()

    def read_ui_settings():
        loaded.append(ui_config.load_settings())
        reader_finished.set()

    with (
        patch.object(ui_config, "CONFIG_FILE", settings_file),
        patch.object(ui_config, "CONFIG_DIR", tmp_path),
        patch.object(
            maintenance_transaction,
            "_atomic_write_secret_bytes",
            side_effect=pause_after_new_key_install,
        ),
    ):
        writer = threading.Thread(
            target=maintenance_transaction.replace_config_files_transactionally,
            args=(
                tmp_path,
                {"encryption.key": new_key, "settings.json": new_settings},
            ),
        )
        writer.start()
        assert mixed_pair_installed.wait(timeout=5)

        reader = threading.Thread(target=read_ui_settings)
        reader.start()
        assert not reader_finished.wait(timeout=0.1)

        allow_transaction_to_finish.set()
        writer.join(timeout=5)
        reader.join(timeout=5)

    assert not writer.is_alive()
    assert not reader.is_alive()
    assert loaded[0].dvr_servers[0]["api_key"] == "new-secret"


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only flock semantics")
def test_core_settings_uses_optional_defaults_in_memory_on_read_only_config(tmp_path):
    from core.helpers.config import CoreSettings

    settings_file = tmp_path / "settings.json"
    persisted = {
        "_version": CURRENT_SCHEMA_VERSION,
        "tz": "UTC",
        "dvr_servers": [],
        "webhooks": [],
    }
    settings_file.write_text(json.dumps(persisted), encoding="utf-8")
    key_file = tmp_path / "encryption.key"
    lock_file = tmp_path / ".encryption-key.lock"
    key_file.write_bytes(os.urandom(32))
    lock_file.write_bytes(b"")
    key_file.chmod(0o600)
    lock_file.chmod(0o600)
    real_open = os.open

    def read_only_lock_open(path, flags, *args):
        if Path(path) == lock_file and flags & os.O_RDWR:
            raise OSError("simulated read-only remount")
        return real_open(path, flags, *args)

    with (
        patch("core.helpers.config.CONFIG_FILE", settings_file),
        patch("core.helpers.config.CONFIG_DIR", tmp_path),
        patch("core.helpers.key_manager.os.open", side_effect=read_only_lock_open),
        patch(
            "core.helpers.config.atomic_write_private_json",
            side_effect=OSError("simulated read-only remount"),
        ),
    ):
        CoreSettings._instance = None
        settings = CoreSettings()

    assert settings.tz == "UTC"
    assert settings.global_rate_limit == 20
    assert json.loads(settings_file.read_text(encoding="utf-8")) == persisted


def test_core_settings_fails_closed_when_plaintext_protected_migration_cannot_persist(
    tmp_path,
):
    from core.helpers.config import CoreSettings

    settings_file = tmp_path / "settings.json"
    persisted = {
        "_version": CURRENT_SCHEMA_VERSION,
        "tz": "UTC",
        "dvr_servers": [
            {
                "id": "dvr-1",
                "host": "dvr.lan",
                "port": 8089,
                "api_key": "plaintext-needs-migration",
            }
        ],
        "webhooks": [],
    }
    settings_file.write_text(json.dumps(persisted), encoding="utf-8")
    key = os.urandom(32)
    key_file = tmp_path / "encryption.key"
    key_file.write_bytes(key)
    key_file.chmod(0o600)

    with (
        patch("core.helpers.config.CONFIG_FILE", settings_file),
        patch("core.helpers.config.CONFIG_DIR", tmp_path),
        patch(
            "core.helpers.config.atomic_write_private_json",
            side_effect=OSError("simulated persistence failure"),
        ),
    ):
        CoreSettings._instance = None
        with pytest.raises(ConfigLoadError, match="required settings"):
            CoreSettings()

    assert json.loads(settings_file.read_text(encoding="utf-8")) == persisted
    assert key_file.read_bytes() == key


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only flock semantics")
def test_core_settings_fails_closed_when_schema_migration_is_read_only(tmp_path):
    from core.helpers.config import CoreSettings

    settings_file = tmp_path / "settings.json"
    persisted = dict(V6_SETTINGS)
    settings_file.write_text(json.dumps(persisted), encoding="utf-8")
    key_file = tmp_path / "encryption.key"
    lock_file = tmp_path / ".encryption-key.lock"
    key_file.write_bytes(os.urandom(32))
    lock_file.write_bytes(b"")
    key_file.chmod(0o600)
    lock_file.chmod(0o600)
    real_open = os.open

    def read_only_lock_open(path, flags, *args):
        if Path(path) == lock_file and flags & os.O_RDWR:
            raise OSError("simulated read-only remount")
        return real_open(path, flags, *args)

    with (
        patch("core.helpers.config.CONFIG_FILE", settings_file),
        patch("core.helpers.config.CONFIG_DIR", tmp_path),
        patch("core.helpers.key_manager.os.open", side_effect=read_only_lock_open),
        patch(
            "core.helpers.migration.atomic_write_json",
            side_effect=OSError("schema migration is read-only"),
        ),
    ):
        CoreSettings._instance = None
        with pytest.raises(OSError, match="schema migration is read-only"):
            CoreSettings()

    assert json.loads(settings_file.read_text(encoding="utf-8")) == persisted


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


def test_activity_recorder_writes_database_under_config_path(tmp_path, monkeypatch):
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

    database_file = tmp_path / "channelwatch.db"
    assert database_file.is_file()
    engine = create_db_engine(f"sqlite:///{database_file}")
    try:
        with get_session(engine) as session:
            rows = list(session.exec(select(ActivityEvent)).all())
        assert [row.title for row in rows] == ["Config path test"]
    finally:
        engine.dispose()


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
        patch.object(main_mod, "RBAC_ENABLED", False),
        patch.object(main_mod, "_STORAGE_AVAILABLE", False),
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


def test_backend_recovers_maintenance_transaction_before_any_settings_reader(
    tmp_path,
):
    import ui.backend.main as main_mod

    events = []
    settings = AppSettings()

    def recover(config_dir):
        events.append(("recover", config_dir))
        return 1

    def core_settings():
        assert events and events[0][0] == "recover"
        events.append(("core-settings", None))
        return settings

    def ui_settings():
        assert events and events[0][0] == "recover"
        events.append(("ui-settings", None))
        return settings

    with (
        patch.object(main_mod.backend_config, "CONFIG_DIR", tmp_path),
        patch("core.helpers.maintenance_transaction.recover_maintenance_transactions", recover),
        patch.object(main_mod, "CORE_APP_AVAILABLE", True),
        patch.object(main_mod, "_get_core_settings_sync", side_effect=core_settings),
        patch.object(main_mod, "load_settings", side_effect=ui_settings),
        patch.object(main_mod, "_refresh_runtime_auth_state"),
        patch.object(main_mod, "_bootstrap_admin_from_env"),
        patch.object(main_mod, "ensure_history_file_watcher_started"),
        patch.object(main_mod.threading, "Thread"),
        patch("core.helpers.logging.setup_logging"),
    ):
        main_mod.run_startup_initialization()

    assert events[0] == ("recover", tmp_path)


def test_backend_lifespan_rejects_terminal_update_restart_failure():
    import ui.backend.main as main_mod

    settings = AppSettings()
    record_calls = []

    class FailingManager:
        def record_startup_success(self, **kwargs):
            record_calls.append(kwargs)
            raise UpdateRestartError("restart handoff failed")

    async def attempt_startup():
        yielded = False
        with pytest.raises(UpdateRestartError, match="restart handoff failed"):
            async with main_mod.lifespan(main_mod.app):
                yielded = True
        return yielded

    with (
        patch.object(main_mod, "CORE_APP_AVAILABLE", False),
        patch.object(main_mod, "RBAC_ENABLED", False),
        patch.object(main_mod, "CW_DISABLE_AUTH", False),
        patch.object(main_mod, "_STORAGE_AVAILABLE", False),
        patch.object(main_mod, "_STARTUP_COMPLETE", True),
        patch.object(main_mod, "load_settings", return_value=settings),
        patch.object(main_mod, "_refresh_runtime_auth_state"),
        patch.object(main_mod, "ensure_history_file_watcher_started"),
        patch.object(main_mod.threading, "Thread"),
        patch.object(main_mod, "_build_update_manager", return_value=FailingManager()),
    ):
        yielded = asyncio.run(attempt_startup())
        assert main_mod._STARTUP_COMPLETE is False

    assert yielded is False
    assert record_calls == [
        {
            "component": "ui",
            "running_version": main_mod.__version__,
            "activation_id": os.environ.get("CHANNELWATCH_ACTIVATION_ID", ""),
            "healthy": False,
        }
    ]


def test_backend_lifespan_pauses_update_scheduler_for_read_only_config():
    import ui.backend.main as main_mod

    scheduler_factory = MagicMock()

    async def run_lifespan():
        async with main_mod.lifespan(main_mod.app):
            return True

    with (
        patch.dict(os.environ, {"CHANNELWATCH_CONFIG_READ_ONLY": "1"}),
        patch.object(main_mod, "run_startup_initialization"),
        patch.object(
            main_mod,
            "_get_update_automation_service",
            scheduler_factory,
        ),
    ):
        assert asyncio.run(run_lifespan()) is True

    scheduler_factory.assert_not_called()


def test_settings_write_reports_read_only_storage_as_service_unavailable():
    import ui.backend.main as main_mod

    with (
        patch.dict(os.environ, {"CHANNELWATCH_CONFIG_READ_ONLY": "1"}),
        pytest.raises(HTTPException) as raised,
    ):
        asyncio.run(main_mod.update_settings_endpoint(AppSettings()))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "ERR_RUNTIME_SETUP_REQUIRED"
    assert "/config is read-only" in raised.value.detail["message"]


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


def test_read_only_sqlite_uses_immutable_auth_and_activity_engines_without_writes(
    tmp_path,
):
    import ui.backend.main as main_mod

    database = tmp_path / "channelwatch.db"
    writer = create_db_engine(f"sqlite:///{database}")
    create_all_tables(writer)
    with get_session(writer) as session:
        session.add(
            ActivityEvent(
                id="immutable-event",
                dvr_id="dvr-test",
                event_type="watching_channel",
                title="Read-only activity",
                message="preserved",
            )
        )
        session.commit()
    writer.dispose()
    before = (database.read_bytes(), database.stat().st_mtime_ns)

    with (
        patch.dict(os.environ, {"CHANNELWATCH_CONFIG_READ_ONLY": "1"}),
        patch.object(main_mod, "_ACTIVITY_DB_FILE", database),
        patch.object(main_mod, "_ACTIVITY_DB_URL", f"sqlite:///{database}"),
        patch.object(main_mod, "_activity_db_engine", None),
        patch.object(main_mod, "_auth_db_engine", None),
    ):
        assert "mode=ro&immutable=1&uri=true" in main_mod._read_only_sqlite_url(
            database
        )
        activity_engine = main_mod._get_activity_db_engine()
        auth_engine = main_mod._ensure_auth_tables()
        assert activity_engine is not None
        assert auth_engine is not None
        with get_session(activity_engine) as session:
            rows = list(
                session.exec(
                    select(ActivityEvent).where(ActivityEvent.id == "immutable-event")
                ).all()
            )
        assert [row.id for row in rows] == ["immutable-event"]
        activity_engine.dispose()
        auth_engine.dispose()

    assert (database.read_bytes(), database.stat().st_mtime_ns) == before
    assert not (tmp_path / "channelwatch.db-wal").exists()
    assert not (tmp_path / "channelwatch.db-shm").exists()
    assert not (tmp_path / "channelwatch.db-journal").exists()
