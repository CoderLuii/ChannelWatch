#!/usr/bin/env python3
"""Export ChannelWatch release metadata for the website sync workflow."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str, tree_ref: str | None = None) -> str:
    if tree_ref:
        try:
            result = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{tree_ref}:{relative}"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            raise ValueError(f"Could not read {relative} from {tree_ref}: {detail}") from exc
        return result.stdout
    return (ROOT / relative).read_text(encoding="utf-8")


def run_git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def match_one(label: str, pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError(f"Could not find {label}.")
    return match.group(1)


def parse_changelog(version: str, tree_ref: str | None = None) -> dict[str, object]:
    changelog = read_text("docs/releases/CHANGELOG.md", tree_ref)
    version_re = re.escape(version)
    header = re.search(
        rf"^## \[{version_re}\] - (?P<date>\d{{4}}-\d{{2}}-\d{{2}})\s*$",
        changelog,
        re.MULTILINE,
    )
    if not header:
        raise ValueError(
            f"docs/releases/CHANGELOG.md has no release heading for {version}."
        )

    start = header.end()
    next_header = re.search(r"^## \[", changelog[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(changelog)
    section = changelog[start:end].strip()

    grouped: dict[str, list[str]] = OrderedDict()
    current_heading = ""
    for line in section.splitlines():
        heading = re.match(r"^### (.+)\s*$", line)
        if heading:
            current_heading = heading.group(1).strip()
            grouped.setdefault(current_heading, [])
            continue
        item = re.match(r"^- (.+)\s*$", line)
        if item and current_heading:
            grouped.setdefault(current_heading, []).append(item.group(1).strip())

    highlights: list[str] = []
    for heading, items in grouped.items():
        if heading.lower() == "known limits":
            continue
        highlights.extend(items)

    known_limits = grouped.get("Known Limits", [])
    known_limits_source = version
    if not known_limits:
        fallback = re.search(
            r"^### Known Limits\s*$\n(?P<body>.*?)(?=^## \[|^### |\Z)",
            changelog[start:],
            re.MULTILINE | re.DOTALL,
        )
        if fallback:
            known_limits = [
                item.group(1).strip()
                for item in re.finditer(r"^- (.+)\s*$", fallback.group("body"), re.MULTILINE)
            ]
            previous_heading = changelog[: start + fallback.start()].rstrip().split("## [")[-1]
            source_match = re.match(r"(?P<version>\d+\.\d+\.\d+)\]", previous_heading)
            if source_match:
                known_limits_source = source_match.group("version")

    previous_releases: list[dict[str, object]] = []
    for item in re.finditer(
        r"^## \[(?P<version>\d+\.\d+\.\d+)\] - (?P<date>\d{4}-\d{2}-\d{2})\s*$",
        changelog,
        re.MULTILINE,
    ):
        item_version = item.group("version")
        if item_version == version:
            continue
        if item_version == "Unreleased":
            continue
        previous_releases.append(
            {
                "version": item_version,
                "versionTag": f"v{item_version}",
                "releaseDate": item.group("date"),
            }
        )
        if len(previous_releases) >= 4:
            break

    return {
        "releaseDate": header.group("date"),
        "changelogHighlights": highlights,
        "knownLimits": known_limits,
        "knownLimitsSource": known_limits_source,
        "previousReleases": previous_releases,
    }


def collect_metadata(
    source_ref: str | None,
    release_url: str | None,
    tree_ref: str | None = None,
) -> dict[str, object]:
    core_version = match_one(
        "core version",
        r'^__version__\s*=\s*"([^"]+)"',
        read_text("app/core/__init__.py", tree_ref),
    )
    ui_package = json.loads(read_text("app/ui/package.json", tree_ref))
    ui_version = ui_package["version"]
    docker_version = match_one(
        "Dockerfile VERSION",
        r"^ARG VERSION=([0-9]+\.[0-9]+\.[0-9]+)",
        read_text("deploy/docker/Dockerfile", tree_ref),
    )
    chart = read_text("deploy/helm/channelwatch/Chart.yaml", tree_ref)
    helm_chart_version = match_one(
        "Helm chart version", r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)", chart
    )
    helm_app_version = match_one(
        "Helm appVersion", r'^appVersion:\s*"([^"]+)"', chart
    )
    helm_values = read_text("deploy/helm/channelwatch/values.yaml", tree_ref)
    helm_image_tag = match_one("Helm image tag", r'^\s*tag:\s*"([^"]+)"', helm_values)

    versions = {
        "core": core_version,
        "ui": ui_version,
        "docker": docker_version,
        "helmChart": helm_chart_version,
        "helmApp": helm_app_version,
        "helmImageTag": helm_image_tag,
    }
    unique_versions = sorted(set(versions.values()))
    if len(unique_versions) != 1:
        details = ", ".join(f"{name}={value}" for name, value in versions.items())
        raise ValueError(f"Release version surfaces disagree: {details}")

    version = unique_versions[0]
    changelog = parse_changelog(version, tree_ref)

    unraid_xml = ElementTree.fromstring(read_text("deploy/unraid/channelwatch.xml", tree_ref))
    repository = unraid_xml.findtext("Repository")
    if not repository:
        raise ValueError("Unraid template does not include a Repository value.")

    source_commit = run_git("rev-parse", tree_ref or "HEAD") or ""
    detected_ref = source_ref or tree_ref or run_git("describe", "--tags", "--exact-match") or run_git(
        "rev-parse", "--abbrev-ref", "HEAD"
    )
    version_tag = f"v{version}"
    if source_ref and source_ref.startswith("v") and source_ref != version_tag:
        raise ValueError(
            f"source ref {source_ref} does not match resolved app version {version_tag}."
        )

    return {
        "version": version,
        "versionTag": version_tag,
        "releaseDate": changelog["releaseDate"],
        "sourceCommit": source_commit,
        "sourceRef": detected_ref,
        "dockerTag": version,
        "dockerImage": "coderluii/channelwatch",
        "helmChartVersion": helm_chart_version,
        "helmAppVersion": helm_app_version,
        "unraidRepository": repository,
        "releaseUrl": release_url
        or f"https://github.com/CoderLuii/ChannelWatch/releases/tag/{version_tag}",
        "changelogHighlights": changelog["changelogHighlights"],
        "knownLimits": changelog["knownLimits"],
        "knownLimitsSource": changelog["knownLimitsSource"],
        "previousReleases": changelog["previousReleases"],
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export ChannelWatch release metadata for ChannelWatch-site."
    )
    parser.add_argument("--source-ref", default=None)
    parser.add_argument(
        "--tree-ref",
        default=None,
        help="Read release files from this git ref while using the exporter from the current checkout.",
    )
    parser.add_argument("--release-url", default=None)
    args = parser.parse_args()

    try:
        metadata = collect_metadata(args.source_ref, args.release_url, args.tree_ref)
    except Exception as exc:  # noqa: BLE001 - command-line exporter should fail clearly
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
