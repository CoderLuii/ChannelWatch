#!/usr/bin/env python3
"""Assemble only the four native evidence shards for the current release commit."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


def merge(input_dir: Path, output_dir: Path, source_commit: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("Native shard source commit must be a full Git SHA.")

    names = {
        f"runtime-capability-{architecture}-{shard}-{source_commit}"
        for architecture in ("amd64", "arm64")
        for shard in ("bootstrap", "chained")
    }
    artifacts = {child.name: child for child in input_dir.iterdir()}
    if artifacts.keys() != names or any(
        artifact.is_symlink() or not artifact.is_dir() for artifact in artifacts.values()
    ):
        raise ValueError("Native evidence requires exactly four SHA-bound artifact directories.")

    files: dict[Path, Path] = {}
    for artifact in artifacts.values():
        for path in artifact.rglob("*"):
            if path.is_symlink() or (not path.is_dir() and not path.is_file()):
                raise ValueError("Native evidence contains an unsupported entry.")
            if path.is_dir():
                continue
            relative = path.relative_to(artifact)
            if relative in files:
                raise ValueError(f"Duplicate native evidence path: {relative}")
            files[relative] = path

    output_dir.mkdir()
    for relative, path in sorted(files.items()):
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    merge(args.input, args.output, args.source_commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
