"""Update Center runtime and bundle management."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import stat as stat_module
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

try:
    import fcntl
except ImportError:  # pragma: no cover - container runtime is POSIX
    fcntl = None

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.helpers.atomic_io import (
    _atomic_write_secret_bytes,
    atomic_write_bytes,
    atomic_write_json,
    fsync_directory,
)
from core.image_metadata import resolve_image_metadata
from core.update_catalog import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_UPDATE_CATALOG_URL,
    DeliveryMode,
    LauncherProtocol,
    launcher_protocol_for_image_version,
    normalize_catalog,
    select_catalog_release,
)

RUNTIME_ABI = "channelwatch-runtime-v1"
MANIFEST_SCHEMA_VERSION = 1
DEFAULT_UPDATE_MANIFEST_URL = "https://channelwatch.coderluii.dev/updates/stable.json"
TRUSTED_UPDATE_HOSTS = {
    "channelwatch.coderluii.dev",
    "github.com",
    "release-assets.githubusercontent.com",
}
DEFAULT_IMAGE_APP_DIR = Path(os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app"))

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 80 * 1024 * 1024
MAX_BUNDLE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES = 180 * 1024 * 1024
MAX_BUNDLE_MEMBER_COUNT = 3000
LOCK_STALE_SECONDS = 60 * 60
ACTIVATION_TIMEOUT_SECONDS = 120
RESTART_REQUIRED_FILE = "restart-required.json"
RESTART_JOURNAL_LOCK_FILE = "restart-required.lock"
PROTOCOL_THREE_HANDOFF_FILE = "restart-services-accepted.json"
PROTOCOL_THREE_HANDOFF_SCHEMA = 1

# Keep protocol reconciliation sleeps module-local so tests and callers can
# replace this wait without mutating the process-wide ``time.sleep`` function.
_protocol_three_sleep = time.sleep
PROTOCOL_THREE_HANDOFF_FIELDS = {"schema", "journal", "old_processes"}
PROTOCOL_THREE_PROCESS_NAMES = {"core", "ui"}
PROTOCOL_THREE_PROCESS_IDENTITY_FIELDS = {"pid", "start"}
PROTOCOL_THREE_RESTART_HELPER_LOCK_FILE = "restart-services.lock"
PROTOCOL_THREE_RECONCILE_GRACE_SECONDS = 0.25
PROTOCOL_THREE_RECONCILE_TIMEOUT_SECONDS = 12.0
PROTOCOL_THREE_RECONCILE_INTERVAL_SECONDS = 0.05
RUNTIME_CONTROL_MAX_BYTES = 256 * 1024
ACTIVATION_OUTCOME_LOCK_FILE = "activation-outcome.lock"
RESTART_JOURNAL_SCHEMA = 2
RESTART_CONTROL_FILES = (
    "active.json",
    "rollback.json",
    "activation-pending.json",
    "activation-core-ready.json",
    "activation-ui-ready.json",
    "update-job.json",
)
RESTART_JOURNAL_OPERATIONS = {"apply", "manual_rollback", "activation_rollback"}
RESTART_JOURNAL_PHASES = {"commit", "abort"}
RESTART_JOURNAL_FIELDS = {
    "schema",
    "reason",
    "operation",
    "phase",
    "job_id",
    "source_active",
    "replace_activation_state",
    "created_at",
    "control",
}


def _read_runtime_json_strict(path: Path, *, label: str) -> Any:
    """Read one bounded, unchanged, single-link runtime control file."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise UpdateLockedError(f"Safe {label} reads are unavailable.")
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise UpdateLockedError(f"The {label} cannot be inspected safely.") from exc
    if (
        not stat_module.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or before.st_size < 0
        or before.st_size > RUNTIME_CONTROL_MAX_BYTES
    ):
        raise UpdateLockedError(f"The {label} is not a trusted bounded regular file.")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise UpdateLockedError(f"The {label} cannot be opened safely.") from exc
    try:
        opened = os.fstat(fd)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not stat_module.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or opened.st_size > RUNTIME_CONTROL_MAX_BYTES
        ):
            raise UpdateLockedError(f"The {label} changed before it was opened.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65_536, RUNTIME_CONTROL_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > RUNTIME_CONTROL_MAX_BYTES:
                raise UpdateLockedError(f"The {label} exceeds the safe size limit.")
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    try:
        after_name = os.lstat(path)
    except OSError as exc:
        raise UpdateLockedError(f"The {label} changed while being read.") from exc
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
        getattr(before, field) != getattr(opened, field)
        or getattr(opened, field) != getattr(after_fd, field)
        or getattr(after_fd, field) != getattr(after_name, field)
        for field in identity_fields
    ):
        raise UpdateLockedError(f"The {label} changed while being read.")
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateLockedError(f"The {label} does not contain valid JSON.") from exc


BUNDLE_LEGAL_MEMBERS = frozenset(
    {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_LICENSES.md",
        "licenses/copyleft/CORRESPONDING_SOURCE.md",
        "licenses/copyleft/GCC-exception-3.1.txt",
        "licenses/copyleft/GPL-1.0-only.txt",
        "licenses/copyleft/GPL-2.0-only.txt",
        "licenses/copyleft/GPL-3.0-only.txt",
        "licenses/copyleft/LGPL-2.1-only.txt",
    }
)

# Public verification keys only. The matching private key belongs in GitHub
# Actions secrets, not in the repository or runtime config volume.
UPDATE_PUBLIC_KEYS: dict[str, str] = {
    "channelwatch-update-ed25519-2026-06": "WrOYZbZ5OZqylyghaE4V/JPcH3JdkWaWtrQ5kPj6FWk=",
}


class UpdateCenterError(RuntimeError):
    """Base class for update center failures."""


class UpdateManifestError(UpdateCenterError):
    """Raised when update metadata is missing, unsafe, or invalid."""


class UpdateBundleError(UpdateCenterError):
    """Raised when a bundle cannot be verified or extracted."""


class UpdateLockedError(UpdateCenterError):
    """Raised when an update operation is already running."""


class UpdateRestartError(UpdateCenterError):
    """Raised when a required coordinated container restart cannot be started."""


def launcher_compatibility_status(
    *, image_version: str | None = None, running_app_dir: str | None = None
) -> dict[str, Any]:
    """Return non-sensitive launcher/image compatibility for API diagnostics."""

    metadata = resolve_image_metadata(image_app_dir=DEFAULT_IMAGE_APP_DIR)
    configured_image = (
        str(image_version).strip().lstrip("v")
        if image_version is not None
        else metadata.version
    )
    protocol = int(launcher_protocol_for_image_version(configured_image))
    try:
        minimum_image_satisfied = compare_versions(configured_image, "0.9.11") >= 0
    except (TypeError, ValueError):
        minimum_image_satisfied = False
    app_dir = str(running_app_dir or os.environ.get("CHANNELWATCH_APP_DIR", "")).strip()
    image_dir = str(DEFAULT_IMAGE_APP_DIR.resolve())
    try:
        bundle_active = bool(app_dir and str(Path(app_dir).resolve()) != image_dir)
    except OSError:
        bundle_active = bool(app_dir)
    return {
        "image_version": configured_image,
        "launcher_protocol": protocol,
        "bundle_active": bundle_active,
        "minimum_safe_image_version": "0.9.11",
        "safe_for_app_updates": (
            minimum_image_satisfied and protocol >= int(LauncherProtocol.LEGACY_ADOPT)
        ),
        "recovery_capable": protocol >= int(LauncherProtocol.RECOVERY_CAPABLE),
    }


