from pathlib import Path


_REPO_DIR = Path(__file__).resolve().parents[3]


def test_dockerfile_healthcheck_uses_liveness_endpoint():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "http://127.0.0.1:8501/healthz/live" in dockerfile
    assert "http://localhost:8501/api/health" not in dockerfile


def test_dockerfile_pins_pnpm_and_uses_frozen_lockfile():
    dockerfile = (_REPO_DIR / "deploy" / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    package_json = (_REPO_DIR / "app" / "ui" / "package.json").read_text(
        encoding="utf-8"
    )

    assert "corepack enable" in dockerfile
    assert '"packageManager": "pnpm@11.8.0+' in package_json
    assert "pnpm install --frozen-lockfile" in dockerfile


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


def test_runtime_diagnostics_are_not_under_legacy_test_package():
    legacy_dir = _REPO_DIR / "app" / "core" / "test"
    legacy_sources = [
        path
        for path in legacy_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    ]

    assert legacy_sources == []
    assert (_REPO_DIR / "app" / "core" / "diagnostics" / "__init__.py").is_file()


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
        "      - name: Extract metadata",
        "      - name: Build and push",
        "      - name: Attest build provenance",
    ]
    for marker in publish_markers:
        assert gate_index < release.index(marker)
