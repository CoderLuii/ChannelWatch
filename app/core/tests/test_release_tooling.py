import base64
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[3]
TEST_RELEASE_GIT_SHA = "0123456789abcdef0123456789abcdef01234567"
TEST_RELEASE_URL = "https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.17"
TEST_BUNDLE_URL = (
    "https://github.com/CoderLuii/ChannelWatch/releases/download/"
    "v0.9.17/channelwatch-app-v0.9.17.zip"
)


def _load_script(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_shell_step(workflow: str, name: str) -> tuple[str, str]:
    marker = f"      - name: {name}\n"
    block = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
    run_marker = "        run: |\n"
    shell = block.split(run_marker, 1)[1]
    return block, "\n".join(
        line.removeprefix("          ") for line in shell.splitlines()
    )


def test_release_body_for_0912_uses_dependency_copy(monkeypatch, capsys):
    module = _load_script(
        "render_release_body",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v0.9.12",
        "releaseDate": "2026-08-11",
        "changelogHighlights": [
            "Keep idle Channels DVR event streams connected.",
            "Stop dashboard stream requests from rebuilding core settings.",
        ],
        "changelogSections": {
            "Changed": [
                "Keep idle Channels DVR event streams connected.",
            ],
            "Fixed": [
                "Stop dashboard stream requests from rebuilding core settings.",
            ],
        },
        "dockerTag": "0.9.12",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.12"])

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.12 - Dependency maintenance\n")
    assert "## Fixed" in output
    assert "## Changed" in output
    assert "adds the new in-app **Update Center**" not in output
    assert "`coderluii/channelwatch:0.9.12`" in output


def test_release_body_for_0913_uses_reporting_copy(monkeypatch, capsys):
    module = _load_script(
        "render_release_body_0913",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v0.9.13",
        "releaseDate": "2026-08-12",
        "changelogHighlights": ["Enable reliable live problem reporting."],
        "changelogSections": {
            "Fixed": ["Enable reliable live problem reporting."],
            "Security": ["Keep private support attachments out of public issues."],
        },
        "dockerTag": "0.9.13",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.13"])

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.13 - Reporting reliability\n")
    assert "## Fixed" in output
    assert "## Security" in output
    assert "`coderluii/channelwatch:0.9.13`" in output


def test_release_body_for_0914_uses_reporting_and_update_copy(monkeypatch, capsys):
    module = _load_script("render_release_body_0914", "scripts/release/render-release-body.py")
    metadata = {
        "versionTag": "v0.9.14", "releaseDate": "2026-08-13",
        "changelogHighlights": ["Make reporting and update recovery clearer."],
        "changelogSections": {"Fixed": ["Make reporting and update recovery clearer."]},
        "dockerTag": "0.9.14",
    }
    monkeypatch.setattr(module, "load_exporter", lambda: SimpleNamespace(collect_metadata=lambda *args: metadata))
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.14"])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.14 - Reporting and update reliability\n")
    assert "## Fixed" in output
    assert "`coderluii/channelwatch:0.9.14`" in output


def test_release_body_for_0915_uses_update_and_reporting_copy(monkeypatch, capsys):
    module = _load_script("render_release_body_0915", "scripts/release/render-release-body.py")
    metadata = {
        "versionTag": "v0.9.15", "releaseDate": "2026-08-14",
        "changelogHighlights": ["Improve update restart recovery and report privacy."],
        "changelogSections": {
            "Fixed": ["Improve update restart recovery."],
            "Security": ["Strengthen public report previews."],
        },
        "dockerTag": "0.9.15",
    }
    monkeypatch.setattr(module, "load_exporter", lambda: SimpleNamespace(collect_metadata=lambda *args: metadata))
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.15"])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.15 - Update and reporting reliability\n")
    assert "## Fixed" in output
    assert "## Security" in output
    assert "`coderluii/channelwatch:0.9.15`" in output


def test_release_body_preserves_0910_repair_copy(monkeypatch, capsys):
    module = _load_script(
        "render_release_body_0910",
        "scripts/release/render-release-body.py",
    )
    monkeypatch.setattr(sys, "argv", ["render-release-body.py", "--version", "0.9.10"])

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith("# ChannelWatch v0.9.10 - Runtime and Config Repair\n")
    assert "Show v0.9.10 as container image update required" in output
    assert "\n## Docs\n" in output
    assert "\n## Images\n" in output


def test_update_bundle_highlights_come_from_changelog():
    module = _load_script(
        "build_update_bundle",
        "scripts/release/build-update-bundle.py",
    )
    exporter = SimpleNamespace(
        parse_changelog=lambda version: {
            "changelogHighlights": [
                "Keep idle Channels DVR event streams connected.",
                "Stop dashboard stream requests from rebuilding core settings.",
            ]
        }
    )

    assert module.release_highlights("0.9.12", exporter=exporter) == [
        "Keep idle Channels DVR event streams connected.",
        "Stop dashboard stream requests from rebuilding core settings.",
    ]


def test_update_bundle_copies_required_legal_files(tmp_path):
    module = _load_script(
        "build_update_bundle_legal_files",
        "scripts/release/build-update-bundle.py",
    )

    module.copy_release_legal_files(tmp_path)

    assert (tmp_path / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert (tmp_path / "NOTICE").read_bytes() == (
        ROOT / "docs/legal/NOTICE"
    ).read_bytes()
    assert (tmp_path / "THIRD_PARTY_LICENSES.md").read_bytes() == (
        ROOT / "docs/legal/THIRD_PARTY_LICENSES.md"
    ).read_bytes()


def test_copyleft_license_manifest_is_pinned_and_complete():
    module = _load_script(
        "copyleft_release_licenses",
        "scripts/release/copyleft_licenses.py",
    )

    assert module.SPDX_LICENSE_LIST_COMMIT == (
        "5bf6d9610255540bfbee6890765a616042bf1e11"
    )
    assert {artifact.filename for artifact in module.COPYLEFT_LICENSES} == {
        "GPL-1.0-only.txt",
        "GPL-2.0-only.txt",
        "GPL-3.0-only.txt",
        "LGPL-2.1-only.txt",
        "GCC-exception-3.1.txt",
    }
    assert all(
        len(artifact.sha256) == 64
        and artifact.url.startswith(
            "https://raw.githubusercontent.com/spdx/license-list-data/"
            f"{module.SPDX_LICENSE_LIST_COMMIT}/text/"
        )
        for artifact in module.COPYLEFT_LICENSES
    )


def test_copyleft_license_archive_is_deterministic(tmp_path):
    module = _load_script(
        "copyleft_release_archive",
        "scripts/release/copyleft_licenses.py",
    )
    source = tmp_path / "licenses"
    source.mkdir()
    (source / "GPL-3.0-only.txt").write_text("GPL text\n", encoding="utf-8")
    (source / "CORRESPONDING_SOURCE.md").write_text(
        "# Source map\n", encoding="utf-8"
    )
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    module.write_deterministic_archive(source, first)
    os.utime(source / "GPL-3.0-only.txt", (1_900_000_000, 1_900_000_000))
    module.write_deterministic_archive(source, second)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == [
            "CORRESPONDING_SOURCE.md",
            "GPL-3.0-only.txt",
        ]


def test_corresponding_source_map_pins_exact_release_sources():
    source_map = (ROOT / "docs/legal/CORRESPONDING_SOURCE.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "8190a1652f4534ad3feebd3b48066514f0f4375f",
        "gdbm 1.26-r5",
        "glibc-2.43 2.43-r15",
        "libgcc 16.2.0-r0",
        "libuuid 2.42.2-r3",
        "libzstd1 1.5.7-r8",
        "readline 8.3-r2",
        "xz 5.8.3-r2",
        "zeroconf 0.150.0",
        "a5fe7feab1de6ef5e541e0a3d07e534fd91629b813fc27281593584100f63164",
        "5bf6d9610255540bfbee6890765a616042bf1e11",
    ):
        assert required in source_map


def test_release_config_marks_0917_as_image_required():
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )

    assert config == {
        "version": "0.9.17",
        "image_required": True,
        "release_heading": (
            "# ChannelWatch v0.9.17 - Setup, diagnostics, and LAN reliability"
        ),
        "verification_assets": True,
    }


def test_release_version_surfaces_accept_multi_digit_patch():
    module = _load_script(
        "export_release_metadata",
        "scripts/release/export-site-release-metadata.py",
    )

    metadata = module.collect_metadata(
        source_ref=None,
        release_url=None,
    )

    assert metadata["version"] == "0.9.17"
    assert metadata["versionTag"] == "v0.9.17"
    assert metadata["dockerTag"] == "0.9.17"
    assert metadata["helmChartVersion"] == "0.9.17"
    assert metadata["helmAppVersion"] == "0.9.17"


def test_release_body_for_0917_links_license_and_sbom_assets(monkeypatch, capsys):
    module = _load_script(
        "render_release_body_0917_legal_assets",
        "scripts/release/render-release-body.py",
    )
    metadata = {
        "versionTag": "v0.9.17",
        "releaseDate": "2026-08-21",
        "changelogHighlights": ["Bundle release license notices."],
        "changelogSections": {
            "Security": ["Bundle release license notices."],
        },
        "dockerTag": "0.9.17",
    }
    monkeypatch.setattr(
        module,
        "load_exporter",
        lambda: SimpleNamespace(collect_metadata=lambda *args: metadata),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["render-release-body.py", "--version", "0.9.17"],
    )

    assert module.main() == 0

    output = capsys.readouterr().out
    assert output.startswith(
        "# ChannelWatch v0.9.17 - Setup, diagnostics, and LAN reliability\n"
    )
    assert "## License and verification" in output
    assert "channelwatch-v0.9.17-THIRD-PARTY-LICENSES.md" in output
    assert "channelwatch-v0.9.17-CORRESPONDING-SOURCE.md" in output
    assert "channelwatch-v0.9.17-COPYLEFT-LICENSES.zip" in output
    assert "channelwatch-v0.9.17-SHA256SUMS.txt" in output
    assert "Exact amd64 and arm64 SPDX and CycloneDX SBOMs" in output
    assert "every other attached asset is covered" in output
    assert "`coderluii/channelwatch:0.9`" in output
    assert "`ghcr.io/coderluii/channelwatch:0.9`" in output


def test_release_workflow_uses_explicit_config_and_python_gate():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts/release/release-config.json" in workflow
    assert 'grep -Eiq "container image update required|image-required"' not in workflow
    assert "python -m pytest app/core/tests" in workflow
    assert "python -m compileall app/core app/ui/backend" in workflow
    assert 'expected_heading="$(jq -r \'.release_heading\' scripts/release/release-config.json)"' in workflow
    assert "GitHub Release body must start with '${expected_heading}'" in workflow
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:",
        1,
    )[0]
    assert "fetch-depth: 0" in release_job
    assert '.draft and .tag_name == \\"${TAG}\\"' in release_job
    assert "is already published and is immutable" in release_job
    create_draft_step = release_job.split(
        "      - name: Create or update draft GitHub Release", 1
    )[1].split("\n      - name: Upload update assets", 1)[0]
    assert 'release_json=""' in create_draft_step
    assert 'if ! release_json="$(gh api ' in create_draft_step
    assert "JSON 404 body" in create_draft_step
    assert "2>/dev/null || true" not in create_draft_step
    assert "--arg tag_name \"${TAG}\"" in release_job
    assert "--arg target_commitish \"${GITHUB_SHA}\"" in release_job


