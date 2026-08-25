#!/usr/bin/env python3
"""Build and sign a ChannelWatch app update bundle."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ABI = "channelwatch-runtime-v1"
SETTINGS_SCHEMA_VERSION = 7
DEFAULT_KEY_ID = "channelwatch-update-ed25519-2026-06"
EXPORTER = ROOT / "scripts" / "release" / "export-site-release-metadata.py"
COPYLEFT_LICENSE_FETCHER = ROOT / "scripts" / "release" / "copyleft_licenses.py"
CATALOG_HISTORY_FILE = ROOT / "scripts" / "release" / "update-catalog-history.json"
INITIAL_V2_BRIDGE_VERSION = (0, 9, 18)
MAX_CATALOG_RELEASES = 64
LEGAL_RELEASE_FILES = {
    "LICENSE": "core/release_legal/LICENSE",
    "docs/legal/NOTICE": "core/release_legal/NOTICE",
    "docs/legal/THIRD_PARTY_LICENSES.md": "core/release_legal/THIRD_PARTY_LICENSES.md",
}
DELIVERY_MODES = (
    "app_update",
    "app_update_with_image_refresh",
    "image_required",
)
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


def _release_version(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip().lstrip("v")
    parts = text.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Release version {value!r} must use X.Y.Z.")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def explicit_catalog_compatibility(
    *,
    command_source_versions: list[str] | None,
    command_launcher_protocols: list[int] | None,
    configured_source_versions: object,
    configured_launcher_protocols: object,
) -> tuple[list[str], list[int]]:
    """Return only compatibility that release configuration states explicitly."""

    raw_sources: object = (
        command_source_versions
        if command_source_versions is not None
        else configured_source_versions
    )
    raw_protocols: object = (
        command_launcher_protocols
        if command_launcher_protocols is not None
        else configured_launcher_protocols
    )
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError(
            "Release compatibility requires an explicit non-empty source-version list."
        )
    if not isinstance(raw_protocols, list) or not raw_protocols:
        raise ValueError(
            "Release compatibility requires an explicit non-empty launcher-protocol list."
        )

    sources = [str(item).strip().lstrip("v") for item in raw_sources]
    if any(not source for source in sources):
        raise ValueError("Compatible source versions cannot be blank.")
    for source in sources:
        _release_version(source)
    if len(sources) != len(set(sources)):
        raise ValueError("Compatible source versions cannot contain duplicates.")
    if any(type(protocol) is not int or protocol < 0 for protocol in raw_protocols):
        raise ValueError("Compatible launcher protocols must be non-negative integers.")
    protocols = list(raw_protocols)
    if len(protocols) != len(set(protocols)):
        raise ValueError("Compatible launcher protocols cannot contain duplicates.")
    return (
        sorted(sources, key=_release_version),
        sorted(protocols),
    )


def load_catalog_history(
    current_version: str,
    *,
    path: Path = CATALOG_HISTORY_FILE,
) -> list[dict[str, object]]:
    """Load deterministic prior v2 entries retained under source control."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The signed-catalog history could not be read safely.") from exc
    releases = raw.get("releases") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(
        releases, list
    ):
        raise ValueError("The signed-catalog history schema is invalid.")
    if len(releases) >= MAX_CATALOG_RELEASES:
        raise ValueError("The signed-catalog history contains too many releases.")

    current = _release_version(current_version)
    retained: list[dict[str, object]] = []
    versions: list[str] = []
    for release in releases:
        if not isinstance(release, dict):
            raise ValueError("Every signed-catalog history entry must be an object.")
        version = str(release.get("version") or "").strip().lstrip("v")
        parsed = _release_version(version)
        if parsed >= current:
            raise ValueError(
                "Signed-catalog history may contain only releases older than the target."
            )
        versions.append(version)
        retained.append(dict(release))
    if len(versions) != len(set(versions)):
        raise ValueError("Signed-catalog history contains a duplicate version.")
    if current > INITIAL_V2_BRIDGE_VERSION and "0.9.18" not in versions:
        raise ValueError(
            "Future v2 catalogs must retain the permanent v0.9.18 bridge entry."
        )
    retained.sort(
        key=lambda release: _release_version(release.get("version")),
        reverse=True,
    )
    return retained


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


def load_exporter():
    spec = importlib.util.spec_from_file_location(
        "export_site_release_metadata",
        EXPORTER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load release metadata exporter.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_highlights(version: str, exporter=None) -> list[str]:
    source = exporter or load_exporter()
    changelog = source.parse_changelog(version)
    return list(changelog.get("changelogHighlights") or [])


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
        raise ValueError(
            "Signing key must be PEM or base64 raw Ed25519 private key bytes."
        ) from exc
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


def _source_date_epoch() -> int:
    """Return a stable release timestamp shared by candidate and tag builds."""

    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if not raw:
        raw = run_git("show", "-s", "--format=%ct", "HEAD")
    try:
        epoch = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "SOURCE_DATE_EPOCH or the exact Git commit timestamp is required."
        ) from exc
    if epoch < 315532800:  # ZIP timestamps cannot represent dates before 1980.
        raise ValueError("Release timestamp must be on or after 1980-01-01.")
    return epoch


