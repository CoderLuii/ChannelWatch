"""Update Center runtime and bundle management."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import stat as stat_module
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
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

from core.helpers.atomic_io import atomic_write_bytes, atomic_write_json, fsync_directory


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
    req = urllib.request.Request(url, headers={"User-Agent": "ChannelWatch-UpdateCenter"})
    opener = urllib.request.build_opener(_TrustedUpdateRedirectHandler())
    try:
        with opener.open(req, timeout=timeout) as response:  # nosec B310: every URL is allowlisted.
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
                    raise UpdateManifestError("Downloaded update data exceeds size limit.")
                chunks.append(chunk)
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateManifestError(
            "The update service could not be reached. Check container internet and DNS, then try again."
        ) from exc


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
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
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

    def __enter__(self) -> "UpdateOperationLock":
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
    def restart_required_path(self) -> Path:
        return self.runtime_dir / RESTART_REQUIRED_FILE

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
            raise UpdateLockedError(
                "Safe activation outcome locking is unavailable."
            )
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
        if any(value is not None and not isinstance(value, dict) for value in control.values()):
            raise UpdateCenterError("Restart journal control values must be objects or null.")
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

    def _cleanup_restart_journal_candidates(self, *, exclude: Path | None = None) -> None:
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
            before = os.lstat(self.restart_required_path)
        except FileNotFoundError as exc:
            raise UpdateLockedError(
                "The expected runtime transition journal no longer exists."
            ) from exc
        except OSError as exc:
            raise UpdateLockedError(
                "The runtime transition journal cannot be inspected safely."
            ) from exc
        if not stat_module.S_ISREG(before.st_mode):
            raise UpdateLockedError(
                "The runtime transition journal is not a regular file."
            )
        if before.st_nlink != 1:
            raise UpdateLockedError(
                "The runtime transition journal is hard-linked."
            )
        try:
            payload = json.loads(self.restart_required_path.read_text(encoding="utf-8"))
            after = os.lstat(self.restart_required_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UpdateLockedError(
                "The runtime transition journal cannot be read safely."
            ) from exc
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_nlink,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_nlink,
        ):
            raise UpdateLockedError(
                "The runtime transition journal changed while it was being read."
            )
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
    ) -> None:
        """Create a journal without clobbering, or replace its exact owner."""

        validated = self._validate_restart_journal(journal)
        with self._restart_journal_lock():
            self._cleanup_restart_journal_candidates()
            staged_path = self._stage_restart_journal(validated)
            try:
                if expected is None:
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

    @staticmethod
    def _validate_restart_journal(journal: Any) -> dict[str, Any]:
        if not isinstance(journal, dict) or journal.get("schema") != RESTART_JOURNAL_SCHEMA:
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
        if any(value is not None and not isinstance(value, dict) for value in control.values()):
            raise UpdateCenterError("Restart journal control values are invalid.")
        return journal

    def apply_restart_journal(self, journal: dict[str, Any] | None = None) -> dict[str, Any]:
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
        if isinstance(pending, dict) and self._pending_matches_active(
            pending, active
        ):
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
            if isinstance(claim, dict) and self._pending_matches_active(
                claim, active
            ):
                matches.append(claim_path)
        return matches

    def _ensure_runtime(self) -> None:
        self.releases_dir.mkdir(parents=True, exist_ok=True)

    def _write_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_runtime()
        job = self._prepare_job(payload)
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
            "rollback_available": (
                isinstance(active, dict)
                and bool(active.get("path"))
                and isinstance(rollback, dict)
                and "previous_active" in rollback
            ),
            "auth_disabled_warning": os.environ.get("CW_DISABLE_AUTH", "").lower() == "true",
        }

    def check(self) -> dict[str, Any]:
        with UpdateOperationLock(self.lock_path):
            # Avoid a needless network request when a handoff is already known,
            # then repeat the check while holding the cross-version journal lock
            # before changing either canonical update control file.
            self._require_restart_journal_absent()
            manifest = self._read_manifest_from_url()
            payload = manifest["payload"]
            update_available = compare_versions(payload["version"], self.current_version) > 0
            image_required = bool(payload.get("image_required", False))
            if update_available and not image_required:
                if payload.get("runtime_abi") != self.runtime_abi:
                    image_required = True
                if int(payload.get("settings_schema_version") or 0) != self.settings_schema_version:
                    image_required = True
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
        with (
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

            previous_control = self._read_control_state()
            previous_active = previous_control["active.json"]
            next_active = {
                "version": target_version,
                "activation_id": uuid.uuid4().hex,
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
            pending = {
                "job_id": job_id,
                "version": target_version,
                "activation_id": next_active["activation_id"],
                "path": str(destination),
                "started_at": utc_now(),
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
                    self._write_restart_journal(
                        abort_journal, expected=commit_journal
                    )
                    self.apply_restart_journal(abort_journal)
                    self._clear_restart_journal(abort_journal)
                    return failed_job
            return job

    def rollback(self) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with (
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
                    "rolled_back_from": current.get("version") if isinstance(current, dict) else None,
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
                    self._write_restart_journal(
                        abort_journal, expected=commit_journal
                    )
                    self.apply_restart_journal(abort_journal)
                    self._clear_restart_journal(abort_journal)
                    return failed_job
            return job

    def record_activation_failure_and_rollback(
        self, error: str, *, job_id: str | None = None
    ) -> dict[str, Any]:
        self._require_restart_journal_absent()
        source_control = self._read_control_state()
        rollback = source_control["rollback.json"]
        current = source_control["active.json"]
        pending = source_control["activation-pending.json"]
        previous = rollback.get("previous_active") if isinstance(rollback, dict) else None
        rolled_back_to = (
            str(previous.get("version") or "previous bundle")
            if isinstance(previous, dict) and previous.get("path")
            else "image"
        )
        resolved_job_id = (
            job_id
            or (pending.get("job_id") if isinstance(pending, dict) else None)
        )
        job = self._prepare_job(
            {
                "job_id": resolved_job_id,
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
        return job

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
        with (
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
                if not isinstance(active, dict):
                    return
                self._recover_pending_activation(active)
                pending = load_json(self.activation_pending_path, None)
                if not isinstance(pending, dict):
                    return
                if not self._pending_matches_active(pending, active):
                    return

            expected_activation_id = str(active.get("activation_id") or "")
            active_version = str(active.get("version") or "").strip().lstrip("v")
            normalized_running_version = running_version.strip().lstrip("v")
            if (
                activation_id != expected_activation_id
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
                    health_ok = (
                        self.healthcheck_callable is None
                        or bool(self.healthcheck_callable())
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
            if committed_restart_journal is None:
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
                existing_job = committed_restart_journal["control"].get(
                    "update-job.json"
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
                if (
                    committed_restart_journal.get("operation")
                    != "activation_rollback"
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
                    self._write_restart_journal(
                        updated_journal,
                        expected=committed_restart_journal,
                    )
                except UpdateLockedError as ownership_error:
                    raise UpdateRestartError(
                        "Update activation rollback completed, but its restart "
                        "journal was replaced by another generation."
                    ) from ownership_error
                self.apply_restart_journal(updated_journal)
                raise UpdateRestartError(
                    "Update activation rollback completed, but the coordinated "
                    "container restart could not be started."
                ) from restart_error
