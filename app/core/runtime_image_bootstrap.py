"""Verify and start dependency generations from immutable image code."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from core import runtime_generation as _image_generation
from core import update_center as _image_update_center
from core.runtime_generation import (
    RuntimeGeneration,
    descriptor_generation_id,
    detect_runtime_platform,
    verify_runtime_generation,
)
from core.update_center import (
    MAX_RUNTIME_CATALOG_BYTES,
    UPDATE_PUBLIC_KEYS,
    read_update_document_bytes,
)


MAX_TRUST_BYTES = 1024 * 1024
MAX_DESCRIPTOR_BYTES = 4 * 1024 * 1024
RUNTIME_IDENTITY_FIELDS = frozenset(
    {
        "runtime_generation_id",
        "runtime_descriptor_sha256",
        "dependency_inventory_sha256",
        "runtime_trust_sha256",
    }
)


class RuntimeImageBootstrapError(RuntimeError):
    """A dependency generation failed image-owned startup verification."""


def _read_unchanged_regular(path: Path, *, maximum_size: int, label: str) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeImageBootstrapError(f"Safe {label} reads are unavailable.")
    try:
        before = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as exc:
        raise RuntimeImageBootstrapError(
            f"The {label} is unavailable or unsafe."
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_nlink != 1
            or opened.st_nlink != 1
            or before.st_uid != os.geteuid()
            or opened.st_uid != os.geteuid()
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size < 0
            or opened.st_size > maximum_size
        ):
            raise RuntimeImageBootstrapError(
                f"The {label} is not a trusted bounded regular file."
            )
        chunks: list[bytes] = []
        remaining = maximum_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum_size:
            raise RuntimeImageBootstrapError(f"The {label} exceeds its size limit.")
        final = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        named = path.lstat()
    except OSError as exc:
        raise RuntimeImageBootstrapError(
            f"The {label} changed while it was being read."
        ) from exc
    fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_nlink",
        "st_uid",
    )
    if any(
        getattr(before, field) != getattr(opened, field)
        or getattr(opened, field) != getattr(final, field)
        or getattr(final, field) != getattr(named, field)
        for field in fields
    ):
        raise RuntimeImageBootstrapError(
            f"The {label} changed while it was being read."
        )
    return payload


def _read_json(path: Path, *, maximum_size: int, label: str) -> tuple[Any, bytes]:
    raw = _read_unchanged_regular(path, maximum_size=maximum_size, label=label)
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeImageBootstrapError(f"The {label} is not valid JSON.") from exc


def _assert_module_in_image(module: object, image_app_dir: Path) -> None:
    origin = getattr(module, "__file__", None)
    try:
        Path(str(origin)).resolve(strict=True).relative_to(
            image_app_dir.resolve(strict=True)
        )
    except (OSError, ValueError) as exc:
        raise RuntimeImageBootstrapError(
            "A dependency bootstrap authority did not come from the image."
        ) from exc


def _assert_image_authorities(image_app_dir: Path) -> None:
    _assert_module_in_image(sys.modules[__name__], image_app_dir)
    _assert_module_in_image(_image_generation, image_app_dir)
    _assert_module_in_image(_image_update_center, image_app_dir)


def _selected_generation(path: Path, runtime_dir: Path, generation_id: str) -> Path:
    try:
        trusted_runtime = _image_generation.validate_runtime_storage_directory(
            runtime_dir, label="Runtime control directory"
        )
        releases = _image_generation.validate_runtime_storage_directory(
            trusted_runtime / "releases", label="Runtime releases directory"
        )
        selected = _image_generation.validate_runtime_storage_directory(
            path, label="Runtime generation directory"
        )
    except (OSError, ValueError) as exc:
        raise RuntimeImageBootstrapError(
            "The active runtime generation path is not trusted."
        ) from exc
    if selected.parent != releases or selected.name != generation_id:
        raise RuntimeImageBootstrapError(
            "The active runtime generation identity is inconsistent."
        )
    return selected


def _generation_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeImageBootstrapError(
            "The active runtime generation cannot be inspected safely."
        ) from exc
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid)


def _active_state(runtime_dir: Path) -> dict[str, Any]:
    try:
        os.lstat(runtime_dir / "active.json")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RuntimeImageBootstrapError(
            "The active runtime selection cannot be inspected safely."
        ) from exc
    value, _raw = _read_json(
        runtime_dir / "active.json",
        maximum_size=MAX_TRUST_BYTES,
        label="active runtime selection",
    )
    if not isinstance(value, dict):
        raise RuntimeImageBootstrapError("The active runtime selection is invalid.")
    return value


def supervise_image_worker(**kwargs: Any) -> None:
    from core import runtime_dependency_bridge

    _assert_module_in_image(runtime_dependency_bridge, kwargs["image_app_dir"])
    runtime_dependency_bridge.supervise_image_worker(**kwargs)


def maybe_supervise(
    *,
    image_app_dir: Path,
    runtime_dir: Path,
    app_dir: Path,
    launcher_protocol: int,
    mode: str,
    app_args: tuple[str, ...],
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
) -> bool:
    """Supervise a signed dependency generation before selected app imports."""

    image_app_dir = image_app_dir.resolve()
    try:
        if app_dir.resolve() == image_app_dir:
            return False
    except OSError:
        pass
    if launcher_protocol >= 4:
        try:
            runtime_dir = _image_generation.validate_runtime_storage_directory(
                runtime_dir, label="Runtime control directory"
            )
        except (OSError, ValueError) as exc:
            raise RuntimeImageBootstrapError(
                "The runtime control directory is not trusted and protected."
            ) from exc
    active = _active_state(runtime_dir)
    present_identity = RUNTIME_IDENTITY_FIELDS.intersection(active)
    if not present_identity:
        if launcher_protocol >= 4:
            raise RuntimeImageBootstrapError(
                "The selected runtime dependency identity is missing."
            )
        return False
    if launcher_protocol < 4:
        raise RuntimeImageBootstrapError(
            "Runtime dependency identity requires launcher protocol 4."
        )
    if present_identity != RUNTIME_IDENTITY_FIELDS:
        raise RuntimeImageBootstrapError(
            "The active runtime dependency identity is incomplete."
        )
    if (
        active.get("activation_protocol") != 4
        or not str(active.get("activation_id") or "")
    ):
        raise RuntimeImageBootstrapError(
            "The active runtime dependency activation is invalid."
        )
    _assert_image_authorities(image_app_dir)

    generation_id = str(active["runtime_generation_id"])
    descriptor_sha256 = str(active["runtime_descriptor_sha256"]).lower()
    dependency_sha256 = str(active["dependency_inventory_sha256"]).lower()
    trust_sha256 = str(active["runtime_trust_sha256"]).lower()
    if any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in (
            generation_id,
            descriptor_sha256,
            dependency_sha256,
            trust_sha256,
        )
    ):
        raise RuntimeImageBootstrapError(
            "The active runtime dependency identity is invalid."
        )
    generation = _selected_generation(app_dir, runtime_dir, generation_id)
    generation_identity = _generation_identity(generation)
    try:
        active_path = _selected_generation(
            Path(str(active["path"])), runtime_dir, generation_id
        )
    except (KeyError, RuntimeImageBootstrapError) as exc:
        raise RuntimeImageBootstrapError(
            "The active runtime generation path is invalid."
        ) from exc
    if active_path != generation:
        raise RuntimeImageBootstrapError(
            "The active runtime generation identity is inconsistent."
        )

    trust, trust_raw = _read_json(
        runtime_dir / "trust" / f"{generation_id}.json",
        maximum_size=MAX_RUNTIME_CATALOG_BYTES,
        label="signed runtime trust catalog",
    )
    if hashlib.sha256(trust_raw).hexdigest() != trust_sha256:
        raise RuntimeImageBootstrapError("The signed runtime trust digest changed.")
    if not isinstance(trust, dict) or trust.get("schema") != 3:
        raise RuntimeImageBootstrapError("The signed runtime trust catalog is invalid.")

    version = str(active.get("version") or "").strip().lstrip("v")
    runtime_abi = str(active.get("runtime_abi") or "")
    try:
        settings_schema_version = int(active["settings_schema_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeImageBootstrapError(
            "The active runtime settings identity is invalid."
        ) from exc
    try:
        document = read_update_document_bytes(
            trust_raw,
            UPDATE_PUBLIC_KEYS,
            current_version=version,
            runtime_abi=runtime_abi,
            settings_schema_version=settings_schema_version,
            launcher_protocol=launcher_protocol,
            runtime_platform=detect_runtime_platform(
                launcher_protocol=launcher_protocol
            ),
        )
    except Exception as exc:
        raise RuntimeImageBootstrapError(
            f"The signed runtime catalog is not compatible: {exc}"
        ) from exc
    release = document.get("payload")
    if not isinstance(release, dict) or str(release.get("version") or "") != version:
        raise RuntimeImageBootstrapError(
            "The signed runtime catalog version does not match the active runtime."
        )
    signed_descriptor = release.get("runtime_descriptor")
    if not isinstance(signed_descriptor, dict):
        raise RuntimeImageBootstrapError(
            "The signed runtime descriptor is missing."
        )
    descriptor, _descriptor_raw = _read_json(
        generation / "channelwatch-bundle.json",
        maximum_size=MAX_DESCRIPTOR_BYTES,
        label="runtime descriptor",
    )
    if descriptor != signed_descriptor:
        raise RuntimeImageBootstrapError(
            "The runtime descriptor does not match the signed catalog."
        )
    if (
        descriptor_generation_id(descriptor) != descriptor_sha256
        or descriptor_sha256 != generation_id
        or descriptor.get("generation_id") != generation_id
        or descriptor.get("dependency_inventory_sha256") != dependency_sha256
    ):
        raise RuntimeImageBootstrapError(
            "The active runtime descriptor identity is inconsistent."
        )
    manifest = active.get("manifest")
    if not isinstance(manifest, dict) or (
        str(manifest.get("bundle_sha256") or "").lower()
        != str(release.get("bundle_sha256") or "").lower()
        or str(manifest.get("key_id") or "") != str(release.get("key_id") or "")
    ):
        raise RuntimeImageBootstrapError(
            "The active runtime bundle identity does not match signed trust."
        )
    receipt, _receipt_raw = _read_json(
        generation / "prepared.json",
        maximum_size=MAX_DESCRIPTOR_BYTES,
        label="prepared runtime receipt",
    )
    if not isinstance(receipt, dict):
        raise RuntimeImageBootstrapError("The prepared runtime receipt is invalid.")
    try:
        runtime = RuntimeGeneration(
            generation_id=str(receipt["generation_id"]),
            app_dir=generation,
            python_executable=generation / str(receipt["python_executable"]),
            dependency_digest=str(receipt["dependency_digest"]),
        )
    except KeyError as exc:
        raise RuntimeImageBootstrapError(
            "The prepared runtime receipt is incomplete."
        ) from exc
    if (
        runtime.generation_id != generation_id
        or runtime.dependency_digest != dependency_sha256
    ):
        raise RuntimeImageBootstrapError(
            "The prepared runtime identity is inconsistent."
        )
    verify_runtime_generation(runtime)
    if _generation_identity(generation) != generation_identity:
        raise RuntimeImageBootstrapError(
            "The active runtime generation changed during startup verification."
        )
    if _active_state(runtime_dir) != active:
        raise RuntimeImageBootstrapError(
            "The active runtime selection changed during startup verification."
        )
    supervise_image_worker(
        image_app_dir=image_app_dir,
        runtime_dir=runtime_dir.resolve(),
        generation=generation,
        activation_id=str(active.get("activation_id") or ""),
        descriptor_sha256=descriptor_sha256,
        mode=mode,
        app_args=app_args,
        host=host,
        port=port,
        log_level=log_level,
    )
    return True
