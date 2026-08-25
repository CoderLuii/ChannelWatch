from pathlib import Path


_REPO_DIR = Path(__file__).resolve().parents[3]


def test_dockerfile_healthcheck_uses_liveness_endpoint():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "http://127.0.0.1:8501/healthz/live" in dockerfile
    assert "http://localhost:8501/api/health" not in dockerfile


def test_official_image_enables_live_reporting_with_trusted_hosted_endpoints():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert 'ENV CHANNELWATCH_REPORT_MODE="live"' in dockerfile
    assert (
        'ENV CHANNELWATCH_REPORT_ENDPOINT="https://channelwatch.coderluii.dev/api/reports"'
        in dockerfile
    )
    assert (
        'ENV CHANNELWATCH_REPORT_PORTAL_URL="https://channelwatch.coderluii.dev/report"'
        in dockerfile
    )


def test_dockerfile_pins_pnpm_and_uses_frozen_lockfile():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    package_json = (_REPO_DIR / "app" / "ui" / "package.json").read_text(
        encoding="utf-8"
    )

    assert "corepack enable" in dockerfile
    assert '"packageManager": "pnpm@11.21.0+' in package_json
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "/venv/bin/pip uninstall --yes pip setuptools" in dockerfile


def test_dockerfile_pins_reviewed_python_bases_and_timezone_package():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert (
        "cgr.dev/chainguard/python:latest-dev@sha256:"
        "4bf7e945777010672b8ccd5d2ae2c41c91ad6d3478878347c731ae536d506bef"
    ) in dockerfile
    assert (
        "cgr.dev/chainguard/python:latest@sha256:"
        "1f6779775c9f466890da563e411cb677045a6c20b6a65160eefad1deffb5012c"
    ) in dockerfile
    assert "apk add --no-cache tzdata=2026c-r0" in dockerfile
    assert "apk add --no-cache tzdata \\" not in dockerfile


def test_dockerfile_builds_static_ui_on_native_build_platform():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert (
        "FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:"
        "d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 "
        "AS ui-builder"
    ) in dockerfile
    assert "FROM --platform=$BUILDPLATFORM cgr.dev/chainguard/python" not in dockerfile


def test_official_image_normalizes_generated_python_and_ui_artifacts():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    next_config = (_REPO_DIR / "app" / "ui" / "next.config.mjs").read_text(
        encoding="utf-8"
    )

    assert 'ENV CHANNELWATCH_BUILD_ID="${GIT_SHA}"' in dockerfile
    assert 'ENV SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}"' in dockerfile
    assert "generateBuildId" in next_config
    assert "process.env.CHANNELWATCH_BUILD_ID" in next_config


def test_official_image_bundles_project_and_third_party_legal_notices():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    dockerignore = (
        _REPO_DIR / "deploy" / "docker" / "Dockerfile.dockerignore"
    ).read_text(encoding="utf-8")

    assert "COPY LICENSE /licenses/channelwatch/LICENSE" in dockerfile
    assert "COPY docs/legal/NOTICE /licenses/channelwatch/NOTICE" in dockerfile
    assert (
        "COPY docs/legal/THIRD_PARTY_LICENSES.md "
        "/licenses/channelwatch/THIRD_PARTY_LICENSES.md"
    ) in dockerfile
    assert "!docs/legal/THIRD_PARTY_LICENSES.md" in dockerignore
    assert "COPY scripts/release/copyleft_licenses.py" in dockerfile
    assert "COPY docs/legal/CORRESPONDING_SOURCE.md" in dockerfile
    assert (
        "COPY --from=python-deps /release-licenses/copyleft "
        "/licenses/channelwatch/copyleft"
    ) in dockerfile
    assert "!docs/legal/CORRESPONDING_SOURCE.md" in dockerignore


def test_primary_compose_project_name_is_lowercase():
    compose = (
        _REPO_DIR / "deploy" / "compose" / "default.yml"
    ).read_text(encoding="utf-8").splitlines()

    assert compose[0] == "name: channelwatch"


