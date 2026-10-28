#!/usr/bin/env python3
"""Build and sign a ChannelWatch app update bundle."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from release_version_policy import validate_release_config
from render_release_legal import render_files

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.runtime_archive import (  # noqa: E402
    MAX_BUNDLE_BYTES as MAX_RUNTIME_BUNDLE_BYTES,
    MAX_BUNDLE_MEMBER_BYTES as MAX_RUNTIME_MEMBER_BYTES,
    MAX_BUNDLE_MEMBER_COUNT as MAX_RUNTIME_MEMBER_COUNT,
    _archive_files as _runtime_archive_files,
)
from core.runtime_catalog import validate_runtime_release_variants  # noqa: E402
from core.runtime_generation import (  # noqa: E402
    DEPENDENCY_INVENTORY_PATH,
    DESCRIPTOR_FILENAME,
    RuntimeGenerationError,
    validate_runtime_descriptor_shape,
)
from core.update_center import (  # noqa: E402
    MAX_RUNTIME_CATALOG_BYTES,
    validate_trusted_url,
)
RUNTIME_ABI = "channelwatch-runtime-v1"
SETTINGS_SCHEMA_VERSION = 7
DEFAULT_KEY_ID = "channelwatch-update-ed25519-2026-06"
EXPORTER = ROOT / "scripts" / "release" / "export-site-release-metadata.py"
COPYLEFT_LICENSE_FETCHER = ROOT / "scripts" / "release" / "copyleft_licenses.py"
CATALOG_HISTORY_FILE = ROOT / "scripts" / "release" / "update-catalog-history.json"
INITIAL_V2_BRIDGE_VERSION = (0, 9, 18)
MAX_CATALOG_RELEASES = 64
LEGACY_PROTOCOL3_APP_UPDATE_CONTRACTS = {
    "1.6.0": ("1.4.0", "1.4.1", "1.4.5"),
    "1.6.1": ("1.4.0", "1.4.1", "1.4.5", "1.6.0"),
    "1.6.2": ("1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1"),
    "1.6.3": ("1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2"),
    "1.6.4": ("1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3"),
}
CANONICAL_PROTOCOL4_APP_UPDATE_CONTRACTS = {
    "1.6.0": ("1.5.3",),
    "1.6.1": ("1.5.3", "1.6.0"),
    "1.6.2": ("1.5.3", "1.6.0", "1.6.1"),
    "1.6.3": ("1.5.3", "1.6.0", "1.6.1", "1.6.2"),
    "1.6.4": ("1.5.3", "1.6.0", "1.6.1", "1.6.2", "1.6.3"),
}
LEGACY_PROTOCOL3_APP_UPDATE_FOUNDATION = "1.4.0"
VERIFIED_DEPENDENCY_FOUNDATION = "1.5.3"
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
    """Load deterministic prior signed entries retained under source control."""

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


def project_catalog_history_v2(
    catalog_history: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Copy full signed history into the schema-2 compatible record shape."""

    projected = copy.deepcopy(catalog_history)
    for release in projected:
        if "runtime_descriptor" in release:
            raise ValueError("A runtime descriptor requires signed platform variants.")
        release.pop("runtime_variants", None)
    return projected


