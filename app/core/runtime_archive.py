"""Bounded extraction for signed dependency runtime generations."""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from core.runtime_generation import (
    DEPENDENCY_INVENTORY_PATH,
    DESCRIPTOR_FILENAME,
    RuntimeGeneration,
    RuntimeGenerationError,
    detect_runtime_platform,
    prepare_runtime_generation,
    validate_runtime_storage_directory,
    validate_runtime_descriptor,
)
from core.update_center import (
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_MEMBER_BYTES,
    MAX_BUNDLE_MEMBER_COUNT,
    MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES,
    UpdateBundleError,
)

RUNTIME_PREPARATION_HEADROOM_BYTES = 64 * 1024 * 1024


def _source_size(source: bytes | BinaryIO) -> int:
    if isinstance(source, bytes):
        return len(source)
    source.seek(0, os.SEEK_END)
    size = source.tell()
    source.seek(0)
    return size


def _archive_source(source: bytes | BinaryIO) -> io.BytesIO | BinaryIO:
    if isinstance(source, bytes):
        return io.BytesIO(source)
    source.seek(0)
    return source


def _safe_member_path(name: str) -> tuple[str, bool]:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise UpdateBundleError("Runtime archive contains an unsafe member path.")
    directory = name.endswith("/")
    value = name[:-1] if directory else name
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise UpdateBundleError("Runtime archive contains an unsafe member path.")
    return value, directory


def _allowed_directories(files: set[str]) -> set[str]:
    directories: set[str] = set()
    for filename in files:
        parent = PurePosixPath(filename).parent
        while parent.parts:
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _signed_modes(descriptor: dict[str, Any]) -> dict[str, int]:
    return {
        DESCRIPTOR_FILENAME: 0o644,
        DEPENDENCY_INVENTORY_PATH: 0o644,
        **{
            str(record["path"]): int(str(record["mode"]), 8)
            for record in descriptor["files"]
        },
    }


def _validate_member_mode(
    info: zipfile.ZipInfo, *, directory: bool, signed_mode: int | None
) -> None:
    unix_mode = info.external_attr >> 16
    if info.create_system != 3 or unix_mode == 0:
        return
    if stat.S_ISLNK(unix_mode):
        raise UpdateBundleError("Runtime archive contains a symbolic link.")
    if directory:
        if not stat.S_ISDIR(unix_mode):
            raise UpdateBundleError("Runtime archive contains a special entry.")
        if stat.S_IMODE(unix_mode) not in {0, 0o755}:
            raise UpdateBundleError("Runtime archive directory mode is not allowed.")
        return
    if not stat.S_ISREG(unix_mode):
        raise UpdateBundleError("Runtime archive contains a special entry.")
    archived_mode = stat.S_IMODE(unix_mode)
    if archived_mode & (stat.S_ISUID | stat.S_ISGID) or archived_mode != signed_mode:
        raise UpdateBundleError("Runtime archive file mode does not match the signed mode.")


def _archive_files(
    archive: zipfile.ZipFile, descriptor: dict[str, Any]
) -> dict[str, zipfile.ZipInfo]:
    signed_modes = _signed_modes(descriptor)
    expected_files = set(signed_modes)
    expected_directories = _allowed_directories(expected_files)
    selected: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    total_uncompressed = 0
    members = archive.infolist()
    if len(members) > MAX_BUNDLE_MEMBER_COUNT:
        raise UpdateBundleError("Runtime archive has too many members.")
    for info in members:
        relative, directory = _safe_member_path(info.filename)
        if relative in seen:
            raise UpdateBundleError("Runtime archive contains a duplicate member.")
        seen.add(relative)
        if info.flag_bits & 0x1:
            raise UpdateBundleError("Runtime archive contains an encrypted member.")
        if directory:
            if relative not in expected_directories:
                raise UpdateBundleError("Runtime archive directory is not signed.")
            _validate_member_mode(info, directory=True, signed_mode=None)
            continue
        if relative not in expected_files:
            raise UpdateBundleError("Runtime archive member is not signed by the descriptor.")
        if info.file_size < 0 or info.file_size > MAX_BUNDLE_MEMBER_BYTES:
            raise UpdateBundleError("Runtime archive member exceeds the size limit.")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES:
            raise UpdateBundleError("Runtime archive expanded size exceeds the limit.")
        _validate_member_mode(
            info, directory=False, signed_mode=signed_modes[relative]
        )
        selected[relative] = info
    missing = expected_files.difference(selected)
    if missing:
        raise UpdateBundleError("Runtime archive is missing a signed member.")
    return selected


