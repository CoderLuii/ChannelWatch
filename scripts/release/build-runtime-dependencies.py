#!/usr/bin/env python3
"""Build a verified, expanded Python dependency payload from an exact wheel lock."""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import json
import platform as host_platform
import re
import stat
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import NamedTuple, Sequence


PYPI_SIMPLE_URL = "https://pypi.org/simple"
MANAGEMENT_DEPENDENCIES = frozenset({"pip", "setuptools", "supervisor"})
LOCK_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)"
    r"(?P<hashes>(?:\s+--hash=\S+)+)$"
)
SHA256_PATTERN = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ENTRY_POINT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
LIBC_FAMILY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
LIBC_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+$")
TOOL_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:[a-z0-9.+-]*)?$")


class DependencyBuildError(ValueError):
    """The dependency payload does not satisfy the controlled-build contract."""


class LockedRequirement(NamedTuple):
    name: str
    version: str
    hashes: tuple[str, ...]


class TargetPlatform(NamedTuple):
    architecture: str
    python_implementation: str
    python_cache_tag: str
    python_version: str
    os_family: str
    libc_family: str
    minimum_libc_version: str
    wheel_platform: str
    compatible_wheel_tags: tuple[str, ...]


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _logical_lines(path: Path) -> list[str]:
    result: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        result.append((pending + line).strip())
        pending = ""
    if pending:
        raise DependencyBuildError(f"Unterminated line continuation in {path}")
    return result


def _parse_requested_requirements(path: Path) -> dict[str, frozenset[str]]:
    try:
        from pip._vendor.packaging.requirements import Requirement
    except ImportError as exc:
        raise DependencyBuildError(
            "pip packaging support is required to parse requested dependencies"
        ) from exc

    requested: dict[str, frozenset[str]] = {}
    for line in _logical_lines(path):
        if line.startswith("-"):
            raise DependencyBuildError(f"Unsupported requested requirement: {line}")
        try:
            requirement = Requirement(line)
        except Exception as exc:
            raise DependencyBuildError(
                f"Unsupported requested requirement: {line}"
            ) from exc
        if requirement.url is not None or requirement.marker is not None:
            raise DependencyBuildError(f"Unsupported requested requirement: {line}")
        name = _normalize_name(requirement.name)
        if name in MANAGEMENT_DEPENDENCIES:
            raise DependencyBuildError(
                f"Requested image-owned management dependency is forbidden: {name}"
            )
        if name in requested:
            raise DependencyBuildError(f"Duplicate requested dependency: {name}")
        requested[name] = frozenset(
            _normalize_name(extra) for extra in requirement.extras
        )
    if not requested:
        raise DependencyBuildError("Requested requirements are empty")
    return requested


def parse_requested_requirements(path: Path) -> tuple[str, ...]:
    return tuple(sorted(_parse_requested_requirements(path)))


def _requested_extras(path: Path) -> dict[str, frozenset[str]]:
    return _parse_requested_requirements(path)


def parse_locked_requirements(path: Path) -> tuple[LockedRequirement, ...]:
    locked: dict[str, LockedRequirement] = {}
    for line in _logical_lines(path):
        match = LOCK_PATTERN.fullmatch(line)
        if match is None:
            if "==" not in line and not line.startswith("-") and " @ " not in line:
                raise DependencyBuildError(f"Requirement must be exactly pinned: {line}")
            if re.match(r"^[A-Za-z0-9._-]+==[^\s]+$", line):
                raise DependencyBuildError(f"Requirement is missing a SHA-256 hash: {line}")
            if "--hash=" in line and "--hash=sha256:" not in line:
                raise DependencyBuildError(f"Only SHA-256 hashes are accepted: {line}")
            raise DependencyBuildError(f"Unsupported lock entry: {line}")
        name = _normalize_name(match.group("name"))
        if name in locked:
            raise DependencyBuildError(f"Duplicate locked dependency: {name}")
        if "+" in match.group("version"):
            raise DependencyBuildError(
                f"Custom or local-version dependency builds are forbidden: {line}"
            )
        hashes: list[str] = []
        for token in match.group("hashes").split():
            value = token.removeprefix("--hash=")
            hash_match = SHA256_PATTERN.fullmatch(value)
            if hash_match is None:
                raise DependencyBuildError(f"Only SHA-256 hashes are accepted: {line}")
            hashes.append(hash_match.group(1).lower())
        if not hashes:
            raise DependencyBuildError(f"Requirement is missing a SHA-256 hash: {line}")
        locked[name] = LockedRequirement(name, match.group("version"), tuple(sorted(set(hashes))))
    if not locked:
        raise DependencyBuildError("Locked requirements are empty")
    return tuple(locked[name] for name in sorted(locked))


