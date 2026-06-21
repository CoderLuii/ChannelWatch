import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from core.helpers.soft_delete_manager import (
    soft_delete_dvr,
    restore_dvr,
    hard_delete_dvr,
    purge_expired_dvrs,
)


def _dvr(dvr_id="dvr_aaa11111", host="192.168.1.1", port=8089, deleted_at=None):
    entry = {
        "id": dvr_id,
        "name": "Test DVR",
        "host": host,
        "port": port,
        "enabled": True,
    }
    if deleted_at is not None:
        entry["deleted_at"] = deleted_at
    return entry


def _history_file(tmp_path: Path, rows):
    f = tmp_path / "activity_history.json"
    f.write_text(json.dumps(rows))
    return f


def _api_settings_file(config_dir: Path, servers):
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_file = config_dir / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "_version": 7,
                "auth_mode": "none",
                "security_setup_completed": True,
                "api_key": "test-key",
                "dvr_servers": servers,
            }
        )
    )
    return settings_file


class TestSoftDelete:
    def test_sets_deleted_at(self):
        servers = [_dvr()]
        assert soft_delete_dvr(servers, "dvr_aaa11111") is True
        assert servers[0].get("deleted_at") is not None

    def test_deleted_at_is_iso_utc(self):
        servers = [_dvr()]
        soft_delete_dvr(servers, "dvr_aaa11111")
        assert isinstance(servers[0]["deleted_at"], str)
        ts = datetime.fromisoformat(servers[0]["deleted_at"])
        assert ts.tzinfo is not None

    def test_preserves_all_other_fields(self):
        servers = [_dvr()]
        soft_delete_dvr(servers, "dvr_aaa11111")
        s = servers[0]
        assert s["host"] == "192.168.1.1"
        assert s["name"] == "Test DVR"
        assert s["enabled"] is True
        assert s["port"] == 8089

    def test_preserves_history_file(self, tmp_path):
        history = [{"id": "e1", "dvr_id": "dvr_aaa11111", "type": "watching_channel"}]
        hf = _history_file(tmp_path, history)
        servers = [_dvr()]
        soft_delete_dvr(servers, "dvr_aaa11111")
        remaining = json.loads(hf.read_text())
        assert len(remaining) == 1, "Soft-delete must NOT purge history"

    def test_missing_dvr_returns_false(self):
        servers = [_dvr()]
        assert soft_delete_dvr(servers, "dvr_zzz99999") is False

    def test_already_deleted_raises(self):
        servers = [_dvr(deleted_at="2026-01-01T00:00:00+00:00")]
        with pytest.raises(ValueError, match="already soft-deleted"):
            soft_delete_dvr(servers, "dvr_aaa11111")


class TestRestore:
    def test_clears_deleted_at(self):
        servers = [_dvr(deleted_at="2026-04-01T12:00:00+00:00")]
        assert restore_dvr(servers, "dvr_aaa11111") is True
        assert not servers[0].get("deleted_at")

    def test_preserves_all_other_fields(self):
        servers = [_dvr(deleted_at="2026-04-01T12:00:00+00:00")]
        restore_dvr(servers, "dvr_aaa11111")
        assert servers[0]["host"] == "192.168.1.1"
        assert servers[0]["enabled"] is True

    def test_missing_dvr_returns_false(self):
        servers = [_dvr(deleted_at="2026-04-01T12:00:00+00:00")]
        assert restore_dvr(servers, "dvr_zzz99999") is False

    def test_not_soft_deleted_raises(self):
        servers = [_dvr()]
        with pytest.raises(ValueError, match="not soft-deleted"):
            restore_dvr(servers, "dvr_aaa11111")