def _format_utc(epoch: int) -> str:
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: str, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def resolve_automatic_install_after(
    publication_time: str, configured_value: str | None
) -> str:
    """Enforce the stable channel's mandatory 24-hour automatic-install delay."""

    published = _parse_utc(publication_time, name="publication_time")
    minimum = published + timedelta(hours=24)
    if configured_value:
        resolved = _parse_utc(
            configured_value, name="automatic_install_after"
        )
        if resolved < minimum:
            raise ValueError(
                "automatic_install_after must be at least 24 hours after publication."
            )
    else:
        resolved = minimum
    return (
        resolved.replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def resolve_publication_time(
    created_at: str,
    explicit_value: str | None,
    configured_value: str | None,
) -> str:
    """Return a deterministic planned publication not earlier than its source."""

    selected = explicit_value or configured_value
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("A planned publication_time is required.")
    publication_time = selected.strip()
    parsed_publication = _parse_utc(publication_time, name="publication_time")
    parsed_created = _parse_utc(created_at, name="created_at")
    if parsed_publication < parsed_created:
        raise ValueError(
            "publication_time cannot be earlier than the exact candidate creation time."
        )
    return publication_time


def write_zip(source: Path, destination: Path, *, source_date_epoch: int) -> None:
    timestamp = datetime.fromtimestamp(source_date_epoch, timezone.utc)
    zip_time = (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second - (timestamp.second % 2),
    )
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(source).as_posix()
            mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
            info = zipfile.ZipInfo(rel, date_time=zip_time)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, path.read_bytes())