def _wheel_identity(path: Path) -> tuple[str, str, str]:
    if not path.name.endswith(".whl"):
        raise DependencyBuildError(f"Dependency payload accepts binary wheels only: {path.name}")
    try:
        distribution_version, python_tag, abi_tag, platform_tag = path.name[:-4].rsplit("-", 3)
        distribution, version = distribution_version.rsplit("-", 1)
    except ValueError as exc:
        raise DependencyBuildError(f"Malformed wheel filename: {path.name}") from exc
    return _normalize_name(distribution), version, f"{python_tag}-{abi_tag}-{platform_tag}"


def _safe_wheel_path(filename: str) -> bool:
    if not filename or "\\" in filename or "\x00" in filename:
        return False
    path = PurePosixPath(filename)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _verify_record(archive: zipfile.ZipFile, wheel_name: str) -> None:
    record_names = [name for name in archive.namelist() if name.endswith(".dist-info/RECORD")]
    if len(record_names) != 1:
        raise DependencyBuildError(f"Wheel must contain exactly one RECORD: {wheel_name}")
    rows = list(csv.reader(io_text(archive.read(record_names[0]))))
    recorded: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or row[0] in recorded:
            raise DependencyBuildError(f"Wheel RECORD is malformed: {wheel_name}")
        recorded[row[0]] = (row[1], row[2])
    regular_members = {
        info.filename: info
        for info in archive.infolist()
        if not info.is_dir()
    }
    if set(recorded) != set(regular_members):
        raise DependencyBuildError(f"Wheel RECORD does not cover every file: {wheel_name}")
    for name, info in regular_members.items():
        if name == record_names[0]:
            continue
        digest_value, size_value = recorded[name]
        if not digest_value.startswith("sha256=") or not size_value.isdecimal():
            raise DependencyBuildError(
                f"Wheel RECORD entry is not SHA-256 locked: {wheel_name}:{name}"
            )
        data = archive.read(info)
        expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if digest_value.removeprefix("sha256=") != expected or int(size_value) != len(data):
            raise DependencyBuildError(f"Wheel RECORD hash mismatch: {wheel_name}:{name}")


def io_text(data: bytes):
    return data.decode("utf-8").splitlines()


def _validate_wheel_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            seen: set[str] = set()
            for info in archive.infolist():
                if not _safe_wheel_path(info.filename):
                    raise DependencyBuildError(
                        f"Wheel contains an unsafe path: {path.name}:{info.filename}"
                    )
                if info.filename in seen:
                    raise DependencyBuildError(
                        f"Wheel contains a duplicate member: {path.name}:{info.filename}"
                    )
                seen.add(info.filename)
                unix_mode = info.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise DependencyBuildError(
                        f"Wheel contains a non-regular member: {path.name}:{info.filename}"
                    )
            _verify_record(archive, path.name)
            wheel_metadata = [
                name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
            ]
            package_metadata = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(wheel_metadata) != 1 or len(package_metadata) != 1:
                raise DependencyBuildError(
                    f"Wheel metadata layout is incomplete: {path.name}"
                )
            filename_name, filename_version, filename_tag = _wheel_identity(path)
            metadata = BytesParser().parsebytes(archive.read(package_metadata[0]))
            if (
                _normalize_name(metadata.get("Name", "")) != filename_name
                or metadata.get("Version", "") != filename_version
            ):
                raise DependencyBuildError(
                    f"Wheel metadata identity does not match filename: {path.name}"
                )
            wheel_fields = BytesParser().parsebytes(archive.read(wheel_metadata[0]))
            python_tag, abi_tag, platform_tag = filename_tag.split("-", 2)
            filename_tags = {
                f"{python_value}-{abi_value}-{platform_value}"
                for python_value in python_tag.split(".")
                for abi_value in abi_tag.split(".")
                for platform_value in platform_tag.split(".")
            }
            if set(wheel_fields.get_all("Tag", [])) != filename_tags:
                raise DependencyBuildError(
                    f"Wheel metadata tag does not match filename: {path.name}"
                )
    except zipfile.BadZipFile as exc:
        raise DependencyBuildError(f"Invalid wheel archive: {path.name}") from exc


