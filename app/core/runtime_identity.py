"""Process-local attestation and durable runtime identity comparisons."""

from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Mapping
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any


RUNTIME_IDENTITY_FIELDS = (
    "runtime_generation_id",
    "runtime_descriptor_sha256",
    "dependency_inventory_sha256",
)
_CLAIM_FIELDS = frozenset({"path", *RUNTIME_IDENTITY_FIELDS})
_RUNTIME_TRUST_FIELD = "runtime_trust_sha256"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PROCESS_CLAIM_ATTRIBUTE = "_channelwatch_runtime_generation_identity"
_RUNTIME_DEPENDENCY_DISTRIBUTIONS = {
    "requests": "requests",
    "cryptography": "cryptography",
    "sqlmodel": "sqlmodel",
    "uvicorn": "uvicorn",
    "pydantic_core": "pydantic-core",
    "sqlalchemy": "sqlalchemy",
    "httptools": "httptools",
}
_claim_lock = threading.Lock()


def runtime_process_diagnostics() -> dict[str, Any]:
    """Describe this worker's interpreter and loaded dependency origins."""

    dependencies = {}
    for name, distribution in _RUNTIME_DEPENDENCY_DISTRIBUTIONS.items():
        module = sys.modules.get(name)
        if module is None:
            dependencies[name] = {"loaded": False}
            continue
        version = getattr(module, "__version__", "")
        version = version.strip() if isinstance(version, str) else ""
        record = {
            "loaded": True,
            "path": str(getattr(module, "__file__", "") or ""),
            "version": version,
        }
        if not version:
            try:
                resolved = importlib_metadata.version(distribution)
                if not isinstance(resolved, str) or not resolved.strip():
                    raise ValueError("Distribution version is unavailable")
                record["version"] = resolved.strip()
            except Exception:
                record["version_error"] = "distribution_metadata_unavailable"
        dependencies[name] = record
    return {
        "pid": os.getpid(),
        "python_executable": sys.executable,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "python_prefix": sys.prefix,
        "dependencies": dependencies,
    }


def extract_runtime_identity(record: Any) -> dict[str, str] | None:
    """Return a complete dependency identity, or fail on a partial identity."""

    if not isinstance(record, Mapping):
        return None
    present = [field for field in RUNTIME_IDENTITY_FIELDS if field in record]
    if not present:
        return None
    if len(present) != len(RUNTIME_IDENTITY_FIELDS):
        raise ValueError("Runtime dependency identity is incomplete")
    path = record.get("path")
    if not isinstance(path, str) or not path or not Path(path).is_absolute():
        raise ValueError("Runtime dependency identity path is invalid")
    identity = {"path": path}
    for field in RUNTIME_IDENTITY_FIELDS:
        value = record.get(field)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ValueError(f"Runtime dependency identity field {field} is invalid")
        identity[field] = value
    return identity


def runtime_identities_match(left: Any, right: Any) -> bool:
    """Compare optional identities while rejecting partial or malformed values."""

    try:
        left_identity = extract_runtime_identity(left)
        right_identity = extract_runtime_identity(right)
    except ValueError:
        return False
    if left_identity is None or right_identity is None:
        return left_identity is None and right_identity is None
    return left_identity == right_identity


def activation_records_match(
    pending: Any, active: Any, app_dir: Path | None = None
) -> bool:
    """Match one pending/claim record to the complete selected activation."""

    if not isinstance(pending, Mapping) or not isinstance(active, Mapping):
        return False
    if not str(active.get("activation_id") or ""):
        return False
    if not all(
        str(pending.get(key) or "") == str(active.get(key) or "")
        for key in ("activation_id", "version", "path")
    ):
        return False
    if not runtime_identities_match(pending, active):
        return False
    pending_trust = pending.get(_RUNTIME_TRUST_FIELD)
    active_trust = active.get(_RUNTIME_TRUST_FIELD)
    if pending_trust is not None or active_trust is not None:
        if (
            not isinstance(pending_trust, str)
            or not _SHA256_RE.fullmatch(pending_trust)
            or not isinstance(active_trust, str)
            or not _SHA256_RE.fullmatch(active_trust)
            or pending_trust != active_trust
        ):
            return False
    if app_dir is None:
        return True
    try:
        return Path(str(pending.get("path") or "")).resolve() == app_dir.resolve()
    except OSError:
        return False


