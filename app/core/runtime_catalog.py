"""Platform projection of already-verified dependency release catalogs."""

import base64
import binascii
import re
from typing import Any, Callable

from core.update_catalog import (
    DeliveryMode,
    VERIFIED_DEPENDENCY_UPDATER_PROTOCOL,
    normalize_catalog_release,
)


def validate_runtime_release_variants(
    raw: object, *, validate_url: Callable[[str], str]
) -> list[dict[str, Any]]:
    """Validate signed variant structure without requiring the local platform."""

    from core.runtime_generation import (
        RuntimeGenerationError,
        validate_runtime_descriptor_shape,
    )

    if not isinstance(raw, dict):
        raise TypeError("Runtime catalog releases must be objects.")
    variants = raw.get("runtime_variants")
    if not isinstance(variants, list) or len(variants) != 2:
        raise ValueError("Runtime releases require exactly two platform variants.")
    release_version = str(raw.get("version") or "").strip().lstrip("v")
    settings_schema = raw.get("settings_schema_version")
    architectures = set()
    validated = []
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) != {
            "bundle_url",
            "bundle_sha256",
            "bundle_signature",
            "key_id",
            "runtime_descriptor",
        }:
            raise ValueError("Runtime variant fields are invalid.")
        bundle_url = variant["bundle_url"]
        if not isinstance(bundle_url, str) or not bundle_url:
            raise ValueError("Runtime variant bundle URL is invalid.")
        validate_url(bundle_url)
        digest = variant["bundle_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Runtime variant bundle SHA-256 is invalid.")
        signature = variant["bundle_signature"]
        if not isinstance(signature, str):
            raise ValueError("Runtime variant bundle signature is invalid.")
        try:
            decoded_signature = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Runtime variant bundle signature is invalid.") from exc
        if (
            len(decoded_signature) != 64
            or base64.b64encode(decoded_signature).decode("ascii") != signature
        ):
            raise ValueError("Runtime variant bundle signature is invalid.")
        if not isinstance(variant["key_id"], str) or not variant["key_id"].strip():
            raise ValueError("Runtime variant signing key is missing.")
        descriptor = variant["runtime_descriptor"]
        if not isinstance(descriptor, dict):
            raise ValueError("Runtime variant descriptor must be an object.")
        try:
            validate_runtime_descriptor_shape(descriptor)
        except RuntimeGenerationError as exc:
            raise ValueError(str(exc)) from exc
        architecture = descriptor.get("architecture")
        if architecture in architectures:
            raise ValueError("Runtime catalog contains duplicate architectures.")
        architectures.add(architecture)
        if descriptor.get("application_version") != release_version:
            raise ValueError("Runtime variant application version does not match release.")
        if descriptor.get("settings_schema_version") != settings_schema:
            raise ValueError("Runtime variant settings schema does not match release.")
        validated.append(variant)
    if architectures != {"amd64", "arm64"}:
        raise ValueError("Runtime catalog architectures are incomplete.")
    return validated


def normalize_runtime_release(
    raw: object,
    *,
    platform: Any,
    validate_url: Callable[[str], str],
    allow_incomplete_history: bool = False,
) -> dict[str, Any]:
    from core.runtime_generation import RuntimeGenerationError, validate_runtime_descriptor

    if not isinstance(raw, dict):
        raise TypeError("Runtime catalog releases must be objects.")
    if "runtime_variants" not in raw:
        if "runtime_descriptor" in raw:
            raise ValueError("A runtime descriptor requires signed platform variants.")
        normalized = normalize_catalog_release(raw, validate_url=validate_url)
        if (
            normalized["delivery_mode"] != DeliveryMode.IMAGE_REQUIRED.value
            and normalized["updater_protocol"]
            == VERIFIED_DEPENDENCY_UPDATER_PROTOCOL
        ):
            if not allow_incomplete_history:
                raise ValueError(
                    "The highest protocol-4 release is missing runtime variants."
                )
            return {**normalized, "runtime_compatible": False}
        return normalized
    variants = validate_runtime_release_variants(raw, validate_url=validate_url)
    compatible = []
    normalized = []
    for variant in variants:
        descriptor = variant["runtime_descriptor"]
        projected = normalize_catalog_release({**raw, **variant}, validate_url=validate_url)
        normalized.append(projected)
        try:
            validate_runtime_descriptor(descriptor, platform)
        except RuntimeGenerationError:
            continue
        compatible.append(projected)
    if len(compatible) > 1:
        raise ValueError("Runtime catalog contains ambiguous compatible variants.")
    result = compatible[0] if compatible else normalized[0]
    return {**result, "runtime_compatible": bool(compatible)}