def test_docs_use_packaged_core_module_for_diagnostics():
    for rel in ("README.md", "docs/reference/health-diagnostics.md"):
        content = (_REPO_DIR / rel).read_text(encoding="utf-8")
        assert "python -m channelwatch.main" not in content
        assert "channelwatch doctor" in content or "python -m core.main" in content


def test_health_docs_keep_public_readiness_minimal_and_detailed_health_protected():
    for rel in ("docs/reference/api.md", "docs/reference/health-diagnostics.md"):
        content = (_REPO_DIR / rel).read_text(encoding="utf-8")
        ready_section = content.split("### `GET /healthz/ready`", 1)[1]
        end_marker = (
            "### `GET /healthz/startup`"
            if rel == "docs/reference/api.md"
            else "## Kubernetes style probes"
        )
        ready_section = ready_section.split(end_marker, 1)[0]
        assert '{"status":"ready","ready":true}' in ready_section
        assert '"dvrs"' not in ready_section
        assert '"tested_version_range"' not in ready_section
        assert "DVR names, IDs" in ready_section

    api_reference = (_REPO_DIR / "docs/reference/api.md").read_text(encoding="utf-8")
    detailed_section = api_reference.split("### `GET /api/health`", 1)[1].split(
        "### `GET /healthz/live`", 1
    )[0]
    assert "api_key or RBAC session when configured" in detailed_section
    assert '"notification_routing_diagnostics"' in detailed_section


def test_runtime_diagnostics_are_not_under_legacy_test_package():
    legacy_dir = _REPO_DIR / "app" / "core" / "test"
    legacy_sources = [
        path
        for path in legacy_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    ]

    assert legacy_sources == []
    assert (_REPO_DIR / "app" / "core" / "diagnostics" / "__init__.py").is_file()


def test_api_reference_matches_public_recovery_and_logout_csrf_contracts():
    api_reference = (_REPO_DIR / "docs/reference/api.md").read_text(encoding="utf-8")
    recovery = api_reference.split(
        "### `GET /api/v1/update/recovery/status`", 1
    )[1].split("### `POST /api/v1/update/recovery/check`", 1)[0]
    logout = api_reference.split("### `POST /api/v1/auth/logout`", 1)[1].split(
        "### `GET /api/v1/auth/whoami`", 1
    )[0]

    assert "intentionally public and minimal" in recovery
    assert "no version fingerprint" in recovery
    assert "RBAC session cookie plus matching `X-CSRF-Token`" in logout
    assert "200, 401, 403, 429" in logout
    assert '-H "X-CSRF-Token: $CSRF_TOKEN"' in logout


def test_release_workflow_changelog_gate_precedes_publish_steps():
    release = (
        _REPO_DIR / ".github" / "workflows" / "docker-publish.yml"
    ).read_text(encoding="utf-8")

    gate_index = release.index("name: Verify CHANGELOG entry")
    gate_block = release[gate_index : release.index("\n      - name:", gate_index + 1)]
    assert "docs/releases/CHANGELOG.md" in gate_block
    assert "exit 1" in gate_block

    publish_markers = [
        "      - name: Login to Docker Hub",
        "      - name: Download exact approved candidate image",
        "      - name: Publish the scanned images and assemble exact manifests",
        "      - name: Attest build provenance",
    ]
    for marker in publish_markers:
        assert gate_index < release.index(marker)


def test_release_workflow_routes_dynamic_values_through_environment():
    release = (
        _REPO_DIR / ".github" / "workflows" / "docker-publish.yml"
    ).read_text(encoding="utf-8")

    assert 'version="${{ steps.version.outputs.version }}"' not in release
    live_step = release.split(
        "      - name: Verify live stable update manifest", 1
    )[1]
    assert '--version "${{ steps.release.outputs.version }}"' not in live_step
    assert "RAW_RELEASE_VERSION: ${{ steps.release.outputs.version }}" in live_step