def _tag_is_compatible(tag: str, compatible: Sequence[str]) -> bool:
    python_tag, abi_tag, platform_tag = tag.split("-", 2)
    allowed = set(compatible)
    return any(
        f"{python_value}-{abi_value}-{platform_value}" in allowed
        for python_value in python_tag.split(".")
        for abi_value in abi_tag.split(".")
        for platform_value in platform_tag.split(".")
    )


def validate_wheelhouse(
    wheelhouse: Path,
    locked_requirements: Sequence[LockedRequirement],
    platform: TargetPlatform,
) -> tuple[dict, ...]:
    locked = {item.name: item for item in locked_requirements}
    artifacts: dict[str, dict] = {}
    if not wheelhouse.is_dir():
        raise DependencyBuildError(f"Wheelhouse does not exist: {wheelhouse}")
    entries = sorted(wheelhouse.iterdir(), key=lambda item: item.name)
    if not entries:
        raise DependencyBuildError("Wheelhouse is empty")
    for path in entries:
        if not path.is_file() or path.suffix != ".whl":
            raise DependencyBuildError(
                f"Dependency payload accepts binary wheels only: {path.name}"
            )
        name, version, tag = _wheel_identity(path)
        requirement = locked.get(name)
        if requirement is None:
            raise DependencyBuildError(f"Unknown wheelhouse artifact: {path.name}")
        if name in artifacts:
            raise DependencyBuildError(f"Multiple wheel artifacts for dependency: {name}")
        if version != requirement.version:
            raise DependencyBuildError(
                f"Wheel version does not match lock: {path.name} != {requirement.version}"
            )
        if not _tag_is_compatible(tag, platform.compatible_wheel_tags):
            raise DependencyBuildError(f"Wheel has an incompatible wheel tag: {path.name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in requirement.hashes:
            raise DependencyBuildError(f"Wheel hash does not match lock: {path.name}")
        _validate_wheel_archive(path)
        artifacts[name] = {
            "filename": path.name,
            "sha256": digest,
            "size": path.stat().st_size,
            "source": PYPI_SIMPLE_URL,
        }
    missing = sorted(set(locked) - set(artifacts))
    if missing:
        raise DependencyBuildError(f"Locked wheels are missing: {', '.join(missing)}")
    return tuple(artifacts[name] for name in sorted(artifacts))


def _run(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        summary = detail[-1] if detail else f"exit {result.returncode}"
        raise DependencyBuildError(f"Dependency build command failed: {summary}")
    return result


def acquire_wheels(*, python_executable: str, lock_path: Path, wheelhouse: Path) -> None:
    wheelhouse.mkdir(parents=True, exist_ok=True)
    if any(wheelhouse.iterdir()):
        raise DependencyBuildError("Acquisition wheelhouse must start empty")
    _run(
        [
            python_executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "download",
            "--require-hashes",
            "--only-binary=:all:",
            "--index-url",
            PYPI_SIMPLE_URL,
            "--dest",
            str(wheelhouse),
            "--requirement",
            str(lock_path),
        ]
    )


def install_wheels_offline(
    *,
    python_executable: str,
    lock_path: Path,
    wheelhouse: Path,
    target: Path,
) -> None:
    target.mkdir(parents=True, exist_ok=False)
    _run(
        [
            python_executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--require-hashes",
            "--only-binary=:all:",
            "--no-compile",
            "--target",
            str(target),
            "--requirement",
            str(lock_path),
        ]
    )


def _declared_console_scripts(wheelhouse: Path) -> dict[str, str]:
    scripts: dict[str, str] = {}
    for wheel in sorted(wheelhouse.glob("*.whl"), key=lambda path: path.name):
        distribution, _version, _tag = _wheel_identity(wheel)
        with zipfile.ZipFile(wheel) as archive:
            entry_points = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/entry_points.txt")
            ]
            if len(entry_points) > 1:
                raise DependencyBuildError(
                    f"Wheel has multiple entry point metadata files: {wheel.name}"
                )
            if not entry_points:
                continue
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.optionxform = str
            try:
                parser.read_string(archive.read(entry_points[0]).decode("utf-8"))
            except (UnicodeError, configparser.Error) as exc:
                raise DependencyBuildError(
                    f"Wheel entry point metadata is invalid: {wheel.name}"
                ) from exc
            for section in ("console_scripts", "gui_scripts"):
                if not parser.has_section(section):
                    continue
                for name, target in parser.items(section):
                    if (
                        not ENTRY_POINT_NAME_PATTERN.fullmatch(name)
                        or not target.strip()
                        or name in scripts
                    ):
                        raise DependencyBuildError(
                            f"Wheel console script ownership is ambiguous: {wheel.name}:{name}"
                        )
                    scripts[name] = distribution
    return scripts


def _remove_generated_console_scripts(
    site_packages: Path, wheelhouse: Path, python_executable: str
) -> None:
    expected = _declared_console_scripts(wheelhouse)
    script_dir = site_packages / "bin"
    if not script_dir.exists():
        if expected:
            raise DependencyBuildError("Declared console scripts were not installed")
        return
    if script_dir.is_symlink() or not script_dir.is_dir():
        raise DependencyBuildError("Installed console script directory is unsafe")
    actual = {path.name: path for path in script_dir.iterdir()}
    if set(actual) != set(expected):
        raise DependencyBuildError(
            "Installed console scripts do not match validated wheel entry points"
        )
    executable = python_executable.encode("utf-8")
    for name, path in sorted(actual.items()):
        metadata = path.lstat()
        payload = path.read_bytes()
        first_line = payload.splitlines()[:1]
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not first_line
            or not first_line[0].startswith(b"#!")
            or executable not in first_line[0]
        ):
            raise DependencyBuildError(
                f"Installed console script is not a generated launcher: {name}"
            )
        path.unlink()
    script_dir.rmdir()
    recorded_owners: dict[str, tuple[str, Path, list[list[str]]]] = {}
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        metadata_path = dist_info / "METADATA"
        record_path = dist_info / "RECORD"
        if not metadata_path.is_file() or not record_path.is_file():
            raise DependencyBuildError(
                f"Installed distribution metadata is incomplete: {dist_info.name}"
            )
        owner = _normalize_name(
            BytesParser().parsebytes(metadata_path.read_bytes()).get("Name", "")
        )
        rows = list(csv.reader(record_path.read_text(encoding="utf-8").splitlines()))
        for row in rows:
            if len(row) != 3:
                raise DependencyBuildError(
                    f"Installed RECORD is malformed: {dist_info.name}"
                )
            for name in expected:
                if row[0] == f"../../bin/{name}":
                    if name in recorded_owners:
                        raise DependencyBuildError(
                            f"Installed console script RECORD ownership is ambiguous: {name}"
                        )
                    recorded_owners[name] = (owner, record_path, rows)
    if set(recorded_owners) != set(expected):
        raise DependencyBuildError(
            "Installed console scripts are not owned by an installed RECORD"
        )
    rewritten: set[Path] = set()
    for name, declared_owner in expected.items():
        owner, record_path, rows = recorded_owners[name]
        if owner != declared_owner:
            raise DependencyBuildError(
                f"Installed console script RECORD owner does not match wheel: {name}"
            )
        if record_path in rewritten:
            continue
        removed_names = {
            script_name
            for script_name, (_owner, path, _rows) in recorded_owners.items()
            if path == record_path
        }
        retained = [
            row for row in rows if row[0] not in {f"../../bin/{item}" for item in removed_names}
        ]
        with record_path.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle, lineterminator="\n").writerows(retained)
        rewritten.add(record_path)


