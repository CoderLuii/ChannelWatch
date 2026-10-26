#!/usr/bin/env python3
"""Prove one native protocol-4 runtime update through the public HTTP API."""

from __future__ import annotations

import argparse
import base64
import datetime as datetime_module
import hashlib
import http.cookiejar
import importlib.util
import json
import os
import platform
import re
import secrets
import shutil
import subprocess  # nosec B404 - fixed Docker argv only
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ASSET_VERIFIER = ROOT / "scripts/release/verify-update-assets.py"
DEFAULT_CATALOG_URL = "https://channelwatch.coderluii.dev/updates/v3/stable.json"
LABEL = "codex.task=channelwatch-runtime-capability"
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
TERMINAL_FAILURES = {"failed", "rolled_back", "cancelled", "rejected"}
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
HEX_RE = re.compile(r"[0-9a-f]{64}")
MAX_DEPENDENCY_INVENTORY_BYTES = 2 * 1024 * 1024
MAX_IMAGE_INVENTORY_PATHS = 100000
MAX_IMAGE_INVENTORY_BYTES = 512 * 1024 * 1024
# Two native receipts must leave one MiB for the aggregate envelope.
MAX_RECEIPT_BYTES = 7 * 1024 * 1024 // 2
# The exact decoded update-check response is retained outside the bounded receipt.
MAX_UPDATE_CHECK_ARTIFACT_BYTES = 8 * 1024 * 1024
UPDATE_CHECK_EVIDENCE_MODE = "protocol-4-runtime"
UPDATE_CHECK_EVIDENCE_PHASE = "initial-update-check"
MAX_ROLLBACK_STATUS_ARTIFACT_BYTES = 8 * 1024 * 1024
ROLLBACK_STATUS_EVIDENCE_PHASE = "qualification-post-rollback"
# Four individually bounded control files plus status and JSON envelope.
MAX_RECOVERY_DIAGNOSTIC_BYTES = 8 * 1024 * 1024
RECOVERY_DIAGNOSTIC_VALUE_FIELDS = {
    "recovery_hold_status",
    "recovery_hold_control_state",
    "rollback_status",
    "post_rollback_control_state",
}
TRACKED_DEPENDENCIES = {
    "requests": "requests",
    "cryptography": "cryptography",
    "sqlmodel": "sqlmodel",
    "uvicorn": "uvicorn",
    "pydantic_core": "pydantic-core",
    "sqlalchemy": "sqlalchemy",
    "httptools": "httptools",
}
IMAGE_DIAGNOSTIC_DEPENDENCIES = {
    "requests", "cryptography", "sqlmodel", "uvicorn", "pydantic_core"
}
IMAGE_BOOTSTRAP_PATHS = (
    "/usr/local/bin/channelwatch",
    "/app/channelwatch-image.json",
    "/app/core/docker-entrypoint.py",
    "/app/core/runtime_launcher.py",
    "/app/core/runtime_image_bootstrap.py",
    "/app/core/runtime_release_trust.py",
    "/app/core/runtime_generation.py",
    "/app/core/runtime_dependency_bridge.py",
    "/app/core/runtime_worker.py",
    "/etc/supervisor/conf.d/supervisord.conf.template",
)


class CapabilityError(RuntimeError):
    """Raised when native protocol-4 qualification does not hold."""


class CommandTimeoutError(CapabilityError):
    """A bounded qualification command did not complete."""


DEFAULT_COMMAND_TIMEOUT_SECONDS = 180
CLEANUP_COMMAND_TIMEOUT_SECONDS = 15
CLEANUP_BUDGET_SECONDS = 120


def run(
    command: list[str], *, check: bool = True,
    timeout: float | None = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    deadline = DEFAULT_COMMAND_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        result = subprocess.run(  # nosec B603
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=deadline,
        )
    except subprocess.TimeoutExpired:
        raise CommandTimeoutError(
            f"{Path(command[0]).name} exceeded the {deadline:g}-second command deadline."
        ) from None
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise CapabilityError(f"{command[0]} failed: {detail[:500]}")
    return result


def run_bytes(
    command: list[str], *, timeout: float | None = DEFAULT_COMMAND_TIMEOUT_SECONDS
) -> bytes:
    deadline = DEFAULT_COMMAND_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        result = subprocess.run(  # nosec B603
            command,
            capture_output=True,
            check=False,
            timeout=deadline,
        )
    except subprocess.TimeoutExpired:
        raise CommandTimeoutError(
            f"{Path(command[0]).name} exceeded the {deadline:g}-second command deadline."
        ) from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"command failed")[:500].decode(
            "utf-8", errors="replace"
        )
        raise CapabilityError(f"{command[0]} failed: {detail.strip()}")
    return result.stdout


