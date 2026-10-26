#!/usr/bin/env python3
"""Prove one native protocol-3 app update without replacing its container."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import secrets
import shutil
import tempfile
import time
import urllib.error
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
IMAGE_TOOL = ROOT / "scripts/release/verify-image-upgrade.py"
CAPABILITY_TOOL = ROOT / "scripts/release/verify-runtime-capability.py"
ASSET_TOOL = ROOT / "scripts/release/verify-update-assets.py"
FRONTEND_TOOL = ROOT / "scripts/release/verify-minimum-image-app-update.py"
CATALOG_URL = "https://channelwatch.coderluii.dev/updates/v2/stable.json"
LABEL = "codex.task=channelwatch-legacy-app-upgrade"
HEX = re.compile(r"[0-9a-f]{64}")
SHA40 = re.compile(r"[0-9a-f]{40}")
JOB = re.compile(r"[0-9a-f]{32}")
DIAGNOSTIC_TEXT_LIMIT = 256
EXPECTED_SCENARIOS = [
    ("stock-1.4.0", "1.4.0", "1.4.0", None),
    ("overlay-1.4.0-to-1.4.1", "1.4.0", "1.4.1", "1.4.1"),
    ("overlay-1.4.0-to-1.4.5", "1.4.0", "1.4.5", "1.4.5"),
    ("stock-1.4.1", "1.4.1", "1.4.1", None),
    ("overlay-1.4.1-to-1.4.5", "1.4.1", "1.4.5", "1.4.5"),
    ("stock-1.4.5", "1.4.5", "1.4.5", None),
]
LEGACY_SOURCE_CONTRACTS = {
    "1.6.0": ["1.4.0", "1.4.1", "1.4.5"],
    "1.6.1": ["1.4.0", "1.4.1", "1.4.5", "1.6.0"],
}
BRIDGE_RELEASE_160 = {
    "version": "1.6.0",
    "source_commit": "b48057184752c912e4ae9b3a474374704d896073",
    "catalog_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.0/channelwatch-catalog-v1.6.0.json",
    "catalog_sha256": "bc9bcdb38c44af581835fdd4f29e2a2550eb8bb5d51b8308395a803110feb185",
    "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.0/channelwatch-app-v1.6.0.zip",
    "bundle_sha256": "f8cbdca71ef254df84b0e3b472facce8ece201b4928218caf757f4c5ddf53914",
}
LEGACY_IMAGE_BOOTSTRAP_PATHS = (
    "/usr/local/bin/channelwatch",
    "/app/channelwatch-image.json",
    "/app/core/docker-entrypoint.py",
    "/app/core/runtime_launcher.py",
    "/etc/supervisor/conf.d/supervisord.conf.template",
)
SELECTED_DOCTOR_BOOTSTRAP = """
import runpy
import sys

app_dir = sys.argv.pop(1)
sys.path.insert(0, app_dir)
sys.argv = ["channelwatch doctor", *sys.argv[1:]]
runpy.run_module("core.cli.doctor", run_name="__main__")
""".strip()


class LegacyAppUpgradeError(RuntimeError):
    """Raised when a same-container legacy qualification does not hold."""


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LegacyAppUpgradeError(f"Could not load {path.name}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image_tool = _load(IMAGE_TOOL, "legacy_image_helpers")
capability_tool = _load(CAPABILITY_TOOL, "legacy_capability_helpers")
frontend_tool = _load(FRONTEND_TOOL, "legacy_frontend_verifier")
LABEL = image_tool.LABEL


def sha256_file(path: Path) -> str:
    return image_tool.sha256_file(path)


def validate_baselines(value: object, *, target_version: str) -> list[dict[str, Any]]:
    expected_schema = 1 if target_version == "1.6.0" else 2
    if not isinstance(value, dict) or value.get("schema") != expected_schema:
        raise LegacyAppUpgradeError("Legacy baseline fixture schema is invalid.")
    if value.get("target_version") != target_version:
        raise LegacyAppUpgradeError("Legacy baseline target version is invalid.")
    if value.get("repository") != "coderluii/channelwatch":
        raise LegacyAppUpgradeError("Legacy baseline repository is invalid.")
    expected_sources = LEGACY_SOURCE_CONTRACTS.get(target_version)
    if expected_sources is None or value.get("legacy_sources") != expected_sources:
        raise LegacyAppUpgradeError("Legacy source versions are incomplete.")
    if target_version == "1.6.1":
        if value.get("qualification_modes") != ["direct", "chained"]:
            raise LegacyAppUpgradeError("Legacy qualification modes are incomplete.")
        if value.get("bridge_release") != BRIDGE_RELEASE_160:
            raise LegacyAppUpgradeError("Legacy bridge release is not the immutable v1.6.0 release.")
    scenarios = value.get("scenarios")
    expected = [
        {"id": key, "image_version": image, "source_application_version": source, "overlay_version": overlay}
        for key, image, source, overlay in EXPECTED_SCENARIOS
    ]
    if scenarios != expected:
        raise LegacyAppUpgradeError("Legacy scenario matrix is incomplete or reordered.")
    images = value.get("images")
    if not isinstance(images, dict) or set(images) != {"1.4.0", "1.4.1", "1.4.5"}:
        raise LegacyAppUpgradeError("Legacy image fixture matrix is incomplete.")
    for version, row in images.items():
        if (
            not isinstance(row, dict)
            or SHA40.fullmatch(str(row.get("source_commit") or "")) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("index_digest") or "")) is None
            or not isinstance(row.get("manifests"), dict)
            or set(row["manifests"]) != {"amd64", "arm64"}
            or any(re.fullmatch(r"sha256:[0-9a-f]{64}", str(digest)) is None for digest in row["manifests"].values())
        ):
            raise LegacyAppUpgradeError(f"Legacy image fixture is invalid: {version}.")
    return scenarios


def validate_release_config(value: object, *, target_version: str) -> list[str]:
    if not isinstance(value, dict) or value.get("version") != target_version:
        raise LegacyAppUpgradeError("Release configuration target version is invalid.")
    sources = value.get("legacy_app_update_sources")
    if sources != LEGACY_SOURCE_CONTRACTS.get(target_version):
        raise LegacyAppUpgradeError("Release configuration legacy sources are incomplete.")
    return sources


def verify_signed_assets(catalog: Path, bundle: Path, *, version: str, source_commit: str) -> tuple[dict[str, Any], str]:
    asset_tool = _load(ASSET_TOOL, "legacy_asset_verifier")
    try:
        verified = asset_tool.verify_update_catalog(
            catalog.read_bytes(), bundle.read_bytes(), expected_version=version,
            expected_delivery_mode="app_update", expected_runtime_abi="channelwatch-runtime-v1",
            expected_settings_schema_version=7, expected_recommended_image_version=version,
        )
        metadata = asset_tool.validate_bundle_archive(
            bundle.read_bytes(), expected_version=version,
            expected_runtime_abi="channelwatch-runtime-v1", expected_settings_schema_version=7,
        )
        release = next(row for row in verified["payload"]["releases"] if row.get("version") == version)
    except Exception as exc:
        raise LegacyAppUpgradeError("Candidate legacy catalog or app bundle is not validly signed.") from exc
    if metadata.get("git_sha") != source_commit:
        raise LegacyAppUpgradeError("Candidate app bundle source commit does not match.")
    expected_sources = LEGACY_SOURCE_CONTRACTS.get(version)
    if expected_sources is None:
        raise LegacyAppUpgradeError("Candidate target is not an approved legacy app-only release.")
    expected = {
        "delivery_mode": "app_update", "image_required": False,
        "image_refresh_recommended": False, "minimum_image_version": "1.4.0",
        "recommended_image_version": version, "updater_protocol": 2,
        "automatic_install_allowed": False,
        "compatible_source_application_versions": expected_sources,
        "compatible_launcher_protocols": [3],
    }
    if any(release.get(key) != expected_value for key, expected_value in expected.items()):
        raise LegacyAppUpgradeError("Candidate legacy catalog projection is not the exact app-only contract.")
    bundle_url = str(release.get("bundle_url") or "")
    if not bundle_url.startswith(f"https://github.com/CoderLuii/ChannelWatch/releases/download/v{version}/"):
        raise LegacyAppUpgradeError("Candidate bundle URL is not release-bound.")
    return release, bundle_url


def require_image_python_process(
    process: object, *, extra_fields: set[str] | None = None
) -> dict[str, Any]:
    expected_fields = {
        "pid", "python_executable", "python_version", "python_prefix",
        "dependencies", *(extra_fields or set()),
    }
    dependencies = process.get("dependencies") if isinstance(process, dict) else None
    if (
        not isinstance(process, dict)
        or set(process) != expected_fields
        or type(process.get("pid")) is not int
        or process["pid"] <= 0
        or process.get("python_executable") != "/venv/bin/python"
        or process.get("python_prefix") != "/venv"
        or process.get("python_version") != "3.14.8"
        or not isinstance(dependencies, dict)
        or set(dependencies) != {
            "requests", "cryptography", "sqlmodel", "uvicorn", "pydantic_core",
            "sqlalchemy", "httptools",
        }
    ):
        raise LegacyAppUpgradeError("Selected legacy runtime process identity is invalid.")
    for record in dependencies.values():
        if not isinstance(record, dict) or not isinstance(record.get("loaded"), bool):
            raise LegacyAppUpgradeError("Selected application dependency evidence is malformed.")
        if not record["loaded"]:
            if set(record) != {"loaded"}:
                raise LegacyAppUpgradeError("Selected application dependency evidence is malformed.")
            continue
        path = record.get("path")
        normalized_path = PurePosixPath(path) if isinstance(path, str) else None
        if (
            set(record) != {"loaded", "path", "version"}
            or not isinstance(path, str)
            or not path.startswith("/venv/lib/python3.14/site-packages/")
            or ".." in normalized_path.parts
            or str(normalized_path) != path
            or not isinstance(record.get("version"), str)
        ):
            raise LegacyAppUpgradeError("Selected application imported a dependency outside the image venv.")
    if any(
        dependencies[name]["loaded"] is not True
        for name in {"cryptography", "sqlmodel", "pydantic_core", "sqlalchemy"}
    ):
        raise LegacyAppUpgradeError("Selected application did not load mandatory image dependencies.")
    return process


def validate_selected_status(status: object, *, target_version: str, image_version: str, bundle_sha256: str) -> dict[str, Any]:
    active = status.get("active_bundle") if isinstance(status, dict) else None
    worker = status.get("worker_runtime") if isinstance(status, dict) else None
    manifest = active.get("manifest") if isinstance(active, dict) else None
    if (
        not isinstance(status, dict) or status.get("current_version") != target_version
        or status.get("image_version") != image_version or status.get("launcher_protocol") != 3
        or status.get("runtime_source") != "app_bundle" or status.get("operation_busy") is not False
        or not isinstance(active, dict) or str(active.get("version") or "").lstrip("v") != target_version
        or not str(active.get("path") or "").startswith("/config/channelwatch-runtime/releases/")
        or any(field in active for field in (
            "runtime_generation_id", "runtime_descriptor_sha256",
            "dependency_inventory_sha256", "runtime_trust_sha256",
        ))
        or not isinstance(manifest, dict) or manifest.get("bundle_sha256") != bundle_sha256
        or not isinstance(worker, dict)
        or worker.get("verified_generation") is not None
    ):
        raise LegacyAppUpgradeError("Selected legacy runtime identity is invalid.")
    require_image_python_process(worker, extra_fields={"verified_generation"})
    return status


def validate_identity_invariant(values: list[dict[str, str]]) -> None:
    if len(values) < 2 or any(
        not isinstance(value, dict) or set(value) != {"container_id", "image_id", "repo_digest"}
        for value in values
    ) or any(value != values[0] for value in values[1:]):
        raise LegacyAppUpgradeError("Container or image identity changed during an app-only update.")


def container_identity(container: str, expected_repo_digest: str) -> dict[str, str]:
    raw = json.loads(image_tool.run(["docker", "inspect", container], timeout=30).stdout)
    if not isinstance(raw, list) or len(raw) != 1:
        raise LegacyAppUpgradeError("Container identity could not be inspected.")
    container_id = str(raw[0].get("Id") or "")
    image_id = str(raw[0].get("Image") or "")
    if HEX.fullmatch(container_id) is None or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise LegacyAppUpgradeError("Container identity is malformed.")
    image = json.loads(image_tool.run(["docker", "image", "inspect", image_id], timeout=30).stdout)[0]
    if expected_repo_digest not in (image.get("RepoDigests") or []):
        raise LegacyAppUpgradeError("Container image is not bound to the expected native digest.")
    return {"container_id": container_id, "image_id": image_id, "repo_digest": expected_repo_digest}


def database_canary(container: str, *, create: bool = False) -> str:
    code = f"""
