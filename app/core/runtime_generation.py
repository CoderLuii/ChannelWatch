"""Strict preparation and verification for persistent runtime generations."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - persistent generations require Linux
    fcntl = None

RUNTIME_DESCRIPTOR_SCHEMA = 1
RUNTIME_BUNDLE_FORMAT = "channelwatch-runtime-generation-v1"
RUNTIME_LAUNCHER_CAPABILITY = "isolated_dependencies_v1"
DEPENDENCY_INVENTORY_SCHEMA = 1
PREPARED_RECEIPT_SCHEMA = 1

DESCRIPTOR_FILENAME = "channelwatch-bundle.json"
PREPARED_RECEIPT_FILENAME = "prepared.json"
DEPENDENCY_INVENTORY_PATH = "dependencies/inventory.json"

MAX_DESCRIPTOR_BYTES = 4 * 1024 * 1024
MAX_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_GENERATION_FILES = 25_000
MAX_GENERATION_FILE_BYTES = 256 * 1024 * 1024
MAX_GENERATION_EXPANDED_BYTES = 1024 * 1024 * 1024

_HEX_40_RE = re.compile(r"[0-9a-f]{40}")
_HEX_64_RE = re.compile(r"[0-9a-f]{64}")
_SEMVER_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_PACKAGE_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?")
_MODE_RE = re.compile(r"0[0-7]{3}")
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_TOOL_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[a-z0-9.+-]*)?")
_EXECUTION_PROBE_RE = re.compile(r"\.channelwatch-exec-probe-[0-9a-f]{32}")
_RECEIPT_TEMP_RE = re.compile(r"\.prepared\.json\.[0-9a-f]{32}\.tmp")
_RECOVERY_REFERENCE_NAMES = frozenset(
    {
        "active.json",
        "rollback.json",
        "activation-pending.json",
        "activation-core-ready.json",
        "activation-ui-ready.json",
        "restart-required.json",
        "restart-services-accepted.json",
    }
)

_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "generation_id",
        "application_version",
        "source_commit",
        "dependency_inventory_sha256",
        "bundle_format",
        "required_launcher_capability",
        "settings_schema_version",
        "architecture",
        "python_implementation",
        "python_cache_tag",
        "python_version",
        "os_family",
        "libc_family",
        "minimum_libc_version",
        "dependency_inventory_path",
        "files",
        "expanded_size",
    }
)
_FILE_FIELDS = frozenset({"path", "sha256", "size", "mode"})
_INVENTORY_FIELDS = frozenset(
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
_BUILD_FIELDS = frozenset({"builder_image_digest", "python_version", "pip_version"})
_INVENTORY_PLATFORM_FIELDS = frozenset(
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
_REQUIREMENTS_FIELDS = frozenset({"direct", "locked"})
_LOCKED_REQUIREMENT_FIELDS = frozenset({"name", "version", "hashes"})
_ARTIFACT_FIELDS = frozenset({"filename", "sha256", "size", "source"})
_DEPENDENCY_FIELDS = frozenset(
    {"name", "version", "license_expression", "license_files"}
)
_COUNT_FIELDS = frozenset({"files", "bytes"})
_ENVIRONMENT_FILE_FIELDS = frozenset({"path", "kind", "sha256", "size", "mode"})
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "generation_id",
        "descriptor_sha256",
        "dependency_digest",
        "application_version",
        "architecture",
        "python_cache_tag",
        "libc_family",
        "libc_version",
        "python_executable",
        "environment_files",
    }
)
_RESERVED_PATHS = frozenset(
    {DESCRIPTOR_FILENAME, DEPENDENCY_INVENTORY_PATH, PREPARED_RECEIPT_FILENAME}
)
_ENVIRONMENT_REGULAR_PATHS = frozenset(
    {
        "environment/.gitignore",
        "environment/pyvenv.cfg",
        "environment/bin/Activate.ps1",
        "environment/bin/activate",
        "environment/bin/activate.csh",
        "environment/bin/activate.fish",
    }
)
_FORBIDDEN_ENVIRONMENT_NAMES = frozenset({"sitecustomize.py", "usercustomize.py"})
_FORBIDDEN_ENVIRONMENT_SUFFIXES = (".pth", ".so", ".pyd", ".dll", ".dylib")


@dataclass(frozen=True)
class RuntimePlatform:
    architecture: str
    python_cache_tag: str
    libc_family: str
    libc_version: tuple[int, int]
    launcher_capabilities: frozenset[str]


@dataclass(frozen=True)
class RuntimeGeneration:
    generation_id: str
    app_dir: Path
    python_executable: Path
    dependency_digest: str


class RuntimeGenerationError(ValueError):
    """A runtime descriptor, generation, or local environment is invalid."""


def _error(message: str) -> RuntimeGenerationError:
    return RuntimeGenerationError(message)


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
        raise _error("Runtime metadata is not canonical JSON data.") from exc


def descriptor_generation_id(descriptor: dict[str, Any]) -> str:
    """Return the descriptor identity without its self-referential ID field."""

    if not isinstance(descriptor, dict):
        raise _error("Runtime descriptor must be an object.")
    identity = {key: value for key, value in descriptor.items() if key != "generation_id"}
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _architecture(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise _error("This processor architecture is not supported.") from exc


def _version_pair(value: str) -> tuple[int, int]:
    parts = value.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        raise _error("The runtime C library version is invalid.")
    return int(parts[0]), int(parts[1])


def detect_runtime_platform(*, launcher_protocol: int | None = None) -> RuntimePlatform:
    """Detect the fixed platform facts used to select and verify a generation."""

    if platform.system().lower() != "linux":
        raise _error("Persistent runtime generations require Linux.")
    libc_family, libc_version = platform.libc_ver()
    libc_family = libc_family.strip().lower()
    if libc_family != "glibc":
        raise _error("Persistent runtime generations require glibc.")
    cache_tag = sys.implementation.cache_tag
    if sys.implementation.name != "cpython" or not cache_tag:
        raise _error("Persistent runtime generations require CPython.")
    if launcher_protocol is None:
        try:
            launcher_protocol = int(
                os.environ.get("CHANNELWATCH_LAUNCHER_PROTOCOL", "0")
            )
        except ValueError:
            launcher_protocol = 0
    capabilities = (
        frozenset({RUNTIME_LAUNCHER_CAPABILITY})
        if launcher_protocol >= 4
        else frozenset()
    )
    return RuntimePlatform(
        architecture=_architecture(platform.machine()),
        python_cache_tag=cache_tag,
        libc_family=libc_family,
        libc_version=_version_pair(libc_version),
        launcher_capabilities=capabilities,
    )


def _require_exact_fields(value: Any, expected: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise _error(f"{label} fields are incomplete or unsupported.")
    return value


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _error(f"{label} must be an integer of at least {minimum}.")
    return value


def _version_triplet(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _SEMVER_RE.fullmatch(value):
        raise _error(f"{label} must use a three-part numeric version.")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise _error(f"{label} is not a safe relative path.")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise _error(f"{label} is not a safe relative path.")
    return value


def _file_record(value: Any, label: str) -> dict[str, Any]:
    record = _require_exact_fields(value, _FILE_FIELDS, label)
    relative = _relative_path(record["path"], f"{label} path")
    if relative in _RESERVED_PATHS or relative == "environment" or relative.startswith(
        "environment/"
    ):
        raise _error(f"{label} uses a reserved runtime path.")
    if PurePosixPath(relative).parts[0] not in {"core", "ui", "dependencies"}:
        raise _error(f"{label} is outside the runtime generation roots.")
    digest = record["sha256"]
    if not isinstance(digest, str) or not _HEX_64_RE.fullmatch(digest):
        raise _error(f"{label} SHA-256 is invalid.")
    size = _positive_int(record["size"], f"{label} size", allow_zero=True)
    if size > MAX_GENERATION_FILE_BYTES:
        raise _error(f"{label} exceeds the per-file size limit.")
    mode = record["mode"]
    if not isinstance(mode, str) or not _MODE_RE.fullmatch(mode):
        raise _error(f"{label} mode is invalid.")
    parsed_mode = int(mode, 8)
    if parsed_mode not in {0o644, 0o755} or parsed_mode & (stat.S_ISUID | stat.S_ISGID):
        raise _error(f"{label} mode is not allowed.")
    return record


def validate_runtime_descriptor_shape(descriptor: dict[str, Any]) -> None:
    """Validate signed descriptor structure without applying host compatibility."""

    value = _require_exact_fields(descriptor, _DESCRIPTOR_FIELDS, "Runtime descriptor")
    if value["schema"] != RUNTIME_DESCRIPTOR_SCHEMA:
        raise _error("Runtime descriptor schema is unsupported.")
    generation_id = value["generation_id"]
    if (
        not isinstance(generation_id, str)
        or not _HEX_64_RE.fullmatch(generation_id)
        or generation_id != descriptor_generation_id(value)
    ):
        raise _error("Runtime descriptor generation identity is invalid.")
    if (
        not isinstance(value["application_version"], str)
        or not _SEMVER_RE.fullmatch(value["application_version"])
    ):
        raise _error("Runtime application version is invalid.")
    if not isinstance(value["source_commit"], str) or not _HEX_40_RE.fullmatch(
        value["source_commit"]
    ):
        raise _error("Runtime source commit is invalid.")
    dependency_digest = value["dependency_inventory_sha256"]
    if not isinstance(dependency_digest, str) or not _HEX_64_RE.fullmatch(
        dependency_digest
    ):
        raise _error("Runtime dependency inventory digest is invalid.")
    if value["bundle_format"] != RUNTIME_BUNDLE_FORMAT:
        raise _error("Runtime bundle format is unsupported.")
    if value["required_launcher_capability"] != RUNTIME_LAUNCHER_CAPABILITY:
        raise _error("Runtime launcher capability is unsupported.")
    _positive_int(value["settings_schema_version"], "Settings schema version")
    if value["architecture"] not in {"amd64", "arm64"}:
        raise _error("Runtime descriptor architecture is unsupported.")
    if value["python_implementation"] != "cpython":
        raise _error("Runtime descriptor interpreter is unsupported.")
    if not isinstance(value["python_cache_tag"], str) or not re.fullmatch(
        r"cpython-[0-9]{3}", value["python_cache_tag"]
    ):
        raise _error("Runtime descriptor CPython cache tag is invalid.")
    _version_triplet(value["python_version"], "Runtime Python version")
    if value["os_family"] != "linux":
        raise _error("Runtime descriptor operating system is unsupported.")
    if value["libc_family"] != "glibc":
        raise _error("Runtime descriptor C library is unsupported.")
    libc_floor = value["minimum_libc_version"]
    if (
        not isinstance(libc_floor, list)
        or len(libc_floor) != 2
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in libc_floor
        )
    ):
        raise _error("Runtime descriptor C library floor is invalid.")
    if value["dependency_inventory_path"] != DEPENDENCY_INVENTORY_PATH:
        raise _error("Runtime dependency inventory path is unsupported.")
    files = value["files"]
    if not isinstance(files, list) or not files or len(files) > MAX_GENERATION_FILES:
        raise _error("Runtime descriptor file inventory is empty or too large.")
    records = [
        _file_record(item, f"Runtime file {index}")
        for index, item in enumerate(files)
    ]
    paths = [record["path"] for record in records]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _error("Runtime descriptor file paths must be unique and sorted.")
    required_roots = ("core/", "ui/backend/", "dependencies/site-packages/")
    if any(not any(path.startswith(root) for path in paths) for root in required_roots):
        raise _error("Runtime descriptor is missing a required application root.")
    expanded_size = _positive_int(
        value["expanded_size"], "Runtime expanded size", allow_zero=True
    )
    if expanded_size > MAX_GENERATION_EXPANDED_BYTES:
        raise _error("Runtime generation exceeds the expanded size limit.")


def validate_runtime_descriptor(
    descriptor: dict[str, Any], platform_value: RuntimePlatform
) -> None:
    """Reject any descriptor that cannot safely run on the current image."""

    validate_runtime_descriptor_shape(descriptor)
    if descriptor["architecture"] != platform_value.architecture:
        raise _error("Runtime descriptor architecture does not match this image.")
    capability = descriptor["required_launcher_capability"]
    if capability not in platform_value.launcher_capabilities:
        raise _error("The image launcher cannot start this runtime generation.")
    if descriptor["python_cache_tag"] != platform_value.python_cache_tag:
        raise _error("Runtime descriptor CPython cache tag does not match this image.")
    python_floor = _version_triplet(
        descriptor["python_version"], "Runtime Python version"
    )
    current_python = sys.version_info[:3]
    if python_floor[:2] != current_python[:2] or python_floor > current_python:
        raise _error("Runtime descriptor requires a different CPython version.")
    if descriptor["libc_family"] != platform_value.libc_family:
        raise _error("Runtime descriptor C library does not match this image.")
    if tuple(descriptor["minimum_libc_version"]) > platform_value.libc_version:
        raise _error("Runtime descriptor requires a newer C library.")


def _read_regular_file(path: Path, *, limit: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _error(f"{label} cannot be inspected.") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
        or metadata.st_size < 0
        or metadata.st_size > limit
    ):
        raise _error(f"{label} is not a trusted bounded regular file.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _error(f"{label} cannot be opened safely.") from exc
    try:
        opened = os.fstat(descriptor)
        identity_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_nlink",
            "st_uid",
        )
        if any(
            getattr(opened, field) != getattr(metadata, field)
            for field in identity_fields
        ):
            raise _error(f"{label} changed before it was read.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise _error(f"{label} exceeds the safe size limit.")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_name = path.lstat()
    except OSError as exc:
        raise _error(f"{label} changed while it was read.") from exc
    if any(
        getattr(after, field) != getattr(opened, field)
        or getattr(after_name, field) != getattr(after, field)
        for field in identity_fields
    ):
        raise _error(f"{label} changed while it was read.")
    return b"".join(chunks)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short filesystem write")
        remaining = remaining[written:]


def _parse_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _error(f"{label} is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise _error(f"{label} must be an object.")
    return value


def _validate_string_list(value: Any, label: str, *, path_values: bool = False) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise _error(f"{label} must be a string list.")
    if value != sorted(value) or len(value) != len(set(value)):
        raise _error(f"{label} must be unique and sorted.")
    if path_values:
        for item in value:
            _relative_path(item, label)
    return value


def _validate_inventory(
    inventory: dict[str, Any], descriptor: dict[str, Any], platform_value: RuntimePlatform
) -> list[dict[str, Any]]:
    value = _require_exact_fields(inventory, _INVENTORY_FIELDS, "Dependency inventory")
    if value["schema"] != DEPENDENCY_INVENTORY_SCHEMA:
        raise _error("Dependency inventory schema is unsupported.")
    build = _require_exact_fields(value["build"], _BUILD_FIELDS, "Dependency build")
    builder_image_digest = build["builder_image_digest"]
    if not isinstance(builder_image_digest, str) or not _IMAGE_DIGEST_RE.fullmatch(
        builder_image_digest
    ):
        raise _error("Dependency builder image digest is invalid.")
    pip_version = build["pip_version"]
    if not isinstance(pip_version, str) or not _TOOL_VERSION_RE.fullmatch(pip_version):
        raise _error("Dependency build pip version is invalid.")
    inventory_platform = _require_exact_fields(
        value["platform"], _INVENTORY_PLATFORM_FIELDS, "Dependency platform"
    )
    if build["python_version"] != inventory_platform["python_version"]:
        raise _error("Dependency build Python version does not match its platform.")
    for field in (
        "architecture",
        "python_implementation",
        "python_cache_tag",
        "python_version",
        "os_family",
        "libc_family",
    ):
        if inventory_platform[field] != descriptor[field]:
            raise _error("Dependency inventory platform does not match the descriptor.")
    minimum_libc_version = inventory_platform["minimum_libc_version"]
    expected_libc_version = ".".join(
        str(part) for part in descriptor["minimum_libc_version"]
    )
    if minimum_libc_version != expected_libc_version:
        raise _error("Dependency inventory C library baseline does not match the descriptor.")
    wheel_platform = inventory_platform["wheel_platform"]
    if not isinstance(wheel_platform, str) or not wheel_platform:
        raise _error("Dependency wheel platform is invalid.")
    requirements = _require_exact_fields(
        value["requirements"], _REQUIREMENTS_FIELDS, "Dependency requirements"
    )
    direct = _validate_string_list(requirements["direct"], "Direct requirements")
    if not direct:
        raise _error("Direct requirements must be a non-empty list.")
    locked = requirements["locked"]
    if not isinstance(locked, list) or not locked:
        raise _error("Locked requirements must be a non-empty list.")
    locked_names: list[str] = []
    for index, item in enumerate(locked):
        record = _require_exact_fields(
            item, _LOCKED_REQUIREMENT_FIELDS, f"Locked requirement {index}"
        )
        name = record["name"]
        if not isinstance(name, str) or not _PACKAGE_NAME_RE.fullmatch(name):
            raise _error("Locked requirement name is invalid.")
        if not isinstance(record["version"], str) or not record["version"]:
            raise _error("Locked requirement version is invalid.")
        hashes = _validate_string_list(record["hashes"], "Locked requirement hashes")
        if not hashes or any(not _HEX_64_RE.fullmatch(item_hash) for item_hash in hashes):
            raise _error("Locked requirement hashes are invalid.")
        locked_names.append(name)
    if locked_names != sorted(locked_names) or len(locked_names) != len(set(locked_names)):
        raise _error("Locked requirements must be unique and sorted.")
    if any(name not in locked_names for name in direct):
        raise _error("Every direct requirement must be locked.")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise _error("Dependency artifacts must be a non-empty list.")
    artifact_names: list[str] = []
    for index, item in enumerate(artifacts):
        record = _require_exact_fields(item, _ARTIFACT_FIELDS, f"Artifact {index}")
        filename = record["filename"]
        if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename:
            raise _error("Dependency artifact filename is invalid.")
        if not isinstance(record["sha256"], str) or not _HEX_64_RE.fullmatch(
            record["sha256"]
        ):
            raise _error("Dependency artifact hash is invalid.")
        _positive_int(record["size"], "Dependency artifact size")
        if not isinstance(record["source"], str) or not record["source"].startswith(
            "https://"
        ):
            raise _error("Dependency artifact source is invalid.")
        artifact_names.append(filename)
    if artifact_names != sorted(artifact_names) or len(artifact_names) != len(
        set(artifact_names)
    ):
        raise _error("Dependency artifacts must be unique and sorted.")
    dependencies = value["dependencies"]
    if not isinstance(dependencies, list) or not dependencies:
        raise _error("Dependency metadata must be a non-empty list.")
    dependency_names: list[str] = []
    descriptor_paths = {item["path"] for item in descriptor["files"]}
    for index, item in enumerate(dependencies):
        record = _require_exact_fields(
            item, _DEPENDENCY_FIELDS, f"Dependency metadata {index}"
        )
        name = record["name"]
        if not isinstance(name, str) or not _PACKAGE_NAME_RE.fullmatch(name):
            raise _error("Dependency metadata name is invalid.")
        if not isinstance(record["version"], str) or not record["version"]:
            raise _error("Dependency metadata version is invalid.")
        if not isinstance(record["license_expression"], str):
            raise _error("Dependency license expression is invalid.")
        licenses = _validate_string_list(
            record["license_files"], "Dependency license files", path_values=True
        )
        if any(path not in descriptor_paths for path in licenses):
            raise _error("Dependency license file is not bound by the descriptor.")
        dependency_names.append(name)
    if dependency_names != sorted(dependency_names) or len(dependency_names) != len(
        set(dependency_names)
    ):
        raise _error("Dependency metadata must be unique and sorted.")
    inventory_files = value["files"]
    if not isinstance(inventory_files, list) or not inventory_files:
        raise _error("Dependency file inventory must be a non-empty list.")
    records = [
        _file_record(item, f"Dependency file {index}")
        for index, item in enumerate(inventory_files)
    ]
    paths = [record["path"] for record in records]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _error("Dependency file paths must be unique and sorted.")
    if any(not path.startswith("dependencies/site-packages/") for path in paths):
        raise _error("Dependency payload files must stay under site-packages.")
    descriptor_records = {item["path"]: item for item in descriptor["files"]}
    if any(descriptor_records.get(item["path"]) != item for item in records):
        raise _error("Dependency files do not match the runtime descriptor.")
    counts = _require_exact_fields(value["counts"], _COUNT_FIELDS, "Dependency counts")
    if counts["files"] != len(records) or counts["bytes"] != sum(
        item["size"] for item in records
    ):
        raise _error("Dependency inventory counts are invalid.")
    return records


def validate_runtime_storage_directory(path: Path, *, label: str) -> Path:
    """Validate one owned canonical directory inside the runtime trust boundary."""

    candidate = Path(path)
    if not candidate.is_absolute():
        raise _error(f"{label} path is not absolute.")
    absolute = candidate.absolute()
    try:
        metadata = absolute.lstat()
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise _error(f"{label} cannot be inspected.") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
        or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o022)
        or resolved != absolute
    ):
        raise _error(f"{label} is not trusted and protected.")
    try:
        after = absolute.lstat()
    except OSError as exc:
        raise _error(f"{label} cannot be inspected safely.") from exc
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_ctime_ns")
    if any(getattr(metadata, field) != getattr(after, field) for field in fields):
        raise _error(f"{label} changed while it was inspected.")
    return absolute


def _validate_root(root: Path) -> Path:
    absolute = validate_runtime_storage_directory(
        root, label="Runtime generation directory"
    )
    validate_runtime_storage_directory(
        absolute.parent, label="Runtime releases directory"
    )
    if absolute.parent.name == "releases":
        validate_runtime_storage_directory(
            absolute.parent.parent, label="Runtime control directory"
        )
    return absolute


def _verify_payload(
    root: Path, descriptor: dict[str, Any], platform_value: RuntimePlatform
) -> None:
    inventory_path = root / DEPENDENCY_INVENTORY_PATH
    inventory_raw = _read_regular_file(
        inventory_path, limit=MAX_INVENTORY_BYTES, label="Dependency inventory"
    )
    if hashlib.sha256(inventory_raw).hexdigest() != descriptor[
        "dependency_inventory_sha256"
    ]:
        raise _error("Dependency inventory digest does not match the descriptor.")
    inventory = _parse_json(inventory_raw, "Dependency inventory")
    _validate_inventory(inventory, descriptor, platform_value)
    expected_paths = {
        DESCRIPTOR_FILENAME,
        DEPENDENCY_INVENTORY_PATH,
        *(record["path"] for record in descriptor["files"]),
    }
    expected_directories = _directory_closure(expected_paths)
    expected_size = len(inventory_raw)
    for index, record in enumerate(descriptor["files"]):
        relative = record["path"]
        path = root / PurePosixPath(relative)
        raw = _read_regular_file(
            path, limit=MAX_GENERATION_FILE_BYTES, label=f"Runtime file {index}"
        )
        if len(raw) != record["size"] or hashlib.sha256(raw).hexdigest() != record[
            "sha256"
        ]:
            raise _error("Runtime file content does not match the descriptor.")
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != int(record["mode"], 8):
            raise _error("Runtime file mode does not match the descriptor.")
        expected_size += len(raw)
    if expected_size != descriptor["expanded_size"]:
        raise _error("Runtime expanded size does not match the descriptor.")
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if (
            relative == PREPARED_RECEIPT_FILENAME
            or relative == "environment"
            or relative.startswith("environment/")
        ):
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise _error("Runtime generation contains a symbolic link.")
        if stat.S_ISREG(metadata.st_mode) and relative not in expected_paths:
            raise _error("Runtime generation contains an unbound file.")
        if stat.S_ISDIR(metadata.st_mode):
            if relative not in expected_directories:
                raise _error("Runtime generation contains an unbound directory.")
            _validate_owned_directory(path, metadata, "Runtime payload directory")
        if not stat.S_ISREG(metadata.st_mode) and not stat.S_ISDIR(metadata.st_mode):
            raise _error("Runtime generation contains a special filesystem entry.")


def _assert_executable_directory(root: Path) -> None:
    if os.name == "nt":
        return
    probe = root / f".channelwatch-exec-probe-{uuid.uuid4().hex}"
    descriptor = -1
    try:
        descriptor = os.open(
            probe,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o700,
        )
        _write_all(descriptor, b"#!/bin/sh\nexit 0\n")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        result = subprocess.run(
            [str(probe)],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            raise _error("Runtime storage does not permit execution.")
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("Runtime storage does not permit execution.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            probe.unlink()
        except FileNotFoundError:
            pass


def _create_environment(environment: Path) -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(environment)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("The local runtime environment could not be created.") from exc
    if result.returncode != 0:
        raise _error("The local runtime environment could not be created.")


def _normalize_environment(environment: Path) -> None:
    """Reduce the venv to one image-owned interpreter link and inert metadata."""

    python_path, _ = _environment_python(environment)
    expected_base = Path(sys.executable).resolve(strict=True)
    try:
        if python_path.resolve(strict=True) != expected_base:
            raise _error("The local runtime Python executable is not image-owned.")
        links = [path for path in environment.rglob("*") if path.is_symlink()]
        for path in links:
            path.unlink()
        directories = sorted(
            (path for path in environment.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in directories:
            try:
                path.rmdir()
            except OSError as exc:
                if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                    raise
        python_path.parent.mkdir(parents=True, exist_ok=True)
        python_path.symlink_to(expected_base)
    except OSError as exc:
        raise _error("The local runtime environment could not be normalized.") from exc


def _normalize_environment_modes(environment: Path) -> None:
    """Apply the verifier's fixed modes to the locally generated venv."""

    if os.name == "nt":  # pragma: no cover - persistent generations require Linux
        return
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise _error("Safe local runtime environment normalization is unavailable.")
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    current_uid = os.geteuid() if hasattr(os, "geteuid") else None

    def trusted(metadata: os.stat_result, *, directory: bool) -> bool:
        expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
        return expected_kind(metadata.st_mode) and (
            current_uid is None or metadata.st_uid == current_uid
        )

    def normalize_directory(descriptor: int, relative: PurePosixPath) -> None:
        opened = os.fstat(descriptor)
        if not trusted(opened, directory=True):
            raise _error("The local runtime environment directory is not trusted.")
        os.fchmod(descriptor, 0o755)
        for name in os.listdir(descriptor):
            child_relative = relative / name
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, directory_flags, dir_fd=descriptor)
                try:
                    child_opened = os.fstat(child)
                    if (
                        not trusted(child_opened, directory=True)
                        or child_opened.st_dev != before.st_dev
                        or child_opened.st_ino != before.st_ino
                    ):
                        raise _error(
                            "The local runtime environment directory changed unexpectedly."
                        )
                    normalize_directory(child, child_relative)
                finally:
                    os.close(child)
                continue
            relative_name = f"environment/{child_relative.as_posix()}"
            if stat.S_ISLNK(before.st_mode):
                if relative_name != "environment/bin/python":
                    raise _error(
                        "The local runtime environment contains an unbound link."
                    )
                continue
            if not stat.S_ISREG(before.st_mode):
                raise _error(
                    "The local runtime environment contains a special entry."
                )
            if (
                relative_name not in _ENVIRONMENT_REGULAR_PATHS
                or name in _FORBIDDEN_ENVIRONMENT_NAMES
                or name.lower().endswith(_FORBIDDEN_ENVIRONMENT_SUFFIXES)
            ):
                raise _error("The local runtime environment contains an unbound file.")
            child = os.open(name, file_flags, dir_fd=descriptor)
            try:
                child_opened = os.fstat(child)
                if (
                    not trusted(child_opened, directory=False)
                    or child_opened.st_nlink != 1
                    or child_opened.st_dev != before.st_dev
                    or child_opened.st_ino != before.st_ino
                ):
                    raise _error(
                        "The local runtime environment file changed unexpectedly."
                    )
                os.fchmod(child, 0o644)
                os.fsync(child)
            finally:
                os.close(child)
        os.fsync(descriptor)

    descriptor = -1
    try:
        descriptor = os.open(environment, directory_flags)
        normalize_directory(descriptor, PurePosixPath("."))
    except OSError as exc:
        raise _error("The local runtime environment could not be normalized.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _environment_python(environment: Path) -> tuple[Path, str]:
    path = environment / "bin/python"
    if path.exists() or path.is_symlink():
        return path, "environment/bin/python"
    raise _error("The local runtime environment has no Python executable.")


def _read_symlink(path: Path, *, label: str) -> bytes:
    try:
        before = path.lstat()
        if (
            not stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or (hasattr(os, "geteuid") and before.st_uid != os.geteuid())
        ):
            raise _error(f"{label} is not a trusted symbolic link.")
        target = os.readlink(path)
        after = path.lstat()
    except OSError as exc:
        raise _error(f"{label} cannot be inspected safely.") from exc
    identity_fields = (
        "st_dev",
        "st_ino",
        "st_mtime_ns",
        "st_ctime_ns",
        "st_uid",
        "st_nlink",
    )
    if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
        raise _error(f"{label} changed while it was inspected.")
    raw = os.fsencode(target)
    if not raw or len(raw) > 4096:
        raise _error(f"{label} target is invalid.")
    return raw


def _environment_file_record(environment: Path, path: Path) -> dict[str, Any]:
    relative = f"environment/{path.relative_to(environment).as_posix()}"
    metadata = path.lstat()
    mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
    if stat.S_ISREG(metadata.st_mode):
        if (
            relative not in _ENVIRONMENT_REGULAR_PATHS
            or path.name in _FORBIDDEN_ENVIRONMENT_NAMES
            or path.name.lower().endswith(_FORBIDDEN_ENVIRONMENT_SUFFIXES)
        ):
            raise _error("The local runtime environment contains an unbound file.")
        raw = _read_regular_file(
            path, limit=MAX_GENERATION_FILE_BYTES, label="Runtime environment file"
        )
        if mode != "0644":
            raise _error("The local runtime environment file mode is not allowed.")
        kind = "regular"
    elif stat.S_ISLNK(metadata.st_mode):
        if relative != "environment/bin/python":
            raise _error("The local runtime environment contains an unbound link.")
        raw = _read_symlink(path, label="Runtime environment interpreter link")
        kind = "symlink"
    else:
        raise _error("The local runtime environment contains a special entry.")
    return {
        "path": relative,
        "kind": kind,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "mode": mode,
    }


def _directory_closure(paths: Iterable[str]) -> set[str]:
    directories: set[str] = set()
    for relative in paths:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _validate_owned_directory(
    path: Path, metadata: os.stat_result, label: str
) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
    ):
        raise _error(f"{label} is not trusted.")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o755:
        raise _error(f"{label} mode is not allowed.")
    try:
        after = path.lstat()
    except OSError as exc:
        raise _error(f"{label} cannot be inspected safely.") from exc
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_ctime_ns")
    if any(
        getattr(metadata, field) != getattr(after, field)
        for field in identity_fields
    ):
        raise _error(f"{label} changed while it was inspected.")


