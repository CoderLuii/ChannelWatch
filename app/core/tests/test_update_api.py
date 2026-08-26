import json
import os
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
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


class FakeUpdateAutomation:
    def __init__(self):
        self.policy = {
            "schema": 1,
            "mode": "automatic",
            "channel": "stable",
            "maintenance_window_start": "03:00",
            "maintenance_window_minutes": 120,
            "timezone_source": "channelwatch",
            "scheduled_restart_at": None,
            "postpone_available": False,
        }
        self.postponements = []
        self.rollback_holds = []

    def get_policy_view(self):
        return dict(self.policy)

    def put_policy(self, changes):
        self.policy.update(changes)
        return dict(self.policy)

    def postpone(self, **kwargs):
        self.postponements.append(kwargs)
        return {"postponed_until": "2026-08-25T00:00:00Z"}

    def retry_release(self, *, version, bundle_sha256):
        return {
            "job_id": "retry-1",
            "operation": "apply",
            "status": "restarting",
            "version": version,
            "bundle_sha256": bundle_sha256,
        }

    def record_rollback_hold(self, **kwargs):
        self.rollback_holds.append(kwargs)
        return kwargs

    def rollback_release(self):
        self.rollback_holds.append(
            {"version": "0.9.18", "bundle_sha256": "a" * 64}
        )
        return {"status": "restarting", "operation": "rollback"}


class FakeRecoveryService:
    def status(self):
        return {
            "current_version": "0.9.18",
            "latest": None,
            "update_available": False,
            "mode": "official-signed-recovery",
        }

    def check(self):
        return {
            **self.status(),
            "latest": {"version": "0.9.19", "delivery_mode": "app_update"},
            "update_available": True,
        }

    def apply(self, version=None):
        return {
            "job_id": "recovery-1",
            "operation": "apply",
            "status": "restarting",
            "version": version or "0.9.19",
        }


