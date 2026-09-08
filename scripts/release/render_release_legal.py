#!/usr/bin/env python3
"""Give release legal documents an exact version without rewriting package data."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import shutil

DOCUMENTS = ("CORRESPONDING_SOURCE.md", "THIRD_PARTY_LICENSES.md")


def render_document(source: str, version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.[0-9]", version):
        raise ValueError("A release version with a single-digit patch is required.")
    title, separator, body = source.partition("\n")
    if not separator or not title.startswith("# "):
        raise ValueError("Legal document must start with a title.")
    title = title.removeprefix("# ").removeprefix("ChannelWatch ")
    return f"# ChannelWatch v{version} - {title}\n{body}"


def render_files(source_dir: Path, output_dir: Path, version: str) -> None:
    documents = {name: render_document((source_dir / name).read_text(), version) for name in DOCUMENTS}
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in documents.items():
        (output_dir / name).write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=os.environ.get("CHANNELWATCH_IMAGE_VERSION"))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--copyleft-source-map", type=Path)
    args = parser.parse_args()
    if not args.version:
        parser.error("--version or CHANNELWATCH_IMAGE_VERSION is required")
    render_files(args.source_dir, args.output_dir, args.version)
    if args.copyleft_source_map:
        shutil.copyfile(args.output_dir / "CORRESPONDING_SOURCE.md", args.copyleft_source_map)


if __name__ == "__main__":
    main()