def _validate_native_host(target: TargetPlatform) -> None:
    machine = host_platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        actual = "amd64"
    elif machine in {"arm64", "aarch64"}:
        actual = "arm64"
    else:
        actual = machine
    libc_family, libc_version = host_platform.libc_ver()
    libc_baseline = ".".join(libc_version.split(".")[:2])
    if (
        target.os_family != "linux"
        or sys.platform != "linux"
        or actual != target.architecture
    ):
        raise DependencyBuildError(
            "Acquisition must run on the target native platform: "
            f"expected linux/{target.architecture}, got {sys.platform}/{actual}"
        )
    if (
        not LIBC_FAMILY_PATTERN.fullmatch(target.libc_family)
        or not LIBC_VERSION_PATTERN.fullmatch(target.minimum_libc_version)
        or libc_family != target.libc_family
        or libc_baseline != target.minimum_libc_version
    ):
        raise DependencyBuildError(
            "Acquisition must run on the exact target libc baseline: "
            f"expected {target.libc_family}/{target.minimum_libc_version}, "
            f"got {libc_family or 'unknown'}/{libc_baseline or 'unknown'}"
        )


def _validate_python_identity(
    python_executable: str, target: TargetPlatform
) -> tuple[str, str]:
    code = (
        "import json,pip,sys;"
        "print(json.dumps([sys.implementation.name,sys.implementation.cache_tag,"
        "'.'.join(map(str,sys.version_info[:3])),pip.__version__]))"
    )
    result = _run([python_executable, "-c", code])
    try:
        implementation, cache_tag, version, pip_version = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise DependencyBuildError("Could not read target Python identity") from exc
    actual = (implementation, cache_tag, version)
    expected = (
        target.python_implementation,
        target.python_cache_tag,
        target.python_version,
    )
    if actual != expected:
        raise DependencyBuildError(
            f"Python identity does not match target platform: expected {expected}, got {actual}"
        )
    if not isinstance(pip_version, str) or not TOOL_VERSION_PATTERN.fullmatch(
        pip_version
    ):
        raise DependencyBuildError("Could not read target pip version")
    return version, pip_version


