import ast
import json
from pathlib import Path
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from starlette.testclient import TestClient

from ui.backend.error_catalog import (
    ErrorCode,
    catalog_entry,
    structured_error,
)


class TestErrorCodeConstants:
    def test_auth_invalid_key(self):
        assert ErrorCode.AUTH_INVALID_KEY == "ERR_AUTH_INVALID_KEY"

    def test_dvr_not_found(self):
        assert ErrorCode.DVR_NOT_FOUND == "ERR_DVR_NOT_FOUND"

    def test_settings_save_failed(self):
        assert ErrorCode.SETTINGS_SAVE_FAILED == "ERR_SETTINGS_SAVE_FAILED"

    def test_all_codes_are_strings(self):
        codes = [
            v
            for k, v in vars(ErrorCode).items()
            if not k.startswith("_") and isinstance(v, str)
        ]
        assert len(codes) >= 20
        for code in codes:
            assert code.startswith("ERR_"), f"{code!r} does not start with ERR_"


class TestCatalogEntry:
    def test_known_code_returns_entry(self):
        entry = catalog_entry(ErrorCode.DVR_NOT_FOUND)
        assert entry is not None
        assert entry.code == ErrorCode.DVR_NOT_FOUND
        assert isinstance(entry.http_status, int)
        assert isinstance(entry.message, str)

    def test_unknown_code_returns_none(self):
        result = catalog_entry("ERR_DOES_NOT_EXIST")
        assert result is None

    def test_every_known_code_is_in_catalog(self):
        codes = [
            v
            for k, v in vars(ErrorCode).items()
            if not k.startswith("_") and isinstance(v, str)
        ]
        for code in codes:
            entry = catalog_entry(code)
            assert entry is not None, f"Catalog missing entry for {code!r}"

    def test_all_entries_have_non_empty_message(self):
        codes = [
            v
            for k, v in vars(ErrorCode).items()
            if not k.startswith("_") and isinstance(v, str)
        ]
        for code in codes:
            entry = catalog_entry(code)
            assert entry is not None
            assert entry.message.strip(), f"Empty message for {code!r}"

    def test_all_entries_have_valid_http_status(self):
        codes = [
            v
            for k, v in vars(ErrorCode).items()
            if not k.startswith("_") and isinstance(v, str)
        ]
        for code in codes:
            entry = catalog_entry(code)
            assert entry is not None
            assert 400 <= entry.http_status < 600, (
                f"Unexpected http_status {entry.http_status} for {code!r}"
            )


class TestStructuredError:
    def test_returns_http_exception(self):
        exc = structured_error(ErrorCode.DVR_NOT_FOUND)
        assert isinstance(exc, HTTPException)

    def test_uses_catalog_status(self):
        entry = catalog_entry(ErrorCode.DVR_NOT_FOUND)
        exc = structured_error(ErrorCode.DVR_NOT_FOUND)
        assert exc.status_code == entry.http_status

    def test_detail_is_dict_with_required_fields(self):
        exc = structured_error(ErrorCode.DVR_NOT_FOUND)
        detail = exc.detail
        assert isinstance(detail, dict)
        assert "code" in detail
        assert "message" in detail
        assert "remediation" in detail
        assert "docs_url" in detail

    def test_detail_code_matches_error_code(self):
        exc = structured_error(ErrorCode.SETTINGS_SAVE_FAILED)
        assert exc.detail["code"] == ErrorCode.SETTINGS_SAVE_FAILED

    def test_override_message(self):
        custom_msg = "DVR 'dvr_abc123' was not found"
        exc = structured_error(ErrorCode.DVR_NOT_FOUND, message=custom_msg)
        assert exc.detail["message"] == custom_msg

    def test_default_message_used_when_no_override(self):
        entry = catalog_entry(ErrorCode.DVR_NOT_FOUND)
        exc = structured_error(ErrorCode.DVR_NOT_FOUND)
        assert exc.detail["message"] == entry.message

    def test_override_remediation(self):
        custom_rem = "Contact your administrator."
        exc = structured_error(ErrorCode.AUTH_INVALID_KEY, remediation=custom_rem)
        assert exc.detail["remediation"] == custom_rem

    def test_override_docs_url(self):
        exc = structured_error(ErrorCode.DVR_NOT_FOUND, docs_url="https://example.com")
        assert exc.detail["docs_url"] == "https://example.com"

    def test_unknown_code_falls_back_to_generic(self):
        exc = structured_error("ERR_NONEXISTENT_CODE_XYZ")
        assert exc.detail["code"] == ErrorCode.UNKNOWN
        assert exc.status_code == 500

    def test_detail_is_json_serialisable(self):
        exc = structured_error(ErrorCode.ACTIVITY_SORT_INVALID)
        serialised = json.dumps(exc.detail)
        roundtripped = json.loads(serialised)
        assert roundtripped["code"] == ErrorCode.ACTIVITY_SORT_INVALID


