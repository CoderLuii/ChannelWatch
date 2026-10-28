#!/usr/bin/env python3
"""Validate a maintainer's local qualification record for the exact release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPOSITORY = "CoderLuii/ChannelWatch"
ACTOR = "CoderLuii"
MAX_BYTES = 16384
COMMANDS = {
    "backend": "linux-backend-full",
    "frontend": "frontend-typecheck-lint-unit-build",
    "browser": "linux-headless-browser-full",
    "workflow": "release-workflow-contracts",
    "docs": "docs-compose-helm",
    "security": "source-and-image-security",
    "image": "canonical-amd64-image-smoke",
}
TOOLS = {"python", "node", "pnpm", "playwright", "git", "docker", "trivy", "helm", "actionlint"}
FIELDS = {
    "schema", "type", "repository", "actor", "tag", "version", "source_commit",
    "source_tree", "workflow_sha256", "release_config_sha256", "qualified_at",
    "tools", "checks",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def exact_fields(value, fields: set[str], name: str) -> None:
    require(isinstance(value, dict) and set(value) == fields, f"Invalid {name} fields.")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON keys are not allowed.")
        result[key] = value
    return result


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        check=True, capture_output=True,
    ).stdout


def tracked_bytes(root: Path, path: str) -> bytes:
    content = git(root, "cat-file", "blob", f"HEAD:{path}")
    require((root / path).read_bytes() == content, "Tracked source bytes differ from HEAD.")
    return content


def validate_record(raw: str, root: Path, env: dict[str, str], now: datetime) -> dict:
    require(0 < len(raw.encode("utf-8")) <= MAX_BYTES, "Missing or oversized local record.")
    record = json.loads(raw, object_pairs_hook=unique_object)
    exact_fields(record, FIELDS, "record")
    require(type(record["schema"]) is int and record["schema"] == 1, "Unsupported record schema.")
    require(record["type"] == "maintainer_local", "Expected a maintainer local attestation.")
    require(record["repository"] == REPOSITORY and record["actor"] == ACTOR, "Wrong record owner.")
    require(env.get("GITHUB_EVENT_NAME") == "push", "Expected a tag-push event.")
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY and env.get("GITHUB_ACTOR") == ACTOR,
            "Wrong event repository or actor.")
    for field, length in (("source_commit", 40), ("source_tree", 40),
                          ("workflow_sha256", 64), ("release_config_sha256", 64)):
        require(isinstance(record[field], str) and
                re.fullmatch(rf"[0-9a-f]{{{length}}}", record[field]) is not None,
                "Invalid source identity or digest.")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    require(record["source_commit"] == commit == env.get("GITHUB_SHA"), "Source commit mismatch.")
    require(record["source_tree"] == tree, "Source tree mismatch.")
    require(not git(root, "status", "--porcelain", "--untracked-files=all"), "Source must be clean.")
    for field, path in (("workflow_sha256", ".github/workflows/docker-publish.yml"),
                        ("release_config_sha256", "scripts/release/release-config.json")):
        require(hashlib.sha256(tracked_bytes(root, path)).hexdigest() == record[field],
                "Workflow or release configuration mismatch.")
    config = json.loads(tracked_bytes(root, "scripts/release/release-config.json"))
    version = config.get("version")
    require(isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version) is not None,
            "Invalid release version.")
    tag = f"v{version}"
    require(record["version"] == version and record["tag"] == tag and
            env.get("GITHUB_REF") == f"refs/tags/{tag}", "Release tag or version mismatch.")
    require(git(root, "cat-file", "-t", f"refs/tags/{tag}").strip() == b"commit" and
            git(root, "rev-parse", f"refs/tags/{tag}").decode().strip() == commit,
            "Expected the exact lightweight release tag.")
    require(isinstance(record["qualified_at"], str) and len(record["qualified_at"]) <= 40,
            "Invalid qualification timestamp.")
    qualified = datetime.fromisoformat(record["qualified_at"].replace("Z", "+00:00"))
    require(qualified.tzinfo is not None, "Qualification timestamp must include a timezone.")
    qualified = qualified.astimezone(timezone.utc)
    created = datetime.fromtimestamp(int(git(root, "show", "-s", "--format=%ct", "HEAD")), timezone.utc)
    require(created <= qualified <= now + timedelta(minutes=5) and now - qualified <= timedelta(days=7),
            "Qualification timestamp is stale, future-dated or predates the source commit.")
    tools = record["tools"]
    exact_fields(tools, TOOLS, "tool")
    for version_string in tools.values():
        require(isinstance(version_string, str) and len(version_string) <= 64 and
                re.fullmatch(r"\d+\.\d+\.\d+(?:[.+-][0-9A-Za-z.-]+)?", version_string) is not None,
                "Invalid tool version.")
    require(tools["python"].startswith("3.14.") and tools["node"].startswith("24."),
            "Local Python or Node major version does not match the release toolchain.")
    package = json.loads(tracked_bytes(root, "app/ui/package.json"))
    manager = package.get("packageManager", "")
    require(isinstance(manager, str) and manager.startswith("pnpm@") and
            tools["pnpm"] == manager.removeprefix("pnpm@").split("+", 1)[0], "pnpm version mismatch.")
    lock = tracked_bytes(root, "app/ui/pnpm-lock.yaml").decode("utf-8")
    matches = re.findall(
        r"^      '@playwright/test':\n        specifier: [^\n]+\n        version: (\d+\.\d+\.\d+)(?:\([^\n]*\))?$",
        lock, re.MULTILINE,
    )
    require(matches == [tools["playwright"]], "Playwright version mismatch.")
    exact_fields(record["checks"], set(COMMANDS), "check")
    for name, command in COMMANDS.items():
        check = record["checks"][name]
        exact_fields(check, {"command_id", "exit_code", "count", "report_sha256"}, "check result")
        require(check["command_id"] == command, "Unrecognized qualification command.")
        require(type(check["exit_code"]) is int and check["exit_code"] == 0, "A local check did not pass.")
        require(type(check["count"]) is int and 0 < check["count"] <= 1000000, "Invalid local check count.")
        require(isinstance(check["report_sha256"], str) and
                re.fullmatch(r"[0-9a-f]{64}", check["report_sha256"]) is not None,
                "Invalid local report digest.")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = validate_record(os.environ.get("CHANNELWATCH_LOCAL_QUALIFICATION", ""),
                                 args.root.resolve(), dict(os.environ), datetime.now(timezone.utc))
        # This records the operator's assertion, not independent execution or log inspection.
        accepted = {
            "operator_attestation": True, "independently_executed": False,
            "boundary": "Local operator attestation; individual reports retain execution provenance; "
                        "exact signed/native release qualification is separate.",
            "record": record,
        }
        content = (json.dumps(accepted, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        digest_path = args.output.with_suffix(args.output.suffix + ".sha256")
        require(not args.output.exists() and not digest_path.exists(), "Accepted evidence already exists.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            output.write(content)
        with digest_path.open("x", encoding="ascii", newline="\n") as output:
            output.write(digest + "\n")
    except (ValueError, TypeError, KeyError, OSError, RecursionError, subprocess.SubprocessError):
        print("Local qualification rejected: record, source or evidence validation failed.", file=sys.stderr)
        return 1
    print(json.dumps({"status": "accepted", "operator_attestation": True,
                      "independently_executed": False, "record_sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
