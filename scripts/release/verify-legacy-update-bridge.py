#!/usr/bin/env python3
"""Run v0.9.11-v0.9.17 trust/allowlist code against the v0.9.18 bridge.

The immutable published v0.9.9 and v0.9.10 images cannot activate this bundle
reliably and are explicit image-pull-only sources. They must never be counted
as bridge evidence.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TAGS = tuple(f"v0.9.{patch}" for patch in range(11, 18))
IMAGE_PULL_ONLY_TAGS = ("v0.9.9", "v0.9.10")


def _git_archive(tag: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", tag, "app/core"],
        check=True,
        capture_output=True,
    ).stdout


def _extract_trusted_archive(data: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            target.relative_to(destination.resolve())
        archive.extractall(destination, filter="data")


def image_pull_only_exception(tag: str) -> dict[str, object]:
    """Return the explicit immutable-image exception, never activation proof."""

    if tag not in IMAGE_PULL_ONLY_TAGS:
        raise ValueError(f"{tag} is not an image-pull-only source.")
    return {
        "tag": tag,
        "support": "image_pull_only",
        "required_image_version": "0.9.18",
        "preserve_config": True,
        "in_app_update_supported": False,
        "published_image_guard_reachable": False,
        "reason": "published_image_cannot_activate_bridge_bundle",
    }


def verify_tag(
    tag: str,
    *,
    manifest_path: Path,
    bundle_path: Path,
    expected_version: str,
    public_keys: dict[str, str] | None = None,
) -> dict[str, object]:
    if tag in IMAGE_PULL_ONLY_TAGS:
        raise RuntimeError(
            f"{tag} is image-pull-only and must not be reported as successful "
            "legacy bridge evidence."
        )
    if tag not in DEFAULT_TAGS:
        raise RuntimeError(f"{tag} is outside the audited legacy bridge matrix.")
    with tempfile.TemporaryDirectory(prefix="channelwatch-legacy-bridge-") as temp:
        checkout = Path(temp)
        _extract_trusted_archive(_git_archive(tag), checkout)
        config_dir = checkout / "config"
        config_dir.mkdir()
        probe = """
import json
import os
import sys
from pathlib import Path
os.environ["CONFIG_PATH"] = sys.argv[5]
os.environ["CHANNELWATCH_IMAGE_VERSION"] = sys.argv[6]
from core.update_center import (
    RUNTIME_ABI,
    UPDATE_PUBLIC_KEYS,
    UpdateManager,
    load_json,
    read_manifest_bytes,
    validate_bundle_archive,
)
override_keys = json.loads(sys.argv[4])
manifest = read_manifest_bytes(
    Path(sys.argv[1]).read_bytes(), override_keys or UPDATE_PUBLIC_KEYS
)
metadata = validate_bundle_archive(
    Path(sys.argv[2]).read_bytes(),
    expected_version=sys.argv[3],
    expected_runtime_abi=RUNTIME_ABI,
    expected_settings_schema_version=7,
)
manifest_bytes = Path(sys.argv[1]).read_bytes()
bundle_bytes = Path(sys.argv[2]).read_bytes()
manager = UpdateManager(
    config_dir=Path(sys.argv[5]),
    current_version=sys.argv[6],
    settings_schema_version=7,
    public_keys=override_keys or UPDATE_PUBLIC_KEYS,
    fetcher=lambda url, _limit: bundle_bytes if url.endswith(".zip") else manifest_bytes,
    restart_callable=lambda: True,
)
checked = manager.check()
applied = manager.apply(sys.argv[3])
journal_replayed = False
restart_path = Path(sys.argv[5]) / "channelwatch-runtime" / "restart-required.json"
if restart_path.is_file():
    from core import runtime_launcher
    runtime_launcher.apply_restart_journal()
    journal_replayed = True
active = load_json(Path(sys.argv[5]) / "channelwatch-runtime" / "active.json", None)
if not isinstance(active, dict) or active.get("version") != sys.argv[3]:
    raise RuntimeError("legacy updater did not select the verified v0.9.18 bundle")
print(json.dumps({
    "manifest_version": manifest["payload"]["version"],
    "metadata": metadata,
    "check_status": checked.get("last_job", {}).get("status"),
    "apply_status": applied.get("status"),
    "active_version": active.get("version"),
    "journal_replayed": journal_replayed,
}))
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
                str(manifest_path.resolve()),
                str(bundle_path.resolve()),
                expected_version,
                json.dumps(public_keys or {}),
                str(config_dir),
                tag.lstrip("v"),
            ],
            cwd=checkout,
            env={**os.environ, "PYTHONPATH": str(checkout / "app")},
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{tag} rejected the v0.9.18 bridge: {result.stderr.strip()}"
            )
        parsed = json.loads(result.stdout)
        return {
            "tag": tag,
            "manifest_version": parsed["manifest_version"],
            "bundle_version": parsed["metadata"]["version"],
            "check_status": parsed["check_status"],
            "apply_status": parsed["apply_status"],
            "applied_active_version": parsed["active_version"],
            "active_version": parsed["active_version"],
            "journal_replayed": parsed["journal_replayed"],
            "source_acceptance": "verified",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--version", default="0.9.18")
    parser.add_argument("--tag", action="append", dest="tags")
    parser.add_argument(
        "--public-key",
        action="append",
        help="Test-only key-id=base64 public key override; omit for official release verification.",
    )
    args = parser.parse_args()

    requested_tags = tuple(args.tags or DEFAULT_TAGS)
    image_only_requested = sorted(set(requested_tags) & set(IMAGE_PULL_ONLY_TAGS))
    if image_only_requested:
        raise RuntimeError(
            ", ".join(image_only_requested)
            + " is image-pull-only and cannot be approved by the bundle "
            "bridge verifier."
        )

    raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    payload = raw_manifest.get("payload", {})
    required_bridge = {
        "version": args.version,
        "image_required": False,
        "delivery_mode": "app_update_with_image_refresh",
        "minimum_image_version": "0.9.11",
        "updater_protocol": 2,
        "recommended_image_version": args.version,
    }
    mismatches = {
        name: (payload.get(name), expected)
        for name, expected in required_bridge.items()
        if payload.get(name) != expected
    }
    if mismatches:
        raise RuntimeError(f"Legacy bridge fields do not match: {mismatches}")

    public_keys: dict[str, str] | None = None
    if args.public_key:
        public_keys = {}
        for item in args.public_key:
            key_id, separator, value = item.partition("=")
            if not separator or not key_id or not value:
                raise ValueError("--public-key must use key-id=base64 format.")
            public_keys[key_id] = value

    results = [
        verify_tag(
            tag,
            manifest_path=args.manifest,
            bundle_path=args.bundle,
            expected_version=args.version,
            public_keys=public_keys,
        )
        for tag in requested_tags
    ]
    print(
        json.dumps(
            {
                "schema": 1,
                "image_pull_only": [
                    image_pull_only_exception(tag) for tag in IMAGE_PULL_ONLY_TAGS
                ],
                "legacy_bridge": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"legacy update bridge verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