@pytest.mark.parametrize(
    "step_name",
    (
        "Download and verify existing draft release assets",
        "Attach verified license and SBOM assets to draft release",
        "Publish release after image verification gates",
    ),
)
def test_release_workflow_resolves_drafts_from_authenticated_release_list(
    step_name,
):
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    _block, shell = _workflow_shell_step(workflow, step_name)

    # GitHub's release-by-tag endpoint returns 404 for an unpublished draft,
    # even though that draft is present in the authenticated release list.
    assert 'releases/tags/${TAG}' not in shell
    assert 'releases?per_page=100' in shell
    assert '.draft == true and .tag_name == $tag' in shell
    assert 'Exactly one draft release for ${TAG} is required.' in shell or (
        'missing or already published; published releases are immutable.' in shell
    )


def test_release_workflow_passes_version_between_isolated_action_shells(tmp_path):
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    _parse_block, parse_shell = _workflow_shell_step(workflow, "Parse version")
    verify_block, verify_shell = _workflow_shell_step(
        workflow, "Verify CHANGELOG entry"
    )
    output_path = tmp_path / "github-output"
    parse_env = {
        **os.environ,
        "GITHUB_REF_NAME": "v0.9.17",
        "GITHUB_OUTPUT": str(output_path),
    }

    parsed = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", parse_shell],
        cwd=ROOT,
        env=parse_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert parsed.returncode == 0, parsed.stderr
    output_values = dict(
        line.split("=", 1)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    )
    assert output_values == {"version": "0.9.17"}
    assert "VERSION: ${{ steps.version.outputs.version }}" in verify_block
    assert 'version_re="${VERSION//' in verify_shell
    assert 'version_re="${version//' not in verify_shell

    verified = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", verify_shell],
        cwd=ROOT,
        env={
            **os.environ,
            "GITHUB_REF_NAME": "v0.9.17",
            "VERSION": output_values["version"],
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert verified.returncode == 0, verified.stderr


def test_release_impact_classifier_keeps_app_only_changes_bundle_compatible():
    module = _load_script(
        "classify_release_impact_app_only",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(
        [
            "app/core/main.py",
            "app/ui/components/report-problem-dialog.tsx",
            "app/ui/backend/support_report.py",
            "app/core/tests/test_support_report.py",
            "docs/releases/CHANGELOG.md",
        ]
    )

    assert result.image_required is False
    assert result.triggering_paths == ()


def test_release_impact_classifier_requires_image_for_runtime_surfaces():
    module = _load_script(
        "classify_release_impact_runtime",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(
        [
            "app/bin/channelwatch",
            "app/core/helpers/atomic_io.py",
            "app/core/helpers/migration.py",
            "deploy/requirements/runtime.txt",
            "deploy/docker/Dockerfile",
            "deploy/docker/Dockerfile.dockerignore",
            "app/ui/pnpm-lock.yaml",
            "app/core/update_center.py",
            "app/ui/components/report-problem-dialog.tsx",
        ]
    )

    assert result.image_required is True
    assert result.triggering_paths == (
        "app/bin/channelwatch",
        "app/core/helpers/atomic_io.py",
        "app/core/helpers/migration.py",
        "app/core/update_center.py",
        "app/ui/pnpm-lock.yaml",
        "deploy/docker/Dockerfile.dockerignore",
        "deploy/requirements/runtime.txt",
    )


def test_release_impact_classifier_requires_image_for_supervisor_template():
    module = _load_script(
        "classify_release_impact_supervisor_template",
        "scripts/release/classify-release-impact.py",
    )
    supervisor_template = (
        ROOT / "deploy/config/supervisor/supervisord.conf.template"
    )
    assert supervisor_template.is_file()
    supervisor_path = supervisor_template.relative_to(ROOT).as_posix()

    result = module.classify_paths([supervisor_path])

    assert result.image_required is True
    assert result.triggering_paths == (supervisor_path,)


def test_release_impact_classifier_rejects_declared_mismatch():
    module = _load_script(
        "classify_release_impact_mismatch",
        "scripts/release/classify-release-impact.py",
    )

    result = module.classify_paths(["app/ui/lib/api.ts"])

    try:
        module.verify_declared_impact(result, declared_image_required=True)
    except module.ReleaseImpactMismatch as exc:
        assert "declares image_required=true" in str(exc)
        assert "classifier requires false" in str(exc)
    else:
        raise AssertionError("release impact mismatch was accepted")


def test_release_impact_classifier_ignores_version_only_package_and_helm_changes():
    module = _load_script(
        "classify_release_impact_version_only",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/ui/package.json": '{"version":"0.9.14","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/Chart.yaml": "version: 0.9.14\nappVersion: 0.9.14\nname: channelwatch\n",
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.14\n  pullPolicy: IfNotPresent\n",
    }
    after = {
        "app/ui/package.json": '{"version":"0.9.15","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/Chart.yaml": "version: 0.9.15\nappVersion: 0.9.15\nname: channelwatch\n",
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.15\n  pullPolicy: IfNotPresent\n",
    }

    result = module.classify_changes(before, after)

    assert result.image_required is False


def test_release_impact_classifier_detects_dependency_and_helm_runtime_changes():
    module = _load_script(
        "classify_release_impact_structured_runtime",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/ui/package.json": '{"version":"0.9.14","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.14\n  pullPolicy: IfNotPresent\n",
    }
    after = {
        "app/ui/package.json": '{"version":"0.9.15","dependencies":{"next":"16.4.0"}}',
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.15\n  pullPolicy: Always\n",
    }

    result = module.classify_changes(before, after)

    assert result.image_required is True
    assert result.triggering_paths == (
        "app/ui/package.json",
        "deploy/helm/channelwatch/values.yaml",
    )


def test_release_impact_classifier_ignores_only_docker_version_default():
    module = _load_script("classify_docker_version", "scripts/release/classify-release-impact.py")
    before = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.14\nLABEL version=$VERSION\n"}
    after = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.15\nLABEL version=$VERSION\n"}
    assert module.classify_changes(before, after).image_required is False


def test_release_impact_classifier_detects_other_dockerfile_changes():
    module = _load_script("classify_docker_runtime", "scripts/release/classify-release-impact.py")
    before = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.14\n"}
    after = {"deploy/docker/Dockerfile": "FROM busybox\nARG VERSION=0.9.15\n"}
    result = module.classify_changes(before, after)
    assert result.image_required is True
    assert result.triggering_paths == ("deploy/docker/Dockerfile",)


def test_release_workflow_gates_declared_impact_and_live_manifest():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "classify-release-impact.py" in workflow
    assert 'version="${GITHUB_REF_NAME#v}"' in workflow
    impact_job = workflow.split("  build-and-push:", 1)[1].split("\n  sync-site:", 1)[0]
    assert "fetch-depth: 0" in impact_job
    assert "Verify live stable update manifest" in workflow
    assert "verify-live-update-manifest.py" in workflow
    assert "- sync-site" in workflow
    live_job = workflow.split("  verify-live-update-manifest:", 1)[1]
    assert '--attempts "90"' in live_job
    assert '--interval "10"' in live_job


def test_release_workflow_serializes_publication_and_preserves_immutability():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "group: channelwatch-release-publication" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "\npermissions: {}\n" in workflow
    assert "verify-release-candidate.py" in workflow
    assert '--sha "${GITHUB_SHA}"' in workflow
    assert "--main-ref origin/main" in workflow
    assert "is already published and is immutable" in workflow
    assert '.draft and .tag_name == \\"${TAG}\\"' in workflow
    assert '.tag_name == \\"${TAG}\\" or' not in workflow
    assert '--target "${GITHUB_SHA}"' in workflow
    assert "actions/setup-python@e797f83bcb11b83ae66e0230d6156d7c80228e7c" in workflow
    assert "python-version: '3.12'" in workflow
    assert "actions/setup-node@395ad3262231945c25e8478fd5baf05154b1d79f" in workflow
    assert "node-version: '24'" in workflow
    assert "package-manager-cache: false" in workflow

    publish_job = workflow.split("  publish-github-release:", 1)[1].split(
        "\n  sync-site:", 1
    )[0]
    assert "missing or already published; published releases are immutable" in publish_job
    assert ".draft == true" in publish_job
    assert "'.target_commitish'" in publish_job


def test_release_workflow_publishes_only_the_scanned_multiarch_archive():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )
    image_job = workflow.split("  build-and-push:", 1)[1].split(
        "\n  update-dockerhub-description:", 1
    )[0]

    build_index = image_job.index(
        "      - name: Build exact multi-architecture archive for release scan"
    )
    scan_index = image_job.index("      - name: Scan exact release candidate images")
    docker_login_index = image_job.index("      - name: Login to Docker Hub")
    ghcr_login_index = image_job.index("      - name: Login to GHCR")
    publish_index = image_job.index(
        "      - name: Publish the scanned images and assemble exact manifests"
    )

    assert build_index < scan_index < docker_login_index
    assert scan_index < ghcr_login_index < publish_index
    assert "platforms: ${{ env.DOCKER_PLATFORMS }}" in image_job
    assert image_job.count("push: false") == 1
    assert "outputs: type=oci,dest=${{ runner.temp }}/channelwatch-release.oci,tar=false" in image_job
    assert image_job.count("provenance: false") == 1
    assert "channelwatch:release-scan" in image_job
    assert "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514" in image_job
    assert "--scanners vuln,secret,misconfig" in image_job
    assert "--severity CRITICAL,HIGH" in image_job
    assert "--exit-code 1" in image_job
    assert '--input "${OCI_LAYOUT}"' in image_job
    assert '--platform "${platform}"' in image_job

    syft_index = image_job.index("      - name: Install pinned Syft")
    download_assets_index = image_job.index(
        "      - name: Download and verify existing draft release assets"
    )
    license_assets_index = image_job.index(
        "      - name: Generate and verify exact-image license assets"
    )
    attach_assets_index = image_job.index(
        "      - name: Attach verified license and SBOM assets to draft release"
    )
    assert (
        scan_index
        < syft_index
        < download_assets_index
        < license_assets_index
        < attach_assets_index
    )
    assert attach_assets_index < docker_login_index
    assert (
        "anchore/sbom-action/download-syft@"
        "e22c389904149dbc22b58101806040fa8d37a610"
    ) in image_job
    assert "syft-version: v1.51.0" in image_job
    assert '"oci-dir:${OCI_LAYOUT}"' in image_job
    assert '"spdx-json=${spdx}"' in image_job
    assert '"cyclonedx-json=${cyclonedx}"' in image_job
    assert 'any(.packages[]; .name == "zeroconf")' in image_job
    assert 'any(.components[]; .name == "zeroconf")' in image_job
    assert "channelwatch-${TAG}-LICENSE.txt" in image_job
    assert "channelwatch-${TAG}-NOTICE.txt" in image_job
    assert "channelwatch-${TAG}-THIRD-PARTY-LICENSES.md" in image_job
    assert "channelwatch-${TAG}-CORRESPONDING-SOURCE.md" in image_job
    assert "channelwatch-${TAG}-COPYLEFT-LICENSES.zip" in image_job
    assert "channelwatch-${TAG}-SHA256SUMS.txt" in image_job
    assert "scripts/release/copyleft_licenses.py" in image_job
    assert "Release checksum manifest must cover all 12 non-checksum assets" in image_job
    for existing_asset in (
        "channelwatch-app-${TAG}.zip",
        "channelwatch-update-${TAG}.json",
        "channelwatch-${TAG}.openvex.json",
    ):
        assert existing_asset in image_job
    assert "Signed assets must come from the matching exact-SHA draft release" in image_job
    assert 'gh release download "${TAG}"' in image_job
    assert 'gh release upload "${TAG}" dist/release/*' in image_job
    assert "Draft release contains an unexpected or missing asset" in image_job
    assert "find dist/release -maxdepth 1 -type f -exec basename" in image_job
    assert "--jq '.assets[].name'" in image_job
    assert "License assets may only be attached to the matching draft release" in image_job
    assert "Draft release target does not match ${GITHUB_SHA}" in image_job

    publish_job = image_job[publish_index:]
    assert "quay.io/skopeo/stable@sha256:64ac45c5a1c01230896fbae960b2213e32a5040e4009b83b5f5cbf31a35f61c3" in publish_job
    assert 'root_index="$(cat "${OCI_LAYOUT}/index.json")"' in publish_job
    assert 'image_index_digest="$(jq -er' in publish_job
    assert 'image_index_blob="${OCI_LAYOUT}/blobs/sha256/${image_index_digest#sha256:}"' in publish_job
    assert "OCI layout does not reference exactly one nested image index" in publish_job
    assert "Scanned OCI layout is missing its nested image-index blob" in publish_job
    assert "oci:/work/channelwatch.oci:release-scan" in publish_job
    assert publish_job.count("--preserve-digests") == 2
    assert "Published manifest does not contain the exact scanned platform descriptors" in publish_job
    assert '"docker://${repository}@${version_digest}"' in publish_job
    assert 'compatible_tag="${VERSION%.*}"' in publish_job
    assert 'publish_alias "${compatible_tag}"' in publish_job
    assert "publish_alias latest" in publish_job
    assert "${alias_tag} does not reference the exact version manifest" in publish_job
    assert '"${VERSION}-amd64"' not in publish_job
    assert '"${VERSION}-arm64"' not in publish_job
    assert "steps.publish.outputs.dockerhub_digest" in image_job
    assert "steps.build.outputs.digest" not in image_job
    assert image_job.count("docker/build-push-action@") == 1
    assert "\n          push: true\n" not in image_job
    assert image_job.count(
        "DOCKER_CONFIG: ${{ runner.temp }}/docker-auth/.docker"
    ) == 4
    assert '--volume "${DOCKER_CONFIG}:/auth:ro"' in publish_job

    attest_job = image_job.split("      - name: Attest build provenance", 1)[1]
    attest_uses_index = attest_job.index("uses: actions/attest-build-provenance@")
    assert attest_job.index(
        "DOCKER_CONFIG: ${{ runner.temp }}/docker-auth/.docker"
    ) < attest_uses_index
    assert attest_job.index("HOME: ${{ runner.temp }}/docker-auth") < attest_uses_index


def test_release_workflow_verifies_generated_and_live_update_assets():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    assert "verify-update-assets.py" in release_job
    assert '--expected-channel "${EXPECTED_CHANNEL}"' in release_job
    assert '--expected-runtime-abi "${EXPECTED_RUNTIME_ABI}"' in release_job
    assert (
        '--expected-settings-schema-version "${EXPECTED_SETTINGS_SCHEMA_VERSION}"'
        in release_job
    )
    assert '--expected-image-required "${expected_image_required}"' in release_job
    assert '--expected-git-sha "${EXPECTED_GIT_SHA}"' in release_job
    assert '--expected-release-url "https://github.com/' in release_job
    assert '--expected-bundle-url "https://github.com/' in release_job
    assert '--source-root "${RELEASE_SOURCE_ROOT}"' in release_job
    assert "EXPECTED_GIT_SHA: ${{ github.sha }}" in release_job
    assert "EXPECTED_CHANNEL: stable" in release_job
    assert "EXPECTED_RUNTIME_ABI: channelwatch-runtime-v1" in release_job
    assert 'EXPECTED_SETTINGS_SCHEMA_VERSION: "7"' in release_job

    live_script = (ROOT / "scripts/release/verify-live-update-manifest.py").read_text(
        encoding="utf-8"
    )
    assert "verify_update_assets" in live_script
    assert "bundle_url" in live_script
    assert "verifier.fetch_bytes" in live_script


def test_release_workflow_verifies_and_publishes_versioned_openvex():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    verify_index = release_job.index("      - name: Verify release VEX")
    draft_index = release_job.index("      - name: Render and validate release body")
    upload_index = release_job.index("      - name: Upload update assets")

    assert verify_index < draft_index < upload_index
    assert 'deploy/security/channelwatch-${TAG}.openvex.json' in release_job
    assert "python scripts/release/verify-vex.py" in release_job
    assert '"${vex}"' in release_job


def test_ci_trivy_scan_renders_helm_and_fails_if_chart_targets_are_skipped():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    security_job = workflow.split("  security:", 1)[1]

    assert "aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514" in security_job
    assert "--ignorefile .trivyignore.yaml" in security_job
    assert "--skip-files deploy/docker/Dockerfile.dockerignore" in security_job
    assert "--helm-set-string secretConfig.secretStorageKey=ci-only-" in security_job
    assert "channelwatch-trivy.json" in security_job
    assert "CHANNELWATCH_TRIVY_REPORT" in security_job
    assert "TRIVY_REPORT:" not in security_job.replace(
        "CHANNELWATCH_TRIVY_REPORT:", ""
    )
    assert 'if result.get("Type") == "helm"' in security_job
    assert "deploy/helm/channelwatch/templates/deployment.yaml" in security_job
    assert "deploy/helm/channelwatch/templates/secret.yaml" in security_job
    assert "Trivy did not scan required Helm targets" in security_job


def test_ci_python_cache_tracks_repository_requirement_files():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    python_job = workflow.split("  python:", 1)[1].split("\n  frontend:", 1)[0]

    assert "fetch-depth: 0" in python_job
    assert "cache: pip" in python_job
    assert "cache-dependency-path:" in python_job
    assert "deploy/requirements/runtime.txt" in python_job
    assert "deploy/requirements/dev.txt" in python_job
    assert "deploy/requirements/dev.constraints.txt" in python_job


def test_trivy_root_entrypoint_exception_is_narrow_and_expires():
    ignore = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text(encoding="utf-8"))

    assert set(ignore) == {"misconfigurations"}
    assert ignore["misconfigurations"] == [
        {
            "id": "AVD-DS-0002",
            "paths": ["deploy/docker/Dockerfile"],
            "expired_at": date(2027, 2, 1),
            "statement": (
                "The entrypoint must start as root only to repair configurable "
                "/config ownership, then it clears supplemental groups and verifies "
                "the requested effective UID and GID before starting ChannelWatch. "
                "Container integration tests require the final process identity to "
                "be non-root and the root filesystem to be read-only."
            ),
        }
    ]


def test_release_workflow_does_not_interpolate_step_outputs_in_shell():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert 'version="${{ steps.version.outputs.version }}"' not in workflow
    live_step = workflow.split(
        "      - name: Verify live stable update manifest", 1
    )[1]
    assert '--version "${{ steps.release.outputs.version }}"' not in live_step
    assert "RAW_RELEASE_VERSION: ${{ steps.release.outputs.version }}" in live_step


def test_release_candidate_rejects_stale_and_divergent_tags():
    module = _load_script(
        "verify_release_candidate",
        "scripts/release/verify-release-candidate.py",
    )

    assert module.validate_candidate("v0.9.17", ["v0.9.15", "v0.9.17"]) == "v0.9.17"
    try:
        module.validate_candidate("v0.9.15", ["v0.9.15", "v0.9.17"])
    except ValueError as exc:
        assert "newer release tag v0.9.17 already exists" in str(exc)
    else:
        raise AssertionError("stale release candidate was accepted")

    try:
        module.validate_candidate(
            "v0.9.17",
            ["v0.9.15", "v0.9.17"],
            candidate_sha="divergent",
            main_sha="current-main",
        )
    except ValueError as exc:
        assert "is not current main" in str(exc)
    else:
        raise AssertionError("divergent release candidate was accepted")


def _signed_update_assets(
    version: str = "0.9.17",
    *,
    image_required: bool = False,
    git_sha: str | None = TEST_RELEASE_GIT_SHA,
    bundle_type: str | None = "channelwatch-app",
    member_contents: dict[str, bytes] | None = None,
    metadata_overrides: dict[str, object] | None = None,
    payload_overrides: dict[str, object] | None = None,
):
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    bundle_io = io.BytesIO()
    metadata = {
        "version": version,
        "version_tag": f"v{version}",
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "created_at": "2026-08-22T00:00:00Z",
    }
    if git_sha is not None:
        metadata["git_sha"] = git_sha
    if bundle_type is not None:
        metadata["bundle_type"] = bundle_type
    metadata.update(metadata_overrides or {})
    members = {
        "core/main.py": b"",
        "ui/backend/main.py": b"",
        **(member_contents or {}),
    }
    with zipfile.ZipFile(bundle_io, "w") as bundle:
        bundle.writestr(
            "channelwatch-bundle.json",
            json.dumps(metadata),
        )
        for member, content in members.items():
            bundle.writestr(member, content)
    bundle_bytes = bundle_io.getvalue()
    digest = hashlib.sha256(bundle_bytes).digest()
    key_id = "test-key"
    payload = {
        "version": version,
        "version_tag": f"v{version}",
        "channel": "stable",
        "runtime_abi": "channelwatch-runtime-v1",
        "settings_schema_version": 7,
        "image_required": image_required,
        "release_url": (
            f"https://github.com/CoderLuii/ChannelWatch/releases/tag/v{version}"
        ),
        "bundle_url": (
            "https://github.com/CoderLuii/ChannelWatch/releases/download/"
            f"v{version}/channelwatch-app-v{version}.zip"
        ),
        "bundle_sha256": digest.hex(),
        "bundle_signature": base64.b64encode(private.sign(digest)).decode("ascii"),
        "key_id": key_id,
        "highlights": [],
    }
    payload.update(payload_overrides or {})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    manifest = {
        "schema": 1,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": key_id,
            "value": base64.b64encode(private.sign(canonical)).decode("ascii"),
        },
    }
    return json.dumps(manifest).encode(), bundle_bytes, {key_id: public}