def guard_legacy_launcher_before_start(
    *,
    config_dir: Path,
    running_version: str,
    restart_callable: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Defense-in-depth if a bundle is ever selected by a protocol-0 launcher.

    The v0.9.9 core launcher rejects Supervisor's ``--stay-alive`` argument
    before importing bundle code, so this hook is not evidence that the
    immutable published image can recover itself. If any child does reach the
    bundle, the hook restores the prior image selection and requests one
    whole-container restart. No network or unsigned input is used here.
    """

    status = launcher_compatibility_status()
    if not status["bundle_active"] or status["safe_for_app_updates"]:
        return {**status, "allowed": True, "recovery_started": False}

    runtime_dir = runtime_dir_for_config(Path(config_dir))
    active_path = runtime_dir / "active.json"
    rollback_path = runtime_dir / "rollback.json"
    job_path = runtime_dir / "update-job.json"
    lock_path = runtime_dir / "legacy-launcher-guard.lock"
    with UpdateOperationLock(lock_path, wait_timeout=5.0):
        rollback = load_json(rollback_path, None)
        previous = (
            rollback.get("previous_active") if isinstance(rollback, dict) else None
        )
        if isinstance(previous, dict) and previous.get("path"):
            atomic_write_json(active_path, previous)
            rolled_back_to = str(previous.get("version") or "previous bundle")
        else:
            try:
                active_path.unlink()
            except FileNotFoundError:
                pass
            rolled_back_to = "image"
        atomic_write_json(
            job_path,
            {
                "job_id": f"legacy-launcher-guard-{int(time.time())}",
                "operation": "activation_guard",
                "status": "image_required",
                "version": running_version.strip().lstrip("v"),
                "message": (
                    "This container image predates safe in-app activation. "
                    "ChannelWatch restored the image runtime; preserve /config, "
                    "pull/recreate v0.9.18, and do not retry the v0.9.9 updater."
                ),
                "minimum_image_version": "0.9.18",
                "rollback_applied": True,
                "rolled_back_to": rolled_back_to,
                "updated_at": utc_now(),
            },
        )
    restart_started = False
    if restart_callable is not None:
        restart_started = bool(restart_callable())
    return {
        **status,
        "allowed": False,
        "recovery_started": restart_started,
        "reason": "launcher_protocol_0_requires_image_refresh",
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_version(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("v")
    parts = text.split(".")
    if len(parts) != 3:
        raise ValueError(f"Version {value!r} is not X.Y.Z.")
    return int(parts[0]), int(parts[1]), int(parts[2])


def compare_versions(left: str, right: str) -> int:
    l_ver = parse_version(left)
    r_ver = parse_version(right)
    return (l_ver > r_ver) - (l_ver < r_ver)


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise UpdateManifestError("Signature is not valid base64.") from exc


def verify_ed25519_signature(
    public_keys: dict[str, str],
    key_id: str,
    signature_b64: str,
    data: bytes,
) -> None:
    public_b64 = public_keys.get(key_id)
    if not public_b64:
        raise UpdateManifestError(f"Unknown update signing key: {key_id}.")
    try:
        key = Ed25519PublicKey.from_public_bytes(_b64decode(public_b64))
        key.verify(_b64decode(signature_b64), data)
    except InvalidSignature as exc:
        raise UpdateManifestError("Update signature could not be verified.") from exc
    except ValueError as exc:
        raise UpdateManifestError("Update public key is invalid.") from exc


def validate_trusted_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UpdateManifestError("Update URLs must use https.")
    try:
        normalized_hostname = parsed.hostname.encode("ascii").decode("ascii").lower()
    except UnicodeError as exc:
        raise UpdateManifestError("Update URL host must use ASCII.") from exc
    if normalized_hostname not in TRUSTED_UPDATE_HOSTS:
        raise UpdateManifestError("Update URL host is not trusted.")
    return url


class _TrustedUpdateRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib sends a request to an untrusted host."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_bytes(url: str, *, max_bytes: int, timeout: float = 20.0) -> bytes:
    validate_trusted_url(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": "ChannelWatch-UpdateCenter"}
    )
    opener = urllib.request.build_opener(_TrustedUpdateRedirectHandler())
    try:
        with opener.open(
            req, timeout=timeout
        ) as response:  # nosec B310: every URL is allowlisted.
            # A custom handler or transport must not be able to bypass the
            # redirect check above. Validate the final URL before reading any
            # response bytes.
            validate_trusted_url(response.geturl())
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UpdateManifestError(
                        "Downloaded update data exceeds size limit."
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateManifestError(
            "The update service could not be reached. Check container internet and DNS, then try again."
        ) from exc


def normalize_manifest(
    raw: dict[str, Any], public_keys: dict[str, str]
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UpdateManifestError("Update manifest must be a JSON object.")
    if raw.get("schema") != MANIFEST_SCHEMA_VERSION:
        raise UpdateManifestError("Unsupported update manifest schema.")
    payload = raw.get("payload")
    signature = raw.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, dict):
        raise UpdateManifestError("Update manifest is missing payload or signature.")

    key_id = str(signature.get("key_id") or "")
    value = str(signature.get("value") or "")
    if signature.get("alg") != "ed25519" or not key_id or not value:
        raise UpdateManifestError("Update manifest signature is incomplete.")
    verify_ed25519_signature(
        public_keys, key_id, value, canonical_payload_bytes(payload)
    )

    version = str(payload.get("version") or "").strip().lstrip("v")
    parse_version(version)
    bundle_url = str(payload.get("bundle_url") or "")
    release_url = str(payload.get("release_url") or "")
    if bundle_url:
        validate_trusted_url(bundle_url)
    if release_url:
        validate_trusted_url(release_url)

    image_required_value = payload.get("image_required", False)
    if type(image_required_value) is not bool:
        raise UpdateManifestError(
            "Update manifest image_required must be an explicit boolean."
        )
    image_required = image_required_value
    delivery_value = str(payload.get("delivery_mode") or "")
    if not delivery_value:
        delivery_mode = (
            DeliveryMode.IMAGE_REQUIRED if image_required else DeliveryMode.APP_UPDATE
        )
    else:
        try:
            delivery_mode = DeliveryMode(delivery_value)
        except ValueError as exc:
            raise UpdateManifestError(
                "Update manifest delivery mode is unsupported."
            ) from exc
        if image_required != (delivery_mode is DeliveryMode.IMAGE_REQUIRED):
            raise UpdateManifestError(
                "Update manifest delivery mode conflicts with image_required."
            )

    minimum_image_version = (
        str(payload.get("minimum_image_version") or "0.9.10").strip().lstrip("v")
    )
    parse_version(minimum_image_version)
    minimum_launcher_protocol = payload.get("minimum_launcher_protocol", 1)
    updater_protocol = payload.get("updater_protocol", 2)
    if type(minimum_launcher_protocol) is not int or minimum_launcher_protocol < 0:
        raise UpdateManifestError(
            "Update manifest minimum_launcher_protocol must be a non-negative integer."
        )
    if type(updater_protocol) is not int or updater_protocol < 1:
        raise UpdateManifestError(
            "Update manifest updater_protocol must be a positive integer."
        )

    automatic_install_allowed = payload.get("automatic_install_allowed", True)
    recovery_compatible = payload.get("recovery_compatible", False)
    if type(automatic_install_allowed) is not bool:
        raise UpdateManifestError(
            "Update manifest automatic_install_allowed must be an explicit boolean."
        )
    if type(recovery_compatible) is not bool:
        raise UpdateManifestError(
            "Update manifest recovery_compatible must be an explicit boolean."
        )

    return {
        "schema": MANIFEST_SCHEMA_VERSION,
        "payload": {
            **payload,
            "version": version,
            "version_tag": str(payload.get("version_tag") or f"v{version}"),
            "runtime_abi": str(payload.get("runtime_abi") or ""),
            "settings_schema_version": int(payload.get("settings_schema_version") or 0),
            "image_required": image_required,
            "delivery_mode": delivery_mode.value,
            "image_refresh_recommended": (
                delivery_mode is DeliveryMode.APP_UPDATE_WITH_IMAGE_REFRESH
            ),
            "minimum_image_version": minimum_image_version,
            "minimum_launcher_protocol": minimum_launcher_protocol,
            "updater_protocol": updater_protocol,
            "automatic_install_allowed": automatic_install_allowed,
            "automatic_install_after": payload.get("automatic_install_after"),
            "recovery_compatible": recovery_compatible,
            "recommended_image_version": str(
                payload.get("recommended_image_version") or version
            )
            .strip()
            .lstrip("v"),
            "highlights": [
                str(item) for item in payload.get("highlights", []) if str(item).strip()
            ],
        },
        "signature": {
            "alg": "ed25519",
            "key_id": key_id,
            "value": value,
        },
    }


def read_manifest_bytes(data: bytes, public_keys: dict[str, str]) -> dict[str, Any]:
    if len(data) > MAX_MANIFEST_BYTES:
        raise UpdateManifestError("Update manifest exceeds size limit.")
    try:
        raw = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateManifestError("Update manifest is not valid JSON.") from exc
    return normalize_manifest(raw, public_keys)


def read_update_document_bytes(
    data: bytes,
    public_keys: dict[str, str],
    *,
    current_version: str,
    runtime_abi: str,
    settings_schema_version: int,
    launcher_protocol: int,
    recovery: bool = False,
) -> dict[str, Any]:
    """Read either the immutable schema-1 bridge or a signed schema-2 catalog.

    The return shape deliberately matches the historical selected-manifest
    contract so existing API and UI code needs no schema switch. Catalog
    provenance is additive under ``catalog``.
    """

    if len(data) > MAX_MANIFEST_BYTES:
        raise UpdateManifestError("Update metadata exceeds size limit.")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateManifestError("Update metadata is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise UpdateManifestError("Update metadata must be a JSON object.")
    if raw.get("schema") == MANIFEST_SCHEMA_VERSION:
        manifest = normalize_manifest(raw, public_keys)
        if recovery and manifest["payload"].get("recovery_compatible") is not True:
            raise UpdateManifestError(
                "The signed legacy update is not approved for recovery mode."
            )
        return manifest
    if raw.get("schema") != CATALOG_SCHEMA_VERSION:
        raise UpdateManifestError("Unsupported update metadata schema.")
    try:
        catalog = normalize_catalog(
            raw,
            public_keys=public_keys,
            verify_signature=verify_ed25519_signature,
            canonical_payload=canonical_payload_bytes,
            validate_url=validate_trusted_url,
        )
        selection = select_catalog_release(
            catalog,
            current_version=current_version,
            runtime_abi=runtime_abi,
            settings_schema_version=settings_schema_version,
            launcher_protocol=launcher_protocol,
            recovery=recovery,
        )
    except (TypeError, ValueError) as exc:
        raise UpdateManifestError(str(exc)) from exc
    if selection.release is None:
        raise UpdateManifestError(
            "The signed catalog contains no release compatible with this installation."
        )
    return {
        "schema": CATALOG_SCHEMA_VERSION,
        "payload": selection.release,
        "signature": catalog["signature"],
        "catalog": {
            "channel": catalog["payload"]["channel"],
            "published_at": catalog["payload"].get("published_at"),
            "selection_reason": selection.reason,
            "considered_versions": list(selection.considered_versions),
            "payload_sha256": sha256_hex(canonical_payload_bytes(catalog["payload"])),
        },
    }


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def is_path_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _validate_bundle_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise UpdateBundleError(f"Bundle contains unsafe member path: {name!r}.")
    return path


def _is_allowed_bundle_member(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    if name.endswith("/"):
        return True

    blocked_parts = {
        ".git",
        ".github",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "tests",
        "scratch",
        "plans",
        "docs",
    }
    if any(part in blocked_parts for part in path.parts):
        return False
    if path.name in {"AGENTS.md", "RELEASE.md", ".env"} or path.suffix in {
        ".pyc",
        ".pyo",
    }:
        return False
    if path.name.startswith(".env"):
        return False

    first = path.parts[0]
    return (
        name == "channelwatch-bundle.json"
        or name in BUNDLE_LEGAL_MEMBERS
        or first == "core"
        or path.parts[:2] == ("ui", "backend")
    )


def validate_bundle_archive(
    bundle_bytes: bytes,
    *,
    expected_version: str,
    expected_runtime_abi: str,
    expected_settings_schema_version: int,
) -> dict[str, Any]:
    if len(bundle_bytes) > MAX_BUNDLE_BYTES:
        raise UpdateBundleError("Update bundle exceeds download size limit.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(bundle_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise UpdateBundleError("Update bundle is not a valid zip file.") from exc

    with zf:
        member_count = 0
        total_uncompressed = 0
        names = zf.namelist()
        for info in zf.infolist():
            _validate_bundle_member_path(info.filename)
            if not _is_allowed_bundle_member(info.filename):
                raise UpdateBundleError(
                    f"Update bundle contains unsupported member: {info.filename!r}."
                )
            if info.is_dir():
                continue
            member_count += 1
            total_uncompressed += info.file_size
            if member_count > MAX_BUNDLE_MEMBER_COUNT:
                raise UpdateBundleError("Update bundle has too many files.")
            if info.file_size > MAX_BUNDLE_MEMBER_BYTES:
                raise UpdateBundleError(
                    f"Update bundle member {info.filename!r} exceeds size limit."
                )
            if total_uncompressed > MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES:
                raise UpdateBundleError("Update bundle uncompressed size is too large.")

        if zf.testzip() is not None:
            raise UpdateBundleError("Update bundle integrity check failed.")
        if "channelwatch-bundle.json" not in names:
            raise UpdateBundleError(
                "Update bundle is missing channelwatch-bundle.json."
            )
        try:
            metadata = json.loads(zf.read("channelwatch-bundle.json").decode("utf-8"))
        except Exception as exc:
            raise UpdateBundleError("Update bundle metadata is invalid.") from exc
        if not isinstance(metadata, dict):
            raise UpdateBundleError("Update bundle metadata must be an object.")

        version = str(metadata.get("version") or "").lstrip("v")
        if version != expected_version:
            raise UpdateBundleError("Update bundle version does not match manifest.")
        if metadata.get("runtime_abi") != expected_runtime_abi:
            raise UpdateBundleError("Update bundle runtime ABI is not compatible.")
        if (
            int(metadata.get("settings_schema_version") or 0)
            != expected_settings_schema_version
        ):
            raise UpdateBundleError(
                "Update bundle schema version does not match manifest."
            )
        if "core/main.py" not in names or "ui/backend/main.py" not in names:
            raise UpdateBundleError(
                "Update bundle is missing required app entrypoints."
            )
        return metadata


def extract_bundle_archive(bundle_bytes: bytes, destination: Path) -> None:
    destination = destination.resolve()
    temp_destination = destination.with_name(
        f".{destination.name}.tmp-{uuid.uuid4().hex}"
    )
    if temp_destination.exists():
        shutil.rmtree(temp_destination)
    temp_destination.mkdir(parents=True, exist_ok=False)

    try:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes), "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                rel = Path(PurePosixPath(info.filename).as_posix())
                dest = (temp_destination / rel).resolve()
                if not is_path_within(dest, temp_destination):
                    raise UpdateBundleError("Update bundle extraction escaped target.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(dest, zf.read(info.filename))
                if os.name != "nt":
                    dest.chmod(0o640)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temp_destination, destination)
    except Exception:
        shutil.rmtree(temp_destination, ignore_errors=True)
        raise


@dataclass
class RuntimeSelection:
    app_dir: Path
    source: str
    active: dict[str, Any] | None = None
    reason: str | None = None


def runtime_dir_for_config(config_dir: Path) -> Path:
    return config_dir / "channelwatch-runtime"


def resolve_active_app_dir(
    *,
    config_dir: Path,
    image_app_dir: Path = DEFAULT_IMAGE_APP_DIR,
    image_version: str,
    runtime_abi: str = RUNTIME_ABI,
    settings_schema_version: int,
    read_only: bool | None = None,
) -> RuntimeSelection:
    runtime_dir = runtime_dir_for_config(config_dir)
    active_path = runtime_dir / "active.json"
    active = load_json(active_path, None)
    if not isinstance(active, dict) or not active.get("path"):
        return RuntimeSelection(image_app_dir, "image", reason="no-active-bundle")

    version = str(active.get("version") or "").strip().lstrip("v")
    bundle_path = Path(str(active.get("path"))).expanduser()
    if not bundle_path.is_absolute():
        bundle_path = runtime_dir / "releases" / bundle_path
    bundle_path = bundle_path.resolve()
    releases_root = (runtime_dir / "releases").resolve()

    reason = ""
    try:
        if not is_path_within(bundle_path, releases_root):
            reason = "active-bundle-path-escapes-runtime"
        elif not bundle_path.is_dir():
            reason = "active-bundle-missing"
        elif str(active.get("runtime_abi") or "") != runtime_abi:
            reason = "active-bundle-abi-mismatch"
        elif int(active.get("settings_schema_version") or 0) != settings_schema_version:
            reason = "active-bundle-schema-mismatch"
        elif compare_versions(version, image_version) <= 0:
            reason = "image-version-is-current-or-newer"
        elif not (bundle_path / "core" / "main.py").is_file():
            reason = "active-bundle-core-missing"
        elif not (bundle_path / "ui" / "backend" / "main.py").is_file():
            reason = "active-bundle-ui-missing"
    except Exception:
        reason = "active-bundle-metadata-invalid"

    if reason:
        effective_read_only = (
            os.getenv("CHANNELWATCH_CONFIG_READ_ONLY") == "1"
            if read_only is None
            else read_only
        )
        if effective_read_only:
            raise UpdateCenterError(
                "The active runtime selection requires writable reconciliation: "
                f"{reason}."
            )
        status_path = runtime_dir / "startup-status.json"
        atomic_write_json(
            status_path,
            {
                "selected_source": "image",
                "reason": reason,
                "active_version": version or None,
                "image_version": image_version,
                "checked_at": utc_now(),
            },
        )
        if reason == "image-version-is-current-or-newer":
            atomic_write_json(
                runtime_dir / "deactivated-active.json",
                {
                    **active,
                    "deactivated_at": utc_now(),
                    "deactivated_reason": reason,
                },
            )
            try:
                active_path.unlink()
            except FileNotFoundError:
                pass
        return RuntimeSelection(image_app_dir, "image", active=active, reason=reason)

    return RuntimeSelection(
        bundle_path, "bundle", active=active, reason="active-compatible"
    )


def is_process_running(pid: int) -> bool:
    """Return whether ``pid`` still refers to a live process."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def get_process_identity(pid: int) -> str | None:
    """Return a Linux process-generation identity that survives PID reuse checks."""

    if pid <= 0:
        return None
    try:
        boot_id = (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        )
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        # The command name is parenthesized and can contain spaces. Splitting
        # after its final ')' makes index 19 the kernel starttime field (22).
        start_time = stat_text.rsplit(")", 1)[1].split()[19]
        namespace_inode = os.stat(f"/proc/{pid}/ns/pid").st_ino
    except (OSError, IndexError, ValueError):
        return None
    return f"{boot_id}:{namespace_inode}:{start_time}"


class UpdateOperationLock:
    def __init__(self, lock_path: Path, *, wait_timeout: float = 0.0):
        self.lock_path = lock_path
        self.wait_timeout = max(0.0, float(wait_timeout))
        self._fd: int | None = None
        self._inode: int | None = None

    def __enter__(self) -> UpdateOperationLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.wait_timeout
        while True:
            self._discard_stale_lock()
            try:
                self._fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                break
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise UpdateLockedError(
                        "Another update operation is already running."
                    ) from exc
                time.sleep(0.02)
        self._inode = os.fstat(self._fd).st_ino
        payload = {
            "pid": os.getpid(),
            "process_identity": get_process_identity(os.getpid()),
            "created_at": utc_now(),
        }
        os.write(self._fd, json.dumps(payload).encode("utf-8"))
        os.fsync(self._fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            if self._inode is not None and self.lock_path.stat().st_ino == self._inode:
                self.lock_path.unlink()
        except (FileNotFoundError, OSError):
            pass
        finally:
            self._inode = None

    def _discard_stale_lock(self) -> None:
        try:
            lock_stat = self.lock_path.stat()
            lock_age = max(0.0, time.time() - lock_stat.st_mtime)
        except FileNotFoundError:
            return
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("lock payload is not an object")
            owner_pid = int(payload.get("pid") or 0)
            owner_identity = str(payload.get("process_identity") or "")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            owner_pid = 0
            owner_identity = ""

        owner_running = is_process_running(owner_pid) if owner_pid > 0 else False
        if owner_identity and owner_running:
            current_identity = get_process_identity(owner_pid)
            if current_identity == owner_identity:
                # A matching boot/namespace/start-time identity proves this is
                # the same owner even if the operation exceeds the age limit.
                return
            if current_identity is None and lock_age < LOCK_STALE_SECONDS:
                # /proc may be temporarily unavailable. Keep a fresh live lock
                # rather than risk starting a concurrent update operation.
                return
            # A different identity proves that the PID was reused.
        elif not owner_identity:
            if owner_running and lock_age < LOCK_STALE_SECONDS:
                # Legacy lock files have no generation identity. Retain their
                # historical bounded-age behavior.
                return
            if owner_pid <= 0 and lock_age < LOCK_STALE_SECONDS:
                # A second process may observe the lock between O_EXCL create
                # and the owner's payload write. Never delete that fresh file.
                return

        try:
            if self.lock_path.stat().st_ino == lock_stat.st_ino:
                self.lock_path.unlink()
        except (FileNotFoundError, OSError):
            pass


class UpdateManager:
    def __init__(
        self,
        *,
        config_dir: Path,
        current_version: str,
        runtime_abi: str = RUNTIME_ABI,
        settings_schema_version: int = 7,
        public_keys: dict[str, str] | None = None,
        manifest_url: str = DEFAULT_UPDATE_CATALOG_URL,
        image_version: str | None = None,
        launcher_protocol: int | None = None,
        fetcher: Callable[[str, int], bytes] | None = None,
        backup_callable: Callable[[Path], bytes] | None = None,
        restart_callable: Callable[[], bool] | None = None,
        healthcheck_callable: Callable[[], bool] | None = None,
        maintenance_lock: Callable[[], AbstractContextManager[Any]] | None = None,
    ):
        self.config_dir = Path(config_dir)
        self.runtime_dir = runtime_dir_for_config(self.config_dir)
        self.current_version = current_version.strip().lstrip("v")
        self.runtime_abi = runtime_abi
        self.settings_schema_version = int(settings_schema_version)
        self.public_keys = dict(public_keys or UPDATE_PUBLIC_KEYS)
        self.manifest_url = manifest_url
        metadata = resolve_image_metadata(image_app_dir=DEFAULT_IMAGE_APP_DIR)
        configured_image_version = metadata.version if metadata.version != "unknown" else ""
        fallback_image_version = self.current_version
        if not image_version and not configured_image_version:
            # Library/unit-test callers outside a container have no immutable
            # image generation. Treat the current Python runtime as at least
            # the first safe launcher; real images always set the env value.
            try:
                if compare_versions(fallback_image_version, "0.9.10") < 0:
                    fallback_image_version = "0.9.10"
            except ValueError:
                fallback_image_version = "0.9.10"
        self.image_version = (
            str(image_version or configured_image_version or fallback_image_version)
            .strip()
            .lstrip("v")
        )
        if launcher_protocol is None:
            self.launcher_protocol = int(
                launcher_protocol_for_image_version(configured_image_version)
                if configured_image_version
                else LauncherProtocol.RECOVERY_CAPABLE
            )
        else:
            self.launcher_protocol = int(launcher_protocol)
        self.fetcher = fetcher
        self.backup_callable = backup_callable
        self.restart_callable = restart_callable
        self.healthcheck_callable = healthcheck_callable
        self.maintenance_lock = maintenance_lock

    def _maintenance_context(
        self, *, already_held: bool = False
    ) -> AbstractContextManager[Any]:
        if already_held or self.maintenance_lock is None:
            return nullcontext()
        return self.maintenance_lock()

    @property
    def releases_dir(self) -> Path:
        return self.runtime_dir / "releases"

    @property
    def active_path(self) -> Path:
        return self.runtime_dir / "active.json"

    @property
    def latest_path(self) -> Path:
        return self.runtime_dir / "latest.json"

    @property
    def rollback_path(self) -> Path:
        return self.runtime_dir / "rollback.json"

    @property
    def job_path(self) -> Path:
        return self.runtime_dir / "update-job.json"

    @property
    def restart_required_path(self) -> Path:
        return self.runtime_dir / RESTART_REQUIRED_FILE

    @property
    def protocol_three_handoff_path(self) -> Path:
        return self.runtime_dir / PROTOCOL_THREE_HANDOFF_FILE

    def activation_ready_path(self, component: str) -> Path:
        if component not in {"core", "ui"}:
            raise ValueError(f"Unknown runtime component: {component}")
        return self.runtime_dir / f"activation-{component}-ready.json"

    @property
    def activation_pending_path(self) -> Path:
        return self.runtime_dir / "activation-pending.json"

    @property
    def lock_path(self) -> Path:
        return self.runtime_dir / "update.lock"

    @property
    def activation_lock_path(self) -> Path:
        return self.runtime_dir / "activation-state.lock"

    def _activation_state_paths(self) -> tuple[Path, ...]:
        return (
            self.activation_pending_path,
            self.activation_ready_path("core"),
            self.activation_ready_path("ui"),
        )

    def _control_path(self, name: str) -> Path:
        if name not in RESTART_CONTROL_FILES:
            raise UpdateCenterError(f"Unsupported restart control file: {name}.")
        return self.runtime_dir / name

    @contextmanager
    def _restart_journal_lock(self):
        """Serialize journal ownership and replay across image/bundle code."""

        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise UpdateLockedError(
                "Safe runtime transition journal locking is unavailable."
            )
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.runtime_dir / RESTART_JOURNAL_LOCK_FILE
        try:
            fd = os.open(
                lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
        except OSError as exc:
            raise UpdateLockedError(
                "The runtime transition journal lock cannot be opened safely."
            ) from exc
        try:
            metadata = os.fstat(fd)
            if not stat_module.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise UpdateLockedError(
                    "The runtime transition journal lock is not a single-link regular file."
                )
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @contextmanager
    def _activation_outcome_lock(self, *, wait: bool = True):
        """Serialize startup quorum and deadline outcomes across all processes."""

        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise UpdateLockedError("Safe activation outcome locking is unavailable.")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.runtime_dir / ACTIVATION_OUTCOME_LOCK_FILE
        try:
            fd = os.open(
                lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
        except OSError as exc:
            raise UpdateLockedError(
                "The activation outcome lock cannot be opened safely."
            ) from exc
        try:
            metadata = os.fstat(fd)
            if not stat_module.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise UpdateLockedError(
                    "The activation outcome lock is not a single-link regular file."
                )
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(
                    fd,
                    fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB),
                )
            except BlockingIOError as exc:
                raise UpdateLockedError(
                    "Another activation outcome is already being committed; "
                    "pending startup validation or recovery is still in progress."
                ) from exc
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _read_control_state(self) -> dict[str, dict[str, Any] | None]:
        control: dict[str, dict[str, Any] | None] = {}
        for name in RESTART_CONTROL_FILES:
            path = self._control_path(name)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                payload = None
            except (OSError, json.JSONDecodeError) as exc:
                raise UpdateCenterError(
                    f"Restart control file {name} could not be read safely."
                ) from exc
            if payload is not None and not isinstance(payload, dict):
                raise UpdateCenterError(
                    f"Restart control file {name} must contain a JSON object."
                )
            control[name] = payload
        return control

    def _prepare_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": payload.get("job_id") or uuid.uuid4().hex,
            "updated_at": utc_now(),
            **payload,
        }

    def _build_restart_journal(
        self,
        *,
        reason: str,
        operation: str,
        phase: str,
        job_id: str | None,
        source_active: dict[str, Any] | None,
        control: dict[str, dict[str, Any] | None],
    ) -> dict[str, Any]:
        if operation not in RESTART_JOURNAL_OPERATIONS:
            raise UpdateCenterError(f"Unsupported restart operation: {operation}.")
        if phase not in RESTART_JOURNAL_PHASES:
            raise UpdateCenterError(f"Unsupported restart phase: {phase}.")
        if set(control) != set(RESTART_CONTROL_FILES):
            raise UpdateCenterError("Restart journal control mapping is incomplete.")
        if any(
            value is not None and not isinstance(value, dict)
            for value in control.values()
        ):
            raise UpdateCenterError(
                "Restart journal control values must be objects or null."
            )
        return {
            "schema": RESTART_JOURNAL_SCHEMA,
            "reason": reason,
            "operation": operation,
            "phase": phase,
            "job_id": job_id,
            "source_active": source_active,
            "replace_activation_state": True,
            "created_at": utc_now(),
            "control": control,
        }

    def _stage_restart_journal(self, journal: dict[str, Any]) -> Path:
        validated = self._validate_restart_journal(journal)
        staged_path = self.restart_required_path.with_name(
            f".{self.restart_required_path.name}.candidate-{uuid.uuid4().hex}"
        )
        atomic_write_json(
            staged_path,
            validated,
            temp_path=staged_path.with_name(
                f".{staged_path.name}.tmp-{uuid.uuid4().hex}"
            ),
        )
        return staged_path

    def _cleanup_restart_journal_candidates(
        self, *, exclude: Path | None = None
    ) -> None:
        """Remove only regular internal candidates abandoned by a dead writer."""

        removed = False
        pattern = f".{self.restart_required_path.name}.candidate-*"
        for candidate in self.runtime_dir.glob(pattern):
            if exclude is not None and candidate == exclude:
                continue
            try:
                metadata = os.lstat(candidate)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            if not stat_module.S_ISREG(metadata.st_mode):
                continue
            try:
                candidate.unlink()
                removed = True
            except FileNotFoundError:
                pass
        if removed:
            fsync_directory(self.runtime_dir)

    def _load_restart_journal_strict(self) -> dict[str, Any]:
        try:
            payload = _read_runtime_json_strict(
                self.restart_required_path,
                label="runtime transition journal",
            )
        except FileNotFoundError as exc:
            raise UpdateLockedError(
                "The expected runtime transition journal no longer exists."
            ) from exc
        try:
            return self._validate_restart_journal(payload)
        except UpdateCenterError as exc:
            raise UpdateLockedError(
                "The runtime transition journal is not valid."
            ) from exc

    def _require_restart_journal_owner(
        self, expected: dict[str, Any]
    ) -> dict[str, Any]:
        validated_expected = self._validate_restart_journal(expected)
        current = self._load_restart_journal_strict()
        if current != validated_expected:
            raise UpdateLockedError(
                "The runtime transition journal is owned by another generation."
            )
        return current

    def _write_restart_journal(
        self,
        journal: dict[str, Any],
        *,
        expected: dict[str, Any] | None = None,
    ) -> bool:
        """Create a journal without clobbering, or replace its exact owner."""

        validated = self._validate_restart_journal(journal)
        with self._restart_journal_lock():
            self._cleanup_restart_journal_candidates()
            staged_path = self._stage_restart_journal(validated)
            try:
                if expected is None:
                    try:
                        os.lstat(self.protocol_three_handoff_path)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        raise UpdateLockedError(
                            "The protocol-3 handoff marker cannot be inspected."
                        ) from exc
                    else:
                        raise UpdateLockedError(
                            "A protocol-3 restart handoff is still active."
                        )
                    self._restart_transition_checkpoint("journal:before-create")
                    try:
                        os.link(staged_path, self.restart_required_path)
                    except FileExistsError as exc:
                        raise UpdateLockedError(
                            "Another runtime transition journal won publication."
                        ) from exc
                else:
                    self._restart_transition_checkpoint("journal:before-replace")
                    self._require_restart_journal_owner(expected)
                    if self._protocol_three_handoff_matches_locked(expected):
                        return False
                    os.replace(staged_path, self.restart_required_path)
                fsync_directory(self.runtime_dir)
            finally:
                try:
                    staged_path.unlink()
                except FileNotFoundError:
                    pass
                else:
                    fsync_directory(self.runtime_dir)
        self._restart_transition_checkpoint("journal")
        return True

    def _restart_transition_checkpoint(self, phase: str) -> None:
        """No-op fault-injection boundary for restart transaction tests."""

        del phase

    def _restart_journal_present(self) -> bool:
        """Treat every filesystem object at the journal path as fail closed."""

        try:
            os.lstat(self.restart_required_path)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    def _require_restart_journal_absent(self) -> None:
        """Prevent a new writer from replacing an unacknowledged handoff."""

        with self._restart_journal_lock():
            if self._restart_journal_present():
                raise UpdateLockedError(
                    "A runtime transition is waiting for the next container entrypoint."
                )

    def _protocol_three_restart_helper_active(self) -> bool:
        """Return true only while the trusted image-owned helper holds its lock."""

        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            return False
        runtime_dir = Path(
            os.environ.get("CHANNELWATCH_RUNTIME_DIR", "/tmp/channelwatch")
        )
        lock_path = runtime_dir / PROTOCOL_THREE_RESTART_HELPER_LOCK_FILE
        try:
            parent = os.lstat(runtime_dir)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise UpdateLockedError(
                "The restart helper runtime directory cannot be inspected."
            ) from exc
        try:
            fd = os.open(
                lock_path,
                os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            )
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise UpdateLockedError(
                "The restart helper lock cannot be opened safely."
            ) from exc
        try:
            metadata = os.fstat(fd)
            if (
                not stat_module.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or stat_module.S_IMODE(parent.st_mode) != 0o700
                or not stat_module.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
            ):
                raise UpdateLockedError("The restart helper lock is not trusted.")
            try:
                named_before = os.stat(lock_path, follow_symlinks=False)
            except OSError as exc:
                raise UpdateLockedError(
                    "The restart helper lock changed ownership."
                ) from exc
            if (
                named_before.st_dev != metadata.st_dev
                or named_before.st_ino != metadata.st_ino
                or not stat_module.S_ISREG(named_before.st_mode)
                or named_before.st_nlink != 1
                or named_before.st_uid != os.geteuid()
            ):
                raise UpdateLockedError("The restart helper lock changed ownership.")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                try:
                    named = os.stat(lock_path, follow_symlinks=False)
                except OSError as exc:
                    raise UpdateLockedError(
                        "The restart helper lock changed ownership."
                    ) from exc
                if (
                    named.st_dev != metadata.st_dev
                    or named.st_ino != metadata.st_ino
                    or not stat_module.S_ISREG(named.st_mode)
                    or named.st_nlink != 1
                    or named.st_uid != os.geteuid()
                ):
                    raise UpdateLockedError(
                        "The restart helper lock changed ownership."
                    )
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
        finally:
            os.close(fd)

    def _protocol_three_handoff_result(
        self,
        expected_journal: dict[str, Any],
        *,
        wait_for_active_helper: bool = False,
    ) -> dict[str, Any] | None:
        """Reconcile a durable result, optionally waiting for the active helper."""

        result = self._protocol_three_handoff_result_once(expected_journal)
        if result is not None or not wait_for_active_helper:
            return result
        started_at = time.monotonic()
        grace_deadline = started_at + PROTOCOL_THREE_RECONCILE_GRACE_SECONDS
        deadline = started_at + PROTOCOL_THREE_RECONCILE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if (
                time.monotonic() >= grace_deadline
                and not self._protocol_three_restart_helper_active()
            ):
                # The helper can release its operation lock immediately after
                # publishing the marker while a replacement child is consuming
                # the journal under the owner lock. Perform one final blocking
                # exact-state reconciliation before considering an abort.
                return self._protocol_three_handoff_result_once(expected_journal)
            _protocol_three_sleep(PROTOCOL_THREE_RECONCILE_INTERVAL_SECONDS)
            result = self._protocol_three_handoff_result_once(expected_journal)
            if result is not None:
                return result
        return self._protocol_three_handoff_result_once(expected_journal)

    def _protocol_three_handoff_result_once(
        self,
        expected_journal: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the exact durable outcome after a lost restart reply.

        The authenticated protocol-3 helper can accept a transition and restart
        both children even if the initiating process disappears before receiving
        its pipe acknowledgement. A replacement child then consumes the journal.
        The replacement can also fail activation, complete the journaled rollback,
        and record its terminal failed job before the initiating process resumes.
        Never replace either durable generation with a stale abort.
        """

        if self.launcher_protocol != int(LauncherProtocol.RECOVERY_CAPABLE):
            return None
        validated = self._validate_restart_journal(expected_journal)
        with self._restart_journal_lock():
            if self._restart_journal_present():
                current_journal = self._load_restart_journal_strict()
                if current_journal != validated:
                    return None
                if not self._protocol_three_handoff_matches_locked(validated):
                    return None
            current = self._read_control_state()
            expected = validated["control"]

            expected_job = expected["update-job.json"]
            current_job = current["update-job.json"]
            if not isinstance(expected_job, dict) or not isinstance(current_job, dict):
                return None
            expected_job_id = str(expected_job.get("job_id") or "")
            if not expected_job_id or str(current_job.get("job_id") or "") != (
                expected_job_id
            ):
                return None
            expected_operation = str(expected_job.get("operation") or "")
            if (
                not expected_operation
                or str(current_job.get("operation") or "") != expected_operation
            ):
                return None

            expected_pending = expected["activation-pending.json"]
            current_pending = current["activation-pending.json"]
            if (
                current["active.json"] == expected["active.json"]
                and current["rollback.json"] == expected["rollback.json"]
            ):
                if expected_pending is None and current_pending is None:
                    return current_job
                if current_pending == expected_pending:
                    return current_job
                if current_pending is None and current_job.get("status") == "success":
                    return current_job

            # An apply can reach activation, fail, and finish its separately
            # journaled rollback before a lost callback reply is reconciled.
            # Bind that terminal state to the original generation and digest.
            expected_active = expected["active.json"]
            if (
                validated.get("operation") == "apply"
                and validated.get("phase") == "commit"
                and current["active.json"] == validated.get("source_active")
                and current["rollback.json"] == expected["rollback.json"]
                and current_pending is None
                and current["activation-core-ready.json"] is None
                and current["activation-ui-ready.json"] is None
                and current_job.get("status") == "failed"
                and current_job.get("rollback_applied") is True
                and isinstance(expected_active, dict)
                and str(current_job.get("rolled_back_from") or "").lstrip("v")
                == str(expected_active.get("version") or "").lstrip("v")
            ):
                expected_digest = str(expected_job.get("bundle_sha256") or "")
                current_digest = str(current_job.get("bundle_sha256") or "")
                if expected_digest and current_digest != expected_digest:
                    return None
                return current_job
            return None

    def _protocol_three_handoff_matches_locked(
        self,
        expected_journal: dict[str, Any],
    ) -> bool:
        """Validate an exact post-barrier marker while the journal lock is held."""

        if self.launcher_protocol != int(LauncherProtocol.RECOVERY_CAPABLE):
            return False
        expected = self._validate_restart_journal(expected_journal)
        path = self.protocol_three_handoff_path
        try:
            payload = _read_runtime_json_strict(
                path,
                label="protocol-3 handoff marker",
            )
        except FileNotFoundError:
            return False
        if (
            not isinstance(payload, dict)
            or set(payload) != PROTOCOL_THREE_HANDOFF_FIELDS
            or payload.get("schema") != PROTOCOL_THREE_HANDOFF_SCHEMA
        ):
            raise UpdateLockedError("The protocol-3 handoff marker is invalid.")
        try:
            marker_journal = self._validate_restart_journal(payload.get("journal"))
        except UpdateCenterError as exc:
            raise UpdateLockedError(
                "The protocol-3 handoff marker contains an invalid journal."
            ) from exc
        old_processes = payload.get("old_processes")
        if not isinstance(old_processes, dict) or set(old_processes) != (
            PROTOCOL_THREE_PROCESS_NAMES
        ):
            raise UpdateLockedError(
                "The protocol-3 handoff marker contains invalid process identities."
            )
        for identity in old_processes.values():
            if (
                not isinstance(identity, dict)
                or set(identity) != PROTOCOL_THREE_PROCESS_IDENTITY_FIELDS
                or isinstance(identity.get("pid"), bool)
                or not isinstance(identity.get("pid"), int)
                or identity["pid"] <= 0
                or isinstance(identity.get("start"), bool)
                or not isinstance(identity.get("start"), int)
                or identity["start"] < 0
            ):
                raise UpdateLockedError(
                    "The protocol-3 handoff marker contains invalid process identities."
                )
        if marker_journal != expected:
            raise UpdateLockedError(
                "The protocol-3 handoff marker belongs to another generation."
            )
        return True

    @staticmethod
    def _validate_restart_journal(journal: Any) -> dict[str, Any]:
        if (
            not isinstance(journal, dict)
            or journal.get("schema") != RESTART_JOURNAL_SCHEMA
        ):
            raise UpdateCenterError("Restart journal schema is invalid.")
        if set(journal) != RESTART_JOURNAL_FIELDS:
            raise UpdateCenterError("Restart journal fields are invalid.")
        if journal.get("operation") not in RESTART_JOURNAL_OPERATIONS:
            raise UpdateCenterError("Restart journal operation is invalid.")
        if journal.get("phase") not in RESTART_JOURNAL_PHASES:
            raise UpdateCenterError("Restart journal phase is invalid.")
        expected_reason = (
            "activation_rollback"
            if journal.get("operation") == "activation_rollback"
            else "runtime_transition"
        )
        if journal.get("reason") != expected_reason:
            raise UpdateCenterError("Restart journal reason is invalid.")
        if journal.get("job_id") is not None and not isinstance(
            journal.get("job_id"), str
        ):
            raise UpdateCenterError("Restart journal job identity is invalid.")
        if not isinstance(journal.get("created_at"), str) or not journal.get(
            "created_at"
        ):
            raise UpdateCenterError("Restart journal timestamp is invalid.")
        if journal.get("replace_activation_state") is not True:
            raise UpdateCenterError("Restart journal replacement policy is invalid.")
        source_active = journal.get("source_active")
        if source_active is not None and not isinstance(source_active, dict):
            raise UpdateCenterError("Restart journal source active state is invalid.")
        control = journal.get("control")
        if not isinstance(control, dict) or set(control) != set(RESTART_CONTROL_FILES):
            raise UpdateCenterError("Restart journal control mapping is invalid.")
        if any(
            value is not None and not isinstance(value, dict)
            for value in control.values()
        ):
            raise UpdateCenterError("Restart journal control values are invalid.")
        return journal

    def apply_restart_journal(
        self, journal: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Idempotently publish the exact control state in a schema-2 journal."""

        with self._restart_journal_lock():
            if journal is None:
                journal = self._load_restart_journal_strict()
            validated = self._validate_restart_journal(journal)
            self._require_restart_journal_owner(validated)
            control = validated["control"]

            # Claimant-specific files are part of activation state even though
            # the canonical mapping names only public files. The journal stays
            # authoritative throughout replay.
            for path in self.runtime_dir.glob("activation-*.json"):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            self._restart_transition_checkpoint("activation-state-removed")

            ordered_names = tuple(
                name for name in RESTART_CONTROL_FILES if name != "active.json"
            ) + ("active.json",)
            for name in ordered_names:
                self._require_restart_journal_owner(validated)
                path = self._control_path(name)
                payload = control[name]
                if payload is None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    atomic_write_json(
                        path,
                        payload,
                        temp_path=path.with_name(
                            f".{path.name}.tmp-{uuid.uuid4().hex}"
                        ),
                    )
                self._restart_transition_checkpoint(f"control:{name}")
            fsync_directory(self.runtime_dir)
            self._restart_transition_checkpoint("control:fsynced")
            return validated

    def _clear_restart_journal(self, expected: dict[str, Any]) -> None:
        with self._restart_journal_lock():
            self._restart_transition_checkpoint("journal:before-clear")
            self._require_restart_journal_owner(expected)
            try:
                self.restart_required_path.unlink()
            except FileNotFoundError:
                raise UpdateLockedError(
                    "The runtime transition journal changed before acknowledgement."
                )
            fsync_directory(self.runtime_dir)
        self._restart_transition_checkpoint("journal:cleared")

    @staticmethod
    def _snapshot_files(paths: tuple[Path, ...]) -> dict[Path, bytes | None]:
        snapshot: dict[Path, bytes | None] = {}
        for path in paths:
            try:
                snapshot[path] = path.read_bytes()
            except FileNotFoundError:
                snapshot[path] = None
        return snapshot

    @staticmethod
    def _restore_files(snapshot: dict[Path, bytes | None]) -> None:
        # Restore selected runtime state before deleting any state that was
        # absent. A crash during recovery therefore always leaves a usable
        # selection rather than a half-selected new activation.
        for path, content in snapshot.items():
            if content is not None:
                atomic_write_bytes(path, content)
        for path, content in snapshot.items():
            if content is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def _clear_activation_state(self) -> None:
        paths = set(self._activation_state_paths())
        paths.update(self.runtime_dir.glob("activation-*.json"))
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _pending_matches_active(
        self, pending: dict[str, Any], active: dict[str, Any]
    ) -> bool:
        return all(
            str(pending.get(key) or "") == str(active.get(key) or "")
            for key in ("activation_id", "version", "path")
        ) and bool(str(active.get("activation_id") or ""))

    def _claim_pending_activation(
        self, pending: dict[str, Any], *, claimant: str
    ) -> Path | None:
        activation_id = str(pending.get("activation_id") or "unknown")
        claim_path = self.runtime_dir / f"activation-{claimant}-{activation_id}.json"
        if not self.activation_pending_path.is_file():
            return None
        try:
            os.replace(self.activation_pending_path, claim_path)
        except FileNotFoundError:
            return None
        fsync_directory(self.runtime_dir)
        claimed = load_json(claim_path, None)
        if not isinstance(claimed, dict) or any(
            str(claimed.get(key) or "") != str(pending.get(key) or "")
            for key in ("activation_id", "version", "path")
        ):
            # The caller lost a race to a different activation generation.
            # Preserve the unexpected claim for forensic recovery.
            return None
        return claim_path

    def _recover_pending_activation(self, active: dict[str, Any]) -> bool:
        """Restore a durable pending generation after an interrupted claim."""

        pending = load_json(self.activation_pending_path, None)
        if isinstance(pending, dict) and self._pending_matches_active(pending, active):
            return True

        candidates = list(
            path
            for path in sorted(self.runtime_dir.glob("activation-*.json"))
            if path.name
            not in {
                self.activation_pending_path.name,
                self.activation_ready_path("core").name,
                self.activation_ready_path("ui").name,
            }
        )
        for candidate_path in candidates:
            candidate = load_json(candidate_path, None)
            if not isinstance(candidate, dict):
                continue
            if not self._pending_matches_active(candidate, active):
                continue
            try:
                os.link(candidate_path, self.activation_pending_path)
            except (FileExistsError, FileNotFoundError):
                recovered = load_json(self.activation_pending_path, None)
                return bool(
                    isinstance(recovered, dict)
                    and self._pending_matches_active(recovered, active)
                )
            except OSError:
                return False
            try:
                candidate_path.unlink()
            except FileNotFoundError:
                pass
            fsync_directory(self.runtime_dir)
            recovered = load_json(self.activation_pending_path, None)
            return bool(
                isinstance(recovered, dict)
                and self._pending_matches_active(recovered, active)
            )
        return False

    def _activation_in_flight(self, active: Any) -> bool:
        """Return true while the selected generation has pending or claimed work.

        A claimant-specific file is the durable replacement for canonical
        pending state between an atomic claim and success/rollback completion.
        Treat it as in-flight even if recovery cannot publish a hard link, so a
        concurrent apply can never interpret that intentional gap as an idle
        update center.
        """

        if not isinstance(active, dict):
            return False
        self._recover_pending_activation(active)
        pending = load_json(self.activation_pending_path, None)
        if isinstance(pending, dict) and self._pending_matches_active(pending, active):
            return True

        return bool(self._matching_activation_claims(active))

    def _matching_activation_claims(self, active: Any) -> list[Path]:
        """Return claimant records that still own the selected generation."""

        if not isinstance(active, dict):
            return []
        excluded = {
            self.activation_pending_path.name,
            self.activation_ready_path("core").name,
            self.activation_ready_path("ui").name,
        }
        matches: list[Path] = []
        for claim_path in self.runtime_dir.glob("activation-*.json"):
            if claim_path.name in excluded:
                continue
            claim = load_json(claim_path, None)
            if isinstance(claim, dict) and self._pending_matches_active(claim, active):
                matches.append(claim_path)
        return matches

    def _ensure_runtime(self) -> None:
        self.releases_dir.mkdir(parents=True, exist_ok=True)

    def _write_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_runtime()
        job = self._prepare_job(payload)
        atomic_write_json(self.job_path, job)
        return job

    def _read_manifest_from_url(
        self, url: str | None = None, *, recovery: bool = False
    ) -> dict[str, Any]:
        target = url or self.manifest_url
        data = (
            self.fetcher(target, MAX_MANIFEST_BYTES)
            if self.fetcher
            else fetch_bytes(target, max_bytes=MAX_MANIFEST_BYTES)
        )
        return read_update_document_bytes(
            data,
            self.public_keys,
            current_version=self.current_version,
            runtime_abi=self.runtime_abi,
            settings_schema_version=self.settings_schema_version,
            launcher_protocol=self.launcher_protocol,
            recovery=recovery,
        )

    def _fetch_bundle(self, url: str) -> bytes:
        return (
            self.fetcher(url, MAX_BUNDLE_BYTES)
            if self.fetcher
            else fetch_bytes(url, max_bytes=MAX_BUNDLE_BYTES)
        )

    def _payload_requires_image(self, payload: dict[str, Any]) -> bool:
        if bool(payload.get("image_required", False)):
            return True
        try:
            if (
                compare_versions(
                    self.image_version,
                    str(payload.get("minimum_image_version") or "0.0.0"),
                )
                < 0
            ):
                return True
        except ValueError:
            return True
        if int(payload.get("minimum_launcher_protocol") or 0) > int(
            self.launcher_protocol
        ):
            return True
        if payload.get("runtime_abi") != self.runtime_abi:
            return True
        return (
            int(payload.get("settings_schema_version") or 0)
            != self.settings_schema_version
        )

    def _runtime_source(self, active: Any) -> str:
        """Report a bundle runtime only when its durable selection is valid."""

        if not isinstance(active, dict) or not active.get("path"):
            return "image"
        try:
            version = str(active.get("version") or "").strip().lstrip("v")
            bundle_path = Path(str(active["path"])).expanduser()
            if not bundle_path.is_absolute():
                bundle_path = self.releases_dir / bundle_path
            bundle_path = bundle_path.resolve()
            releases_root = self.releases_dir.resolve()
            valid = (
                is_path_within(bundle_path, releases_root)
                and bundle_path.is_dir()
                and str(active.get("runtime_abi") or "") == self.runtime_abi
                and int(active.get("settings_schema_version") or 0)
                == self.settings_schema_version
                and compare_versions(version, self.current_version) == 0
                and compare_versions(version, self.image_version) > 0
                and (bundle_path / "core" / "main.py").is_file()
                and (bundle_path / "ui" / "backend" / "main.py").is_file()
            )
        except (OSError, TypeError, ValueError):
            valid = False
        return "app_bundle" if valid else "image"

    def _operation_lock_active(self) -> bool:
        """Return whether a live update operation owns the single-flight lock.

        The lock implementation already knows how to reject a dead or reused
        PID.  Reusing that stale-owner cleanup here keeps status reporting from
        presenting an abandoned lock as an active operation.
        """

        lock = UpdateOperationLock(self.lock_path)
        lock._discard_stale_lock()
        try:
            metadata = self.lock_path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            # An uninspectable lock must fail closed.  Starting a second update
            # would be less safe than temporarily reporting the operation busy.
            return True
        return not (
            stat_module.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
        ) or self.lock_path.exists()

    @staticmethod
    def _job_operation_state(
        job: Any, *, lock_active: bool, transition_pending: bool
    ) -> str:
        """Translate durable update records into the public operation state."""

        record = job if isinstance(job, dict) else {}
        status = str(record.get("status") or "").strip().lower()
        operation = str(record.get("operation") or "").strip().lower()

        if transition_pending:
            if operation == "rollback":
                return "rolling_back"
            if status == "validating":
                return "validating"
            return "restarting"

        if lock_active:
            if operation == "rollback":
                return "rolling_back"
            return {
                "backing_up": "backing_up",
                "verifying": "downloading",
                "applying": "applying",
                "restarting": "restarting",
                "validating": "validating",
            }.get(status, "checking")

        if status == "failed":
            return "failed"
        return "idle"

    def _catalog_checked_at(self) -> str | None:
        try:
            checked_at = datetime.fromtimestamp(
                self.latest_path.stat().st_mtime, tz=timezone.utc
            )
        except (FileNotFoundError, OSError, OverflowError, ValueError):
            return None
        return checked_at.isoformat().replace("+00:00", "Z")

    def status(self) -> dict[str, Any]:
        self._ensure_runtime()
        active = load_json(self.active_path, None)
        latest = load_json(self.latest_path, None)
        job = load_json(self.job_path, None)
        rollback = load_json(self.rollback_path, None)
        payload = latest.get("payload") if isinstance(latest, dict) else None
        catalog_checked_at = self._catalog_checked_at()
        catalog_state = "not_checked" if latest is None else "error"
        cached_release_stale = False
        trusted_target: dict[str, Any] | None = None
        visible_latest: dict[str, Any] | None = None
        update_available = False
        image_required = False
        if isinstance(payload, dict):
            try:
                comparison = compare_versions(
                    str(payload.get("version") or "0.0.0"), self.current_version
                )
                if comparison < 0:
                    cached_release_stale = True
                    catalog_state = "stale_cache"
                elif comparison == 0:
                    visible_latest = payload
                    catalog_state = "current"
                else:
                    visible_latest = payload
                    trusted_target = payload
                    update_available = True
                    image_required = self._payload_requires_image(payload)
                    catalog_state = "update_available"
            except Exception:
                catalog_state = "error"

        lock_active = self._operation_lock_active()
        transition_pending = self.runtime_transition_pending()
        operation_state = self._job_operation_state(
            job,
            lock_active=lock_active,
            transition_pending=transition_pending,
        )
        if operation_state == "checking":
            catalog_state = "checking"
        operation_busy = operation_state in {
            "checking",
            "downloading",
            "backing_up",
            "applying",
            "restarting",
            "validating",
            "rolling_back",
        }
        return {
            "current_version": self.current_version,
            "image_version": self.image_version,
            "launcher_protocol": self.launcher_protocol,
            "runtime_abi": self.runtime_abi,
            "settings_schema_version": self.settings_schema_version,
            "runtime_source": self._runtime_source(active),
            "active_bundle": (
                active if isinstance(active, dict) and active.get("path") else None
            ),
            # ``latest`` remains for v1 API compatibility, but it is now a
            # safe visible release rather than an arbitrary stale cache row.
            "latest": visible_latest,
            "trusted_target": trusted_target,
            "update_available": update_available,
            "catalog_state": catalog_state,
            "catalog_checked_at": catalog_checked_at,
            "cached_release_stale": cached_release_stale,
            "operation_state": operation_state,
            "operation_busy": operation_busy,
            "image_required": image_required if update_available else False,
            "delivery_mode": (
                str(
                    visible_latest.get("delivery_mode")
                    or DeliveryMode.APP_UPDATE.value
                )
                if isinstance(visible_latest, dict)
                else None
            ),
            "image_refresh_recommended": bool(
                isinstance(visible_latest, dict)
                and visible_latest.get("image_refresh_recommended")
            ),
            "recommended_image_version": (
                visible_latest.get("recommended_image_version")
                if isinstance(visible_latest, dict)
                else None
            ),
            "last_job": job if isinstance(job, dict) else None,
            "rollback_available": (
                isinstance(active, dict)
                and bool(active.get("path"))
                and isinstance(rollback, dict)
                and "previous_active" in rollback
            ),
            "auth_disabled_warning": os.environ.get("CW_DISABLE_AUTH", "").lower()
            == "true",
        }

    def runtime_transition_pending(self) -> bool:
        """Return true while apply/rollback handoff or activation is unresolved."""

        if self._restart_journal_present():
            return True
        active = load_json(self.active_path, None)
        return self._activation_in_flight(active)

    def check(
        self, *, recovery: bool = False, maintenance_lock_held: bool = False
    ) -> dict[str, Any]:
        with (
            self._maintenance_context(already_held=maintenance_lock_held),
            UpdateOperationLock(self.lock_path),
        ):
            # Avoid a needless network request when a handoff is already known,
            # then repeat the check while holding the cross-version journal lock
            # before changing either canonical update control file.
            self._require_restart_journal_absent()
            manifest = self._read_manifest_from_url(recovery=recovery)
            payload = manifest["payload"]
            update_available = (
                compare_versions(payload["version"], self.current_version) > 0
            )
            image_required = self._payload_requires_image(payload)
            with self._restart_journal_lock():
                if self._restart_journal_present():
                    raise UpdateLockedError(
                        "A runtime transition is waiting for the next container entrypoint."
                    )
                self._ensure_runtime()
                atomic_write_json(self.latest_path, manifest)
                job = self._write_job(
                    {
                        "operation": "check",
                        "recovery": recovery,
                        "status": (
                            "image_required"
                            if image_required and update_available
                            else "available" if update_available else "current"
                        ),
                        "version": payload["version"],
                        "message": (
                            "Container image update required."
                            if image_required and update_available
                            else "Update check completed."
                        ),
                    }
                )
                # The check owns the lock until this return value has been
                # built.  Its work is nevertheless complete, so do not send a
                # transient busy state back to the tab that initiated it.
                return {
                    **self.status(),
                    "catalog_state": (
                        "update_available" if update_available else "current"
                    ),
                    "operation_state": "idle",
                    "operation_busy": False,
                    "last_job": job,
                }

    def _verify_bundle_signature(
        self, payload: dict[str, Any], bundle_bytes: bytes
    ) -> None:
        expected_hash = str(payload.get("bundle_sha256") or "").lower()
        actual_hash = sha256_hex(bundle_bytes)
        if not expected_hash or actual_hash != expected_hash:
            raise UpdateBundleError("Update bundle hash did not match manifest.")
        signature_b64 = str(payload.get("bundle_signature") or "")
        if not signature_b64:
            raise UpdateBundleError("Update bundle signature is missing.")
        key_id = str(payload.get("key_id") or "")
        if not key_id:
            manifest_sig = load_json(self.latest_path, {}).get("signature", {})
            if isinstance(manifest_sig, dict):
                key_id = str(manifest_sig.get("key_id") or "")
        verify_ed25519_signature(
            self.public_keys, key_id, signature_b64, bytes.fromhex(actual_hash)
        )

    def apply(
        self,
        version: str | None = None,
        *,
        recovery: bool = False,
        maintenance_lock_held: bool = False,
        job_id: str | None = None,
        scheduler_attempt_id: str | None = None,
        expected_bundle_sha256: str | None = None,
    ) -> dict[str, Any]:
        job_id = job_id or uuid.uuid4().hex
        with (
            self._maintenance_context(already_held=maintenance_lock_held),
            self._activation_outcome_lock(wait=False),
            UpdateOperationLock(self.lock_path),
            UpdateOperationLock(self.activation_lock_path),
        ):
            self._require_restart_journal_absent()
            selected_active = load_json(self.active_path, None)
            if self._activation_in_flight(selected_active):
                raise UpdateLockedError(
                    "The previous update is still waiting for startup validation."
                )
            cached_latest = load_json(self.latest_path, None)
            cached_payload = (
                cached_latest.get("payload")
                if isinstance(cached_latest, dict)
                else None
            )
            # Every apply re-fetches and verifies the authoritative feed. This
            # prevents a stale v0.9.17 latest.json, a changed catalog, or an
            # incompatible feed projection from being applied after v0.9.18
            # activation. The selected version and digest are bound together.
            latest = self._read_manifest_from_url(recovery=recovery)
            payload = latest["payload"]
            if isinstance(cached_payload, dict):
                try:
                    cached_is_newer = (
                        compare_versions(
                            str(cached_payload.get("version") or "0.0.0"),
                            self.current_version,
                        )
                        > 0
                    )
                except ValueError:
                    cached_is_newer = False
                cached_version = str(cached_payload.get("version") or "").lstrip("v")
                cached_digest = str(cached_payload.get("bundle_sha256") or "").lower()
                fresh_version = str(payload.get("version") or "").lstrip("v")
                fresh_digest = str(payload.get("bundle_sha256") or "").lower()
                requested_matches_cached = not version or (
                    version.strip().lstrip("v") == cached_version
                )
                if (
                    cached_is_newer
                    and requested_matches_cached
                    and (cached_version, cached_digest) != (fresh_version, fresh_digest)
                ):
                    atomic_write_json(self.latest_path, latest)
                    raise UpdateManifestError(
                        "The signed update catalog changed after the previous check. "
                        "Review the newly selected release before applying it."
                    )
            atomic_write_json(self.latest_path, latest)
            target_version = str(payload["version"]).lstrip("v")
            bundle_sha256 = str(payload.get("bundle_sha256") or "").strip().lower()
            if expected_bundle_sha256 is not None and (
                expected_bundle_sha256.strip().lower() != bundle_sha256
            ):
                raise UpdateManifestError(
                    "The signed update digest changed after scheduling. Review the "
                    "new release before applying it."
                )
            job_identity = {
                "scheduler_attempt_id": scheduler_attempt_id,
                "bundle_sha256": bundle_sha256,
            }
            if version and version.strip().lstrip("v") != target_version:
                raise UpdateManifestError(
                    "Requested update version does not match the latest trusted manifest."
                )
            if compare_versions(target_version, self.current_version) <= 0:
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "current",
                        "version": target_version,
                        **job_identity,
                        "message": "ChannelWatch is already current.",
                    }
                )
            if payload.get("image_required"):
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "image_required",
                        "version": target_version,
                        **job_identity,
                        "message": "This release requires a new container image.",
                    }
                )
            try:
                image_too_old = (
                    compare_versions(
                        self.image_version,
                        str(payload.get("minimum_image_version") or "0.0.0"),
                    )
                    < 0
                )
            except ValueError:
                image_too_old = True
            launcher_too_old = int(payload.get("minimum_launcher_protocol") or 0) > int(
                self.launcher_protocol
            )
            if image_too_old or launcher_too_old:
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "image_required",
                        "version": target_version,
                        **job_identity,
                        "message": (
                            "This release requires a newer ChannelWatch container "
                            "launcher before it can be installed in-app."
                        ),
                    }
                )
            if payload.get("runtime_abi") != self.runtime_abi:
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "image_required",
                        "version": target_version,
                        **job_identity,
                        "message": "This release requires a compatible runtime image.",
                    }
                )
            if (
                int(payload.get("settings_schema_version") or 0)
                != self.settings_schema_version
            ):
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "image_required",
                        "version": target_version,
                        **job_identity,
                        "message": "This release changes persistent settings schema and needs a new image update.",
                    }
                )

            self._write_job(
                {
                    "job_id": job_id,
                    "operation": "apply",
                    "status": "backing_up",
                    "version": target_version,
                    **job_identity,
                    "message": "Creating pre-update backup.",
                }
            )
            backup_path = None
            if self.backup_callable is not None:
                backup_bytes = self.backup_callable(self.config_dir)
                backup_dir = self.config_dir / "backups"
                try:
                    backup_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
                    backup_metadata = backup_dir.lstat()
                except OSError as exc:
                    raise UpdateCenterError(
                        "The private pre-update backup directory is unavailable."
                    ) from exc
                if stat_module.S_ISLNK(
                    backup_metadata.st_mode
                ) or not stat_module.S_ISDIR(backup_metadata.st_mode):
                    raise UpdateCenterError(
                        "Refusing unsafe pre-update backup directory."
                    )
                # Pre-update backups contain credential-bearing configuration,
                # so the directory must remain owner-only.
                os.chmod(  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
                    backup_dir, 0o700
                )
                backup_path = backup_dir / (
                    f"pre-update.v{target_version}.{int(time.time())}.{job_id[:8]}.zip"
                )
                _atomic_write_secret_bytes(backup_path, backup_bytes)
                backup_file_metadata = backup_path.lstat()
                if (
                    not stat_module.S_ISREG(backup_file_metadata.st_mode)
                    or stat_module.S_IMODE(backup_file_metadata.st_mode) != 0o600
                    or backup_file_metadata.st_nlink != 1
                ):
                    raise UpdateCenterError(
                        "Pre-update backup permissions could not be secured."
                    )

            self._write_job(
                {
                    "job_id": job_id,
                    "operation": "apply",
                    "status": "verifying",
                    "version": target_version,
                    **job_identity,
                    "backup_path": str(backup_path) if backup_path else None,
                    "message": "Downloading and verifying update bundle.",
                }
            )
            bundle_url = str(payload.get("bundle_url") or "")
            if not bundle_url:
                raise UpdateManifestError(
                    "Update manifest does not include a bundle URL."
                )
            bundle_bytes = self._fetch_bundle(bundle_url)
            self._verify_bundle_signature(payload, bundle_bytes)
            metadata = validate_bundle_archive(
                bundle_bytes,
                expected_version=target_version,
                expected_runtime_abi=self.runtime_abi,
                expected_settings_schema_version=self.settings_schema_version,
            )

            destination = self.releases_dir / f"v{target_version}"
            self._write_job(
                {
                    "job_id": job_id,
                    "operation": "apply",
                    "status": "applying",
                    "version": target_version,
                    **job_identity,
                    "backup_path": str(backup_path) if backup_path else None,
                    "message": "Installing verified update bundle.",
                }
            )
            extract_bundle_archive(bundle_bytes, destination)

            previous_control = self._read_control_state()
            previous_active = previous_control["active.json"]
            next_active = {
                "version": target_version,
                "activation_id": uuid.uuid4().hex,
                "activation_protocol": self.launcher_protocol,
                "path": str(destination),
                "runtime_abi": self.runtime_abi,
                "settings_schema_version": self.settings_schema_version,
                "activated_at": utc_now(),
                "manifest": {
                    "release_url": payload.get("release_url"),
                    "bundle_sha256": payload.get("bundle_sha256"),
                    "key_id": payload.get("key_id")
                    or latest.get("signature", {}).get("key_id"),
                },
                "metadata": metadata,
            }
            pending = {
                "job_id": job_id,
                "version": target_version,
                **job_identity,
                "activation_id": next_active["activation_id"],
                "path": str(destination),
                "started_at": utc_now(),
                "launcher_protocol": self.launcher_protocol,
                "deadline_at": (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=ACTIVATION_TIMEOUT_SECONDS)
                )
                .isoformat()
                .replace("+00:00", "Z"),
            }
            job = self._prepare_job(
                {
                    "job_id": job_id,
                    "operation": "apply",
                    "status": "restarting",
                    "version": target_version,
                    **job_identity,
                    "backup_path": str(backup_path) if backup_path else None,
                    "message": "Update installed. Restarting ChannelWatch to activate it.",
                    "restart_required": True,
                }
            )
            target_control = {
                **previous_control,
                "active.json": next_active,
                "rollback.json": {
                    "created_at": utc_now(),
                    "target_version": target_version,
                    "previous_active": (
                        previous_active if isinstance(previous_active, dict) else None
                    ),
                    "backup_path": str(backup_path) if backup_path else None,
                },
                "activation-pending.json": pending,
                "activation-core-ready.json": None,
                "activation-ui-ready.json": None,
                "update-job.json": job,
            }
            if self.launcher_protocol == int(LauncherProtocol.LEGACY_ADOPT):
                # Protocol-1 images do not know the schema-2 restart journal.
                # Publish all subordinate state first and active.json last so
                # an interruption cannot select a generation without its
                # rollback target, pending deadline, and job record.
                for name in (
                    "rollback.json",
                    "activation-pending.json",
                    "activation-core-ready.json",
                    "activation-ui-ready.json",
                    "update-job.json",
                    "active.json",
                ):
                    path = self._control_path(name)
                    value = target_control[name]
                    if value is None:
                        try:
                            path.unlink()
                        except FileNotFoundError:
                            pass
                    else:
                        atomic_write_json(path, value)
                fsync_directory(self.runtime_dir)
                if self.restart_callable is not None:
                    restart_error: Exception | None = None
                    try:
                        restart_started = bool(self.restart_callable())
                    except Exception as exc:
                        restart_started = False
                        restart_error = exc
                    if not restart_started:
                        # Active was the last published file, so restoring the
                        # complete pre-apply snapshot returns both old children
                        # to an internally consistent selection.
                        for name in RESTART_CONTROL_FILES:
                            path = self._control_path(name)
                            value = previous_control[name]
                            if value is None:
                                try:
                                    path.unlink()
                                except FileNotFoundError:
                                    pass
                            else:
                                atomic_write_json(path, value)
                        failed_job = self._write_job(
                            {
                                **job,
                                "status": "failed",
                                "message": (
                                    "Update restart could not be started. "
                                    "The previous runtime remains selected."
                                ),
                                "rollback_applied": True,
                                "error": (
                                    str(restart_error)[:2000]
                                    if restart_error is not None
                                    else None
                                ),
                            }
                        )
                        return failed_job
                return job
            commit_journal = self._build_restart_journal(
                reason="runtime_transition",
                operation="apply",
                phase="commit",
                job_id=job_id,
                source_active=(
                    previous_active if isinstance(previous_active, dict) else None
                ),
                control=target_control,
            )
            # The write-ahead journal is the first durable control mutation.
            # Any crash after this point is replayable by the image-stable
            # launcher or the next container entrypoint.
            self._write_restart_journal(commit_journal)
            self.apply_restart_journal(commit_journal)

            if self.restart_callable is not None:
                restart_error: Exception | None = None
                try:
                    restart_started = bool(self.restart_callable())
                except Exception as exc:
                    restart_started = False
                    restart_error = exc
                if not restart_started:
                    handoff_job = self._protocol_three_handoff_result(
                        commit_journal,
                        wait_for_active_helper=True,
                    )
                    if handoff_job is not None:
                        return handoff_job
                    failed_job = self._prepare_job(
                        {
                            **job,
                            "status": "failed",
                            "message": (
                                "Update restart could not be started. "
                                "The previous runtime remains selected."
                            ),
                            "rollback_applied": True,
                            "error": (
                                str(restart_error)[:2000]
                                if restart_error is not None
                                else None
                            ),
                        }
                    )
                    abort_control = {
                        **previous_control,
                        "update-job.json": failed_job,
                    }
                    abort_journal = self._build_restart_journal(
                        reason="runtime_transition",
                        operation="apply",
                        phase="abort",
                        job_id=job_id,
                        source_active=next_active,
                        control=abort_control,
                    )
                    # Atomic journal replacement is the reversal linearization
                    # point. Replay may be repeated after any later crash.
                    if not self._write_restart_journal(
                        abort_journal,
                        expected=commit_journal,
                    ):
                        handoff_job = self._protocol_three_handoff_result(
                            commit_journal
                        )
                        if handoff_job is None:
                            raise UpdateRestartError(
                                "The accepted protocol-3 restart handoff could not "
                                "be reconciled safely."
                            )
                        return handoff_job
                    self.apply_restart_journal(abort_journal)
                    self._clear_restart_journal(abort_journal)
                    return failed_job
            return job

    def rollback(self, *, maintenance_lock_held: bool = False) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with (
            self._maintenance_context(already_held=maintenance_lock_held),
            self._activation_outcome_lock(wait=False),
            UpdateOperationLock(self.lock_path),
            UpdateOperationLock(self.activation_lock_path),
        ):
            self._require_restart_journal_absent()
            previous_control = self._read_control_state()
            rollback = previous_control["rollback.json"]
            if not isinstance(rollback, dict) or "previous_active" not in rollback:
                raise UpdateCenterError("No rollback target is available.")
            previous = rollback.get("previous_active")
            current = previous_control["active.json"]
            if self._activation_in_flight(current):
                # The image-stable launcher intentionally does not import this
                # bundle's lock implementation. Canonical pending state and a
                # durable claim are both cross-version ownership records;
                # never steal either while startup validation is incomplete.
                raise UpdateLockedError(
                    "The activation outcome is still pending startup validation."
                )
            job = self._prepare_job(
                {
                    "job_id": job_id,
                    "operation": "rollback",
                    "status": "restarting",
                    "version": rollback.get("target_version"),
                    "message": "Rollback activated. Restarting ChannelWatch.",
                    "restart_required": True,
                    "rolled_back_from": (
                        current.get("version") if isinstance(current, dict) else None
                    ),
                }
            )
            target_control = {
                **previous_control,
                "active.json": (
                    previous
                    if isinstance(previous, dict) and previous.get("path")
                    else None
                ),
                "activation-pending.json": None,
                "activation-core-ready.json": None,
                "activation-ui-ready.json": None,
                "update-job.json": job,
            }
            if self.launcher_protocol == int(LauncherProtocol.LEGACY_ADOPT):
                for name in (
                    "activation-pending.json",
                    "activation-core-ready.json",
                    "activation-ui-ready.json",
                    "update-job.json",
                    "active.json",
                ):
                    path = self._control_path(name)
                    value = target_control[name]
                    if value is None:
                        try:
                            path.unlink()
                        except FileNotFoundError:
                            pass
                    else:
                        atomic_write_json(path, value)
                fsync_directory(self.runtime_dir)
                if self.restart_callable is not None:
                    restart_error: Exception | None = None
                    try:
                        restart_started = bool(self.restart_callable())
                    except Exception as exc:
                        restart_started = False
                        restart_error = exc
                    if not restart_started:
                        for name in RESTART_CONTROL_FILES:
                            path = self._control_path(name)
                            value = previous_control[name]
                            if value is None:
                                try:
                                    path.unlink()
                                except FileNotFoundError:
                                    pass
                            else:
                                atomic_write_json(path, value)
                        return self._write_job(
                            {
                                **job,
                                "status": "failed",
                                "message": (
                                    "Rollback restart could not be started. "
                                    "The current runtime remains selected."
                                ),
                                "rollback_applied": False,
                                "error": (
                                    str(restart_error)[:2000]
                                    if restart_error is not None
                                    else None
                                ),
                            }
                        )
                return job
            commit_journal = self._build_restart_journal(
                reason="runtime_transition",
                operation="manual_rollback",
                phase="commit",
                job_id=job_id,
                source_active=current if isinstance(current, dict) else None,
                control=target_control,
            )
            self._write_restart_journal(commit_journal)
            self.apply_restart_journal(commit_journal)

            if self.restart_callable is not None:
                restart_error: Exception | None = None
                try:
                    restart_started = bool(self.restart_callable())
                except Exception as exc:
                    restart_started = False
                    restart_error = exc
                if not restart_started:
                    handoff_job = self._protocol_three_handoff_result(
                        commit_journal,
                        wait_for_active_helper=True,
                    )
                    if handoff_job is not None:
                        return handoff_job
                    failed_job = self._prepare_job(
                        {
                            **job,
                            "status": "failed",
                            "message": (
                                "Rollback restart could not be started. "
                                "The current runtime remains selected."
                            ),
                            "rollback_applied": False,
                            "error": (
                                str(restart_error)[:2000]
                                if restart_error is not None
                                else None
                            ),
                        }
                    )
                    abort_control = {
                        **previous_control,
                        "update-job.json": failed_job,
                    }
                    abort_journal = self._build_restart_journal(
                        reason="runtime_transition",
                        operation="manual_rollback",
                        phase="abort",
                        job_id=job_id,
                        source_active=(
                            target_control["active.json"]
                            if isinstance(target_control["active.json"], dict)
                            else None
                        ),
                        control=abort_control,
                    )
                    if not self._write_restart_journal(
                        abort_journal,
                        expected=commit_journal,
                    ):
                        handoff_job = self._protocol_three_handoff_result(
                            commit_journal
                        )
                        if handoff_job is None:
                            raise UpdateRestartError(
                                "The accepted protocol-3 rollback handoff could "
                                "not be reconciled safely."
                            )
                        return handoff_job
                    self.apply_restart_journal(abort_journal)
                    self._clear_restart_journal(abort_journal)
                    return failed_job
            return job

    def record_activation_failure_and_rollback(
        self,
        error: str,
        *,
        job_id: str | None = None,
        pending_identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_restart_journal_absent()
        source_control = self._read_control_state()
        rollback = source_control["rollback.json"]
        current = source_control["active.json"]
        source_pending = source_control["activation-pending.json"]
        pending = (
            pending_identity if isinstance(pending_identity, dict) else source_pending
        )
        previous = (
            rollback.get("previous_active") if isinstance(rollback, dict) else None
        )
        rolled_back_to = (
            str(previous.get("version") or "previous bundle")
            if isinstance(previous, dict) and previous.get("path")
            else "image"
        )
        resolved_job_id = job_id or (
            pending.get("job_id") if isinstance(pending, dict) else None
        )
        job = self._prepare_job(
            {
                "job_id": resolved_job_id,
                "operation": "apply",
                "status": "failed",
                "version": (
                    current.get("version") if isinstance(current, dict) else None
                ),
                "message": "Update activation failed. ChannelWatch rolled back to the previous runtime.",
                "error": error[:2000],
                "rollback_applied": True,
                "rolled_back_from": (
                    current.get("version") if isinstance(current, dict) else None
                ),
                "rolled_back_to": rolled_back_to,
                "failed_at": utc_now(),
                "scheduler_attempt_id": (
                    pending.get("scheduler_attempt_id")
                    if isinstance(pending, dict)
                    else None
                ),
                "bundle_sha256": (
                    pending.get("bundle_sha256") if isinstance(pending, dict) else None
                ),
            }
        )
        target_control = {
            **source_control,
            "active.json": (
                previous
                if isinstance(previous, dict) and previous.get("path")
                else None
            ),
            "activation-pending.json": None,
            "activation-core-ready.json": None,
            "activation-ui-ready.json": None,
            "update-job.json": job,
        }
        if self.launcher_protocol == int(LauncherProtocol.LEGACY_ADOPT):
            for name in (
                "activation-pending.json",
                "activation-core-ready.json",
                "activation-ui-ready.json",
                "update-job.json",
                "active.json",
            ):
                path = self._control_path(name)
                value = target_control[name]
                if value is None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    atomic_write_json(path, value)
            fsync_directory(self.runtime_dir)
            self._record_activation_quarantine(
                pending=pending,
                active=current,
                job=job,
            )
            return job
        journal = self._build_restart_journal(
            reason="activation_rollback",
            operation="activation_rollback",
            phase="commit",
            job_id=resolved_job_id,
            source_active=current if isinstance(current, dict) else None,
            control=target_control,
        )
        self._write_restart_journal(journal)
        self.apply_restart_journal(journal)
        self._record_activation_quarantine(
            pending=pending,
            active=current,
            job=job,
        )
        return job

    def _record_activation_quarantine(
        self,
        *,
        pending: Any,
        active: Any,
        job: dict[str, Any],
    ) -> None:
        """Persist exact failed identity only after durable control rollback."""

        try:
            from core.update_policy import record_failed_activation_quarantine

            record_failed_activation_quarantine(
                self.config_dir,
                pending=pending if isinstance(pending, dict) else None,
                active=active if isinstance(active, dict) else None,
                job=job,
            )
        except Exception:
            # Runtime selection always takes precedence over scheduler
            # bookkeeping. The exact failed job remains available for a later
            # reconciliation if policy storage is temporarily unavailable.
            return

    def _capture_activation_restart_journal(
        self, job: dict[str, Any]
    ) -> dict[str, Any]:
        with self._restart_journal_lock():
            journal = self._load_restart_journal_strict()
            if (
                journal.get("operation") != "activation_rollback"
                or journal.get("phase") != "commit"
                or journal.get("job_id") != job.get("job_id")
                or journal["control"].get("update-job.json") != job
            ):
                raise UpdateLockedError(
                    "Activation rollback journal ownership changed before handoff."
                )
            return journal

    def _adopt_protocol_one_activation(
        self,
        active: dict[str, Any],
        *,
        running_version: str,
    ) -> tuple[dict[str, Any], dict[str, Any], bool] | None:
        """Upgrade a v0.9.10-v0.9.15 selection to the readiness quorum.

        Protocol-1 launchers write ``active.json`` and restart Supervisor, but
        do not create a generation ID or pending deadline. Once the signed
        v0.9.18 bundle is running, its first healthy component adopts that
        exact selected path/version and creates the protocol-2 transaction.
        """

        if str(active.get("activation_id") or ""):
            return None
        if self.launcher_protocol != int(LauncherProtocol.LEGACY_ADOPT):
            return None
        job = load_json(self.job_path, None)
        if not isinstance(job, dict):
            return None
        active_version = str(active.get("version") or "").strip().lstrip("v")
        normalized_running = running_version.strip().lstrip("v")
        if (
            job.get("operation") != "apply"
            or job.get("status") not in {"restarting", "validating"}
            or str(job.get("version") or "").strip().lstrip("v") != active_version
            or normalized_running != active_version
        ):
            return None
        selected_dir = os.environ.get("CHANNELWATCH_APP_DIR", "").strip()
        if not selected_dir:
            return None
        try:
            selected_path = Path(selected_dir).resolve()
            active_path = Path(str(active.get("path") or "")).resolve()
            active_path.relative_to(self.releases_dir.resolve())
        except (OSError, ValueError):
            return None
        if selected_path != active_path:
            return None

        activation_id = uuid.uuid4().hex
        adopted_at = datetime.now(timezone.utc)
        active_manifest = active.get("manifest")
        active_manifest = active_manifest if isinstance(active_manifest, dict) else {}
        legacy_attempt_id = str(
            job.get("scheduler_attempt_id")
            or f"activation@{job.get('job_id') or activation_id}"
        )
        legacy_bundle_sha256 = (
            str(job.get("bundle_sha256") or active_manifest.get("bundle_sha256") or "")
            .strip()
            .lower()
        )
        pending = {
            "job_id": job.get("job_id"),
            "version": active_version,
            "scheduler_attempt_id": legacy_attempt_id,
            "bundle_sha256": legacy_bundle_sha256 or None,
            "activation_id": activation_id,
            "path": str(active_path),
            "started_at": adopted_at.isoformat().replace("+00:00", "Z"),
            "deadline_at": (adopted_at + timedelta(seconds=ACTIVATION_TIMEOUT_SECONDS))
            .isoformat()
            .replace("+00:00", "Z"),
            "adopted_launcher_protocol": int(LauncherProtocol.LEGACY_ADOPT),
        }
        adopted_active = {
            **active,
            "activation_id": activation_id,
            "activation_protocol": int(LauncherProtocol.LEGACY_ADOPT),
            "activation_adopted_at": pending["started_at"],
        }

        # Pending-first is recoverable: a crash before active replacement
        # leaves an unmatched marker that the next adopter clears. Publishing
        # active last prevents a selected generation ID with no deadline.
        self._clear_activation_state()
        atomic_write_json(self.activation_pending_path, pending)
        atomic_write_json(self.active_path, adopted_active)
        self._write_job(
            {
                **job,
                "status": "validating",
                "message": (
                    "Update started through a legacy launcher; waiting for core "
                    "and UI startup validation."
                ),
                "activation_id": activation_id,
                "adopted_launcher_protocol": int(LauncherProtocol.LEGACY_ADOPT),
                "scheduler_attempt_id": legacy_attempt_id,
                "bundle_sha256": legacy_bundle_sha256 or None,
            }
        )
        return adopted_active, pending, True

    @staticmethod
    def _start_adopted_activation_watchdog(app_dir: Path) -> None:
        """Start the bundle watchdog after the caller releases activation locks."""

        try:
            from core.runtime_launcher import start_activation_watchdog

            start_activation_watchdog(app_dir)
        except Exception:
            # Both children independently attempt this. The durable deadline is
            # also re-evaluated on later v0.9.18 process starts.
            return

    def _record_image_refresh_recovery_startup(
        self,
        *,
        component: str,
        running_version: str,
        healthy: bool,
    ) -> bool:
        """Complete image-pull recovery only after a healthy core/UI quorum.

        The image entrypoint removes an unsafe legacy active pointer through a
        replayable journal, but it cannot claim that ChannelWatch started. Each
        child records only its own startup here while the existing update and
        activation locks serialize the cross-process read/modify/write.
        """

        job = load_json(self.job_path, None)
        if not isinstance(job, dict):
            return False
        normalized_version = running_version.strip().lstrip("v")
        if (
            job.get("operation") != "image_refresh_recovery"
            or str(job.get("version") or "").strip().lstrip("v") != normalized_version
            or job.get("legacy_pointer_deactivated") is not True
        ):
            return False
        status = str(job.get("status") or "")
        if status in {"success", "failed"}:
            return True
        validation_id = str(job.get("startup_validation_id") or "")
        if status != "validating" or not validation_id:
            return False

        base_job = {
            "job_id": str(job.get("job_id") or "") or uuid.uuid4().hex,
            "operation": "image_refresh_recovery",
            "version": normalized_version,
            "legacy_pointer_deactivated": True,
            "startup_validation_id": validation_id,
            "restart_required": False,
        }
        components = job.get("startup_components")
        if not isinstance(components, dict):
            components = {}
        else:
            components = {
                name: marker
                for name, marker in components.items()
                if name in {"core", "ui"} and isinstance(marker, dict)
            }

        if not healthy:
            self._write_job(
                {
                    **base_job,
                    "status": "failed",
                    "message": (
                        "The v0.9.18 image runtime was selected, but "
                        f"{component} startup validation failed."
                    ),
                    "image_pull_completed": False,
                    "startup_validation_pending": False,
                    "startup_components": components,
                    "failed_component": component,
                    "failed_at": utc_now(),
                }
            )
            return True

        components[component] = {"healthy": True, "ready_at": utc_now()}
        complete = all(
            isinstance(components.get(name), dict)
            and components[name].get("healthy") is True
            for name in ("core", "ui")
        )
        self._write_job(
            {
                **base_job,
                "status": "success" if complete else "validating",
                "message": (
                    "The v0.9.18 image runtime started successfully; /config "
                    "was preserved and the legacy update marker was cleared."
                    if complete
                    else "The v0.9.18 image runtime is waiting for core and UI "
                    "startup validation."
                ),
                "image_pull_completed": complete,
                "startup_validation_pending": not complete,
                "startup_components": components,
                **({"validated_at": utc_now()} if complete else {}),
            }
        )
        return True

    def _record_manual_rollback_startup(
        self,
        *,
        component: str,
        running_version: str,
        healthy: bool,
        active: dict[str, Any] | None,
    ) -> bool:
        """Finish a manual rollback only after the restored runtime quorum.

        The restart journal durably restores the previous selection before
        either replacement child starts.  Without this second-stage quorum,
        the persisted job remains ``restarting`` forever even though rollback
        succeeded.  Bind completion to the restored image or bundle identity
        so a stale child cannot acknowledge a different selection.
        """

        job = load_json(self.job_path, None)
        if (
            not isinstance(job, dict)
            or job.get("operation") != "rollback"
            or job.get("status") not in {"restarting", "validating"}
        ):
            return False

        normalized_running = running_version.strip().lstrip("v")
        selected_dir = os.environ.get("CHANNELWATCH_APP_DIR", "").strip()
        try:
            running_dir = Path(selected_dir).resolve()
            if isinstance(active, dict) and active.get("path"):
                expected_dir = Path(str(active.get("path") or "")).resolve()
                expected_version = str(active.get("version") or "").strip().lstrip(
                    "v"
                )
            else:
                expected_dir = Path(
                    os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app")
                ).resolve()
                expected_version = self.image_version
        except OSError:
            return False
        if (
            not selected_dir
            or running_dir != expected_dir
            or normalized_running != expected_version
        ):
            return False

        components = job.get("startup_components")
        if not isinstance(components, dict):
            components = {}
        else:
            components = {
                name: marker
                for name, marker in components.items()
                if name in {"core", "ui"} and isinstance(marker, dict)
            }

        base_job = {
            **job,
            "status": "validating",
            "restart_required": True,
            "rollback_applied": True,
            "restored_version": expected_version,
        }
        if not healthy:
            self._write_job(
                {
                    **base_job,
                    "status": "failed",
                    "message": (
                        "Rollback restored the previous runtime, but "
                        f"{component} startup validation failed."
                    ),
                    "restart_required": False,
                    "startup_components": components,
                    "failed_component": component,
                    "failed_at": utc_now(),
                }
            )
            return True

        components[component] = {
            "healthy": True,
            "version": expected_version,
            "ready_at": utc_now(),
        }
        complete = all(
            isinstance(components.get(name), dict)
            and components[name].get("healthy") is True
            and components[name].get("version") == expected_version
            for name in ("core", "ui")
        )
        self._write_job(
            {
                **base_job,
                "status": "success" if complete else "validating",
                "message": (
                    "Rollback completed and the previous ChannelWatch runtime "
                    "started successfully."
                    if complete
                    else "Rollback restored the previous runtime; waiting for "
                    "core and UI startup validation."
                ),
                "restart_required": not complete,
                "startup_components": components,
                **({"validated_at": utc_now()} if complete else {}),
            }
        )
        return True

    def record_startup_success(
        self,
        *,
        component: str,
        running_version: str,
        activation_id: str,
        healthy: bool,
    ) -> None:
        """Record one component and complete only the matching healthy quorum."""

        if component not in {"core", "ui"}:
            raise ValueError(f"Unknown runtime component: {component}")

        restart_needed = False
        committed_restart_journal: dict[str, Any] | None = None
        adopted_watchdog_dir: Path | None = None
        with (
            self._maintenance_context(),
            self._activation_outcome_lock(),
            UpdateOperationLock(self.activation_lock_path, wait_timeout=5.0),
        ):
            # A journal is the authoritative state until a new entrypoint has
            # replayed it and durably pinned both Supervisor children. An old
            # process must not publish readiness or replace that transaction.
            with self._restart_journal_lock():
                if self._restart_journal_present():
                    return
                active = load_json(self.active_path, None)
                if self._record_manual_rollback_startup(
                    component=component,
                    running_version=running_version,
                    healthy=healthy,
                    active=active if isinstance(active, dict) else None,
                ):
                    return
                if not isinstance(active, dict):
                    self._record_image_refresh_recovery_startup(
                        component=component,
                        running_version=running_version,
                        healthy=healthy,
                    )
                    return
                adoption = self._adopt_protocol_one_activation(
                    active, running_version=running_version
                )
                if adoption is not None:
                    active, _adopted_pending, _ = adoption
                    adopted_watchdog_dir = Path(str(active["path"])).resolve()
                    threading.Thread(
                        target=self._start_adopted_activation_watchdog,
                        args=(adopted_watchdog_dir,),
                        daemon=True,
                        name="legacy-activation-watchdog-starter",
                    ).start()
                self._recover_pending_activation(active)
                pending = load_json(self.activation_pending_path, None)
                if not isinstance(pending, dict):
                    return
                if not self._pending_matches_active(pending, active):
                    return
                active_manifest = active.get("manifest")
                active_manifest = (
                    active_manifest if isinstance(active_manifest, dict) else {}
                )
                job_id = str(pending.get("job_id") or "")
                enriched_pending = {
                    **pending,
                    "scheduler_attempt_id": str(
                        pending.get("scheduler_attempt_id")
                        or f"activation@{job_id or active.get('activation_id')}"
                    ),
                    "bundle_sha256": str(
                        pending.get("bundle_sha256")
                        or active_manifest.get("bundle_sha256")
                        or ""
                    )
                    .strip()
                    .lower()
                    or None,
                }
                if enriched_pending != pending:
                    atomic_write_json(self.activation_pending_path, enriched_pending)
                    pending = enriched_pending
                if (
                    int(active.get("activation_protocol") or 0)
                    == int(LauncherProtocol.LEGACY_ADOPT)
                    and adopted_watchdog_dir is None
                ):
                    try:
                        adopted_watchdog_dir = Path(str(active["path"])).resolve()
                    except (KeyError, OSError):
                        adopted_watchdog_dir = None
                    if adopted_watchdog_dir is not None:
                        threading.Thread(
                            target=self._start_adopted_activation_watchdog,
                            args=(adopted_watchdog_dir,),
                            daemon=True,
                            name="protocol-one-activation-watchdog-starter",
                        ).start()

            expected_activation_id = str(active.get("activation_id") or "")
            active_version = str(active.get("version") or "").strip().lstrip("v")
            normalized_running_version = running_version.strip().lstrip("v")
            effective_activation_id = activation_id
            if not effective_activation_id and int(
                active.get("activation_protocol") or 0
            ) == int(LauncherProtocol.LEGACY_ADOPT):
                # Protocol-1 launchers cannot export a generation ID. Path and
                # version were already bound during adoption under both locks.
                effective_activation_id = expected_activation_id
            if (
                effective_activation_id != expected_activation_id
                or normalized_running_version != active_version
                or not healthy
            ):
                with self._restart_journal_lock():
                    if self._restart_journal_present():
                        return
                    claim = self._claim_pending_activation(
                        pending, claimant=f"failed-{component}"
                    )
                if claim is None:
                    return
                try:
                    rollback_job = self.record_activation_failure_and_rollback(
                        "Update runtime identity or health did not match the selected activation.",
                        job_id=str(pending.get("job_id") or "") or None,
                        pending_identity=pending,
                    )
                except Exception:
                    if (
                        not self._restart_journal_present()
                        and claim.exists()
                        and not self.activation_pending_path.exists()
                    ):
                        os.replace(claim, self.activation_pending_path)
                    raise
                else:
                    if self.launcher_protocol != int(LauncherProtocol.LEGACY_ADOPT):
                        committed_restart_journal = (
                            self._capture_activation_restart_journal(rollback_job)
                        )
                    try:
                        claim.unlink()
                    except FileNotFoundError:
                        pass
                restart_needed = True
            else:
                ready_payload = {
                    "component": component,
                    "version": active_version,
                    "activation_id": expected_activation_id,
                    "path": str(active.get("path") or ""),
                    "healthy": True,
                    "ready_at": utc_now(),
                }
                with self._restart_journal_lock():
                    if self._restart_journal_present():
                        return
                    atomic_write_json(
                        self.activation_ready_path(component), ready_payload
                    )

                expected_identity = {
                    "version": active_version,
                    "activation_id": expected_activation_id,
                    "path": str(active.get("path") or ""),
                    "healthy": True,
                }
                components_ready = all(
                    isinstance(marker, dict)
                    and all(
                        marker.get(key) == value
                        for key, value in expected_identity.items()
                    )
                    for marker in (
                        load_json(self.activation_ready_path("core"), {}),
                        load_json(self.activation_ready_path("ui"), {}),
                    )
                )
                if not components_ready:
                    return

                health_error: str | None = None
                try:
                    health_ok = self.healthcheck_callable is None or bool(
                        self.healthcheck_callable()
                    )
                except Exception as exc:
                    health_ok = False
                    health_error = str(exc)[:1000]
                # A health check may call arbitrary integration code. Recheck
                # the journal ownership barrier before claiming or completing
                # activation so an interleaved rollback remains authoritative.
                if self._restart_journal_present():
                    return
                if not health_ok:
                    with self._restart_journal_lock():
                        if self._restart_journal_present():
                            return
                        claim = self._claim_pending_activation(
                            pending, claimant="failed-healthcheck"
                        )
                    if claim is None:
                        return
                    try:
                        rollback_job = self.record_activation_failure_and_rollback(
                            "Update health validation failed."
                            + (f" {health_error}" if health_error else ""),
                            job_id=str(pending.get("job_id") or "") or None,
                            pending_identity=pending,
                        )
                    except Exception:
                        if (
                            not self._restart_journal_present()
                            and claim.exists()
                            and not self.activation_pending_path.exists()
                        ):
                            os.replace(claim, self.activation_pending_path)
                        raise
                    else:
                        if self.launcher_protocol != int(LauncherProtocol.LEGACY_ADOPT):
                            committed_restart_journal = (
                                self._capture_activation_restart_journal(rollback_job)
                            )
                        try:
                            claim.unlink()
                        except FileNotFoundError:
                            pass
                    restart_needed = True
                else:
                    # Completion must atomically own pending state before it
                    # publishes success.  The launcher deadline watchdog uses
                    # the same rename-to-claim transition, so exactly one side
                    # can commit the generation outcome.
                    self._restart_transition_checkpoint(
                        "activation:before-success-lock"
                    )
                    with self._restart_journal_lock():
                        if self._restart_journal_present():
                            return
                        current_pending = load_json(self.activation_pending_path, None)
                        current_active = load_json(self.active_path, None)
                        if (
                            not isinstance(current_pending, dict)
                            or not isinstance(current_active, dict)
                            or not self._pending_matches_active(
                                current_pending, current_active
                            )
                            or str(current_pending.get("activation_id") or "")
                            != expected_activation_id
                        ):
                            return
                        completion_claim = self._claim_pending_activation(
                            current_pending,
                            claimant=f"completed-{component}-{os.getpid()}",
                        )
                        if completion_claim is None:
                            return
                        claimed_pending = load_json(completion_claim, None)
                        claimed_active = load_json(self.active_path, None)
                        if (
                            not isinstance(claimed_pending, dict)
                            or not isinstance(claimed_active, dict)
                            or not self._pending_matches_active(
                                claimed_pending, claimed_active
                            )
                            or str(claimed_pending.get("activation_id") or "")
                            != expected_activation_id
                        ):
                            # Another state transition changed the selected runtime
                            # after the pre-claim check. Preserve the claim as a
                            # forensic/recovery record; never publish success.
                            return
                        try:
                            self._write_job(
                                {
                                    "job_id": claimed_pending.get("job_id"),
                                    "operation": "apply",
                                    "version": active_version,
                                    "scheduler_attempt_id": claimed_pending.get(
                                        "scheduler_attempt_id"
                                    ),
                                    "bundle_sha256": claimed_pending.get(
                                        "bundle_sha256"
                                    ),
                                    "status": "success",
                                    "message": (
                                        "Update activated and ChannelWatch started successfully."
                                    ),
                                    "validated_at": utc_now(),
                                }
                            )
                            self._clear_activation_state()
                        except Exception:
                            # A crash leaves the claimant-specific file durable for
                            # startup recovery. For an ordinary write failure,
                            # restore canonical pending immediately when possible.
                            if (
                                completion_claim.exists()
                                and not self.activation_pending_path.exists()
                            ):
                                os.replace(
                                    completion_claim, self.activation_pending_path
                                )
                                fsync_directory(self.runtime_dir)
                            raise

        if restart_needed and self.restart_callable is not None:
            protocol_one = self.launcher_protocol == int(LauncherProtocol.LEGACY_ADOPT)
            if not protocol_one and committed_restart_journal is None:
                raise UpdateRestartError(
                    "Activation rollback did not retain journal ownership."
                )
            restart_error: Exception | None = None
            try:
                restart_started = bool(self.restart_callable())
            except Exception as exc:
                restart_started = False
                restart_error = exc
            if not restart_started:
                if (
                    not protocol_one
                    and committed_restart_journal is not None
                    and self._protocol_three_handoff_result(
                        committed_restart_journal,
                        wait_for_active_helper=True,
                    )
                    is not None
                ):
                    return
                existing_job = (
                    load_json(self.job_path, None)
                    if protocol_one
                    else committed_restart_journal["control"].get("update-job.json")
                )
                if not isinstance(existing_job, dict):
                    existing_job = {}
                restart_detail = (
                    str(restart_error).strip()[:2000]
                    if restart_error is not None
                    else "The coordinated restart callback did not accept the request."
                )
                if not restart_detail and restart_error is not None:
                    restart_detail = restart_error.__class__.__name__
                failed_job = self._prepare_job(
                    {
                        **existing_job,
                        "status": "failed",
                        "message": (
                            "Update activation failed and the previous runtime was "
                            "selected, but the required container restart could not "
                            "be started."
                        ),
                        "rollback_applied": True,
                        "restart_required": True,
                        "restart_started": False,
                        "restart_error": restart_detail,
                        "updated_at": utc_now(),
                    }
                )
                if protocol_one:
                    self._write_job(failed_job)
                    raise UpdateRestartError(
                        "Update activation rollback completed, but the legacy "
                        "container restart could not be started."
                    ) from restart_error
                assert committed_restart_journal is not None
                if (
                    committed_restart_journal.get("operation") != "activation_rollback"
                    or committed_restart_journal.get("phase") != "commit"
                ):
                    raise UpdateCenterError(
                        "Activation rollback restart journal is not authoritative."
                    )
                updated_control = {
                    **committed_restart_journal["control"],
                    "update-job.json": failed_job,
                }
                updated_journal = self._build_restart_journal(
                    reason="activation_rollback",
                    operation="activation_rollback",
                    phase="commit",
                    job_id=failed_job.get("job_id"),
                    source_active=(
                        committed_restart_journal.get("source_active")
                        if isinstance(
                            committed_restart_journal.get("source_active"), dict
                        )
                        else None
                    ),
                    control=updated_control,
                )
                # Preserve the durable restart requirement while making the
                # callback failure itself crash-replayable as part of the same
                # committed transition.
                try:
                    journal_replaced = self._write_restart_journal(
                        updated_journal,
                        expected=committed_restart_journal,
                    )
                except UpdateLockedError as ownership_error:
                    raise UpdateRestartError(
                        "Update activation rollback completed, but its restart "
                        "journal was replaced by another generation."
                    ) from ownership_error
                if not journal_replaced:
                    if (
                        self._protocol_three_handoff_result(committed_restart_journal)
                        is None
                    ):
                        raise UpdateRestartError(
                            "The accepted protocol-3 activation rollback could "
                            "not be reconciled safely."
                        )
                    return
                self.apply_restart_journal(updated_journal)
                raise UpdateRestartError(
                    "Update activation rollback completed, but the coordinated "
                    "container restart could not be started."
                ) from restart_error
