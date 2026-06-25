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
    else:
        body = [
            f"# ChannelWatch {version_tag} - Update Center",
            "",
            "ChannelWatch adds the new in-app **Update Center** in Settings.",
            "",
            "Compatible app-only updates can now be checked, verified, backed up, applied, restarted, and rolled back from the web UI. If a future release needs a new container image because the runtime changed, ChannelWatch will say **container image update required** instead of trying to force an unsafe in-app update.",
            "",
            "## What's New",
            "",
        ]
    body.extend(f"- {item}" for item in highlights)
    body.extend(
        [
            "",
            "## Docs",
            "",
            "[ChannelWatch Official Docs Site](https://channelwatch.coderluii.dev/)",
            "",
            "## Images",
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
    text = "\n".join(body) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
