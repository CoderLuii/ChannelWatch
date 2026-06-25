#!/usr/bin/env python3
"""Build and sign a ChannelWatch app update bundle."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ABI = "channelwatch-runtime-v1"
SETTINGS_SCHEMA_VERSION = 7
DEFAULT_KEY_ID = "channelwatch-update-ed25519-2026-06"
BLOCKED_DIRS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "tests",
}
BLOCKED_SUFFIXES = {".pyc", ".pyo"}


def run_git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def load_private_key(raw: str) -> Ed25519PrivateKey:
    value = raw.strip()
    if not value:
        raise ValueError("Signing key is empty.")
    if value.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(value.encode("utf-8"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Signing key must be an Ed25519 private key.")
        return key
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("Signing key must be PEM or base64 raw Ed25519 private key bytes.") from exc
    if len(decoded) != 32:
        raise ValueError("Raw Ed25519 private key must be 32 bytes.")
    return Ed25519PrivateKey.from_private_bytes(decoded)


def copy_tree(src: Path, dest: Path) -> None:
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if any(part in BLOCKED_DIRS for part in rel.parts):
            continue
        if path.is_dir():
            continue
        if path.suffix in BLOCKED_SUFFIXES or path.name in {"AGENTS.md", "RELEASE.md"}:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def write_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(source).as_posix()
            zf.write(path, rel)


def canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(path: Path) -> bytes:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--out-dir", default="dist/update")
    parser.add_argument("--key-id", default=DEFAULT_KEY_ID)
    parser.add_argument("--bundle-url", required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument(
        "--image-required",
        action="store_true",
        help="Mark the release as requiring a normal container image update.",
    )
    parser.add_argument("--signing-key-env", default="CHANNELWATCH_UPDATE_SIGNING_KEY")
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    out_dir = (ROOT / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    signing_key_value = os.environ.get(args.signing_key_env, "")
    private_key = load_private_key(signing_key_value)

    bundle_name = f"channelwatch-app-v{version}.zip"
    bundle_path = out_dir / bundle_name
    manifest_path = out_dir / f"channelwatch-update-v{version}.json"
    git_sha = run_git("rev-parse", "HEAD") or "unknown"
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp) / "bundle"
        staging.mkdir()
        copy_tree(ROOT / "app" / "core", staging / "core")
        copy_tree(ROOT / "app" / "ui" / "backend", staging / "ui" / "backend")

        ui_out = ROOT / "app" / "ui" / "out"
        if not ui_out.is_dir():
            raise ValueError("app/ui/out is missing. Run the UI build before packaging the update bundle.")
        copy_tree(ui_out, staging / "ui" / "backend" / "static_ui")

        image_dir = ROOT / "app" / "ui" / "public" / "images"
        if image_dir.is_dir():
            copy_tree(image_dir, staging / "ui" / "backend" / "static" / "images")

        metadata = {
            "version": version,
            "version_tag": f"v{version}",
            "runtime_abi": RUNTIME_ABI,
            "settings_schema_version": SETTINGS_SCHEMA_VERSION,
            "git_sha": git_sha,
            "created_at": created_at,
            "bundle_type": "channelwatch-app",
        }
        (staging / "channelwatch-bundle.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        write_zip(staging, bundle_path)

    digest = sha256_bytes(bundle_path)
    bundle_signature = base64.b64encode(private_key.sign(digest)).decode("ascii")
    if args.image_required:
        highlights = [
            "This release requires a normal container image update.",
            "It repairs runtime startup, settings migration metadata, Windows-edited settings files, and blank DVR names.",
        ]
    else:
        highlights = [
            "Compatible app updates can be checked and applied from Settings -> Updates.",
            "Pre-update backup, signed bundle verification, restart activation, and rollback support are built in.",
        ]

    payload = {
        "version": version,
        "version_tag": f"v{version}",
        "channel": "stable",
        "runtime_abi": RUNTIME_ABI,
        "settings_schema_version": SETTINGS_SCHEMA_VERSION,
        "image_required": bool(args.image_required),
        "release_url": args.release_url,
        "bundle_url": args.bundle_url,
        "bundle_sha256": digest.hex(),
        "bundle_signature": bundle_signature,
        "key_id": args.key_id,
        "published_at": created_at,
        "highlights": highlights,
    }
    manifest = {
        "schema": 1,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": args.key_id,
            "value": base64.b64encode(private_key.sign(canonical_payload_bytes(payload))).decode("ascii"),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(bundle_path), "manifest": str(manifest_path), "sha256": digest.hex()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