def project_current_release_v2(
    catalog_release: dict[str, object],
    legacy_app_update_sources: object,
) -> dict[str, object]:
    """Project the one-time protocol-3 bridge without changing canonical assets."""

    projected = copy.deepcopy(catalog_release)
    if legacy_app_update_sources in (None, []):
        return projected
    version = str(projected.get("version") or "").strip().lstrip("v")
    _release_version(version)
    legacy_contract = LEGACY_PROTOCOL3_APP_UPDATE_CONTRACTS.get(version)
    canonical_contract = CANONICAL_PROTOCOL4_APP_UPDATE_CONTRACTS.get(version)
    if legacy_contract is None or canonical_contract is None:
        raise ValueError(
            "Legacy protocol-3 projection is not allowed for this release target."
        )
    expected_sources = list(legacy_contract)
    if legacy_app_update_sources != expected_sources:
        raise ValueError(
            "legacy_app_update_sources must match the exact target contract."
        )
    if (
        projected.get("delivery_mode") not in {"app_update", "app_update_with_image_refresh"}
        or projected.get("image_required") is not False
        or projected.get("updater_protocol") != 4
        or projected.get("minimum_image_version") != VERIFIED_DEPENDENCY_FOUNDATION
        or projected.get("compatible_source_application_versions")
        != list(canonical_contract)
        or projected.get("compatible_launcher_protocols") != [4]
    ):
        raise ValueError(
            "Legacy protocol-3 projection requires the verified-dependency "
            "app-update contract."
        )
    projected.update(
        {
            "delivery_mode": "app_update",
            "image_refresh_recommended": False,
            "minimum_image_version": LEGACY_PROTOCOL3_APP_UPDATE_FOUNDATION,
            "updater_protocol": 2,
            "automatic_install_allowed": False,
            "compatible_source_application_versions": expected_sources,
            "compatible_launcher_protocols": [3],
        }
    )
    return projected


def _project_runtime_catalog_history(
    catalog_history: list[dict[str, object]],
) -> tuple[list[dict[str, object]], str | None]:
    """Keep only the newest prior full runtime fallback in a v3 catalog."""

    projected = copy.deepcopy(catalog_history)
    fallback_indexes: list[int] = []
    for index, release in enumerate(projected):
        if "runtime_descriptor" in release:
            raise ValueError("A runtime descriptor requires signed platform variants.")
        if (
            "runtime_variants" in release
            and release.get("updater_protocol") == 4
            and release.get("delivery_mode") != "image_required"
        ):
            fallback_indexes.append(index)
    retained_index = (
        max(
            fallback_indexes,
            key=lambda index: _release_version(projected[index].get("version")),
        )
        if fallback_indexes
        else None
    )
    for index, release in enumerate(projected):
        if index != retained_index:
            release.pop("runtime_variants", None)
    retained_version = (
        str(projected[retained_index].get("version"))
        if retained_index is not None
        else None
    )
    return projected, retained_version


def _verify_tag_is_ancestor(tag: str, *, repo_root: Path) -> None:
    try:
        resolved = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "rev-parse",
                "--verify",
                f"refs/tags/{tag}^{{commit}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "merge-base",
                "--is-ancestor",
                resolved,
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            f"Unpublished predecessor exception {tag} must resolve to an ancestor of HEAD."
        ) from exc


def _verify_github_release_absence(
    tag: str,
    *,
    repository: str,
    github_token: str,
    urlopen=urllib.request.urlopen,
) -> None:
    if not repository or repository.count("/") != 1:
        raise ValueError(
            "GITHUB_REPOSITORY is required to verify GitHub release absence."
        )
    if not github_token:
        raise ValueError(
            "An authenticated GitHub token is required to verify release absence."
        )
    api_root = "https://api.github.com"
    repository_path = urllib.parse.quote(repository, safe="/")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "User-Agent": "channelwatch-release-predecessor-verifier",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = (
        f"{api_root}/repos/{repository_path}/releases/tags/"
        f"{urllib.parse.quote(tag, safe='')}"
    )
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urlopen(request, timeout=15)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status != 404:
            raise ValueError(
                "GitHub release absence for "
                f"{tag} could not be verified (HTTP {status})."
            ) from exc
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise ValueError(
            f"GitHub release absence for {tag} could not be verified."
        ) from exc
    else:
        try:
            status = getattr(response, "status", None)
        finally:
            response.close()
        if status == 200:
            raise ValueError(
                f"GitHub release absence for {tag} was not verified because a release exists."
            )
        raise ValueError(
            f"GitHub release absence for {tag} could not be verified (HTTP {status})."
        )

    # GitHub's release-by-tag endpoint can hide drafts. The authenticated list
    # must independently prove that no draft or published release uses the tag.
    list_request = urllib.request.Request(
        f"{api_root}/repos/{repository_path}/releases?per_page=100",
        headers=headers,
    )
    try:
        listed = urlopen(list_request, timeout=15)
        try:
            if getattr(listed, "status", None) != 200:
                raise ValueError("GitHub release list returned an unexpected status.")
            raw = listed.read(2 * 1024 * 1024 + 1)
        finally:
            listed.close()
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise ValueError(
            f"GitHub release absence for {tag} could not be verified."
        ) from exc
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("GitHub release list exceeded the verification limit.")
    try:
        releases = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub release list was malformed.") from exc
    if not isinstance(releases, list) or len(releases) >= 100:
        raise ValueError("GitHub release list coverage could not be verified.")
    if any(
        not isinstance(release, dict)
        or not isinstance(release.get("tag_name"), str)
        or not release["tag_name"].strip()
        for release in releases
    ):
        raise ValueError("GitHub release list was malformed.")
    if any(release["tag_name"] == tag for release in releases):
        raise ValueError(
            f"GitHub release absence for {tag} was not verified because a release exists."
        )