def _normalize_and_validate_tree(site_packages: Path) -> None:
    root = site_packages.resolve()
    for path in sorted(site_packages.rglob("*")):
        info = path.lstat()
        if path.is_symlink() or not (
            stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
        ):
            raise DependencyBuildError(
                f"Installed dependency tree contains a non-regular entry: {path}"
            )
        if root not in path.resolve().parents and path.resolve() != root:
            raise DependencyBuildError(f"Installed dependency path escapes the payload: {path}")
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o755 if info.st_mode & 0o111 else 0o644)


def _verify_native_libraries(site_packages: Path) -> None:
    for path in sorted(site_packages.rglob("*")):
        if path.is_file() and (path.name.endswith(".so") or ".so." in path.name):
            result = subprocess.run(
                ["ldd", str(path)], capture_output=True, text=True, check=False
            )
            output = f"{result.stdout}\n{result.stderr}"
            if result.returncode or "not found" in output:
                if "not found" in output:
                    raise DependencyBuildError(
                        f"Native dependency has an unresolved native library: {path.name}"
                    )
                raise DependencyBuildError(
                    f"Native dependency could not be inspected with ldd: {path.name}"
                )


def _installed_dependencies(
    site_packages: Path,
) -> tuple[tuple[dict, ...], dict[str, tuple[str, ...]]]:
    dependencies: list[dict] = []
    requirements_by_name: dict[str, tuple[str, ...]] = {}
    for dist_info in sorted(site_packages.glob("*.dist-info"), key=lambda path: path.name.lower()):
        metadata_path = dist_info / "METADATA"
        if not metadata_path.is_file():
            raise DependencyBuildError(f"Installed distribution has no METADATA: {dist_info.name}")
        metadata = BytesParser().parsebytes(metadata_path.read_bytes())
        name = _normalize_name(metadata.get("Name", ""))
        version = metadata.get("Version", "")
        if not name or not version:
            raise DependencyBuildError(
                f"Installed distribution identity is incomplete: {dist_info.name}"
            )
        license_files = [
            path.relative_to(site_packages.parent.parent).as_posix()
            for path in sorted(dist_info.rglob("*"))
            if path.is_file()
            and (
                "licenses" in {part.lower() for part in path.relative_to(dist_info).parts}
                or path.name.lower().startswith(("license", "copying", "notice"))
            )
        ]
        dependencies.append(
            {
                "name": name,
                "version": version,
                "license_expression": metadata.get("License-Expression", "")
                or metadata.get("License", ""),
                "license_files": license_files,
            }
        )
        requirements_by_name[name] = tuple(metadata.get_all("Requires-Dist", []))
    names = [item["name"] for item in dependencies]
    if len(names) != len(set(names)):
        raise DependencyBuildError("Installed dependency identities are duplicated")
    return (
        tuple(sorted(dependencies, key=lambda item: item["name"])),
        requirements_by_name,
    )


