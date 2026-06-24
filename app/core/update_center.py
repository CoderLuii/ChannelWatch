"""Update Center runtime and bundle management."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import time
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.helpers.atomic_io import atomic_write_bytes, atomic_write_json


RUNTIME_ABI = "channelwatch-runtime-v1"
MANIFEST_SCHEMA_VERSION = 1
DEFAULT_UPDATE_MANIFEST_URL = "https://channelwatch.coderluii.dev/updates/stable.json"
TRUSTED_UPDATE_HOSTS = {"channelwatch.coderluii.dev", "github.com"}
DEFAULT_IMAGE_APP_DIR = Path(os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app"))

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 80 * 1024 * 1024
MAX_BUNDLE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES = 180 * 1024 * 1024
MAX_BUNDLE_MEMBER_COUNT = 3000
LOCK_STALE_SECONDS = 60 * 60

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
    if parsed.hostname.lower() not in TRUSTED_UPDATE_HOSTS:
        raise UpdateManifestError("Update URL host is not trusted.")
    return url


def fetch_bytes(url: str, *, max_bytes: int, timeout: float = 20.0) -> bytes:
    validate_trusted_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "ChannelWatch-UpdateCenter"})
    with urllib.request.urlopen(req, timeout=timeout) as response:  # nosec B310: host is allowlisted above.
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UpdateManifestError("Downloaded update data exceeds size limit.")
            chunks.append(chunk)
        return b"".join(chunks)


def normalize_manifest(raw: dict[str, Any], public_keys: dict[str, str]) -> dict[str, Any]:
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
    verify_ed25519_signature(public_keys, key_id, value, canonical_payload_bytes(payload))

    version = str(payload.get("version") or "").strip().lstrip("v")
    parse_version(version)
    bundle_url = str(payload.get("bundle_url") or "")
    release_url = str(payload.get("release_url") or "")
    if bundle_url:
        validate_trusted_url(bundle_url)
    if release_url:
        validate_trusted_url(release_url)

    return {
        "schema": MANIFEST_SCHEMA_VERSION,
        "payload": {
            **payload,
            "version": version,
            "version_tag": str(payload.get("version_tag") or f"v{version}"),
            "runtime_abi": str(payload.get("runtime_abi") or ""),
            "settings_schema_version": int(payload.get("settings_schema_version") or 0),
            "image_required": bool(payload.get("image_required", False)),
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
    if path.name in {"AGENTS.md", "RELEASE.md", ".env"} or path.suffix in {".pyc", ".pyo"}:
        return False
    if path.name.startswith(".env"):
        return False

    first = path.parts[0]
    return (
        name == "channelwatch-bundle.json"
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
            raise UpdateBundleError("Update bundle is missing channelwatch-bundle.json.")
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
        if int(metadata.get("settings_schema_version") or 0) != expected_settings_schema_version:
            raise UpdateBundleError("Update bundle schema version does not match manifest.")
        if "core/main.py" not in names or "ui/backend/main.py" not in names:
            raise UpdateBundleError("Update bundle is missing required app entrypoints.")
        return metadata


def extract_bundle_archive(bundle_bytes: bytes, destination: Path) -> None:
    destination = destination.resolve()
    temp_destination = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
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

    return RuntimeSelection(bundle_path, "bundle", active=active, reason="active-compatible")


class UpdateOperationLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: int | None = None

    def __enter__(self) -> "UpdateOperationLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._discard_stale_lock()
        try:
            self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise UpdateLockedError("Another update operation is already running.") from exc
        payload = {
            "pid": os.getpid(),
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
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    def _discard_stale_lock(self) -> None:
        try:
            lock_age = time.time() - self.lock_path.stat().st_mtime
        except FileNotFoundError:
            return
        if lock_age < LOCK_STALE_SECONDS:
            return
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
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
        manifest_url: str = DEFAULT_UPDATE_MANIFEST_URL,
        fetcher: Callable[[str, int], bytes] | None = None,
        backup_callable: Callable[[Path], bytes] | None = None,
        restart_callable: Callable[[], bool] | None = None,
        healthcheck_callable: Callable[[], bool] | None = None,
    ):
        self.config_dir = Path(config_dir)
        self.runtime_dir = runtime_dir_for_config(self.config_dir)
        self.current_version = current_version.strip().lstrip("v")
        self.runtime_abi = runtime_abi
        self.settings_schema_version = int(settings_schema_version)
        self.public_keys = dict(public_keys or UPDATE_PUBLIC_KEYS)
        self.manifest_url = manifest_url
        self.fetcher = fetcher
        self.backup_callable = backup_callable
        self.restart_callable = restart_callable
        self.healthcheck_callable = healthcheck_callable

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
    def lock_path(self) -> Path:
        return self.runtime_dir / "update.lock"

    def _ensure_runtime(self) -> None:
        self.releases_dir.mkdir(parents=True, exist_ok=True)

    def _write_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_runtime()
        job = {
            "job_id": payload.get("job_id") or uuid.uuid4().hex,
            "updated_at": utc_now(),
            **payload,
        }
        atomic_write_json(self.job_path, job)
        return job

    def _read_manifest_from_url(self, url: str | None = None) -> dict[str, Any]:
        target = url or self.manifest_url
        data = self.fetcher(target, MAX_MANIFEST_BYTES) if self.fetcher else fetch_bytes(target, max_bytes=MAX_MANIFEST_BYTES)
        return read_manifest_bytes(data, self.public_keys)

    def _fetch_bundle(self, url: str) -> bytes:
        return self.fetcher(url, MAX_BUNDLE_BYTES) if self.fetcher else fetch_bytes(url, max_bytes=MAX_BUNDLE_BYTES)

    def status(self) -> dict[str, Any]:
        self._ensure_runtime()
        active = load_json(self.active_path, None)
        latest = load_json(self.latest_path, None)
        job = load_json(self.job_path, None)
        rollback = load_json(self.rollback_path, None)
        payload = latest.get("payload") if isinstance(latest, dict) else None
        update_available = False
        image_required = False
        if isinstance(payload, dict):
            try:
                update_available = compare_versions(str(payload.get("version") or "0.0.0"), self.current_version) > 0
                image_required = bool(payload.get("image_required"))
            except Exception:
                update_available = False
        return {
            "current_version": self.current_version,
            "runtime_abi": self.runtime_abi,
            "settings_schema_version": self.settings_schema_version,
            "active_bundle": active if isinstance(active, dict) and active.get("path") else None,
            "latest": payload if isinstance(payload, dict) else None,
            "update_available": update_available,
            "image_required": image_required if update_available else False,
            "last_job": job if isinstance(job, dict) else None,
            "rollback_available": isinstance(rollback, dict) and bool(rollback.get("previous_active")),
            "auth_disabled_warning": os.environ.get("CW_DISABLE_AUTH", "").lower() == "true",
        }

    def check(self) -> dict[str, Any]:
        with UpdateOperationLock(self.lock_path):
            manifest = self._read_manifest_from_url()
            self._ensure_runtime()
            atomic_write_json(self.latest_path, manifest)
            payload = manifest["payload"]
            update_available = compare_versions(payload["version"], self.current_version) > 0
            image_required = bool(payload.get("image_required", False))
            if update_available and not image_required:
                if payload.get("runtime_abi") != self.runtime_abi:
                    image_required = True
                if int(payload.get("settings_schema_version") or 0) != self.settings_schema_version:
                    image_required = True
            job = self._write_job(
                {
                    "operation": "check",
                    "status": "image_required" if image_required and update_available else "available" if update_available else "current",
                    "version": payload["version"],
                    "message": "Container image update required." if image_required and update_available else "Update check completed.",
                }
            )
            return {
                **self.status(),
                "latest": payload,
                "update_available": update_available,
                "image_required": image_required if update_available else False,
                "last_job": job,
            }

    def _verify_bundle_signature(self, payload: dict[str, Any], bundle_bytes: bytes) -> None:
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
        verify_ed25519_signature(self.public_keys, key_id, signature_b64, bytes.fromhex(actual_hash))

    def apply(self, version: str | None = None) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with UpdateOperationLock(self.lock_path):
            latest = load_json(self.latest_path, None)
            if not isinstance(latest, dict):
                latest = self._read_manifest_from_url()
                atomic_write_json(self.latest_path, latest)
            payload = latest["payload"]
            target_version = str(payload["version"]).lstrip("v")
            if version and version.strip().lstrip("v") != target_version:
                raise UpdateManifestError("Requested update version does not match the latest trusted manifest.")
            if compare_versions(target_version, self.current_version) <= 0:
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "current",
                        "version": target_version,
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
                        "message": "This release requires a new container image.",
                    }
                )
            if payload.get("runtime_abi") != self.runtime_abi:
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "image_required",
                        "version": target_version,
                        "message": "This release requires a compatible runtime image.",
                    }
                )
            if int(payload.get("settings_schema_version") or 0) != self.settings_schema_version:
                return self._write_job(
                    {
                        "job_id": job_id,
                        "operation": "apply",
                        "status": "image_required",
                        "version": target_version,
                        "message": "This release changes persistent settings schema and needs a new image update.",
                    }
                )

            self._write_job(
                {
                    "job_id": job_id,
                    "operation": "apply",
                    "status": "backing_up",
                    "version": target_version,
                    "message": "Creating pre-update backup.",
                }
            )
            backup_path = None
            if self.backup_callable is not None:
                backup_bytes = self.backup_callable(self.config_dir)
                backup_dir = self.config_dir / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / f"pre-update.v{target_version}.{int(time.time())}.zip"
                atomic_write_bytes(backup_path, backup_bytes)

            self._write_job(
                {
                    "job_id": job_id,
                    "operation": "apply",
                    "status": "verifying",
                    "version": target_version,
                    "backup_path": str(backup_path) if backup_path else None,
                    "message": "Downloading and verifying update bundle.",
                }
            )
            bundle_url = str(payload.get("bundle_url") or "")
            if not bundle_url:
                raise UpdateManifestError("Update manifest does not include a bundle URL.")
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
                    "backup_path": str(backup_path) if backup_path else None,
                    "message": "Installing verified update bundle.",
                }
            )
            extract_bundle_archive(bundle_bytes, destination)

            previous_active = load_json(self.active_path, None)
            next_active = {
                "version": target_version,
                "path": str(destination),
                "runtime_abi": self.runtime_abi,
                "settings_schema_version": self.settings_schema_version,
                "activated_at": utc_now(),
                "manifest": {
                    "release_url": payload.get("release_url"),
                    "bundle_sha256": payload.get("bundle_sha256"),
                    "key_id": payload.get("key_id") or latest.get("signature", {}).get("key_id"),
                },
                "metadata": metadata,
            }
            atomic_write_json(
                self.rollback_path,
                {
                    "created_at": utc_now(),
                    "target_version": target_version,
                    "previous_active": previous_active if isinstance(previous_active, dict) else None,
                    "backup_path": str(backup_path) if backup_path else None,
                },
            )
            atomic_write_json(self.active_path, next_active)

            job = self._write_job(
                {
                    "job_id": job_id,
                    "operation": "apply",
                    "status": "restarting",
                    "version": target_version,
                    "backup_path": str(backup_path) if backup_path else None,
                    "message": "Update installed. Restarting ChannelWatch to activate it.",
                    "restart_required": True,
                }
            )
            if self.restart_callable is not None:
                if not self.restart_callable():
                    return self._write_job(
                        {
                            **job,
                            "status": "failed",
                            "message": "Update installed, but restart could not be started.",
                        }
                    )
            return job

    def rollback(self) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with UpdateOperationLock(self.lock_path):
            rollback = load_json(self.rollback_path, None)
            if not isinstance(rollback, dict) or "previous_active" not in rollback:
                raise UpdateCenterError("No rollback target is available.")
            previous = rollback.get("previous_active")
            current = load_json(self.active_path, None)
            if isinstance(previous, dict) and previous.get("path"):
                atomic_write_json(self.active_path, previous)
            else:
                try:
                    self.active_path.unlink()
                except FileNotFoundError:
                    pass
            job = self._write_job(
                {
                    "job_id": job_id,
                    "operation": "rollback",
                    "status": "restarting",
                    "version": rollback.get("target_version"),
                    "message": "Rollback activated. Restarting ChannelWatch.",
                    "restart_required": True,
                    "rolled_back_from": current.get("version") if isinstance(current, dict) else None,
                }
            )
            if self.restart_callable is not None:
                if not self.restart_callable():
                    return self._write_job(
                        {
                            **job,
                            "status": "failed",
                            "message": "Rollback was selected, but restart could not be started.",
                        }
                    )
            return job

    def record_activation_failure_and_rollback(self, error: str) -> dict[str, Any]:
        rollback = load_json(self.rollback_path, None)
        current = load_json(self.active_path, None)
        previous = rollback.get("previous_active") if isinstance(rollback, dict) else None
        rolled_back_to = "image"

        if isinstance(previous, dict) and previous.get("path"):
            atomic_write_json(self.active_path, previous)
            rolled_back_to = str(previous.get("version") or "previous bundle")
        else:
            try:
                self.active_path.unlink()
            except FileNotFoundError:
                pass

        return self._write_job(
            {
                "operation": "apply",
                "status": "failed",
                "version": current.get("version") if isinstance(current, dict) else None,
                "message": "Update activation failed. ChannelWatch rolled back to the previous runtime.",
                "error": error[:2000],
                "rollback_applied": True,
                "rolled_back_from": current.get("version") if isinstance(current, dict) else None,
                "rolled_back_to": rolled_back_to,
                "failed_at": utc_now(),
            }
        )

    def record_startup_success(self) -> None:
        job = load_json(self.job_path, None)
        active = load_json(self.active_path, None)
        if not isinstance(job, dict) or not isinstance(active, dict):
            return
        if job.get("operation") != "apply" or job.get("status") not in {"restarting", "validating"}:
            return
        if str(job.get("version") or "") != str(active.get("version") or ""):
            return

        if self.healthcheck_callable is not None and not self.healthcheck_callable():
            self.record_activation_failure_and_rollback("Update health validation failed.")
            if self.restart_callable is not None:
                self.restart_callable()
            return

        self._write_job(
            {
                **job,
                "status": "success",
                "message": "Update activated and ChannelWatch started successfully.",
                "validated_at": utc_now(),
            }
        )