def require_previous_catalog_release(
    current_version: str,
    history: list[dict[str, object]],
    published_tags: list[str],
    *,
    unpublished_predecessor_tags: object = None,
    repository: str = "",
    github_token: str = "",
    repo_root: Path = ROOT,
    urlopen=urllib.request.urlopen,
) -> None:
    """Do not publish a new feed that silently drops its immediate predecessor."""
    previous = []
    for tag in published_tags:
        if not tag.startswith("v"):
            continue
        try:
            parsed = _release_version(tag)
        except ValueError:
            continue
        if INITIAL_V2_BRIDGE_VERSION <= parsed < _release_version(current_version):
            previous.append(parsed)
    if not previous:
        if _release_version(current_version) > INITIAL_V2_BRIDGE_VERSION:
            raise ValueError("Cannot verify catalog predecessor; fetch the complete release tags.")
        return
    exceptions = (
        []
        if unpublished_predecessor_tags is None
        else unpublished_predecessor_tags
    )
    if not isinstance(exceptions, list) or any(
        not isinstance(tag, str) for tag in exceptions
    ):
        raise ValueError("unpublished_predecessor_tags must be a list of tag strings.")
    if len(exceptions) != len(set(exceptions)):
        raise ValueError("unpublished_predecessor_tags cannot contain duplicates.")

    ordered = sorted(set(previous), reverse=True)
    version = ".".join(map(str, ordered[0]))
    retained_versions = {str(release.get("version") or "") for release in history}
    if version in retained_versions:
        if exceptions:
            raise ValueError(
                "unpublished_predecessor_tags may only name missing immediate predecessors."
            )
        return
    expected_tags = [f"v{'.'.join(map(str, item))}" for item in ordered]
    if not exceptions or exceptions != expected_tags[: len(exceptions)]:
        raise ValueError(f"Signed-catalog history must retain the previous release {version}.")
    if len(exceptions) >= len(ordered):
        raise ValueError("Cannot verify catalog predecessor; fetch the complete release tags.")
    if any(tag.removeprefix("v") in retained_versions for tag in exceptions):
        raise ValueError(
            "unpublished_predecessor_tags may only name missing immediate predecessors."
        )
    for expected_tag in exceptions:
        _verify_tag_is_ancestor(expected_tag, repo_root=repo_root)
        _verify_github_release_absence(
            expected_tag,
            repository=repository,
            github_token=github_token,
            urlopen=urlopen,
        )
    retained_predecessor = ".".join(map(str, ordered[len(exceptions)]))
    if retained_predecessor not in retained_versions:
        raise ValueError(
            "Signed-catalog history must retain the nearest published predecessor "
            f"{retained_predecessor}."
        )


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