import hashlib,json,sqlite3
path='/config/channelwatch.db'
with sqlite3.connect(path) as db:
 db.execute('CREATE TABLE IF NOT EXISTS release_compatibility_canary (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
 if {create!r}: db.execute('INSERT OR REPLACE INTO release_compatibility_canary(id,value) VALUES(?,?)',('legacy-160','preserved'))
 row=db.execute('SELECT id,value FROM release_compatibility_canary WHERE id=?',('legacy-160',)).fetchone()
if row != ('legacy-160','preserved'): raise SystemExit('database canary missing')
print(json.dumps({{'sha256':hashlib.sha256(json.dumps(row,separators=(',',':')).encode()).hexdigest()}}))
"""
    value = image_tool._docker_json(container, code)
    digest = str(value.get("sha256") or "")
    if HEX.fullmatch(digest) is None:
        raise LegacyAppUpgradeError("Database canary evidence is invalid.")
    return digest


def runtime_storage(container: str, *, active_path: str | None = None) -> dict[str, Any]:
    code = f"""
import json
from pathlib import Path
root=Path('/config/channelwatch-runtime')
names=sorted(path.name for path in root.iterdir()) if root.exists() else []
for forbidden in ('dependencies','dependency-generations','generations','runtime-generations'):
 if forbidden in names: raise SystemExit('dependency generation present')
selected=Path({active_path!r}) if {active_path is not None!r} else None
selected_children=[]
if selected is not None:
 selected=selected.resolve()
 releases=(root/'releases').resolve()
 if releases not in selected.parents: raise SystemExit('selected release escaped releases root')
 if not selected.is_dir(): raise SystemExit('selected release missing')
 selected_children=sorted(path.name for path in selected.iterdir())
 if any(name in selected_children for name in ('environment','dependencies','dependency-generation')):
  raise SystemExit('selected app release contains dependency generation')
print(json.dumps({{'entries':names,'selected_children':selected_children,'dependency_generation_absent':True}},sort_keys=True))
"""
    return image_tool._docker_json(container, code)


def require_legacy_encrypted_settings(container: str, client: Any) -> None:
    if not image_tool.encrypted_settings_usable(
        container
    ) or not image_tool.credential_api_semantics(client):
        raise LegacyAppUpgradeError(
            "Legacy source credential evidence is incomplete."
        )


def legacy_image_inventory(
    container: str, *, image_version: str
) -> dict[str, Any]:
    if image_version not in {"1.4.0", "1.4.1", "1.4.5"}:
        raise LegacyAppUpgradeError(
            "Legacy image inventory source version is unsupported."
        )
    return capability_tool.image_inventory(
        container, bootstrap_paths=LEGACY_IMAGE_BOOTSTRAP_PATHS
    )


def feed_request_count(container: str) -> int:
    result = image_tool.run(["docker", "logs", container], check=False, timeout=30)
    if result.returncode:
        raise LegacyAppUpgradeError("Legacy feed logs could not be read.")
    return len(re.findall(r'"GET /updates/v2/stable\.json(?:\?[^ ]*)? HTTP/[0-9.]+" 200(?: |$)', result.stdout))


def checked_current_on_v2(client, feed: str, *, target_version: str) -> dict[str, Any]:
    before = feed_request_count(feed)
    checked = client.request("/api/v1/update/check", {})
    after = feed_request_count(feed)
    job = checked.get("last_job") if isinstance(checked, dict) else None
    if (
        after != before + 1
        or checked.get("update_available") is not False
        or checked.get("current_version") != target_version
        or not isinstance(job, dict)
        or job.get("operation") != "check"
        or job.get("status") != "current"
        or job.get("version") != target_version
    ):
        raise LegacyAppUpgradeError(
            "The activated app did not fetch the protocol-3 v2 catalog and remain current."
        )
    return checked


def selected_runtime_info(
    container: str, *, version: str, application_dir: str
) -> dict[str, Any]:
    result = image_tool.run(
        [
            "docker", "exec", "--env", f"CHANNELWATCH_APP_DIR={application_dir}",
            container, "/venv/bin/python", "-I", "-B", "-c",
            SELECTED_DOCTOR_BOOTSTRAP, application_dir, "runtime-info",
        ],
        timeout=60,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LegacyAppUpgradeError("Selected runtime-info is not JSON.") from exc
    process = value.get("process_runtime") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {
            "diagnostic_source", "application_dir", "application_version",
            "process_runtime", "verified_generation",
        }
        or value.get("diagnostic_source") != "selected_application"
        or value.get("application_version") != version or value.get("verified_generation") is not None
        or value.get("application_dir") != application_dir
    ):
        raise LegacyAppUpgradeError("Selected runtime-info did not retain the image Python closure.")
    require_image_python_process(process)
    return value


def validate_source_status(status: object, *, scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict) or status.get("launcher_protocol") != 3 or status.get("image_version") != scenario["image_version"]:
        raise LegacyAppUpgradeError("Rollback did not retain the legacy image runtime.")
    active = status.get("active_bundle")
    if scenario["overlay_version"] is None:
        valid = status.get("current_version") == scenario["image_version"] and status.get("runtime_source") == "image" and active is None
    else:
        valid = status.get("current_version") == scenario["overlay_version"] and status.get("runtime_source") == "app_bundle" and isinstance(active, dict) and active.get("version") == scenario["overlay_version"]
    if not valid:
        raise LegacyAppUpgradeError("Rollback did not restore the exact prior application state.")
    return status


def _require_exact_job(
    job: object,
    *,
    job_id: str | None,
    operation: str,
    status: str,
    version: str,
    bundle_sha256: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(job, dict)
        or JOB.fullmatch(str(job.get("job_id") or "")) is None
        or (job_id is not None and job.get("job_id") != job_id)
        or job.get("operation") != operation
        or job.get("status") != status
        or job.get("version") != version
        or (
            bundle_sha256 is not None
            and job.get("bundle_sha256") != bundle_sha256
        )
    ):
        raise LegacyAppUpgradeError("Update status is not bound to the expected job.")
    return job


def wait_for_job_idle(
    client,
    *,
    job: dict[str, Any],
    operation: str,
    status: str,
    version: str,
    timeout: int,
    description: str,
    status_validator,
    bundle_sha256: str | None = None,
) -> dict[str, Any]:
    expected = _require_exact_job(
        job,
        job_id=None,
        operation=operation,
        status=status,
        version=version,
        bundle_sha256=bundle_sha256,
    )
    expected_busy_state = "rolling_back" if operation == "rollback" else "checking"

    def idle():
        current = client.request("/api/v1/update/status")
        if not isinstance(current, dict):
            raise LegacyAppUpgradeError("Update status response is invalid.")
        if current.get("operation_busy") is True:
            if current.get("operation_state") != expected_busy_state:
                raise LegacyAppUpgradeError("Update operation state is ambiguous.")
            return None
        if (
            current.get("operation_busy") is not False
            or current.get("operation_state") != "idle"
        ):
            raise LegacyAppUpgradeError("Update operation state is ambiguous.")
        status_validator(current)
        _require_exact_job(
            current.get("last_job"),
            job_id=expected["job_id"],
            operation=operation,
            status=status,
            version=version,
            bundle_sha256=bundle_sha256,
        )
        return current

    return image_tool.wait_until(idle, timeout, description)


def wait_for_available_check_idle(
    client,
    *,
    checked: dict[str, Any],
    target_version: str,
    bundle_sha256: str,
    timeout: int,
    description: str,
    status_validator,
) -> dict[str, Any]:
    if not isinstance(checked, dict):
        raise LegacyAppUpgradeError("Target check response is invalid.")
    returned_job = checked.get("last_job") if isinstance(checked, dict) else None
    returned_job_id = None
    if checked.get("operation_busy") is False:
        returned_job_id = _require_exact_job(
            returned_job,
            job_id=None,
            operation="check",
            status="available",
            version=target_version,
        )["job_id"]
    elif not (
        checked.get("operation_busy") is True
        and checked.get("operation_state") == "checking"
    ):
        raise LegacyAppUpgradeError("Target check operation state is ambiguous.")

    def idle():
        current = client.request("/api/v1/update/status")
        if not isinstance(current, dict):
            raise LegacyAppUpgradeError("Target check status response is invalid.")
        status_validator(current)
        if current.get("operation_busy") is True:
            if current.get("operation_state") != "checking":
                raise LegacyAppUpgradeError("Target check operation state is ambiguous.")
            return None
        if (
            current.get("operation_busy") is not False
            or current.get("operation_state") != "idle"
        ):
            raise LegacyAppUpgradeError("Target check operation state is ambiguous.")
        trusted = current.get("trusted_target")
        if (
            current.get("update_available") is not True
            or current.get("image_required") is not False
            or not isinstance(trusted, dict)
            or trusted.get("version") != target_version
            or trusted.get("bundle_sha256") != bundle_sha256
        ):
            raise LegacyAppUpgradeError(
                "Target check is not bound to the exact signed app release."
            )
        _require_exact_job(
            current.get("last_job"),
            job_id=returned_job_id,
            operation="check",
            status="available",
            version=target_version,
        )
        return current

    return image_tool.wait_until(idle, timeout, description)


def wait_for_target_check_idle(
    client,
    *,
    checked: dict[str, Any],
    scenario: dict[str, Any],
    target_version: str,
    bundle_sha256: str,
    timeout: int,
) -> dict[str, Any]:
    return wait_for_available_check_idle(
        client,
        checked=checked,
        target_version=target_version,
        bundle_sha256=bundle_sha256,
        timeout=timeout,
        description="target update check idle",
        status_validator=lambda value: validate_source_status(
            value, scenario=scenario
        ),
    )


def check_target_until_idle(
    client,
    *,
    scenario: dict[str, Any],
    target_version: str,
    bundle_sha256: str,
    timeout: int,
) -> dict[str, Any]:
    checked = client.request("/api/v1/update/check", {})
    return wait_for_target_check_idle(
        client,
        checked=checked,
        scenario=scenario,
        target_version=target_version,
        bundle_sha256=bundle_sha256,
        timeout=timeout,
    )


def wait_for_overlay_idle(
    client,
    *,
    scenario: dict[str, Any],
    overlay_job: dict[str, Any],
    bundle_sha256: str,
    timeout: int,
) -> dict[str, Any]:
    overlay_version = scenario["overlay_version"]
    if not isinstance(overlay_version, str):
        raise LegacyAppUpgradeError("Overlay update did not return an exact successful job.")
    try:
        return wait_for_job_idle(
            client,
            job=overlay_job,
            operation="apply",
            status="success",
            version=overlay_version,
            bundle_sha256=bundle_sha256,
            timeout=timeout,
            description="overlay operation idle",
            status_validator=lambda value: validate_source_status(
                value, scenario=scenario
            ),
        )
    except LegacyAppUpgradeError as exc:
        raise LegacyAppUpgradeError(
            "Overlay update did not return an exact successful job and idle operation."
        ) from exc


def seed_scheduler_between_checks(reference: str, volume: str) -> dict[str, Any]:
    code = r"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, '/app')
from core.update_policy import (
    CHECK_CADENCE,
    UpdatePolicyStore,
    format_timestamp,
    utc_now,
)

config = Path('/config')
policy = config / 'channelwatch-runtime' / 'update-policy.json'
store = UpdatePolicyStore(config)
before_policy_value = asdict(store.get())
before_policy = policy.read_bytes()
before = store.get_state()
expected = format_timestamp(utc_now() + CHECK_CADENCE)
after = store.put_state({'next_check_at': expected})
if after.get('next_check_at') != expected:
    raise SystemExit('scheduler next check was not preserved')
if any(after.get(key) != value for key, value in before.items() if key != 'next_check_at'):
    raise SystemExit('scheduler state changed outside next_check_at')
after_policy = policy.read_bytes() if policy.exists() else None
after_policy_value = asdict(store.get())
if before_policy != after_policy or before_policy_value != after_policy_value:
    raise SystemExit('update policy changed while seeding scheduler state')
if after_policy_value.get('mode') != 'automatic':
    raise SystemExit('automatic scheduler policy is not enabled')
print(json.dumps({
    'schema': after.get('schema'),
    'next_check_at': after.get('next_check_at'),
    'cadence_seconds': int(CHECK_CADENCE.total_seconds()),
    'policy_unchanged': True,
    'automatic_scheduler_enabled': True,
    'policy_mode': after_policy_value.get('mode'),
}, sort_keys=True))
""".strip()
    result = image_tool.run(
        [
            "docker", "run", "--rm", "--pull=never",
            "--mount", f"type=volume,source={volume},target=/config",
            "--entrypoint", "/venv/bin/python", reference,
            "-I", "-B", "-c", code,
        ],
        timeout=60,
    )
    try:
        evidence = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LegacyAppUpgradeError("Scheduler seed evidence is not JSON.") from exc
    if (
        not isinstance(evidence, dict)
        or not isinstance(evidence.get("schema"), int)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z",
            str(evidence.get("next_check_at") or ""),
        ) is None
        or evidence.get("cadence_seconds") != 6 * 60 * 60
        or evidence.get("policy_unchanged") is not True
        or evidence.get("automatic_scheduler_enabled") is not True
        or evidence.get("policy_mode") != "automatic"
    ):
        raise LegacyAppUpgradeError("Scheduler seed evidence is invalid.")
    return evidence