def _environment_inventory(environment: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    directories: list[tuple[Path, os.stat_result]] = []
    for path in environment.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            directories.append((path, metadata))
            continue
        records.append(_environment_file_record(environment, path))
    records.sort(key=lambda item: item["path"])
    paths = {record["path"] for record in records}
    if (
        "environment/pyvenv.cfg" not in paths
        or "environment/bin/python" not in paths
    ):
        raise _error("The local runtime environment is incomplete.")
    expected_directories = _directory_closure(paths)
    for path, metadata in directories:
        relative = f"environment/{path.relative_to(environment).as_posix()}"
        if relative not in expected_directories:
            raise _error(
                "The local runtime environment contains an unbound directory."
            )
        _validate_owned_directory(path, metadata, "Runtime environment directory")
    return records


def _validate_environment_files(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > 64:
        raise _error("Prepared runtime environment inventory is invalid.")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        record = _require_exact_fields(
            item, _ENVIRONMENT_FILE_FIELDS, f"Runtime environment file {index}"
        )
        relative = _relative_path(
            record["path"], f"Runtime environment file {index} path"
        )
        kind = record["kind"]
        if kind == "regular":
            if relative not in _ENVIRONMENT_REGULAR_PATHS or record["mode"] != "0644":
                raise _error("Prepared runtime environment file is not allowed.")
        elif kind == "symlink":
            if relative != "environment/bin/python":
                raise _error("Prepared runtime environment link is not allowed.")
        else:
            raise _error("Prepared runtime environment entry kind is invalid.")
        digest = record["sha256"]
        if not isinstance(digest, str) or not _HEX_64_RE.fullmatch(digest):
            raise _error("Prepared runtime environment digest is invalid.")
        _positive_int(
            record["size"], "Prepared runtime environment size", allow_zero=True
        )
        mode = record["mode"]
        if not isinstance(mode, str) or not _MODE_RE.fullmatch(mode):
            raise _error("Prepared runtime environment mode is invalid.")
        records.append(record)
    paths = [record["path"] for record in records]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise _error("Prepared runtime environment paths must be unique and sorted.")
    return records


def _verify_environment(
    environment: Path, expected_relative: str, expected_files: Any
) -> Path:
    try:
        metadata = environment.lstat()
    except OSError as exc:
        raise _error("The local runtime environment is missing.") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise _error("The local runtime environment is not a directory.")
    _validate_owned_directory(
        environment, metadata, "The local runtime environment directory"
    )
    if expected_relative != "environment/bin/python":
        raise _error("The local runtime Python path is unsupported.")
    expected_records = _validate_environment_files(expected_files)
    if _environment_inventory(environment) != expected_records:
        raise _error("The local runtime environment does not match its receipt.")
    python_path = environment.parent / PurePosixPath(expected_relative)
    expected_base = Path(sys.executable).resolve(strict=True)
    try:
        resolved_python = python_path.resolve(strict=True)
    except OSError as exc:
        raise _error("The local runtime Python executable is missing.") from exc
    if resolved_python != expected_base:
        raise _error("The local runtime Python executable is not image-owned.")
    if not os.access(python_path, os.X_OK):
        raise _error("The local runtime Python executable cannot run.")
    return python_path


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    raw = _canonical_json(value)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o644,
        )
        os.fchmod(descriptor, 0o644)
        _write_all(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise _error("The prepared runtime receipt could not be persisted.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _descriptor_from_disk(root: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_file(
        root / DESCRIPTOR_FILENAME,
        limit=MAX_DESCRIPTOR_BYTES,
        label="Runtime descriptor",
    )
    return _parse_json(raw, "Runtime descriptor"), raw


def _runtime_generation(
    root: Path, descriptor: dict[str, Any], python_executable: Path
) -> RuntimeGeneration:
    return RuntimeGeneration(
        generation_id=descriptor["generation_id"],
        app_dir=root,
        python_executable=python_executable,
        dependency_digest=descriptor["dependency_inventory_sha256"],
    )


def _remove_partial_preparation(environment: Path, receipt_path: Path) -> None:
    try:
        receipt_path.unlink()
    except FileNotFoundError:
        pass
    if environment.exists() and environment.is_dir() and not environment.is_symlink():
        shutil.rmtree(environment, ignore_errors=True)


def _value_references_generation(value: Any, root: Path) -> bool:
    if isinstance(value, dict):
        return any(_value_references_generation(item, root) for item in value.values())
    if isinstance(value, list):
        return any(_value_references_generation(item, root) for item in value)
    if not isinstance(value, str):
        return False
    if value == root.name:
        return True
    if not os.path.isabs(value):
        return False
    return Path(os.path.abspath(os.path.normpath(value))) == root


def _assert_incomplete_generation_unreferenced(root: Path) -> None:
    if root.parent.name != "releases":
        return
    runtime_dir = root.parent.parent
    before = runtime_dir.lstat()
    controls = sorted(
        (
            path
            for path in runtime_dir.iterdir()
            if path.name in _RECOVERY_REFERENCE_NAMES
            or (
                path.name.startswith("activation-")
                and path.name.endswith(".json")
            )
            or path.name.startswith(".restart-required.json.candidate-")
        ),
        key=lambda path: path.name,
    )
    for index, control in enumerate(controls):
        value = _parse_json(
            _read_regular_file(
                control,
                limit=MAX_DESCRIPTOR_BYTES,
                label=f"Runtime recovery control {index}",
            ),
            f"Runtime recovery control {index}",
        )
        if _value_references_generation(value, root):
            raise _error(
                "Interrupted runtime generation is referenced by runtime control state."
            )
    after = runtime_dir.lstat()
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise _error("Runtime control state changed during interrupted recovery.")


def _remove_owned_preparation_transient(path: Path, *, mode: int, limit: int) -> None:
    before = path.lstat()
    _read_regular_file(path, limit=limit, label="Runtime preparation transient")
    after = path.lstat()
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise _error("Runtime preparation transient changed during recovery.")
    if stat.S_IMODE(before.st_mode) != mode:
        raise _error("Runtime preparation transient mode is not allowed.")
    try:
        path.unlink()
    except OSError as exc:
        raise _error("Runtime preparation transient could not be removed safely.") from exc


def _recover_interrupted_preparation(root: Path, environment: Path) -> None:
    _assert_incomplete_generation_unreferenced(root)
    for path in sorted(root.iterdir(), key=lambda candidate: candidate.name):
        if _EXECUTION_PROBE_RE.fullmatch(path.name):
            _remove_owned_preparation_transient(path, mode=0o700, limit=4096)
        elif _RECEIPT_TEMP_RE.fullmatch(path.name):
            _remove_owned_preparation_transient(
                path, mode=0o644, limit=MAX_DESCRIPTOR_BYTES
            )
    if environment.exists() or environment.is_symlink():
        try:
            metadata = environment.lstat()
        except OSError as exc:
            raise _error("Interrupted runtime environment cannot be inspected.") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())
            or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o022)
            or not getattr(shutil.rmtree, "avoids_symlink_attacks", False)
        ):
            raise _error("Interrupted runtime environment is not an owned directory.")
        shutil.rmtree(environment)
    _assert_incomplete_generation_unreferenced(root)