def resolve_timing_override(
    explicit_value: str | None, configured_value: str | None, *, name: str
) -> str | None:
    if explicit_value and configured_value and _parse_utc(
        explicit_value, name=name
    ) != _parse_utc(configured_value, name=name):
        raise ValueError(
            f"{name} override must match release-config.json; prepare synchronized "
            "release dates before building the candidate."
        )
    return explicit_value or configured_value


def resolve_publication_time(
    created_at: str,
    explicit_value: str | None,
    configured_value: str | None,
) -> str:
    """Return a deterministic planned publication not earlier than its source."""

    selected = resolve_timing_override(
        explicit_value, configured_value, name="publication_time"
    )
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


def copy_release_legal_files(destination: Path, *, version: str) -> None:
    for source_name, target_name in LEGAL_RELEASE_FILES.items():
        source = ROOT / source_name
        if not source.is_file():
            raise ValueError(f"Required release legal file is missing: {source_name}")
        target = destination / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    render_files(ROOT / "docs/legal", destination / "core/release_legal", version)


def copy_copyleft_release_files(destination: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(COPYLEFT_LICENSE_FETCHER),
            "--output-dir",
            str(destination / "core" / "release_legal" / "copyleft"),
            "--source-map",
            str(destination / "core" / "release_legal" / "CORRESPONDING_SOURCE.md"),
        ],
        cwd=ROOT,
        check=True,
    )


def stage_runtime_application_payload(destination: Path, *, version: str) -> None:
    """Stage the same image-shaped application payload used by app bundles."""

    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Runtime application staging directory must be empty.")
    copy_release_legal_files(destination, version=version)
    copy_copyleft_release_files(destination)
    copy_tree(ROOT / "app" / "core", destination / "core")
    copy_tree(ROOT / "app" / "ui" / "backend", destination / "ui" / "backend")

    ui_out = ROOT / "app" / "ui" / "out"
    if not ui_out.is_dir():
        raise ValueError(
            "app/ui/out is missing. Run the UI build before packaging the update bundle."
        )
    copy_tree(ui_out, destination / "ui" / "backend" / "static_ui")

    image_dir = ROOT / "app" / "ui" / "public" / "images"
    if image_dir.is_dir():
        copy_tree(image_dir, destination / "ui" / "backend" / "static" / "images")