def _dependency_closure(
    requested_extras: dict[str, frozenset[str]],
    requirements_by_name: dict[str, tuple[str, ...]],
    target: TargetPlatform,
) -> frozenset[str]:
    try:
        from pip._vendor.packaging.markers import default_environment
        from pip._vendor.packaging.requirements import Requirement
    except ImportError as exc:
        raise DependencyBuildError(
            "pip packaging support is required to validate dependency closure"
        ) from exc

    environment = default_environment()
    architecture = "x86_64" if target.architecture == "amd64" else "aarch64"
    environment.update(
        {
            "implementation_name": target.python_implementation,
            "platform_machine": architecture,
            "platform_system": "Linux",
            "python_full_version": target.python_version,
            "python_version": ".".join(target.python_version.split(".")[:2]),
            "sys_platform": "linux",
        }
    )
    queue = [
        (name, extra)
        for name, extras in sorted(requested_extras.items())
        for extra in ("", *sorted(extras))
    ]
    seen_states: set[tuple[str, str]] = set()
    closure: set[str] = set()
    while queue:
        name, active_extra = queue.pop(0)
        state = (name, active_extra)
        if state in seen_states:
            continue
        seen_states.add(state)
        closure.add(name)
        for raw_requirement in requirements_by_name.get(name, ()):
            try:
                requirement = Requirement(raw_requirement)
            except Exception as exc:
                raise DependencyBuildError(
                    "Installed dependency metadata has an invalid requirement: "
                    f"{name}: {raw_requirement}"
                ) from exc
            marker_environment = dict(environment)
            marker_environment["extra"] = active_extra
            if requirement.marker is not None and not requirement.marker.evaluate(
                marker_environment
            ):
                continue
            dependency_name = _normalize_name(requirement.name)
            queue.append((dependency_name, ""))
            queue.extend(
                (dependency_name, _normalize_name(extra))
                for extra in sorted(requirement.extras)
            )
    return frozenset(closure)


def _file_inventory(site_packages: Path) -> tuple[dict, ...]:
    generation_root = site_packages.parent.parent
    files = []
    for path in sorted(site_packages.rglob("*")):
        if not path.is_file():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode not in {0o644, 0o755}:
            raise DependencyBuildError(f"Installed dependency mode is not normalized: {path}")
        data = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(generation_root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "mode": f"{mode:04o}",
            }
        )
    return tuple(sorted(files, key=lambda item: item["path"]))


