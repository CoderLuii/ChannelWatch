from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/docker-publish.yml"


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def job_text(name: str, next_name: str) -> str:
    return workflow_text().split(f"  {name}:", 1)[1].split(
        f"\n  {next_name}:", 1
    )[0]


def test_legacy_upgrade_job_runs_exact_native_matrix_headlessly():
    workflow = yaml.safe_load(workflow_text())
    job = workflow["jobs"]["legacy-app-upgrade"]
    assert job["needs"] == [
        "candidate-source-gates",
        "candidate-signing",
        "runtime-dependencies",
    ]
    assert job["strategy"]["matrix"]["include"] == [
        {"architecture": "amd64", "runner": "ubuntu-24.04"},
        {"architecture": "arm64", "runner": "ubuntu-24.04-arm"},
    ]
    text = job_text("legacy-app-upgrade", "candidate-qualification")
    scenarios = [
        "stock-1.4.0",
        "overlay-1.4.0-to-1.4.1",
        "overlay-1.4.0-to-1.4.5",
        "stock-1.4.1",
        "overlay-1.4.1-to-1.4.5",
        "stock-1.4.5",
    ]
    assert all(f"            {scenario}\n" in text for scenario in scenarios)
    assert "python scripts/release/verify-legacy-app-upgrade.py" in text
    assert "--release-config scripts/release/release-config.json" in text
    assert "BROWSER: none" in text
    assert "PLAYWRIGHT_HTML_OPEN: never" in text
    assert "pnpm exec playwright install --with-deps chromium" in text


def test_legacy_upgrade_gate_is_mandatory_and_aggregates_all_receipts():
    workflow = yaml.safe_load(workflow_text())
    candidate = workflow["jobs"]["candidate-qualification"]
    assert "legacy-app-upgrade" in candidate["needs"]
    text = job_text("candidate-qualification", "signed-candidate")
    assert "pattern: legacy-app-upgrade-*-${{ github.sha }}" in text
    assert "merge-multiple: true" in text
    assert "python scripts/release/aggregate-legacy-app-upgrades.py" in text
    assert "--release-config scripts/release/release-config.json" in text
    assert "for architecture in amd64 arm64" in text
    assert "jq -r '.scenarios[].id' scripts/release/legacy-app-upgrade-baselines.json" in text
    assert "channelwatch-legacy-app-upgrade-${tag}-logs.tar" in text


def test_legacy_upgrade_evidence_is_carried_through_release_and_public_checksums():
    text = workflow_text()
    release = job_text("build-update-bundle-and-release", "build-and-push")
    public = job_text("verify-public-release-assets", "sync-site")
    for fragment in (
        "channelwatch-legacy-app-upgrade-${TAG}-evidence.json",
        "channelwatch-legacy-app-upgrade-${TAG}-logs.tar",
        "channelwatch-legacy-app-upgrade-${TAG}-${architecture}-${scenario}.json",
    ):
        assert fragment in release
    for fragment in (
        "channelwatch-legacy-app-upgrade-${tag}-evidence.json",
        "channelwatch-legacy-app-upgrade-${tag}-logs.tar",
        "channelwatch-legacy-app-upgrade-${tag}-${architecture}-${scenario}.json",
    ):
        assert fragment in public
    assert "expected_checksum_count=32" in public


def test_every_update_asset_verification_is_explicitly_config_bound():
    text = workflow_text()
    invocations = text.split("python scripts/release/verify-update-assets.py \\\n")[1:]
    assert len(invocations) == 4
    assert all(
        invocation.lstrip().startswith(
            "--release-config scripts/release/release-config.json"
        )
        for invocation in invocations
    )


def test_legacy_runtime_proof_requires_fresh_v2_checks_and_no_visible_browser():
    verifier = (ROOT / "scripts/release/verify-legacy-app-upgrade.py").read_text(
        encoding="utf-8"
    )
    assert 'CATALOG_URL = "https://channelwatch.coderluii.dev/updates/v2/stable.json"' in verifier
    assert "checked_current_on_v2(" in verifier
    assert "post_activation_check" in verifier
    assert "post_restart_check" in verifier
    assert "headless:true" in (
        ROOT / "scripts/release/verify-image-upgrade.py"
    ).read_text(encoding="utf-8")