@contextmanager
def _generation_prepare_lock(root: Path):
    """Serialize preparation for one immutable descriptor-named generation."""

    if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise _error("Safe runtime generation locking is unavailable.")
    absolute = Path(root).absolute()
    if not _HEX_64_RE.fullmatch(absolute.name):
        raise _error("Runtime generation directory identity is invalid.")
    parent = validate_runtime_storage_directory(
        absolute.parent, label="Runtime releases directory"
    )
    lock_path = parent / f".prepare-{absolute.name}.lock"
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        named = lock_path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_dev != named.st_dev
            or opened.st_ino != named.st_ino
            or not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or (
                hasattr(os, "geteuid")
                and (opened.st_uid != os.geteuid() or named.st_uid != os.geteuid())
            )
        ):
            raise _error("Runtime generation preparation lock is not trusted.")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        after = lock_path.lstat()
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
            or (hasattr(os, "geteuid") and after.st_uid != os.geteuid())
        ):
            raise _error("Runtime generation preparation lock changed unexpectedly.")
        yield
    except OSError as exc:
        raise _error("Runtime generation preparation lock is unavailable.") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def prepare_runtime_generation(
    release_dir: Path, descriptor: dict[str, Any]
) -> RuntimeGeneration:
    """Verify an inactive generation, create its final venv, and receipt it."""

    requested_root = Path(release_dir).absolute()
    with _generation_prepare_lock(requested_root):
        return _prepare_runtime_generation_locked(requested_root, descriptor)