@pytest.fixture
def settings_file(tmp_path):
    settings = {
        "dvr_servers": [
            {
                "id": "dvr_test",
                "host": "192.168.1.100",
                "port": 8089,
                "name": "Test DVR",
                "enabled": True,
            }
        ],
        "tz": "America/New_York",
        "api_key": "test-key-abc",
    }
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(settings))
    return f


@pytest.fixture
def client(settings_file):
    with (
        patch("ui.backend.config.CONFIG_FILE", settings_file),
        patch("ui.backend.config.CONFIG_DIR", settings_file.parent),
        patch("ui.backend.main.CW_DISABLE_AUTH", False),
        patch("ui.backend.main.API_KEY_CACHE", "test-key-abc"),
    ):
        from ui.backend.main import app

        yield TestClient(app, raise_server_exceptions=False)


class TestMigratedEndpointPayloads:
    def test_settings_save_fails_with_structured_error(self, client, settings_file):
        with patch(
            "ui.backend.main._save_settings_and_signal_reload",
            side_effect=RuntimeError("disk full"),
        ):
            resp = client.post(
                "/api/settings",
                json=json.loads(settings_file.read_text()),
                headers={"X-API-Key": "test-key-abc"},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == ErrorCode.SETTINGS_SAVE_FAILED
        assert "message" in detail
        assert "remediation" in detail

    def test_dvr_soft_delete_not_found_structured(self, client):
        resp = client.post(
            "/api/dvrs/dvr_nonexistent/soft-delete",
            headers={"X-API-Key": "test-key-abc"},
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == ErrorCode.DVR_NOT_FOUND

    def test_dvr_hard_delete_not_found_structured(self, client):
        resp = client.delete(
            "/api/dvrs/dvr_nonexistent",
            headers={"X-API-Key": "test-key-abc"},
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == ErrorCode.DVR_NOT_FOUND

    def test_activity_history_sort_invalid_structured(self, client):
        resp = client.get(
            "/api/activity-history?sort=sideways",
            headers={"X-API-Key": "test-key-abc"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == ErrorCode.ACTIVITY_SORT_INVALID

    def test_invalid_api_key_uses_structured_auth_error(self, client):
        resp = client.get("/api/activity-history", headers={"X-API-Key": "wrong"})

        assert resp.status_code == 401
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == ErrorCode.AUTH_INVALID_KEY

    def test_v1_dvr_not_found_uses_structured_error(self, client):
        resp = client.get(
            "/api/v1/dvrs/dvr_nonexistent",
            headers={"X-API-Key": "test-key-abc"},
        )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == ErrorCode.DVR_NOT_FOUND

    def test_structured_payload_always_has_four_fields(self, client):
        resp = client.get(
            "/api/activity-history?sort=sideways",
            headers={"X-API-Key": "test-key-abc"},
        )
        detail = resp.json()["detail"]
        for field in ("code", "message", "remediation", "docs_url"):
            assert field in detail, (
                f"Missing field {field!r} in structured error payload"
            )

    def test_main_has_no_public_plain_string_http_exception_details(self):
        main_path = Path(__file__).resolve().parents[2] / "ui" / "backend" / "main.py"
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
        offenders = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            func_name = func.id if isinstance(func, ast.Name) else ""
            if func_name != "HTTPException":
                continue
            for keyword in node.exc.keywords:
                if keyword.arg != "detail":
                    continue
                if isinstance(keyword.value, (ast.Constant, ast.JoinedStr)):
                    offenders.append(node.lineno)

        assert offenders == []
