#!/usr/bin/env python3
"""Validate and describe an exact two-platform ChannelWatch OCI image layout."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXPECTED_PLATFORMS = ("linux/amd64", "linux/arm64")
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _load_json(path: Path, *, description: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is unreadable or invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object.")
    return value, raw


def _validate_descriptor(
    layout: Path,
    descriptor: Any,
    *,
    description: str,
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(descriptor, dict):
        raise ValueError(f"{description} descriptor must be an object.")
    media_type = descriptor.get("mediaType")
    if not isinstance(media_type, str) or not media_type:
        raise ValueError(f"{description} descriptor has an invalid media type.")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError(f"{description} descriptor has an invalid digest.")
    suffix = digest.removeprefix("sha256:")
    if len(suffix) != 64 or any(
        character not in "0123456789abcdef" for character in suffix
    ):
        raise ValueError(f"{description} descriptor has an invalid digest.")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError(f"{description} descriptor has an invalid size.")
    blob = layout / "blobs" / "sha256" / suffix
    try:
        raw = blob.read_bytes()
    except OSError as exc:
        raise ValueError(f"{description} blob is missing.") from exc
    if len(raw) != size or _sha256(raw) != digest:
        raise ValueError(f"{description} blob does not match its descriptor.")
    return descriptor, raw


def describe_layout(layout: Path) -> dict[str, Any]:
    layout_metadata, _layout_raw = _load_json(
        layout / "oci-layout", description="OCI layout metadata"
    )
    if layout_metadata != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("OCI layout metadata must declare imageLayoutVersion 1.0.0.")
    root, _root_raw = _load_json(layout / "index.json", description="OCI root index")
    manifests = root.get("manifests")
    if (
        root.get("schemaVersion") != 2
        or not isinstance(manifests, list)
        or len(manifests) != 1
    ):
        raise ValueError("OCI root index must reference exactly one image index.")
    root_descriptor, index_raw = _validate_descriptor(
        layout,
        manifests[0],
        description="nested image index",
    )
    if root_descriptor.get("mediaType") != OCI_INDEX:
        raise ValueError("OCI root descriptor does not reference an OCI image index.")
    try:
        image_index = json.loads(index_raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Nested OCI image index is invalid JSON.") from exc
    platform_manifests = image_index.get("manifests") if isinstance(image_index, dict) else None
    if (
        not isinstance(image_index, dict)
        or image_index.get("schemaVersion") != 2
        or image_index.get("mediaType") != OCI_INDEX
        or not isinstance(platform_manifests, list)
    ):
        raise ValueError("Nested OCI image index is invalid.")

    described_platforms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for descriptor in platform_manifests:
        platform = descriptor.get("platform") if isinstance(descriptor, dict) else None
        if not isinstance(platform, dict):
            raise ValueError("A platform manifest has no platform descriptor.")
        os_name = platform.get("os")
        architecture = platform.get("architecture")
        platform_name = f"{os_name}/{architecture}"
        if platform_name not in EXPECTED_PLATFORMS or platform_name in seen:
            raise ValueError("OCI image must contain exactly linux/amd64 and linux/arm64.")
        if set(platform) - {"architecture", "os"}:
            raise ValueError("OCI platform descriptors must not contain unreviewed variants.")
        seen.add(platform_name)
        manifest_descriptor, manifest_raw = _validate_descriptor(
            layout,
            descriptor,
            description=f"{platform_name} manifest",
        )
        if manifest_descriptor.get("mediaType") != OCI_MANIFEST:
            raise ValueError(f"{platform_name} does not reference an OCI image manifest.")
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{platform_name} manifest is invalid JSON.") from exc
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
            raise ValueError(f"{platform_name} manifest is invalid.")
        config_descriptor, _config_raw = _validate_descriptor(
            layout,
            manifest.get("config"),
            description=f"{platform_name} config",
        )
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ValueError(f"{platform_name} manifest has no layers.")
        described_layers: list[dict[str, Any]] = []
        for number, layer in enumerate(layers, start=1):
            layer_descriptor, _layer_raw = _validate_descriptor(
                layout,
                layer,
                description=f"{platform_name} layer {number}",
            )
            described_layers.append(
                {
                    key: layer_descriptor[key]
                    for key in ("mediaType", "digest", "size")
                }
            )
        described_platforms.append(
            {
                "platform": platform_name,
                "manifest": {
                    key: manifest_descriptor[key]
                    for key in ("mediaType", "digest", "size")
                },
                "config": {
                    key: config_descriptor[key]
                    for key in ("mediaType", "digest", "size")
                },
                "layers": described_layers,
            }
        )
    if seen != set(EXPECTED_PLATFORMS):
        raise ValueError("OCI image must contain exactly linux/amd64 and linux/arm64.")
    return {
        "schema": 1,
        "image_index": {
            key: root_descriptor[key] for key in ("mediaType", "digest", "size")
        },
        "platforms": sorted(described_platforms, key=lambda item: item["platform"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oci-layout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--digest-output", required=True, type=Path)
    args = parser.parse_args()
    try:
        description = describe_layout(args.oci_layout)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.digest_output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(description, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        args.digest_output.write_text(
            str(description["image_index"]["digest"]) + "\n",
            encoding="ascii",
        )
    except (OSError, ValueError) as exc:
        print(f"OCI image verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