def _prepare_runtime_generation_locked(
    release_dir: Path, descriptor: dict[str, Any]
) -> RuntimeGeneration:
    root = _validate_root(release_dir)
    platform_value = detect_runtime_platform()
    validate_runtime_descriptor(descriptor, platform_value)
    if root.name != descriptor["generation_id"]:
        raise _error("Runtime generation is not in its immutable final path.")
    disk_descriptor, _ = _descriptor_from_disk(root)
    if disk_descriptor != descriptor:
        raise _error("Runtime descriptor does not match the prepared release.")
    receipt_path = root / PREPARED_RECEIPT_FILENAME
    if receipt_path.exists() or receipt_path.is_symlink():
        _verify_payload(root, descriptor, platform_value)
        receipt = _parse_json(
            _read_regular_file(
                receipt_path, limit=MAX_DESCRIPTOR_BYTES, label="Prepared runtime receipt"
            ),
            "Prepared runtime receipt",
        )
        _require_exact_fields(receipt, _RECEIPT_FIELDS, "Prepared runtime receipt")
        generation = _runtime_generation(
            root,
            descriptor,
            _verify_environment(
                root / "environment",
                receipt["python_executable"],
                receipt["environment_files"],
            ),
        )
        verify_runtime_generation(generation)
        return generation
    environment = root / "environment"
    _recover_interrupted_preparation(root, environment)
    _verify_payload(root, descriptor, platform_value)
    _assert_executable_directory(root)
    try:
        _create_environment(environment)
        _normalize_environment(environment)
        _normalize_environment_modes(environment)
        python_path, python_relative = _environment_python(environment)
        environment_files = _environment_inventory(environment)
        python_path = _verify_environment(
            environment, python_relative, environment_files
        )
        _verify_payload(root, descriptor, platform_value)
        receipt = {
            "schema": PREPARED_RECEIPT_SCHEMA,
            "generation_id": descriptor["generation_id"],
            "descriptor_sha256": descriptor_generation_id(descriptor),
            "dependency_digest": descriptor["dependency_inventory_sha256"],
            "application_version": descriptor["application_version"],
            "architecture": platform_value.architecture,
            "python_cache_tag": platform_value.python_cache_tag,
            "libc_family": platform_value.libc_family,
            "libc_version": list(platform_value.libc_version),
            "python_executable": python_relative,
            "environment_files": environment_files,
        }
        _atomic_write_json(receipt_path, receipt)
    except OSError as exc:
        _remove_partial_preparation(environment, receipt_path)
        raise _error("The local runtime environment could not be prepared.") from exc
    except BaseException:
        _remove_partial_preparation(environment, receipt_path)
        raise
    generation = _runtime_generation(root, descriptor, python_path)
    verify_runtime_generation(generation)
    return generation


