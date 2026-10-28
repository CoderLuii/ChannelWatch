import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/verify-local-qualification.py"
COMMANDS = {
    "backend": "linux-backend-full",
    "frontend": "frontend-typecheck-lint-unit-build",
    "browser": "linux-headless-browser-full",
    "workflow": "release-workflow-contracts",
    "docs": "docs-compose-helm",
    "security": "source-and-image-security",
    "image": "canonical-amd64-image-smoke",
}


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def qualification(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    files = {
        ".github/workflows/docker-publish.yml": "name: Release\n",
        "scripts/release/release-config.json": '{"version":"1.6.4"}\n',
        "app/ui/package.json": '{"packageManager":"pnpm@11.28.9+sha512.test"}\n',
        "app/ui/pnpm-lock.yaml": (
            "importers:\n  .:\n    devDependencies:\n      '@playwright/test':\n"
            "        specifier: ^1.64.1\n        version: 1.64.1\n"
        ),
    }
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    _git(root, "init", "--quiet")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release-test@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "v1.6.4")
    _git(root, "tag", "v1.6.4")
    commit = _git(root, "rev-parse", "HEAD")
    record = {
        "schema": 1,
        "type": "maintainer_local",
        "repository": "CoderLuii/ChannelWatch",
        "actor": "CoderLuii",
        "tag": "v1.6.4",
        "version": "1.6.4",
        "source_commit": commit,
        "source_tree": _git(root, "rev-parse", "HEAD^{tree}"),
        "workflow_sha256": hashlib.sha256(
            (root / ".github/workflows/docker-publish.yml").read_bytes()
        ).hexdigest(),
        "release_config_sha256": hashlib.sha256(
            (root / "scripts/release/release-config.json").read_bytes()
        ).hexdigest(),
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "tools": {
            "python": "3.14.8", "node": "24.22.1", "pnpm": "11.28.9",
            "playwright": "1.64.1", "git": "2.52.0.windows.1",
            "docker": "29.9.2", "trivy": "0.76.1", "helm": "4.3.1",
            "actionlint": "1.7.12",
        },
        "checks": {
            name: {"command_id": command, "exit_code": 0, "count": 1,
                   "report_sha256": hashlib.sha256(name.encode()).hexdigest()}
            for name, command in COMMANDS.items()
        },
    }
    env = dict(os.environ, GITHUB_EVENT_NAME="push", GITHUB_ACTOR="CoderLuii",
               GITHUB_REPOSITORY="CoderLuii/ChannelWatch", GITHUB_SHA=commit,
               GITHUB_REF="refs/tags/v1.6.4", GIT_TERMINAL_PROMPT="0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return root, record, env, tmp_path / "accepted.json"


def _run(qualification, *, raw=None):
    root, record, env, output = qualification
    env = dict(env)
    if raw is not None:
        env["CHANNELWATCH_LOCAL_QUALIFICATION"] = raw
    else:
        env["CHANNELWATCH_LOCAL_QUALIFICATION"] = json.dumps(record)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--output", str(output)],
        env=env, capture_output=True, text=True,
    )
    return result, output


def test_accepts_exact_maintainer_record_without_claiming_remote_execution(qualification):
    result, output = _run(qualification)
    assert result.returncode == 0, result.stderr
    accepted = json.loads(output.read_text(encoding="utf-8"))
    assert accepted["operator_attestation"] is True
    assert accepted["independently_executed"] is False
    assert "individual reports retain execution provenance" in accepted["boundary"]
    assert accepted["record"] == qualification[1]
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.with_suffix(".json.sha256").read_text().strip() == digest


@pytest.mark.parametrize("field,value", [
    ("schema", True), ("schema", 2), ("type", "hosted"),
    ("repository", "other/ChannelWatch"), ("actor", "other"),
    ("tag", "v1.6.3"), ("version", "1.6.3"),
    ("source_commit", "a" * 40), ("source_tree", "b" * 40),
    ("workflow_sha256", "c" * 64), ("release_config_sha256", "d" * 64),
    ("qualified_at", "2026-10-28T12:00:00"),
    ("qualified_at", "not a date"),
])
def test_rejects_inconsistent_identity(qualification, field, value):
    qualification[1][field] = value
    result, output = _run(qualification)
    assert result.returncode == 1
    assert "Local qualification rejected:" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("field,value", [
    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_ACTOR", "other"),
    ("GITHUB_REPOSITORY", "other/ChannelWatch"),
    ("GITHUB_SHA", "a" * 40), ("GITHUB_REF", "refs/heads/main"),
])
def test_rejects_wrong_event(qualification, field, value):
    qualification[2][field] = value
    result, output = _run(qualification)
    assert result.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize("raw", ["", "{}", "[]", "null", "{", "x" * 16385],
                         ids=["missing", "empty-object", "array", "null", "malformed", "oversized"])