class TestHardDelete:
    def test_removes_from_list(self, tmp_path):
        servers = [_dvr("dvr_aaa11111"), _dvr("dvr_bbb22222")]
        assert hard_delete_dvr(tmp_path, servers, "dvr_aaa11111") is True
        assert len(servers) == 1
        assert servers[0]["id"] == "dvr_bbb22222"

    def test_removes_state_file(self, tmp_path):
        state = tmp_path / "session_state_dvr_aaa11111.json"
        state.write_text("{}")
        servers = [_dvr()]
        hard_delete_dvr(tmp_path, servers, "dvr_aaa11111")
        assert not state.exists()

    def test_removes_history_rows(self, tmp_path):
        history = [
            {"id": "e1", "dvr_id": "dvr_aaa11111", "type": "watching_channel"},
            {"id": "e2", "dvr_id": "dvr_bbb22222", "type": "recording_event"},
        ]
        hf = _history_file(tmp_path, history)
        servers = [_dvr("dvr_aaa11111")]
        hard_delete_dvr(tmp_path, servers, "dvr_aaa11111")
        remaining = json.loads(hf.read_text())
        assert len(remaining) == 1
        assert remaining[0]["dvr_id"] == "dvr_bbb22222"

    def test_preserves_other_dvr_history(self, tmp_path):
        history = [
            {"id": "e1", "dvr_id": "dvr_aaa11111"},
            {"id": "e2", "dvr_id": "dvr_ccc33333"},
            {"id": "e3", "dvr_id": "dvr_ddd44444"},
        ]
        hf = _history_file(tmp_path, history)
        servers = [_dvr("dvr_aaa11111")]
        hard_delete_dvr(tmp_path, servers, "dvr_aaa11111")
        remaining = json.loads(hf.read_text())
        assert len(remaining) == 2

    def test_missing_dvr_returns_false(self, tmp_path):
        servers = [_dvr()]
        assert hard_delete_dvr(tmp_path, servers, "dvr_zzz99999") is False
        assert len(servers) == 1

    def test_soft_delete_then_hard_delete_removes_history(self, tmp_path):
        history = [{"id": "e1", "dvr_id": "dvr_aaa11111", "type": "disk_alert"}]
        hf = _history_file(tmp_path, history)
        servers = [_dvr()]

        soft_delete_dvr(servers, "dvr_aaa11111")
        assert len(json.loads(hf.read_text())) == 1, (
            "soft-delete must not purge history"
        )

        hard_delete_dvr(tmp_path, servers, "dvr_aaa11111")
        assert len(json.loads(hf.read_text())) == 0, "hard-delete must purge history"


class TestAutoPurge:
    def test_removes_expired_archived_dvr(self, tmp_path):
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        servers = [_dvr("dvr_old111111", deleted_at=old), _dvr("dvr_active22")]
        purged = purge_expired_dvrs(tmp_path, servers, retention_days=30)
        assert "dvr_old111111" in purged
        assert len(servers) == 1
        assert servers[0]["id"] == "dvr_active22"

    def test_keeps_recently_deleted(self, tmp_path):
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        servers = [_dvr(deleted_at=recent)]
        purged = purge_expired_dvrs(tmp_path, servers, retention_days=30)
        assert purged == []
        assert len(servers) == 1

    def test_purges_at_exact_boundary(self, tmp_path):
        boundary = (
            datetime.now(timezone.utc) - timedelta(days=30, seconds=1)
        ).isoformat()
        servers = [_dvr("dvr_boundary", deleted_at=boundary)]
        purged = purge_expired_dvrs(tmp_path, servers, retention_days=30)
        assert "dvr_boundary" in purged

    def test_nothing_to_purge(self, tmp_path):
        servers = [_dvr("dvr_active1"), _dvr("dvr_active2")]
        assert purge_expired_dvrs(tmp_path, servers) == []
        assert len(servers) == 2

    def test_removes_state_file_on_purge(self, tmp_path):
        old = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        state = tmp_path / "session_state_dvr_gone1111.json"
        state.write_text("{}")
        servers = [_dvr("dvr_gone1111", deleted_at=old)]
        purge_expired_dvrs(tmp_path, servers, retention_days=30)
        assert not state.exists()

    def test_removes_history_rows_on_purge(self, tmp_path):
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        history = [
            {"id": "e1", "dvr_id": "dvr_gone1111", "type": "watching_channel"},
            {"id": "e2", "dvr_id": "dvr_keep1111", "type": "recording_event"},
        ]
        hf = _history_file(tmp_path, history)
        servers = [_dvr("dvr_gone1111", deleted_at=old), _dvr("dvr_keep1111")]
        purge_expired_dvrs(tmp_path, servers, retention_days=30)
        remaining = json.loads(hf.read_text())
        assert len(remaining) == 1
        assert remaining[0]["dvr_id"] == "dvr_keep1111"