def readiness_marker_matches(marker: Any, pending: Any, *, component: str) -> bool:
    """Match one healthy component marker to its full pending identity."""

    if not isinstance(marker, Mapping) or not isinstance(pending, Mapping):
        return False
    expected = {
        "component": component,
        "version": str(pending.get("version") or "").strip().lstrip("v"),
        "activation_id": str(pending.get("activation_id") or ""),
        "path": str(pending.get("path") or ""),
        "healthy": True,
    }
    return all(marker.get(key) == value for key, value in expected.items()) and (
        runtime_identities_match(marker, pending)
    )


def set_verified_runtime_claim(claim: Mapping[str, Any]) -> None:
    """Set the verified process identity once, allowing exact idempotent repeats."""

    if not isinstance(claim, Mapping) or set(claim) != _CLAIM_FIELDS:
        raise ValueError("Verified runtime claim fields are incomplete or unsupported")
    identity = extract_runtime_identity(claim)
    if identity is None:
        raise ValueError("Verified runtime claim requires a dependency identity")
    claimed_path = Path(identity["path"])
    try:
        canonical_path = claimed_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("Verified runtime claim path is unavailable") from exc
    if canonical_path != claimed_path or not canonical_path.is_dir():
        raise ValueError("Verified runtime claim path is not a canonical directory")
    normalized = {**identity, "path": str(canonical_path)}
    with _claim_lock:
        existing = getattr(sys, _PROCESS_CLAIM_ATTRIBUTE, None)
        if existing is None:
            setattr(sys, _PROCESS_CLAIM_ATTRIBUTE, normalized)
            return
        if existing != normalized:
            raise RuntimeError("Verified runtime claim is already set")


def get_verified_runtime_claim() -> dict[str, str] | None:
    """Return a copy of the verified process-local runtime claim."""

    with _claim_lock:
        claim = getattr(sys, _PROCESS_CLAIM_ATTRIBUTE, None)
        if claim is None:
            return None
        if not isinstance(claim, Mapping) or set(claim) != _CLAIM_FIELDS:
            return None
        try:
            identity = extract_runtime_identity(claim)
        except ValueError:
            return None
        if identity is None:
            return None
        claimed_path = Path(identity["path"])
        try:
            canonical_path = claimed_path.resolve(strict=True)
        except OSError:
            return None
        if canonical_path != claimed_path or not canonical_path.is_dir():
            return None
        return {**identity, "path": str(canonical_path)}


def attested_runtime_matches(
    active: Any,
    running_app_path: Path | None,
    verified_runtime_claim: Any = None,
) -> bool:
    """Require process attestation for dependency generations and bind app path."""

    try:
        dependency_identity = extract_runtime_identity(active)
    except ValueError:
        return False
    active_path = str(active.get("path") or "") if isinstance(active, Mapping) else ""
    if dependency_identity is None:
        if verified_runtime_claim is not None:
            return False
        if running_app_path is None or not active_path:
            return True
        try:
            return running_app_path.resolve() == Path(active_path).resolve()
        except OSError:
            return False
    claim = get_verified_runtime_claim()
    if running_app_path is None or claim is None or verified_runtime_claim is None:
        return False
    if not (
        runtime_identities_match(active, claim)
        and runtime_identities_match(active, verified_runtime_claim)
        and claim == verified_runtime_claim
    ):
        return False
    try:
        canonical_running_path = running_app_path.resolve(strict=True)
        return (
            canonical_running_path == Path(active_path).resolve(strict=True)
            and canonical_running_path == Path(claim["path"]).resolve(strict=True)
        )
    except OSError:
        return False
