import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_release_config_marks_0914_as_bundle_compatible():
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )

    assert config == {
        "version": "0.9.14",
        "image_required": False,
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

    assert metadata["version"] == "0.9.14"
    assert metadata["versionTag"] == "v0.9.14"
    assert metadata["dockerTag"] == "0.9.14"
    assert metadata["helmChartVersion"] == "0.9.14"
    assert metadata["helmAppVersion"] == "0.9.14"


def test_release_workflow_uses_explicit_config_and_python_gate():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts/release/release-config.json" in workflow
    assert 'grep -Eiq "container image update required|image-required"' not in workflow
    assert "python -m pytest app/core/tests" in workflow
    assert "python -m compileall app/core app/ui/backend" in workflow
    assert 'expected_heading="# ChannelWatch ${TAG} - Reporting and update reliability"' in workflow
    assert "GitHub Release body must start with '${expected_heading}'" in workflow
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:",
        1,
    )[0]
    assert "fetch-depth: 0" in release_job
    assert '.draft and .name == \\"${TAG}\\"' in release_job
    assert "--arg tag_name \"${TAG}\"" in release_job
    assert "--arg target_commitish \"${GITHUB_SHA}\"" in release_job


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
            "deploy/requirements/runtime.txt",
            "deploy/docker/Dockerfile",
            "app/ui/pnpm-lock.yaml",
            "app/core/update_center.py",
            "app/ui/components/report-problem-dialog.tsx",
        ]
    )

    assert result.image_required is True
    assert result.triggering_paths == (
        "app/core/update_center.py",
        "app/ui/pnpm-lock.yaml",
        "deploy/requirements/runtime.txt",
    )


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
        "app/ui/package.json": '{"version":"0.9.13","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/Chart.yaml": "version: 0.9.13\nappVersion: 0.9.13\nname: channelwatch\n",
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.13\n  pullPolicy: IfNotPresent\n",
    }
    after = {
        "app/ui/package.json": '{"version":"0.9.14","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/Chart.yaml": "version: 0.9.14\nappVersion: 0.9.14\nname: channelwatch\n",
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.14\n  pullPolicy: IfNotPresent\n",
    }

    result = module.classify_changes(before, after)

    assert result.image_required is False


def test_release_impact_classifier_detects_dependency_and_helm_runtime_changes():
    module = _load_script(
        "classify_release_impact_structured_runtime",
        "scripts/release/classify-release-impact.py",
    )
    before = {
        "app/ui/package.json": '{"version":"0.9.13","dependencies":{"next":"16.3.0"}}',
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.13\n  pullPolicy: IfNotPresent\n",
    }
    after = {
        "app/ui/package.json": '{"version":"0.9.14","dependencies":{"next":"16.4.0"}}',
        "deploy/helm/channelwatch/values.yaml": "image:\n  tag: 0.9.14\n  pullPolicy: Always\n",
    }

    result = module.classify_changes(before, after)

    assert result.image_required is True
    assert result.triggering_paths == (
        "app/ui/package.json",
        "deploy/helm/channelwatch/values.yaml",
    )


def test_release_impact_classifier_ignores_only_docker_version_default():
    module = _load_script("classify_docker_version", "scripts/release/classify-release-impact.py")
    before = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.13\nLABEL version=$VERSION\n"}
    after = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.14\nLABEL version=$VERSION\n"}
    assert module.classify_changes(before, after).image_required is False


def test_release_impact_classifier_detects_other_dockerfile_changes():
    module = _load_script("classify_docker_runtime", "scripts/release/classify-release-impact.py")
    before = {"deploy/docker/Dockerfile": "FROM scratch\nARG VERSION=0.9.13\n"}
    after = {"deploy/docker/Dockerfile": "FROM busybox\nARG VERSION=0.9.14\n"}
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