class TestConfigFiltersSoftDeleted:
    def test_get_dvr_connections_excludes_deleted(self, tmp_path, monkeypatch):
        from core.helpers import config as cfg_module
        import json as _json

        settings_data = {
            "_version": 7,
            "dvr_servers": [
                {
                    "id": "dvr_active11",
                    "name": "Active",
                    "host": "10.0.0.1",
                    "port": 8089,
                    "enabled": True,
                },
                {
                    "id": "dvr_deleted1",
                    "name": "Deleted",
                    "host": "10.0.0.2",
                    "port": 8089,
                    "enabled": True,
                    "deleted_at": "2026-01-01T00:00:00+00:00",
                },
            ],
        }

        config_file = tmp_path / "settings.json"
        config_file.write_text(_json.dumps(settings_data))

        monkeypatch.setenv("CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr(cfg_module, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_module, "CONFIG_FILE", config_file)
        monkeypatch.setattr(cfg_module.CoreSettings, "_instance", None)

        settings = cfg_module.CoreSettings()
        connections = settings.get_dvr_connections()
        ids = [c.id for c in connections]
        assert "dvr_active11" in ids
        assert "dvr_deleted1" not in ids

    def test_multi_dvr_v2_disabled_uses_first_non_deleted_dvr_only(
        self, tmp_path, monkeypatch
    ):
        from core.helpers import config as cfg_module
        import json as _json

        settings_data = {
            "_version": 7,
            "multi_dvr_v2_enabled": False,
            "dvr_servers": [
                _dvr("dvr_primary", host="10.0.0.1"),
                _dvr("dvr_secondary", host="10.0.0.2"),
            ],
        }
        config_file = tmp_path / "settings.json"
        config_file.write_text(_json.dumps(settings_data))

        monkeypatch.setenv("CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr(cfg_module, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_module, "CONFIG_FILE", config_file)
        monkeypatch.setattr(cfg_module.CoreSettings, "_instance", None)

        settings = cfg_module.CoreSettings()
        connections = settings.get_dvr_connections()

        assert [connection.id for connection in connections] == ["dvr_primary"]


class TestDvrManagementEndpoints:
    def test_archived_endpoint_lists_only_deleted_dvrs(self, tmp_path):
        config_dir = tmp_path / "config"
        settings_file = _api_settings_file(
            config_dir,
            [
                _dvr("dvr_active11", host="192.168.1.10"),
                {
                    **_dvr(
                        "dvr_archived",
                        host="192.168.1.20",
                        deleted_at="2026-04-01T00:00:00+00:00",
                    ),
                    "api_key": "archived-secret-key",
                },
            ],
        )

        import ui.backend.main as ui_main

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "RBAC_ENABLED", False),
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            response = client.get("/api/dvrs/archived")

        assert response.status_code == 200
        archived = response.json()["archived"]
        assert [entry["id"] for entry in archived] == ["dvr_archived"]
        assert archived[0]["api_key"] == "****"

    def test_soft_delete_endpoint_persists_deleted_at_and_signals_reload(
        self, tmp_path
    ):
        config_dir = tmp_path / "config"
        settings_file = _api_settings_file(config_dir, [_dvr("dvr_aaa11111")])

        import ui.backend.main as ui_main

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(
                ui_main, "_signal_core_hot_reload", return_value=True
            ) as reload_mock,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            response = client.post("/api/dvrs/dvr_aaa11111/soft-delete")

        assert response.status_code == 200
        saved = json.loads(settings_file.read_text())
        saved_dvr = saved["dvr_servers"][0]
        assert saved_dvr["id"] == "dvr_aaa11111"
        assert saved_dvr.get("deleted_at")
        datetime.fromisoformat(saved_dvr["deleted_at"])
        reload_mock.assert_called_once()

    def test_restore_endpoint_clears_deleted_at_and_signals_reload(self, tmp_path):
        config_dir = tmp_path / "config"
        settings_file = _api_settings_file(
            config_dir,
            [_dvr("dvr_aaa11111", deleted_at="2026-04-01T00:00:00+00:00")],
        )

        import ui.backend.main as ui_main

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(
                ui_main, "_signal_core_hot_reload", return_value=True
            ) as reload_mock,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            response = client.post("/api/dvrs/dvr_aaa11111/restore")

        assert response.status_code == 200
        saved = json.loads(settings_file.read_text())
        assert "deleted_at" not in saved["dvr_servers"][0]
        reload_mock.assert_called_once()

    def test_hard_delete_endpoint_removes_dvr_state_and_history(self, tmp_path):
        config_dir = tmp_path / "config"
        settings_file = _api_settings_file(
            config_dir,
            [_dvr("dvr_aaa11111"), _dvr("dvr_keep2222", host="192.168.1.20")],
        )
        state_file = config_dir / "session_state_dvr_aaa11111.json"
        state_file.write_text("{}")
        _history_file(
            config_dir,
            [
                {"id": "e1", "dvr_id": "dvr_aaa11111", "type": "watching_channel"},
                {"id": "e2", "dvr_id": "dvr_keep2222", "type": "recording_event"},
            ],
        )

        import ui.backend.main as ui_main

        with (
            patch("ui.backend.config.CONFIG_FILE", settings_file),
            patch("ui.backend.config.CONFIG_DIR", config_dir),
            patch.object(ui_main, "_CORE_CONFIG_DIR", config_dir),
            patch.object(ui_main, "CW_DISABLE_AUTH", True),
            patch.object(ui_main, "RBAC_ENABLED", False),
            patch.object(
                ui_main, "_signal_core_hot_reload", return_value=True
            ) as reload_mock,
        ):
            client = TestClient(ui_main.app, raise_server_exceptions=False)
            response = client.delete("/api/dvrs/dvr_aaa11111")

        assert response.status_code == 200
        saved = json.loads(settings_file.read_text())
        assert [entry["id"] for entry in saved["dvr_servers"]] == ["dvr_keep2222"]
        assert not state_file.exists()
        history_rows = json.loads((config_dir / "activity_history.json").read_text())
        assert [row["dvr_id"] for row in history_rows] == ["dvr_keep2222"]
        reload_mock.assert_called_once()