def build_runtime_dependencies(
    *,
    requirements_path: Path,
    lock_path: Path,
    output_dir: Path,
    wheelhouse: Path,
    platform: TargetPlatform,
    python_executable: str,
    builder_image_digest: str,
    acquire: bool = True,
) -> Path:
    if IMAGE_DIGEST_PATTERN.fullmatch(builder_image_digest) is None:
        raise DependencyBuildError(
            "Builder image identity must be an immutable sha256 digest"
        )
    requested = parse_requested_requirements(requirements_path)
    requested_extras = _requested_extras(requirements_path)
    locked = parse_locked_requirements(lock_path)
    locked_by_name = {item.name: item for item in locked}
    missing_direct = sorted(set(requested) - set(locked_by_name))
    if missing_direct:
        raise DependencyBuildError(
            f"Requested dependencies are absent from the complete lock: {', '.join(missing_direct)}"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise DependencyBuildError(f"Output directory must start empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    _validate_native_host(platform)
    python_version, pip_version = _validate_python_identity(
        python_executable, platform
    )
    if acquire:
        acquire_wheels(
            python_executable=python_executable,
            lock_path=lock_path,
            wheelhouse=wheelhouse,
        )
    artifacts = validate_wheelhouse(wheelhouse, locked, platform)
    site_packages = output_dir / "dependencies/site-packages"
    install_wheels_offline(
        python_executable=python_executable,
        lock_path=lock_path,
        wheelhouse=wheelhouse,
        target=site_packages,
    )
    _remove_generated_console_scripts(site_packages, wheelhouse, python_executable)
    _normalize_and_validate_tree(site_packages)
    _verify_native_libraries(site_packages)
    dependencies, requirements_by_name = _installed_dependencies(site_packages)
    installed = {item["name"]: item["version"] for item in dependencies}
    expected = {item.name: item.version for item in locked}
    if installed != expected:
        missing = sorted(set(expected) - set(installed))
        unknown = sorted(set(installed) - set(expected))
        mismatched = sorted(
            name for name in set(installed) & set(expected) if installed[name] != expected[name]
        )
        raise DependencyBuildError(
            "Installed dependency closure does not exactly match the lock "
            f"(missing={missing}, unknown={unknown}, mismatched={mismatched})"
        )
    closure = _dependency_closure(requested_extras, requirements_by_name, platform)
    outside_closure = sorted(set(expected) - set(closure))
    if outside_closure:
        raise DependencyBuildError(
            "Locked packages are outside the requested dependency closure: "
            + ", ".join(outside_closure)
        )
    files = _file_inventory(site_packages)
    inventory = {
        "schema": 1,
        "build": {
            "builder_image_digest": builder_image_digest,
            "python_version": python_version,
            "pip_version": pip_version,
        },
        "platform": {
            "architecture": platform.architecture,
            "python_implementation": platform.python_implementation,
            "python_cache_tag": platform.python_cache_tag,
            "python_version": platform.python_version,
            "os_family": platform.os_family,
            "libc_family": platform.libc_family,
            "minimum_libc_version": platform.minimum_libc_version,
            "wheel_platform": platform.wheel_platform,
        },
        "requirements": {
            "direct": list(requested),
            "locked": [
                {"name": item.name, "version": item.version, "hashes": list(item.hashes)}
                for item in locked
            ],
        },
        "artifacts": list(artifacts),
        "dependencies": list(dependencies),
        "files": list(files),
        "counts": {
            "files": len(files),
            "bytes": sum(item["size"] for item in files),
        },
    }
    inventory_path = output_dir / "dependencies/inventory.json"
    inventory_path.write_text(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return inventory_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--python-implementation", default="cpython")
    parser.add_argument("--python-cache-tag", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--os-family", default="linux")
    parser.add_argument("--libc-family", required=True)
    parser.add_argument("--minimum-libc-version", required=True)
    parser.add_argument("--wheel-platform", required=True)
    parser.add_argument("--compatible-wheel-tag", action="append", required=True)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--builder-image-digest", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    target = TargetPlatform(
        architecture=args.architecture,
        python_implementation=args.python_implementation,
        python_cache_tag=args.python_cache_tag,
        python_version=args.python_version,
        os_family=args.os_family,
        libc_family=args.libc_family,
        minimum_libc_version=args.minimum_libc_version,
        wheel_platform=args.wheel_platform,
        compatible_wheel_tags=tuple(sorted(set(args.compatible_wheel_tag))),
    )
    try:
        inventory = build_runtime_dependencies(
            requirements_path=args.requirements,
            lock_path=args.lock,
            output_dir=args.output,
            wheelhouse=args.wheelhouse,
            platform=target,
            python_executable=args.python_executable,
            builder_image_digest=args.builder_image_digest,
        )
    except DependencyBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