def _settings(tmp_path: Path, api_key: str = "test-key") -> Path:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"dvr_servers": [], "api_key": api_key, "tz": "UTC"})
    )
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
        patch.object(
            ui_main, "_build_update_manager", return_value=FakeUpdateManager()
        ),
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
        patch.object(
            ui_main, "_build_update_manager", return_value=FakeUpdateManager()
        ),
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
        patch.object(
            ui_main,
            "_get_update_automation_service",
            return_value=SimpleNamespace(
                apply_release=ImageRequiredManager().apply,
            ),
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        resp = client.post("/api/v1/update/apply", json={})

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ERR_UPDATE_IMAGE_REQUIRED"


def test_update_check_network_error_maps_to_check_failure(tmp_path: Path):
    import ui.backend.main as ui_main

    class OfflineManager(FakeUpdateManager):
        def check(self):
            raise OSError("network unreachable")

    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_build_update_manager", return_value=OfflineManager()),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        resp = client.post("/api/v1/update/check")

    assert resp.status_code >= 400
    assert resp.json()["detail"]["code"] == "ERR_UPDATE_CHECK_FAILED"


def test_update_check_returns_last_status_when_another_check_owns_lock(tmp_path: Path):
    import ui.backend.main as ui_main
    from core.update_center import UpdateLockedError

    class BusyManager(FakeUpdateManager):
        def check(self):
            raise UpdateLockedError("Another update operation is already running.")

    manager = BusyManager()
    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_build_update_manager", return_value=manager),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        resp = client.post("/api/v1/update/check")

    assert resp.status_code == 200
    assert resp.json()["current_version"] == "0.9.9"
    assert resp.json()["operation_busy"] is True


def test_missing_update_job_uses_structured_error(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(
            ui_main, "_build_update_manager", return_value=FakeUpdateManager()
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        resp = client.get("/api/v1/update/jobs/missing-job")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "ERR_UPDATE_JOB_NOT_FOUND"


def test_update_policy_uses_canonical_contract_and_dirty_draft_is_one_day(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    automation = FakeUpdateAutomation()
    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(
            ui_main,
            "_get_update_automation_service",
            return_value=automation,
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        saved = client.put(
            "/api/v1/update/policy",
            json={
                "mode": "notify_only",
                "maintenance_window_start": "04:30",
                "maintenance_window_minutes": 90,
            },
        )
        postponed = client.post(
            "/api/v1/update/postpone",
            json={"hours": 24, "reason": "dirty_report_draft"},
        )

    assert saved.status_code == 200
    assert saved.json()["mode"] == "notify_only"
    assert saved.json()["maintenance_window_start"] == "04:30"
    assert postponed.status_code == 200
    assert automation.postponements == [{"reason": "dirty_report_draft"}]


def test_update_preflight_reports_unresolved_configuration_transaction(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    transaction = tmp_path / ".channelwatch-transactions" / "unfinished"
    transaction.mkdir(parents=True)
    (transaction / "journal.json").write_text("{}", encoding="utf-8")
    with patch.object(ui_main, "CONFIG_DIR", tmp_path):
        result = ui_main._update_install_preflight({})

    assert result["maintenance_transactions_ok"] is False


def test_update_install_preflight_accepts_private_storage_and_fails_closed(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "settings.json").write_text("{}", encoding="utf-8")
    with (
        patch.object(ui_main, "CONFIG_DIR", safe),
        patch.object(
            ui_main.os,
            "statvfs",
            return_value=SimpleNamespace(f_bavail=10**9, f_frsize=4096),
        ),
    ):
        healthy = ui_main._update_install_preflight({})
    assert healthy == {
        "free_space_ok": True,
        "private_backup_ok": True,
        "maintenance_transactions_ok": True,
    }
    assert (safe / "backups").stat().st_mode & 0o777 == 0o700

    unsafe = tmp_path / "unsafe"
    outside = tmp_path / "outside"
    unsafe.mkdir()
    outside.mkdir()
    (unsafe / "backups").symlink_to(outside, target_is_directory=True)
    with patch.object(ui_main, "CONFIG_DIR", unsafe):
        bad_backup = ui_main._update_install_preflight({})
    assert bad_backup["private_backup_ok"] is False

    config_link = tmp_path / "config-link"
    config_link.symlink_to(safe, target_is_directory=True)
    with patch.object(ui_main, "CONFIG_DIR", config_link):
        bad_root = ui_main._update_install_preflight({})
    assert bad_root == {
        "free_space_ok": False,
        "private_backup_ok": False,
        "maintenance_transactions_ok": False,
    }

    with (
        patch.object(ui_main, "CONFIG_DIR", safe),
        patch.object(ui_main.Path, "lstat", side_effect=OSError("unreadable")),
    ):
        unreadable = ui_main._update_install_preflight({})
    assert unreadable == bad_root


def test_update_install_preflight_counts_nested_config_data(tmp_path: Path):
    import ui.backend.main as ui_main
    from core import update_center

    nested = tmp_path / "data" / "nested"
    nested.mkdir(parents=True)
    (nested / "activity.db").write_bytes(b"x" * 1024)
    fixed_overhead = 32 * 1024 * 1024
    with (
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(update_center, "MAX_BUNDLE_BYTES", 0),
        patch.object(update_center, "MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES", 0),
        patch.object(
            ui_main.os,
            "statvfs",
            return_value=SimpleNamespace(
                f_bavail=fixed_overhead + 1500,
                f_frsize=1,
            ),
        ),
    ):
        result = ui_main._update_install_preflight({})

    assert result["free_space_ok"] is False


def test_update_factories_and_notification_drain_wire_exact_callbacks(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    update_manager = SimpleNamespace(name="manager")
    automation = SimpleNamespace(name="automation")
    recovery = SimpleNamespace(name="recovery")
    with (
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch("core.update_center.UpdateManager", return_value=update_manager) as manager,
        patch(
            "core.update_policy.UpdateAutomationService", return_value=automation
        ) as automation_factory,
        patch(
            "core.update_policy.OfficialRecoveryUpdateService",
            return_value=recovery,
        ) as recovery_factory,
        patch(
            "core.notification_drain.request_core_notification_drain",
            return_value=True,
        ) as drain,
        patch(
            "core.notification_drain.release_core_notification_drain",
            return_value=True,
        ) as release,
        patch.object(ui_main, "_UPDATE_AUTOMATION_SERVICE", None),
    ):
        assert ui_main._request_update_notification_drain(12.5) is True
        assert ui_main._release_update_notification_drain() is True
        assert ui_main._build_update_manager() is update_manager
        assert ui_main._get_update_automation_service() is automation
        assert ui_main._get_update_automation_service() is automation
        assert ui_main._build_official_recovery_service() is recovery

    drain.assert_called_once_with(tmp_path, 12.5)
    release.assert_called_once_with(tmp_path)
    manager.assert_called_once()
    assert manager.call_args.kwargs["config_dir"] == tmp_path
    automation_factory.assert_called_once()
    recovery_factory.assert_called_once()


def test_update_error_mapping_covers_locked_manifest_bundle_and_rollback():
    import ui.backend.main as ui_main

    from core.update_center import (
        UpdateBundleError,
        UpdateCenterError,
        UpdateLockedError,
        UpdateManifestError,
    )

    cases = (
        (UpdateLockedError("locked"), {}, "ERR_UPDATE_LOCKED"),
        (UpdateManifestError("manifest"), {}, "ERR_UPDATE_CHECK_FAILED"),
        (UpdateBundleError("bundle"), {"apply": True}, "ERR_UPDATE_APPLY_FAILED"),
        (
            UpdateCenterError("rollback"),
            {"rollback": True},
            "ERR_UPDATE_ROLLBACK_FAILED",
        ),
        (RuntimeError("unexpected"), {"apply": True}, "ERR_UPDATE_APPLY_FAILED"),
    )
    for error, kwargs, code in cases:
        with pytest.raises(HTTPException) as raised:
            ui_main._raise_update_error(error, **kwargs)
        assert raised.value.detail["code"] == code


def test_update_error_mapping_reports_read_only_storage_as_service_unavailable():
    import ui.backend.main as ui_main

    with (
        patch.dict(os.environ, {"CHANNELWATCH_CONFIG_READ_ONLY": "1"}),
        pytest.raises(HTTPException) as raised,
    ):
        ui_main._raise_update_error(OSError("read-only filesystem"), apply=True)

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "ERR_RUNTIME_SETUP_REQUIRED"
    assert "/config is read-only" in raised.value.detail["message"]


def test_update_retry_admin_postpone_and_rollback_hold(tmp_path: Path):
    import ui.backend.main as ui_main

    digest = "d" * 64

    class RetryRollbackManager(FakeUpdateManager):
        def status(self):
            return {
                **super().status(),
                "latest": {"version": "0.9.19", "bundle_sha256": digest},
                "active_bundle": {
                    "version": "0.9.18",
                    "manifest": {"bundle_sha256": "a" * 64},
                },
            }

        def rollback(self):
            return {"status": "restarting", "operation": "rollback"}

    manager = RetryRollbackManager()
    automation = FakeUpdateAutomation()
    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_build_update_manager", return_value=manager),
        patch.object(
            ui_main, "_get_update_automation_service", return_value=automation
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        policy = client.get("/api/v1/update/policy")
        postponed = client.post(
            "/api/v1/update/postpone",
            json={"hours": 168, "reason": "administrator"},
        )
        retried = client.post("/api/v1/update/retry")
        rolled_back = client.post("/api/v1/update/rollback")

    assert policy.status_code == 200
    assert postponed.status_code == 200
    assert automation.postponements == [
        {"minutes": 168 * 60, "reason": "administrator"}
    ]
    assert retried.status_code == 202
    assert retried.json()["version"] == "0.9.19"
    assert rolled_back.status_code == 202
    assert automation.rollback_holds == [
        {"version": "0.9.18", "bundle_sha256": "a" * 64}
    ]


def test_recovery_update_accepts_authenticated_api_key(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = _settings(tmp_path)
    recovery = FakeRecoveryService()
    settings = SimpleNamespace(auth_mode="api_key", api_key="test-key")
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", False),
        patch.object(ui_main, "_active_administrator_auth_required", return_value=True),
        patch.object(ui_main, "_official_recovery_active", return_value=True),
        patch.object(ui_main, "_load_settings_async", AsyncMock(return_value=settings)),
        patch.object(
            ui_main, "_build_official_recovery_service", return_value=recovery
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/update/recovery/check",
            headers={"X-API-Key": "test-key"},
            json={},
        )

    assert response.status_code == 200
    assert response.json()["update_available"] is True


def test_update_automation_recovery_state_is_not_enabled_by_no_auth_alone(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    with (
        patch.dict(
            "os.environ",
            {
                "CW_DISABLE_AUTH": "true",
                "CHANNELWATCH_OFFICIAL_RECOVERY_MODE": "",
            },
            clear=False,
        ),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(
            ui_main,
            "inspect_runtime_preflight",
            return_value=SimpleNamespace(setup_required=False),
        ),
    ):
        assert ui_main._update_automation_recovery_state() is False


def test_update_automation_recovery_state_follows_blocked_runtime(tmp_path: Path):
    import ui.backend.main as ui_main

    with (
        patch.dict(
            "os.environ", {"CHANNELWATCH_OFFICIAL_RECOVERY_MODE": ""}, clear=False
        ),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(
            ui_main,
            "inspect_runtime_preflight",
            return_value=SimpleNamespace(setup_required=True),
        ),
    ):
        assert ui_main._update_automation_recovery_state() is True


def test_public_recovery_requires_same_origin_cookie_csrf_and_confirmation(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    settings_file = _settings(tmp_path, api_key="")
    recovery = FakeRecoveryService()
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(
            ui_main,
            "_active_administrator_auth_required",
            return_value=False,
        ),
        patch.object(ui_main, "_official_recovery_active", return_value=True),
        patch.object(
            ui_main,
            "_build_official_recovery_service",
            return_value=recovery,
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        status = client.get("/api/v1/update/recovery/status")
        token = status.json()["bootstrap_csrf"]

        missing_csrf = client.post("/api/v1/update/recovery/check", json={})
        checked = client.post(
            "/api/v1/update/recovery/check",
            headers={"X-CSRF-Token": token},
            json={},
        )
        missing_confirmation = client.post(
            "/api/v1/update/recovery/apply",
            headers={"X-CSRF-Token": token},
            json={"version": "0.9.19"},
        )
        applied = client.post(
            "/api/v1/update/recovery/apply",
            headers={"X-CSRF-Token": token},
            json={
                "version": "0.9.19",
                "confirmation": "INSTALL OFFICIAL UPDATE",
            },
        )
        replay = client.post(
            "/api/v1/update/recovery/apply",
            headers={"X-CSRF-Token": token},
            json={
                "version": "0.9.19",
                "confirmation": "INSTALL OFFICIAL UPDATE",
            },
        )

    assert status.status_code == 200
    assert status.json()["recovery_active"] is True
    assert status.json()["confirmation_required"] is True
    assert token
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["detail"]["code"] == "ERR_AUTH_CSRF_INVALID"
    assert checked.status_code == 200
    assert missing_confirmation.status_code == 422
    assert missing_confirmation.json()["detail"]["code"] == (
        "ERR_UPDATE_RECOVERY_CONFIRMATION"
    )
    assert applied.status_code == 202
    assert applied.json()["status"] == "restarting"
    assert replay.status_code == 403


def test_public_recovery_csrf_tokens_do_not_invalidate_other_tabs(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = _settings(tmp_path, api_key="")
    recovery = FakeRecoveryService()
    ui_main._RECOVERY_CSRF_TOKENS.clear()
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(
            ui_main,
            "_active_administrator_auth_required",
            return_value=False,
        ),
        patch.object(ui_main, "_official_recovery_active", return_value=True),
        patch.object(
            ui_main,
            "_build_official_recovery_service",
            return_value=recovery,
        ),
    ):
        first_client = TestClient(ui_main.app, raise_server_exceptions=False)
        second_client = TestClient(ui_main.app, raise_server_exceptions=False)
        first_token = first_client.get("/api/v1/update/recovery/status").json()[
            "bootstrap_csrf"
        ]
        second_token = second_client.get("/api/v1/update/recovery/status").json()[
            "bootstrap_csrf"
        ]

        first_check = first_client.post(
            "/api/v1/update/recovery/check",
            headers={"X-CSRF-Token": first_token},
            json={},
        )
        second_check = second_client.post(
            "/api/v1/update/recovery/check",
            headers={"X-CSRF-Token": second_token},
            json={},
        )

    assert first_token != second_token
    assert first_check.status_code == 200
    assert second_check.status_code == 200


def test_healthy_authenticated_install_rejects_recovery_surface(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = _settings(tmp_path)
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", False),
        patch.object(ui_main, "_active_administrator_auth_required", return_value=True),
        patch.object(ui_main, "_official_recovery_active", return_value=False),
        patch.object(
            ui_main,
            "_build_official_recovery_service",
            return_value=FakeRecoveryService(),
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        status = client.get("/api/v1/update/recovery/status")
        apply = client.post(
            "/api/v1/update/recovery/apply",
            json={
                "version": "0.9.19",
                "confirmation": "INSTALL OFFICIAL UPDATE",
            },
        )

    assert status.status_code == 200
    assert status.json()["recovery_active"] is False
    assert status.json()["bootstrap_csrf"] is None
    assert status.json() == {
        "current_version": "",
        "latest": None,
        "update_available": False,
        "image_required": False,
        "recovery_waiting_for_newer_release": False,
        "mode": "official-signed-recovery",
        "status": "inactive",
        "reason_code": "official_recovery_inactive",
        "recovery_active": False,
        "bootstrap_csrf": None,
        "confirmation_required": False,
    }
    assert apply.status_code == 409
    assert apply.json()["detail"]["code"] == "ERR_UPDATE_RECOVERY_INACTIVE"


def test_protocol_three_image_recovery_mode_activates_official_recovery(
    monkeypatch,
):
    import ui.backend.main as ui_main

    monkeypatch.setenv("CHANNELWATCH_OFFICIAL_RECOVERY_MODE", "1")
    assert ui_main._official_recovery_active(administrator_required=True) is True


def test_recovery_csrf_and_rate_limit_prune_expired_clients(monkeypatch):
    import ui.backend.main as ui_main

    ui_main._RECOVERY_CSRF_TOKENS.clear()
    ui_main._RECOVERY_WRITE_ATTEMPTS.clear()
    ui_main._RECOVERY_CSRF_TOKENS["old"] = ("expired", 99.0)
    ui_main._RECOVERY_CSRF_TOKENS["current"] = ("live", 2000.0)
    ui_main._RECOVERY_WRITE_ATTEMPTS["expired"] = deque([1.0])
    ui_main._RECOVERY_WRITE_ATTEMPTS["live"] = deque([950.0])
    monkeypatch.setattr(ui_main.time, "monotonic", lambda: 1000.0)

    ui_main._issue_recovery_csrf("new-client")
    assert "old" not in ui_main._RECOVERY_CSRF_TOKENS
    assert "current" in ui_main._RECOVERY_CSRF_TOKENS
    assert ui_main._consume_recovery_rate_limit("new-client") is True
    assert "expired" not in ui_main._RECOVERY_WRITE_ATTEMPTS
    assert "live" in ui_main._RECOVERY_WRITE_ATTEMPTS

    ui_main._RECOVERY_CSRF_TOKENS.clear()
    for index in range(ui_main._RECOVERY_CSRF_MAX_TOKENS):
        ui_main._RECOVERY_CSRF_TOKENS[f"token-{index}"] = (
            f"client-{index}",
            2000.0 + index,
        )
    newest = ui_main._issue_recovery_csrf("bounded-client")
    assert len(ui_main._RECOVERY_CSRF_TOKENS) == ui_main._RECOVERY_CSRF_MAX_TOKENS
    assert "token-0" not in ui_main._RECOVERY_CSRF_TOKENS
    assert ui_main._RECOVERY_CSRF_TOKENS[newest][0] == "bounded-client"

    ui_main._RECOVERY_CSRF_TOKENS.clear()
    ui_main._RECOVERY_WRITE_ATTEMPTS.clear()