def require_scheduler_checkpoint(
    container: str,
    *,
    expected_schema: int,
) -> dict[str, Any]:
    code = r"""
import json
from datetime import datetime, timezone
from pathlib import Path

runtime = Path('/config/channelwatch-runtime')
state = json.loads((runtime / 'update-scheduler.json').read_text(encoding='utf-8'))
policy = json.loads((runtime / 'update-policy.json').read_text(encoding='utf-8'))
next_check = datetime.fromisoformat(str(state.get('next_check_at') or '').replace('Z', '+00:00'))
last_success = datetime.fromisoformat(str(state.get('last_success_at') or '').replace('Z', '+00:00'))
if next_check.tzinfo is None or next_check.astimezone(timezone.utc) <= datetime.now(timezone.utc):
    raise SystemExit('scheduler is not between future checks')
cadence_seconds = int((next_check - last_success).total_seconds())
print(json.dumps({
    'schema': state.get('schema'),
    'next_check_at': state.get('next_check_at'),
    'last_success_at': state.get('last_success_at'),
    'cadence_seconds': cadence_seconds,
    'policy_mode': policy.get('mode'),
}, sort_keys=True))
""".strip()
    evidence = image_tool._docker_json(container, code)
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema") != expected_schema
        or evidence.get("cadence_seconds") != 6 * 60 * 60
        or evidence.get("policy_mode") != "automatic"
    ):
        raise LegacyAppUpgradeError(
            "Scheduler did not retain the exact between-checks checkpoint."
        )
    return evidence