def validate_runtime_archive(
    bundle_bytes: bytes | BinaryIO, descriptor: dict[str, Any]
) -> dict[str, zipfile.ZipInfo]:
    """Validate every archive member against the signed runtime descriptor."""

    if _source_size(bundle_bytes) > MAX_BUNDLE_BYTES:
        raise UpdateBundleError("Runtime archive exceeds the download size limit.")
    try:
        validate_runtime_descriptor(descriptor, detect_runtime_platform())
    except RuntimeGenerationError as exc:
        raise UpdateBundleError(str(exc)) from exc
    try:
        archive = zipfile.ZipFile(_archive_source(bundle_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise UpdateBundleError("Runtime archive is not a valid zip file.") from exc
    with archive:
        selected = _archive_files(archive, descriptor)
        if archive.testzip() is not None:
            raise UpdateBundleError("Runtime archive integrity check failed.")
        try:
            archived_descriptor = json.loads(
                archive.read(selected[DESCRIPTOR_FILENAME]).decode("utf-8")
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise UpdateBundleError("Runtime archive descriptor is invalid.") from exc
        if archived_descriptor != descriptor:
            raise UpdateBundleError(
                "Runtime archive descriptor does not match the signed descriptor."
            )
        inventory = archive.read(selected[DEPENDENCY_INVENTORY_PATH])
        if hashlib.sha256(inventory).hexdigest() != descriptor[
            "dependency_inventory_sha256"
        ]:
            raise UpdateBundleError(
                "Runtime archive inventory does not match the signed descriptor."
            )
        expected_size = len(inventory)
        for record in descriptor["files"]:
            content = archive.read(selected[record["path"]])
            if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != (
                record["sha256"]
            ):
                raise UpdateBundleError(
                    "Runtime archive payload does not match the signed descriptor."
                )
            expected_size += len(content)
        if expected_size != descriptor["expanded_size"]:
            raise UpdateBundleError(
                "Runtime archive expanded size does not match the signed descriptor."
            )
        return selected


def _trusted_releases_dir(releases_dir: Path) -> Path:
    root = Path(releases_dir)
    if not root.is_absolute():
        raise UpdateBundleError("Runtime releases directory path is not absolute.")
    try:
        validate_runtime_storage_directory(
            root.parent, label="Runtime control directory"
        )
        root.mkdir(mode=0o755, exist_ok=True)
    except OSError as exc:
        raise UpdateBundleError("Runtime releases directory is unavailable.") from exc
    except RuntimeGenerationError as exc:
        raise UpdateBundleError(str(exc)) from exc
    try:
        return validate_runtime_storage_directory(
            root, label="Runtime releases directory"
        )
    except RuntimeGenerationError as exc:
        raise UpdateBundleError(str(exc)) from exc


def _require_free_space(root: Path, bundle_size: int, expanded_size: int) -> None:
    required = bundle_size + expanded_size + RUNTIME_PREPARATION_HEADROOM_BYTES
    try:
        available = shutil.disk_usage(root).free
    except OSError as exc:
        raise UpdateBundleError("Runtime storage capacity cannot be inspected.") from exc
    if available < required:
        raise UpdateBundleError(
            "Runtime storage does not have enough free space for this generation."
        )


def _write_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    *,
    mode: int,
) -> None:
    destination.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, mode)
    except OSError as exc:
        raise UpdateBundleError("Runtime archive member could not be created safely.") from exc
    try:
        with archive.open(info, "r") as source:
            remaining = info.file_size
            while remaining:
                chunk = source.read(min(65_536, remaining))
                if not chunk:
                    raise UpdateBundleError("Runtime archive member ended unexpectedly.")
                pending = memoryview(chunk)
                while pending:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise UpdateBundleError(
                            "Runtime archive member could not be written completely."
                        )
                    pending = pending[written:]
                remaining -= len(chunk)
            if source.read(1):
                raise UpdateBundleError("Runtime archive member exceeds its declared size.")
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(directory, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode) or (
                hasattr(os, "geteuid") and metadata.st_uid != os.geteuid()
            ):
                raise UpdateBundleError(
                    "Runtime archive directory could not be trusted."
                )
            os.fchmod(descriptor, 0o755)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def install_runtime_archive(
    bundle_bytes: bytes | BinaryIO, releases_dir: Path, descriptor: dict[str, Any]
) -> RuntimeGeneration:
    """Extract and prepare one immutable descriptor-named runtime generation."""

    selected = validate_runtime_archive(bundle_bytes, descriptor)
    root = _trusted_releases_dir(releases_dir)
    destination = root / descriptor["generation_id"]
    if destination.exists() or destination.is_symlink():
        try:
            metadata = destination.lstat()
        except OSError as exc:
            raise UpdateBundleError("Runtime generation cannot be inspected.") from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise UpdateBundleError("Runtime generation path is not a directory.")
        try:
            return prepare_runtime_generation(destination, descriptor)
        except RuntimeGenerationError as exc:
            raise UpdateBundleError(str(exc)) from exc

    _require_free_space(root, _source_size(bundle_bytes), int(descriptor["expanded_size"]))
    temporary = root / f".{descriptor['generation_id']}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o700, exist_ok=False)
    signed_modes = _signed_modes(descriptor)
    try:
        with zipfile.ZipFile(_archive_source(bundle_bytes), "r") as archive:
            for relative in sorted(selected):
                _write_member(
                    archive,
                    selected[relative],
                    temporary / PurePosixPath(relative),
                    mode=signed_modes[relative],
                )
        _fsync_tree(temporary)
        try:
            os.rename(temporary, destination)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY} or not destination.is_dir():
                raise
        if temporary.exists():
            shutil.rmtree(temporary)
        _fsync_directory(root)
    except OSError as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise UpdateBundleError("Runtime archive could not be installed safely.") from exc
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    try:
        return prepare_runtime_generation(destination, descriptor)
    except RuntimeGenerationError as exc:
        raise UpdateBundleError(str(exc)) from exc