def copy_release_legal_files(destination: Path) -> None:
    for source_name, target_name in LEGAL_RELEASE_FILES.items():
        source = ROOT / source_name
        if not source.is_file():
            raise ValueError(f"Required release legal file is missing: {source_name}")
        target = destination / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_copyleft_release_files(destination: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(COPYLEFT_LICENSE_FETCHER),
            "--output-dir",
            str(destination / "core" / "release_legal" / "copyleft"),
            "--source-map",
            str(ROOT / "docs" / "legal" / "CORRESPONDING_SOURCE.md"),
        ],
        cwd=ROOT,
        check=True,
    )


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
        help="Deprecated alias for --delivery-mode=image_required.",
    )
    parser.add_argument(
        "--delivery-mode",
        choices=DELIVERY_MODES,
    )
    parser.add_argument("--minimum-image-version", default="0.9.10")
    parser.add_argument("--updater-protocol", type=int, default=2)
    parser.add_argument("--recommended-image-version")
    parser.add_argument(
        "--compatible-source-application-version",
        action="append",
        dest="compatible_source_versions",
    )
    parser.add_argument(
        "--compatible-launcher-protocol",
        action="append",
        type=int,
        dest="compatible_launcher_protocols",
    )
    parser.add_argument(
        "--catalog-history",
        type=Path,
        default=CATALOG_HISTORY_FILE,
        help=(
            "Repository-controlled prior schema-2 release entries to retain in "
            "the newly signed catalog."
        ),
    )
    parser.add_argument("--automatic-install-after")
    parser.add_argument("--publication-time")
    parser.add_argument(
        "--automatic-install-allowed",
        choices=("true", "false"),
        default="true",
    )
    parser.add_argument(
        "--recovery-compatible",
        choices=("true", "false"),
        default="true",
    )
    parser.add_argument("--signing-key-env", default="CHANNELWATCH_UPDATE_SIGNING_KEY")
    args = parser.parse_args()

    if args.image_required and args.delivery_mode is not None:
        raise ValueError(
            "--image-required cannot be combined with an explicit delivery mode."
        )
    version = args.version.strip().lstrip("v")
    configured_delivery_mode = None
    configured_publication_time = None
    configured_automatic_install_after = None
    configured_source_versions = None
    configured_launcher_protocols = None
    try:
        release_config = json.loads(
            (ROOT / "scripts" / "release" / "release-config.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(release_config, dict):
            raise ValueError("release-config.json must contain a JSON object.")
        if str(release_config.get("version") or "").lstrip("v") == version:
            configured_delivery_mode = release_config.get("delivery_mode")
            configured_publication_time = release_config.get("publication_time")
            configured_automatic_install_after = release_config.get(
                "automatic_install_after"
            )
            configured_source_versions = release_config.get(
                "compatible_source_application_versions"
            )
            configured_launcher_protocols = release_config.get(
                "compatible_launcher_protocols"
            )
            if (
                not isinstance(configured_publication_time, str)
                or not configured_publication_time.strip()
            ):
                raise ValueError(
                    "release-config.json publication_time is required for the target release."
                )
            if (
                not isinstance(configured_automatic_install_after, str)
                or not configured_automatic_install_after.strip()
            ):
                raise ValueError(
                    "release-config.json automatic_install_after is required for the target release."
                )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("release-config.json could not be read safely.") from exc
    if configured_delivery_mode not in {*DELIVERY_MODES, None}:
        raise ValueError("release-config.json delivery_mode is invalid.")
    delivery_mode = (
        "image_required"
        if args.image_required
        else args.delivery_mode
        or configured_delivery_mode
        or "app_update"
    )
    out_dir = (ROOT / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    signing_key_value = os.environ.get(args.signing_key_env, "")
    private_key = load_private_key(signing_key_value)

    bundle_name = f"channelwatch-app-v{version}.zip"
    bundle_path = out_dir / bundle_name
    manifest_path = out_dir / f"channelwatch-update-v{version}.json"
    catalog_path = out_dir / f"channelwatch-catalog-v{version}.json"
    git_sha = run_git("rev-parse", "HEAD") or "unknown"
    source_date_epoch = _source_date_epoch()
    created_at = _format_utc(source_date_epoch)
    publication_time = resolve_publication_time(
        created_at,
        args.publication_time,
        configured_publication_time,
    )
    recommended_image_version = (
        (args.recommended_image_version or version).strip().lstrip("v")
    )
    compatible_source_versions, compatible_launcher_protocols = (
        explicit_catalog_compatibility(
            command_source_versions=args.compatible_source_versions,
            command_launcher_protocols=args.compatible_launcher_protocols,
            configured_source_versions=configured_source_versions,
            configured_launcher_protocols=configured_launcher_protocols,
        )
    )
    catalog_history = load_catalog_history(version, path=args.catalog_history)

    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp) / "bundle"
        staging.mkdir()
        copy_release_legal_files(staging)
        copy_copyleft_release_files(staging)
        copy_tree(ROOT / "app" / "core", staging / "core")
        copy_tree(ROOT / "app" / "ui" / "backend", staging / "ui" / "backend")

        ui_out = ROOT / "app" / "ui" / "out"
        if not ui_out.is_dir():
            raise ValueError(
                "app/ui/out is missing. Run the UI build before packaging the update bundle."
            )
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
        write_zip(staging, bundle_path, source_date_epoch=source_date_epoch)

    digest = sha256_bytes(bundle_path)
    bundle_signature = base64.b64encode(private_key.sign(digest)).decode("ascii")
    highlights = release_highlights(version)

    payload = {
        "version": version,
        "version_tag": f"v{version}",
        "channel": "stable",
        "runtime_abi": RUNTIME_ABI,
        "settings_schema_version": SETTINGS_SCHEMA_VERSION,
        "image_required": delivery_mode == "image_required",
        "delivery_mode": delivery_mode,
        "image_refresh_recommended": (
            delivery_mode == "app_update_with_image_refresh"
        ),
        "minimum_image_version": args.minimum_image_version.strip().lstrip("v"),
        "updater_protocol": args.updater_protocol,
        "recommended_image_version": recommended_image_version,
        "automatic_install_allowed": args.automatic_install_allowed == "true",
        "recovery_compatible": args.recovery_compatible == "true",
        "release_url": args.release_url,
        "bundle_url": args.bundle_url,
        "bundle_sha256": digest.hex(),
        "bundle_signature": bundle_signature,
        "key_id": args.key_id,
        "published_at": publication_time,
        "highlights": highlights,
    }
    manifest = {
        "schema": 1,
        "payload": payload,
        "signature": {
            "alg": "ed25519",
            "key_id": args.key_id,
            "value": base64.b64encode(
                private_key.sign(canonical_payload_bytes(payload))
            ).decode("ascii"),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    automatic_install_after_value = resolve_automatic_install_after(
        publication_time,
        args.automatic_install_after or configured_automatic_install_after,
    )

    catalog_release = {
        **payload,
        "automatic_install_after": automatic_install_after_value,
        "compatible_source_application_versions": compatible_source_versions,
        "compatible_runtime_abis": [RUNTIME_ABI],
        "compatible_settings_schema_versions": [SETTINGS_SCHEMA_VERSION],
        "compatible_launcher_protocols": compatible_launcher_protocols,
        "revocation_state": "active",
        "publication_time": publication_time,
    }
    catalog_payload = {
        "channel": "stable",
        "published_at": publication_time,
        "releases": [catalog_release, *catalog_history],
    }
    catalog = {
        "schema": 2,
        "payload": catalog_payload,
        "signature": {
            "alg": "ed25519",
            "key_id": args.key_id,
            "value": base64.b64encode(
                private_key.sign(canonical_payload_bytes(catalog_payload))
            ).decode("ascii"),
        },
    }
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "bundle": str(bundle_path),
                "manifest": str(manifest_path),
                "catalog": str(catalog_path),
                "sha256": digest.hex(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