def verify_runtime_generation(generation: RuntimeGeneration) -> None:
    """Revalidate a prepared receipt, payload, inventory, and local venv."""

    root = _validate_root(generation.app_dir)
    if root != generation.app_dir.resolve(strict=True):
        raise _error("Runtime generation path is not canonical.")
    descriptor, _ = _descriptor_from_disk(root)
    platform_value = detect_runtime_platform()
    validate_runtime_descriptor(descriptor, platform_value)
    if root.name != descriptor["generation_id"]:
        raise _error("Runtime generation directory identity is invalid.")
    _verify_payload(root, descriptor, platform_value)
    receipt = _parse_json(
        _read_regular_file(
            root / PREPARED_RECEIPT_FILENAME,
            limit=MAX_DESCRIPTOR_BYTES,
            label="Prepared runtime receipt",
        ),
        "Prepared runtime receipt",
    )
    _require_exact_fields(receipt, _RECEIPT_FIELDS, "Prepared runtime receipt")
    expected_receipt = {
        "schema": PREPARED_RECEIPT_SCHEMA,
        "generation_id": descriptor["generation_id"],
        "descriptor_sha256": descriptor_generation_id(descriptor),
        "dependency_digest": descriptor["dependency_inventory_sha256"],
        "application_version": descriptor["application_version"],
        "architecture": platform_value.architecture,
        "python_cache_tag": platform_value.python_cache_tag,
        "libc_family": platform_value.libc_family,
        "libc_version": list(platform_value.libc_version),
        "python_executable": receipt["python_executable"],
        "environment_files": receipt["environment_files"],
    }
    if receipt != expected_receipt or receipt["python_executable"] != "environment/bin/python":
        raise _error("Prepared runtime receipt does not match this generation.")
    python_path = _verify_environment(
        root / "environment",
        receipt["python_executable"],
        receipt["environment_files"],
    )
    expected_generation = _runtime_generation(root, descriptor, python_path)
    if generation != expected_generation:
        raise _error("Runtime generation reference does not match its receipt.")
