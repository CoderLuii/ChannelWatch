#!/usr/bin/env python3
"""Verify that the public schema-1 feed remains pinned to v0.9.18."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

VERIFIER = Path(__file__).with_name("verify-update-assets.py")
BRIDGE_VERSION = "0.9.18"
BRIDGE_RELEASE_URL = (
    "https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.18"
)
BRIDGE_BUNDLE_URL = (
    "https://github.com/CoderLuii/ChannelWatch/releases/download/"
    "v0.9.18/channelwatch-app-v0.9.18.zip"
)


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_pinned_v1_assets", VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the update asset verifier.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_bridge_bytes(
    manifest_bytes: bytes,
    bundle_bytes: bytes,
    *,
    verifier: Any | None = None,
) -> dict[str, Any]:
    verifier = verifier or load_verifier()
    manifest = verifier.verify_update_assets(
        manifest_bytes,
        bundle_bytes,
        expected_version=BRIDGE_VERSION,
        expected_channel="stable",
        expected_runtime_abi="channelwatch-runtime-v1",
        expected_settings_schema_version=7,
        expected_image_required=False,
        expected_delivery_mode="app_update_with_image_refresh",
        expected_recommended_image_version=BRIDGE_VERSION,
        expected_release_url=BRIDGE_RELEASE_URL,
        expected_bundle_url=BRIDGE_BUNDLE_URL,
    )
    payload = manifest["payload"]
    required = {
        "version_tag": "v0.9.18",
        "minimum_image_version": "0.9.11",
        "updater_protocol": 2,
        "recommended_image_version": BRIDGE_VERSION,
    }
    mismatches = {
        name: (payload.get(name), expected)
        for name, expected in required.items()
        if payload.get(name) != expected
    }
    if mismatches:
        raise ValueError(f"The public v1 bridge is not pinned to its contract: {mismatches}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="https://channelwatch.coderluii.dev/updates/stable.json",
    )
    args = parser.parse_args()
    verifier = load_verifier()
    manifest_bytes = verifier.fetch_bytes(
        args.url,
        max_bytes=verifier.MAX_MANIFEST_BYTES,
    )
    trusted = verifier.verify_manifest(manifest_bytes)
    bundle_url = str(trusted.get("payload", {}).get("bundle_url") or "")
    if bundle_url != BRIDGE_BUNDLE_URL:
        raise ValueError("The public schema-1 feed no longer points to v0.9.18.")
    bundle_bytes = verifier.fetch_bytes(
        bundle_url,
        max_bytes=verifier.MAX_BUNDLE_BYTES,
    )
    manifest = verify_bridge_bytes(
        manifest_bytes,
        bundle_bytes,
        verifier=verifier,
    )
    print(
        json.dumps(
            {
                "schema": 1,
                "version": manifest["payload"]["version"],
                "pinned": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
