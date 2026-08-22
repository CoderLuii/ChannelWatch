import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

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


def test_helm_managed_secret_requires_long_storage_key():
    missing = _helm_template()
    assert missing.returncode != 0
    assert "secretStorageKey must contain at least 32" in missing.stderr

    short = _helm_template("secretConfig.secretStorageKey=too-short")
    assert short.returncode != 0
    assert "secretStorageKey must contain at least 32" in short.stderr


def test_helm_managed_secret_renders_required_storage_key():
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
    assert "key: CHANNELWATCH_SECRET_STORAGE_KEY" in rendered.stdout


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
    rendered = _helm_template(
        "secretConfig.secretStorageKey=0123456789abcdef0123456789abcdef",
        value,
    )
    assert rendered.returncode != 0
    assert error_fragment in rendered.stderr


def test_unraid_requires_masked_secret_storage_key_without_a_default():
    root = ElementTree.parse(UNRAID_TEMPLATE).getroot()
    fields = root.findall(
        './/Config[@Target="CHANNELWATCH_SECRET_STORAGE_KEY"]'
    )

    assert len(fields) == 1
    field = fields[0]
    assert field.get("Type") == "Variable"
    assert field.get("Required") == "true"
    assert field.get("Mask") == "true"
    assert field.get("Default", "") == ""
    assert (field.text or "").strip() == ""


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