def request_update_operation(
    client, path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        return client.request(path, payload)
    except urllib.error.HTTPError as exc:
        context = ""
        try:
            raw = exc.read(4097)
        except OSError:
            raw = b""
        if len(raw) <= 4096:
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = None
            detail = value.get("detail") if isinstance(value, dict) else None
            code = detail.get("code") if isinstance(detail, dict) else None
            message = detail.get("message") if isinstance(detail, dict) else None
            if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code):
                context = code
            if isinstance(message, str):
                safe_message = " ".join(message.split())
                safe_message = re.sub(
                    r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+",
                    lambda match: f"{match.group(1)}=[redacted]",
                    safe_message,
                )[:240]
                if safe_message:
                    context = f"{context}: {safe_message}" if context else safe_message
        suffix = f" ({context})" if context else ""
        raise LegacyAppUpgradeError(
            f"Update API POST {path} returned HTTP {exc.code}{suffix}."
        ) from exc


def wait_job(client, *, endpoint: str, version: str, timeout: int, image_version: str) -> dict[str, Any]:
    requested = secrets.token_hex(16)
    reply = request_update_operation(
        client, endpoint, {"version": version, "job_id": requested}
    )
    job_id = str(reply.get("job_id") or "")
    if JOB.fullmatch(job_id) is None:
        raise LegacyAppUpgradeError("Update response returned an invalid job identity.")
    if image_version not in image_tool.SERVER_ASSIGNED_JOB_ID_IMAGES and job_id != requested:
        raise LegacyAppUpgradeError("Update response changed the requested job identity.")
    def completed():
        try:
            job = client.request(f"/api/v1/update/jobs/{job_id}")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 404, 503}:
                if exc.code == 401:
                    client.login()
                return None
            raise
        if job.get("job_id") != job_id:
            raise LegacyAppUpgradeError("Update job identity changed.")
        if job.get("status") in {"failed", "rolled_back", "cancelled", "rejected"}:
            raise LegacyAppUpgradeError("Signed legacy app update failed.")
        return job if job.get("status") == "success" else None
    return image_tool.wait_until(completed, timeout, f"legacy update job {job_id}")


