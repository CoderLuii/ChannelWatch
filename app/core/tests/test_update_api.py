import json
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient


class FakeUpdateManager:
    def __init__(self):
        self.job = {
            "job_id": "job-1",
            "operation": "check",
            "status": "current",
            "message": "Update check completed.",
        }

    def status(self):
        return {
            "current_version": "0.9.9",
            "runtime_abi": "channelwatch-runtime-v1",
            "settings_schema_version": 7,
            "active_bundle": None,
            "latest": None,
            "update_available": False,
            "image_required": False,
            "last_job": self.job,
            "rollback_available": False,
            "auth_disabled_warning": True,
        }

    def check(self):
        return self.status()

    def apply(self, version=None):
        return {**self.job, "operation": "apply", "status": "current"}

    def rollback(self):
        return {**self.job, "operation": "rollback", "status": "restarting"}


def _settings(tmp_path: Path, api_key: str = "test-key") -> Path:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"dvr_servers": [], "api_key": api_key, "tz": "UTC"}))
    return settings_file


def test_update_status_requires_auth_when_api_key_configured(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", False),
        patch.object(ui_main, "API_KEY_CACHE", "test-key"),
        patch.object(ui_main, "_build_update_manager", return_value=FakeUpdateManager()),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        resp = client.get("/api/v1/update/status")

    assert resp.status_code == 401


def test_update_status_returns_no_auth_warning_when_auth_disabled(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_build_update_manager", return_value=FakeUpdateManager()),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        resp = client.get("/api/v1/update/status")

    assert resp.status_code == 200
    assert resp.json()["auth_disabled_warning"] is True


def test_update_apply_image_required_maps_to_structured_error(tmp_path: Path):
    import ui.backend.main as ui_main

    class ImageRequiredManager(FakeUpdateManager):
        def apply(self, version=None):
            return {
                "job_id": "job-2",
                "operation": "apply",
                "status": "image_required",
                "message": "This release requires a new container image.",
            }

    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_build_update_manager", return_value=ImageRequiredManager()),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        resp = client.post("/api/v1/update/apply", json={})

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ERR_UPDATE_IMAGE_REQUIRED"
