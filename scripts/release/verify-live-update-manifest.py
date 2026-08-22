#!/usr/bin/env python3
"""Wait for and verify the public stable update manifest after deployment."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path


VERIFIER = Path(__file__).with_name("verify-update-assets.py")


def load_asset_verifier():
    spec = importlib.util.spec_from_file_location("verify_update_assets", VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load update asset verifier.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://channelwatch.coderluii.dev/updates/stable.json")
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--image-required", choices=("true", "false"), required=True)
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--interval", type=float, default=10.0)
    return parser.parse_args()


def verify(manifest: dict, args: argparse.Namespace) -> list[str]:
    payload = manifest.get("payload") if isinstance(manifest, dict) else None
    if not isinstance(payload, dict):
        return ["manifest payload is missing"]
    expected_image_required = args.image_required == "true"
    checks = {
        "version": (payload.get("version"), args.version),
        "version_tag": (payload.get("version_tag"), f"v{args.version}"),
        "release_url": (payload.get("release_url"), args.release_url),
        "runtime_abi": (payload.get("runtime_abi"), "channelwatch-runtime-v1"),
        "bundle_sha256": (payload.get("bundle_sha256"), args.bundle_sha256),
        "image_required": (payload.get("image_required"), expected_image_required),
    }
    return [f"{name}={actual!r}, expected {expected!r}" for name, (actual, expected) in checks.items() if actual != expected]


def main() -> int:
    args = parse_args()
    verifier = load_asset_verifier()
    last_errors: list[str] = []
    for attempt in range(1, args.attempts + 1):
        try:
            manifest_bytes = verifier.fetch_bytes(
                args.url,
                max_bytes=verifier.MAX_MANIFEST_BYTES,
            )
            trusted_manifest = verifier.verify_manifest(manifest_bytes)
            bundle_url = str(trusted_manifest["payload"].get("bundle_url") or "")
            bundle_bytes = verifier.fetch_bytes(
                bundle_url,
                max_bytes=verifier.MAX_BUNDLE_BYTES,
            )
            manifest = verifier.verify_update_assets(
                manifest_bytes,
                bundle_bytes,
                expected_version=args.version,
            )
            last_errors = verify(manifest, args)
            if not last_errors:
                print(
                    "Live stable manifest and bundle verified for "
                    f"v{args.version} on attempt {attempt}."
                )
                return 0
        except Exception as exc:
            last_errors = [str(exc)]
        if attempt < args.attempts:
            time.sleep(args.interval)
    print("live stable manifest verification failed: " + "; ".join(last_errors), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
