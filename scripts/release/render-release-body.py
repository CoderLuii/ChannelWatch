#!/usr/bin/env python3
"""Render ChannelWatch GitHub Release body text."""

from __future__ import annotations

import argparse
import importlib.util
import json
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

    exporter = load_exporter()
    metadata = exporter.collect_metadata(
        f"v{args.version.strip().lstrip('v')}",
        args.release_url,
        None,
    )
    version_tag = metadata["versionTag"]
    highlights = metadata.get("changelogHighlights") or []
    known_limits = metadata.get("knownLimits") or []
    body = [
        f"# ChannelWatch {version_tag} - Update Center",
        "",
        "This release adds the new in-app Update Center and keeps the normal container update path clear when a release needs a new image.",
        "",
        "## What's New",
        "",
    ]
    body.extend(f"- {item}" for item in highlights)
    if known_limits:
        body.extend(["", "## Known Limits", ""])
        body.extend(f"- {item}" for item in known_limits)
    body.extend(
        [
            "",
            "## Images",
            "",
            f"Docker Hub: `coderluii/channelwatch:{metadata['dockerTag']}` and `coderluii/channelwatch:latest`",
            "",
            f"GHCR: `ghcr.io/coderluii/channelwatch:{metadata['dockerTag']}` and `ghcr.io/coderluii/channelwatch:latest`",
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
