"""Resolve immutable container-image metadata for runtime compatibility checks."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_MAX_METADATA_BYTES = 64 * 1024


@dataclass(frozen=True)
class ImageMetadata:
    version: str
    runtime_abi: str | None
    settings_schema_version: int | None
    source: str
    environment_mismatch: bool


def _normalized_version(value: object) -> str:
    version = str(value or "").strip().lstrip("v")
    return version if _VERSION_RE.fullmatch(version) else ""


def resolve_image_metadata(
    *,
    image_app_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ImageMetadata:
    """Prefer build-owned metadata and use the environment only as a fallback.

    Container managers can preserve stale environment values while recreating a
    container from a newer image.  The file baked into the image cannot drift in
    that way, so a valid file is authoritative.  Historical/dev runtimes without
    the file retain the environment fallback.
    """

    environment = os.environ if environ is None else environ
    root = Path(
        image_app_dir
        if image_app_dir is not None
        else environment.get("CHANNELWATCH_IMAGE_APP_DIR", "/app")
    )
    metadata_path = root / "channelwatch-image.json"
    environment_version = _normalized_version(
        environment.get("CHANNELWATCH_IMAGE_VERSION", "")
    )

    try:
        file_size = metadata_path.stat().st_size
        if file_size <= 0 or file_size > _MAX_METADATA_BYTES:
            raise ValueError("image metadata size is invalid")
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("image metadata must be an object")
        version = _normalized_version(payload.get("version"))
        if not version:
            raise ValueError("image metadata version is invalid")
        runtime_abi = str(payload.get("runtime_abi") or "").strip() or None
        raw_schema = payload.get("settings_schema_version")
        schema = int(raw_schema) if raw_schema is not None else None
        if schema is not None and schema < 0:
            raise ValueError("image metadata schema is invalid")
        return ImageMetadata(
            version=version,
            runtime_abi=runtime_abi,
            settings_schema_version=schema,
            source="embedded",
            environment_mismatch=bool(
                environment_version and environment_version != version
            ),
        )
    except FileNotFoundError:
        return ImageMetadata(
            version=environment_version or "unknown",
            runtime_abi=None,
            settings_schema_version=None,
            source="environment" if environment_version else "unavailable",
            environment_mismatch=False,
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        # A present-but-invalid immutable record is not replaced by a mutable
        # environment claim.  Report it as unavailable so updates fail safely.
        return ImageMetadata(
            version="unknown",
            runtime_abi=None,
            settings_schema_version=None,
            source="invalid",
            environment_mismatch=bool(environment_version),
        )
