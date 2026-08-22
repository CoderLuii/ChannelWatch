#!/usr/bin/env python3
"""Verify a signed update manifest and bundle with the runtime trust path."""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.update_center import (  # noqa: E402
    MAX_BUNDLE_BYTES,
    MAX_MANIFEST_BYTES,
    UPDATE_PUBLIC_KEYS,
    UpdateBundleError,
    UpdateCenterError,
    fetch_bytes,
    read_manifest_bytes,
    sha256_hex,
    validate_bundle_archive,
    verify_ed25519_signature,
)

CRITICAL_SOURCE_MEMBERS = {
    "core/__init__.py": Path("app/core/__init__.py"),
    "core/docker-entrypoint.py": Path("app/core/docker-entrypoint.py"),
    "core/helpers/atomic_io.py": Path("app/core/helpers/atomic_io.py"),
    "core/helpers/migration.py": Path("app/core/helpers/migration.py"),
    "core/main.py": Path("app/core/main.py"),
    "core/runtime_launcher.py": Path("app/core/runtime_launcher.py"),
    "core/update_center.py": Path("app/core/update_center.py"),
    "ui/backend/main.py": Path("app/ui/backend/main.py"),
}


def verify_manifest(
    manifest_bytes: bytes,
    *,
    public_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    keys = UPDATE_PUBLIC_KEYS if public_keys is None else public_keys
    return read_manifest_bytes(manifest_bytes, keys)


def verify_update_assets(
    manifest_bytes: bytes,
    bundle_bytes: bytes,
    *,
    public_keys: dict[str, str] | None = None,
    expected_version: str | None = None,
    expected_channel: str | None = None,
    expected_runtime_abi: str | None = None,
    expected_settings_schema_version: int | None = None,
    expected_image_required: bool | None = None,
    expected_git_sha: str | None = None,
    expected_release_url: str | None = None,
    expected_bundle_url: str | None = None,
    expected_source_root: Path | None = None,
) -> dict[str, Any]:
    keys = UPDATE_PUBLIC_KEYS if public_keys is None else public_keys
    manifest = read_manifest_bytes(manifest_bytes, keys)
    payload = manifest["payload"]
    version = str(payload["version"]).lstrip("v")
    if expected_version and version != expected_version.strip().lstrip("v"):
        raise UpdateBundleError(
            f"Update manifest version {version} does not match expected version "
            f"{expected_version.strip().lstrip('v')}."
        )

    try:
        raw_manifest = json.loads(manifest_bytes.decode("utf-8"))
        raw_payload = raw_manifest["payload"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise UpdateBundleError("Update manifest payload could not be inspected.") from exc
    if not isinstance(raw_payload, dict):
        raise UpdateBundleError("Update manifest payload must be an object.")
    expected_version_tag = f"v{version}"
    if raw_payload.get("version_tag") != expected_version_tag:
        raise UpdateBundleError(
            "Update manifest version_tag does not match the release version."
        )
    for name, expected in (
        ("channel", expected_channel),
        ("runtime_abi", expected_runtime_abi),
    ):
        if expected is None:
            continue
        actual = raw_payload.get(name)
        if not isinstance(actual, str):
            raise UpdateBundleError(f"Update manifest {name} must be a string.")
        if actual != expected:
            raise UpdateBundleError(
                f"Update manifest {name} does not match the release contract."
            )
    if expected_settings_schema_version is not None:
        actual_schema = raw_payload.get("settings_schema_version")
        if type(actual_schema) is not int:  # bool and numeric strings are not versions
            raise UpdateBundleError(
                "Update manifest settings_schema_version must be an integer."
            )
        if actual_schema != expected_settings_schema_version:
            raise UpdateBundleError(
                "Update manifest settings_schema_version does not match the release "
                "contract."
            )
    if expected_image_required is not None:
        actual_image_required = raw_payload.get("image_required")
        if type(actual_image_required) is not bool:  # bool, not truthy strings or integers
            raise UpdateBundleError(
                "Update manifest image_required must be an explicit boolean."
            )
        if actual_image_required is not expected_image_required:
            raise UpdateBundleError(
                "Update manifest image_required does not match the release contract."
            )
    for name, expected in (
        ("release_url", expected_release_url),
        ("bundle_url", expected_bundle_url),
    ):
        if expected is not None and raw_payload.get(name) != expected:
            raise UpdateBundleError(
                f"Update manifest {name} does not match the release contract."
            )

    expected_hash = str(payload.get("bundle_sha256") or "").lower()
    actual_hash = sha256_hex(bundle_bytes)
    if not expected_hash or actual_hash != expected_hash:
        raise UpdateBundleError("Update bundle hash did not match manifest.")

    key_id = str(payload.get("key_id") or "")
    signature = str(payload.get("bundle_signature") or "")
    if not key_id or not signature:
        raise UpdateBundleError("Update bundle signature is incomplete.")
    verify_ed25519_signature(keys, key_id, signature, bytes.fromhex(actual_hash))
    manifest_runtime_abi = str(payload.get("runtime_abi") or "")
    manifest_settings_schema_version = int(
        payload.get("settings_schema_version") or 0
    )
    metadata = validate_bundle_archive(
        bundle_bytes,
        expected_version=version,
        expected_runtime_abi=(
            expected_runtime_abi
            if expected_runtime_abi is not None
            else manifest_runtime_abi
        ),
        expected_settings_schema_version=(
            expected_settings_schema_version
            if expected_settings_schema_version is not None
            else manifest_settings_schema_version
        ),
    )
    if expected_runtime_abi is not None and not isinstance(
        metadata.get("runtime_abi"), str
    ):
        raise UpdateBundleError("Update bundle metadata runtime_abi must be a string.")
    if expected_settings_schema_version is not None and type(
        metadata.get("settings_schema_version")
    ) is not int:
        raise UpdateBundleError(
            "Update bundle metadata settings_schema_version must be an integer."
        )

    if expected_git_sha is not None:
        expected_sha = expected_git_sha.strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None:
            raise UpdateBundleError(
                "Expected release Git SHA must be a complete 40-character SHA."
            )
        required_metadata = {
            "version_tag": expected_version_tag,
            "bundle_type": "channelwatch-app",
            "git_sha": expected_sha,
        }
        for name, expected in required_metadata.items():
            actual = str(metadata.get(name) or "").strip()
            if name == "git_sha":
                actual = actual.lower()
            if actual != expected:
                raise UpdateBundleError(
                    f"Update bundle metadata {name} does not match the release contract."
                )
        if not str(metadata.get("created_at") or "").strip():
            raise UpdateBundleError("Update bundle metadata is missing created_at.")

    if expected_source_root is not None:
        source_root = Path(expected_source_root).resolve()
        try:
            archive = zipfile.ZipFile(io.BytesIO(bundle_bytes), "r")
        except zipfile.BadZipFile as exc:
            raise UpdateBundleError("Update bundle is not a valid zip file.") from exc
        with archive:
            for member, relative_source in CRITICAL_SOURCE_MEMBERS.items():
                source_path = source_root / relative_source
                try:
                    source_bytes = source_path.read_bytes()
                except OSError as exc:
                    raise UpdateBundleError(
                        f"Critical release source is unavailable: {relative_source}."
                    ) from exc
                if expected_git_sha is not None:
                    committed = subprocess.run(
                        [
                            "git",
                            "-C",
                            str(source_root),
                            "show",
                            f"{expected_sha}:{relative_source.as_posix()}",
                        ],
                        capture_output=True,
                        check=False,
                    )
                    if committed.returncode != 0:
                        raise UpdateBundleError(
                            "Critical release source could not be read from exact Git SHA: "
                            f"{relative_source}."
                        )
                    if source_bytes != committed.stdout:
                        raise UpdateBundleError(
                            "Critical working source does not match exact Git SHA: "
                            f"{relative_source}."
                        )
                try:
                    bundled_bytes = archive.read(member)
                except KeyError as exc:
                    raise UpdateBundleError(
                        f"Update bundle is missing critical source member: {member}."
                    ) from exc
                if bundled_bytes != source_bytes:
                    raise UpdateBundleError(
                        f"Update bundle member {member} does not match exact release source."
                    )
    return manifest


def _read_limited_file(path: Path, *, max_bytes: int) -> bytes:
    size = path.stat().st_size
    if size > max_bytes:
        raise UpdateBundleError(f"{path.name} exceeds the {max_bytes}-byte size limit.")
    return path.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-channel", choices=("stable",), required=True)
    parser.add_argument("--expected-runtime-abi", required=True)
    parser.add_argument(
        "--expected-settings-schema-version",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--expected-image-required",
        choices=("true", "false"),
        required=True,
    )
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-release-url", required=True)
    parser.add_argument("--expected-bundle-url", required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        manifest = verify_update_assets(
            _read_limited_file(args.manifest, max_bytes=MAX_MANIFEST_BYTES),
            _read_limited_file(args.bundle, max_bytes=MAX_BUNDLE_BYTES),
            expected_version=args.expected_version,
            expected_channel=args.expected_channel,
            expected_runtime_abi=args.expected_runtime_abi,
            expected_settings_schema_version=args.expected_settings_schema_version,
            expected_image_required=args.expected_image_required == "true",
            expected_git_sha=args.expected_git_sha,
            expected_release_url=args.expected_release_url,
            expected_bundle_url=args.expected_bundle_url,
            expected_source_root=args.source_root,
        )
    except (OSError, ValueError, UpdateCenterError) as exc:
        print(f"update asset verification failed: {exc}", file=sys.stderr)
        return 1
    payload = manifest["payload"]
    print(
        f"Verified signed update assets for v{payload['version']} "
        f"({payload['bundle_sha256']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