def _load_asset_verifier():
    spec = importlib.util.spec_from_file_location("runtime_capability_assets", ASSET_VERIFIER)
    if spec is None or spec.loader is None:
        raise CapabilityError("The runtime asset verifier could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_package_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityError("Dependency inventory contains an invalid package name.")
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def load_signed_dependency_inventory(
    bundle_path: Path, descriptor: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            member = archive.getinfo("dependencies/inventory.json")
            if member.file_size > MAX_DEPENDENCY_INVENTORY_BYTES:
                raise CapabilityError("Signed dependency inventory exceeds the evidence bound.")
            raw = archive.read(member)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise CapabilityError("Runtime bundle has no readable signed dependency inventory.") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != descriptor.get("dependency_inventory_sha256"):
        raise CapabilityError("Signed dependency inventory digest does not match the descriptor.")
    try:
        inventory = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CapabilityError("Signed dependency inventory is not canonical JSON.") from exc
    if not isinstance(inventory, dict):
        raise CapabilityError("Signed dependency inventory root is invalid.")
    dependencies = inventory.get("dependencies")
    files = inventory.get("files")
    if not isinstance(dependencies, list) or not isinstance(files, list):
        raise CapabilityError("Signed dependency inventory records are missing.")
    packages: dict[str, dict[str, Any]] = {}
    for row in dependencies:
        if not isinstance(row, dict):
            raise CapabilityError("Signed dependency package record is invalid.")
        name = normalized_package_name(row.get("name"))
        version = row.get("version")
        if name in packages or not isinstance(version, str) or not version.strip():
            raise CapabilityError("Signed dependency package names and versions must be unique and exact.")
        packages[name] = {"name": str(row["name"]), "version": version.strip()}
    file_records: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for row in files:
        if not isinstance(row, dict):
            raise CapabilityError("Signed dependency file record is invalid.")
        path = row.get("path")
        digest_value = row.get("sha256")
        size = row.get("size")
        mode = row.get("mode")
        if (
            not isinstance(path, str)
            or not path.startswith("dependencies/site-packages/")
            or path in file_records
            or not isinstance(digest_value, str)
            or HEX_RE.fullmatch(digest_value) is None
            or type(size) is not int
            or size < 0
            or not isinstance(mode, str)
            or re.fullmatch(r"0[0-7]{3}", mode) is None
        ):
            raise CapabilityError("Signed dependency file metadata is invalid or duplicated.")
        file_records[path] = {
            "path": path,
            "sha256": digest_value,
            "size": size,
            "mode": mode,
        }
        total_bytes += size
    counts = inventory.get("counts")
    if counts != {"files": len(file_records), "bytes": total_bytes}:
        raise CapabilityError("Signed dependency inventory counts are inconsistent.")
    for required in ("sqlalchemy", "httptools"):
        if required not in packages:
            raise CapabilityError(f"Signed dependency inventory omits {required}.")
    if packages["sqlalchemy"]["version"] != "2.1.4" or packages["httptools"]["version"] != "0.9.0":
        raise CapabilityError("Signed dependency inventory does not contain the approved native proof versions.")
    parser_files = [
        record
        for path, record in file_records.items()
        if re.fullmatch(
            r"dependencies/site-packages/httptools/parser/parser[^/]*cpython-314[^/]*\.so", path
        )
    ]
    if len(parser_files) != 1:
        raise CapabilityError("Signed dependency inventory has no unique httptools parser extension.")
    evidence = {
        "sha256": digest,
        "size": len(raw),
        "json_base64": base64.b64encode(raw).decode("ascii"),
    }
    return evidence, {
        "packages": packages,
        "files": file_records,
        "httptools_parser": parser_files[0],
    }


def verify_assets(
    catalog_path: Path,
    bundles: dict[str, Path],
    *,
    version: str,
    source_commit: str,
) -> tuple[bytes, dict[str, Any], dict[str, dict[str, Any]]]:
    catalog_bytes = catalog_path.read_bytes()
    try:
        raw = json.loads(catalog_bytes.decode("utf-8"))
        releases = raw["payload"]["releases"]
        release = next(
            item
            for item in releases
            if isinstance(item, dict) and item.get("version") == version
        )
        variants = release["runtime_variants"]
        by_arch = {
            item["runtime_descriptor"]["architecture"]: item for item in variants
        }
        urls = {architecture: by_arch[architecture]["bundle_url"] for architecture in ("amd64", "arm64")}
    except (UnicodeError, json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
        raise CapabilityError("The signed runtime catalog has no unique two-architecture target.") from exc
    if set(by_arch) != {"amd64", "arm64"} or set(bundles) != {"amd64", "arm64"}:
        raise CapabilityError("Qualification requires exactly amd64 and arm64 assets.")
    verifier = _load_asset_verifier()
    verified = verifier.verify_runtime_update_catalog(
        catalog_bytes,
        {name: bundles[name].read_bytes() for name in ("amd64", "arm64")},
        expected_version=version,
        expected_source_commit=source_commit,
        expected_bundle_urls=urls,
    )
    if verified != raw:
        raise CapabilityError("The asset verifier did not return the exact signed catalog.")
    return catalog_bytes, release, by_arch


def native_architecture(requested: str | None = None) -> str:
    host = ARCHITECTURES.get(platform.machine().lower())
    docker = ARCHITECTURES.get(
        run(["docker", "info", "--format", "{{.Architecture}}"], timeout=30)
        .stdout.strip()
        .lower()
    )
    if host is None or docker is None or host != docker:
        raise CapabilityError("Host and Docker server must expose the same native architecture.")
    if requested is not None and requested != host:
        raise CapabilityError("Requested qualification architecture is not host-native.")
    return host


def pinned_native_image(
    repository: str, index_digest: str, architecture: str
) -> tuple[str, str, str, str, dict[str, Any]]:
    if SHA256_RE.fullmatch(index_digest) is None:
        raise CapabilityError("Bootstrap image index digest is invalid.")
    raw = run_bytes(
        ["docker", "buildx", "imagetools", "inspect", "--raw", f"{repository}@{index_digest}"],
        timeout=120,
    )
    if "sha256:" + hashlib.sha256(raw).hexdigest() != index_digest:
        raise CapabilityError("Bootstrap image index bytes do not match the pinned digest.")
    try:
        index = json.loads(raw.decode("utf-8"))
        matches = [
            item
            for item in index["manifests"]
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == architecture
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CapabilityError("Bootstrap reference is not a valid multi-platform index.") from exc
    if len(matches) != 1 or SHA256_RE.fullmatch(str(matches[0].get("digest") or "")) is None:
        raise CapabilityError("Bootstrap index has no unique native Linux manifest.")
    manifest_digest = str(matches[0]["digest"])
    reference = f"{repository}@{manifest_digest}"
    run(["docker", "pull", "--platform", f"linux/{architecture}", reference], timeout=300)
    inspected = json.loads(run(["docker", "image", "inspect", reference]).stdout)
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise CapabilityError("Pulled bootstrap image inspection is invalid.")
    image = inspected[0]
    if (
        image.get("Os") != "linux"
        or image.get("Architecture") != architecture
        or SHA256_RE.fullmatch(str(image.get("Id") or "")) is None
        or reference not in image.get("RepoDigests", [])
    ):
        raise CapabilityError("Pulled bootstrap image is not the selected native manifest.")
    inspection = {
        "os": image["Os"],
        "architecture": image["Architecture"],
        "repo_digests": image["RepoDigests"],
    }
    return (
        reference,
        manifest_digest,
        str(image["Id"]),
        base64.b64encode(raw).decode("ascii"),
        inspection,
    )


def validate_update_check(
    checked: object,
    *,
    version: str,
    descriptor: dict[str, Any],
    bundle_sha256: str,
) -> dict[str, Any]:
    if not isinstance(checked, dict):
        raise CapabilityError("Update check did not select the exact signed runtime target.")
    target = checked.get("trusted_target")
    if (
        checked.get("update_available") is not True
        or checked.get("image_required") is not False
        or checked.get("catalog_state") != "update_available"
        or not isinstance(target, dict)
        or target.get("version") != version
        or target.get("runtime_descriptor") != descriptor
        or target.get("bundle_sha256") != bundle_sha256
    ):
        raise CapabilityError("Update check did not select the exact signed runtime target.")
    return checked


def retain_update_check_status(
    evidence_json: Path,
    *,
    architecture: str,
    source_commit: str,
    status: dict[str, Any],
) -> dict[str, Any]:
    if architecture not in {"amd64", "arm64"} or re.fullmatch(
        r"[0-9a-f]{40}", source_commit
    ) is None:
        raise CapabilityError("Initial update-check evidence binding is invalid.")
    path = evidence_json.parent / f"{evidence_json.stem}-initial-update-check.json"
    payload = {
        "schema": 1,
        "mode": UPDATE_CHECK_EVIDENCE_MODE,
        "phase": UPDATE_CHECK_EVIDENCE_PHASE,
        "architecture": architecture,
        "source_commit": source_commit,
        "status": status,
    }
    try:
        encoded = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CapabilityError("Initial update-check evidence is not valid JSON.") from exc
    if len(encoded) > MAX_UPDATE_CHECK_ARTIFACT_BYTES:
        raise CapabilityError("Initial update-check evidence exceeds its 8 MiB bound.")
    path.write_bytes(encoded)
    return artifact_descriptor(path)


def check_update(
    client: Any, evidence_json: Path, *, architecture: str, source_commit: str,
    version: str, descriptor: dict[str, Any], bundle_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    checked = client.request("/api/v1/update/check", {})
    artifact = retain_update_check_status(
        evidence_json, architecture=architecture, source_commit=source_commit,
        status=checked,
    )
    return validate_update_check(
        checked, version=version, descriptor=descriptor,
        bundle_sha256=bundle_sha256,
    ), artifact


def compact_update_check_evidence(
    checked: dict[str, Any], artifact: dict[str, Any]
) -> dict[str, Any]:
    target = checked.get("trusted_target")
    if not isinstance(target, dict):
        raise CapabilityError("Initial update-check evidence has no trusted target.")
    return {
        "update_available": checked.get("update_available"),
        "image_required": checked.get("image_required"),
        "catalog_state": checked.get("catalog_state"),
        "trusted_target": {
            "version": target.get("version"),
            "runtime_descriptor": target.get("runtime_descriptor"),
            "bundle_sha256": target.get("bundle_sha256"),
        },
        "raw_status_artifact": artifact,
    }


def retain_rollback_status(
    evidence_path: Path,
    *,
    architecture: str,
    source_commit: str,
    status: dict[str, Any],
) -> dict[str, Any]:
    path = evidence_path.with_name(
        evidence_path.stem + "-post-rollback-status.json"
    )
    payload = {
        "schema": 1,
        "mode": UPDATE_CHECK_EVIDENCE_MODE,
        "phase": ROLLBACK_STATUS_EVIDENCE_PHASE,
        "architecture": architecture,
        "source_commit": source_commit,
        "status": status,
    }
    try:
        encoded = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CapabilityError("Post-rollback status evidence is not valid JSON.") from exc
    if len(encoded) > MAX_ROLLBACK_STATUS_ARTIFACT_BYTES:
        raise CapabilityError("Post-rollback status evidence exceeds its 8 MiB bound.")
    path.write_bytes(encoded)
    return artifact_descriptor(path)


def compact_rollback_status_evidence(
    status: dict[str, Any], artifact: dict[str, Any]
) -> dict[str, Any]:
    return {
        "current_version": status.get("current_version"),
        "launcher_protocol": status.get("launcher_protocol"),
        "active_bundle": status.get("active_bundle"),
        "operation_busy": status.get("operation_busy"),
        "worker_runtime": status.get("worker_runtime"),
        "raw_status_artifact": artifact,
    }


def compact_filesystem_refusal_status(
    status: object, *, target_version: str
) -> dict[str, Any]:
    validated = validate_rollback_status(status, target_version=target_version)
    return {
        "current_version": validated["current_version"],
        "launcher_protocol": validated["launcher_protocol"],
        "active_bundle": validated["active_bundle"],
        "operation_busy": validated["operation_busy"],
        "worker_runtime": validated["worker_runtime"],
    }


def validate_rollback_response(
    response: object, *, source_version: str, target_version: str
) -> dict[str, Any]:
    if (
        not isinstance(response, dict)
        or response.get("operation") != "rollback"
        or response.get("status") not in {"restarting", "validating", "success"}
        or response.get("version") != source_version
        or response.get("rolled_back_from") != source_version
        or not isinstance(response.get("job_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", response["job_id"]) is None
    ):
        raise CapabilityError("API rollback response is not bound to the selected runtime.")

    status = response["status"]
    if status == "restarting":
        if response.get("restart_required") is not True:
            raise CapabilityError(
                "API rollback response is not bound to the selected runtime."
            )
        return response

    components = response.get("startup_components")
    expected_components = {"core", "ui"} if status == "success" else None
    if (
        response.get("rollback_applied") is not True
        or response.get("restored_version") != target_version
        or not isinstance(components, dict)
        or not components
        or not set(components).issubset({"core", "ui"})
        or (expected_components is not None and set(components) != expected_components)
        or (status == "validating" and len(components) != 1)
        or any(
            not isinstance(marker, dict)
            or set(marker) != {"healthy", "version", "ready_at"}
            or marker.get("healthy") is not True
            or marker.get("version") != target_version
            or not isinstance(marker.get("ready_at"), str)
            or not marker["ready_at"]
            for marker in components.values()
        )
        or (status == "validating" and response.get("restart_required") is not True)
        or (status == "success" and response.get("restart_required") is not False)
        or (
            status == "success"
            and (
                not isinstance(response.get("validated_at"), str)
                or not response["validated_at"]
            )
        )
    ):
        raise CapabilityError("API rollback response is not bound to the selected runtime.")
    return response


def validate_rollback_status(
    status: object, *, target_version: str, require_idle: bool = True
) -> dict[str, Any]:
    if (
        not isinstance(status, dict)
        or status.get("current_version") != target_version
        or status.get("launcher_protocol") != 4
        or status.get("active_bundle") is not None
        or (require_idle and status.get("operation_busy") is not False)
    ):
        raise CapabilityError("Durable post-rollback status is invalid.")
    worker = status.get("worker_runtime")
    if (
        not isinstance(worker, dict)
        or set(worker) != {
            "python_version", "python_executable", "verified_generation"
        }
        or worker.get("python_executable") != "/venv/bin/python"
        or worker.get("python_version") != "3.14.8"
        or worker.get("verified_generation") is not None
    ):
        raise CapabilityError("Frozen image status interpreter identity is invalid.")
    return status


def _validate_frozen_image_worker(status: dict[str, Any]) -> dict[str, Any]:
    worker = status.get("worker_runtime")
    if (
        not isinstance(worker, dict)
        or set(worker) != {
            "python_version", "python_executable", "verified_generation"
        }
        or worker.get("python_executable") != "/venv/bin/python"
        or worker.get("python_version") != "3.14.8"
        or worker.get("verified_generation") is not None
    ):
        raise CapabilityError("Frozen image status interpreter identity is invalid.")
    return worker


def validate_recovery_hold_status(
    status: object,
    *,
    descriptor: dict[str, Any],
    version: str,
    image_version: str,
    bundle_sha256: str,
    successful_apply_job: dict[str, Any],
) -> dict[str, Any]:
    generation_root = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
    active = status.get("active_bundle") if isinstance(status, dict) else None
    manifest = active.get("manifest") if isinstance(active, dict) else None
    if (
        not isinstance(status, dict)
        or status.get("current_version") != image_version
        or status.get("image_version") != image_version
        or status.get("launcher_protocol") != 4
        or status.get("runtime_source") != "image"
        or status.get("operation_busy") is not False
        or status.get("rollback_available") is not True
        or not isinstance(successful_apply_job, dict)
        or not isinstance(active, dict)
        or str(active.get("version") or "").lstrip("v") != version
        or active.get("path") != generation_root
        or active.get("runtime_generation_id") != descriptor["generation_id"]
        or active.get("runtime_descriptor_sha256") != descriptor["generation_id"]
        or active.get("dependency_inventory_sha256")
        != descriptor["dependency_inventory_sha256"]
        or not isinstance(manifest, dict)
        or manifest.get("bundle_sha256") != bundle_sha256
        or status.get("last_job") != successful_apply_job
        or successful_apply_job.get("operation") != "apply"
        or successful_apply_job.get("status") != "success"
        or str(successful_apply_job.get("version") or "").lstrip("v") != version
        or successful_apply_job.get("bundle_sha256") != bundle_sha256
        or not isinstance(successful_apply_job.get("job_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", successful_apply_job["job_id"]) is None
    ):
        raise CapabilityError("Durable official recovery-hold status is invalid.")
    _validate_frozen_image_worker(status)
    return status


def validate_recovery_marker(
    record: object, *, version: str, bundle_sha256: str
) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record, dict) else None
    entered_at = payload.get("entered_at") if isinstance(payload, dict) else None
    try:
        parsed_time = datetime_module.datetime.fromisoformat(
            str(entered_at).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        parsed_time = None
    if (
        not isinstance(record, dict)
        or set(record) != {"type", "mode", "size", "sha256", "payload"}
        or record.get("type") != "file"
        or not isinstance(record.get("mode"), str)
        or re.fullmatch(r"0[0-7]{3}", record["mode"]) is None
        or type(record.get("size")) is not int
        or record["size"] <= 0
        or not isinstance(record.get("sha256"), str)
        or HEX_RE.fullmatch(record["sha256"]) is None
        or not isinstance(payload, dict)
        or set(payload) != {
            "schema", "mode", "failed_version", "failed_bundle_sha256", "entered_at"
        }
        or type(payload.get("schema")) is not int
        or payload.get("schema") != 1
        or payload.get("mode") != "official_signed_only"
        or str(payload.get("failed_version") or "").lstrip("v") != version
        or payload.get("failed_bundle_sha256") != bundle_sha256
        or parsed_time is None
        or parsed_time.tzinfo is None
    ):
        raise CapabilityError("Official recovery marker is invalid.")
    return record


def validate_completed_rollback_job(
    job: object,
    *,
    response: dict[str, Any],
    successful_apply_job: dict[str, Any],
    source_version: str,
    image_version: str,
) -> dict[str, Any]:
    startup_components = job.get("startup_components") if isinstance(job, dict) else None
    components_valid = isinstance(startup_components, dict) and set(
        startup_components
    ) == {"core", "ui"}
    if components_valid:
        for record in startup_components.values():
            ready_at = record.get("ready_at") if isinstance(record, dict) else None
            try:
                parsed_time = datetime_module.datetime.fromisoformat(
                    str(ready_at).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                parsed_time = None
            if (
                not isinstance(record, dict)
                or set(record) != {"healthy", "version", "ready_at"}
                or record.get("healthy") is not True
                or str(record.get("version") or "").lstrip("v") != image_version
                or parsed_time is None
                or parsed_time.tzinfo is None
            ):
                components_valid = False
                break
    if (
        not isinstance(job, dict)
        or not isinstance(response, dict)
        or not isinstance(successful_apply_job, dict)
        or job.get("job_id") != response.get("job_id")
        or job.get("job_id") == successful_apply_job.get("job_id")
        or job.get("operation") != "rollback"
        or job.get("status") != "success"
        or str(job.get("version") or "").lstrip("v") != source_version
        or str(job.get("rolled_back_from") or "").lstrip("v") != source_version
        or str(job.get("restored_version") or "").lstrip("v") != image_version
        or job.get("rollback_applied") is not True
        or job.get("restart_required") is not False
        or not components_valid
    ):
        raise CapabilityError("Completed rollback job is not bound to the recovery hold.")
    return job


def validate_log_hashes(hashes: object) -> dict[str, str]:
    if (
        not isinstance(hashes, dict)
        or set(hashes) != {"app", "feed"}
        or any(
            not isinstance(value, str) or HEX_RE.fullmatch(value) is None
            for value in hashes.values()
        )
    ):
        raise CapabilityError("Native evidence requires exact nonempty app and feed logs.")
    return hashes


def validate_runtime_components(
    job: dict[str, Any], descriptor: dict[str, Any], inventory: dict[str, Any]
) -> dict[str, Any]:
    components = job.get("runtime_components")
    if not isinstance(components, dict) or set(components) != {"core", "ui"}:
        raise CapabilityError("Successful update did not retain both runtime component receipts.")
    expected_identity = {
        "runtime_generation_id": descriptor["generation_id"],
        "runtime_descriptor_sha256": descriptor["generation_id"],
        "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
    }
    generation_root = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
    expected_environment = f"{generation_root}/environment"
    expected_site_packages = f"{generation_root}/dependencies/site-packages/"
    for component, value in components.items():
        if not isinstance(value, dict) or any(
            value.get(name) != expected for name, expected in expected_identity.items()
        ):
            raise CapabilityError(f"{component} runtime identity does not match the signed descriptor.")
        process = value.get("process_runtime")
        dependencies = process.get("dependencies") if isinstance(process, dict) else None
        if (
            not isinstance(process, dict)
            or type(process.get("pid")) is not int
            or process["pid"] <= 0
            or process.get("python_version") != descriptor["python_version"]
            or process.get("python_executable") != f"{expected_environment}/bin/python"
            or process.get("python_prefix") != expected_environment
            or not isinstance(dependencies, dict)
        ):
            raise CapabilityError(f"{component} process diagnostics are missing.")
        for required in ("requests", "cryptography", "sqlalchemy"):
            record = dependencies.get(required)
            if not isinstance(record, dict) or record.get("loaded") is not True:
                raise CapabilityError(f"{component} did not load required dependency {required}.")
        if set(dependencies) != set(TRACKED_DEPENDENCIES):
            raise CapabilityError(f"{component} dependency diagnostics use an unexpected namespace.")
        for name, distribution in TRACKED_DEPENDENCIES.items():
            record = dependencies[name]
            if not isinstance(record, dict):
                raise CapabilityError(f"{component} dependency diagnostic {name} is invalid.")
            if record.get("loaded") is True:
                package = inventory["packages"].get(distribution)
                if package is None or record.get("version") != package["version"]:
                    raise CapabilityError(f"{component} loaded {name} with an unsigned version.")
                if not str(record.get("path") or "").startswith(expected_site_packages):
                    raise CapabilityError(f"{component} loaded {name} outside the selected generation.")
            elif record != {"loaded": False}:
                raise CapabilityError(f"{component} unloaded dependency diagnostic {name} is invalid.")
    return components


def _validate_selected_process_runtime(
    process: object, descriptor: dict[str, Any], inventory: dict[str, Any], component: str
) -> dict[str, Any]:
    generation_root = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
    expected_environment = f"{generation_root}/environment"
    expected_site_packages = f"{generation_root}/dependencies/site-packages/"
    dependencies = process.get("dependencies") if isinstance(process, dict) else None
    if (
        not isinstance(process, dict)
        or type(process.get("pid")) is not int
        or process["pid"] <= 0
        or process.get("python_version") != descriptor["python_version"]
        or process.get("python_executable") != f"{expected_environment}/bin/python"
        or process.get("python_prefix") != expected_environment
        or not isinstance(dependencies, dict)
        or set(dependencies) != set(TRACKED_DEPENDENCIES)
    ):
        raise CapabilityError(f"{component} selected process diagnostics are invalid.")
    for required in ("requests", "cryptography", "sqlalchemy"):
        record = dependencies.get(required)
        if not isinstance(record, dict) or record.get("loaded") is not True:
            raise CapabilityError(f"{component} did not load required dependency {required}.")
    for module, distribution in TRACKED_DEPENDENCIES.items():
        record = dependencies[module]
        if not isinstance(record, dict):
            raise CapabilityError(f"{component} dependency diagnostic {module} is invalid.")
        if record.get("loaded") is True:
            package = inventory["packages"].get(distribution)
            if (
                package is None
                or record.get("version") != package["version"]
                or not str(record.get("path") or "").startswith(expected_site_packages)
            ):
                raise CapabilityError(f"{component} loaded {module} outside the signed generation.")
        elif record != {"loaded": False}:
            raise CapabilityError(f"{component} unloaded dependency diagnostic {module} is invalid.")
    return process


def validate_selected_status(
    status: object,
    *,
    descriptor: dict[str, Any],
    inventory: dict[str, Any],
    version: str,
    ui_pid: int,
) -> dict[str, Any]:
    generation_root = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
    expected_identity = {
        "path": generation_root,
        "runtime_generation_id": descriptor["generation_id"],
        "runtime_descriptor_sha256": descriptor["generation_id"],
        "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
    }
    active = status.get("active_bundle") if isinstance(status, dict) else None
    worker = status.get("worker_runtime") if isinstance(status, dict) else None
    if (
        not isinstance(status, dict)
        or status.get("current_version") != version
        or not isinstance(active, dict)
        or active.get("path") != generation_root
        or any(active.get(key) != value for key, value in expected_identity.items() if key != "path")
        or not isinstance(worker, dict)
        or worker.get("pid") != ui_pid
        or worker.get("verified_generation") != expected_identity
    ):
        raise CapabilityError("Current UI status is not bound to the selected generation/process.")
    _validate_selected_process_runtime(worker, descriptor, inventory, "ui status handler")
    return status


def validate_preservation(before: dict[str, str], after: dict[str, str]) -> None:
    if not before or before != after:
        raise CapabilityError("Persistent settings, managed key, or image venv identity changed.")


class DockerResources:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.containers: set[str] = set()
        self.volumes: set[str] = set()
        self.networks: set[str] = set()

    def create_network(self) -> str:
        name = f"{self.prefix}-network"
        self.networks.add(name)
        run(["docker", "network", "create", "--label", LABEL, name])
        return name

    def create_volume(self, suffix: str = "config") -> str:
        name = f"{self.prefix}-{suffix}"
        self.volumes.add(name)
        run(["docker", "volume", "create", "--label", LABEL, name])
        return name

    def container(self, suffix: str) -> str:
        name = f"{self.prefix}-{suffix}"
        self.containers.add(name)
        return name

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        deadline = time.monotonic() + CLEANUP_BUDGET_SECONDS

        def cleanup_command(command: list[str]) -> subprocess.CompletedProcess[str]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandTimeoutError("Owned resource cleanup deadline was exhausted.")
            return run(
                command, check=False,
                timeout=min(CLEANUP_COMMAND_TIMEOUT_SECONDS, remaining),
            )

        for name in sorted(self.containers):
            try:
                inspected = cleanup_command(
                    ["docker", "inspect", "--format", '{{index .Config.Labels "codex.task"}}', name],
                )
            except CommandTimeoutError:
                failures.append(f"container:{name}:inspect_timeout")
                continue
            except Exception:
                failures.append(f"container:{name}:inspect_error")
                continue
            if inspected.returncode != 0:
                failures.append(f"container:{name}:inspect_failed")
                continue
            if inspected.stdout.strip() != LABEL.split("=", 1)[1]:
                failures.append(f"container:{name}:ownership_mismatch")
                continue
            try:
                removed = cleanup_command(["docker", "rm", "-f", name])
            except CommandTimeoutError:
                failures.append(f"container:{name}:remove_timeout")
                continue
            except Exception:
                failures.append(f"container:{name}:remove_error")
                continue
            if removed.returncode != 0:
                failures.append(f"container:{name}:remove_failed")
        for kind, names in (("volume", self.volumes), ("network", self.networks)):
            for name in sorted(names):
                try:
                    inspected = cleanup_command(
                        ["docker", kind, "inspect", "--format", '{{index .Labels "codex.task"}}', name],
                    )
                except CommandTimeoutError:
                    failures.append(f"{kind}:{name}:inspect_timeout")
                    continue
                except Exception:
                    failures.append(f"{kind}:{name}:inspect_error")
                    continue
                if inspected.returncode != 0:
                    failures.append(f"{kind}:{name}:inspect_failed")
                    continue
                if inspected.stdout.strip() != LABEL.split("=", 1)[1]:
                    failures.append(f"{kind}:{name}:ownership_mismatch")
                    continue
                try:
                    removed = cleanup_command(["docker", kind, "rm", name])
                except CommandTimeoutError:
                    failures.append(f"{kind}:{name}:remove_timeout")
                    continue
                except Exception:
                    failures.append(f"{kind}:{name}:remove_error")
                    continue
                if removed.returncode != 0:
                    failures.append(f"{kind}:{name}:remove_failed")
        return failures


def create_tls_material(root: Path) -> tuple[Path, Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime_module.datetime.now(datetime_module.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ChannelWatch capability CA")])
    ca_subject_key_identifier = x509.SubjectKeyIdentifier.from_public_key(
        ca_key.public_key()
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime_module.timedelta(minutes=5))
        .not_valid_after(now + datetime_module.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ca_subject_key_identifier, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_subject_key_identifier
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "channelwatch.coderluii.dev")])
    server = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime_module.timedelta(minutes=5))
        .not_valid_after(now + datetime_module.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("channelwatch.coderluii.dev"), x509.DNSName("github.com")]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca_subject_key_identifier
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = root / "ca.pem", root / "server.pem", root / "server-key.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return ca_path, cert_path, key_path


def publish_feed_catalog(source: Path, served: Path) -> str:
    data = source.read_bytes()
    expected = hashlib.sha256(data).hexdigest()
    served_mode = served.stat().st_mode & 0o777
    descriptor, name = tempfile.mkstemp(prefix=f".{served.name}.", dir=served.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        temporary.chmod(served_mode)
        os.replace(temporary, served)
        if hashlib.sha256(served.read_bytes()).hexdigest() != expected:
            raise CapabilityError("Published feed catalog failed readback verification.")
    finally:
        temporary.unlink(missing_ok=True)
    return expected


def write_feed(root: Path, routes: dict[str, Path]) -> dict[str, Path]:
    route_map: dict[str, str] = {}
    staged_routes: dict[str, Path] = {}
    assets = root / "assets"
    assets.mkdir(parents=True)
    for index, (url, source) in enumerate(sorted(routes.items())):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "channelwatch.coderluii.dev"}:
            raise CapabilityError("Signed feed URL is not an approved HTTPS origin.")
        key = f"asset-{index}"
        shutil.copyfile(source, assets / key)
        route_map[f"{parsed.hostname}{parsed.path}"] = key
        staged_routes[url] = assets / key
    script = root / "feed.py"
    dvr_responses = {
        "/status": {"version": "mock-1.0", "name": "Capability DVR"},
        "/disk": {"path": "/shares/mock", "free": 120 * 1024**3, "total": 200 * 1024**3, "used": 80 * 1024**3},
        "/channels": [{"number": "100", "name": "Channel 100", "logo_url": ""}],
        "/api/v1/channels": [{"number": "100", "name": "Channel 100", "logo_url": ""}],
        "/streams": {"count": 0, "active": []},
        "/dvr": {
            "name": "Capability DVR",
            "path": "/shares/mock",
            "disk": {"path": "/shares/mock", "free": 120 * 1024**3, "total": 200 * 1024**3, "used": 80 * 1024**3},
            "streams": {"count": 0, "active": []},
        },
        "/api/v1/all": [],
        "/api/v1/jobs": [],
    }
    script.write_text(
        "import http.server,json,ssl,threading,time\n"
        "from pathlib import Path\n"
        f"ROUTES={route_map!r}\n"
        f"DVR_RESPONSES={dvr_responses!r}\n"
        "class FeedHandler(http.server.BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  key=ROUTES.get(self.headers.get('Host','').split(':',1)[0]+self.path.split('?',1)[0])\n"
        "  if key is None: self.send_error(404); return\n"
        "  data=(Path('/feed/assets')/key).read_bytes(); self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)\n"
        " def log_message(self,fmt,*args): print(fmt%args,flush=True)\n"
        "class DvrHandler(http.server.BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  path=self.path.split('?',1)[0]\n"
        "  if path in ('/events','/dvr/events/subscribe'):\n"
        "   self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.end_headers()\n"
        "   try:\n"
        "    self.wfile.write(b'data: {\"Type\":\"hello\",\"Name\":\"hello\",\"Value\":\"ready\"}\\n\\n');self.wfile.flush()\n"
        "    while True:self.wfile.write(b': keepalive\\n\\n');self.wfile.flush();time.sleep(.5)\n"
        "   except (BrokenPipeError,ConnectionResetError,OSError):pass\n"
        "   return\n"
        "  value=DVR_RESPONSES.get(path)\n"
        "  if value is None:self.send_error(404);return\n"
        "  data=json.dumps(value,separators=(',',':')).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)\n"
        " def log_message(self,fmt,*args): print(fmt%args,flush=True)\n"
        "dvr=http.server.ThreadingHTTPServer(('0.0.0.0',8089),DvrHandler)\n"
        "threading.Thread(target=dvr.serve_forever,daemon=True).start()\n"
        "server=http.server.ThreadingHTTPServer(('0.0.0.0',443),FeedHandler)\n"
        "ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain('/feed/server.pem','/feed/server-key.pem');server.socket=ctx.wrap_socket(server.socket,server_side=True);server.serve_forever()\n",
        encoding="utf-8",
    )
    return staged_routes


class ApiClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url
        self.username = username
        self.password = password
        self.csrf = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def set_base_url(self, base_url: str) -> None:
        self.base_url = base_url

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        body = json.dumps(payload).encode() if payload is not None else None
        with self.opener.open(
            urllib.request.Request(self.base_url + path, data=body, headers=headers), timeout=20
        ) as response:
            return json.loads(response.read() or b"{}")

    def login(self) -> None:
        response = self.request(
            "/api/v1/auth/login", {"username": self.username, "password": self.password}
        )
        self.csrf = str(response.get("csrf_token") or "")
        if not self.csrf:
            raise CapabilityError("Synthetic administrator login did not return CSRF state.")


def wait_until(callback, timeout: int, description: str):
    deadline = time.monotonic() + timeout
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            value = callback()
            if value:
                return value
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last = exc
        time.sleep(1)
    raise CapabilityError(f"Timed out waiting for {description}: {type(last).__name__ if last else 'no matching state'}")


def published_base_url(name: str) -> str:
    output = run(["docker", "port", name, "8501/tcp"]).stdout.strip()
    try:
        port = int(output.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise CapabilityError("Docker did not publish the ChannelWatch HTTP port.") from exc
    if not 1 <= port <= 65535:
        raise CapabilityError("Docker published an invalid ChannelWatch HTTP port.")
    return f"http://127.0.0.1:{port}"


def apply_once_and_wait(
    client: ApiClient,
    endpoint: str,
    *,
    version: str,
    job_id: str,
    timeout: int,
) -> dict[str, Any]:
    try:
        response = client.request(endpoint, {"version": version, "job_id": job_id})
        if response.get("job_id") != job_id:
            raise CapabilityError("Apply response did not bind the requested job ID.")
    except (TimeoutError, ConnectionError, urllib.error.URLError):
        pass

    def completed():
        try:
            job = client.request(f"/api/v1/update/jobs/{job_id}")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 404, 503}:
                if exc.code == 401:
                    client.login()
                return None
            raise
        status = str(job.get("status") or "")
        if status in TERMINAL_FAILURES:
            raise CapabilityError(f"Runtime update job failed: {status}")
        return job if status == "success" else None

    return wait_until(completed, timeout, f"job {job_id}")


def wait_for_successful_job(
    client: ApiClient, *, job_id: str, timeout: int, description: str
) -> dict[str, Any]:
    def completed():
        try:
            job = client.request(f"/api/v1/update/jobs/{job_id}")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 404, 503}:
                if exc.code == 401:
                    client.login()
                return None
            raise
        status = str(job.get("status") or "")
        if status in TERMINAL_FAILURES:
            raise CapabilityError(f"{description} failed: {status}")
        return job if status == "success" else None

    return wait_until(completed, timeout, description)


def docker_exec_json(name: str, code: str) -> dict[str, Any]:
    result = run(
        ["docker", "exec", name, "/usr/local/bin/python", "-I", "-B", "-S", "-c", code],
        timeout=120,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def persistent_hashes(name: str) -> dict[str, str]:
    code = """
import hashlib,json
from pathlib import Path
paths=[Path('/config/settings.json'),Path('/config/encryption.key')]
out={}
for path in paths:
 if not path.exists(): raise SystemExit('missing preservation path: '+str(path))
 out[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(out,sort_keys=True))
"""
    return {str(key): str(value) for key, value in docker_exec_json(name, code).items()}


def image_inventory(
    name: str,
    *,
    bootstrap_paths: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    required_paths = IMAGE_BOOTSTRAP_PATHS if bootstrap_paths is None else bootstrap_paths
    code = f"""
import hashlib,json,os,stat
from pathlib import Path,PurePosixPath
roots=[Path('/venv')]
required={list(required_paths)!r}
paths=[]
for root in roots:
 paths.append(root)
 for current,dirs,files in os.walk(root,topdown=True,followlinks=False):
  base=Path(current)
  paths.extend(base/name for name in dirs)
  paths.extend(base/name for name in files)
paths.extend(Path(value) for value in required)
out=[]; regular_bytes=0; seen=set()
for path in sorted(paths,key=lambda value:str(value)):
 absolute=str(path)
 pure=PurePosixPath(absolute)
 if not pure.is_absolute() or '..' in pure.parts or '\\\\' in absolute: raise SystemExit('invalid inventory path: '+absolute)
 if absolute in seen: raise SystemExit('duplicate inventory path: '+absolute)
 seen.add(absolute)
 try: meta=path.lstat()
 except OSError as exc: raise SystemExit('unreadable inventory path: '+absolute) from exc
 record={{'path':absolute,'mode':format(stat.S_IMODE(meta.st_mode),'04o'),'size':meta.st_size}}
 if stat.S_ISREG(meta.st_mode):
  digest=hashlib.sha256()
  with path.open('rb') as handle:
   for chunk in iter(lambda:handle.read(1048576),b''): digest.update(chunk)
  record.update(type='file',sha256=digest.hexdigest()); regular_bytes+=meta.st_size
 elif stat.S_ISDIR(meta.st_mode): record.update(type='directory')
 elif stat.S_ISLNK(meta.st_mode):
  target=os.readlink(path); resolved=(path.parent/target).resolve(strict=False)
  allowed_external={{
   '/venv/bin/python':Path('/usr/local/bin/python3.14'),
   '/venv/bin/python3':Path('/usr/local/bin/python3.14'),
   '/venv/bin/python3.14':Path('/usr/local/bin/python3.14'),
   '/venv/bin/𝜋thon':Path('/usr/local/bin/python3.14'),
  }}
  inside=resolved == Path('/venv') or Path('/venv') in resolved.parents
  if absolute.startswith('/venv/') and not inside and resolved != allowed_external.get(absolute):
   raise SystemExit('escaping inventory symlink: '+absolute)
  record.update(type='symlink',link_target=target)
 else: raise SystemExit('unsupported inventory path type: '+absolute)
 out.append(record)
if len(out)>{MAX_IMAGE_INVENTORY_PATHS!r}: raise SystemExit('image inventory path limit exceeded')
if regular_bytes>{MAX_IMAGE_INVENTORY_BYTES!r}: raise SystemExit('image inventory byte limit exceeded')
encoded=json.dumps(out,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
print(json.dumps({{'sha256':hashlib.sha256(encoded).hexdigest(),'path_count':len(out),'regular_file_bytes':regular_bytes,'entries':out}},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False))
"""
    result = docker_exec_json(name, code)
    if (
        not isinstance(result, dict)
        or set(result) != {"sha256", "path_count", "regular_file_bytes", "entries"}
        or not isinstance(result["entries"], list)
        or result["path_count"] != len(result["entries"])
        or type(result["regular_file_bytes"]) is not int
        or result["regular_file_bytes"] < 0
        or not isinstance(result["sha256"], str)
        or HEX_RE.fullmatch(result["sha256"]) is None
    ):
        raise CapabilityError("Image inventory probe returned malformed evidence.")
    return result


def inventory_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256": value["sha256"],
        "path_count": value["path_count"],
        "regular_file_bytes": value["regular_file_bytes"],
    }


def validate_image_inventory(before: dict[str, Any], after: dict[str, Any]) -> None:
    if not before.get("entries") or before != after:
        raise CapabilityError("Image /venv or bootstrap inventory changed.")


def retain_image_inventory(
    value: dict[str, Any], evidence_path: Path, architecture: str, stage: str
) -> dict[str, Any]:
    path = evidence_path.parent / f"{evidence_path.stem}-{architecture}-image-inventory-{stage}.json"
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"file": path.name, "sha256": sha256_file(path), "size": path.stat().st_size}


def runtime_storage_observation(name: str, generation_path: str | None = None) -> dict[str, int]:
    code = f"""
import json,os
from pathlib import Path
root=Path('/config/channelwatch-runtime')
selected=Path({generation_path!r}) if {generation_path is not None!r} else None
selected_bytes=0
if selected is not None and selected.exists():
 for current,dirs,files in os.walk(selected,followlinks=False):
  for filename in files:
   path=Path(current)/filename
   if not path.is_symlink(): selected_bytes+=path.stat().st_size
stats=os.statvfs(root)
print(json.dumps({{'free_bytes':stats.f_bavail*stats.f_frsize,'selected_generation_bytes':selected_bytes}},sort_keys=True))
"""
    result = docker_exec_json(name, code)
    if set(result) != {"free_bytes", "selected_generation_bytes"} or any(
        type(value) is not int or value < 0 for value in result.values()
    ):
        raise CapabilityError("Runtime storage observation is invalid.")
    return result


class MemorySampler:
    def __init__(self, container: str) -> None:
        self.container = container
        self.samples: list[int] = []
        self.failure: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                output = run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}", self.container],
                    timeout=30,
                ).stdout.strip()
                value = json.loads(output).get("MemUsage", "").split("/", 1)[0].strip()
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMG]iB|B)", value)
                if match is None:
                    raise ValueError("unsupported Docker memory value")
                scale = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}[match.group(2)]
                self.samples.append(int(float(match.group(1)) * scale))
            except (
                CapabilityError,
                subprocess.TimeoutExpired,
                json.JSONDecodeError,
                IndexError,
                KeyError,
                TypeError,
                ValueError,
            ):
                self.failure = "memory_sampling_failed"
                return
            self._stop.wait(0.5)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict[str, int]:
        self._stop.set()
        self._thread.join(timeout=35)
        if self._thread.is_alive() or self.failure or not self.samples:
            raise CapabilityError("Container memory sampling did not produce bounded numeric evidence.")
        return {"sample_count": len(self.samples), "peak_container_memory_bytes": max(self.samples)}

    def cancel(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=35)


def validate_resource_observations(value: object) -> dict[str, int | float]:
    integer_fields = {
        "bundle_bytes", "expanded_bytes", "member_count",
        "selected_generation_disk_bytes", "free_bytes_before", "free_bytes_after",
        "sample_count", "peak_container_memory_bytes",
    }
    duration_fields = {
        "preparation_duration_seconds", "restart_duration_seconds",
        "rollback_duration_seconds", "reapply_duration_seconds",
    }
    if not isinstance(value, dict) or set(value) != integer_fields | duration_fields:
        raise CapabilityError("Resource observations are incomplete.")
    for field in integer_fields:
        if type(value[field]) is not int or value[field] <= 0:
            raise CapabilityError(f"Resource observation {field} is not positive.")
    for field in duration_fields:
        if type(value[field]) not in {int, float} or value[field] < 0:
            raise CapabilityError(f"Resource observation {field} is invalid.")
    return value


def validate_release_log_artifacts(
    artifacts: object, hashes: object, *, architecture: str
) -> None:
    required = {"damaged-native"}
    if architecture == "amd64":
        required.update({"noexec", "low-space", "live-ui"})
    if not isinstance(artifacts, dict) or not isinstance(hashes, dict) or not required.issubset(artifacts):
        raise CapabilityError("Mandatory release probe logs are missing.")
    for suffix in required:
        descriptor = artifacts[suffix]
        if (
            not isinstance(descriptor, dict)
            or set(descriptor) != {"file", "sha256", "size"}
            or not isinstance(descriptor["file"], str)
            or Path(descriptor["file"]).name != descriptor["file"]
            or not isinstance(descriptor["sha256"], str)
            or HEX_RE.fullmatch(descriptor["sha256"]) is None
            or descriptor["sha256"] != hashes.get(suffix)
            or type(descriptor["size"]) is not int
            or descriptor["size"] <= 0
        ):
            raise CapabilityError(f"Release probe log descriptor is invalid: {suffix}")


def serialize_bounded_receipt(receipt: dict[str, Any]) -> bytes:
    receipt["serialized_size_bytes"] = 0
    try:
        for _ in range(4):
            encoded = (
                json.dumps(
                    receipt,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            if len(encoded) == receipt["serialized_size_bytes"]:
                break
            receipt["serialized_size_bytes"] = len(encoded)
        encoded = (
            json.dumps(
                receipt,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CapabilityError("Runtime capability receipt is not valid JSON.") from exc
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise CapabilityError("Runtime capability receipt exceeds the 3.5 MiB per-architecture bound.")
    return encoded


def import_smoke(
    name: str,
    *,
    python: str,
    site_packages: str,
    expected_versions: dict[str, str],
    parser_sha256: str | None,
) -> dict[str, Any]:
    snippet = f"""
import hashlib,importlib.metadata,json,sys
site={site_packages!r}
sys.path.insert(0,site)
import sqlalchemy,httptools,httptools.parser.parser as parser
def record(module,distribution):
 return {{'path':module.__file__,'version':importlib.metadata.version(distribution)}}
out={{'sqlalchemy':record(sqlalchemy,'sqlalchemy'),'httptools':record(httptools,'httptools'),'httptools.parser.parser':record(parser,'httptools')}}
with open(parser.__file__,'rb') as handle: out['httptools.parser.parser']['sha256']=hashlib.sha256(handle.read()).hexdigest()
print(json.dumps(out,sort_keys=True,separators=(',',':')))
"""
    result = run(
        ["docker", "exec", name, python, "-I", "-B", "-S", "-c", snippet],
        timeout=120,
    )
    try:
        imports = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise CapabilityError("Isolated dependency import did not return JSON evidence.") from exc
    if set(imports) != {"sqlalchemy", "httptools", "httptools.parser.parser"}:
        raise CapabilityError("Isolated dependency import evidence is incomplete.")
    prefix = site_packages.rstrip("/") + "/"
    for module in ("sqlalchemy", "httptools", "httptools.parser.parser"):
        record = imports[module]
        distribution = "httptools" if module.startswith("httptools") else module
        if (
            not isinstance(record, dict)
            or record.get("version") != expected_versions[distribution]
            or not str(record.get("path") or "").startswith(prefix)
        ):
            raise CapabilityError(f"Isolated {module} import does not match its expected origin/version.")
    package_path = str(imports["httptools"]["path"])
    parser_path = str(imports["httptools.parser.parser"]["path"])
    if package_path == parser_path or not parser_path.endswith(".so"):
        raise CapabilityError("httptools package and native parser evidence are conflated.")
    if parser_sha256 is not None and imports["httptools.parser.parser"].get("sha256") != parser_sha256:
        raise CapabilityError("Selected httptools parser digest does not match the signed inventory.")
    return imports


def seed_synthetic_rbac_settings(reference: str, volume: str) -> None:
    settings = json.dumps(
        {
            "api_key": "",
            "auth_mode": "rbac",
            "rbac_enabled": True,
            "security_setup_completed": True,
            "dvr_servers": [
                {
                    "id": "capability-dvr",
                    "name": "Capability DVR",
                    "host": "synthetic-dvr",
                    "port": 8089,
                    "enabled": True,
                    "api_key": "",
                }
            ],
        },
        sort_keys=True,
    )
    code = (
        "from pathlib import Path; "
        "p=Path('/config/settings.json'); "
        f"p.write_text({settings!r},encoding='utf-8'); p.chmod(0o600)"
    )
    run(
        [
            "docker", "run", "--rm", "--label", LABEL, "--user", "0:0",
            "--network", "none", "--mount", f"type=volume,source={volume},target=/config",
            "--entrypoint", "/usr/local/bin/python", reference, "-c", code,
        ],
        timeout=60,
    )


def image_supervisor_commands(
    name: str, services: dict[str, dict[str, Any]]
) -> dict[str, str]:
    return {
        component: process["command"]
        for component, process in image_supervisor_processes(name, services).items()
    }


def worker_processes(
    name: str, *, python_executable: str, allow_absent: bool = False
) -> dict[str, dict[str, Any]]:
    # Supervisor and worker diagnostics use container PIDs; docker top uses host PIDs.
    code = f"""
import json
from pathlib import Path
matches={{'core': [], 'ui': []}}
for path in Path('/proc').iterdir():
 if not path.name.isdecimal(): continue
 try:
  argv=(path/'cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')
  stat_fields=(path/'stat').read_text(encoding='utf-8').rsplit(')',1)[1].split()
 except (FileNotFoundError, ProcessLookupError, PermissionError): continue
 if not argv or argv[0].decode('utf-8',errors='replace') != {python_executable!r}: continue
 for component in matches:
  marker=b'--mode'
  if any(argv[index] == marker and argv[index+1] == component.encode() for index in range(len(argv)-1)):
   matches[component].append({{'pid':int(path.name),'ppid':int(stat_fields[1]),'command':' '.join(value.decode('utf-8') for value in argv)}})
if {allow_absent!r} and all(not values for values in matches.values()):
 print(json.dumps({{}},sort_keys=True)); raise SystemExit(0)
if any(len(values) != 1 for values in matches.values()): raise SystemExit('expected one worker for each component')
print(json.dumps({{component: values[0] for component,values in matches.items()}},sort_keys=True))
"""
    processes = docker_exec_json(name, code)
    if allow_absent and processes == {}:
        return {}
    if (not isinstance(processes, dict) or set(processes) != {"core", "ui"}
            or any(not isinstance(value, dict) or type(value.get("pid")) is not int
                   or value["pid"] <= 0 or type(value.get("ppid")) is not int
                   or value["ppid"] <= 0 or not isinstance(value.get("command"), str)
                   for value in processes.values())):
        raise CapabilityError("Worker process evidence is invalid.")
    return processes


def image_supervisor_processes(
    name: str, services: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(services, dict)
        or set(services) != {"core", "ui"}
        or any(
            not isinstance(value, dict)
            or set(value) != {"status", "pid", "uptime"}
            or value["status"] != "RUNNING"
            or type(value["pid"]) is not int
            or value["pid"] <= 0
            or not isinstance(value["uptime"], str)
            or not value["uptime"]
            for value in services.values()
        )
        or services["core"]["pid"] == services["ui"]["pid"]
    ):
        raise CapabilityError(
            "Supervisor service evidence is invalid for image launcher inspection."
        )
    expected_pids = {
        component: services[component]["pid"] for component in ("core", "ui")
    }
    code = """
import json
from pathlib import Path
expected={
 'core':['-u','/app/core/runtime_launcher.py','core','--stay-alive'],
 'ui':['-u','/app/core/runtime_launcher.py','ui','--host','0.0.0.0','--port','8501','--log-level','warning'],
}
expected_pids=%r
venv_python=Path('/venv/bin/python').resolve(strict=True)

def identity(path):
 values={}
 for line in (path/'status').read_text(encoding='utf-8').splitlines():
  key,separator,value=line.partition(':')
  if separator and key in {'Name','Pid','PPid','Uid'}:
   values[key]=value.strip()
 return values

supervisor_reasons=[]
try:
 supervisor_argv=[value.decode('utf-8',errors='strict') for value in (Path('/proc/1')/'cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')]
 supervisor_identity=identity(Path('/proc/1'))
except (FileNotFoundError,ProcessLookupError):
 supervisor_reasons.append('process_missing')
except PermissionError:
 supervisor_reasons.append('process_unreadable')
except (UnicodeDecodeError,OSError):
 supervisor_reasons.append('identity_unreadable')
else:
 if supervisor_argv != ['/venv/bin/python','-m','supervisor.supervisord','-c','/tmp/supervisord.conf']: supervisor_reasons.append('argv')
 if supervisor_identity.get('Name') != 'python': supervisor_reasons.append('name')
 if supervisor_identity.get('Pid') != '1': supervisor_reasons.append('pid')
 if supervisor_identity.get('PPid') != '0': supervisor_reasons.append('parent_pid')
 if supervisor_identity.get('Uid','').split() != ['1000']*4: supervisor_reasons.append('uids')
if supervisor_reasons:
 raise SystemExit('image supervisor identity invalid: '+','.join(supervisor_reasons))

processes={}
failures=[]
for component in ('core','ui'):
 pid=expected_pids[component]
 path=Path('/proc')/str(pid)
 reasons=[]
 try:
  argv=[value.decode('utf-8',errors='strict') for value in (path/'cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')]
  process_identity=identity(path)
 except (FileNotFoundError,ProcessLookupError):
  reasons.append('process_missing')
 except PermissionError:
  reasons.append('process_unreadable')
 except UnicodeDecodeError:
  reasons.append('argv_encoding')
 except OSError:
  reasons.append('identity_unreadable')
 if not reasons:
  if not argv or argv[0] != 'python': reasons.append('argv0')
  if argv[1:] != expected[component]: reasons.append('argv_suffix')
  if process_identity.get('Name') != 'python': reasons.append('name')
  if process_identity.get('Pid') != str(pid): reasons.append('pid')
  if process_identity.get('PPid') != '1': reasons.append('parent_pid')
  if process_identity.get('Uid','').split() != ['1000']*4: reasons.append('uids')
 try:
  executable=(path/'exe').resolve(strict=True)
 except PermissionError:
  pass
 except (FileNotFoundError,ProcessLookupError):
  if 'process_missing' not in reasons: reasons.append('process_missing')
 except OSError:
  reasons.append('executable_unreadable')
 else:
  if executable != venv_python: reasons.append('executable')
 if reasons:
  failures.append(component+':'+','.join(reasons))
 else:
  processes[component]={'pid':pid,'command':' '.join(['/venv/bin/python',*expected[component]])}
if failures:
 raise SystemExit('image launcher identity invalid: '+';'.join(failures))
print(json.dumps(processes,sort_keys=True))
""" % expected_pids
    processes = docker_exec_json(name, code)
    if (
        not isinstance(processes, dict)
        or set(processes) != {"core", "ui"}
        or any(
            not isinstance(value, dict)
            or set(value) != {"pid", "command"}
            or type(value["pid"]) is not int
            or value["pid"] <= 0
            or not isinstance(value["command"], str)
            for value in processes.values()
        )
    ):
        raise CapabilityError("Image launcher process evidence is invalid.")
    return processes

def selected_supervisor_processes(
    name: str, descriptor: dict[str, Any], *, allow_absent: bool = False
) -> dict[str, dict[str, Any]]:
    executable = (
        f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
        "/environment/bin/python"
    )
    return worker_processes(
        name, python_executable=executable, allow_absent=allow_absent
    )


def worker_pid_scan(name: str, *, python_executable: str) -> dict[str, list[int]]:
    code = f"""
import json
from pathlib import Path
matches={{'core': [], 'ui': []}}
for path in Path('/proc').iterdir():
 if not path.name.isdecimal(): continue
 try: argv=(path/'cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')
 except (FileNotFoundError, ProcessLookupError, PermissionError): continue
 if not argv or argv[0].decode('utf-8',errors='replace') != {python_executable!r}: continue
 for component in matches:
  if any(argv[index] == b'--mode' and argv[index+1] == component.encode() for index in range(len(argv)-1)):
   matches[component].append(int(path.name))
print(json.dumps(matches,sort_keys=True))
"""
    value = docker_exec_json(name, code)
    if (
        not isinstance(value, dict)
        or set(value) != {"core", "ui"}
        or any(
            not isinstance(pids, list)
            or any(type(pid) is not int or pid <= 0 for pid in pids)
            for pids in value.values()
        )
    ):
        raise CapabilityError("Worker PID scan evidence is invalid.")
    return value


class ForbiddenSelectedProcessMonitor:
    def __init__(
        self,
        container: str,
        descriptor: dict[str, Any],
        allowed_pids: dict[str, int],
    ) -> None:
        self.container = container
        self.python_executable = (
            f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
            "/environment/bin/python"
        )
        if set(allowed_pids) != {"core", "ui"}:
            raise CapabilityError("Damaged-native monitor requires exact core/UI PIDs.")
        self.allowed_pids = allowed_pids
        self.barrier_observed = False
        self.forbidden_pids: set[int] = set()
        self.successful_sample_count = 0
        self.sampling_error_count = 0
        self.post_barrier_sample_count = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _record(self, scan: dict[str, list[int]]) -> None:
        self.successful_sample_count += 1
        current = {pid for pids in scan.values() for pid in pids}
        if not current:
            if self.barrier_observed:
                self.post_barrier_sample_count += 1
            self.barrier_observed = True
        elif self.barrier_observed:
            self.forbidden_pids.update(current)
        elif not current.issubset(set(self.allowed_pids.values())):
            self.forbidden_pids.update(current - set(self.allowed_pids.values()))

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                self._record(
                    worker_pid_scan(
                        self.container, python_executable=self.python_executable
                    )
                )
            except (CapabilityError, subprocess.TimeoutExpired):
                self.sampling_error_count += 1
            self._stop.wait(0.05)

    def start(self) -> None:
        initial = worker_pid_scan(
            self.container, python_executable=self.python_executable
        )
        if initial != {
            component: [pid] for component, pid in self.allowed_pids.items()
        }:
            raise CapabilityError("Damaged-native monitor did not bind the running selected workers.")
        self._record(initial)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=35)
        if (
            self._thread.is_alive()
            or not self.barrier_observed
            or self.post_barrier_sample_count < 1
            or self.forbidden_pids
        ):
            raise CapabilityError("Damaged selected generation launched after the restart barrier.")
        return {
            "successful_sample_count": self.successful_sample_count,
            "sampling_error_count": self.sampling_error_count,
            "restart_barrier_observed": True,
            "post_barrier_sample_count": self.post_barrier_sample_count,
            "forbidden_selected_pids": [],
        }


def image_supervisor_services(name: str) -> dict[str, dict[str, Any]]:
    output = run(
        [
            "docker", "exec", name, "/venv/bin/python", "-I", "-B", "-m",
            "supervisor.supervisorctl", "-c", "/tmp/supervisord.conf",
            "status", "core", "ui",
        ],
        timeout=30,
    ).stdout.splitlines()
    services: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"^(core|ui)\s+RUNNING\s+pid\s+(\d+),\s+uptime\s+(.+)$")
    for line in output:
        match = pattern.fullmatch(line.strip())
        if match:
            services[match.group(1)] = {
                "status": "RUNNING",
                "pid": int(match.group(2)),
                "uptime": match.group(3),
            }
    if set(services) != {"core", "ui"} or services["core"]["pid"] == services["ui"]["pid"]:
        raise CapabilityError("Supervisor did not report distinct healthy core/UI services.")
    return services


def validate_image_process_evidence(
    commands: dict[str, str],
    processes: dict[str, dict[str, Any]],
    services: dict[str, dict[str, Any]],
) -> None:
    if set(commands) != {"core", "ui"} or set(processes) != set(commands) or set(services) != set(commands):
        raise CapabilityError("Image process evidence does not cover exact core/UI launchers.")
    for component in commands:
        if processes[component].get("command") != commands[component] or processes[component].get("pid") != services[component].get("pid"):
            raise CapabilityError(f"Image {component} command, PID, and supervisor service do not agree.")


def validate_selected_process_evidence(
    commands: dict[str, str],
    processes: dict[str, dict[str, Any]],
    launchers: dict[str, dict[str, Any]],
    services: dict[str, dict[str, Any]],
) -> None:
    if any(set(value) != {"core", "ui"} for value in (commands, processes, launchers, services)):
        raise CapabilityError("Selected process evidence does not cover exact core/UI workers.")
    if (
        len({processes[component].get("pid") for component in commands}) != 2
        or len({launchers[component].get("pid") for component in commands}) != 2
    ):
        raise CapabilityError("Selected worker and launcher PIDs must be distinct.")
    for component in commands:
        process = processes[component]
        launcher = launchers[component]
        service = services[component]
        if (
            process.get("command") != commands[component]
            or process.get("ppid") != launcher.get("pid")
            or service.get("pid") != launcher.get("pid")
        ):
            raise CapabilityError(
                f"Selected {component} worker, launcher, and supervisor service do not agree."
            )


def image_health(client: ApiClient) -> dict[str, Any]:
    value = {
        "startup": client.request("/healthz/startup"),
        "ready": client.request("/healthz/ready"),
    }
    if value != {
        "startup": {"status": "ready"},
        "ready": {"status": "ready", "ready": True},
    }:
        raise CapabilityError("Image core/UI readiness checks are not both ready.")
    return value


def wait_for_image_health(
    client: ApiClient, timeout: int, description: str
) -> dict[str, Any]:
    return wait_until(lambda: image_health(client), timeout, description)


def capture_image_operational_state(
    name: str,
    client: ApiClient,
    *,
    image_version: str,
    require_idle: bool,
) -> dict[str, Any]:
    status = validate_rollback_status(
        client.request("/api/v1/update/status"),
        target_version=image_version,
        require_idle=require_idle,
    )
    services = image_supervisor_services(name)
    processes = image_supervisor_processes(name, services)
    commands = {
        component: process["command"]
        for component, process in processes.items()
    }
    validate_image_process_evidence(commands, processes, services)
    return {
        "status_worker_scope": "frozen_image_ui_status_interpreter_only",
        "status_worker": status["worker_runtime"],
        "commands": commands,
        "processes": processes,
        "services": services,
        "health": image_health(client),
    }


def capture_recovery_hold_operational_state(
    name: str, client: ApiClient, *, recovery_status: dict[str, Any]
) -> dict[str, Any]:
    services = image_supervisor_services(name)
    processes = image_supervisor_processes(name, services)
    commands = {
        component: process["command"]
        for component, process in processes.items()
    }
    validate_image_process_evidence(commands, processes, services)
    return {
        "status_worker_scope": "frozen_image_ui_status_interpreter_only",
        "status_worker": recovery_status["worker_runtime"],
        "commands": commands,
        "processes": processes,
        "services": services,
        "health": image_health(client),
    }


def capture_selected_operational_state(
    name: str,
    client: ApiClient,
    *,
    descriptor: dict[str, Any],
    inventory: dict[str, Any],
    version: str,
    completed_job: dict[str, Any] | None,
) -> dict[str, Any]:
    processes = selected_supervisor_processes(name, descriptor)
    commands = {
        component: process["command"]
        for component, process in processes.items()
    }
    services = image_supervisor_services(name)
    launchers = image_supervisor_processes(name, services)
    validate_selected_process_evidence(commands, processes, launchers, services)
    status = validate_selected_status(
        client.request("/api/v1/update/status"),
        descriptor=descriptor,
        inventory=inventory,
        version=version,
        ui_pid=processes["ui"]["pid"],
    )
    runtime_components = None
    if completed_job is not None:
        runtime_components = validate_runtime_components(
            completed_job, descriptor, inventory
        )
        for component in ("core", "ui"):
            if runtime_components[component]["process_runtime"]["pid"] != processes[component]["pid"]:
                raise CapabilityError(
                    f"Completed {component} readiness PID is stale or not currently running."
                )
    return {
        "runtime_components": runtime_components,
        "status_worker_scope": "ui_status_handler_only",
        "status_worker": status["worker_runtime"],
        "commands": commands,
        "processes": processes,
        "launchers": launchers,
        "services": services,
        "health": image_health(client),
    }


def artifact_descriptor(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CapabilityError(f"Required evidence artifact is missing: {path.name}")
    return {"file": path.name, "sha256": sha256_file(path), "size": path.stat().st_size}


def capture_container_logs(
    resources: DockerResources,
    *,
    prefix: str,
    app: str,
    feed: str,
    evidence_json: Path,
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, Any]], list[str]]:
    log_hashes: dict[str, str] = {}
    release_log_hashes: dict[str, str] = {}
    release_log_artifacts: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for name in sorted(resources.containers):
        suffix = name.removeprefix(prefix + "-")
        try:
            result = run(
                ["docker", "logs", name], check=False,
                timeout=CLEANUP_COMMAND_TIMEOUT_SECONDS,
            )
        except Exception:
            failures.append(f"docker_logs:{suffix}:capture_error")
            continue
        if result.returncode != 0:
            failures.append(f"docker_logs:{suffix}:capture_failed")
            continue
        if not (result.stdout or result.stderr):
            failures.append(f"docker_logs:{suffix}:empty")
            continue
        try:
            log_path = evidence_json.parent / f"{evidence_json.stem}-{suffix}.log"
            log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
            digest = sha256_file(log_path)
            if name in {app, feed}:
                log_hashes[name.rsplit("-", 1)[-1]] = digest
            else:
                release_log_hashes[suffix] = digest
                release_log_artifacts[suffix] = artifact_descriptor(log_path)
        except (OSError, CapabilityError):
            failures.append(f"docker_logs:{suffix}:artifact_failed")
    return log_hashes, release_log_hashes, release_log_artifacts, failures


def remove_temp_root(path: Path) -> list[str]:
    try:
        shutil.rmtree(path)
    except Exception:
        return ["temp_root:remove_error"]
    if path.exists():
        return ["temp_root:remove_failed"]
    return []


def apply_secondary_failures(receipt: dict[str, Any], failures: list[str]) -> None:
    unique_failures = list(dict.fromkeys(failures))
    if not unique_failures:
        return
    receipt["secondary_failures"] = unique_failures
    if receipt.get("status") == "passed":
        receipt["status"] = "failed"
        architecture = receipt.get("architecture")
        if isinstance(architecture, dict):
            architecture["status"] = "failed"
        receipt["failure"] = "Runtime capability evidence finalization failed."


def validate_same_container_identity(before: object, after: object, label: str) -> str:
    if not isinstance(before, str) or not before or after != before:
        raise CapabilityError(f"{label} replaced its container.")
    return before


def validate_live_ui_evidence(
    value: object,
    *,
    target_version: str,
    image_version: str,
    base_url: str,
    evidence_root: Path,
) -> dict[str, Any]:
    expected = {
        "status": "passed",
        "apply_requests": 1,
        "rollback_requests": 1,
        "target_version": target_version,
        "final_version": image_version,
        "final_active_bundle": None,
        "observed_progress": True,
        "console_errors": [],
        "page_errors": [],
        "final_health": {
            "startup": {"status": "ready"},
            "ready": {"status": "ready", "ready": True},
        },
    }
    required = set(expected) | {
        "page_url", "page_title", "screenshots", "expected_console_events",
    }
    if not isinstance(value, dict) or set(value) != required or any(
        value.get(key) != expected_value for key, expected_value in expected.items()
    ):
        raise CapabilityError("Live UI qualification evidence does not prove the exact lifecycle.")
    expected_console_events = value.get("expected_console_events")
    if (
        not isinstance(expected_console_events, dict)
        or set(expected_console_events) != {
            "unauthenticated_bootstrap",
            "apply_restart_transport",
            "rollback_restart_transport",
        }
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in expected_console_events.values()
        )
    ):
        raise CapabilityError("Live UI expected console events are invalid.")
    if not isinstance(value.get("page_url"), str) or not value["page_url"].startswith(base_url + "/"):
        raise CapabilityError("Live UI qualification page URL is not bound to the probe container.")
    if not isinstance(value.get("page_title"), str) or not value["page_title"].strip():
        raise CapabilityError("Live UI qualification did not record a page title.")
    screenshots = value.get("screenshots")
    if not isinstance(screenshots, dict) or set(screenshots) != {"selected", "rollback"}:
        raise CapabilityError("Live UI qualification screenshot evidence is incomplete.")
    retained_screenshots: dict[str, dict[str, Any]] = {}
    for stage, expected_name in {
        "selected": "runtime-update-selected.png",
        "rollback": "runtime-update-rollback.png",
    }.items():
        record = screenshots.get(stage)
        descriptor = artifact_descriptor(evidence_root / expected_name)
        if (
            not isinstance(record, dict)
            or record.get("file") != expected_name
            or record.get("sha256") != descriptor["sha256"]
            or record.get("size") != descriptor["size"]
        ):
            raise CapabilityError(f"Live UI {stage} screenshot descriptor is invalid.")
        retained_screenshots[stage] = descriptor
    return {
        **expected,
        "page_url": value["page_url"],
        "page_title": value["page_title"],
        "screenshots": retained_screenshots,
        "expected_console_events": expected_console_events,
    }


def live_ui_qualification(
    *,
    resources: DockerResources,
    reference: str,
    architecture: str,
    network: str,
    ca_path: Path,
    username: str,
    password: str,
    target_version: str,
    image_version: str,
    evidence_json: Path,
    startup_timeout: int,
    update_timeout: int,
) -> dict[str, Any]:
    if architecture != "amd64":
        raise CapabilityError("The live headless UI qualification is restricted to native AMD64.")
    name = resources.container("live-ui")
    volume = resources.create_volume("live-ui-config")
    seed_synthetic_rbac_settings(reference, volume)
    start_probe_app(
        name=name,
        reference=reference,
        architecture=architecture,
        network=network,
        volume=volume,
        ca_path=ca_path,
        username=username,
        password=password,
    )
    client = ApiClient(published_base_url(name), username, password)
    wait_for_image_health(client, startup_timeout, "live UI probe readiness")
    client.login()
    initial_operational_state = capture_image_operational_state(
        name, client, image_version=image_version, require_idle=True
    )
    container_id_before = run(
        ["docker", "inspect", "--format", "{{.Id}}", name]
    ).stdout.strip()
    if not container_id_before:
        raise CapabilityError("Live UI probe has no container identity.")
    evidence_root = evidence_json.parent
    for artifact_name in (
        "runtime-update-live.json",
        "runtime-update-selected.png",
        "runtime-update-rollback.png",
    ):
        (evidence_root / artifact_name).unlink(missing_ok=True)
    log_path = evidence_json.parent / f"{evidence_json.stem}-live-ui-playwright.log"
    failure_output = evidence_root / "runtime-update-live-failure"
    if failure_output.exists():
        shutil.rmtree(failure_output)
    environment = os.environ.copy()
    environment.update(
        {
            "CHANNELWATCH_PLAYWRIGHT_BASE_URL": client.base_url,
            "CHANNELWATCH_PLAYWRIGHT_ADMIN_USERNAME": username,
            "CHANNELWATCH_PLAYWRIGHT_ADMIN_PASSWORD": password,
            "CHANNELWATCH_PLAYWRIGHT_TARGET_VERSION": target_version,
            "CHANNELWATCH_PLAYWRIGHT_IMAGE_VERSION": image_version,
            "CHANNELWATCH_PLAYWRIGHT_EVIDENCE_DIR": str(evidence_root),
            "CHANNELWATCH_PLAYWRIGHT_HEALTH_TIMEOUT_MS": str(
                startup_timeout * 1000
            ),
            "PLAYWRIGHT_HTML_OPEN": "never",
        }
    )
    result = subprocess.run(  # nosec B603 - fixed repository-owned Playwright argv
        [
            "pnpm",
            "exec",
            "playwright",
            "test",
            "-c",
            "playwright.runtime-live.config.ts",
            "playwright/runtime-update-live-smoke.spec.ts",
            "--project=runtime-live",
            "--reporter=line",
            "--output",
            str(failure_output),
        ],
        cwd=ROOT / "app/ui",
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=(2 * update_timeout) + startup_timeout,
    )
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise CapabilityError(
            "Authenticated headless live UI qualification failed; inspect the retained Playwright log."
        )
    raw_path = evidence_root / "runtime-update-live.json"
    if not raw_path.is_file() or raw_path.stat().st_size >= 64 * 1024:
        raise CapabilityError("Live UI qualification did not emit bounded JSON evidence.")
    try:
        value = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CapabilityError("Live UI qualification emitted invalid JSON evidence.") from exc
    validated = validate_live_ui_evidence(
        value,
        target_version=target_version,
        image_version=image_version,
        base_url=client.base_url,
        evidence_root=evidence_root,
    )
    container_id_after = run(
        ["docker", "inspect", "--format", "{{.Id}}", name]
    ).stdout.strip()
    validate_same_container_identity(
        container_id_before, container_id_after, "Live UI lifecycle"
    )
    wait_for_image_health(client, startup_timeout, "live UI final readiness")
    client.login()
    final_operational_state = capture_image_operational_state(
        name, client, image_version=image_version, require_idle=True
    )
    return {
        **validated,
        "container_id_before": container_id_before,
        "container_id_after": container_id_after,
        "initial_operational_state": initial_operational_state,
        "final_operational_state": final_operational_state,
        "evidence_json": artifact_descriptor(raw_path),
        "playwright_log": artifact_descriptor(log_path),
    }


def runtime_info(name: str, *, image_version: str) -> dict[str, Any]:
    result = run(
        ["docker", "exec", name, "/usr/local/bin/channelwatch", "doctor", "runtime-info"],
        timeout=60,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CapabilityError("Image runtime-info did not return JSON evidence.") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {
            "diagnostic_source", "application_dir", "application_version",
            "process_runtime", "verified_generation",
        }
        or value.get("diagnostic_source") != "image"
        or value.get("application_dir") != "/app"
        or value.get("application_version") != image_version
        or value.get("verified_generation") is not None
    ):
        raise CapabilityError("Image runtime-info evidence is invalid.")
    process = value["process_runtime"]
    dependencies = process.get("dependencies") if isinstance(process, dict) else None
    if (
        not isinstance(process, dict)
        or set(process) != {
            "pid", "python_executable", "python_version", "python_prefix",
            "dependencies",
        }
        or type(process["pid"]) is not int
        or process["pid"] <= 0
        or process["python_executable"] != "/venv/bin/python"
        or process["python_version"] != "3.14.8"
        or process["python_prefix"] != "/venv"
        or not isinstance(dependencies, dict)
        or set(dependencies) != IMAGE_DIAGNOSTIC_DEPENDENCIES
    ):
        raise CapabilityError("Image runtime-info process evidence is invalid.")
    for module, record in dependencies.items():
        if not isinstance(record, dict) or record.get("loaded") not in {True, False}:
            raise CapabilityError(f"Image runtime-info dependency is invalid: {module}")
        if record["loaded"] is False:
            if record != {"loaded": False}:
                raise CapabilityError(f"Image runtime-info dependency is invalid: {module}")
            continue
        if (
            set(record) != {"loaded", "path", "version"}
            or not str(record["path"]).startswith(
                "/venv/lib/python3.14/site-packages/"
            )
            or not isinstance(record["version"], str)
        ):
            raise CapabilityError(f"Image runtime-info dependency is invalid: {module}")
    return value


def start_probe_app(
    *,
    name: str,
    reference: str,
    architecture: str,
    network: str,
    volume: str,
    ca_path: Path,
    username: str,
    password: str,
    tmpfs: str | None = None,
) -> None:
    command = [
        "docker", "run", "-d", "--name", name, "--label", LABEL,
        "--network", network, "--platform", f"linux/{architecture}",
        "--mount", f"type=volume,source={volume},target=/config",
        "--mount", f"type=bind,source={ca_path},target=/canary/ca.pem,readonly",
        "--env", "SSL_CERT_FILE=/canary/ca.pem", "--env", f"CW_ADMIN_USER={username}",
        "--env", f"CW_ADMIN_PASS={password}", "--env", "PUID=1000", "--env", "PGID=1000",
        "-p", "127.0.0.1::8501",
    ]
    if tmpfs is not None:
        command.extend(["--tmpfs", f"/config/channelwatch-runtime:{tmpfs}"])
    command.append(reference)
    run(command)


def control_state(name: str) -> dict[str, Any]:
    code = """
import hashlib,json,stat
from pathlib import Path
root=Path('/config/channelwatch-runtime')
names=['active.json','rollback.json','restart-required.json','restart-required.lock','restart-services-accepted.json','activation-pending.json','activation-core-ready.json','activation-ui-ready.json']
out={}
for filename in names:
 path=root/filename
 try: metadata=path.lstat()
 except FileNotFoundError: out[filename]={'type':'absent'}; continue
 if not stat.S_ISREG(metadata.st_mode): raise SystemExit('unsafe control object: '+filename)
 raw=path.read_bytes()
 after=path.lstat()
 if (metadata.st_dev,metadata.st_ino,metadata.st_size)!=(after.st_dev,after.st_ino,after.st_size): raise SystemExit('control object changed: '+filename)
 out[filename]={'type':'file','mode':format(stat.S_IMODE(metadata.st_mode),'04o'),'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
print(json.dumps(out,sort_keys=True))
"""
    value = docker_exec_json(name, code)
    if not isinstance(value, dict) or set(value) != {
        "active.json", "rollback.json", "restart-required.json",
        "restart-required.lock", "restart-services-accepted.json",
        "activation-pending.json", "activation-core-ready.json",
        "activation-ui-ready.json",
    }:
        raise CapabilityError("Runtime control state evidence is incomplete.")
    for filename, record in value.items():
        if record == {"type": "absent"}:
            continue
        if (
            not isinstance(record, dict)
            or set(record) != {"type", "mode", "size", "sha256"}
            or record["type"] != "file"
            or not isinstance(record["mode"], str)
            or re.fullmatch(r"0[0-7]{3}", record["mode"]) is None
            or type(record["size"]) is not int
            or record["size"] < 0
            or not isinstance(record["sha256"], str)
            or HEX_RE.fullmatch(record["sha256"]) is None
        ):
            raise CapabilityError(f"Runtime control state record is unsafe: {filename}")
    return value


def recovery_control_script(root: str = "/config/channelwatch-runtime") -> str:
    return """
import hashlib,json,os,stat
from pathlib import Path
root=Path(__ROOT__)
names=['active.json','rollback.json','update-job.json','official-recovery-mode.json']
out={}
for filename in names:
 path=root/filename
 flags=os.O_RDONLY|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0)
 try: fd=os.open(path,flags)
 except FileNotFoundError: out[filename]={'type':'absent'}; continue
 try:
  before=os.fstat(fd)
  if not stat.S_ISREG(before.st_mode): raise SystemExit('unsafe recovery control object: '+filename)
  if before.st_size<=0 or before.st_size>1048576: raise SystemExit('recovery control object exceeds its bound: '+filename)
  chunks=[]; remaining=1048577
  while remaining:
   chunk=os.read(fd,min(65536,remaining))
   if not chunk: break
   chunks.append(chunk); remaining-=len(chunk)
  raw=b''.join(chunks)
  after=os.fstat(fd)
  if len(raw)>1048576 or len(raw)!=before.st_size: raise SystemExit('recovery control object exceeds its bound: '+filename)
  if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns): raise SystemExit('recovery control object changed: '+filename)
 finally: os.close(fd)
 payload=json.loads(raw)
 if not isinstance(payload,dict): raise SystemExit('recovery control object is not a mapping: '+filename)
 out[filename]={'type':'file','mode':format(stat.S_IMODE(before.st_mode),'04o'),'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'payload':payload}
print(json.dumps(out,sort_keys=True))
""".replace("__ROOT__", repr(root))


def recovery_control_state(name: str) -> dict[str, Any]:
    value = docker_exec_json(name, recovery_control_script())
    names = {
        "active.json", "rollback.json", "update-job.json",
        "official-recovery-mode.json",
    }
    if not isinstance(value, dict) or set(value) != names:
        raise CapabilityError("Recovery control state evidence is incomplete.")
    for filename, record in value.items():
        if record == {"type": "absent"}:
            continue
        if (
            not isinstance(record, dict)
            or set(record) != {"type", "mode", "size", "sha256", "payload"}
            or record.get("type") != "file"
            or not isinstance(record.get("mode"), str)
            or re.fullmatch(r"0[0-7]{3}", record["mode"]) is None
            or type(record.get("size")) is not int
            or record["size"] <= 0
            or not isinstance(record.get("sha256"), str)
            or HEX_RE.fullmatch(record["sha256"]) is None
            or not isinstance(record.get("payload"), dict)
        ):
            raise CapabilityError(f"Recovery control state record is unsafe: {filename}")
    return value


def retain_recovery_diagnostic(
    evidence_json: Path,
    *,
    phase: str,
    status: dict[str, Any],
    control: dict[str, Any],
) -> dict[str, Any]:
    path = evidence_json.parent / f"{evidence_json.stem}-damaged-native-{phase}.json"
    payload = {
        "schema": 1,
        "phase": phase,
        "status": status,
        "control": control,
    }
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise CapabilityError(
            "Damaged-native recovery diagnostic is not valid JSON."
        ) from exc
    encoded_bytes = encoded.encode("utf-8")
    if len(encoded_bytes) > MAX_RECOVERY_DIAGNOSTIC_BYTES:
        raise CapabilityError("Damaged-native recovery diagnostic exceeds its bound.")
    path.write_bytes(encoded_bytes)
    return artifact_descriptor(path)


def compact_recovery_diagnostic_evidence(result: dict[str, Any]) -> dict[str, Any]:
    if not RECOVERY_DIAGNOSTIC_VALUE_FIELDS.issubset(result):
        raise CapabilityError("Damaged-native recovery diagnostic evidence is incomplete.")
    return {
        key: value
        for key, value in result.items()
        if key not in RECOVERY_DIAGNOSTIC_VALUE_FIELDS
    }


def validate_no_restart_transients(state: dict[str, Any]) -> None:
    for filename in (
        "restart-required.json",
        "restart-services-accepted.json",
        "activation-pending.json",
        "activation-core-ready.json",
        "activation-ui-ready.json",
    ):
        if state.get(filename) != {"type": "absent"}:
            raise CapabilityError(f"Refusal left transient runtime control state: {filename}")


def filesystem_state(name: str) -> dict[str, Any]:
    code = """
import json,os,stat
from pathlib import Path
path=Path('/config/channelwatch-runtime')
meta=path.stat(); fs=os.statvfs(path)
mount=''
for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
 fields=line.split()
 if len(fields)>5 and fields[4]=='/config/channelwatch-runtime': mount=line
if not mount: raise SystemExit('runtime mount not found')
left,right=mount.split(' - ',1); left_fields=left.split(); right_fields=right.split()
print(json.dumps({'mount_type':right_fields[0],'mount_options':sorted(set(left_fields[5].split(',')) | set(right_fields[2].split(','))),'uid':meta.st_uid,'gid':meta.st_gid,'mode':format(stat.S_IMODE(meta.st_mode),'04o'),'free_bytes':fs.f_bavail*fs.f_frsize},sort_keys=True))
"""
    value = docker_exec_json(name, code)
    if (
        not isinstance(value, dict)
        or set(value) != {"mount_type", "mount_options", "uid", "gid", "mode", "free_bytes"}
        or value["mount_type"] != "tmpfs"
        or not isinstance(value["mount_options"], list)
        or type(value["free_bytes"]) is not int
        or value["free_bytes"] < 0
    ):
        raise CapabilityError("Filesystem refusal mount evidence is invalid.")
    return value


def expected_refusal_apply_error(
    error: urllib.error.HTTPError, *, image_version: str
) -> bool:
    if image_version != "1.5.3" or error.code != 500:
        return False
    content_type = (
        str(error.headers.get("Content-Type", "")).split(";", 1)[0].strip()
    )
    if content_type != "application/json":
        return False
    try:
        raw = error.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            return False
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if (
        not isinstance(detail, dict)
        or detail.get("code") != "ERR_UPDATE_APPLY_FAILED"
    ):
        return False
    return detail.get("message") in {
        "Runtime storage does not permit execution.",
        "Runtime storage does not have enough free space for this generation.",
    }


def expect_apply_refusal(
    client: ApiClient,
    *,
    container: str,
    version: str,
    image_version: str,
    job_id: str,
    kind: str,
    bundle_sha256: str,
    timeout: int,
    initial_operational_state: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    health_samples = [{
        "phase": "before",
        **initial_operational_state,
    }]
    try:
        response = client.request(
            "/api/v1/update/apply", {"version": version, "job_id": job_id}
        )
    except urllib.error.HTTPError as exc:
        if not expected_refusal_apply_error(exc, image_version=image_version):
            raise
        response = {"job_id": job_id}
    if response.get("job_id") != job_id:
        raise CapabilityError("Refusal probe did not bind the requested job ID.")
    health_samples.append({
        "phase": "during",
        **capture_image_operational_state(
            container, client, image_version=image_version, require_idle=False
        ),
    })
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.request(f"/api/v1/update/jobs/{job_id}")
        status = str(job.get("status") or "")
        if status in TERMINAL_FAILURES:
            job = validate_refusal_terminal_job(
                job,
                kind=kind,
                job_id=job_id,
                version=version,
                bundle_sha256=bundle_sha256,
            )
            health_samples.append({
                "phase": "after",
                **capture_image_operational_state(
                    container, client, image_version=image_version, require_idle=True
                ),
            })
            return job, health_samples
        if status == "success":
            raise CapabilityError("Filesystem refusal probe unexpectedly activated the candidate.")
        time.sleep(0.25)
    raise CapabilityError("Filesystem refusal probe did not reach terminal failure.")


def damage_native_file(
    name: str, *, path: str, expected_sha256: str
) -> dict[str, str]:
    code = f"""
import hashlib,json,os,stat
path={path!r}; expected={expected_sha256!r}
metadata=os.lstat(path)
if not stat.S_ISREG(metadata.st_mode): raise SystemExit('native proof target is not regular')
with open(path,'r+b',buffering=0) as handle:
 raw=handle.read()
 before=hashlib.sha256(raw).hexdigest()
 if before != expected or not raw: raise SystemExit('native proof target did not match signed digest')
 handle.seek(0); handle.write(bytes([raw[0]^255])); handle.flush(); os.fsync(handle.fileno())
after=hashlib.sha256(open(path,'rb').read()).hexdigest()
if after == expected: raise SystemExit('native proof target was not damaged')
print(json.dumps({{'path':path,'signed_sha256':expected,'before_sha256':before,'damaged_sha256':after}},sort_keys=True))
"""
    value = docker_exec_json(name, code)
    if (
        not isinstance(value, dict)
        or set(value) != {"path", "signed_sha256", "before_sha256", "damaged_sha256"}
        or value["path"] != path
        or value["signed_sha256"] != expected_sha256
        or value["before_sha256"] != expected_sha256
        or not isinstance(value["damaged_sha256"], str)
        or HEX_RE.fullmatch(value["damaged_sha256"]) is None
        or value["damaged_sha256"] == expected_sha256
    ):
        raise CapabilityError("Damaged-native pre-execution proof is invalid.")
    return value


def validate_refusal_terminal_job(
    job: object,
    *,
    kind: str,
    job_id: str,
    version: str,
    bundle_sha256: str,
) -> dict[str, Any]:
    reason = {
        "noexec": "Runtime storage does not permit execution.",
        "low_space": "Runtime storage does not have enough free space for this generation.",
    }[kind]
    if (
        not isinstance(job, dict)
        or job.get("job_id") != job_id
        or job.get("operation") != "apply"
        or job.get("status") != "failed"
        or str(job.get("version") or "").lstrip("v") != version
        or job.get("bundle_sha256") != bundle_sha256
        or job.get("rollback_applied") is not False
        or job.get("restart_required") is not False
        or reason not in str(job.get("error") or "")
    ):
        raise CapabilityError(f"{kind} refusal is not bound to the expected pre-handoff failure.")
    return job


def damaged_native_recovery(
    *,
    resources: DockerResources,
    reference: str,
    architecture: str,
    network: str,
    ca_path: Path,
    descriptor: dict[str, Any],
    bundle_sha256: str,
    inventory: dict[str, Any],
    version: str,
    image_version: str,
    startup_timeout: int,
    update_timeout: int,
    evidence_json: Path,
) -> dict[str, Any]:
    volume = resources.create_volume("damaged-native-config")
    name = resources.container("damaged-native")
    seed_synthetic_rbac_settings(reference, volume)
    username = "damage-admin"
    password = secrets.token_urlsafe(36)
    start_probe_app(
        name=name,
        reference=reference,
        architecture=architecture,
        network=network,
        volume=volume,
        ca_path=ca_path,
        username=username,
        password=password,
    )
    client = ApiClient(published_base_url(name), username, password)
    wait_for_image_health(client, startup_timeout, "damaged-native initial readiness")
    client.login()
    initial_operational_state = capture_image_operational_state(
        name, client, image_version=image_version, require_idle=True
    )
    baseline_inventory = image_inventory(name)
    baseline_persistent = persistent_hashes(name)
    container_id = run(["docker", "inspect", "--format", "{{.Id}}", name]).stdout.strip()
    validate_update_check(
        client.request("/api/v1/update/check", {}),
        version=version,
        descriptor=descriptor,
        bundle_sha256=bundle_sha256,
    )
    job_id = secrets.token_hex(16)
    applied = apply_once_and_wait(
        client,
        "/api/v1/update/apply",
        version=version,
        job_id=job_id,
        timeout=update_timeout,
    )
    wait_for_image_health(client, update_timeout, "damaged-native selected readiness")
    selected_state = capture_selected_operational_state(
        name,
        client,
        descriptor=descriptor,
        inventory=inventory,
        version=version,
        completed_job=applied,
    )
    generation = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
    imports = import_smoke(
        name,
        python=f"{generation}/environment/bin/python",
        site_packages=f"{generation}/dependencies/site-packages",
        expected_versions={
            "sqlalchemy": inventory["packages"]["sqlalchemy"]["version"],
            "httptools": inventory["packages"]["httptools"]["version"],
        },
        parser_sha256=inventory["httptools_parser"]["sha256"],
    )
    parser_path = imports["httptools.parser.parser"]["path"]
    parser_record = inventory["httptools_parser"]
    if not parser_path.endswith(
        parser_record["path"].removeprefix("dependencies/site-packages")
    ):
        raise CapabilityError("Damaged-native parser path does not match the signed file record.")
    damage = damage_native_file(
        name,
        path=parser_path,
        expected_sha256=inventory["httptools_parser"]["sha256"],
    )
    monitor = ForbiddenSelectedProcessMonitor(
        name,
        descriptor,
        {
            "core": selected_state["processes"]["core"]["pid"],
            "ui": selected_state["processes"]["ui"]["pid"],
        },
    )
    monitor.start()
    try:
        run(["docker", "restart", name], timeout=120)
        client.set_base_url(published_base_url(name))
        wait_for_image_health(client, update_timeout, "damaged-native image readiness")
        client.login()
        wait_until(
            lambda: client.request("/api/v1/update/status").get("current_version")
            == image_version,
            update_timeout,
            "damaged-native recovery",
        )
    finally:
        no_selected_launch = monitor.stop()
    raw_recovery_status = client.request("/api/v1/update/status")
    recovery_control = recovery_control_state(name)
    recovery_diagnostic = retain_recovery_diagnostic(
        evidence_json,
        phase="recovery-hold",
        status=raw_recovery_status,
        control=recovery_control,
    )
    recovery_status = validate_recovery_hold_status(
        raw_recovery_status,
        descriptor=descriptor,
        version=version,
        image_version=image_version,
        bundle_sha256=bundle_sha256,
        successful_apply_job=applied,
    )
    recovery_marker = validate_recovery_marker(
        recovery_control["official-recovery-mode.json"],
        version=version,
        bundle_sha256=bundle_sha256,
    )
    recovery_state = capture_recovery_hold_operational_state(
        name, client, recovery_status=recovery_status
    )
    if selected_supervisor_processes(name, descriptor, allow_absent=True) != {}:
        raise CapabilityError("Damaged selected generation still has a running worker.")

    rollback_response = validate_rollback_response(
        client.request("/api/v1/update/rollback", {}),
        source_version=version,
        target_version=image_version,
    )
    wait_until(
        lambda: client.request("/api/v1/update/status").get("current_version")
        == image_version,
        update_timeout,
        "damaged-native explicit rollback",
    )
    rollback_job = wait_for_successful_job(
        client,
        job_id=rollback_response["job_id"],
        timeout=update_timeout,
        description="damaged-native rollback job",
    )
    wait_for_image_health(client, update_timeout, "damaged-native rollback readiness")
    client.login()
    raw_rollback_status = client.request("/api/v1/update/status")
    rollback_control = recovery_control_state(name)
    rollback_diagnostic = retain_recovery_diagnostic(
        evidence_json,
        phase="post-rollback",
        status=raw_rollback_status,
        control=rollback_control,
    )
    rollback_status = validate_rollback_status(
        raw_rollback_status, target_version=image_version
    )
    completed_rollback_job = validate_completed_rollback_job(
        rollback_job,
        response=rollback_response,
        successful_apply_job=applied,
        source_version=version,
        image_version=image_version,
    )
    if rollback_status.get("last_job") != completed_rollback_job:
        raise CapabilityError("Durable rollback status did not retain its terminal job.")
    if rollback_control["official-recovery-mode.json"] != {"type": "absent"}:
        raise CapabilityError("Official recovery marker remained after explicit rollback.")
    validate_same_container_identity(
        container_id,
        run(["docker", "inspect", "--format", "{{.Id}}", name]).stdout.strip(),
        "Damaged-native recovery",
    )
    after_inventory = image_inventory(name)
    validate_image_inventory(baseline_inventory, after_inventory)
    after_persistent = persistent_hashes(name)
    validate_preservation(baseline_persistent, after_persistent)
    if selected_supervisor_processes(name, descriptor, allow_absent=True) != {}:
        raise CapabilityError("Damaged selected generation still has a running worker.")
    post_rollback_state = capture_image_operational_state(
        name, client, image_version=image_version, require_idle=True
    )
    recovered_imports = import_smoke(
        name,
        python="/venv/bin/python",
        site_packages="/venv/lib/python3.14/site-packages",
        expected_versions={"sqlalchemy": "2.0.54", "httptools": "0.8.0"},
        parser_sha256=None,
    )
    return {
        "status": "passed",
        "job_id": job_id,
        "runtime_generation_id": descriptor["generation_id"],
        "container_id": container_id,
        "initial_operational_state": initial_operational_state,
        "selected_state_before_damage": selected_state,
        "native_parser": imports["httptools.parser.parser"],
        "damage": damage,
        "no_selected_launch_after_restart": no_selected_launch,
        "successful_apply_job": applied,
        "recovery_hold_status": recovery_status,
        "recovery_marker": recovery_marker,
        "recovery_hold_operational_state": recovery_state,
        "recovery_hold_control_state": recovery_control,
        "recovery_hold_diagnostic": recovery_diagnostic,
        "rollback_response": rollback_response,
        "rollback_status": rollback_status,
        "completed_rollback_job": completed_rollback_job,
        "post_rollback_control_state": rollback_control,
        "post_rollback_diagnostic": rollback_diagnostic,
        "selected_processes_after_recovery": {},
        "post_rollback_operational_state": post_rollback_state,
        "image_imports_after_recovery": recovered_imports,
        "image_inventory": inventory_summary(after_inventory),
        "persistent_hashes": after_persistent,
    }


def filesystem_refusal(
    *,
    kind: str,
    resources: DockerResources,
    reference: str,
    architecture: str,
    network: str,
    ca_path: Path,
    descriptor: dict[str, Any],
    bundle_sha256: str,
    version: str,
    image_version: str,
    startup_timeout: int,
    update_timeout: int,
    bundle_bytes: int,
) -> dict[str, Any]:
    required_bytes = bundle_bytes + int(descriptor["expanded_size"]) + 64 * 1024 * 1024
    if kind == "noexec":
        tmpfs = f"rw,noexec,size={required_bytes + 64 * 1024 * 1024},mode=0755,uid=1000,gid=1000"
    elif kind == "low_space":
        tmpfs_bytes = bundle_bytes + 8 * 1024 * 1024
        if tmpfs_bytes >= required_bytes:
            raise CapabilityError("Low-space probe size does not fall below the preparation requirement.")
        tmpfs = f"rw,exec,size={tmpfs_bytes},mode=0755,uid=1000,gid=1000"
    else:
        raise CapabilityError("Unknown filesystem refusal probe.")
    volume = resources.create_volume(f"{kind.replace('_', '-')}-config")
    name = resources.container(kind.replace("_", "-"))
    seed_synthetic_rbac_settings(reference, volume)
    username = f"{kind}-admin"
    password = secrets.token_urlsafe(36)
    start_probe_app(
        name=name,
        reference=reference,
        architecture=architecture,
        network=network,
        volume=volume,
        ca_path=ca_path,
        username=username,
        password=password,
        tmpfs=tmpfs,
    )
    client = ApiClient(published_base_url(name), username, password)
    wait_for_image_health(client, startup_timeout, f"{kind} initial readiness")
    client.login()
    initial_operational_state = capture_image_operational_state(
        name, client, image_version=image_version, require_idle=True
    )
    mount = filesystem_state(name)
    if kind == "noexec" and "noexec" not in mount["mount_options"]:
        raise CapabilityError("Noexec refusal mount is executable.")
    if kind == "low_space" and mount["free_bytes"] >= required_bytes:
        raise CapabilityError("Low-space refusal mount has enough free space.")
    before_control = control_state(name)
    validate_no_restart_transients(before_control)
    before_inventory = image_inventory(name)
    before_persistent = persistent_hashes(name)
    container_id = run(["docker", "inspect", "--format", "{{.Id}}", name]).stdout.strip()
    validate_update_check(
        client.request("/api/v1/update/check", {}),
        version=version,
        descriptor=descriptor,
        bundle_sha256=bundle_sha256,
    )
    job_id = secrets.token_hex(16)
    job, health_samples = expect_apply_refusal(
        client,
        container=name,
        version=version,
        image_version=image_version,
        job_id=job_id,
        kind=kind,
        bundle_sha256=bundle_sha256,
        timeout=update_timeout,
        initial_operational_state=initial_operational_state,
    )
    status = validate_rollback_status(
        client.request("/api/v1/update/status"), target_version=image_version
    )
    after_control = control_state(name)
    validate_no_restart_transients(after_control)
    if after_control != before_control:
        raise CapabilityError(f"{kind} refusal changed runtime selection/control state.")
    after_inventory = image_inventory(name)
    validate_image_inventory(before_inventory, after_inventory)
    after_persistent = persistent_hashes(name)
    validate_preservation(before_persistent, after_persistent)
    validate_same_container_identity(
        container_id,
        run(["docker", "inspect", "--format", "{{.Id}}", name]).stdout.strip(),
        f"{kind} refusal",
    )
    return {
        "status": "passed",
        "container_id": container_id,
        "initial_operational_state": initial_operational_state,
        "job": job,
        "mount": mount,
        "required_bytes": required_bytes,
        "control_state_before": before_control,
        "control_state_after": after_control,
        "control_state": after_control,
        "health_samples": health_samples,
        "operational_state_after": {
            key: value for key, value in health_samples[-1].items() if key != "phase"
        },
        "rollback_status": compact_filesystem_refusal_status(
            status, target_version=image_version
        ),
        "image_inventory": inventory_summary(after_inventory),
        "persistent_hashes": after_persistent,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--amd64-bundle", type=Path, required=True)
    parser.add_argument("--arm64-bundle", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--minimum-image-version", required=True)
    parser.add_argument("--image-repository", default="coderluii/channelwatch")
    parser.add_argument("--image-index-digest", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--update-timeout", type=int, default=900)
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    bundles = {"amd64": args.amd64_bundle.resolve(), "arm64": args.arm64_bundle.resolve()}
    catalog_bytes, release, variants = verify_assets(
        args.catalog.resolve(), bundles, version=version, source_commit=args.source_commit
    )
    architecture = native_architecture(args.architecture)
    descriptor = variants[architecture]["runtime_descriptor"]
    signed_inventory, normalized_inventory = load_signed_dependency_inventory(
        bundles[architecture], descriptor
    )
    with zipfile.ZipFile(bundles[architecture]) as runtime_bundle:
        bundle_member_count = len(runtime_bundle.infolist())
    descriptor_sha256 = hashlib.sha256(
        json.dumps(
            descriptor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    reference, manifest_digest, image_id, encoded_index, image_inspection = pinned_native_image(
        args.image_repository, args.image_index_digest, architecture
    )
    prefix = f"cw-runtime-cap-{architecture}-{secrets.token_hex(5)}"
    resources = DockerResources(prefix)
    temp_root = Path(tempfile.mkdtemp(prefix=prefix + "-"))
    args.evidence_json.parent.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "schema": 2,
        "status": "running",
        "target_application_version": version,
        "source_commit": args.source_commit,
        "signed_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "bootstrap": {
            "status": "passed",
            "minimum_image_version": args.minimum_image_version.strip().lstrip("v"),
            "launcher_protocol": 4,
            "immutable_bootstrap_sha256": args.image_index_digest.removeprefix("sha256:"),
            "image_index_digest": args.image_index_digest,
        },
        "architecture": {
            "status": "running",
            "architecture": architecture,
            "source_commit": args.source_commit,
            "descriptor_sha256": descriptor_sha256,
            "runtime_generation_id": descriptor["generation_id"],
            "dependency_inventory_sha256": descriptor["dependency_inventory_sha256"],
            "target_variant_bundle_sha256": variants[architecture]["bundle_sha256"],
            "python_implementation": descriptor["python_implementation"],
            "python_version": descriptor["python_version"],
            "python_cache_tag": descriptor["python_cache_tag"],
            "os_family": descriptor["os_family"],
            "libc_family": descriptor["libc_family"],
            "minimum_libc_version": descriptor["minimum_libc_version"],
            "qualification": {},
            "release_qualification": {},
        },
    }
    app = resources.container("app")
    feed = resources.container("feed")
    sampler: MemorySampler | None = None
    try:
        ca_path, _cert_path, _key_path = create_tls_material(temp_root)
        routes = {DEFAULT_CATALOG_URL: args.catalog.resolve()}
        for name in ("amd64", "arm64"):
            routes[variants[name]["bundle_url"]] = bundles[name]
        write_feed(temp_root, routes)
        network = resources.create_network()
        volume = resources.create_volume()
        seed_synthetic_rbac_settings(reference, volume)
        run([
            "docker", "run", "-d", "--name", feed, "--label", LABEL,
            "--network", network, "--network-alias", "github.com", "--network-alias", "channelwatch.coderluii.dev",
            "--network-alias", "synthetic-dvr",
            "--mount", f"type=bind,source={temp_root},target=/feed,readonly",
            "--entrypoint", "/usr/local/bin/python", reference, "/feed/feed.py",
        ])
        username = "capability-admin"
        password = secrets.token_urlsafe(36)
        run([
            "docker", "run", "-d", "--name", app, "--label", LABEL,
            "--network", network, "--platform", f"linux/{architecture}",
            "--mount", f"type=volume,source={volume},target=/config",
            "--mount", f"type=bind,source={ca_path},target=/canary/ca.pem,readonly",
            "--env", "SSL_CERT_FILE=/canary/ca.pem", "--env", f"CW_ADMIN_USER={username}",
            "--env", f"CW_ADMIN_PASS={password}", "--env", "PUID=1000", "--env", "PGID=1000",
            "-p", "127.0.0.1::8501", reference,
        ])
        client = ApiClient(published_base_url(app), username, password)
        wait_for_image_health(client, args.startup_timeout, "initial readiness")
        client.login()
        run(["docker", "restart", app], timeout=120)
        client.set_base_url(published_base_url(app))
        wait_for_image_health(client, args.startup_timeout, "synthetic RBAC restart readiness")
        client.login()
        initial_status = validate_rollback_status(
            client.request("/api/v1/update/status"),
            target_version=args.minimum_image_version.strip().lstrip("v"),
        )
        image_imports_before = import_smoke(
            app,
            python="/venv/bin/python",
            site_packages="/venv/lib/python3.14/site-packages",
            expected_versions={"sqlalchemy": "2.0.54", "httptools": "0.8.0"},
            parser_sha256=None,
        )
        initial_operational_state = capture_image_operational_state(
            app,
            client,
            image_version=args.minimum_image_version.strip().lstrip("v"),
            require_idle=True,
        )
        image_inventory_before = image_inventory(app)
        inventory_artifacts = {
            "before": retain_image_inventory(
                image_inventory_before, args.evidence_json, architecture, "before"
            )
        }
        before = persistent_hashes(app)
        storage_before = runtime_storage_observation(app)
        container_identity_before = run(
            ["docker", "inspect", "--format", "{{.Id}}", app]
        ).stdout.strip()
        sampler = MemorySampler(app)
        sampler.start()
        checked, checked_artifact = check_update(
            client, args.evidence_json,
            architecture=architecture,
            source_commit=args.source_commit,
            version=version,
            descriptor=descriptor,
            bundle_sha256=variants[architecture]["bundle_sha256"],
        )
        compact_checked = compact_update_check_evidence(checked, checked_artifact)
        job_id = secrets.token_hex(16)
        apply_started = time.monotonic()
        applied = apply_once_and_wait(client, "/api/v1/update/apply", version=version, job_id=job_id, timeout=args.update_timeout)
        apply_duration = time.monotonic() - apply_started
        wait_for_image_health(client, args.update_timeout, "selected Apply readiness")
        apply_operational_state = capture_selected_operational_state(
            app,
            client,
            descriptor=descriptor,
            inventory=normalized_inventory,
            version=version,
            completed_job=applied,
        )
        components = apply_operational_state["runtime_components"]
        generation_root = f"/config/channelwatch-runtime/releases/{descriptor['generation_id']}"
        parser_record = normalized_inventory["httptools_parser"]
        imports = import_smoke(
            app,
            python=f"{generation_root}/environment/bin/python",
            site_packages=f"{generation_root}/dependencies/site-packages",
            expected_versions={
                "sqlalchemy": normalized_inventory["packages"]["sqlalchemy"]["version"],
                "httptools": normalized_inventory["packages"]["httptools"]["version"],
            },
            parser_sha256=parser_record["sha256"],
        )
        if not imports["httptools.parser.parser"]["path"].endswith(
            parser_record["path"].removeprefix("dependencies/site-packages")
        ):
            raise CapabilityError("Selected httptools parser path does not match the signed file record.")
        commands = apply_operational_state["commands"]
        after_apply = persistent_hashes(app)
        validate_preservation(before, after_apply)
        image_inventory_after_apply = image_inventory(app)
        validate_image_inventory(image_inventory_before, image_inventory_after_apply)
        inventory_artifacts["after_apply"] = retain_image_inventory(
            image_inventory_after_apply, args.evidence_json, architecture, "after-apply"
        )
        restart_started = time.monotonic()
        run(["docker", "restart", app], timeout=120)
        client.set_base_url(published_base_url(app))
        wait_for_image_health(client, args.startup_timeout, "container restart readiness")
        client.login()
        restart_operational_state = capture_selected_operational_state(
            app,
            client,
            descriptor=descriptor,
            inventory=normalized_inventory,
            version=version,
            completed_job=None,
        )
        restarted = client.request("/api/v1/update/status")
        restart_imports = import_smoke(
            app,
            python=f"{generation_root}/environment/bin/python",
            site_packages=f"{generation_root}/dependencies/site-packages",
            expected_versions={
                "sqlalchemy": normalized_inventory["packages"]["sqlalchemy"]["version"],
                "httptools": normalized_inventory["packages"]["httptools"]["version"],
            },
            parser_sha256=parser_record["sha256"],
        )
        restart_duration = time.monotonic() - restart_started
        after_restart = persistent_hashes(app)
        validate_preservation(before, after_restart)
        image_inventory_after_restart = image_inventory(app)
        validate_image_inventory(image_inventory_before, image_inventory_after_restart)
        inventory_artifacts["after_restart"] = retain_image_inventory(
            image_inventory_after_restart, args.evidence_json, architecture, "after-restart"
        )
        commands_after_restart = restart_operational_state["commands"]
        rollback_started = time.monotonic()
        rollback = validate_rollback_response(
            client.request("/api/v1/update/rollback", {}),
            source_version=version,
            target_version=args.minimum_image_version.strip().lstrip("v"),
        )
        wait_until(lambda: client.request("/api/v1/update/status").get("current_version") == args.minimum_image_version.strip().lstrip("v"), args.update_timeout, "API rollback")
        wait_for_image_health(client, args.update_timeout, "rollback image readiness")
        client.login()
        raw_rollback_status = client.request("/api/v1/update/status")
        rollback_status = validate_rollback_status(
            raw_rollback_status,
            target_version=args.minimum_image_version.strip().lstrip("v"),
        )
        rollback_status_artifact = retain_rollback_status(
            args.evidence_json,
            architecture=architecture,
            source_commit=args.source_commit,
            status=raw_rollback_status,
        )
        rollback_duration = time.monotonic() - rollback_started
        rollback_imports = import_smoke(
            app,
            python="/venv/bin/python",
            site_packages="/venv/lib/python3.14/site-packages",
            expected_versions={"sqlalchemy": "2.0.54", "httptools": "0.8.0"},
            parser_sha256=None,
        )
        rollback_operational_state = capture_image_operational_state(
            app,
            client,
            image_version=args.minimum_image_version.strip().lstrip("v"),
            require_idle=True,
        )
        rollback_commands = rollback_operational_state["commands"]
        rollback_processes = rollback_operational_state["processes"]
        rollback_services = rollback_operational_state["services"]
        rollback_health = rollback_operational_state["health"]
        rollback_runtime_info = runtime_info(
            app,
            image_version=args.minimum_image_version.strip().lstrip("v"),
        )
        after_rollback = persistent_hashes(app)
        validate_preservation(before, after_rollback)
        image_inventory_after_rollback = image_inventory(app)
        validate_image_inventory(image_inventory_before, image_inventory_after_rollback)
        inventory_artifacts["after_rollback"] = retain_image_inventory(
            image_inventory_after_rollback, args.evidence_json, architecture, "after-rollback"
        )
        retry_id = secrets.token_hex(16)
        if retry_id == job_id:
            raise CapabilityError("Retry reused the Apply job ID.")
        reapply_started = time.monotonic()
        reapplied = apply_once_and_wait(client, "/api/v1/update/retry", version=version, job_id=retry_id, timeout=args.update_timeout)
        reapply_duration = time.monotonic() - reapply_started
        wait_for_image_health(client, args.update_timeout, "selected Retry readiness")
        reapply_operational_state = capture_selected_operational_state(
            app,
            client,
            descriptor=descriptor,
            inventory=normalized_inventory,
            version=version,
            completed_job=reapplied,
        )
        reapply_components = reapply_operational_state["runtime_components"]
        reapply_imports = import_smoke(
            app,
            python=f"{generation_root}/environment/bin/python",
            site_packages=f"{generation_root}/dependencies/site-packages",
            expected_versions={
                "sqlalchemy": normalized_inventory["packages"]["sqlalchemy"]["version"],
                "httptools": normalized_inventory["packages"]["httptools"]["version"],
            },
            parser_sha256=parser_record["sha256"],
        )
        after_reapply = persistent_hashes(app)
        validate_preservation(before, after_reapply)
        image_inventory_after_reapply = image_inventory(app)
        validate_image_inventory(image_inventory_before, image_inventory_after_reapply)
        inventory_artifacts["after_reapply"] = retain_image_inventory(
            image_inventory_after_reapply, args.evidence_json, architecture, "after-reapply"
        )
        commands_after_reapply = reapply_operational_state["commands"]
        storage_after = runtime_storage_observation(app, generation_root)
        memory_observations = sampler.stop()
        container_identity_after = run(
            ["docker", "inspect", "--format", "{{.Id}}", app]
        ).stdout.strip()
        validate_same_container_identity(
            container_identity_before,
            container_identity_after,
            "Qualification",
        )
        damage_recovery = damaged_native_recovery(
            resources=resources,
            reference=reference,
            architecture=architecture,
            network=network,
            ca_path=ca_path,
            descriptor=descriptor,
            bundle_sha256=variants[architecture]["bundle_sha256"],
            inventory=normalized_inventory,
            version=version,
            image_version=args.minimum_image_version.strip().lstrip("v"),
            startup_timeout=args.startup_timeout,
            update_timeout=args.update_timeout,
            evidence_json=args.evidence_json,
        )
        live_ui = (
            live_ui_qualification(
                resources=resources,
                reference=reference,
                architecture=architecture,
                network=network,
                ca_path=ca_path,
                username=username,
                password=password,
                target_version=version,
                image_version=args.minimum_image_version.strip().lstrip("v"),
                evidence_json=args.evidence_json,
                startup_timeout=args.startup_timeout,
                update_timeout=args.update_timeout,
            )
            if architecture == "amd64"
            else None
        )
        refusal_evidence: dict[str, Any] | None = None
        if architecture == "amd64":
            refusal_evidence = {
                kind: filesystem_refusal(
                    kind=kind,
                    resources=resources,
                    reference=reference,
                    architecture=architecture,
                    network=network,
                    ca_path=ca_path,
                    descriptor=descriptor,
                    bundle_sha256=variants[architecture]["bundle_sha256"],
                    version=version,
                    image_version=args.minimum_image_version.strip().lstrip("v"),
                    startup_timeout=args.startup_timeout,
                    update_timeout=args.update_timeout,
                    bundle_bytes=bundles[architecture].stat().st_size,
                )
                for kind in ("noexec", "low_space")
            }
        resource_observations = validate_resource_observations(
            {
                "bundle_bytes": bundles[architecture].stat().st_size,
                "expanded_bytes": descriptor["expanded_size"],
                "member_count": bundle_member_count,
                "preparation_duration_seconds": apply_duration,
                "restart_duration_seconds": restart_duration,
                "rollback_duration_seconds": rollback_duration,
                "reapply_duration_seconds": reapply_duration,
                "selected_generation_disk_bytes": storage_after["selected_generation_bytes"],
                "free_bytes_before": storage_before["free_bytes"],
                "free_bytes_after": storage_after["free_bytes"],
                **memory_observations,
            }
        )
        receipt["architecture"].update(
            {
                "status": "passed",
                "interpreter_smoke": True,
                "native_imports": True,
                "system_libraries": True,
                "persistent_restart": True,
                "rollback": True,
                "qualification": {
                    "platform_manifest_digest": manifest_digest,
                    "pulled_image_id": image_id,
                    "bootstrap_index_base64": encoded_index,
                    "pulled_image_inspection": image_inspection,
                    "native_host_architecture": architecture,
                    "docker_engine_architecture": architecture,
                    "container_identity_before": container_identity_before,
                    "container_identity_after": container_identity_after,
                    "initial_status": initial_status,
                    "initial_operational_state": initial_operational_state,
                    "check": compact_checked,
                    "signed_dependency_inventory": signed_inventory,
                    "apply_job_id": job_id,
                    "apply_runtime_components": components,
                    "apply_operational_state": apply_operational_state,
                    "image_imports_before": image_imports_before,
                    "selected_native_imports": imports,
                    "selected_native_imports_after_restart": restart_imports,
                    "selected_native_imports_after_reapply": reapply_imports,
                    "image_imports_after_rollback": rollback_imports,
                    "supervisor_processes": commands,
                    "supervisor_processes_after_restart": commands_after_restart,
                    "restart_operational_state": restart_operational_state,
                    "rollback_supervisor_processes": rollback_commands,
                    "rollback_supervisor_process_identities": rollback_processes,
                    "rollback_supervisor_services": rollback_services,
                    "rollback_health": rollback_health,
                    "supervisor_processes_after_reapply": commands_after_reapply,
                    "image_inventory_before": image_inventory_before,
                    "image_inventory_after_apply": inventory_summary(image_inventory_after_apply),
                    "image_inventory_after_restart": inventory_summary(image_inventory_after_restart),
                    "image_inventory_after_rollback": inventory_summary(image_inventory_after_rollback),
                    "image_inventory_after_reapply": inventory_summary(image_inventory_after_reapply),
                    "image_inventory_artifacts": inventory_artifacts,
                    "persistent_hashes_before": before,
                    "persistent_hashes_after_apply": after_apply,
                    "persistent_hashes_after_restart": after_restart,
                    "persistent_hashes_after_rollback": after_rollback,
                    "persistent_hashes_after_reapply": after_reapply,
                    "container_restart": {"status": "passed", "current_version": restarted.get("current_version")},
                    "rollback": rollback,
                    "rollback_status": compact_rollback_status_evidence(
                        rollback_status, rollback_status_artifact
                    ),
                    "rollback_runtime_components": {
                        "ui": rollback_status["worker_runtime"]
                    },
                    "rollback_runtime_component_scope": (
                        "frozen_image_ui_status_interpreter_only"
                    ),
                    "rollback_runtime_info": rollback_runtime_info,
                    "reapply_job_id": retry_id,
                    "reapply_runtime_components": reapply_components,
                    "reapply_operational_state": reapply_operational_state,
                },
                "release_qualification": {
                    "damaged_native_recovery": compact_recovery_diagnostic_evidence(
                        damage_recovery
                    ),
                    "resource_observations": resource_observations,
                    **(
                        {"filesystem_refusals": refusal_evidence}
                        if refusal_evidence is not None
                        else {}
                    ),
                    **({"live_ui": live_ui} if live_ui is not None else {}),
                },
            }
        )
        receipt["status"] = "passed"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["failure"] = str(exc)[:1000]
        raise
    finally:
        if sampler is not None:
            sampler.cancel()
        secondary_failures: list[str] = []
        try:
            (
                log_hashes,
                release_log_hashes,
                release_log_artifacts,
                log_capture_failures,
            ) = capture_container_logs(
                resources,
                prefix=prefix,
                app=app,
                feed=feed,
                evidence_json=args.evidence_json,
            )
            secondary_failures.extend(log_capture_failures)
        except Exception:
            log_hashes = {}
            release_log_hashes = {}
            release_log_artifacts = {}
            secondary_failures.append("docker_logs:collection_error")
        receipt["logs_sha256"] = log_hashes
        receipt["release_logs_sha256"] = release_log_hashes
        receipt["release_log_artifacts"] = release_log_artifacts
        try:
            validate_log_hashes(log_hashes)
        except CapabilityError:
            secondary_failures.append("docker_logs:lifecycle_validation_failed")
        try:
            validate_release_log_artifacts(
                release_log_artifacts,
                release_log_hashes,
                architecture=architecture,
            )
        except CapabilityError:
            secondary_failures.append("docker_logs:release_validation_failed")
        try:
            cleanup_failures = resources.cleanup()
        except Exception:
            cleanup_failures = ["docker_resources:cleanup_error"]
        cleanup_failures.extend(remove_temp_root(temp_root))
        secondary_failures.extend(cleanup_failures)
        receipt["cleanup"] = {
            "status": "passed" if not cleanup_failures else "failed",
            "failures": cleanup_failures,
        }
        apply_secondary_failures(receipt, secondary_failures)
        try:
            encoded_receipt = serialize_bounded_receipt(receipt)
        except CapabilityError as exc:
            primary_failure = receipt.get("failure")
            fallback_secondary = list(receipt.get("secondary_failures", []))
            fallback_secondary.append("receipt:serialization_failed")
            receipt = {
                "schema": 2,
                "status": "failed",
                "target_application_version": version,
                "source_commit": args.source_commit,
                "failure": (
                    primary_failure
                    if isinstance(primary_failure, str) and primary_failure
                    else str(exc)
                ),
                "secondary_failures": list(dict.fromkeys(fallback_secondary)),
                "cleanup": receipt["cleanup"],
            }
            encoded_receipt = serialize_bounded_receipt(receipt)
        args.evidence_json.write_bytes(encoded_receipt)
    print(json.dumps({"status": receipt["status"], "evidence": str(args.evidence_json)}))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
