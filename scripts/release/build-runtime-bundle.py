#!/usr/bin/env python3
"""Build one deterministic, descriptor-bound ChannelWatch runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.runtime_archive import (  # noqa: E402
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_MEMBER_BYTES,
    MAX_BUNDLE_MEMBER_COUNT,
    MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES,
)
from core.runtime_generation import (  # noqa: E402
    DEPENDENCY_INVENTORY_PATH,
    DESCRIPTOR_FILENAME,
    RUNTIME_BUNDLE_FORMAT,
    RUNTIME_DESCRIPTOR_SCHEMA,
    RUNTIME_LAUNCHER_CAPABILITY,
    descriptor_generation_id,
    validate_runtime_descriptor_shape,
)


INVENTORY_FIELDS = frozenset(
    {
        "schema",
        "build",
        "platform",
        "requirements",
        "artifacts",
        "dependencies",
        "files",
        "counts",
    }
)
BUILD_FIELDS = frozenset(
    {"builder_image_digest", "python_version", "pip_version"}
)
PLATFORM_FIELDS = frozenset(
    {
        "architecture",
        "python_implementation",
        "python_cache_tag",
        "python_version",
        "os_family",
        "libc_family",
        "minimum_libc_version",
        "wheel_platform",
    }
)
FILE_FIELDS = frozenset({"path", "sha256", "size", "mode"})
IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
PYTHON_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
TOOL_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[a-z0-9.+-]*)?")
RESERVED_PATHS = frozenset(
    {DESCRIPTOR_FILENAME, DEPENDENCY_INVENTORY_PATH, "prepared.json"}
)
REQUIRED_LEGAL_PATHS = frozenset(
    {
        "core/release_legal/LICENSE",
        "core/release_legal/NOTICE",
        "core/release_legal/THIRD_PARTY_LICENSES.md",
    }
)


class RuntimeBundleBuildError(ValueError):
    """The staged payload cannot be represented by the signed runtime contract."""


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeBundleBuildError("Runtime metadata is not canonical JSON data") from exc


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise RuntimeBundleBuildError("Payload path is not a safe relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or unicodedata.normalize("NFC", value) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeBundleBuildError("Payload path is not a safe relative path")
    return value


def _trusted_root(root: Path, label: str) -> Path:
    absolute = Path(root).absolute()
    try:
        metadata = absolute.lstat()
    except OSError as exc:
        raise RuntimeBundleBuildError(f"{label} cannot be inspected") from exc
    if not stat.S_ISDIR(metadata.st_mode) or absolute.is_symlink():
        raise RuntimeBundleBuildError(f"{label} must be a regular directory")
    return absolute


def _read_regular(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise RuntimeBundleBuildError(f"{label} cannot be inspected") from exc
    if stat.S_ISLNK(before.st_mode):
        raise RuntimeBundleBuildError(f"{label} is a symbolic link")
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeBundleBuildError(f"{label} is not a regular file")
    if before.st_nlink != 1:
        raise RuntimeBundleBuildError(f"{label} has multiple filesystem links")
    if before.st_size < 0 or before.st_size > MAX_BUNDLE_MEMBER_BYTES:
        raise RuntimeBundleBuildError(f"{label} exceeds the archive member size limit")
    try:
        content = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise RuntimeBundleBuildError(f"{label} cannot be read") from exc
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
    if any(getattr(before, field) != getattr(after, field) for field in identity):
        raise RuntimeBundleBuildError(f"{label} changed while it was read")
    if len(content) != before.st_size:
        raise RuntimeBundleBuildError(f"{label} changed while it was read")
    return content, before


def _file_record(relative: str, content: bytes, metadata: os.stat_result) -> dict[str, Any]:
    mode = "0755" if stat.S_IMODE(metadata.st_mode) & 0o111 else "0644"
    return {
        "path": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "mode": mode,
    }


def _collect_payload(
    root: Path,
    *,
    allowed_prefixes: tuple[str, ...],
    allowed_exact: frozenset[str] = frozenset(),
    label: str,
) -> dict[str, tuple[bytes, dict[str, Any]]]:
    result: dict[str, tuple[bytes, dict[str, Any]]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = _safe_relative(path.relative_to(root).as_posix())
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimeBundleBuildError(f"{label} entry cannot be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeBundleBuildError(f"{label} contains a symbolic link: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeBundleBuildError(f"{label} contains a special entry: {relative}")
        if relative not in allowed_exact and not relative.startswith(allowed_prefixes):
            top = PurePosixPath(relative).parts[0]
            if relative in RESERVED_PATHS or top == "environment":
                raise RuntimeBundleBuildError(
                    f"{label} contains a reserved runtime path: {relative}"
                )
            raise RuntimeBundleBuildError(
                f"{label} contains a file outside its application roots: {relative}"
            )
        content, opened = _read_regular(path, f"{label} file {relative}")
        result[relative] = (content, _file_record(relative, content, opened))
    return result


def _check_path_collisions(paths: Sequence[str]) -> None:
    seen: dict[str, str] = {}
    for path in paths:
        identity = unicodedata.normalize("NFC", path).casefold()
        previous = seen.get(identity)
        if previous is not None and previous != path:
            raise RuntimeBundleBuildError(
                f"Payload path collision between {previous!r} and {path!r}"
            )
        seen[identity] = path


def _read_inventory(
    dependency_root: Path,
    dependency_payload: dict[str, tuple[bytes, dict[str, Any]]],
) -> tuple[bytes, dict[str, Any]]:
    inventory_path = dependency_root / PurePosixPath(DEPENDENCY_INVENTORY_PATH)
    inventory_raw, _metadata = _read_regular(inventory_path, "Dependency inventory")
    try:
        inventory = json.loads(inventory_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeBundleBuildError("Dependency inventory is not valid JSON") from exc
    if not isinstance(inventory, dict) or set(inventory) != INVENTORY_FIELDS:
        raise RuntimeBundleBuildError(
            "Dependency inventory fields are incomplete or unsupported"
        )
    platform_value = inventory.get("platform")
    if not isinstance(platform_value, dict) or set(platform_value) != PLATFORM_FIELDS:
        raise RuntimeBundleBuildError("Dependency inventory platform fields are incomplete")
    build = inventory.get("build")
    if not isinstance(build, dict) or set(build) != BUILD_FIELDS:
        raise RuntimeBundleBuildError(
            "Dependency build provenance fields are incomplete"
        )
    builder_image_digest = build.get("builder_image_digest")
    python_version = build.get("python_version")
    pip_version = build.get("pip_version")
    if (
        not isinstance(builder_image_digest, str)
        or IMAGE_DIGEST_PATTERN.fullmatch(builder_image_digest) is None
    ):
        raise RuntimeBundleBuildError("Dependency builder image digest is invalid")
    if (
        not isinstance(python_version, str)
        or PYTHON_VERSION_PATTERN.fullmatch(python_version) is None
        or python_version != platform_value.get("python_version")
    ):
        raise RuntimeBundleBuildError("Dependency build Python version is invalid")
    if (
        not isinstance(pip_version, str)
        or TOOL_VERSION_PATTERN.fullmatch(pip_version) is None
    ):
        raise RuntimeBundleBuildError("Dependency build pip version is invalid")
    inventory_files = inventory.get("files")
    actual_records = [dependency_payload[path][1] for path in sorted(dependency_payload)]
    if not isinstance(inventory_files, list) or inventory_files != actual_records:
        raise RuntimeBundleBuildError("Dependency inventory does not exactly match its payload")
    if any(
        not isinstance(item, dict) or set(item) != FILE_FIELDS
        for item in inventory_files
    ):
        raise RuntimeBundleBuildError("Dependency inventory file records are invalid")
    counts = inventory.get("counts")
    expected_counts = {
        "files": len(actual_records),
        "bytes": sum(record["size"] for record in actual_records),
    }
    if counts != expected_counts:
        raise RuntimeBundleBuildError("Dependency inventory counts do not match its payload")
    return inventory_raw, inventory


def _libc_floor(value: Any) -> list[int]:
    if not isinstance(value, str):
        raise RuntimeBundleBuildError("Dependency minimum libc version is invalid")
    parts = value.split(".")
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        raise RuntimeBundleBuildError("Dependency minimum libc version is invalid")
    return [int(part) for part in parts]


def _zip_timestamp(source_date_epoch: int) -> tuple[int, int, int, int, int, int]:
    if isinstance(source_date_epoch, bool) or not isinstance(source_date_epoch, int):
        raise RuntimeBundleBuildError("Source date epoch must be an integer")
    try:
        timestamp = datetime.fromtimestamp(source_date_epoch, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise RuntimeBundleBuildError("Source date epoch is outside the ZIP range") from exc
    if not 1980 <= timestamp.year <= 2107:
        raise RuntimeBundleBuildError("Source date epoch is outside the ZIP range")
    return (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second - timestamp.second % 2,
    )


def _zip_info(
    path: str, *, mode: int, timestamp: tuple[int, int, int, int, int, int]
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=timestamp)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def _write_new_file(path: Path, content: bytes) -> None:
    path = Path(path)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o644,
        )
    except FileExistsError as exc:
        raise RuntimeBundleBuildError(f"Output already exists: {path}") from exc
    except OSError as exc:
        raise RuntimeBundleBuildError(f"Output cannot be created: {path}") from exc
    try:
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short output write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)


def build_runtime_bundle(
    *,
    app_root: Path,
    dependency_root: Path,
    output_path: Path,
    application_version: str,
    source_commit: str,
    settings_schema_version: int,
    source_date_epoch: int,
) -> dict[str, Any]:
    """Build a deterministic runtime ZIP and return its embedded descriptor."""

    output_path = Path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise RuntimeBundleBuildError(f"Output already exists: {output_path}")
    app_root = _trusted_root(app_root, "Application payload root")
    dependency_root = _trusted_root(dependency_root, "Dependency payload root")
    application_payload = _collect_payload(
        app_root,
        allowed_prefixes=("core/", "ui/backend/"),
        label="Application payload",
    )
    dependency_all = _collect_payload(
        dependency_root,
        allowed_prefixes=("dependencies/site-packages/",),
        allowed_exact=frozenset({DEPENDENCY_INVENTORY_PATH}),
        label="Dependency payload",
    )
    dependency_payload = {
        path: value
        for path, value in dependency_all.items()
        if path != DEPENDENCY_INVENTORY_PATH
    }
    inventory_raw, inventory = _read_inventory(dependency_root, dependency_payload)
    missing_legal = sorted(REQUIRED_LEGAL_PATHS.difference(application_payload))
    if missing_legal:
        raise RuntimeBundleBuildError(
            "Application payload is missing required release legal files: "
            + ", ".join(missing_legal)
        )

    payload = {**application_payload, **dependency_payload}
    if len(payload) != len(application_payload) + len(dependency_payload):
        raise RuntimeBundleBuildError("Application and dependency payload paths collide")
    all_paths = [DESCRIPTOR_FILENAME, DEPENDENCY_INVENTORY_PATH, *payload]
    _check_path_collisions(all_paths)
    if len(all_paths) > MAX_BUNDLE_MEMBER_COUNT:
        raise RuntimeBundleBuildError("Runtime archive has too many members")

    records = [payload[path][1] for path in sorted(payload)]
    platform_value = inventory["platform"]
    descriptor: dict[str, Any] = {
        "schema": RUNTIME_DESCRIPTOR_SCHEMA,
        "generation_id": "",
        "application_version": application_version,
        "source_commit": source_commit,
        "dependency_inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
        "bundle_format": RUNTIME_BUNDLE_FORMAT,
        "required_launcher_capability": RUNTIME_LAUNCHER_CAPABILITY,
        "settings_schema_version": settings_schema_version,
        "architecture": platform_value["architecture"],
        "python_implementation": platform_value["python_implementation"],
        "python_cache_tag": platform_value["python_cache_tag"],
        "python_version": platform_value["python_version"],
        "os_family": platform_value["os_family"],
        "libc_family": platform_value["libc_family"],
        "minimum_libc_version": _libc_floor(platform_value["minimum_libc_version"]),
        "dependency_inventory_path": DEPENDENCY_INVENTORY_PATH,
        "files": records,
        "expanded_size": len(inventory_raw) + sum(record["size"] for record in records),
    }
    descriptor["generation_id"] = descriptor_generation_id(descriptor)
    try:
        validate_runtime_descriptor_shape(descriptor)
    except Exception as exc:
        raise RuntimeBundleBuildError(f"Runtime descriptor is invalid: {exc}") from exc
    descriptor_raw = _canonical_json(descriptor)

    members: dict[str, tuple[bytes, int]] = {
        DESCRIPTOR_FILENAME: (descriptor_raw, 0o644),
        DEPENDENCY_INVENTORY_PATH: (inventory_raw, 0o644),
        **{
            path: (content, int(record["mode"], 8))
            for path, (content, record) in payload.items()
        },
    }
    if any(len(content) > MAX_BUNDLE_MEMBER_BYTES for content, _mode in members.values()):
        raise RuntimeBundleBuildError("Runtime archive member exceeds the size limit")
    if (
        sum(len(content) for content, _mode in members.values())
        > MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES
    ):
        raise RuntimeBundleBuildError("Runtime archive expanded size exceeds the limit")

    timestamp = _zip_timestamp(source_date_epoch)
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in sorted(members):
            content, mode = members[relative]
            archive.writestr(
                _zip_info(relative, mode=mode, timestamp=timestamp), content
            )
    bundle = output.getvalue()
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise RuntimeBundleBuildError("Runtime archive exceeds the download size limit")
    _write_new_file(output_path, bundle)
    return descriptor


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--dependency-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--application-version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--settings-schema-version", type=int, required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        descriptor = build_runtime_bundle(
            app_root=args.app_root,
            dependency_root=args.dependency_root,
            output_path=args.output,
            application_version=args.application_version,
            source_commit=args.source_commit,
            settings_schema_version=args.settings_schema_version,
            source_date_epoch=args.source_date_epoch,
        )
    except RuntimeBundleBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(descriptor, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
