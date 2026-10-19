#!/usr/bin/env python3
"""Verify a signed update manifest and bundle with the runtime trust path."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from render_release_legal import render_document

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.runtime_archive import (
    MAX_BUNDLE_MEMBER_BYTES as MAX_RUNTIME_MEMBER_BYTES,
    MAX_BUNDLE_MEMBER_COUNT as MAX_RUNTIME_MEMBER_COUNT,
    _archive_files as _runtime_archive_files,
)
from core.runtime_catalog import (
    normalize_runtime_release,
    validate_runtime_release_variants,
)
from core.runtime_generation import (
    DEPENDENCY_INVENTORY_PATH,
    DESCRIPTOR_FILENAME,
    RuntimeGenerationError,
    validate_runtime_descriptor_shape,
)
from core.update_catalog import normalize_catalog
from core.update_center import (
    MAX_BUNDLE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_RUNTIME_CATALOG_BYTES,
    UPDATE_PUBLIC_KEYS,
    UpdateBundleError,
    UpdateCenterError,
    canonical_payload_bytes,
    fetch_bytes,  # noqa: F401 - public helper consumed by live verifier
    read_manifest_bytes,
    sha256_hex,
    validate_bundle_archive,
    validate_trusted_url,
    verify_ed25519_signature,
)

LEGACY_PROTOCOL3_APP_UPDATE_CONTRACTS = {
    "1.6.0": ("1.4.0", "1.4.1", "1.4.5"),
    "1.6.1": ("1.4.0", "1.4.1", "1.4.5", "1.6.0"),
}
CANONICAL_PROTOCOL4_APP_UPDATE_CONTRACTS = {
    "1.6.0": ("1.5.3",),
    "1.6.1": ("1.5.3", "1.6.0"),
}
LEGACY_PROTOCOL3_APP_UPDATE_FOUNDATION = "1.4.0"
VERIFIED_DEPENDENCY_FOUNDATION = "1.5.3"

CRITICAL_SOURCE_MEMBERS = {
    "core/__init__.py": Path("app/core/__init__.py"),
    "core/docker-entrypoint.py": Path("app/core/docker-entrypoint.py"),
    "core/helpers/atomic_io.py": Path("app/core/helpers/atomic_io.py"),
    "core/helpers/migration.py": Path("app/core/helpers/migration.py"),
    "core/main.py": Path("app/core/main.py"),
    "core/notification_drain.py": Path("app/core/notification_drain.py"),
    "core/notifications/notification.py": Path(
        "app/core/notifications/notification.py"
    ),
    "core/runtime_launcher.py": Path("app/core/runtime_launcher.py"),
    "core/update_catalog.py": Path("app/core/update_catalog.py"),
    "core/update_center.py": Path("app/core/update_center.py"),
    "core/update_policy.py": Path("app/core/update_policy.py"),
    "core/release_legal/LICENSE": Path("LICENSE"),
    "core/release_legal/NOTICE": Path("docs/legal/NOTICE"),
    "core/release_legal/THIRD_PARTY_LICENSES.md": Path(
        "docs/legal/THIRD_PARTY_LICENSES.md"
    ),
    "ui/backend/main.py": Path("app/ui/backend/main.py"),
}

GENERATED_LEGAL_SOURCE_MEMBERS = {
    "core/release_legal/THIRD_PARTY_LICENSES.md": Path("docs/legal/THIRD_PARTY_LICENSES.md"),
    "core/release_legal/CORRESPONDING_SOURCE.md": Path("docs/legal/CORRESPONDING_SOURCE.md"),
    "core/release_legal/copyleft/CORRESPONDING_SOURCE.md": Path("docs/legal/CORRESPONDING_SOURCE.md"),
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
    expected_delivery_mode: str | None = None,
    expected_recommended_image_version: str | None = None,
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
        raise UpdateBundleError(
            "Update manifest payload could not be inspected."
        ) from exc
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
        if (
            type(actual_image_required) is not bool
        ):  # bool, not truthy strings or integers
            raise UpdateBundleError(
                "Update manifest image_required must be an explicit boolean."
            )
        if actual_image_required is not expected_image_required:
            raise UpdateBundleError(
                "Update manifest image_required does not match the release contract."
            )
    if expected_delivery_mode is not None:
        if raw_payload.get("delivery_mode") != expected_delivery_mode:
            raise UpdateBundleError(
                "Update manifest delivery_mode does not match the release contract."
            )
    if expected_recommended_image_version is not None:
        actual_recommended = (
            str(raw_payload.get("recommended_image_version") or "").strip().lstrip("v")
        )
        if actual_recommended != expected_recommended_image_version.strip().lstrip("v"):
            raise UpdateBundleError(
                "Update manifest recommended_image_version does not match the release contract."
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
    manifest_settings_schema_version = int(payload.get("settings_schema_version") or 0)
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
    if (
        expected_settings_schema_version is not None
        and type(metadata.get("settings_schema_version")) is not int
    ):
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
            # Earlier immutable releases copied legal documents verbatim.
            generated_legal = GENERATED_LEGAL_SOURCE_MEMBERS if tuple(map(int, version.split("."))) >= (1, 1, 0) else {}
            for member, relative_source in {**CRITICAL_SOURCE_MEMBERS, **generated_legal}.items():
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
                if member in generated_legal:
                    source_bytes = render_document(source_bytes.decode("utf-8"), version).encode("utf-8")
                if bundled_bytes != source_bytes:
                    raise UpdateBundleError(
                        f"Update bundle member {member} does not match exact release source."
                    )
    return manifest


def _validated_legacy_app_update_sources(
    raw: object, *, target_version: str
) -> list[str]:
    if raw in (None, []):
        return []
    expected_contract = LEGACY_PROTOCOL3_APP_UPDATE_CONTRACTS.get(target_version)
    if expected_contract is None:
        raise UpdateBundleError(
            "Legacy app-update sources are not allowed for this supported release target."
        )
    expected = list(expected_contract)
    if raw != expected:
        raise UpdateBundleError(
            "Legacy app-update sources do not match the exact published bridge set."
        )
    return expected


def load_legacy_app_update_sources(
    path: Path, *, expected_version: str
) -> list[str]:
    """Load the exact legacy bridge contract from bounded release configuration."""

    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise UpdateBundleError("Release configuration exceeds size limit.")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateBundleError("Release configuration could not be read safely.") from exc
    if not isinstance(raw, dict):
        raise UpdateBundleError("Release configuration must be a JSON object.")
    configured_version = str(raw.get("version") or "").strip().lstrip("v")
    if configured_version != expected_version.strip().lstrip("v"):
        return []
    sources = _validated_legacy_app_update_sources(
        raw.get("legacy_app_update_sources"), target_version=configured_version
    )
    return sources


def verify_legacy_app_release_projection(
    release: dict[str, Any],
    *,
    legacy_app_update_sources: list[str] | None,
    expected_version: str,
) -> None:
    """Verify the only allowed protocol-3 projection of the current release."""

    version = expected_version.strip().lstrip("v")
    sources = _validated_legacy_app_update_sources(
        legacy_app_update_sources, target_version=version
    )
    if not sources:
        return
    expected = {
        "version": version,
        "delivery_mode": "app_update",
        "image_required": False,
        "image_refresh_recommended": False,
        "minimum_image_version": LEGACY_PROTOCOL3_APP_UPDATE_FOUNDATION,
        "updater_protocol": 2,
        "automatic_install_allowed": False,
        "compatible_source_application_versions": sources,
        "compatible_launcher_protocols": [3],
    }
    if any(release.get(name) != value for name, value in expected.items()):
        raise UpdateBundleError(
            "Schema-2 release does not match the exact legacy app-update projection."
        )


def verify_update_catalog(
    catalog_bytes: bytes,
    bundle_bytes: bytes,
    *,
    public_keys: dict[str, str] | None = None,
    expected_version: str,
    expected_delivery_mode: str,
    expected_runtime_abi: str,
    expected_settings_schema_version: int,
    expected_recommended_image_version: str,
    legacy_app_update_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Independently verify schema-2 metadata and its exact signed bundle."""

    if len(catalog_bytes) > MAX_MANIFEST_BYTES:
        raise UpdateBundleError("Update catalog exceeds size limit.")
    try:
        raw = json.loads(catalog_bytes.decode("utf-8"))
        catalog = normalize_catalog(
            raw,
            public_keys=UPDATE_PUBLIC_KEYS if public_keys is None else public_keys,
            verify_signature=verify_ed25519_signature,
            canonical_payload=canonical_payload_bytes,
            validate_url=validate_trusted_url,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        UpdateCenterError,
    ) as exc:
        raise UpdateBundleError("Signed update catalog is invalid.") from exc
    normalized_version = expected_version.strip().lstrip("v")
    release = next(
        (
            item
            for item in catalog["payload"]["releases"]
            if item["version"] == normalized_version
        ),
        None,
    )
    if not isinstance(release, dict):
        raise UpdateBundleError("Update catalog is missing the expected release.")
    expected_fields = {
        "delivery_mode": expected_delivery_mode,
        "runtime_abi": expected_runtime_abi,
        "settings_schema_version": expected_settings_schema_version,
        "recommended_image_version": expected_recommended_image_version.strip().lstrip(
            "v"
        ),
        "revocation_state": "active",
    }
    for name, expected in expected_fields.items():
        if release.get(name) != expected:
            raise UpdateBundleError(
                f"Update catalog {name} does not match the release contract."
            )
    verify_legacy_app_release_projection(
        release,
        legacy_app_update_sources=legacy_app_update_sources,
        expected_version=normalized_version,
    )
    actual_hash = sha256_hex(bundle_bytes)
    if release.get("bundle_sha256") != actual_hash:
        raise UpdateBundleError("Update catalog bundle hash did not match.")
    verify_ed25519_signature(
        UPDATE_PUBLIC_KEYS if public_keys is None else public_keys,
        str(release.get("key_id") or ""),
        str(release.get("bundle_signature") or ""),
        bytes.fromhex(actual_hash),
    )
    validate_bundle_archive(
        bundle_bytes,
        expected_version=normalized_version,
        expected_runtime_abi=expected_runtime_abi,
        expected_settings_schema_version=expected_settings_schema_version,
    )
    return catalog


