import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "channelwatch"
UNRAID_TEMPLATE = REPO_ROOT / "deploy" / "unraid" / "channelwatch.xml"


def _helm_template(*values: str) -> subprocess.CompletedProcess[str]:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is not installed")
    command = [helm, "template", "channelwatch", str(CHART_DIR)]
    for value in values:
        flag = "--set" if value.startswith("persistence.enabled=") else "--set-string"
        command.extend([flag, value])
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _rendered_resources(rendered: str) -> dict[str, dict]:
    return {
        resource["kind"]: resource
        for resource in yaml.safe_load_all(rendered)
        if resource
    }


def test_helm_uses_recreate_for_the_single_config_writer():
    rendered = _helm_template()
    assert rendered.returncode == 0, rendered.stderr

    deployment = _rendered_resources(rendered.stdout)["Deployment"]
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}


def test_helm_config_changes_replace_the_pod_template():
    original = _helm_template()
    changed = _helm_template("config.tz=UTC")
    assert original.returncode == changed.returncode == 0

    original_annotations = _rendered_resources(original.stdout)["Deployment"][
        "spec"
    ]["template"]["metadata"]["annotations"]
    changed_annotations = _rendered_resources(changed.stdout)["Deployment"]["spec"][
        "template"
    ]["metadata"]["annotations"]
    assert original_annotations["checksum/channelwatch-config"]
    assert (
        changed_annotations["checksum/channelwatch-config"]
        != original_annotations["checksum/channelwatch-config"]
    )


def test_helm_managed_secret_changes_replace_the_pod_template():
    first = _helm_template("secretConfig.apiKey=first-ci-only-placeholder")
    second = _helm_template("secretConfig.apiKey=second-ci-only-placeholder")
    assert first.returncode == second.returncode == 0

    first_annotations = _rendered_resources(first.stdout)["Deployment"]["spec"][
        "template"
    ]["metadata"]["annotations"]
    second_annotations = _rendered_resources(second.stdout)["Deployment"]["spec"][
        "template"
    ]["metadata"]["annotations"]
    assert first_annotations["checksum/channelwatch-managed-secret"]
    assert (
        second_annotations["checksum/channelwatch-managed-secret"]
        != first_annotations["checksum/channelwatch-managed-secret"]
    )


def test_helm_existing_secret_does_not_claim_to_hash_external_contents():
    rendered = _helm_template("secretConfig.existingSecret=channelwatch-runtime")
    assert rendered.returncode == 0, rendered.stderr

    deployment = _rendered_resources(rendered.stdout)["Deployment"]
    annotations = deployment["spec"]["template"]["metadata"]["annotations"]
    assert "checksum/channelwatch-managed-secret" not in annotations
    assert deployment["spec"]["template"]["spec"]["containers"][0]["envFrom"][
        1
    ]["secretRef"]["name"] == "channelwatch-runtime"


def test_helm_keeps_custom_annotations_without_overriding_rollout_checksums():
    rendered = _helm_template(r"podAnnotations.example\.com/owner=operations")
    assert rendered.returncode == 0, rendered.stderr

    annotations = _rendered_resources(rendered.stdout)["Deployment"]["spec"][
        "template"
    ]["metadata"]["annotations"]
    assert annotations["example.com/owner"] == "operations"
    assert annotations["checksum/channelwatch-config"]


def test_helm_rejects_operator_override_of_reserved_rollout_checksum():
    rendered = _helm_template(
        "podAnnotations.checksum/channelwatch-config=operator-value"
    )
    assert rendered.returncode != 0
    assert "checksum/channelwatch- are reserved" in rendered.stderr


def test_documented_external_secret_rollout_selector_survives_name_override():
    rendered = _helm_template("nameOverride=watch")
    assert rendered.returncode == 0, rendered.stderr

    labels = _rendered_resources(rendered.stdout)["Deployment"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/instance"] == "channelwatch"
    assert labels["app.kubernetes.io/name"] == "watch"

    command = (
        "kubectl rollout restart deployment -l "
        "app.kubernetes.io/instance=<release-name>"
    )
    for path in (
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs" / "reference" / "health-diagnostics.md",
    ):
        contents = path.read_text(encoding="utf-8")
        assert command in contents
        assert "app.kubernetes.io/name=channelwatch" not in contents


def test_helm_managed_secret_needs_no_external_storage_key():
    missing = _helm_template()
    assert missing.returncode == 0, missing.stderr
    assert "kind: Secret" not in missing.stdout
    assert "secretRef:" not in missing.stdout
    assert "CHANNELWATCH_SECRET_STORAGE_KEY" not in missing.stdout


def test_helm_rejects_short_optional_legacy_storage_key():

    short = _helm_template("secretConfig.secretStorageKey=too-short")
    assert short.returncode != 0
    assert "deprecated secretConfig.secretStorageKey must contain at least 32" in short.stderr


def test_helm_managed_secret_renders_optional_legacy_storage_key():
    rendered = _helm_template(
        "secretConfig.secretStorageKey=0123456789abcdef0123456789abcdef"
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "kind: Secret" in rendered.stdout
    assert "CHANNELWATCH_SECRET_STORAGE_KEY" in rendered.stdout


def test_helm_existing_secret_is_referenced_without_rendering_managed_secret():
    rendered = _helm_template("secretConfig.existingSecret=channelwatch-runtime")
    assert rendered.returncode == 0, rendered.stderr
    assert "name: channelwatch-runtime" in rendered.stdout
    assert "kind: Secret" not in rendered.stdout


def test_helm_rejects_conflicting_secret_modes():
    rendered = _helm_template(
        "secretConfig.existingSecret=channelwatch-runtime",
        "secretConfig.secretStorageKey=0123456789abcdef0123456789abcdef",
    )
    assert rendered.returncode != 0
    assert "cannot be combined" in rendered.stderr


@pytest.mark.parametrize(
    "value,error_fragment",
    [
        ("replicaCount=2", "replicaCount=1 only"),
        ("persistence.enabled=false", "requires persistence.enabled=true"),
    ],
)
def test_helm_rejects_unsupported_runtime_modes(value, error_fragment):
    rendered = _helm_template(value)
    assert rendered.returncode != 0
    assert error_fragment in rendered.stderr


def test_unraid_does_not_prompt_for_an_external_storage_key():
    root = ElementTree.parse(UNRAID_TEMPLATE).getroot()
    fields = root.findall(
        './/Config[@Target="CHANNELWATCH_SECRET_STORAGE_KEY"]'
    )

    assert fields == []


def test_documented_local_dev_state_is_excluded_from_git_and_docker_contexts():
    gitignore = REPO_ROOT / ".gitignore"
    dockerignore = REPO_ROOT / "deploy" / "docker" / "Dockerfile.dockerignore"

    assert ".dev-config/" in gitignore.read_text(encoding="utf-8").splitlines()
    assert ".dev-config" in dockerignore.read_text(encoding="utf-8").splitlines()
    for relative_path in (
        ".dev-config/settings.json",
        ".dev-config/channelwatch.db",
        ".dev-config/session_state_review.json",
    ):
        ignored = subprocess.run(
            [
                "git",
                "check-ignore",
                "--quiet",
                "--no-index",
                relative_path,
            ],
            cwd=REPO_ROOT,
            check=False,
        )
        assert ignored.returncode == 0, f"Git does not ignore {relative_path}"
