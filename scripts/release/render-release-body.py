#!/usr/bin/env python3
"""Render ChannelWatch GitHub Release body text."""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts" / "release" / "export-site-release-metadata.py"


def load_exporter():
    spec = importlib.util.spec_from_file_location("export_site_release_metadata", EXPORTER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load release metadata exporter.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-url", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    requested_version = args.version.strip().lstrip("v")
    requested_tag = f"v{requested_version}"
    exporter = load_exporter()
    metadata = exporter.collect_metadata(
        requested_tag,
        args.release_url,
        requested_tag if requested_tag == "v0.9.10" else None,
    )
    version_tag = metadata["versionTag"]
    highlights = metadata.get("changelogHighlights") or []
    if version_tag == "v0.9.10":
        body = [
            f"# ChannelWatch {version_tag} - Runtime and Config Repair",
            "",
            "This is a repair release for v0.9.9. If you pulled v0.9.9, update to v0.9.10.",
            "",
            "It fixes the runtime launcher/startup path, preserves settings schema metadata so migration backups do not repeat, treats blank DVR names as optional by falling back to the DVR host or IP, and accepts Windows-edited UTF-8 settings files.",
            "",
            "Because this repair touches Docker entrypoint and runtime behavior, it requires a normal container image update.",
            "",
            "## What's Fixed",
            "",
        ]
    elif version_tag in {"v0.9.12", "v0.9.13", "v0.9.14", "v0.9.15", "v0.9.16"}:
        title = {
            "v0.9.12": "Dependency maintenance",
            "v0.9.13": "Reporting reliability",
            "v0.9.14": "Reporting and update reliability",
            "v0.9.15": "Update and reporting reliability",
            "v0.9.16": "Monitoring, update, and deployment reliability",
        }[version_tag]
        body = [f"# ChannelWatch {version_tag} - {title}"]
        sections = metadata.get("changelogSections") or {"Changed": highlights}
        for heading in ("Added", "Changed", "Fixed", "Security"):
            items = sections.get(heading) or []
            if not items:
                continue
            body.extend(["", f"## {heading}", ""])
            body.extend(f"- {item}" for item in items)
    else:
        release_date = datetime.strptime(
            str(metadata["releaseDate"]),
            "%Y-%m-%d",
        ).strftime("%m/%d/%Y")
        body = [f"## {release_date}"]
        sections = metadata.get("changelogSections") or {"Fixed": highlights}
        for heading in ("Added", "Changed", "Fixed", "Security"):
            items = sections.get(heading) or []
            if not items:
                continue
            body.extend(["", f"### {heading}", ""])
            body.extend(f"- {item}" for item in items)
    if version_tag == "v0.9.10":
        body.extend(f"- {item}" for item in highlights)
    heading_level = "##" if version_tag in {"v0.9.10", "v0.9.12", "v0.9.13", "v0.9.14", "v0.9.15", "v0.9.16"} else "###"
    body.extend(
        [
            "",
            f"{heading_level} Docs",
            "",
            "[ChannelWatch Official Docs Site](https://channelwatch.coderluii.dev/)",
            "",
            f"{heading_level} Images",
            "",
            "Docker Hub:",
            f"`coderluii/channelwatch:{metadata['dockerTag']}`",
            "`coderluii/channelwatch:latest`",
            "",
            "GHCR:",
            f"`ghcr.io/coderluii/channelwatch:{metadata['dockerTag']}`",
            "`ghcr.io/coderluii/channelwatch:latest`",
        ]
    )
    if version_tag == "v0.9.16":
        asset_base = (
            "https://github.com/CoderLuii/ChannelWatch/releases/download/"
            f"{version_tag}"
        )
        body.extend(
            [
                "",
                "## License and verification",
                "",
                "ChannelWatch is MIT-licensed. The container and app-update bundle "
                "include the project notices, applicable complete copyleft license "
                "texts, and an exact corresponding-source map.",
                "",
                f"- [Third-party license inventory]({asset_base}/channelwatch-{version_tag}-THIRD-PARTY-LICENSES.md)",
                f"- [Corresponding-source and rebuild map]({asset_base}/channelwatch-{version_tag}-CORRESPONDING-SOURCE.md)",
                f"- [Complete copyleft license texts]({asset_base}/channelwatch-{version_tag}-COPYLEFT-LICENSES.zip)",
                f"- [Release asset checksums]({asset_base}/channelwatch-{version_tag}-SHA256SUMS.txt)",
                "- Exact amd64 and arm64 SPDX and CycloneDX SBOMs are attached below; every other attached asset is covered by the checksum manifest.",
            ]
        )
    text = "\n".join(body) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
