#!/usr/bin/env python3
"""Fetch and package pinned copyleft license texts for release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse


SPDX_LICENSE_LIST_COMMIT = "5bf6d9610255540bfbee6890765a616042bf1e11"
MAX_LICENSE_BYTES = 128 * 1024


class LicenseArtifact(NamedTuple):
    filename: str
    sha256: str

    @property
    def url(self) -> str:
        return (
            "https://raw.githubusercontent.com/spdx/license-list-data/"
            f"{SPDX_LICENSE_LIST_COMMIT}/text/{self.filename}"
        )


COPYLEFT_LICENSES = (
    LicenseArtifact(
        "GPL-1.0-only.txt",
        "da37ffd5fcdf0a7d76af862a4330ea1076e1443b51782b2f912148357b7acabe",
    ),
    LicenseArtifact(
        "GPL-2.0-only.txt",
        "aaf135472f81c5b4a0dca9367e5bb5e9750032b5bebe5442b36e4c0a47430df3",
    ),
    LicenseArtifact(
        "GPL-3.0-only.txt",
        "fb981668c18a279e285fc4d83fba1e836cc84dd4daa73c9697d3cfd2d8aca6e0",
    ),
    LicenseArtifact(
        "LGPL-2.1-only.txt",
        "5749785c8bdefafcb5d798270ed0a967036fe2ca63dcedade1627565dfef81d2",
    ),
    LicenseArtifact(
        "GCC-exception-3.1.txt",
        "7103d4f7f7e2f8ce10d282a05e0689637f8d6d9ef7b399d808d1da313e69b960",
    ),
)


def _download(artifact: LicenseArtifact) -> bytes:
    request = urllib.request.Request(
        artifact.url,
        headers={"User-Agent": "ChannelWatch release license fetcher"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        parsed = urlparse(final_url)
        expected_prefix = (
            f"/spdx/license-list-data/{SPDX_LICENSE_LIST_COMMIT}/text/"
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname != "raw.githubusercontent.com"
            or not parsed.path.startswith(expected_prefix)
        ):
            raise ValueError(f"Untrusted final license URL for {artifact.filename}.")
        data = response.read(MAX_LICENSE_BYTES + 1)
    if len(data) > MAX_LICENSE_BYTES:
        raise ValueError(f"License text is too large: {artifact.filename}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != artifact.sha256:
        raise ValueError(f"License text digest mismatch: {artifact.filename}")
    return data


def fetch_license_texts(destination: Path) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for artifact in COPYLEFT_LICENSES:
        target = destination / artifact.filename
        target.write_bytes(_download(artifact))
        written.append(target)
    return tuple(written)


def write_deterministic_archive(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(item for item in source.iterdir() if item.is_file()):
            info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-map", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()

    fetch_license_texts(args.output_dir)
    if args.source_map:
        source_map = args.source_map.resolve()
        if not source_map.is_file():
            raise ValueError(f"Corresponding-source map is missing: {source_map}")
        shutil.copy2(source_map, args.output_dir / "CORRESPONDING_SOURCE.md")
    if args.archive:
        write_deterministic_archive(args.output_dir, args.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
