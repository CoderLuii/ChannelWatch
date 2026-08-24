"""Signed Update Center catalog validation and compatible release selection.

Schema 1 remains the immutable bridge consumed by pre-v0.9.18 clients.  Schema
2 is an additive, signed catalog used by v0.9.18 and newer runtimes.  Keeping
catalog parsing separate from HTTP and persistence makes the same trust path
usable by the normal scheduler and the fail-safe recovery service.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Any

CATALOG_SCHEMA_VERSION = 2
MAX_CATALOG_RELEASES = 64
DEFAULT_UPDATE_CATALOG_URL = "https://channelwatch.coderluii.dev/updates/v2/stable.json"
CURRENT_UPDATER_PROTOCOL = 2


class DeliveryMode(StrEnum):
    """How a verified release may be delivered to an installation."""

    APP_UPDATE = "app_update"
    APP_UPDATE_WITH_IMAGE_REFRESH = "app_update_with_image_refresh"
    IMAGE_REQUIRED = "image_required"


class LauncherProtocol(IntEnum):
    """Runtime-launcher generations shipped by ChannelWatch images."""

    UNSAFE_LEGACY = 0  # v0.9.9: core argv parsing cannot launch a bundle safely
    LEGACY_ADOPT = 1  # v0.9.10-v0.9.15: bundle selection, no readiness quorum
    ACTIVATION_QUORUM = 2  # v0.9.16-v0.9.17
    RECOVERY_CAPABLE = 3  # v0.9.18+


@dataclass(frozen=True)
class CatalogSelection:
    release: dict[str, Any] | None
    reason: str
    considered_versions: tuple[str, ...]


def launcher_protocol_for_image_version(image_version: str) -> LauncherProtocol:
    """Map the immutable container image version to its launcher contract."""

    try:
        major, minor, patch = _parse_version(image_version)
    except (TypeError, ValueError):
        return LauncherProtocol.UNSAFE_LEGACY
    if (major, minor) != (0, 9):
        return (
            LauncherProtocol.RECOVERY_CAPABLE
            if (major, minor, patch) > (0, 9, 17)
            else LauncherProtocol.UNSAFE_LEGACY
        )
    if patch <= 9:
        return LauncherProtocol.UNSAFE_LEGACY
    if patch <= 15:
        return LauncherProtocol.LEGACY_ADOPT
    if patch <= 17:
        return LauncherProtocol.ACTIVATION_QUORUM
    return LauncherProtocol.RECOVERY_CAPABLE


def _parse_version(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip().lstrip("v")
    parts = text.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Version {value!r} is not X.Y.Z.")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _normalized_version(value: object) -> str:
    version = str(value or "").strip().lstrip("v")
    _parse_version(version)
    return version


def _explicit_bool(payload: dict[str, Any], name: str, default: bool) -> bool:
    value = payload.get(name, default)
    if type(value) is not bool:
        raise ValueError(f"Catalog release {name} must be a boolean.")
    return value


def _explicit_int(
    payload: dict[str, Any], name: str, default: int, *, minimum: int = 0
) -> int:
    value = payload.get(name, default)
    if type(value) is not int or value < minimum:
        raise ValueError(
            f"Catalog release {name} must be an integer of at least {minimum}."
        )
    return value


def _version_list(raw: object, *, name: str) -> list[str]:
    if not isinstance(raw, list) or not raw or len(raw) > 128:
        raise ValueError(f"Catalog release {name} must be a non-empty bounded list.")
    versions = [_normalized_version(value) for value in raw]
    if len(versions) != len(set(versions)):
        raise ValueError(f"Catalog release {name} contains duplicates.")
    return versions


def _string_list(raw: object, *, name: str) -> list[str]:
    if not isinstance(raw, list) or not raw or len(raw) > 32:
        raise ValueError(f"Catalog release {name} must be a non-empty bounded list.")
    values = [str(value).strip() for value in raw]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(f"Catalog release {name} contains empty or duplicate values.")
    return values


def _int_list(raw: object, *, name: str, minimum: int = 0) -> list[int]:
    if not isinstance(raw, list) or not raw or len(raw) > 32:
        raise ValueError(f"Catalog release {name} must be a non-empty bounded list.")
    if any(type(value) is not int or value < minimum for value in raw):
        raise ValueError(f"Catalog release {name} contains an invalid integer.")
    if len(raw) != len(set(raw)):
        raise ValueError(f"Catalog release {name} contains duplicates.")
    return list(raw)


def _timestamp(value: object, *, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Catalog release {name} is missing.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Catalog release {name} is not an ISO timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Catalog release {name} must include a timezone.")
    return text


def _parsed_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_catalog_release(
    raw: object,
    *,
    validate_url: Callable[[str], str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError("Catalog releases must be JSON objects.")

    version = _normalized_version(raw.get("version"))
    version_tag = str(raw.get("version_tag") or f"v{version}")
    if version_tag != f"v{version}":
        raise ValueError("Catalog release version_tag does not match version.")

    try:
        delivery_mode = DeliveryMode(str(raw.get("delivery_mode") or ""))
    except ValueError as exc:
        raise ValueError("Catalog release delivery_mode is unsupported.") from exc

    bundle_url = str(raw.get("bundle_url") or "")
    release_url = str(raw.get("release_url") or "")
    image_url = str(raw.get("image_url") or "")
    if delivery_mode is not DeliveryMode.IMAGE_REQUIRED and not bundle_url:
        raise ValueError("An app-deliverable catalog release needs a bundle URL.")
    for url in (bundle_url, release_url, image_url):
        if url:
            validate_url(url)

    runtime_abi = str(raw.get("runtime_abi") or "")
    bundle_sha256 = str(raw.get("bundle_sha256") or "").lower()
    bundle_signature = str(raw.get("bundle_signature") or "")
    key_id = str(raw.get("key_id") or "")
    if delivery_mode is not DeliveryMode.IMAGE_REQUIRED:
        if not runtime_abi:
            raise ValueError("Catalog release runtime_abi is missing.")
        if len(bundle_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in bundle_sha256
        ):
            raise ValueError("Catalog release bundle_sha256 is invalid.")
        if not bundle_signature or not key_id:
            raise ValueError("Catalog release bundle signature is incomplete.")

    compatible_source_versions = _version_list(
        raw.get("compatible_source_application_versions"),
        name="compatible_source_application_versions",
    )
    compatible_runtime_abis = _string_list(
        raw.get("compatible_runtime_abis"),
        name="compatible_runtime_abis",
    )
    compatible_settings_schemas = _int_list(
        raw.get("compatible_settings_schema_versions"),
        name="compatible_settings_schema_versions",
    )
    compatible_launcher_protocols = _int_list(
        raw.get("compatible_launcher_protocols"),
        name="compatible_launcher_protocols",
    )
    recommended_image_version = _normalized_version(
        raw.get("recommended_image_version") or version
    )
    revocation_state = str(raw.get("revocation_state") or "")
    if revocation_state not in {"active", "revoked"}:
        raise ValueError("Catalog release revocation_state is unsupported.")
    publication_time = _timestamp(raw.get("publication_time"), name="publication_time")
    automatic_install_after = _timestamp(
        raw.get("automatic_install_after"), name="automatic_install_after"
    )
    if _parsed_timestamp(automatic_install_after) < (
        _parsed_timestamp(publication_time) + timedelta(hours=24)
    ):
        raise ValueError(
            "Catalog release automatic_install_after must be at least 24 hours "
            "after publication_time."
        )

    highlights_raw = raw.get("highlights", [])
    if not isinstance(highlights_raw, list) or len(highlights_raw) > 32:
        raise ValueError("Catalog release highlights must be a bounded list.")

    return {
        **raw,
        "version": version,
        "version_tag": version_tag,
        "delivery_mode": delivery_mode.value,
        # Preserve the schema-1 compatibility projection for existing UI and
        # API clients while the richer delivery mode remains authoritative.
        "image_required": delivery_mode is DeliveryMode.IMAGE_REQUIRED,
        "image_refresh_recommended": (
            delivery_mode is DeliveryMode.APP_UPDATE_WITH_IMAGE_REFRESH
        ),
        "runtime_abi": runtime_abi,
        "settings_schema_version": _explicit_int(raw, "settings_schema_version", 0),
        "updater_protocol": _explicit_int(raw, "updater_protocol", 2, minimum=1),
        "automatic_install_allowed": _explicit_bool(
            raw, "automatic_install_allowed", False
        ),
        "automatic_install_after": automatic_install_after,
        "recovery_compatible": _explicit_bool(raw, "recovery_compatible", False),
        "compatible_source_application_versions": compatible_source_versions,
        "compatible_runtime_abis": compatible_runtime_abis,
        "compatible_settings_schema_versions": compatible_settings_schemas,
        "compatible_launcher_protocols": compatible_launcher_protocols,
        "recommended_image_version": recommended_image_version,
        "revocation_state": revocation_state,
        "publication_time": publication_time,
        "bundle_url": bundle_url,
        "release_url": release_url,
        "image_url": image_url,
        "bundle_sha256": bundle_sha256,
        "bundle_signature": bundle_signature,
        "key_id": key_id,
        "highlights": [str(item) for item in highlights_raw if str(item).strip()],
    }


def normalize_catalog(
    raw: object,
    *,
    public_keys: dict[str, str],
    verify_signature: Callable[[dict[str, str], str, str, bytes], None],
    canonical_payload: Callable[[dict[str, Any]], bytes],
    validate_url: Callable[[str], str],
) -> dict[str, Any]:
    """Validate a schema-2 catalog and its one authoritative signature."""

    if not isinstance(raw, dict) or raw.get("schema") != CATALOG_SCHEMA_VERSION:
        raise TypeError("Unsupported update catalog schema.")
    payload = raw.get("payload")
    signature = raw.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, dict):
        raise TypeError("Update catalog is missing payload or signature.")
    if signature.get("alg") != "ed25519":
        raise ValueError("Update catalog signature algorithm is unsupported.")
    key_id = str(signature.get("key_id") or "")
    value = str(signature.get("value") or "")
    if not key_id or not value:
        raise ValueError("Update catalog signature is incomplete.")
    verify_signature(public_keys, key_id, value, canonical_payload(payload))

    channel = str(payload.get("channel") or "")
    if channel != "stable":
        raise ValueError("Update catalog channel is unsupported.")
    releases_raw = payload.get("releases")
    if not isinstance(releases_raw, list) or not releases_raw:
        raise ValueError("Update catalog must contain at least one release.")
    if len(releases_raw) > MAX_CATALOG_RELEASES:
        raise ValueError("Update catalog contains too many releases.")
    releases = [
        normalize_catalog_release(item, validate_url=validate_url)
        for item in releases_raw
    ]
    versions = [release["version"] for release in releases]
    if len(versions) != len(set(versions)):
        raise ValueError("Update catalog contains a duplicate release version.")

    # Deterministic descending order makes selection and audits independent of
    # publisher input ordering.
    releases.sort(key=lambda item: _parse_version(item["version"]), reverse=True)
    return {
        "schema": CATALOG_SCHEMA_VERSION,
        "payload": {
            **payload,
            "channel": channel,
            "releases": releases,
        },
        "signature": {
            "alg": "ed25519",
            "key_id": key_id,
            "value": value,
        },
    }


def _release_compatible(
    release: dict[str, Any],
    *,
    current_version: str,
    runtime_abi: str,
    settings_schema_version: int,
    launcher_protocol: int,
    recovery: bool,
) -> bool:
    current = _parse_version(current_version)
    # Selecting the current release lets a catalog-backed check report
    # "current" without treating an ordinary up-to-date installation as an
    # error. UpdateManager still refuses to apply a non-newer version.
    if _parse_version(release["version"]) < current:
        return False
    if release.get("revocation_state") != "active":
        return False
    if int(release.get("updater_protocol") or 0) != CURRENT_UPDATER_PROTOCOL:
        return False
    if (
        current_version.strip().lstrip("v")
        not in release["compatible_source_application_versions"]
        and _parse_version(release["version"]) != current
    ):
        return False
    if int(launcher_protocol) not in release["compatible_launcher_protocols"]:
        return False
    if recovery and not bool(release.get("recovery_compatible")):
        return False
    if release["delivery_mode"] != DeliveryMode.IMAGE_REQUIRED.value:
        if runtime_abi not in release["compatible_runtime_abis"]:
            return False
        if (
            int(settings_schema_version)
            not in release["compatible_settings_schema_versions"]
        ):
            return False
    return True


def select_catalog_release(
    catalog: dict[str, Any],
    *,
    current_version: str,
    runtime_abi: str,
    settings_schema_version: int,
    launcher_protocol: int,
    recovery: bool = False,
) -> CatalogSelection:
    releases: Iterable[dict[str, Any]] = catalog["payload"]["releases"]
    considered: list[str] = []
    for release in releases:
        considered.append(str(release["version"]))
        if _release_compatible(
            release,
            current_version=current_version,
            runtime_abi=runtime_abi,
            settings_schema_version=settings_schema_version,
            launcher_protocol=launcher_protocol,
            recovery=recovery,
        ):
            return CatalogSelection(
                release=release,
                reason="compatible-release",
                considered_versions=tuple(considered),
            )
    return CatalogSelection(
        release=None,
        reason=(
            "no-compatible-recovery-release" if recovery else "no-compatible-release"
        ),
        considered_versions=tuple(considered),
    )
