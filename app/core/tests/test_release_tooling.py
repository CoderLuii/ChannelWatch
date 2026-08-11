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


def test_release_config_marks_0912_as_image_required():
    config = json.loads(
        (ROOT / "scripts/release/release-config.json").read_text(encoding="utf-8")
    )

    assert config == {
        "version": "0.9.12",
        "image_required": True,
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

    assert metadata["version"] == "0.9.12"
    assert metadata["versionTag"] == "v0.9.12"
    assert metadata["dockerTag"] == "0.9.12"
    assert metadata["helmChartVersion"] == "0.9.12"
    assert metadata["helmAppVersion"] == "0.9.12"


def test_release_workflow_uses_explicit_config_and_python_gate():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts/release/release-config.json" in workflow
    assert 'grep -Eiq "container image update required|image-required"' not in workflow
    assert "python -m pytest app/core/tests" in workflow
    assert "python -m compileall app/core app/ui/backend" in workflow
    assert "GitHub Release body must start with '${expected_heading}'" in workflow
    release_job = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:",
        1,
    )[0]
    assert "fetch-depth: 0" in release_job
    assert '.draft and .name == \\"${TAG}\\"' in release_job
    assert "--arg tag_name \"${TAG}\"" in release_job
    assert "--arg target_commitish \"${GITHUB_SHA}\"" in release_job
