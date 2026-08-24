#!/usr/bin/env python3
"""Wait for and verify the public stable update manifest after deployment."""

from __future__ import annotations

import argparse
import importlib.util
import json
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
    parser.add_argument(
        "--url", default="https://channelwatch.coderluii.dev/updates/stable.json"
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--image-required", choices=("true", "false"), required=True)
    parser.add_argument(
        "--delivery-mode",
        choices=("app_update", "app_update_with_image_refresh", "image_required"),
    )
    parser.add_argument("--recommended-image-version")
    parser.add_argument("--catalog-url")
    parser.add_argument("--bridge-version", default="0.9.18")
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--interval", type=float, default=10.0)
    return parser.parse_args()


def verify(
    manifest: dict,
    args: argparse.Namespace,
    *,
    expected_version: str | None = None,
    expected_release_url: str | None = None,
    expected_bundle_sha256: str | None = None,
    expected_image_required: bool | None = None,
    expected_delivery_mode: str | None = None,
    expected_recommended_image_version: str | None = None,
) -> list[str]:
    payload = manifest.get("payload") if isinstance(manifest, dict) else None
    if not isinstance(payload, dict):
        return ["manifest payload is missing"]
    version = expected_version or args.version
    image_required = (
        args.image_required == "true"
        if expected_image_required is None
        else expected_image_required
    )
    checks = {
        "version": (payload.get("version"), version),
        "version_tag": (payload.get("version_tag"), f"v{version}"),
        "release_url": (
            payload.get("release_url"),
            expected_release_url or args.release_url,
        ),
        "runtime_abi": (payload.get("runtime_abi"), "channelwatch-runtime-v1"),
        "bundle_sha256": (
            payload.get("bundle_sha256"),
            expected_bundle_sha256 or args.bundle_sha256,
        ),
        "image_required": (payload.get("image_required"), image_required),
    }
    delivery_mode = expected_delivery_mode or args.delivery_mode
    if delivery_mode:
        checks["delivery_mode"] = (
            payload.get("delivery_mode"),
            delivery_mode,
        )
    recommended = (
        expected_recommended_image_version or args.recommended_image_version
    )
    if recommended:
        checks["recommended_image_version"] = (
            payload.get("recommended_image_version"),
            recommended,
        )
    return [
        f"{name}={actual!r}, expected {expected!r}"
        for name, (actual, expected) in checks.items()
        if actual != expected
    ]


def main() -> int:
    args = parse_args()
    verifier = load_asset_verifier()
    last_errors: list[str] = []
    for attempt in range(1, args.attempts + 1):
        try:
            bridge_manifest_bytes = verifier.fetch_bytes(
                args.url,
                max_bytes=verifier.MAX_MANIFEST_BYTES,
            )
            trusted_bridge = verifier.verify_manifest(bridge_manifest_bytes)
            bridge_url = str(trusted_bridge["payload"].get("bundle_url") or "")
            bridge_bundle = verifier.fetch_bytes(
                bridge_url,
                max_bytes=verifier.MAX_BUNDLE_BYTES,
            )
            bridge_manifest = verifier.verify_update_assets(
                bridge_manifest_bytes,
                bridge_bundle,
                expected_version=args.bridge_version,
                expected_runtime_abi="channelwatch-runtime-v1",
                expected_settings_schema_version=7,
                expected_image_required=False,
                expected_delivery_mode="app_update_with_image_refresh",
                expected_recommended_image_version=args.bridge_version,
            )
            bridge_payload = bridge_manifest["payload"]
            bridge_expected_sha = (
                args.bundle_sha256
                if args.version == args.bridge_version
                else str(bridge_payload.get("bundle_sha256") or "")
            )
            bridge_release_url = (
                "https://github.com/CoderLuii/ChannelWatch/releases/tag/"
                f"v{args.bridge_version}"
            )
            last_errors = verify(
                bridge_manifest,
                args,
                expected_version=args.bridge_version,
                expected_release_url=bridge_release_url,
                expected_bundle_sha256=bridge_expected_sha,
                expected_image_required=False,
                expected_delivery_mode="app_update_with_image_refresh",
                expected_recommended_image_version=args.bridge_version,
            )
            if not args.catalog_url:
                raise ValueError("The signed v2 catalog URL is required.")
            catalog_bytes = verifier.fetch_bytes(
                args.catalog_url,
                max_bytes=verifier.MAX_MANIFEST_BYTES,
            )
            raw_catalog = json.loads(catalog_bytes.decode("utf-8"))
            trusted_catalog = verifier.normalize_catalog(
                raw_catalog,
                public_keys=verifier.UPDATE_PUBLIC_KEYS,
                verify_signature=verifier.verify_ed25519_signature,
                canonical_payload=verifier.canonical_payload_bytes,
                validate_url=verifier.validate_trusted_url,
            )
            current_release = next(
                (
                    item
                    for item in trusted_catalog["payload"]["releases"]
                    if item.get("version") == args.version
                ),
                None,
            )
            if not isinstance(current_release, dict):
                raise ValueError("The signed v2 catalog is missing the current release.")
            current_bundle = verifier.fetch_bytes(
                str(current_release.get("bundle_url") or ""),
                max_bytes=verifier.MAX_BUNDLE_BYTES,
            )
            verifier.verify_update_catalog(
                catalog_bytes,
                current_bundle,
                expected_version=args.version,
                expected_delivery_mode=(args.delivery_mode or "app_update"),
                expected_runtime_abi="channelwatch-runtime-v1",
                expected_settings_schema_version=7,
                expected_recommended_image_version=(
                    args.recommended_image_version or args.version
                ),
            )
            last_errors.extend(
                verify(
                    {"payload": current_release},
                    args,
                    expected_bundle_sha256=args.bundle_sha256,
                )
            )
            if not last_errors:
                print(
                    f"Live v1 bridge v{args.bridge_version} and v2 release "
                    f"v{args.version} verified on attempt {attempt}."
                )
                return 0
        except Exception as exc:
            last_errors = [str(exc)]
        if attempt < args.attempts:
            time.sleep(args.interval)
    print(
        "live stable manifest verification failed: " + "; ".join(last_errors),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
