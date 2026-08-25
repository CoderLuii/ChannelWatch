import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from core.helpers.runtime_preflight import RuntimePreflight


def _lifespan_patches():
    automation = MagicMock()
    automation.start.return_value = True
    automation.stop.return_value = True
    return (
        patch("ui.backend.main.run_startup_initialization"),
        patch(
            "ui.backend.main._get_update_automation_service",
            return_value=automation,
        ),
    )


def test_runtime_preflight_is_public_and_minimal():
    from ui.backend.main import app

    result = RuntimePreflight(
        status="setup_required",
        setup_required=True,
        blockers=("secret_storage_key_missing",),
    )
    startup_patch, scheduler_patch = _lifespan_patches()
    with (
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.inspect_runtime_preflight", return_value=result),
        startup_patch,
        scheduler_patch,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/api/v1/runtime/preflight")

    assert response.status_code == 200
    assert response.json() == {
        "status": "setup_required",
        "setup_required": True,
        "blockers": ["secret_storage_key_missing"],
        "warnings": [],
    }


def test_runtime_preflight_openapi_publishes_the_exact_response_contract():
    from ui.backend.main import app

    schema = app.openapi()
    response_schema = schema["paths"]["/api/v1/runtime/preflight"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert response_schema == {
        "$ref": "#/components/schemas/RuntimePreflightResponse"
    }

    contract = schema["components"]["schemas"]["RuntimePreflightResponse"]
    assert contract["required"] == [
        "status",
        "setup_required",
        "blockers",
        "warnings",
    ]
    assert set(contract["properties"]["status"]["enum"]) == {
        "ready",
        "setup_required",
        "migration_recommended",
    }
    assert set(contract["properties"]["blockers"]["items"]["enum"]) == {
        "secret_storage_key_missing",
        "secret_storage_key_too_short",
        "secret_storage_key_mismatch",
        "secret_storage_key_file_unreadable",
    }
    assert contract["properties"]["warnings"]["items"]["const"] == (
        "legacy_plaintext_key_migration_recommended"
    )


def test_setup_required_keeps_unauthenticated_readiness_minimal():
    from ui.backend.main import app

    result = RuntimePreflight(
        status="setup_required",
        setup_required=True,
        blockers=("secret_storage_key_mismatch",),
    )
    startup_patch, scheduler_patch = _lifespan_patches()
    with (
        patch("ui.backend.main.inspect_runtime_preflight", return_value=result),
        patch(
            "ui.backend.main._get_monitoring_health_summary",
            return_value={"ready": True, "dvrs": []},
        ),
        startup_patch,
        scheduler_patch,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/healthz/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "ready": False}


def test_setup_required_overrides_stale_detailed_monitor_readiness():
    from ui.backend.main import app

    result = RuntimePreflight(
        status="setup_required",
        setup_required=True,
        blockers=("secret_storage_key_mismatch",),
    )
    startup_patch, scheduler_patch = _lifespan_patches()
    with (
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.inspect_runtime_preflight", return_value=result),
        patch(
            "ui.backend.main._get_monitoring_health_summary",
            return_value={"ready": True, "dvrs": []},
        ),
        startup_patch,
        scheduler_patch,
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/api/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["ready"] is False
    assert payload["runtime"] == {
        "status": "setup_required",
        "setup_required": True,
        "blockers": ["secret_storage_key_mismatch"],
        "warnings": [],
    }


def test_key_recovery_attempt_window_prunes_limits_and_clears(monkeypatch):
    import ui.backend.main as ui_main

    ui_main._KEY_RECOVERY_ATTEMPTS.clear()
    monkeypatch.setattr(ui_main.time, "monotonic", lambda: 1000.0)
    ui_main._KEY_RECOVERY_ATTEMPTS["client"] = ui_main.deque([1.0, 999.0])
    assert ui_main._key_recovery_is_limited("client") is False
    assert list(ui_main._KEY_RECOVERY_ATTEMPTS["client"]) == [999.0]
    for _ in range(ui_main._KEY_RECOVERY_MAX_FAILURES - 1):
        ui_main._record_key_recovery_failure("client")
    assert ui_main._key_recovery_is_limited("client") is True
    ui_main._clear_key_recovery_failures("client")
    assert "client" not in ui_main._KEY_RECOVERY_ATTEMPTS


def test_protected_credential_counts_and_recovery_status_are_redacted(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    protected = [
        SimpleNamespace(collection="dvr_servers"),
        SimpleNamespace(collection="webhooks"),
        SimpleNamespace(collection="webhooks"),
        SimpleNamespace(collection="ignored"),
    ]
    candidates = [SimpleNamespace(available=False), SimpleNamespace(available=True)]
    status = SimpleNamespace(
        state="ready",
        key_mode="legacy_envelope",
        blocker=None,
        unreadable_credentials=("dvr_servers.0.api_key",),
    )
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch(
            "core.helpers.atomic_io.read_regular_file_bytes",
            return_value=json.dumps({"dvr_servers": [], "webhooks": {}}).encode(),
        ),
        patch(
            "core.helpers.protected_credentials.iter_protected_values",
            side_effect=lambda _settings: iter(protected),
        ),
        patch(
            "core.helpers.key_manager.inspect_key_recovery_status",
            return_value=status,
        ),
        patch(
            "core.helpers.atomic_io.legacy_secret_storage_key_candidates",
            return_value=candidates,
        ),
    ):
        assert ui_main._protected_credential_counts() == (1, 2)
        payload = ui_main._key_recovery_status_payload()

    assert payload == {
        "state": "managed_local",
        "recovery_required": False,
        "can_migrate": True,
        "can_reset": False,
        "blocker_code": None,
        "affected_dvr_credentials": 1,
        "affected_notification_credentials": 2,
        "legacy_input_detected": True,
        "unreadable_fields": ["dvr_servers.0.api_key"],
        "message": None,
    }

    with patch(
        "core.helpers.atomic_io.read_regular_file_bytes", return_value=b"[]"
    ):
        assert ui_main._protected_credential_counts() == (0, 0)


def test_key_recovery_migrate_accepts_each_one_time_input_and_rejects_mixing(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    status_payload = {
        "state": "managed_local",
        "recovery_required": False,
        "can_migrate": False,
        "can_reset": False,
        "blocker_code": None,
        "affected_dvr_credentials": 0,
        "affected_notification_credentials": 0,
        "legacy_input_detected": False,
        "unreadable_fields": [],
        "message": None,
    }
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_key_recovery_is_limited", return_value=False),
        patch.object(
            ui_main, "_key_recovery_status_payload", return_value=status_payload
        ),
        patch.object(ui_main, "_signal_core_hot_reload") as reload_signal,
        patch("core.helpers.key_manager.recover_legacy_envelope") as recover,
        patch("core.helpers.key_manager.install_recovered_raw_key") as install,
        patch("core.helpers.key_manager.ensure_managed_key") as ensure,
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        legacy = client.post(
            "/api/v1/runtime/key-recovery/migrate",
            json={"legacy_storage_key": "temporary legacy wrapping value"},
        )
        raw = client.post(
            "/api/v1/runtime/key-recovery/migrate",
            files={"raw_key_file": ("encryption.key", b"r" * 32)},
        )
        configured = client.post(
            "/api/v1/runtime/key-recovery/migrate",
            json={},
        )
        mixed = client.post(
            "/api/v1/runtime/key-recovery/migrate",
            data={"legacy_storage_key": "legacy"},
            files={"raw_key_file": ("encryption.key", b"r" * 32)},
        )
        invalid_file = client.post(
            "/api/v1/runtime/key-recovery/migrate",
            files={"raw_key_file": ("encryption.key", b"short")},
        )

    assert legacy.status_code == 200
    assert legacy.json()["message"].startswith("Credential protection recovered")
    recover.assert_called_once()
    install.assert_called_once()
    ensure.assert_called_once()
    assert reload_signal.call_count == 3
    assert raw.status_code == 200
    assert configured.status_code == 200
    assert mixed.status_code == 422
    assert mixed.json()["detail"]["code"] == "ERR_RUNTIME_KEY_RECOVERY_FAILED"
    assert invalid_file.status_code == 422
    assert invalid_file.json()["detail"]["code"] == (
        "ERR_RUNTIME_KEY_RECOVERY_FAILED"
    )


def test_key_recovery_migrate_rate_limit_is_structured(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_key_recovery_is_limited", return_value=True),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/runtime/key-recovery/migrate",
            json={"legacy_storage_key": "value"},
        )

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == (
        "ERR_RUNTIME_KEY_RECOVERY_RATE_LIMITED"
    )


def test_protected_credential_reset_requires_confirmation_and_reports_counts(
    tmp_path: Path,
):
    import ui.backend.main as ui_main

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    status_payload = {
        "state": "managed_local",
        "recovery_required": False,
        "can_migrate": False,
        "can_reset": False,
        "blocker_code": None,
        "affected_dvr_credentials": 0,
        "affected_notification_credentials": 0,
        "legacy_input_detected": False,
        "unreadable_fields": [],
        "message": None,
    }
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_protected_credential_counts", return_value=(2, 3)),
        patch.object(
            ui_main, "_key_recovery_status_payload", return_value=status_payload
        ),
        patch.object(ui_main, "_signal_core_hot_reload") as reload_signal,
        patch(
            "core.helpers.credential_maintenance.reset_protected_credentials",
            return_value=SimpleNamespace(recovery_snapshot=tmp_path / "snapshot"),
        ) as reset,
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        rejected = client.post(
            "/api/v1/runtime/key-recovery/reset",
            json={"confirmation": "reset"},
        )
        accepted = client.post(
            "/api/v1/runtime/key-recovery/reset",
            json={"confirmation": "RESET PROTECTED CREDENTIALS"},
        )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == (
        "ERR_RUNTIME_KEY_RESET_CONFIRMATION"
    )
    assert accepted.status_code == 200
    assert accepted.json()["cleared_dvr_credentials"] == 2
    assert accepted.json()["cleared_notification_credentials"] == 3
    assert accepted.json()["backup_created"] is True
    reset.assert_called_once()
    reload_signal.assert_called_once()


def test_protected_credential_reset_failure_is_structured(tmp_path: Path):
    import ui.backend.main as ui_main

    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch.object(ui_main, "CONFIG_DIR", tmp_path),
        patch.object(ui_main, "CW_DISABLE_AUTH", True),
        patch.object(ui_main, "_protected_credential_counts", return_value=(1, 1)),
        patch(
            "core.helpers.credential_maintenance.reset_protected_credentials",
            side_effect=RuntimeError("reset failed"),
        ),
    ):
        client = TestClient(ui_main.app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/runtime/key-recovery/reset",
            json={"confirmation": "RESET PROTECTED CREDENTIALS"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ERR_RUNTIME_KEY_RECOVERY_FAILED"
