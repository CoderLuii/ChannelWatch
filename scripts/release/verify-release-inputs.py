#!/usr/bin/env python3
"""Check source-derived catalog inputs before preparing native artifacts."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def load_builder(root: Path):
    path = root / "scripts/release/build-update-bundle.py"
    spec = importlib.util.spec_from_file_location("release_input_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_inputs(root: Path, *, builder=None) -> dict:
    builder = builder or load_builder(root)
    config = json.loads((root / "scripts/release/release-config.json").read_text(encoding="utf-8"))
    builder.validate_release_config(config)
    version = config["version"]
    history = builder.load_catalog_history(version)
    tags = subprocess.run(["git", "-C", str(root), "tag", "--list"],
                          check=True, capture_output=True, text=True).stdout.splitlines()
    builder.require_previous_catalog_release(version, history, tags,
        unpublished_predecessor_tags=config.get("unpublished_predecessor_tags"), repo_root=root)
    # This checks source metadata, not artifact bytes or production signatures.
    # The isolated key never leaves this process and is not a trusted update key.
    key = Ed25519PrivateKey.generate()
    release = {
        **config, "version_tag": f"v{version}", "channel": "stable",
        "runtime_abi": builder.RUNTIME_ABI, "settings_schema_version": builder.SETTINGS_SCHEMA_VERSION,
        "release_url": f"https://github.com/CoderLuii/ChannelWatch/releases/tag/v{version}",
        "bundle_url": f"https://github.com/CoderLuii/ChannelWatch/releases/download/v{version}/channelwatch-app-v{version}.zip",
        "bundle_sha256": "0" * 64, "bundle_signature": base64.b64encode(key.sign(bytes(32))).decode("ascii"),
        "key_id": "local-input-check", "published_at": config["publication_time"],
        "highlights": builder.release_highlights(version), "revocation_state": "active",
        "compatible_runtime_abis": [builder.RUNTIME_ABI],
        "compatible_settings_schema_versions": [builder.SETTINGS_SCHEMA_VERSION],
        "image_refresh_recommended": config["delivery_mode"] == "app_update_with_image_refresh",
    }
    payload = {"channel": "stable", "published_at": config["publication_time"],
               "releases": [builder.project_current_release_v2(release, config.get("legacy_app_update_sources")),
                            *builder.project_catalog_history_v2(history)]}
    signature = key.sign(builder.canonical_payload_bytes(payload))
    catalog = {"schema": 2, "payload": payload, "signature": {
        "alg": "ed25519", "key_id": "local-input-check", "value": base64.b64encode(signature).decode("ascii")}}
    from core.update_catalog import normalize_catalog
    normalize_catalog(catalog, public_keys={"local-input-check": key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)},
        verify_signature=lambda public_keys, key_id, value, data: key.public_key().verify(
            base64.b64decode(value), data),
        canonical_payload=builder.canonical_payload_bytes, validate_url=builder.validate_trusted_url)
    return {"status": "passed", "scope": "catalog-inputs-only", "catalog_schema": 2,
            "version": version, "release_count": len(payload["releases"]), "highlights": len(release["highlights"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        print(json.dumps(validate_inputs(args.root.resolve())))
    except (ValueError, OSError, TypeError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Release inputs rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
