"""Retain authenticated catalog bytes for image-owned startup verification."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


def _variant(data: bytes, descriptor: dict, public_keys: dict[str, str]) -> dict:
    from core.update_center import (
        MAX_RUNTIME_CATALOG_BYTES,
        UpdateManifestError,
        canonical_payload_bytes,
        verify_ed25519_signature,
    )

    if not data or len(data) > MAX_RUNTIME_CATALOG_BYTES:
        raise UpdateManifestError("Runtime trust catalog exceeds its size limit.")
    try:
        raw = json.loads(data)
        signature = raw["signature"]
        payload = raw["payload"]
        if raw["schema"] != 3 or signature["alg"] != "ed25519":
            raise ValueError("Unsupported runtime trust catalog.")
        verify_ed25519_signature(
            public_keys, signature["key_id"], signature["value"],
            canonical_payload_bytes(payload),
        )
        matches = [
            variant
            for release in payload["releases"]
            if release["version"] == descriptor["application_version"]
            for variant in release.get("runtime_variants", [])
            if variant.get("runtime_descriptor") == descriptor
        ]
        if len(matches) != 1:
            raise ValueError("Runtime descriptor is not uniquely signed in the catalog.")
        return matches[0]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateManifestError("Runtime trust catalog is invalid.") from exc


def _read_existing(path: Path, maximum: int) -> bytes:
    from core.update_center import UpdateBundleError

    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
        or before.st_uid != os.geteuid() or before.st_size > maximum
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        raise UpdateBundleError("Runtime trust file is not a private regular file.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise UpdateBundleError("Runtime trust file changed while opening.")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(maximum + 1)
        after = os.fstat(fd)
        named = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if len(data) > maximum or any(
            getattr(before, field) != getattr(after, field)
            or getattr(after, field) != getattr(named, field) for field in fields
        ):
            raise UpdateBundleError("Runtime trust file changed while reading.")
        return data
    finally:
        os.close(fd)


def persist_runtime_catalog(
    runtime_dir: Path, descriptor: dict, data: bytes, public_keys: dict[str, str],
) -> str:
    """Atomically retain one immutable signed envelope before pointer commit."""
    from core.helpers.atomic_io import fsync_directory
    from core.update_center import MAX_RUNTIME_CATALOG_BYTES, UpdateBundleError

    generation_id = descriptor.get("generation_id")
    if not isinstance(generation_id, str) or not re.fullmatch(r"[a-f0-9]{64}", generation_id):
        raise UpdateBundleError("Runtime trust generation identity is invalid.")
    expected_variant = _variant(data, descriptor, public_keys)
    _validate_runtime_directory(runtime_dir)
    directory = runtime_dir / "trust"
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise UpdateBundleError("Runtime trust directory is not private and owned.")
    path = directory / f"{generation_id}.json"

    def retained_digest() -> str:
        retained = _read_existing(path, MAX_RUNTIME_CATALOG_BYTES)
        if _variant(retained, descriptor, public_keys) != expected_variant:
            raise UpdateBundleError("The retained runtime trust record changed identity.")
        return hashlib.sha256(retained).hexdigest()

    if path.exists() or path.is_symlink():
        return retained_digest()
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".catalog-", delete=False) as file:
            staged = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(staged, path, follow_symlinks=False)
        except FileExistsError:
            return retained_digest()
        staged.unlink()
        staged = None
        fsync_directory(directory)
        return retained_digest()
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _validate_runtime_directory(runtime_dir: Path) -> None:
    from core.update_center import UpdateBundleError

    metadata = runtime_dir.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise UpdateBundleError("Runtime directory is not owned and protected.")


def verify_retained_runtime_catalog(
    runtime_dir: Path, descriptor: dict, public_keys: dict[str, str],
) -> str:
    """Verify retained signing authority before treating a generation as trusted."""
    from core.update_center import MAX_RUNTIME_CATALOG_BYTES, UpdateBundleError

    generation_id = descriptor.get("generation_id")
    if not isinstance(generation_id, str) or not re.fullmatch(r"[a-f0-9]{64}", generation_id):
        raise UpdateBundleError("Runtime trust generation identity is invalid.")
    _validate_runtime_directory(runtime_dir)
    directory = runtime_dir / "trust"
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise UpdateBundleError("Runtime trust directory is not private and owned.")
    data = _read_existing(directory / f"{generation_id}.json", MAX_RUNTIME_CATALOG_BYTES)
    _variant(data, descriptor, public_keys)
    return hashlib.sha256(data).hexdigest()


def delete_retained_runtime_catalog(
    runtime_dir: Path, descriptor: dict, public_keys: dict[str, str],
) -> None:
    """Remove only the unchanged authenticated catalog of a deleted generation."""
    from core.update_center import MAX_RUNTIME_CATALOG_BYTES, UpdateBundleError

    generation_id = descriptor.get("generation_id")
    if not isinstance(generation_id, str) or not re.fullmatch(r"[a-f0-9]{64}", generation_id):
        raise UpdateBundleError("Runtime trust generation identity is invalid.")
    _validate_runtime_directory(runtime_dir)
    directory = runtime_dir / "trust"
    before_directory = directory.lstat()
    if (
        not stat.S_ISDIR(before_directory.st_mode)
        or before_directory.st_uid != os.geteuid()
        or stat.S_IMODE(before_directory.st_mode) != 0o700
    ):
        raise UpdateBundleError("Runtime trust directory is not private and owned.")
    directory_fd = os.open(
        directory, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened_directory = os.fstat(directory_fd)
        if (opened_directory.st_dev, opened_directory.st_ino) != (
            before_directory.st_dev, before_directory.st_ino
        ):
            raise UpdateBundleError("Runtime trust directory changed while opening.")
        name = f"{generation_id}.json"
        before_file = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        data = _read_existing(directory / name, MAX_RUNTIME_CATALOG_BYTES)
        _variant(data, descriptor, public_keys)
        after_file = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        fields = (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
            "st_nlink", "st_uid", "st_mode",
        )
        named_directory = directory.lstat()
        if any(getattr(before_file, field) != getattr(after_file, field) for field in fields) or (
            (opened_directory.st_dev, opened_directory.st_ino)
            != (named_directory.st_dev, named_directory.st_ino)
            or not stat.S_ISDIR(named_directory.st_mode)
            or named_directory.st_uid != os.geteuid()
            or stat.S_IMODE(named_directory.st_mode) != 0o700
        ):
            raise UpdateBundleError("Runtime trust record changed before deletion.")
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