def test_update_asset_verifier_uses_runtime_signature_and_archive_validation():
    module = _load_script(
        "verify_update_assets",
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets()

    verified = module.verify_update_assets(
        manifest_bytes,
        bundle_bytes,
        public_keys=public_keys,
        expected_version="0.9.17",
        expected_image_required=False,
        expected_git_sha=TEST_RELEASE_GIT_SHA,
        expected_release_url=TEST_RELEASE_URL,
        expected_bundle_url=TEST_BUNDLE_URL,
    )

    assert verified["payload"]["version"] == "0.9.17"


def test_update_asset_verifier_binds_image_requirement_and_exact_git_sha():
    module = _load_script(
        "verify_update_assets_release_contract",
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets()

    with pytest.raises(Exception, match="image_required does not match"):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=True,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    missing_image_manifest, missing_image_bundle, missing_image_keys = (
        _signed_update_assets(payload_overrides={"image_required": None})
    )
    with pytest.raises(Exception, match="image_required must be an explicit boolean"):
        module.verify_update_assets(
            missing_image_manifest,
            missing_image_bundle,
            public_keys=missing_image_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    with pytest.raises(Exception, match="git_sha does not match"):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha="f" * 40,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    missing_sha_manifest, missing_sha_bundle, missing_sha_keys = (
        _signed_update_assets(git_sha=None)
    )
    with pytest.raises(Exception, match="git_sha does not match"):
        module.verify_update_assets(
            missing_sha_manifest,
            missing_sha_bundle,
            public_keys=missing_sha_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    missing_type_manifest, missing_type_bundle, missing_type_keys = (
        _signed_update_assets(bundle_type=None)
    )
    with pytest.raises(Exception, match="bundle_type does not match"):
        module.verify_update_assets(
            missing_type_manifest,
            missing_type_bundle,
            public_keys=missing_type_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    wrong_type_manifest, wrong_type_bundle, wrong_type_keys = _signed_update_assets(
        bundle_type="not-channelwatch"
    )
    with pytest.raises(Exception, match="bundle_type does not match"):
        module.verify_update_assets(
            wrong_type_manifest,
            wrong_type_bundle,
            public_keys=wrong_type_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )

    wrong_metadata_tag_manifest, wrong_metadata_tag_bundle, wrong_metadata_tag_keys = (
        _signed_update_assets(metadata_overrides={"version_tag": "v0.9.99"})
    )
    with pytest.raises(Exception, match="metadata version_tag does not match"):
        module.verify_update_assets(
            wrong_metadata_tag_manifest,
            wrong_metadata_tag_bundle,
            public_keys=wrong_metadata_tag_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


@pytest.mark.parametrize(
    "metadata_overrides,payload_overrides,error_fragment",
    [
        ({}, {"channel": None}, "channel must be a string"),
        ({}, {"channel": "beta"}, "channel does not match"),
        (
            {"runtime_abi": None},
            {"runtime_abi": None},
            "runtime_abi must be a string",
        ),
        (
            {"runtime_abi": "channelwatch-runtime-v999"},
            {"runtime_abi": "channelwatch-runtime-v999"},
            "runtime_abi does not match",
        ),
        (
            {"settings_schema_version": None},
            {"settings_schema_version": None},
            "settings_schema_version must be an integer",
        ),
        (
            {"settings_schema_version": 999},
            {"settings_schema_version": 999},
            "settings_schema_version does not match",
        ),
        (
            {"settings_schema_version": "7"},
            {"settings_schema_version": "7"},
            "settings_schema_version must be an integer",
        ),
    ],
)
def test_update_asset_verifier_binds_canonical_runtime_contract(
    metadata_overrides, payload_overrides, error_fragment
):
    module = _load_script(
        "verify_update_assets_runtime_contract_" + error_fragment.split()[0],
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        metadata_overrides=metadata_overrides,
        payload_overrides=payload_overrides,
    )

    with pytest.raises(Exception, match=error_fragment):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_channel="stable",
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


@pytest.mark.parametrize(
    "metadata_overrides,error_fragment",
    [
        ({"runtime_abi": None}, "runtime ABI is not compatible"),
        ({"runtime_abi": 1}, "runtime ABI is not compatible"),
        (
            {"settings_schema_version": None},
            "schema version does not match",
        ),
        (
            {"settings_schema_version": "7"},
            "metadata settings_schema_version must be an integer",
        ),
    ],
)
def test_update_asset_verifier_rejects_wrong_bundle_metadata_types(
    metadata_overrides, error_fragment
):
    module = _load_script(
        "verify_update_assets_bundle_metadata_types_" + error_fragment.split()[0],
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        metadata_overrides=metadata_overrides,
    )

    with pytest.raises(Exception, match=error_fragment):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_channel="stable",
            expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7,
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


@pytest.mark.parametrize(
    "payload_overrides,error_fragment",
    [
        ({"version_tag": None}, "version_tag does not match"),
        ({"version_tag": "v0.9.99"}, "version_tag does not match"),
        ({"release_url": None}, "release_url does not match"),
        (
            {"release_url": "https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.15"},
            "release_url does not match",
        ),
        ({"bundle_url": None}, "bundle_url does not match"),
        (
            {
                "bundle_url": (
                    "https://github.com/CoderLuii/ChannelWatch/releases/download/"
                    "v0.9.15/channelwatch-app-v0.9.15.zip"
                )
            },
            "bundle_url does not match",
        ),
    ],
)
def test_update_asset_verifier_rejects_wrong_tag_and_urls(
    payload_overrides, error_fragment
):
    module = _load_script(
        "verify_update_assets_manifest_contract_" + error_fragment.split()[0],
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        payload_overrides=payload_overrides
    )

    with pytest.raises(Exception, match=error_fragment):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=TEST_RELEASE_GIT_SHA,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
        )


def test_update_asset_verifier_matches_critical_members_to_exact_source(tmp_path):
    module = _load_script(
        "verify_update_assets_exact_source",
        "scripts/release/verify-update-assets.py",
    )
    member_contents = {
        member: f"exact bytes for {member}\n".encode()
        for member in module.CRITICAL_SOURCE_MEMBERS
    }
    for member, relative_source in module.CRITICAL_SOURCE_MEMBERS.items():
        source_path = tmp_path / relative_source
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(member_contents[member])
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "review@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Review"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "add", "app"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "exact source"], cwd=tmp_path, check=True
    )
    exact_git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets(
        member_contents=member_contents,
        git_sha=exact_git_sha,
    )

    module.verify_update_assets(
        manifest_bytes,
        bundle_bytes,
        public_keys=public_keys,
        expected_version="0.9.17",
        expected_channel="stable",
        expected_runtime_abi="channelwatch-runtime-v1",
        expected_settings_schema_version=7,
        expected_image_required=False,
        expected_git_sha=exact_git_sha,
        expected_release_url=TEST_RELEASE_URL,
        expected_bundle_url=TEST_BUNDLE_URL,
        expected_source_root=tmp_path,
    )

    for metadata_overrides, payload_overrides, error_fragment in (
        (
            {"runtime_abi": "channelwatch-runtime-v999"},
            {"runtime_abi": "channelwatch-runtime-v999"},
            "runtime_abi does not match",
        ),
        (
            {"settings_schema_version": 999},
            {"settings_schema_version": 999},
            "settings_schema_version does not match",
        ),
    ):
        wrong_manifest, wrong_bundle, wrong_keys = _signed_update_assets(
            member_contents=member_contents,
            git_sha=exact_git_sha,
            metadata_overrides=metadata_overrides,
            payload_overrides=payload_overrides,
        )
        with pytest.raises(Exception, match=error_fragment):
            module.verify_update_assets(
                wrong_manifest,
                wrong_bundle,
                public_keys=wrong_keys,
                expected_version="0.9.17",
                expected_channel="stable",
                expected_runtime_abi="channelwatch-runtime-v1",
                expected_settings_schema_version=7,
                expected_image_required=False,
                expected_git_sha=exact_git_sha,
                expected_release_url=TEST_RELEASE_URL,
                expected_bundle_url=TEST_BUNDLE_URL,
                expected_source_root=tmp_path,
            )

    changed_source = tmp_path / module.CRITICAL_SOURCE_MEMBERS["core/update_center.py"]
    changed_source.write_bytes(b"changed after packaging\n")
    with pytest.raises(Exception, match="does not match exact Git SHA"):
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys=public_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=exact_git_sha,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
            expected_source_root=tmp_path,
        )

    changed_source.write_bytes(member_contents["core/update_center.py"])
    mismatched_members = {
        **member_contents,
        "core/update_center.py": b"bundle bytes from a different source\n",
    }
    mismatched_manifest, mismatched_bundle, mismatched_keys = _signed_update_assets(
        member_contents=mismatched_members,
        git_sha=exact_git_sha,
    )
    with pytest.raises(Exception, match="does not match exact release source"):
        module.verify_update_assets(
            mismatched_manifest,
            mismatched_bundle,
            public_keys=mismatched_keys,
            expected_version="0.9.17",
            expected_image_required=False,
            expected_git_sha=exact_git_sha,
            expected_release_url=TEST_RELEASE_URL,
            expected_bundle_url=TEST_BUNDLE_URL,
            expected_source_root=tmp_path,
        )


def test_update_asset_verifier_rejects_tampered_bundle_and_empty_trust_store():
    module = _load_script(
        "verify_update_assets_tamper",
        "scripts/release/verify-update-assets.py",
    )
    manifest_bytes, bundle_bytes, public_keys = _signed_update_assets()

    try:
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes + b"tampered",
            public_keys=public_keys,
            expected_version="0.9.17",
        )
    except Exception as exc:
        assert "hash did not match" in str(exc)
    else:
        raise AssertionError("tampered update bundle was accepted")

    try:
        module.verify_update_assets(
            manifest_bytes,
            bundle_bytes,
            public_keys={},
            expected_version="0.9.17",
        )
    except Exception as exc:
        assert "Unknown update signing key" in str(exc)
    else:
        raise AssertionError("an empty trust store unexpectedly used production keys")
