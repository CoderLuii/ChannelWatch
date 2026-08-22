import json
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi.routing import APIRoute
from starlette.testclient import TestClient
from ui.backend import main as backend_main

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "app" / "ui" / "api-contracts.json"
EXPECTED_LEGACY_BROWSER_ROUTES = {
    ("GET", "/api/about"),
    ("GET", "/api/system-info"),
    ("POST", "/api/run_test/{test_name_url}"),
    ("GET", "/api/discover-servers"),
    ("GET", "/api/recordings/upcoming"),
    ("GET", "/api/recordings/active"),
    ("GET", "/api/streams/active"),
    ("GET", "/api/streams/details"),
    ("GET", "/api/recent-activity"),
    ("GET", "/api/activity-history"),
    ("POST", "/api/clear-activity-history"),
    ("POST", "/api/regenerate-api-key"),
}
EXPLICIT_SESSION_CSRF_ROUTES = {
    ("POST", "/api/v1/auth/change-credentials"),
    ("POST", "/api/v1/auth/logout"),
}


def _contracts():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _minimum_roles_by_route():
    roles = {}
    for route in backend_main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        minimum_role = None
        for dependency in route.dependant.dependencies:
            candidate = getattr(
                dependency.call, "channelwatch_minimum_role", None
            )
            if candidate is not None:
                minimum_role = candidate
        for method in route.methods:
            roles[(method, route.path)] = minimum_role
    return roles


def test_browser_contracts_exist_in_generated_openapi_with_expected_methods():
    paths = backend_main.app.openapi()["paths"]
    for contract in _contracts():
        assert contract["path"] in paths, contract
        assert contract["method"].lower() in paths[contract["path"]], contract


def test_browser_contracts_match_middleware_auth_and_role_policy():
    roles = _minimum_roles_by_route()
    for contract in _contracts():
        identity = (contract["method"], contract["path"])
        is_exempt = backend_main._middleware_auth_exempt(*identity)
        if contract["auth"] == "middleware":
            assert not is_exempt, contract
        else:
            assert is_exempt, contract
        assert roles.get(identity) == contract["minimum_role"], contract


def test_browser_contracts_disposition_aliases_and_error_envelopes_explicitly():
    legacy_routes = set()
    for contract in _contracts():
        assert isinstance(contract["legacy_alias"], bool)
        assert contract["error_shape"] == "structured-detail"
        if contract["legacy_alias"]:
            legacy_routes.add((contract["method"], contract["path"]))

    assert legacy_routes == EXPECTED_LEGACY_BROWSER_ROUTES

    response = backend_main._structured_error_response(
        backend_main.ErrorCode.AUTH_UNAUTHENTICATED
    )
    payload = json.loads(response.body)
    assert set(payload["detail"]) == {
        "code",
        "message",
        "remediation",
        "docs_url",
    }


def test_browser_contracts_match_central_csrf_policy():
    explicit_session_csrf = set()
    for contract in _contracts():
        identity = (contract["method"], contract["path"])
        if contract["auth"] == "session-csrf":
            explicit_session_csrf.add(identity)
        if (
            contract["auth"] == "middleware"
            and contract["method"] in backend_main.CSRF_PROTECTED_METHODS
        ):
            assert not backend_main._middleware_auth_exempt(*identity), contract

    assert explicit_session_csrf == EXPLICIT_SESSION_CSRF_ROUTES


def test_every_middleware_browser_route_returns_structured_auth_error(monkeypatch):
    async def api_key_mode():
        return "api_key", "contract-test-key", False

    monkeypatch.setattr(backend_main, "CW_DISABLE_AUTH", False)
    monkeypatch.setattr(backend_main, "RBAC_ENABLED", False)
    monkeypatch.setattr(backend_main, "_get_runtime_auth_snapshot", api_key_mode)
    client = TestClient(backend_main.app, raise_server_exceptions=False)
    try:
        for contract in _contracts():
            if contract["auth"] != "middleware":
                continue
            concrete_path = re.sub(r"\{[^}]+\}", "contract-id", contract["path"])
            response = client.request(contract["method"], concrete_path)
            assert response.status_code == 401, contract
            detail = response.json()["detail"]
            assert detail["code"] == backend_main.ErrorCode.AUTH_INVALID_KEY, contract
            assert set(detail) == {
                "code",
                "message",
                "remediation",
                "docs_url",
            }, contract
    finally:
        client.close()


def test_browser_role_and_csrf_failures_return_structured_403(monkeypatch):
    async def rbac_mode():
        return "rbac", "", False

    session = SimpleNamespace(user_id=7, csrf_token="contract-csrf")
    monkeypatch.setattr(backend_main, "CW_DISABLE_AUTH", False)
    monkeypatch.setattr(backend_main, "RBAC_ENABLED", True)
    monkeypatch.setattr(backend_main, "_get_runtime_auth_snapshot", rbac_mode)
    monkeypatch.setattr(backend_main, "_lookup_user_session", lambda _token: session)
    monkeypatch.setattr(
        backend_main,
        "_get_user_role_for_auth_check",
        lambda _user_id: (True, "viewer"),
    )
    backend_main.rate_limiter._requests.clear()
    client = TestClient(backend_main.app, raise_server_exceptions=False)
    client.cookies.set("channelwatch_session", "contract-session")
    try:
        for contract in _contracts():
            if contract["minimum_role"] is None:
                continue
            concrete_path = re.sub(r"\{[^}]+\}", "contract-id", contract["path"])
            headers = (
                {"X-CSRF-Token": "contract-csrf"}
                if contract["method"] in backend_main.CSRF_PROTECTED_METHODS
                else {}
            )
            response = client.request(
                contract["method"], concrete_path, headers=headers
            )
            assert response.status_code == 403, contract
            detail = response.json()["detail"]
            assert detail["code"] == backend_main.ErrorCode.AUTH_FORBIDDEN, contract
            assert set(detail) == {
                "code",
                "message",
                "remediation",
                "docs_url",
            }, contract

        csrf_requests = [
            ("/api/settings", {}),
            ("/api/v1/auth/logout", {}),
            (
                "/api/v1/auth/change-credentials",
                {"json": {"current_password": "unused"}},
            ),
        ]
        for path, kwargs in csrf_requests:
            response = client.post(path, **kwargs)
            assert response.status_code == 403, path
            detail = response.json()["detail"]
            assert detail["code"] == backend_main.ErrorCode.AUTH_CSRF_INVALID, path
            assert set(detail) == {
                "code",
                "message",
                "remediation",
                "docs_url",
            }, path
    finally:
        client.close()
        backend_main.rate_limiter._requests.clear()
