from unittest.mock import patch

from starlette.testclient import TestClient

from core.helpers.runtime_preflight import RuntimePreflight


def test_runtime_preflight_is_public_and_minimal():
    from ui.backend.main import app

    result = RuntimePreflight(
        status="setup_required",
        setup_required=True,
        blockers=("secret_storage_key_missing",),
    )
    with (
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.inspect_runtime_preflight", return_value=result),
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
    with (
        patch("ui.backend.main.inspect_runtime_preflight", return_value=result),
        patch(
            "ui.backend.main._get_monitoring_health_summary",
            return_value={"ready": True, "dvrs": []},
        ),
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
    with (
        patch("ui.backend.main.CW_DISABLE_AUTH", True),
        patch("ui.backend.main.inspect_runtime_preflight", return_value=result),
        patch(
            "ui.backend.main._get_monitoring_health_summary",
            return_value={"ready": True, "dvrs": []},
        ),
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