def test_fails_closed_on_missing_or_invalid_record(qualification, raw):
    result, output = _run(qualification, raw=raw)
    assert result.returncode == 1
    assert "Local qualification rejected:" in result.stderr
    assert not output.exists()


def test_rejects_duplicate_keys_and_extra_fields(qualification):
    record = qualification[1]
    duplicate = json.dumps(record).replace('"schema": 1', '"schema": 1, "schema": 1')
    result, output = _run(qualification, raw=duplicate)
    assert result.returncode == 1
    record["private_path"] = "do-not-copy"
    result, output = _run(qualification)
    assert result.returncode == 1
    assert "do-not-copy" not in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("check", COMMANDS)
@pytest.mark.parametrize("field,value", [
    ("command_id", "different-command"), ("exit_code", 1),
    ("exit_code", False), ("count", 0), ("count", True),
    ("report_sha256", "not-a-hash"),
])
def test_rejects_failed_empty_or_unrecognized_checks(qualification, check, field, value):
    qualification[1]["checks"][check][field] = value
    result, output = _run(qualification)
    assert result.returncode == 1
    assert not output.exists()


def test_requires_all_checks_and_only_recognized_fields(qualification):
    record = qualification[1]
    original = copy.deepcopy(record["checks"])
    del record["checks"]["browser"]
    result, _ = _run(qualification)
    assert result.returncode == 1
    record["checks"] = original
    record["checks"]["image"]["path"] = "do-not-copy"
    result, _ = _run(qualification)
    assert result.returncode == 1


@pytest.mark.parametrize("tool,value", [
    ("python", "3.13.9"), ("node", "22.23.0"), ("pnpm", "11.28.5"),
    ("playwright", "1.63.0"), ("git", "$(do-not-run)"),
])
def test_rejects_unmatched_or_unsafe_tools(qualification, tool, value):
    qualification[1]["tools"][tool] = value
    result, output = _run(qualification)
    assert result.returncode == 1
    assert "do-not-run" not in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("delta", [timedelta(days=-8), timedelta(minutes=-5), timedelta(minutes=6)])
def test_rejects_stale_or_future_time(qualification, delta):
    qualification[1]["qualified_at"] = (datetime.now(timezone.utc) + delta).isoformat()
    result, output = _run(qualification)
    assert result.returncode == 1
    assert not output.exists()


@pytest.mark.parametrize("change", ["tracked", "staged", "untracked", "annotated-tag"])
def test_requires_exact_clean_source_and_lightweight_tag(qualification, change):
    root = qualification[0]
    if change == "annotated-tag":
        _git(root, "tag", "-f", "-a", "v1.6.4", "-m", "Release")
    elif change == "untracked":
        (root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    else:
        (root / ".github/workflows/docker-publish.yml").write_text("changed\n")
        if change == "staged":
            _git(root, "add", ".")
    result, output = _run(qualification)
    assert result.returncode == 1
    assert not output.exists()


def test_refuses_to_overwrite_existing_accepted_evidence(qualification):
    output = qualification[3]
    output.write_text("previous", encoding="utf-8")
    result, _ = _run(qualification)
    assert result.returncode == 1
    assert output.read_text() == "previous"


def test_rejects_extra_or_missing_tools(qualification):
    tools = qualification[1]["tools"]
    docker = tools.pop("docker")
    result, _ = _run(qualification)
    assert result.returncode == 1
    tools["docker"] = docker
    tools["extra"] = "1.2.3"
    result, _ = _run(qualification)
    assert result.returncode == 1


def test_rejects_unbounded_nested_json_without_echoing_it(qualification):
    result, output = _run(qualification, raw="[" * 2000 + "]" * 2000)
    assert result.returncode == 1
    assert "Local qualification rejected:" in result.stderr
    assert not output.exists()


def test_does_not_mistake_clean_crlf_conversion_for_exact_source_bytes(qualification):
    root = qualification[0]
    _git(root, "config", "core.autocrlf", "true")
    (root / ".github/workflows/docker-publish.yml").write_bytes(b"name: Release\r\n")
    _git(root, "add", ".github/workflows/docker-publish.yml")
    assert _git(root, "write-tree") == qualification[1]["source_tree"]
    assert not _git(root, "status", "--porcelain")
    result, output = _run(qualification)
    assert result.returncode == 1
    assert not output.exists()


def test_does_not_write_output_when_digest_already_exists(qualification):
    output = qualification[3]
    digest_path = output.with_suffix(".json.sha256")
    digest_path.write_text("previous", encoding="ascii")
    result, _ = _run(qualification)
    assert result.returncode == 1
    assert not output.exists()
    assert digest_path.read_text() == "previous"