def reconciled_attempt(
    container: str,
    *,
    version: str,
    bundle_sha256: str,
    job_id: str,
    attempt_id: str,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    code = """
import json
from pathlib import Path
path=Path('/config/channelwatch-runtime/update-scheduler.json')
print(json.dumps(json.loads(path.read_text(encoding='utf-8'))))
"""
    state = image_tool._docker_json(container, code)
    attempt = state.get("last_attempt") if isinstance(state, dict) else None
    if not isinstance(attempt, dict):
        if observation is not None:
            observation.clear()
            observation["state"] = "missing"
        return None
    if observation is not None:
        observation.clear()
        observation.update(
            {
                "state": "present",
                **{
                    key: _diagnostic_text(attempt.get(key), allow_none=True)
                    for key in (
                        "version",
                        "bundle_sha256",
                        "job_id",
                        "attempt_id",
                        "phase",
                        "completed_at",
                        "terminal_job_status",
                    )
                },
            }
        )
    if (
        attempt.get("version") != version
        or attempt.get("bundle_sha256") != bundle_sha256
        or attempt.get("job_id") != job_id
        or attempt.get("attempt_id") != attempt_id
    ):
        raise LegacyAppUpgradeError(
            "Update scheduler reconciled a different legacy upgrade attempt."
        )
    phase = attempt.get("phase")
    if phase in {"apply_started", "activation_pending"}:
        return None
    if (
        phase != "success"
        or attempt.get("terminal_job_status") != "success"
        or not isinstance(attempt.get("completed_at"), str)
        or not attempt["completed_at"]
    ):
        raise LegacyAppUpgradeError(
            "Update scheduler did not reconcile the legacy upgrade successfully."
        )
    return attempt


def wait_reconciled_attempt(
    container: str,
    *,
    version: str,
    bundle_sha256: str,
    job_id: str,
    attempt_id: str,
    timeout: int,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def reconciled():
        return reconciled_attempt(
            container,
            version=version,
            bundle_sha256=bundle_sha256,
            job_id=job_id,
            attempt_id=attempt_id,
            observation=observation,
        )

    return image_tool.wait_until(
        reconciled,
        timeout,
        f"legacy update scheduler reconciliation for {job_id}",
    )


def checked_current_after_reconciliation(
    client,
    feed: str,
    *,
    container: str,
    applied_job: dict[str, Any],
    target_version: str,
    bundle_sha256: str,
    timeout: int,
    observation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    applied = _require_exact_job(
        applied_job,
        job_id=None,
        operation="apply",
        status="success",
        version=target_version,
        bundle_sha256=bundle_sha256,
    )
    attempt_id = applied.get("scheduler_attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise LegacyAppUpgradeError(
            "Successful apply job is missing its scheduler attempt identity."
        )
    reconciled = wait_reconciled_attempt(
        container,
        version=target_version,
        bundle_sha256=bundle_sha256,
        job_id=applied["job_id"],
        attempt_id=attempt_id,
        timeout=timeout,
        observation=observation,
    )
    return reconciled, checked_current_on_v2(
        client, feed, target_version=target_version
    )


def restart_after_reconciliation(
    container: str,
    *,
    version: str,
    bundle_sha256: str,
    job_id: str,
    attempt_id: str,
    timeout: int,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempt = wait_reconciled_attempt(
        container,
        version=version,
        bundle_sha256=bundle_sha256,
        job_id=job_id,
        attempt_id=attempt_id,
        timeout=timeout,
        observation=observation,
    )
    image_tool.run(["docker", "restart", container], timeout=120)
    return attempt


def require_persisted_reconciliation(
    container: str,
    *,
    version: str,
    bundle_sha256: str,
    job_id: str,
    attempt_id: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    actual = reconciled_attempt(
        container,
        version=version,
        bundle_sha256=bundle_sha256,
        job_id=job_id,
        attempt_id=attempt_id,
    )
    if actual != expected:
        raise LegacyAppUpgradeError(
            "Update scheduler reconciliation did not persist across container restart."
        )
    return actual


def selected_status_evidence(status: dict[str, Any]) -> dict[str, Any]:
    active = status["active_bundle"]
    worker = status["worker_runtime"]
    dependencies = worker.get("dependencies")
    dependency_evidence: dict[str, Any]
    if isinstance(dependencies, dict):
        dependency_evidence = {}
        for name in (
            "requests",
            "cryptography",
            "sqlmodel",
            "uvicorn",
            "pydantic_core",
            "sqlalchemy",
            "httptools",
        ):
            record = dependencies.get(name)
            if not isinstance(record, dict):
                dependency_evidence[name] = {"invalid": True}
                continue
            dependency_evidence[name] = {
                "loaded": _diagnostic_scalar(record.get("loaded"), bool),
            }
            if record.get("loaded") is True:
                dependency_evidence[name].update(
                    path=_diagnostic_text(record.get("path")),
                    version=_diagnostic_text(record.get("version")),
                )
    else:
        dependency_evidence = {"invalid": True}
    return {
        "current_version": _diagnostic_text(status.get("current_version")),
        "image_version": _diagnostic_text(status.get("image_version")),
        "launcher_protocol": _diagnostic_scalar(status.get("launcher_protocol"), int),
        "runtime_source": _diagnostic_text(status.get("runtime_source")),
        "operation_busy": _diagnostic_scalar(status.get("operation_busy"), bool),
        "active_bundle": {
            "version": _diagnostic_text(active.get("version")),
            "path": _diagnostic_text(active.get("path")),
            "activation_id": _diagnostic_text(
                active.get("activation_id"), allow_none=True
            ),
            "activation_protocol": _diagnostic_scalar(
                active.get("activation_protocol"), int, allow_none=True
            ),
            "bundle_sha256": _diagnostic_text(
                (active.get("manifest") or {}).get("bundle_sha256")
                if isinstance(active.get("manifest"), dict)
                else None,
                allow_none=True,
            ),
            "git_sha": _diagnostic_text(
                (active.get("metadata") or {}).get("git_sha")
                if isinstance(active.get("metadata"), dict)
                else None,
                allow_none=True,
            ),
        },
        "worker_runtime": {
            "pid": _diagnostic_scalar(worker.get("pid"), int),
            "python_executable": _diagnostic_text(worker.get("python_executable")),
            "python_version": _diagnostic_text(worker.get("python_version")),
            "python_prefix": _diagnostic_text(worker.get("python_prefix")),
            "verified_generation": _diagnostic_text(
                worker.get("verified_generation"), allow_none=True
            ),
            "dependencies": dependency_evidence,
        },
    }


def current_check_evidence(checked: dict[str, Any]) -> dict[str, Any]:
    job = checked["last_job"]
    return {
        "current_version": _diagnostic_text(checked.get("current_version")),
        "image_version": _diagnostic_text(checked.get("image_version")),
        "launcher_protocol": _diagnostic_scalar(checked.get("launcher_protocol"), int),
        "runtime_source": _diagnostic_text(checked.get("runtime_source")),
        "catalog_state": _diagnostic_text(checked.get("catalog_state"), allow_none=True),
        "catalog_checked_at": _diagnostic_text(
            checked.get("catalog_checked_at"), allow_none=True
        ),
        "update_available": _diagnostic_scalar(
            checked.get("update_available"), bool
        ),
        "image_required": _diagnostic_scalar(checked.get("image_required"), bool),
        "last_job": update_job_evidence(job),
    }


def _diagnostic_text(value: object, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        return "<invalid>"
    return value[:DIAGNOSTIC_TEXT_LIMIT]


def _diagnostic_scalar(
    value: object, expected_type: type, *, allow_none: bool = False
) -> bool | int | None | str:
    if value is None and allow_none:
        return None
    if type(value) is not expected_type:
        return "<invalid>"
    return value


def update_job_evidence(job: object) -> dict[str, Any]:
    if not isinstance(job, dict):
        return {"invalid": True}
    return {
        key: _diagnostic_text(job.get(key), allow_none=True)
        for key in (
            "job_id",
            "operation",
            "status",
            "version",
            "bundle_sha256",
            "scheduler_attempt_id",
            "created_at",
            "updated_at",
            "completed_at",
        )
    }


def rollback_response_evidence(response: object) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"invalid": True}
    components = response.get("startup_components")
    retained_components: dict[str, Any] | str | None
    if components is None:
        retained_components = None
    elif not isinstance(components, dict):
        retained_components = "<invalid>"
    else:
        retained_components = {}
        for component in ("core", "ui"):
            marker = components.get(component)
            if marker is None:
                continue
            if not isinstance(marker, dict):
                retained_components[component] = {"invalid": True}
                continue
            retained_components[component] = {
                "healthy": _diagnostic_scalar(marker.get("healthy"), bool),
                "version": _diagnostic_text(marker.get("version"), allow_none=True),
                "ready_at": _diagnostic_text(marker.get("ready_at"), allow_none=True),
            }
        if set(components) - {"core", "ui"}:
            retained_components["unexpected_components"] = True
    return {
        "job_id": _diagnostic_text(response.get("job_id"), allow_none=True),
        "operation": _diagnostic_text(response.get("operation"), allow_none=True),
        "status": _diagnostic_text(response.get("status"), allow_none=True),
        "version": _diagnostic_text(response.get("version"), allow_none=True),
        "rolled_back_from": _diagnostic_text(
            response.get("rolled_back_from"), allow_none=True
        ),
        "restored_version": _diagnostic_text(
            response.get("restored_version"), allow_none=True
        ),
        "rollback_applied": _diagnostic_scalar(
            response.get("rollback_applied"), bool, allow_none=True
        ),
        "restart_required": _diagnostic_scalar(
            response.get("restart_required"), bool, allow_none=True
        ),
        "validated_at": _diagnostic_text(
            response.get("validated_at"), allow_none=True
        ),
        "startup_components": retained_components,
    }


def _catalog_asset(feed_root: Path) -> Path:
    first_lines = (feed_root / "feed.py").read_text(encoding="utf-8").splitlines()
    routes_line = next(line for line in first_lines if line.startswith("ROUTES="))
    routes = ast.literal_eval(routes_line.split("=", 1)[1])
    return feed_root / "assets" / routes["channelwatch.coderluii.dev/updates/v2/stable.json"]


def _capture_logs(
    containers: dict[str, str], output: Path
) -> tuple[dict[str, str], list[str], list[dict[str, Any]]]:
    output.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    empty_containers: list[str] = []
    failures: list[dict[str, Any]] = []
    for label, container in containers.items():
        path = output / f"{label}.log"
        try:
            result = image_tool.run(
                ["docker", "logs", container], check=False, timeout=30
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            path.write_text(
                f"[stdout]\n{stdout}\n[stderr]\n{stderr}", encoding="utf-8"
            )
            if result.returncode:
                failures.append(
                    {
                        "container": label,
                        "returncode": result.returncode,
                        "failure_summary": (
                            stderr or stdout or "docker logs failed"
                        ).strip()[:500],
                    }
                )
            elif not stdout.strip() and not stderr.strip():
                empty_containers.append(label)
        except Exception as exc:
            summary = str(exc)[:500]
            path.write_text(
                f"[capture-error]\n{type(exc).__name__}: {summary}\n",
                encoding="utf-8",
            )
            failures.append(
                {
                    "container": label,
                    "failure_type": type(exc).__name__,
                    "failure_summary": summary,
                }
            )
        hashes[label] = sha256_file(path)
    return hashes, empty_containers, failures


def _record_primary_failure(receipt: dict[str, Any], exc: Exception) -> None:
    receipt["status"] = "failed"
    if "failure_type" not in receipt:
        receipt["failure_type"] = type(exc).__name__
        receipt["failure_summary"] = str(exc)[:1000]


def _finalize_receipt(
    receipt: dict[str, Any],
    *,
    containers: dict[str, str],
    log_directory: Path,
    resources: Any,
    root: Path,
) -> None:
    try:
        log_hashes, empty_logs, log_failures = _capture_logs(
            containers, log_directory
        )
        receipt["logs_sha256"] = log_hashes
        if empty_logs or log_failures:
            reasons = []
            if log_failures:
                reasons.append(
                    f"{len(log_failures)} command failure"
                    + ("s" if len(log_failures) != 1 else "")
                )
            if empty_logs:
                reasons.append(
                    f"{len(empty_logs)} empty log"
                    + ("s" if len(empty_logs) != 1 else "")
                )
            log_error = LegacyAppUpgradeError(
                "Container log evidence failed: " + "; ".join(reasons) + "."
            )
            receipt["log_capture"] = {
                "status": "failed",
                "empty_containers": empty_logs,
                "command_failures": log_failures,
                "failure_type": type(log_error).__name__,
                "failure_summary": str(log_error)[:1000],
            }
            _record_primary_failure(receipt, log_error)
        else:
            receipt["log_capture"] = {
                "status": "passed",
                "empty_containers": [],
                "command_failures": [],
            }
    except Exception as exc:
        receipt["log_capture"] = {
            "status": "failed",
            "failure_type": type(exc).__name__,
            "failure_summary": str(exc)[:1000],
        }
        _record_primary_failure(receipt, exc)

    checks = receipt.get("checks")
    try:
        cleanup_failures = resources.cleanup()
    except Exception as exc:
        receipt["cleanup"] = {
            "status": "failed",
            "failures": [],
            "failure_type": type(exc).__name__,
            "failure_summary": str(exc)[:1000],
        }
        if isinstance(checks, dict):
            checks["cleanup"] = False
        _record_primary_failure(receipt, exc)
    else:
        receipt["cleanup"] = {
            "status": "passed" if not cleanup_failures else "failed",
            "failures": cleanup_failures,
        }
        if isinstance(checks, dict):
            checks["cleanup"] = not cleanup_failures
        if cleanup_failures:
            _record_primary_failure(
                receipt,
                LegacyAppUpgradeError(
                    "Owned Docker resources could not be removed."
                ),
            )
    shutil.rmtree(root, ignore_errors=True)


def qualify(args: argparse.Namespace) -> dict[str, Any]:
    architecture = image_tool.native_architecture(args.architecture)
    baselines = json.loads(args.baselines.read_text(encoding="utf-8"))
    release_config = json.loads(args.release_config.read_text(encoding="utf-8"))
    scenarios = validate_baselines(baselines, target_version=args.target_version)
    if validate_release_config(release_config, target_version=args.target_version) != baselines["legacy_sources"]:
        raise LegacyAppUpgradeError("Release configuration does not match legacy fixtures.")
    scenario = next((row for row in scenarios if row["id"] == args.scenario), None)
    if scenario is None:
        raise LegacyAppUpgradeError("Requested legacy scenario is unsupported.")
    release, bundle_url = verify_signed_assets(args.catalog, args.bundle, version=args.target_version, source_commit=args.source_commit)
    image = baselines["images"][scenario["image_version"]]
    reference, manifest, _image_id = image_tool._native_manifest(
        baselines["repository"], image["index_digest"], architecture,
        expected_manifest_digest=image["manifests"][architecture],
        expected_version=scenario["image_version"], expected_source_commit=image["source_commit"],
    )
    overlay_release = overlay_url = None
    if scenario["overlay_version"]:
        overlay = baselines["signed_overlays"][scenario["overlay_version"]]
        overlay_release, overlay_url = image_tool.verify_signed_baseline_assets(
            args.overlay_catalog, args.overlay_bundle,
            version=overlay["version"], source_commit=overlay["source_commit"],
        )
        if sha256_file(args.overlay_catalog) != overlay["catalog_sha256"] or sha256_file(args.overlay_bundle) != overlay["bundle_sha256"] or overlay_url != overlay["bundle_url"]:
            raise LegacyAppUpgradeError("Overlay assets do not match the published v1.4.5 fixture.")
    bridge_release = bridge_url = None
    if args.qualification_mode == "chained":
        bridge = baselines["bridge_release"]
        bridge_release, bridge_url = verify_signed_assets(
            args.bridge_catalog,
            args.bridge_bundle,
            version=bridge["version"],
            source_commit=bridge["source_commit"],
        )
        if (
            sha256_file(args.bridge_catalog) != bridge["catalog_sha256"]
            or sha256_file(args.bridge_bundle) != bridge["bundle_sha256"]
            or bridge_url != bridge["bundle_url"]
        ):
            raise LegacyAppUpgradeError("Bridge assets do not match the immutable v1.6.0 release.")
    prefix = f"cw-legacy-{architecture}-{secrets.token_hex(5)}"
    resources = image_tool.Resources(prefix)
    root = Path(tempfile.mkdtemp(prefix=prefix + "-"))
    app = resources.container("app")
    feed = resources.container("feed")
    receipt: dict[str, Any] = {
        "schema": 2, "status": "running", "evidence_kind": "native",
        "architecture": architecture, "scenario": scenario,
        "qualification_mode": args.qualification_mode,
        "target_version": args.target_version, "source_commit": args.source_commit,
        "catalog_sha256": sha256_file(args.catalog), "bundle_sha256": sha256_file(args.bundle),
        "release_config_sha256": sha256_file(args.release_config),
        "baseline": {"repository": baselines["repository"], "source_commit": image["source_commit"], "index_digest": image["index_digest"], "manifest_digest": manifest},
    }
    try:
        network = resources.create_network()
        volume = resources.create_volume()
        image_tool._seed_settings(reference, volume)
        receipt["scheduler_seed"] = seed_scheduler_between_checks(
            reference, volume
        )
        feed_root = root / "feed"
        feed_root.mkdir()
        capability_tool.create_tls_material(feed_root)
        routes = {CATALOG_URL: args.catalog.resolve(), bundle_url: args.bundle.resolve()}
        if overlay_url:
            routes[overlay_url] = args.overlay_bundle.resolve()
        if bridge_url:
            routes[bridge_url] = args.bridge_bundle.resolve()
        capability_tool.write_feed(feed_root, routes)
        catalog_asset = _catalog_asset(feed_root)
        if scenario["overlay_version"]:
            capability_tool.publish_feed_catalog(args.overlay_catalog, catalog_asset)
        image_tool.run([
            "docker", "run", "-d", "--name", feed, "--label", LABEL, "--network", network,
            "--network-alias", "github.com", "--network-alias", "channelwatch.coderluii.dev",
            "--mount", f"type=bind,source={feed_root},target=/feed,readonly",
            "--entrypoint", "/usr/local/bin/python", reference, "/feed/feed.py",
        ], timeout=60)
        username, password = "legacy-admin", secrets.token_urlsafe(36)
        image_tool.run([
            "docker", "run", "-d", "--pull=never", "--name", app, "--label", LABEL,
            "--network", network, "--platform", f"linux/{architecture}",
            "--mount", f"type=volume,source={volume},target=/config",
            "--mount", f"type=bind,source={feed_root / 'ca.pem'},target=/canary/ca.pem,readonly",
            "--env", "SSL_CERT_FILE=/canary/ca.pem", "--env", f"CW_ADMIN_USER={username}",
            "--env", f"CW_ADMIN_PASS={password}", "--env", "PUID=1000", "--env", "PGID=1000",
            "-p", "127.0.0.1::8501", reference,
        ], timeout=60)
        client = image_tool.ApiClient(image_tool.published_base_url(app), username, password)
        image_tool.wait_until(lambda: client.request("/healthz/startup").get("status") == "ready", args.startup_timeout, "legacy startup")
        client.login()
        image_tool._configure_encrypted_setting(client)
        if scenario["overlay_version"]:
            checked = client.request("/api/v1/update/check", {})
            stock_scenario = {**scenario, "overlay_version": None}
            wait_for_available_check_idle(
                client,
                checked=checked,
                target_version=scenario["overlay_version"],
                bundle_sha256=sha256_file(args.overlay_bundle),
                timeout=args.update_timeout,
                description="overlay update check idle",
                status_validator=lambda value: validate_source_status(
                    value, scenario=stock_scenario
                ),
            )
            overlay_job = wait_job(client, endpoint="/api/v1/update/apply", version=scenario["overlay_version"], timeout=args.update_timeout, image_version=scenario["image_version"])
            wait_for_overlay_idle(
                client,
                scenario=scenario,
                overlay_job=overlay_job,
                bundle_sha256=sha256_file(args.overlay_bundle),
                timeout=args.update_timeout,
            )
            overlay_attempt_id = str(
                overlay_job.get("scheduler_attempt_id") or ""
            )
            if not overlay_attempt_id:
                raise LegacyAppUpgradeError(
                    "Overlay update did not return a scheduler attempt identity."
                )
            overlay_identity_before_restart = container_identity(
                app, f"{baselines['repository']}@{manifest}"
            )
            overlay_observation: dict[str, Any] = {}
            overlay_reconciled_attempt = restart_after_reconciliation(
                app,
                version=scenario["overlay_version"],
                bundle_sha256=sha256_file(args.overlay_bundle),
                job_id=overlay_job["job_id"],
                attempt_id=overlay_attempt_id,
                timeout=args.update_timeout,
                observation=overlay_observation,
            )
            client.set_base_url(image_tool.published_base_url(app))
            image_tool.wait_until(
                lambda: client.request("/healthz/startup").get("status") == "ready",
                args.startup_timeout,
                "overlay reconciliation restart",
            )
            client.login()
            require_persisted_reconciliation(
                app,
                version=scenario["overlay_version"],
                bundle_sha256=sha256_file(args.overlay_bundle),
                job_id=overlay_job["job_id"],
                attempt_id=overlay_attempt_id,
                expected=overlay_reconciled_attempt,
            )
            receipt["overlay_scheduler_checkpoint"] = require_scheduler_checkpoint(
                app,
                expected_schema=receipt["scheduler_seed"]["schema"],
            )
            wait_for_overlay_idle(
                client,
                scenario=scenario,
                overlay_job=overlay_job,
                bundle_sha256=sha256_file(args.overlay_bundle),
                timeout=args.update_timeout,
            )
            overlay_identity_after_restart = container_identity(
                app, f"{baselines['repository']}@{manifest}"
            )
            validate_identity_invariant(
                [overlay_identity_before_restart, overlay_identity_after_restart]
            )
            receipt["overlay_scheduler_attempt"] = overlay_reconciled_attempt
            receipt["overlay_scheduler_observation"] = overlay_observation
            receipt["overlay_restart_identity"] = {
                "before": overlay_identity_before_restart,
                "after": overlay_identity_after_restart,
            }
            capability_tool.publish_feed_catalog(args.catalog, catalog_asset)
        if args.qualification_mode == "chained":
            bridge = baselines["bridge_release"]
            bridge_scenario = {
                **scenario,
                "source_application_version": bridge["version"],
                "overlay_version": bridge["version"],
            }
            bridge_before_identity = container_identity(
                app, f"{baselines['repository']}@{manifest}"
            )
            bridge_before_inventory = legacy_image_inventory(
                app, image_version=scenario["image_version"]
            )
            bridge_before_storage = runtime_storage(app)
            bridge_before_hashes = image_tool.persistent_hashes(app)
            bridge_before_db = database_canary(app, create=True)
            capability_tool.publish_feed_catalog(args.bridge_catalog, catalog_asset)
            checked = client.request("/api/v1/update/check", {})
            wait_for_available_check_idle(
                client,
                checked=checked,
                target_version=bridge["version"],
                bundle_sha256=bridge["bundle_sha256"],
                timeout=args.update_timeout,
                description="v1.6.0 bridge update check idle",
                status_validator=lambda value: validate_source_status(
                    value, scenario=scenario
                ),
            )
            bridge_job = wait_job(
                client,
                endpoint="/api/v1/update/apply",
                version=bridge["version"],
                timeout=args.update_timeout,
                image_version=scenario["image_version"],
            )
            wait_for_overlay_idle(
                client,
                scenario=bridge_scenario,
                overlay_job=bridge_job,
                bundle_sha256=bridge["bundle_sha256"],
                timeout=args.update_timeout,
            )
            bridge_attempt_id = str(bridge_job.get("scheduler_attempt_id") or "")
            if not bridge_attempt_id:
                raise LegacyAppUpgradeError(
                    "v1.6.0 bridge update did not return a scheduler attempt identity."
                )
            bridge_observation: dict[str, Any] = {}
            bridge_reconciled = restart_after_reconciliation(
                app,
                version=bridge["version"],
                bundle_sha256=bridge["bundle_sha256"],
                job_id=bridge_job["job_id"],
                attempt_id=bridge_attempt_id,
                timeout=args.update_timeout,
                observation=bridge_observation,
            )
            client.set_base_url(image_tool.published_base_url(app))
            image_tool.wait_until(
                lambda: client.request("/healthz/startup").get("status") == "ready",
                args.startup_timeout,
                "v1.6.0 bridge reconciliation restart",
            )
            client.login()
            require_persisted_reconciliation(
                app,
                version=bridge["version"],
                bundle_sha256=bridge["bundle_sha256"],
                job_id=bridge_job["job_id"],
                attempt_id=bridge_attempt_id,
                expected=bridge_reconciled,
            )
            bridge_status = client.request("/api/v1/update/status")
            validate_source_status(bridge_status, scenario=bridge_scenario)
            bridge_runtime = selected_runtime_info(
                app,
                version=bridge["version"],
                application_dir=bridge_status["active_bundle"]["path"],
            )
            bridge_after_identity = container_identity(
                app, f"{baselines['repository']}@{manifest}"
            )
            bridge_after_inventory = legacy_image_inventory(
                app, image_version=scenario["image_version"]
            )
            capability_tool.validate_image_inventory(
                bridge_before_inventory, bridge_after_inventory
            )
            validate_identity_invariant(
                [bridge_before_identity, bridge_after_identity]
            )
            if (
                image_tool.persistent_hashes(app) != bridge_before_hashes
                or database_canary(app) != bridge_before_db
                or not image_tool.credential_api_semantics(client)
            ):
                raise LegacyAppUpgradeError(
                    "Persistent state changed while applying the v1.6.0 bridge."
                )
            bridge_after_storage = runtime_storage(
                app, active_path=bridge_status["active_bundle"]["path"]
            )
            if not (
                bridge_before_storage["dependency_generation_absent"]
                and bridge_after_storage["dependency_generation_absent"]
            ):
                raise LegacyAppUpgradeError(
                    "The v1.6.0 bridge created a dependency generation on a legacy image."
                )
            receipt["bridge_qualification"] = {
                "release": bridge,
                "catalog_sha256": sha256_file(args.bridge_catalog),
                "bundle_sha256": sha256_file(args.bridge_bundle),
                "job": update_job_evidence(bridge_job),
                "status": bridge_status,
                "selected_runtime_info": bridge_runtime,
                "identity": {
                    "before": bridge_before_identity,
                    "after": bridge_after_identity,
                },
                "runtime_storage": {
                    "before": bridge_before_storage,
                    "after": bridge_after_storage,
                },
                "scheduler_attempt": bridge_reconciled,
                "scheduler_observation": bridge_observation,
            }
            capability_tool.publish_feed_catalog(args.catalog, catalog_asset)
            scenario = bridge_scenario
        source_status = client.request("/api/v1/update/status")
        validate_source_status(source_status, scenario=scenario)
        require_legacy_encrypted_settings(app, client)
        before_hashes = image_tool.persistent_hashes(app)
        before_db = database_canary(app, create=True)
        before_inventory = legacy_image_inventory(
            app, image_version=scenario["image_version"]
        )
        before_identity = container_identity(app, f"{baselines['repository']}@{manifest}")
        before_storage = runtime_storage(app)
        receipt["target_check_idle_checkpoint"] = current_check_evidence(
            check_target_until_idle(
                client,
                scenario=scenario,
                target_version=args.target_version,
                bundle_sha256=receipt["bundle_sha256"],
                timeout=args.update_timeout,
            )
        )
        applied = wait_job(client, endpoint="/api/v1/update/apply", version=args.target_version, timeout=args.update_timeout, image_version=scenario["image_version"])
        receipt["apply_job"] = update_job_evidence(applied)
        image_tool.wait_until(lambda: client.request("/api/v1/update/status").get("current_version") == args.target_version, args.update_timeout, "legacy app activation")
        client.login()
        applied_status = wait_for_job_idle(
            client,
            job=applied,
            operation="apply",
            status="success",
            version=args.target_version,
            bundle_sha256=receipt["bundle_sha256"],
            timeout=args.update_timeout,
            description="legacy apply operation idle",
            status_validator=lambda value: validate_selected_status(
                value,
                target_version=args.target_version,
                image_version=scenario["image_version"],
                bundle_sha256=receipt["bundle_sha256"],
            ),
        )
        receipt["apply_status_checkpoint"] = selected_status_evidence(applied_status)
        scheduler_observation: dict[str, Any] = {}
        receipt["scheduler_attempt_observation"] = scheduler_observation
        reconciled_apply_attempt, post_activation_check = (
            checked_current_after_reconciliation(
                client,
                feed,
                container=app,
                applied_job=applied,
                target_version=args.target_version,
                bundle_sha256=receipt["bundle_sha256"],
                timeout=args.update_timeout,
                observation=scheduler_observation,
            )
        )
        wait_for_job_idle(
            client,
            job=post_activation_check["last_job"],
            operation="check",
            status="current",
            version=args.target_version,
            timeout=args.update_timeout,
            description="post-activation update check idle",
            status_validator=lambda value: validate_selected_status(
                value,
                target_version=args.target_version,
                image_version=scenario["image_version"],
                bundle_sha256=receipt["bundle_sha256"],
            ),
        )
        receipt["post_activation_check_checkpoint"] = current_check_evidence(
            post_activation_check
        )
        apply_runtime_info = selected_runtime_info(
            app,
            version=args.target_version,
            application_dir=applied_status["active_bundle"]["path"],
        )
        frontend_tool.verify_frontend(app, args.bundle)
        browser_apply = image_tool.browser_smoke(int(client.base_url.rsplit(":", 1)[1]))
        cli_apply = image_tool.run(["docker", "exec", app, "/usr/local/bin/channelwatch", "doctor", "config-check"], timeout=60)
        after_apply_identity = container_identity(app, f"{baselines['repository']}@{manifest}")
        after_apply_inventory = legacy_image_inventory(
            app, image_version=scenario["image_version"]
        )
        capability_tool.validate_image_inventory(before_inventory, after_apply_inventory)
        if image_tool.persistent_hashes(app) != before_hashes or database_canary(app) != before_db or not image_tool.credential_api_semantics(client):
            raise LegacyAppUpgradeError("Persistent configuration, database, or credential state changed after Apply.")
        after_apply_storage = runtime_storage(
            app, active_path=applied_status["active_bundle"]["path"]
        )
        reconciled_before_restart = restart_after_reconciliation(
            app,
            version=args.target_version,
            bundle_sha256=receipt["bundle_sha256"],
            job_id=applied["job_id"],
            attempt_id=str(applied.get("scheduler_attempt_id") or ""),
            timeout=args.update_timeout,
            observation=scheduler_observation,
        )
        if reconciled_before_restart != reconciled_apply_attempt:
            raise LegacyAppUpgradeError(
                "Update scheduler reconciliation changed before container restart."
            )
        client.set_base_url(image_tool.published_base_url(app))
        image_tool.wait_until(lambda: client.request("/healthz/startup").get("status") == "ready", args.startup_timeout, "legacy restart")
        client.login()
        restart_status = wait_for_job_idle(
            client,
            job=post_activation_check["last_job"],
            operation="check",
            status="current",
            version=args.target_version,
            timeout=args.update_timeout,
            description="post-restart prior check idle",
            status_validator=lambda value: validate_selected_status(
                value,
                target_version=args.target_version,
                image_version=scenario["image_version"],
                bundle_sha256=receipt["bundle_sha256"],
            ),
        )
        post_restart_check = checked_current_on_v2(
            client, feed, target_version=args.target_version
        )
        wait_for_job_idle(
            client,
            job=post_restart_check["last_job"],
            operation="check",
            status="current",
            version=args.target_version,
            timeout=args.update_timeout,
            description="post-restart update check idle",
            status_validator=lambda value: validate_selected_status(
                value,
                target_version=args.target_version,
                image_version=scenario["image_version"],
                bundle_sha256=receipt["bundle_sha256"],
            ),
        )
        restart_runtime_info = selected_runtime_info(
            app,
            version=args.target_version,
            application_dir=restart_status["active_bundle"]["path"],
        )
        frontend_tool.verify_frontend(app, args.bundle)
        browser_restart = image_tool.browser_smoke(int(client.base_url.rsplit(":", 1)[1]))
        cli_restart = image_tool.run(["docker", "exec", app, "/usr/local/bin/channelwatch", "doctor", "config-check"], timeout=60)
        after_restart_identity = container_identity(app, f"{baselines['repository']}@{manifest}")
        after_restart_inventory = legacy_image_inventory(
            app, image_version=scenario["image_version"]
        )
        capability_tool.validate_image_inventory(before_inventory, after_restart_inventory)
        if image_tool.persistent_hashes(app) != before_hashes or database_canary(app) != before_db or not image_tool.credential_api_semantics(client):
            raise LegacyAppUpgradeError("Persistent state changed after container restart.")
        require_persisted_reconciliation(
            app,
            version=args.target_version,
            bundle_sha256=receipt["bundle_sha256"],
            job_id=applied["job_id"],
            attempt_id=str(applied.get("scheduler_attempt_id") or ""),
            expected=reconciled_apply_attempt,
        )
        rollback_response = request_update_operation(
            client, "/api/v1/update/rollback", {}
        )
        receipt["rollback_response_checkpoint"] = rollback_response_evidence(
            rollback_response
        )
        rollback = capability_tool.validate_rollback_response(
            rollback_response,
            source_version=args.target_version,
            target_version=scenario["source_application_version"],
        )
        image_tool.wait_until(lambda: client.request("/api/v1/update/status").get("current_version") == scenario["source_application_version"], args.update_timeout, "legacy rollback")
        client.login()
        rollback_job = capability_tool.wait_for_successful_job(
            client,
            job_id=rollback["job_id"],
            timeout=args.update_timeout,
            description="legacy rollback job",
        )
        rollback_status = wait_for_job_idle(
            client,
            job=rollback_job,
            operation="rollback",
            status="success",
            version=args.target_version,
            timeout=args.update_timeout,
            description="legacy rollback operation idle",
            status_validator=lambda value: validate_source_status(
                value, scenario=scenario
            ),
        )
        after_rollback_storage = runtime_storage(
            app,
            active_path=(
                rollback_status["active_bundle"]["path"]
                if rollback_status.get("active_bundle") else None
            ),
        )
        after_rollback_identity = container_identity(app, f"{baselines['repository']}@{manifest}")
        after_rollback_inventory = legacy_image_inventory(
            app, image_version=scenario["image_version"]
        )
        capability_tool.validate_image_inventory(before_inventory, after_rollback_inventory)
        if image_tool.persistent_hashes(app) != before_hashes or database_canary(app) != before_db or not image_tool.credential_api_semantics(client):
            raise LegacyAppUpgradeError("Persistent state changed after rollback.")
        browser_rollback = image_tool.browser_smoke(int(client.base_url.rsplit(":", 1)[1]))
        cli_rollback = image_tool.run(["docker", "exec", app, "/usr/local/bin/channelwatch", "doctor", "config-check"], timeout=60)
        reapplied = wait_job(client, endpoint="/api/v1/update/retry", version=args.target_version, timeout=args.update_timeout, image_version=scenario["image_version"])
        image_tool.wait_until(lambda: client.request("/api/v1/update/status").get("current_version") == args.target_version, args.update_timeout, "legacy reapply")
        client.login()
        reapply_status = wait_for_job_idle(
            client,
            job=reapplied,
            operation="apply",
            status="success",
            version=args.target_version,
            bundle_sha256=receipt["bundle_sha256"],
            timeout=args.update_timeout,
            description="legacy reapply operation idle",
            status_validator=lambda value: validate_selected_status(
                value,
                target_version=args.target_version,
                image_version=scenario["image_version"],
                bundle_sha256=receipt["bundle_sha256"],
            ),
        )
        after_reapply_storage = runtime_storage(
            app, active_path=reapply_status["active_bundle"]["path"]
        )
        reapply_runtime_info = selected_runtime_info(
            app,
            version=args.target_version,
            application_dir=reapply_status["active_bundle"]["path"],
        )
        frontend_tool.verify_frontend(app, args.bundle)
        browser_reapply = image_tool.browser_smoke(int(client.base_url.rsplit(":", 1)[1]))
        cli_reapply = image_tool.run(["docker", "exec", app, "/usr/local/bin/channelwatch", "doctor", "config-check"], timeout=60)
        after_reapply_identity = container_identity(app, f"{baselines['repository']}@{manifest}")
        after_reapply_inventory = legacy_image_inventory(
            app, image_version=scenario["image_version"]
        )
        capability_tool.validate_image_inventory(before_inventory, after_reapply_inventory)
        if image_tool.persistent_hashes(app) != before_hashes or database_canary(app) != before_db or not image_tool.credential_api_semantics(client):
            raise LegacyAppUpgradeError("Persistent state changed after reapply.")
        identities = {
            "before": before_identity, "after_apply": after_apply_identity,
            "after_restart": after_restart_identity, "after_rollback": after_rollback_identity,
            "after_reapply": after_reapply_identity,
        }
        validate_identity_invariant(list(identities.values()))
        storage = {
            "before": before_storage,
            "after_apply": after_apply_storage,
            "after_rollback": after_rollback_storage,
            "after_reapply": after_reapply_storage,
        }
        if not all(row["dependency_generation_absent"] for row in storage.values()):
            raise LegacyAppUpgradeError("Legacy update created a dependency generation.")
        receipt.update({
            "status": "passed", "identity": identities,
            "source_status": source_status, "apply_status": applied_status,
            "post_activation_check": post_activation_check,
            "post_restart_check": post_restart_check,
            "selected_runtime_info": {
                "after_apply": apply_runtime_info,
                "after_restart": restart_runtime_info,
                "after_reapply": reapply_runtime_info,
            },
            "restart_status": restart_status, "rollback_status": rollback_status,
            "reapply_status": reapply_status,
            "apply_job": update_job_evidence(applied),
            "reconciled_apply_attempt": reconciled_apply_attempt,
            "rollback_job": rollback_job, "reapply_job": reapplied,
            "image_inventory_sha256": before_inventory["sha256"],
            "persistent_hashes": before_hashes, "database_canary_sha256": before_db,
            "runtime_storage": storage,
            "browser_sha256": {
                "after_apply": browser_apply,
                "after_restart": browser_restart,
                "after_rollback": browser_rollback,
                "after_reapply": browser_reapply,
            },
            "cli_output_sha256": {
                "after_apply": hashlib.sha256(cli_apply.stdout.encode()).hexdigest(),
                "after_restart": hashlib.sha256(cli_restart.stdout.encode()).hexdigest(),
                "after_rollback": hashlib.sha256(cli_rollback.stdout.encode()).hexdigest(),
                "after_reapply": hashlib.sha256(cli_reapply.stdout.encode()).hexdigest(),
            },
            "checks": {name: True for name in (
                "signed_assets", "update_check", "apply_job", "same_container", "same_image",
                "selected_runtime", "image_packages_unchanged", "persistent_state", "database_canary",
                "credentials", "frontend", "cli", "restart", "rollback", "reapply",
                "no_dependency_generation", "cleanup",
            )},
        })
        receipt["checks"]["cleanup"] = False
    except Exception as exc:
        _record_primary_failure(receipt, exc)
    finally:
        _finalize_receipt(
            receipt,
            containers={"app": app, "feed": feed},
            log_directory=args.log_directory,
            resources=resources,
            root=root,
        )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--release-config", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--overlay-catalog", type=Path)
    parser.add_argument("--overlay-bundle", type=Path)
    parser.add_argument(
        "--qualification-mode", choices=("direct", "chained"), required=True
    )
    parser.add_argument("--bridge-catalog", type=Path)
    parser.add_argument("--bridge-bundle", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--log-directory", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=int, default=240)
    parser.add_argument("--update-timeout", type=int, default=600)
    args = parser.parse_args()
    args.target_version = args.target_version.strip().lstrip("v")
    if SHA40.fullmatch(args.source_commit) is None:
        raise SystemExit("Source commit must be a complete lowercase Git SHA.")
    if args.scenario.startswith("overlay-") and (args.overlay_catalog is None or args.overlay_bundle is None):
        raise SystemExit("Overlay scenarios require the exact signed v1.4.5 assets.")
    if args.qualification_mode == "chained" and (
        args.bridge_catalog is None or args.bridge_bundle is None
    ):
        raise SystemExit("Chained qualification requires the exact signed v1.6.0 assets.")
    receipt = qualify(args)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