def _verify_runtime_archive_bytes(
    bundle: bytes, descriptor: dict[str, Any]
) -> None:
    if not bundle or len(bundle) > MAX_BUNDLE_BYTES:
        raise UpdateBundleError("Runtime bundle exceeds its bounded download size.")
    try:
        validate_runtime_descriptor_shape(descriptor)
    except RuntimeGenerationError as exc:
        raise UpdateBundleError(str(exc)) from exc
    try:
        archive = zipfile.ZipFile(io.BytesIO(bundle), "r")
    except zipfile.BadZipFile as exc:
        raise UpdateBundleError("Runtime bundle is not a valid ZIP archive.") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_RUNTIME_MEMBER_COUNT:
            raise UpdateBundleError("Runtime bundle has too many members.")
        descriptor_members = [
            info for info in members if info.filename == DESCRIPTOR_FILENAME
        ]
        if len(descriptor_members) != 1 or not (
            0 <= descriptor_members[0].file_size <= MAX_RUNTIME_MEMBER_BYTES
        ):
            raise UpdateBundleError("Runtime bundle descriptor member is invalid.")
        selected = _runtime_archive_files(archive, descriptor)
        if archive.testzip() is not None:
            raise UpdateBundleError("Runtime bundle ZIP integrity check failed.")
        try:
            embedded = json.loads(
                archive.read(selected[DESCRIPTOR_FILENAME]).decode("utf-8")
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise UpdateBundleError("Runtime bundle descriptor is invalid.") from exc
        if embedded != descriptor:
            raise UpdateBundleError(
                "Runtime bundle descriptor does not match the signed catalog."
            )
        inventory = archive.read(selected[DEPENDENCY_INVENTORY_PATH])
        if hashlib.sha256(inventory).hexdigest() != descriptor[
            "dependency_inventory_sha256"
        ]:
            raise UpdateBundleError("Runtime bundle inventory hash did not match.")
        expanded_size = len(inventory)
        for record in descriptor["files"]:
            content = archive.read(selected[record["path"]])
            if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != (
                record["sha256"]
            ):
                raise UpdateBundleError("Runtime bundle payload hash did not match.")
            expanded_size += len(content)
        if expanded_size != descriptor["expanded_size"]:
            raise UpdateBundleError("Runtime bundle expanded size did not match.")


def verify_runtime_update_catalog(
    catalog_bytes: bytes,
    runtime_bundles: dict[str, bytes],
    *,
    public_keys: dict[str, str] | None = None,
    expected_version: str,
    expected_source_commit: str,
    expected_bundle_urls: dict[str, str],
) -> dict[str, Any]:
    """Verify a signed protocol-4 bootstrap or dependency catalog."""

    keys = UPDATE_PUBLIC_KEYS if public_keys is None else public_keys
    if len(catalog_bytes) > MAX_RUNTIME_CATALOG_BYTES:
        raise UpdateBundleError("Runtime update catalog exceeds its size limit.")
    if re.fullmatch(r"[0-9a-f]{40}", expected_source_commit) is None:
        raise UpdateBundleError(
            "Expected runtime source commit must be a full lowercase Git SHA."
        )
    try:
        raw = json.loads(catalog_bytes.decode("utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != 3:
            raise ValueError("Runtime update catalog schema is unsupported.")
        payload = raw["payload"]
        signature = raw["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, dict):
            raise TypeError("Runtime update catalog envelope is incomplete.")
        if signature.get("alg") != "ed25519":
            raise ValueError("Runtime update catalog signature algorithm is unsupported.")
        verify_ed25519_signature(
            keys,
            str(signature.get("key_id") or ""),
            str(signature.get("value") or ""),
            canonical_payload_bytes(payload),
        )
        releases = payload.get("releases")
        if not isinstance(releases, list):
            raise TypeError("Runtime update catalog releases are invalid.")
        normalized_version = expected_version.strip().lstrip("v")
        matches = [
            release
            for release in releases
            if isinstance(release, dict)
            and str(release.get("version") or "").strip().lstrip("v")
            == normalized_version
        ]
        if len(matches) != 1:
            raise ValueError("Runtime update catalog target release is not unique.")
        release = matches[0]
        for catalog_release in releases:
            if not isinstance(catalog_release, dict):
                continue
            if catalog_release.get("delivery_mode") == "image_required":
                if catalog_release is release:
                    continue
                if (
                    "runtime_variants" in catalog_release
                    or "runtime_descriptor" in catalog_release
                ):
                    raise ValueError(
                        "Historical image-only releases cannot contain runtime generations."
                    )
                continue
            if "runtime_variants" in catalog_release:
                validate_runtime_release_variants(
                    catalog_release, validate_url=validate_trusted_url
                )
        for historical_release in releases:
            if (
                historical_release is release
                or not isinstance(historical_release, dict)
                or historical_release.get("delivery_mode") == "image_required"
                or "runtime_variants" not in historical_release
            ):
                continue
            for historical_variant in historical_release["runtime_variants"]:
                verify_ed25519_signature(
                    keys,
                    str(historical_variant.get("key_id") or ""),
                    str(historical_variant.get("bundle_signature") or ""),
                    bytes.fromhex(str(historical_variant["bundle_sha256"])),
                )
        variants = release.get("runtime_variants")
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, UpdateCenterError):
            raise
        raise UpdateBundleError("Signed runtime update catalog is invalid.") from exc

    if release.get("compatible_launcher_protocols") != [4]:
        raise UpdateBundleError("Runtime update catalog must target protocol 4.")
    image_bootstrap = (
        release.get("delivery_mode") == "image_required"
        and release.get("image_required") is True
    )
    if image_bootstrap:
        if "runtime_variants" in release or "runtime_descriptor" in release:
            raise UpdateBundleError(
                "Image-only runtime catalog cannot contain a runtime generation."
            )
        if runtime_bundles or expected_bundle_urls:
            raise UpdateBundleError(
                "Image-only runtime catalog cannot verify runtime generation assets."
            )
        try:
            normalized = normalize_runtime_release(
                release, platform=None, validate_url=validate_trusted_url
            )
        except (TypeError, ValueError) as exc:
            raise UpdateBundleError(
                "Image-only runtime catalog release is invalid."
            ) from exc
        if normalized["delivery_mode"] != "image_required":
            raise UpdateBundleError(
                "Image-only runtime catalog cannot be applied as an app update."
            )
        return raw
    if (
        release.get("delivery_mode") == "image_required"
        or release.get("image_required") is not False
    ):
        raise UpdateBundleError("Runtime update catalog classification is inconsistent.")
    if not isinstance(variants, list) or len(variants) != 2:
        raise UpdateBundleError("Runtime update catalog must contain two native variants.")
    if set(runtime_bundles) != {"amd64", "arm64"} or set(expected_bundle_urls) != {
        "amd64",
        "arm64",
    }:
        raise UpdateBundleError(
            "Runtime update verification requires exactly amd64 and arm64 assets."
        )
    for url in expected_bundle_urls.values():
        validate_trusted_url(url)

    by_architecture: dict[str, dict[str, Any]] = {}
    interpreter_identity: tuple[object, ...] | None = None
    for variant in variants:
        descriptor = variant["runtime_descriptor"]
        architecture = descriptor["architecture"]
        if architecture in by_architecture:
            raise UpdateBundleError("Runtime catalog contains a duplicate architecture.")
        by_architecture[architecture] = variant
        if descriptor["source_commit"] != expected_source_commit:
            raise UpdateBundleError("Runtime descriptor source commit does not match.")
        identity = (
            descriptor["python_implementation"],
            descriptor["python_cache_tag"],
            descriptor["python_version"],
            descriptor["os_family"],
            descriptor["libc_family"],
            tuple(descriptor["minimum_libc_version"]),
        )
        if interpreter_identity is None:
            interpreter_identity = identity
        elif identity != interpreter_identity:
            raise UpdateBundleError(
                "Runtime descriptor interpreter and system bindings do not match."
            )
    if set(by_architecture) != {"amd64", "arm64"}:
        raise UpdateBundleError("Runtime catalog architectures are incomplete.")

    for architecture in ("amd64", "arm64"):
        variant = by_architecture[architecture]
        if variant["bundle_url"] != expected_bundle_urls[architecture]:
            raise UpdateBundleError("Runtime bundle URL does not match the release contract.")
        bundle = runtime_bundles[architecture]
        actual_hash = sha256_hex(bundle)
        if variant["bundle_sha256"] != actual_hash:
            raise UpdateBundleError("Runtime bundle hash did not match the catalog.")
        verify_ed25519_signature(
            keys,
            str(variant.get("key_id") or ""),
            str(variant.get("bundle_signature") or ""),
            bytes.fromhex(actual_hash),
        )
        _verify_runtime_archive_bytes(bundle, variant["runtime_descriptor"])
    return raw


def verify_runtime_catalog_binding(
    app_catalog: dict[str, Any],
    runtime_catalog: dict[str, Any],
    *,
    expected_version: str,
    legacy_app_update_sources: list[str] | None = None,
) -> None:
    """Bind independently verified catalogs to the same signed app history."""

    app_payload = app_catalog["payload"]
    runtime_payload = runtime_catalog["payload"]
    for name in ("channel", "published_at"):
        if app_payload.get(name) != runtime_payload.get(name):
            raise UpdateBundleError("Runtime and app catalog publication fields differ.")
    normalized_version = expected_version.strip().lstrip("v")
    release_maps = []
    for payload in (app_payload, runtime_payload):
        releases = payload["releases"]
        by_version = {
            str(release.get("version") or "").strip().lstrip("v"): release
            for release in releases
            if isinstance(release, dict)
        }
        if len(by_version) != len(releases) or normalized_version not in by_version:
            raise UpdateBundleError("Catalog target release is not unique.")
        release_maps.append(by_version)
    if release_maps[0].keys() != release_maps[1].keys():
        raise UpdateBundleError("Runtime and app catalog release histories differ.")
    legacy_sources = _validated_legacy_app_update_sources(
        legacy_app_update_sources,
        target_version=expected_version.strip().lstrip("v"),
    )
    for version, app_release in release_maps[0].items():
        runtime_release = {
            name: value
            for name, value in release_maps[1][version].items()
            if name != "runtime_variants"
        }
        if version == normalized_version and legacy_sources:
            verify_legacy_app_release_projection(
                app_release,
                legacy_app_update_sources=legacy_sources,
                expected_version=normalized_version,
            )
            canonical_sources = CANONICAL_PROTOCOL4_APP_UPDATE_CONTRACTS.get(
                normalized_version
            )
            if canonical_sources is None:
                raise UpdateBundleError(
                    "Runtime catalog target is not an approved protocol-4 release."
                )
            expected_runtime = {
                "version": normalized_version,
                "delivery_mode": "app_update",
                "image_required": False,
                "image_refresh_recommended": False,
                "minimum_image_version": VERIFIED_DEPENDENCY_FOUNDATION,
                "updater_protocol": 4,
                "automatic_install_allowed": False,
                "compatible_source_application_versions": list(canonical_sources),
                "compatible_launcher_protocols": [4],
            }
            if any(
                runtime_release.get(name) != value
                for name, value in expected_runtime.items()
            ):
                raise UpdateBundleError(
                    "Runtime catalog does not match the protocol-4 release contract."
                )
            projected_fields = {
                "minimum_image_version",
                "updater_protocol",
                "compatible_source_application_versions",
                "compatible_launcher_protocols",
            }
            app_shared = {
                name: value
                for name, value in app_release.items()
                if name not in projected_fields
            }
            runtime_shared = {
                name: value
                for name, value in runtime_release.items()
                if name not in projected_fields
            }
            if app_shared != runtime_shared:
                raise UpdateBundleError(
                    "Legacy and runtime catalogs do not share the same signed app asset."
                )
        elif app_release != runtime_release:
            raise UpdateBundleError("Runtime catalog does not match the signed app release.")


def _read_limited_file(path: Path, *, max_bytes: int) -> bytes:
    size = path.stat().st_size
    if size > max_bytes:
        raise UpdateBundleError(f"{path.name} exceeds the {max_bytes}-byte size limit.")
    return path.read_bytes()


def _architecture_values(values: list[str] | None, *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        architecture, separator, item = value.partition("=")
        if (
            not separator
            or architecture not in {"amd64", "arm64"}
            or not item
            or architecture in result
        ):
            raise UpdateBundleError(
                f"{label} must provide one unique amd64=value and arm64=value pair."
            )
        result[architecture] = item
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--runtime-catalog", type=Path)
    parser.add_argument(
        "--runtime-bundle",
        action="append",
        help="Runtime archive as amd64=path or arm64=path.",
    )
    parser.add_argument(
        "--expected-runtime-bundle-url",
        action="append",
        help="Expected runtime URL as amd64=url or arm64=url.",
    )
    parser.add_argument("--expected-runtime-source-commit")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-channel", choices=("stable",), required=True)
    parser.add_argument("--expected-runtime-abi", required=True)
    parser.add_argument(
        "--expected-settings-schema-version",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--expected-delivery-mode",
        choices=("app_update", "app_update_with_image_refresh", "image_required"),
    )
    parser.add_argument("--expected-recommended-image-version")
    parser.add_argument(
        "--expected-image-required",
        choices=("true", "false"),
        required=True,
    )
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-release-url", required=True)
    parser.add_argument("--expected-bundle-url", required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument(
        "--release-config",
        type=Path,
        default=ROOT / "scripts" / "release" / "release-config.json",
    )
    parser.add_argument(
        "--public-key",
        action="append",
        help=(
            "Test-only key-id=base64 public key override; omit for official "
            "candidate and release verification."
        ),
    )
    args = parser.parse_args()
    public_keys: dict[str, str] | None = None
    if args.public_key:
        public_keys = {}
        for item in args.public_key:
            key_id, separator, value = item.partition("=")
            if not separator or not key_id or not value:
                print(
                    "update asset verification failed: --public-key must use "
                    "key-id=base64 format.",
                    file=sys.stderr,
                )
                return 1
            public_keys[key_id] = value
    try:
        legacy_app_update_sources = load_legacy_app_update_sources(
            args.release_config,
            expected_version=args.expected_version,
        )
        manifest = verify_update_assets(
            _read_limited_file(args.manifest, max_bytes=MAX_MANIFEST_BYTES),
            _read_limited_file(args.bundle, max_bytes=MAX_BUNDLE_BYTES),
            public_keys=public_keys,
            expected_version=args.expected_version,
            expected_channel=args.expected_channel,
            expected_runtime_abi=args.expected_runtime_abi,
            expected_settings_schema_version=args.expected_settings_schema_version,
            expected_image_required=args.expected_image_required == "true",
            expected_delivery_mode=args.expected_delivery_mode,
            expected_recommended_image_version=(
                args.expected_recommended_image_version
            ),
            expected_git_sha=args.expected_git_sha,
            expected_release_url=args.expected_release_url,
            expected_bundle_url=args.expected_bundle_url,
            expected_source_root=args.source_root,
        )
        verified_catalog = None
        if args.catalog is not None:
            if not args.expected_delivery_mode:
                raise UpdateBundleError(
                    "Catalog verification requires --expected-delivery-mode."
                )
            catalog_bytes = _read_limited_file(args.catalog, max_bytes=MAX_MANIFEST_BYTES)
            verify_update_catalog(
                catalog_bytes,
                _read_limited_file(args.bundle, max_bytes=MAX_BUNDLE_BYTES),
                public_keys=public_keys,
                expected_version=args.expected_version,
                expected_delivery_mode=args.expected_delivery_mode,
                expected_runtime_abi=args.expected_runtime_abi,
                expected_settings_schema_version=args.expected_settings_schema_version,
                expected_recommended_image_version=(
                    args.expected_recommended_image_version or args.expected_version
                ),
                legacy_app_update_sources=legacy_app_update_sources,
            )
            # Compare signed records, without normalization-added defaults.
            verified_catalog = json.loads(catalog_bytes)
        runtime_paths = _architecture_values(
            args.runtime_bundle, label="--runtime-bundle"
        )
        runtime_urls = _architecture_values(
            args.expected_runtime_bundle_url,
            label="--expected-runtime-bundle-url",
        )
        runtime_requested = bool(
            args.runtime_catalog
            or runtime_paths
            or runtime_urls
            or args.expected_runtime_source_commit
        )
        if runtime_requested:
            if (
                args.runtime_catalog is None
                or not args.expected_runtime_source_commit
                or verified_catalog is None
            ):
                raise UpdateBundleError(
                    "Runtime verification requires both catalogs and a source commit."
                )
            if args.expected_runtime_source_commit != args.expected_git_sha.strip().lower():
                raise UpdateBundleError("Runtime and app verification source commits differ.")
            if (runtime_paths or runtime_urls) and (
                set(runtime_paths) != {"amd64", "arm64"}
                or set(runtime_urls) != {"amd64", "arm64"}
            ):
                raise UpdateBundleError(
                    "Runtime generation verification requires both native bundles and URLs."
                )
            runtime_catalog = verify_runtime_update_catalog(
                _read_limited_file(
                    args.runtime_catalog, max_bytes=MAX_RUNTIME_CATALOG_BYTES
                ),
                (
                    {
                        architecture: _read_limited_file(
                            Path(runtime_paths[architecture]),
                            max_bytes=MAX_BUNDLE_BYTES,
                        )
                        for architecture in ("amd64", "arm64")
                    }
                    if runtime_paths
                    else {}
                ),
                public_keys=public_keys,
                expected_version=args.expected_version,
                expected_source_commit=args.expected_runtime_source_commit,
                expected_bundle_urls=runtime_urls,
            )
            verify_runtime_catalog_binding(
                verified_catalog,
                runtime_catalog,
                expected_version=args.expected_version,
                legacy_app_update_sources=legacy_app_update_sources,
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