def canonical_payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _runtime_bundle_descriptor(bundle: bytes) -> dict[str, object]:
    """Validate a prebuilt runtime archive without projecting it onto this host."""

    if not bundle or len(bundle) > MAX_RUNTIME_BUNDLE_BYTES:
        raise ValueError("Runtime bundle exceeds its bounded download size.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(bundle), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("Runtime bundle is not a valid ZIP archive.") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_RUNTIME_MEMBER_COUNT:
            raise ValueError("Runtime bundle has too many members.")
        descriptor_members = [
            info for info in members if info.filename == DESCRIPTOR_FILENAME
        ]
        if len(descriptor_members) != 1 or not (
            0 <= descriptor_members[0].file_size <= MAX_RUNTIME_MEMBER_BYTES
        ):
            raise ValueError("Runtime bundle descriptor member is invalid.")
        try:
            descriptor = json.loads(archive.read(DESCRIPTOR_FILENAME).decode("utf-8"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Runtime bundle descriptor is invalid.") from exc
        if not isinstance(descriptor, dict):
            raise ValueError("Runtime bundle descriptor must be an object.")
        try:
            validate_runtime_descriptor_shape(descriptor)
        except RuntimeGenerationError as exc:
            raise ValueError(str(exc)) from exc
        selected = _runtime_archive_files(archive, descriptor)
        if archive.testzip() is not None:
            raise ValueError("Runtime bundle ZIP integrity check failed.")
        embedded = json.loads(archive.read(selected[DESCRIPTOR_FILENAME]).decode("utf-8"))
        if embedded != descriptor:
            raise ValueError("Runtime bundle descriptor identity changed while reading.")
        inventory = archive.read(selected[DEPENDENCY_INVENTORY_PATH])
        if hashlib.sha256(inventory).hexdigest() != descriptor["dependency_inventory_sha256"]:
            raise ValueError("Runtime bundle dependency inventory digest is invalid.")
        expanded_size = len(inventory)
        for record in descriptor["files"]:
            content = archive.read(selected[record["path"]])
            if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise ValueError("Runtime bundle payload digest is invalid.")
            expanded_size += len(content)
        if expanded_size != descriptor["expanded_size"]:
            raise ValueError("Runtime bundle expanded size is invalid.")
    return descriptor


def build_runtime_catalog_v3(
    *,
    catalog_release: dict[str, object],
    catalog_history: list[dict[str, object]],
    runtime_bundles: dict[str, tuple[Path, str]],
    expected_source_commit: str,
    private_key: Ed25519PrivateKey,
    key_id: str,
    publication_time: str,
) -> dict[str, object]:
    """Build a signed protocol-4 catalog for bootstrap or runtime delivery."""

    version = str(catalog_release.get("version") or "").strip().lstrip("v")
    _release_version(version)
    if catalog_release.get("compatible_launcher_protocols") != [4]:
        raise ValueError(
            "Runtime catalogs require an exact protocol-4 release."
        )
    image_bootstrap = catalog_release.get("delivery_mode") == "image_required"
    if image_bootstrap:
        if catalog_release.get("image_required") is not True:
            raise ValueError("Image-only runtime catalog classification is inconsistent.")
        if runtime_bundles:
            raise ValueError("Image-only runtime catalogs cannot contain runtime generation assets.")
    elif catalog_release.get("image_required") is not False:
        raise ValueError("Runtime generation catalogs require a non-image release.")
    elif set(runtime_bundles) != {"amd64", "arm64"}:
        raise ValueError("Runtime generation packaging requires exactly amd64 and arm64 bundles.")
    settings_schema = catalog_release.get("settings_schema_version")
    if type(settings_schema) is not int:
        raise ValueError("Runtime catalog settings schema must be an integer.")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_commit):
        raise ValueError("Runtime catalog source commit must be an exact lowercase Git SHA.")

    variants: list[dict[str, object]] = []
    interpreter_identity: tuple[object, ...] | None = None
    for architecture in (() if image_bootstrap else ("amd64", "arm64")):
        path, url = runtime_bundles[architecture]
        if not isinstance(url, str) or not url:
            raise ValueError(f"Runtime {architecture} bundle URL is missing.")
        validate_trusted_url(url)
        try:
            bundle = Path(path).read_bytes()
        except OSError as exc:
            raise ValueError(f"Runtime {architecture} bundle could not be read.") from exc
        descriptor = _runtime_bundle_descriptor(bundle)
        if descriptor["architecture"] != architecture:
            raise ValueError(f"Runtime {architecture} bundle descriptor architecture does not match.")
        if descriptor["application_version"] != version:
            raise ValueError(f"Runtime {architecture} bundle version does not match the release.")
        if descriptor["source_commit"] != expected_source_commit:
            raise ValueError(f"Runtime {architecture} bundle source commit does not match.")
        if descriptor["settings_schema_version"] != settings_schema:
            raise ValueError(f"Runtime {architecture} bundle settings schema does not match.")
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
            raise ValueError("Runtime bundle interpreter and system bindings do not match.")
        digest = hashlib.sha256(bundle).digest()
        variants.append(
            {
                "bundle_url": url,
                "bundle_sha256": digest.hex(),
                "bundle_signature": base64.b64encode(private_key.sign(digest)).decode("ascii"),
                "key_id": key_id,
                "runtime_descriptor": descriptor,
            }
        )

    runtime_release = copy.deepcopy(catalog_release)
    if not image_bootstrap:
        runtime_release["runtime_variants"] = variants
    runtime_history, retained_fallback = _project_runtime_catalog_history(
        catalog_history
    )
    for historical_release in runtime_history:
        if "runtime_variants" in historical_release:
            validate_runtime_release_variants(
                historical_release, validate_url=validate_trusted_url
            )

    def signed_catalog(history: list[dict[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {
            "channel": "stable",
            "published_at": publication_time,
            "releases": [runtime_release, *history],
        }
        return {
            "schema": 3,
            "payload": payload,
            "signature": {
                "alg": "ed25519",
                "key_id": key_id,
                "value": base64.b64encode(
                    private_key.sign(canonical_payload_bytes(payload))
                ).decode("ascii"),
            },
        }

    catalog = signed_catalog(runtime_history)
    serialized_size = len((json.dumps(catalog, indent=2) + "\n").encode("utf-8"))
    if serialized_size > MAX_RUNTIME_CATALOG_BYTES and retained_fallback is not None:
        for release in runtime_history:
            if str(release.get("version")) == retained_fallback:
                release.pop("runtime_variants", None)
                break
        catalog = signed_catalog(runtime_history)
        serialized_size = len(
            (json.dumps(catalog, indent=2) + "\n").encode("utf-8")
        )
    if serialized_size > MAX_RUNTIME_CATALOG_BYTES:
        raise ValueError("Runtime update catalog exceeds the signed-catalog size limit.")
    return catalog


def _architecture_values(
    values: list[str] | None, *, label: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        architecture, separator, item = value.partition("=")
        if (
            not separator
            or architecture not in {"amd64", "arm64"}
            or not item
            or architecture in result
        ):
            raise ValueError(
                f"{label} must provide one unique amd64=value and arm64=value pair."
            )
        result[architecture] = item
    return result


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
    parser.add_argument(
        "--runtime-bundle",
        action="append",
        help="Prebuilt runtime archive as amd64=path or arm64=path.",
    )
    parser.add_argument(
        "--runtime-bundle-url",
        action="append",
        help="Published runtime archive URL as amd64=url or arm64=url.",
    )
    args = parser.parse_args()

    if args.image_required and args.delivery_mode is not None:
        raise ValueError(
            "--image-required cannot be combined with an explicit delivery mode."
        )
    version = args.version.strip().lstrip("v")
    configured_delivery_mode = None
    configured_publication_time = None
    configured_automatic_install_after = None
    configured_automatic_install_allowed = None
    configured_source_versions = None
    configured_launcher_protocols = None
    configured_legacy_app_update_sources = None
    configured_unpublished_predecessor_tags = None
    runtime_generation_required = False
    try:
        release_config = json.loads(
            (ROOT / "scripts" / "release" / "release-config.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(release_config, dict):
            raise ValueError("release-config.json must contain a JSON object.")
        if str(release_config.get("version") or "").lstrip("v") == version:
            validate_release_config(release_config)
            configured_delivery_mode = release_config.get("delivery_mode")
            configured_publication_time = release_config.get("publication_time")
            configured_automatic_install_after = release_config.get(
                "automatic_install_after"
            )
            configured_automatic_install_allowed = release_config.get(
                "automatic_install_allowed"
            )
            configured_source_versions = release_config.get(
                "compatible_source_application_versions"
            )
            configured_launcher_protocols = release_config.get(
                "compatible_launcher_protocols"
            )
            configured_legacy_app_update_sources = release_config.get(
                "legacy_app_update_sources"
            )
            configured_unpublished_predecessor_tags = release_config.get(
                "unpublished_predecessor_tags"
            )
            runtime_generation_required = (
                release_config.get("runtime_generation_required") is True
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
            if not isinstance(configured_automatic_install_allowed, bool):
                raise ValueError(
                    "release-config.json automatic_install_allowed must be a boolean."
                )
            for field in ("publication_time", "automatic_install_after"):
                resolve_timing_override(
                    getattr(args, field), release_config.get(field), name=field
                )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("release-config.json could not be read safely.") from exc
    if configured_delivery_mode not in {*DELIVERY_MODES, None}:
        raise ValueError("release-config.json delivery_mode is invalid.")
    runtime_bundle_paths = _architecture_values(
        args.runtime_bundle, label="--runtime-bundle"
    )
    runtime_bundle_urls = _architecture_values(
        args.runtime_bundle_url, label="--runtime-bundle-url"
    )
    if runtime_generation_required:
        if set(runtime_bundle_paths) != {"amd64", "arm64"} or set(
            runtime_bundle_urls
        ) != {"amd64", "arm64"}:
            raise ValueError(
                "runtime_generation_required needs both amd64 and arm64 runtime bundles and URLs."
            )
    elif runtime_bundle_paths or runtime_bundle_urls:
        raise ValueError(
            "Runtime bundles are allowed only when release-config enables runtime_generation_required."
        )
    delivery_mode = (
        "image_required"
        if args.image_required
        else args.delivery_mode
        or configured_delivery_mode
        or "app_update"
    )
    automatic_install_allowed = (
        configured_automatic_install_allowed
        if configured_automatic_install_allowed is not None
        else args.automatic_install_allowed == "true"
    )
    out_dir = (ROOT / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    signing_key_value = os.environ.get(args.signing_key_env, "")
    private_key = load_private_key(signing_key_value)

    bundle_name = f"channelwatch-app-v{version}.zip"
    bundle_path = out_dir / bundle_name
    manifest_path = out_dir / f"channelwatch-update-v{version}.json"
    catalog_path = out_dir / f"channelwatch-catalog-v{version}.json"
    runtime_catalog_path = out_dir / "channelwatch-update-catalog-v3.json"
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
    if (
        delivery_mode != "image_required"
        and 4 in compatible_launcher_protocols
        and not runtime_generation_required
    ):
        raise ValueError(
            "Every protocol-4 non-image release requires runtime_generation_required."
        )
    catalog_history = load_catalog_history(version, path=args.catalog_history)
    require_previous_catalog_release(
        version,
        catalog_history,
        run_git("tag", "--merged", "HEAD", "--list").splitlines(),
        unpublished_predecessor_tags=configured_unpublished_predecessor_tags,
        repository=os.environ.get("GITHUB_REPOSITORY", ""),
        github_token=os.environ.get("GH_TOKEN", "")
        or os.environ.get("GITHUB_TOKEN", ""),
    )

    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp) / "bundle"
        stage_runtime_application_payload(staging, version=version)

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
        "automatic_install_allowed": automatic_install_allowed,
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
    legacy_catalog_release = project_current_release_v2(
        catalog_release,
        configured_legacy_app_update_sources,
    )
    catalog_payload = {
        "channel": "stable",
        "published_at": publication_time,
        "releases": [
            legacy_catalog_release,
            *project_catalog_history_v2(catalog_history),
        ],
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
    runtime_catalog = None
    runtime_catalog_required = compatible_launcher_protocols == [4] and (
        runtime_generation_required or delivery_mode == "image_required"
    )
    if runtime_catalog_required:
        if runtime_generation_required and delivery_mode == "image_required":
            raise ValueError(
                "Runtime generation catalogs require a non-image protocol-4 release."
            )
        runtime_catalog = build_runtime_catalog_v3(
            catalog_release=catalog_release,
            catalog_history=catalog_history,
            runtime_bundles=(
                {
                    architecture: (
                        Path(runtime_bundle_paths[architecture]),
                        runtime_bundle_urls[architecture],
                    )
                    for architecture in ("amd64", "arm64")
                }
                if runtime_generation_required
                else {}
            ),
            expected_source_commit=git_sha,
            private_key=private_key,
            key_id=args.key_id,
            publication_time=publication_time,
        )
        runtime_catalog_path.write_text(
            json.dumps(runtime_catalog, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "bundle": str(bundle_path),
                "manifest": str(manifest_path),
                "catalog": str(catalog_path),
                **(
                    {"runtime_catalog": str(runtime_catalog_path)}
                    if runtime_catalog is not None
                    else {}
                ),
                "sha256": digest.hex(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
