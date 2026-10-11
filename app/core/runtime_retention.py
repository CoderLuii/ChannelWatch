"""Conservative cleanup for verified persistent runtime generations."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime_generation import (
    DESCRIPTOR_FILENAME,
    MAX_DESCRIPTOR_BYTES,
    PREPARED_RECEIPT_FILENAME,
    RuntimeGeneration,
    verify_runtime_generation,
)

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_CONTROL_MAX_BYTES = 256 * 1024
_CONTROL_NAMES = frozenset(
    {
        "active.json",
        "rollback.json",
        "activation-pending.json",
        "activation-core-ready.json",
        "activation-ui-ready.json",
        "update-job.json",
        "restart-required.json",
        "update-scheduler.json",
    }
)


@dataclass(frozen=True)
class RuntimeRetentionResult:
    deleted: tuple[Path, ...]
    retained: tuple[Path, ...]
    warnings: tuple[str, ...]


def _entry_owned_by_current_user(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return not hasattr(os, "geteuid") or metadata.st_uid == os.geteuid()


def _trusted_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        after = path.lstat()
    except OSError:
        return False
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_ctime_ns")
    return bool(
        stat.S_ISDIR(metadata.st_mode)
        and resolved == path.absolute()
        and (not hasattr(os, "geteuid") or metadata.st_uid == os.geteuid())
        and (
            os.name == "nt" or (stat.S_IMODE(metadata.st_mode) & 0o022) == 0
        )
        and all(
            getattr(metadata, field) == getattr(after, field) for field in fields
        )
    )


def _fsync_trusted_directory(path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("safe directory synchronization is unavailable")
    before = path.lstat()
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or not stat.S_ISDIR(opened.st_mode)
            or (hasattr(os, "geteuid") and opened.st_uid != os.geteuid())
            or (os.name != "nt" and stat.S_IMODE(opened.st_mode) & 0o022)
        ):
            raise OSError("runtime directory changed before synchronization")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_control(path: Path, *, limit: int = _CONTROL_MAX_BYTES) -> Any:
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("safe control reads are unavailable")
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or not _entry_owned_by_current_user(path)
        or before.st_size < 0
        or before.st_size > limit
    ):
        raise OSError("control file is not trusted")
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > limit
            or (hasattr(os, "geteuid") and opened.st_uid != os.geteuid())
        ):
            raise OSError("control file changed before opening")
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > limit:
                raise OSError("control file exceeds size limit")
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_name = path.lstat()
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
        getattr(before, field) != getattr(after_fd, field)
        or getattr(after_fd, field) != getattr(after_name, field)
        for field in fields
    ):
        raise OSError("control file changed while being read")
    return json.loads(bytes(raw).decode("utf-8"))


def _control_files(runtime_dir: Path) -> tuple[Path, ...]:
    selected: set[Path] = set()
    for name in _CONTROL_NAMES:
        path = runtime_dir / name
        if path.exists() or path.is_symlink():
            selected.add(path)
    for pattern in ("activation-*.json", ".restart-required.json.candidate-*"):
        selected.update(runtime_dir.glob(pattern))
    return tuple(sorted(selected, key=lambda path: path.name))


def _referenced_generations(value: Any, releases: Path) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"generation_id", "runtime_generation_id"} and isinstance(
                item, str
            ):
                if _DIGEST_RE.fullmatch(item):
                    references.add(item)
            elif key == "path" and isinstance(item, str):
                candidate = Path(item)
                if not candidate.is_absolute():
                    candidate = releases.parent / candidate
                normalized = Path(os.path.abspath(os.path.normpath(candidate)))
                if normalized.parent == releases and _DIGEST_RE.fullmatch(
                    normalized.name
                ):
                    references.add(normalized.name)
            references.update(_referenced_generations(item, releases))
    elif isinstance(value, list):
        for item in value:
            references.update(_referenced_generations(item, releases))
    return references


def _protected_versions(value: Any) -> set[str]:
    versions: set[str] = set()
    if not isinstance(value, dict):
        return versions
    for group_name in ("quarantines", "rollback_holds"):
        group = value.get(group_name)
        if not isinstance(group, dict):
            continue
        for record in group.values():
            if isinstance(record, dict) and isinstance(record.get("version"), str):
                versions.add(record["version"])
    return versions


def _read_generation_json(path: Path, label: str) -> dict[str, Any]:
    raw = _read_control(path, limit=MAX_DESCRIPTOR_BYTES)
    if not isinstance(raw, dict):
        raise ValueError(f"{label} is not an object")
    return raw


def _verify_signed_generation(
    runtime_dir: Path,
    descriptor: dict[str, Any],
    public_keys: dict[str, str] | None,
) -> None:
    if not public_keys:
        raise ValueError("runtime signing keys are unavailable")
    from core.runtime_release_trust import verify_retained_runtime_catalog

    generation_id = descriptor.get("generation_id")
    if not isinstance(generation_id, str) or not _DIGEST_RE.fullmatch(generation_id):
        raise ValueError("runtime descriptor identity is invalid")
    verify_retained_runtime_catalog(runtime_dir, descriptor, public_keys)


def cleanup_runtime_generations(
    runtime_dir: Path, *, public_keys: dict[str, str] | None = None
) -> RuntimeRetentionResult:
    """Remove only verified, unreferenced, owned digest-named generations."""

    root = Path(runtime_dir)
    if not root.is_absolute():
        return RuntimeRetentionResult(
            (),
            (),
            ("Runtime retention directory path is not absolute; cleanup was skipped.",),
        )
    releases = root / "releases"
    if not releases.exists() and not releases.is_symlink():
        return RuntimeRetentionResult((), (), ())
    if not _trusted_directory(root) or not _trusted_directory(releases):
        return RuntimeRetentionResult(
            (),
            (),
            ("Runtime retention directories are not trusted; cleanup was skipped.",),
        )

    referenced: set[str] = set()
    protected_versions: set[str] = set()
    try:
        for control in _control_files(root):
            value = _read_control(control)
            referenced.update(_referenced_generations(value, releases))
            if control.name == "update-scheduler.json":
                protected_versions.update(_protected_versions(value))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return RuntimeRetentionResult(
            (),
            (),
            (
                "Runtime retention control state could not be verified; "
                f"cleanup was skipped: {exc}",
            ),
        )

    deleted: list[Path] = []
    retained: list[Path] = []
    warnings: list[str] = []
    try:
        entries = sorted(releases.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return RuntimeRetentionResult(
            (), (), (f"Runtime generations could not be listed: {exc}",)
        )
    for entry in entries:
        if re.fullmatch(r"\.prepare-[0-9a-f]{64}\.lock", entry.name):
            retained.append(entry)
            continue
        try:
            metadata = entry.lstat()
        except OSError as exc:
            warnings.append(
                f"Runtime generation {entry.name} could not be inspected: {exc}"
            )
            continue
        if (
            not _DIGEST_RE.fullmatch(entry.name)
            or not stat.S_ISDIR(metadata.st_mode)
            or not _entry_owned_by_current_user(entry)
            or entry.name in referenced
        ):
            retained.append(entry)
            if entry.name not in referenced:
                warnings.append(
                    f"Runtime entry {entry.name} is not an owned cleanup candidate."
                )
            continue
        descriptor: dict[str, Any]
        try:
            descriptor = _read_generation_json(
                entry / DESCRIPTOR_FILENAME, "Runtime descriptor"
            )
            _verify_signed_generation(root, descriptor, public_keys)
            receipt = _read_generation_json(
                entry / PREPARED_RECEIPT_FILENAME, "Prepared receipt"
            )
            dependency_digest = receipt.get("dependency_digest")
            if not isinstance(dependency_digest, str):
                raise ValueError("prepared receipt dependency digest is invalid")
            generation = RuntimeGeneration(
                generation_id=entry.name,
                app_dir=entry,
                python_executable=entry / "environment/bin/python",
                dependency_digest=dependency_digest,
            )
            verify_runtime_generation(generation)
            if descriptor.get("application_version") in protected_versions:
                retained.append(entry)
                continue
            after = entry.lstat()
            if (
                after.st_dev != metadata.st_dev
                or after.st_ino != metadata.st_ino
                or not stat.S_ISDIR(after.st_mode)
                or not _entry_owned_by_current_user(entry)
            ):
                raise OSError("generation changed during verification")
            if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
                raise OSError("safe recursive removal is unavailable")
            shutil.rmtree(entry)
        except Exception as exc:
            retained.append(entry)
            warnings.append(
                f"Runtime generation {entry.name} could not be removed safely: {exc}"
            )
            continue
        deleted.append(entry)
        try:
            _fsync_trusted_directory(releases)
        except Exception as exc:
            warnings.append(
                f"Runtime generation {entry.name} was removed, but its deletion "
                f"could not be synchronized; trust was retained: {exc}"
            )
            continue
        try:
            from core.runtime_release_trust import delete_retained_runtime_catalog

            if not public_keys:
                raise ValueError("runtime signing keys are unavailable")
            delete_retained_runtime_catalog(root, descriptor, public_keys)
        except Exception as exc:
            warnings.append(
                f"Runtime generation {entry.name} was removed, but its trust "
                f"record could not be removed safely: {exc}"
            )
    return RuntimeRetentionResult(tuple(deleted), tuple(retained), tuple(warnings))
