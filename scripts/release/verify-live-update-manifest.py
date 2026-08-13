#!/usr/bin/env python3
"""Wait for and verify the public stable update manifest after deployment."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


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


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "ChannelWatch-Release-Verification"})
    with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310: workflow-owned URL
        return json.load(response)


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
    last_errors: list[str] = []
    for attempt in range(1, args.attempts + 1):
        try:
            last_errors = verify(fetch_json(args.url), args)
            if not last_errors:
                print(f"Live stable manifest verified for v{args.version} on attempt {attempt}.")
                return 0
        except Exception as exc:
            last_errors = [str(exc)]
        if attempt < args.attempts:
            time.sleep(args.interval)
    print("live stable manifest verification failed: " + "; ".join(last_errors), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
