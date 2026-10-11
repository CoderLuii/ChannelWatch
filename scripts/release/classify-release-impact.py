#!/usr/bin/env python3
"""Classify whether a ChannelWatch release requires a new container image."""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from release_version_policy import validate_release_config


class ReleaseImpact(NamedTuple):
    delivery_mode: str
    image_required: bool
    image_refresh_recommended: bool
    triggering_paths: tuple[str, ...]
    refresh_paths: tuple[str, ...]


class ReleaseImpactMismatch(RuntimeError):
    pass


EXACT_RUNTIME_PATHS = {
    "app/bin/channelwatch",
    "app/core/docker-entrypoint.py",
    "app/core/runtime_launcher.py",
    "app/core/runtime_image_bootstrap.py",
    "app/core/runtime_release_trust.py",
    "app/core/runtime_generation.py",
    "app/core/runtime_dependency_bridge.py",
    "app/core/runtime_worker.py",
    "deploy/config/supervisor/supervisord.conf.template",
    "deploy/docker/Dockerfile.dockerignore",
}

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT_COMPATIBILITY_PATH = "app/core/docker-entrypoint.py"
RUNTIME_LAUNCHER_COMPATIBILITY_PATH = "app/core/runtime_launcher.py"
LAUNCHER_PROTOCOL_COMPATIBILITY_PATH = "deploy/docker/Dockerfile"
AUDITED_RUNTIME_COMPATIBILITY_PATHS = frozenset(
    {
        ENTRYPOINT_COMPATIBILITY_PATH,
        RUNTIME_LAUNCHER_COMPATIBILITY_PATH,
        LAUNCHER_PROTOCOL_COMPATIBILITY_PATH,
    }
)
MINIMUM_IMAGE_APP_UPDATE_COMPATIBILITY_PATHS = frozenset(
    {RUNTIME_LAUNCHER_COMPATIBILITY_PATH}
)
LEGACY_BRIDGE_KIND = "legacy_update_bridge_v1"
LEGACY_BRIDGE_VERSION = "0.9.18"
LEGACY_BRIDGE_MINIMUM_IMAGE = "0.9.11"
LEGACY_BRIDGE_TAGS = tuple(f"v0.9.{patch}" for patch in range(11, 18))
IMAGE_PULL_ONLY_SOURCES = {
    tag: {
        "required_image_version": LEGACY_BRIDGE_VERSION,
        "preserve_config": True,
        "in_app_update_supported": False,
        "pre_pull_false_success_possible": True,
        "recovery_image_repairs_marker": True,
        "reason": "published_image_cannot_activate_bridge_bundle",
    }
    for tag in ("v0.9.9", "v0.9.10")
}
LEGACY_BRIDGE_ACTIVATIONS = {
    tag: ("protocol_1_adoption" if tag <= "v0.9.15" else "protocol_2_quorum")
    for tag in LEGACY_BRIDGE_TAGS
}
HISTORICAL_SUCCESS_SCENARIO = "activation_success"
HISTORICAL_FAILURE_SCENARIO = "activation_failure"
HISTORICAL_TAMPER_SCENARIO = "tamper_rejection"
HISTORICAL_RECOVERY_SCENARIO = "image_refresh_recovery"
HISTORICAL_RECOVERY_VERSIONS = ("0.9.9", "0.9.10")
HISTORICAL_FAILURE_VARIANTS = {
    "0.9.15": "ui",
    "0.9.17": "core",
}
HISTORICAL_TAMPER_VERSIONS = ("0.9.15", "0.9.17")

RUNTIME_PREFIXES = ("deploy/requirements/",)
RUNTIME_CAPABILITY_EVIDENCE_TRIGGER = "runtime-capability-evidence"
MAX_DEPENDENCY_INVENTORY_BYTES = 2 * 1024 * 1024
MAX_IMAGE_INVENTORY_PATHS = 100000
MAX_IMAGE_INVENTORY_BYTES = 512 * 1024 * 1024
TRACKED_RUNTIME_DISTRIBUTIONS = {
    "requests": "requests",
    "cryptography": "cryptography",
    "sqlmodel": "sqlmodel",
    "uvicorn": "uvicorn",
    "pydantic_core": "pydantic-core",
    "sqlalchemy": "sqlalchemy",
    "httptools": "httptools",
}
IMAGE_BOOTSTRAP_PATHS = frozenset(
    {
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
    }
)
PROTOCOL_FOUR_MINIMUM_IMAGE = (1, 5, 0)
PROTOCOL_FOUR_ARCHITECTURES = frozenset({"amd64", "arm64"})
_HEX_40_RE = re.compile(r"[0-9a-f]{40}")
_HEX_64_RE = re.compile(r"[0-9a-f]{64}")

IMAGE_REFRESH_PATHS = {
    "app/core/helpers/atomic_io.py",
    "app/core/helpers/migration.py",
    "app/core/update_catalog.py",
    "app/ui/pnpm-lock.yaml",
    "app/ui/pnpm-workspace.yaml",
}

IMAGE_REFRESH_PREFIXES = (
    "deploy/compose/",
    "deploy/helm/channelwatch/",
    "deploy/unraid/",
)

STRUCTURED_RELEASE_PATHS = {
    "app/ui/package.json",
    "deploy/helm/channelwatch/Chart.yaml",
    "deploy/helm/channelwatch/values.yaml",
    "deploy/docker/Dockerfile",
}

# These defaults affect only a brand-new configuration created by a matching
# optional image. They do not participate in launcher behavior and are never
# applied to an existing settings file by an app bundle. Keeping this allowlist
# narrow prevents unrelated entrypoint changes from being misclassified as an
# in-app release.
FRESH_NOTIFICATION_DEFAULT_KEYS = frozenset(
    {
        "alert_channel_watching",
        "alert_vod_watching",
        "alert_dvr_health",
        "rd_alert_scheduled",
        "rd_alert_started",
        "rd_alert_completed",
        "rd_alert_cancelled",
        "rd_alert_failed",
        "rd_alert_skipped",
        "rd_alert_missed",
        "rd_alert_interrupted",
        "dvr_alert_unreachable",
        "dvr_alert_recovered",
        "dvr_health_alert_delay_seconds",
        "notification_preferences_version",
    }
)


def normalize_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix().lstrip("./")


def requires_image(path: str) -> bool:
    normalized = normalize_path(path)
    return normalized in EXACT_RUNTIME_PATHS or normalized.startswith(RUNTIME_PREFIXES)


def recommends_image_refresh(path: str) -> bool:
    normalized = normalize_path(path)
    return normalized in IMAGE_REFRESH_PATHS or normalized.startswith(
        IMAGE_REFRESH_PREFIXES
    )


def _impact(required: set[str], refresh: set[str]) -> ReleaseImpact:
    required_paths = tuple(sorted(required))
    refresh_paths = tuple(sorted(refresh - required))
    if required_paths:
        delivery_mode = "image_required"
    elif refresh_paths:
        delivery_mode = "app_update_with_image_refresh"
    else:
        delivery_mode = "app_update"
    return ReleaseImpact(
        delivery_mode,
        bool(required_paths),
        bool(refresh_paths),
        required_paths,
        refresh_paths,
    )


def _entrypoint_without_default_settings(
    source: str | None,
) -> tuple[str, dict[str, str]] | None:
    """Return executable entrypoint text and literal fresh-install defaults."""

    if source is None:
        return None
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    assignment: ast.Assign | None = None
    for node in module.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "DEFAULT_SETTINGS":
            assignment = node
            break
    if assignment is None or assignment.end_lineno is None:
        return None
    if not isinstance(assignment.value, ast.Dict):
        return None
    defaults: dict[str, str] = {}
    for key_node, value_node in zip(
        assignment.value.keys, assignment.value.values, strict=True
    ):
        if not isinstance(key_node, ast.Constant) or not isinstance(
            key_node.value, str
        ):
            return None
        defaults[key_node.value] = ast.dump(value_node, include_attributes=False)
    lines = source.splitlines(keepends=True)
    start = assignment.lineno - 1
    end = assignment.end_lineno
    normalized = (
        "".join(lines[:start]) + "DEFAULT_SETTINGS = {}\n" + "".join(lines[end:])
    )
    return normalized, defaults


def _only_fresh_notification_defaults_changed(
    before: str | None, after: str | None
) -> bool:
    """Allow only the explicit fresh-install alert-policy table to differ."""

    parsed_before = _entrypoint_without_default_settings(before)
    parsed_after = _entrypoint_without_default_settings(after)
    if parsed_before is None or parsed_after is None:
        return False
    before_runtime, before_defaults = parsed_before
    after_runtime, after_defaults = parsed_after
    if before_runtime != after_runtime:
        return False
    changed_keys = {
        key
        for key in set(before_defaults) | set(after_defaults)
        if before_defaults.get(key) != after_defaults.get(key)
    }
    return bool(changed_keys) and changed_keys <= FRESH_NOTIFICATION_DEFAULT_KEYS


def classify_paths(paths: list[str]) -> ReleaseImpact:
    required = {normalize_path(path) for path in paths if requires_image(path)}
    refresh = {normalize_path(path) for path in paths if recommends_image_refresh(path)}
    return _impact(required, refresh)


def apply_release_version_policy(
    result: ReleaseImpact, policy: object | None
) -> ReleaseImpact:
    """Enforce image milestones unless a validated capability floor is retained."""
    if policy is None or not bool(getattr(policy, "image_milestone", False)):
        return result
    return _impact(
        set(result.triggering_paths) | {"scripts/release/release-config.json"},
        set(result.refresh_paths),
    )


def expected_entrypoint_compatibility_declaration() -> dict[str, object]:
    """Return the one audited image-runtime compatibility exception."""

    return {
        "kind": LEGACY_BRIDGE_KIND,
        "candidate_version": LEGACY_BRIDGE_VERSION,
        "source_tags": list(LEGACY_BRIDGE_TAGS),
        "minimum_image_version": LEGACY_BRIDGE_MINIMUM_IMAGE,
        "expected_activations": dict(LEGACY_BRIDGE_ACTIVATIONS),
        "image_pull_only_sources": IMAGE_PULL_ONLY_SOURCES,
    }


def _declared_runtime_compatibility_paths(
    config: dict[str, object],
) -> frozenset[str]:
    raw = config.get("runtime_compatibility_evidence", {})
    if not isinstance(raw, dict):
        raise ReleaseImpactMismatch(
            "release-config runtime_compatibility_evidence must be an object"
        )
    unsupported = set(raw) - AUDITED_RUNTIME_COMPATIBILITY_PATHS
    if unsupported:
        raise ReleaseImpactMismatch(
            "release-config attempts to override non-audited image-runtime paths: "
            + ", ".join(sorted(str(path) for path in unsupported))
        )
    if raw and config.get("version") != LEGACY_BRIDGE_VERSION:
        raise ReleaseImpactMismatch(
            "the audited runtime compatibility exception is limited to v0.9.18"
        )
    expected = expected_entrypoint_compatibility_declaration()
    invalid = {
        str(path): declaration
        for path, declaration in raw.items()
        if declaration != expected
    }
    if invalid:
        raise ReleaseImpactMismatch(
            "release-config runtime compatibility evidence declaration does "
            "not match the audited v0.9.18 bridge contract: "
            + ", ".join(sorted(invalid))
        )
    return frozenset(str(path) for path in raw)


def _load_legacy_bridge_verifier():
    path = ROOT / "scripts" / "release" / "verify-legacy-update-bridge.py"
    spec = importlib.util.spec_from_file_location(
        "channelwatch_legacy_update_bridge_verifier", path
    )
    if spec is None or spec.loader is None:
        raise ReleaseImpactMismatch(
            "historical compatibility verifier could not be loaded"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_bridge_result(result: object, *, tag: str) -> None:
    if not isinstance(result, dict):
        raise ReleaseImpactMismatch(
            f"historical compatibility result for {tag} is invalid"
        )
    expected = {
        "tag": tag,
        "manifest_version": LEGACY_BRIDGE_VERSION,
        "bundle_version": LEGACY_BRIDGE_VERSION,
        "check_status": "available",
        "apply_status": "restarting",
        "applied_active_version": LEGACY_BRIDGE_VERSION,
        "active_version": LEGACY_BRIDGE_VERSION,
        "journal_replayed": tag in {"v0.9.16", "v0.9.17"},
        "source_acceptance": "verified",
    }
    mismatches = {
        key: (result.get(key), value)
        for key, value in expected.items()
        if result.get(key) != value
    }
    if mismatches:
        raise ReleaseImpactMismatch(
            f"historical compatibility result for {tag} did not match the "
            f"audited bridge contract: {mismatches}"
        )


def verify_entrypoint_compatibility_evidence(
    *,
    manifest_path: Path,
    bundle_path: Path,
    public_keys: dict[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Execute every historical updater against the exact signed artifacts."""

    if not manifest_path.is_file() or not bundle_path.is_file():
        raise ReleaseImpactMismatch(
            "entrypoint compatibility requires the exact manifest and bundle artifacts"
        )
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseImpactMismatch(
            "entrypoint compatibility manifest could not be read"
        ) from exc
    payload = raw_manifest.get("payload") if isinstance(raw_manifest, dict) else None
    if not isinstance(payload, dict):
        raise ReleaseImpactMismatch(
            "entrypoint compatibility manifest payload is invalid"
        )
    required_bridge = {
        "version": LEGACY_BRIDGE_VERSION,
        "image_required": False,
        "delivery_mode": "app_update_with_image_refresh",
        "minimum_image_version": LEGACY_BRIDGE_MINIMUM_IMAGE,
        "updater_protocol": 2,
        "recommended_image_version": LEGACY_BRIDGE_VERSION,
    }
    mismatches = {
        key: (payload.get(key), value)
        for key, value in required_bridge.items()
        if payload.get(key) != value
    }
    if mismatches:
        raise ReleaseImpactMismatch(
            f"entrypoint compatibility manifest does not match the audited "
            f"bridge contract: {mismatches}"
        )

    verifier = _load_legacy_bridge_verifier()
    results: list[dict[str, object]] = []
    for tag in LEGACY_BRIDGE_TAGS:
        try:
            result = verifier.verify_tag(
                tag,
                manifest_path=manifest_path,
                bundle_path=bundle_path,
                expected_version=LEGACY_BRIDGE_VERSION,
                public_keys=public_keys,
            )
        except Exception as exc:
            raise ReleaseImpactMismatch(
                f"historical compatibility replay failed for {tag}: {exc}"
            ) from exc
        _verify_bridge_result(result, tag=tag)
        results.append(result)
    return tuple(results)


def _historical_image_evidence_key(
    result: dict[str, object],
) -> str:
    """Validate and return the generator's exact scenario key."""

    source_version = result.get("source_version")
    if not isinstance(source_version, str) or not source_version:
        raise ReleaseImpactMismatch(
            "historical published-image evidence has an invalid source version"
        )
    scenario = result.get("scenario")
    if not isinstance(scenario, str) or not scenario:
        raise ReleaseImpactMismatch(
            "historical published-image evidence has an invalid scenario"
        )
    variant: str | None = None
    if scenario in {HISTORICAL_SUCCESS_SCENARIO, HISTORICAL_RECOVERY_SCENARIO}:
        if any(name in result for name in ("failed_component", "tamper_case")):
            raise ReleaseImpactMismatch(
                f"historical {scenario} evidence has an unexpected variant"
            )
    if scenario == HISTORICAL_FAILURE_SCENARIO:
        component = result.get("failed_component")
        if not isinstance(component, str) or not component or "tamper_case" in result:
            raise ReleaseImpactMismatch(
                "historical activation-failure evidence has an invalid variant"
            )
        variant = component
    elif scenario == HISTORICAL_TAMPER_SCENARIO:
        case = result.get("tamper_case")
        if not isinstance(case, str) or not case or "failed_component" in result:
            raise ReleaseImpactMismatch(
                "historical tamper-rejection evidence has an invalid variant"
            )
        variant = case
    elif scenario not in {HISTORICAL_SUCCESS_SCENARIO, HISTORICAL_RECOVERY_SCENARIO}:
        raise ReleaseImpactMismatch(
            f"historical published-image evidence has an unknown scenario: {scenario!r}"
        )
    expected_key = ":".join(
        part for part in (scenario, source_version, variant) if part is not None
    )
    if result.get("scenario_key") != expected_key:
        raise ReleaseImpactMismatch(
            "historical published-image evidence scenario key is invalid: "
            f"expected {expected_key!r}, got {result.get('scenario_key')!r}"
        )
    return expected_key


def _expected_historical_image_evidence_keys() -> set[str]:
    expected = {
        f"{HISTORICAL_SUCCESS_SCENARIO}:{tag.lstrip('v')}" for tag in LEGACY_BRIDGE_TAGS
    }
    expected.update(
        f"{HISTORICAL_RECOVERY_SCENARIO}:{version}"
        for version in HISTORICAL_RECOVERY_VERSIONS
    )
    expected.update(
        f"{HISTORICAL_FAILURE_SCENARIO}:{version}:{component}"
        for version, component in HISTORICAL_FAILURE_VARIANTS.items()
    )
    expected.update(
        f"{HISTORICAL_TAMPER_SCENARIO}:{version}:{case}"
        for version in HISTORICAL_TAMPER_VERSIONS
        for case in ("manifest", "bundle")
    )
    return expected


def _historical_field_mismatches(
    result: dict[str, object],
    expected: dict[str, object],
) -> dict[str, tuple[object, object]]:
    """Compare evidence without treating integers as equivalent to booleans."""

    mismatches: dict[str, tuple[object, object]] = {}
    for name, expected_value in expected.items():
        actual = result.get(name)
        if isinstance(expected_value, bool):
            matches = actual is expected_value
        elif type(expected_value) is int:
            matches = type(actual) is int and actual == expected_value
        else:
            matches = actual == expected_value
        if not matches:
            mismatches[name] = actual, expected_value
    return mismatches


def verify_historical_image_evidence(
    evidence_path: Path,
    *,
    bundle_path: Path,
) -> tuple[dict[str, object], ...]:
    """Require observed published-image activation before downgrading impact."""

    try:
        raw = json.loads(evidence_path.read_text(encoding="utf-8"))
        locks = json.loads(
            (ROOT / "scripts/release/historical-image-lock.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseImpactMismatch(
            "historical published-image evidence could not be read"
        ) from exc
    if (
        type(raw) is not dict
        or type(raw.get("schema")) is not int
        or raw.get("schema") != 2
        or raw.get("target_version") != LEGACY_BRIDGE_VERSION
        or raw.get("platform") != "linux/amd64"
        or raw.get("passed") is not True
    ):
        raise ReleaseImpactMismatch(
            "historical published-image evidence header is invalid"
        )
    lock_rows = locks.get("images") if isinstance(locks, dict) else None
    if (
        not isinstance(locks, dict)
        or type(locks.get("schema")) is not int
        or locks.get("schema") != 1
        or locks.get("repository") != "coderluii/channelwatch"
        or locks.get("platform") != "linux/amd64"
        or not isinstance(lock_rows, list)
        or any(not isinstance(item, dict) for item in lock_rows)
    ):
        raise ReleaseImpactMismatch("historical published-image lock is invalid")
    locked_images: dict[str, dict[str, object]] = {}
    for item in lock_rows:
        version = item.get("version")
        if not isinstance(version, str) or version in locked_images:
            raise ReleaseImpactMismatch(
                "historical published-image lock versions are invalid or duplicated"
            )
        locked_images[version] = item
    expected_lock_versions = {f"0.9.{patch}" for patch in range(9, 18)}
    if set(locked_images) != expected_lock_versions:
        raise ReleaseImpactMismatch(
            "historical published-image lock does not contain the exact audited matrix"
        )
    support_mismatches = {
        version: lock.get("support")
        for version, lock in locked_images.items()
        if lock.get("support")
        != (
            "image_pull_only"
            if version in HISTORICAL_RECOVERY_VERSIONS
            else "app_update"
        )
    }
    if support_mismatches:
        raise ReleaseImpactMismatch(
            "historical published-image lock support classifications are invalid: "
            f"{support_mismatches}"
        )
    results = raw.get("results")
    if not isinstance(results, list) or any(
        not isinstance(item, dict) for item in results
    ):
        raise ReleaseImpactMismatch(
            "historical published-image evidence results are invalid"
        )
    by_key: dict[str, dict[str, object]] = {}
    for item in results:
        key = _historical_image_evidence_key(item)
        if key in by_key:
            raise ReleaseImpactMismatch(
                "historical published-image evidence contains duplicate scenario "
                f"row {key}"
            )
        by_key[key] = item
    expected_keys = _expected_historical_image_evidence_keys()
    missing = expected_keys - set(by_key)
    extra = set(by_key) - expected_keys
    if missing or extra:
        raise ReleaseImpactMismatch(
            "historical published-image evidence scenario matrix is invalid: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )

    expected_bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    for tag in LEGACY_BRIDGE_TAGS:
        version = tag.lstrip("v")
        result = by_key[f"{HISTORICAL_SUCCESS_SCENARIO}:{version}"]
        lock = locked_images.get(version)
        expected = {
            "source_sha": lock.get("source_sha"),
            "image_index_digest": lock.get("index_digest"),
            "amd64_digest": lock.get("amd64_digest"),
            "launcher_protocol": lock.get("launcher_protocol"),
            "bundle_sha256": expected_bundle_sha,
            "check_status": "available",
            "final_job_status": "success",
            "core_bundle": True,
            "ui_bundle": True,
            "portal_api_verified": True,
            "supervisor_stable": True,
            "rollback_target_verified": True,
            "active_identity_verified": True,
            "restart_count_delta": 1,
            "managed_key_verified": True,
            "stale_control_file_count": 0,
            "result": "passed",
        }
        mismatches = _historical_field_mismatches(result, expected)
        if result.get("apply_status") not in {
            "restarting",
            "connection_closed_for_restart",
        }:
            mismatches["apply_status"] = (
                result.get("apply_status"),
                "restarting or connection_closed_for_restart",
            )
        restart_total = result.get("restart_count")
        if type(restart_total) is not int or restart_total < 1:
            mismatches["restart_count"] = (restart_total, "positive integer")
        if mismatches:
            raise ReleaseImpactMismatch(
                f"historical published-image evidence for {tag} is invalid: "
                f"{mismatches}"
            )

    for version, component in HISTORICAL_FAILURE_VARIANTS.items():
        result = by_key[f"{HISTORICAL_FAILURE_SCENARIO}:{version}:{component}"]
        expected = {
            "source_sha": locked_images[version].get("source_sha"),
            "image_index_digest": locked_images[version].get("index_digest"),
            "amd64_digest": locked_images[version].get("amd64_digest"),
            "launcher_protocol": locked_images[version].get("launcher_protocol"),
            "bundle_sha256": expected_bundle_sha,
            "portal_api_verified": True,
            "final_job_status": "failed",
            "rollback_applied": True,
            "failed_identity_quarantined": True,
            "image_runtime_restored": True,
            "fault_applied": True,
            "rollback_target_verified": True,
            "scheduler_attempt_verified": True,
            "supervisor_stable": True,
            "stale_control_file_count": 0,
            "restart_count_delta": 2,
            "result": "passed",
        }
        mismatches = _historical_field_mismatches(result, expected)
        if mismatches:
            raise ReleaseImpactMismatch(
                "historical activation-failure evidence for "
                f"v{version}/{component} is invalid: {mismatches}"
            )

    for version in HISTORICAL_TAMPER_VERSIONS:
        for case in ("manifest", "bundle"):
            result = by_key[f"{HISTORICAL_TAMPER_SCENARIO}:{version}:{case}"]
            expected = {
                "source_sha": locked_images[version].get("source_sha"),
                "image_index_digest": locked_images[version].get("index_digest"),
                "amd64_digest": locked_images[version].get("amd64_digest"),
                "launcher_protocol": locked_images[version].get("launcher_protocol"),
                "bundle_sha256": expected_bundle_sha,
                "tamper_applied": True,
                "fetch_transport_verified": True,
                "rejected_before_selection": True,
                "active_unchanged": True,
                "candidate_release_absent": True,
                "supervisor_stable": True,
                "stale_control_file_count": 0,
                "restart_count_delta": 0,
                "result": "passed",
            }
            mismatches = _historical_field_mismatches(result, expected)
            if mismatches:
                raise ReleaseImpactMismatch(
                    "historical tamper-rejection evidence for "
                    f"v{version}/{case} is invalid: {mismatches}"
                )

    for version in HISTORICAL_RECOVERY_VERSIONS:
        recovery = by_key[f"{HISTORICAL_RECOVERY_SCENARIO}:{version}"]
        lock = locked_images[version]
        recovery_expected = {
            "source_sha": lock.get("source_sha"),
            "image_index_digest": lock.get("index_digest"),
            "amd64_digest": lock.get("amd64_digest"),
            "launcher_protocol": lock.get("launcher_protocol"),
            "bundle_sha256": expected_bundle_sha,
            "check_status": "available",
            "image_refresh_required": True,
            "recovery_job_status": "success",
            "recovery_quorum_verified": True,
            "recovery_image_runtime_verified": True,
            "managed_key_verified": True,
            "supervisor_stable": True,
            "stale_control_file_count": 0,
            "result": "passed_with_documented_image_only_limitation",
        }
        if version == "0.9.9":
            recovery_expected.update(
                {
                    "apply_status": "restarting",
                    "immutable_false_success_observed": True,
                    "legacy_core_launcher_failure_observed": True,
                    "legacy_ui_image_runtime_verified": True,
                    "recovery_image_cleared_false_success": True,
                }
            )
        else:
            recovery_expected.update(
                {
                    "portal_api_verified": True,
                    "immutable_entrypoint_failure_observed": True,
                    "legacy_restart_loop_observed": True,
                    "legacy_restart_count_at_least": 2,
                    "legacy_staged_identity_preserved": True,
                    "recovery_image_cleared_failed_activation": True,
                }
            )
        recovery_mismatches = _historical_field_mismatches(recovery, recovery_expected)
        if version == "0.9.10" and recovery.get("apply_status") not in {
            "restarting",
            "connection_closed_for_restart",
        }:
            recovery_mismatches["apply_status"] = (
                recovery.get("apply_status"),
                "restarting or connection_closed_for_restart",
            )
        if recovery_mismatches:
            raise ReleaseImpactMismatch(
                f"v{version} image-refresh recovery evidence is incomplete: "
                f"{recovery_mismatches}"
            )
    return tuple(
        by_key[f"{HISTORICAL_SUCCESS_SCENARIO}:{tag.lstrip('v')}"]
        for tag in LEGACY_BRIDGE_TAGS
    )


def apply_verified_runtime_compatibility(
    result: ReleaseImpact,
    *,
    config: dict[str, object],
    manifest_path: Path | None,
    bundle_path: Path | None,
    historical_image_evidence: Path | None = None,
    public_keys: dict[str, str] | None = None,
    audited_change_paths: frozenset[str] | set[str] | None = None,
) -> ReleaseImpact:
    """Downgrade only exact, independently replayed v0.9.18 runtime deltas."""

    declared = _declared_runtime_compatibility_paths(config)
    affected = set(result.triggering_paths) & declared
    if not declared:
        if manifest_path is not None or bundle_path is not None:
            raise ReleaseImpactMismatch(
                "compatibility artifacts were supplied without the audited "
                "release-config declaration"
            )
        return result
    if not affected:
        return result
    audited = set(audited_change_paths or ())
    unaudited = affected - audited
    if unaudited:
        raise ReleaseImpactMismatch(
            "runtime compatibility evidence cannot override an unrecognized "
            "launcher/runtime delta: " + ", ".join(sorted(unaudited))
        )
    if (
        manifest_path is None
        or bundle_path is None
        or historical_image_evidence is None
    ):
        raise ReleaseImpactMismatch(
            "historical published-image compatibility verification is required before "
            + ", ".join(sorted(affected))
            + " can be classified as an image refresh"
        )
    verify_entrypoint_compatibility_evidence(
        manifest_path=manifest_path,
        bundle_path=bundle_path,
        public_keys=public_keys,
    )
    verify_historical_image_evidence(
        historical_image_evidence,
        bundle_path=bundle_path,
    )
    return _impact(
        set(result.triggering_paths) - affected,
        set(result.refresh_paths) | affected,
    )


def apply_verified_minimum_image_app_update(
    result: ReleaseImpact,
    *,
    config: dict[str, object],
    evidence_path: Path | None,
    bundle_path: Path | None,
) -> ReleaseImpact:
    """Allow bundle-covered launcher changes only after exact image replay."""

    affected = set(result.triggering_paths) & set(
        MINIMUM_IMAGE_APP_UPDATE_COMPATIBILITY_PATHS
    )
    if not affected:
        return result
    if evidence_path is None or bundle_path is None:
        return result
    version = config.get("version")
    minimum_image = config.get("minimum_image_version")
    delivery_mode = config.get("delivery_mode")
    if (
        not isinstance(version, str)
        or not isinstance(minimum_image, str)
        or delivery_mode not in {"app_update", "app_update_with_image_refresh"}
        or version.split(".")[-1] == "0"
    ):
        raise ReleaseImpactMismatch(
            "minimum-image app-update evidence is valid only for a non-milestone "
            "in-app release"
        )
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseImpactMismatch(
            "minimum-image app-update compatibility evidence could not be read"
        ) from exc
    if not isinstance(evidence, dict) or type(evidence.get("schema")) is not int:
        raise ReleaseImpactMismatch(
            "minimum-image app-update compatibility evidence header is invalid"
        )
    expected_header = {
        "schema": 1,
        "status": "passed",
        "source_image_version": minimum_image,
        "image_index_digest": config.get("minimum_image_sha256"),
        "platform": "linux/amd64",
        "target_application_version": version,
        "bundle_sha256": bundle_sha256,
    }
    mismatches = {
        key: (evidence.get(key), expected)
        for key, expected in expected_header.items()
        if evidence.get(key) != expected
    }
    for name in ("image_index_digest", "platform_manifest_digest", "pulled_image_id"):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(evidence.get(name, ""))) is None:
            mismatches[name] = (evidence.get(name), "immutable image digest")
    phases = (
        "dynamic_activation",
        "ui_restart",
        "rollback_reapply",
        "container_restart",
    )
    for phase in phases:
        value = evidence.get(phase)
        if (
            not isinstance(value, dict)
            or value.get("index_matches") is not True
            or type(value.get("referenced_assets_verified")) is not int
            or value["referenced_assets_verified"] < 1
        ):
            mismatches[phase] = (value, "matching index and referenced assets")
    if mismatches:
        raise ReleaseImpactMismatch(
            "minimum-image app-update compatibility evidence is invalid: "
            f"{mismatches}"
        )
    return _impact(
        set(result.triggering_paths) - affected,
        set(result.refresh_paths),
    )


def _version_tuple(value: object) -> tuple[int, int, int] | None:
    text = str(value or "").strip().lstrip("v")
    parts = text.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    parsed = tuple(int(part) for part in parts)
    if text != ".".join(str(part) for part in parsed):
        return None
    return parsed  # type: ignore[return-value]


def _declares_dependency_runtime(config: dict[str, object]) -> bool:
    return config.get("runtime_generation_required") is True and config.get(
        "delivery_mode"
    ) in {"app_update", "app_update_with_image_refresh"}


def _protocol_four_runtime_contract_valid(config: dict[str, object]) -> bool:
    floor = _version_tuple(config.get("minimum_image_version"))
    return (
        config.get("runtime_generation_required") is True
        and config.get("updater_protocol") == 4
        and config.get("minimum_launcher_protocol") == 4
        and config.get("compatible_launcher_protocols") == [4]
        and isinstance(config.get("runtime_bootstrap_sha256"), str)
        and _HEX_64_RE.fullmatch(str(config["runtime_bootstrap_sha256"])) is not None
        and config.get("minimum_image_sha256")
        == "sha256:" + str(config["runtime_bootstrap_sha256"])
        and floor is not None
        and floor >= PROTOCOL_FOUR_MINIMUM_IMAGE
        and config.get("delivery_mode") in {
            "app_update",
            "app_update_with_image_refresh",
        }
    )


def _normalized_distribution_name(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def _signed_dependency_inventory(
    qualification: dict[str, object], descriptor: dict[str, object]
) -> tuple[dict[str, str], dict[str, dict[str, object]]] | None:
    evidence = qualification.get("signed_dependency_inventory")
    if not isinstance(evidence, dict) or set(evidence) != {
        "sha256",
        "size",
        "json_base64",
    }:
        return None
    encoded = evidence["json_base64"]
    if not isinstance(encoded, str) or len(encoded) > 3 * MAX_DEPENDENCY_INVENTORY_BYTES:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
        if (
            not raw
            or len(raw) > MAX_DEPENDENCY_INVENTORY_BYTES
            or evidence["size"] != len(raw)
            or evidence["sha256"] != hashlib.sha256(raw).hexdigest()
            or evidence["sha256"] != descriptor["dependency_inventory_sha256"]
        ):
            return None
        inventory = json.loads(raw.decode("utf-8"))
        if not isinstance(inventory, dict):
            return None
        from core.runtime_generation import RuntimePlatform, _validate_inventory

        minimum_libc = descriptor["minimum_libc_version"]
        if (
            not isinstance(minimum_libc, list)
            or len(minimum_libc) != 2
            or any(type(part) is not int for part in minimum_libc)
        ):
            return None
        platform_value = RuntimePlatform(
            architecture=str(descriptor["architecture"]),
            python_cache_tag=str(descriptor["python_cache_tag"]),
            libc_family=str(descriptor["libc_family"]),
            libc_version=(minimum_libc[0], minimum_libc[1]),
            launcher_capabilities=frozenset(),
        )
        records = _validate_inventory(inventory, descriptor, platform_value)
    except Exception:
        return None
    packages: dict[str, str] = {}
    for item in inventory.get("dependencies", []):
        if not isinstance(item, dict):
            return None
        name = _normalized_distribution_name(item.get("name"))
        version = item.get("version")
        if name is None or name in packages or not isinstance(version, str) or not version:
            return None
        packages[name] = version
    if not set(TRACKED_RUNTIME_DISTRIBUTIONS.values()).issubset(packages):
        return None
    return packages, {str(item["path"]): item for item in records}


def _selected_process_valid(
    process: object,
    *,
    descriptor: dict[str, object],
    generation_root: str,
    packages: dict[str, str],
    files: dict[str, dict[str, object]],
) -> bool:
    if (
        not isinstance(process, dict)
        or type(process.get("pid")) is not int
        or process["pid"] <= 0
        or process.get("python_executable")
        != generation_root + "/environment/bin/python"
        or process.get("python_prefix") != generation_root + "/environment"
        or process.get("python_version") != descriptor["python_version"]
    ):
        return False
    dependencies = process.get("dependencies")
    if not isinstance(dependencies, dict) or set(dependencies) != set(
        TRACKED_RUNTIME_DISTRIBUTIONS
    ):
        return False
    for required in ("requests", "cryptography", "sqlalchemy"):
        record = dependencies[required]
        if not isinstance(record, dict) or record.get("loaded") is not True:
            return False
    prefix = generation_root + "/"
    for module, distribution in TRACKED_RUNTIME_DISTRIBUTIONS.items():
        record = dependencies[module]
        if not isinstance(record, dict):
            return False
        if record.get("loaded") is False:
            if set(record) != {"loaded"}:
                return False
            continue
        path = record.get("path")
        if (
            set(record) != {"loaded", "path", "version"}
            or record.get("loaded") is not True
            or record.get("version") != packages[distribution]
            or not isinstance(path, str)
            or not path.startswith(
                prefix + f"dependencies/site-packages/{module}/"
            )
            or path.removeprefix(prefix) not in files
        ):
            return False
    return True


def _selected_imports_valid(
    value: object,
    *,
    generation_root: str,
    packages: dict[str, str],
    files: dict[str, dict[str, object]],
) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "sqlalchemy",
        "httptools",
        "httptools.parser.parser",
    }:
        return False
    for module, distribution in (("sqlalchemy", "sqlalchemy"), ("httptools", "httptools")):
        record = value[module]
        relative = f"dependencies/site-packages/{module}/__init__.py"
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "version"}
            or record.get("path") != generation_root + "/" + relative
            or record.get("version") != packages[distribution]
            or relative not in files
        ):
            return False
    parser = value["httptools.parser.parser"]
    if not isinstance(parser, dict) or set(parser) != {"path", "version", "sha256"}:
        return False
    parser_path = parser.get("path")
    if (
        not isinstance(parser_path, str)
        or not parser_path.startswith(
            generation_root + "/dependencies/site-packages/httptools/parser/parser"
        )
        or not parser_path.endswith(".so")
        or parser_path == value["httptools"]["path"]
        or parser.get("version") != packages["httptools"]
    ):
        return False
    signed = files.get(parser_path.removeprefix(generation_root + "/"))
    return isinstance(signed, dict) and parser.get("sha256") == signed.get("sha256")


def _resolved_posix_link(path: str, target: str) -> str | None:
    if "\\" in target or "\x00" in target:
        return None
    candidate = PurePosixPath(target)
    parts: list[str] = []
    source = candidate if candidate.is_absolute() else PurePosixPath(path).parent / candidate
    for part in source.parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/" + "/".join(parts)


def _selected_supervisor_command_valid(
    command: object, *, component: str, generation_root: str, descriptor_sha256: str
) -> bool:
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    prefix = [
        generation_root + "/environment/bin/python",
        "-I",
        "-B",
        "-S",
        "/app/core/runtime_worker.py",
        "--image-app-dir",
        "/app",
        "--mode",
        component,
        "--generation",
        generation_root,
        "--activation-id",
    ]
    if tokens[: len(prefix)] != prefix or len(tokens) <= len(prefix):
        return False
    activation_id = tokens[len(prefix)]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", activation_id) is None:
        return False
    suffix = tokens[len(prefix) + 1 :]
    expected = ["--descriptor-sha256", descriptor_sha256]
    if component == "core":
        expected.extend(["--", "--stay-alive"])
    else:
        expected.extend(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "8501",
                "--log-level",
                "warning",
                "--",
            ]
        )
    return suffix == expected


def _image_supervisor_command_valid(command: object, *, component: str) -> bool:
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    expected = [
        "/venv/bin/python",
        "-u",
        "/app/core/runtime_launcher.py",
        component,
    ]
    if component == "core":
        expected.append("--stay-alive")
    else:
        expected.extend(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "8501",
                "--log-level",
                "warning",
            ]
        )
    return tokens == expected


def _verified_generation_valid(
    value: object, *, descriptor: dict[str, object], generation_root: str
) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "path",
            "runtime_generation_id",
            "runtime_descriptor_sha256",
            "dependency_inventory_sha256",
        }
        and value.get("path") == generation_root
        and value.get("runtime_generation_id") == descriptor["generation_id"]
        and value.get("runtime_descriptor_sha256") == descriptor["generation_id"]
        and value.get("dependency_inventory_sha256")
        == descriptor["dependency_inventory_sha256"]
    )


def _ready_health_valid(value: object) -> bool:
    return value == {
        "startup": {"status": "ready"},
        "ready": {"status": "ready", "ready": True},
    }


def _image_operational_state_valid(
    value: object,
    *,
    descriptor: dict[str, object],
    image_records: dict[str, dict[str, object]],
    expected_worker: object,
) -> bool:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "status_worker_scope",
            "status_worker",
            "commands",
            "processes",
            "services",
            "health",
        }
        or value.get("status_worker_scope")
        != "frozen_image_ui_status_interpreter_only"
        or value.get("status_worker") != expected_worker
        or not _image_status_worker_valid(value.get("status_worker"), descriptor)
        or not _ready_health_valid(value.get("health"))
    ):
        return False
    commands = value.get("commands")
    processes = value.get("processes")
    services = value.get("services")
    if any(
        not isinstance(records, dict) or set(records) != {"core", "ui"}
        for records in (commands, processes, services)
    ):
        return False
    pids: set[int] = set()
    for component in ("core", "ui"):
        process = processes[component]
        service = services[component]
        if (
            not _image_supervisor_command_valid(
                commands[component], component=component
            )
            or not isinstance(process, dict)
            or set(process) != {"pid", "command"}
            or type(process.get("pid")) is not int
            or process["pid"] <= 0
            or process.get("command") != commands[component]
            or not isinstance(service, dict)
            or set(service) != {"status", "pid", "uptime"}
            or service.get("status") != "RUNNING"
            or service.get("pid") != process["pid"]
            or not isinstance(service.get("uptime"), str)
            or not service["uptime"].strip()
        ):
            return False
        pids.add(process["pid"])
    return len(pids) == 2


def _selected_operational_state_valid(
    value: object,
    *,
    descriptor: dict[str, object],
    generation_root: str,
    packages: dict[str, str],
    files: dict[str, dict[str, object]],
    expected_components: object,
    expected_commands: object,
) -> bool:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "runtime_components",
            "status_worker_scope",
            "status_worker",
            "commands",
            "processes",
            "launchers",
            "services",
            "health",
        }
        or value.get("runtime_components") != expected_components
        or value.get("status_worker_scope") != "ui_status_handler_only"
        or value.get("commands") != expected_commands
        or not _ready_health_valid(value.get("health"))
        or not _selected_process_valid(
            value.get("status_worker"),
            descriptor=descriptor,
            generation_root=generation_root,
            packages=packages,
            files=files,
        )
        or not _verified_generation_valid(
            value["status_worker"].get("verified_generation"),
            descriptor=descriptor,
            generation_root=generation_root,
        )
    ):
        return False
    commands = value["commands"]
    processes = value.get("processes")
    launchers = value.get("launchers")
    services = value.get("services")
    if any(
        not isinstance(records, dict) or set(records) != {"core", "ui"}
        for records in (commands, processes, launchers, services)
    ):
        return False
    worker_pids: set[int] = set()
    launcher_pids: set[int] = set()
    for component in ("core", "ui"):
        process = processes[component]
        launcher = launchers[component]
        service = services[component]
        if (
            not _selected_supervisor_command_valid(
                commands[component],
                component=component,
                generation_root=generation_root,
                descriptor_sha256=str(descriptor["generation_id"]),
            )
            or not isinstance(process, dict)
            or set(process) != {"pid", "ppid", "command"}
            or type(process.get("pid")) is not int
            or process["pid"] <= 0
            or type(process.get("ppid")) is not int
            or process["ppid"] <= 0
            or process.get("command") != commands[component]
            or not isinstance(launcher, dict)
            or set(launcher) != {"pid", "command"}
            or type(launcher.get("pid")) is not int
            or launcher["pid"] <= 0
            or not _image_supervisor_command_valid(
                launcher.get("command"), component=component
            )
            or process["ppid"] != launcher["pid"]
            or not isinstance(service, dict)
            or set(service) != {"status", "pid", "uptime"}
            or service.get("status") != "RUNNING"
            or service.get("pid") != launcher["pid"]
            or not isinstance(service.get("uptime"), str)
            or not service["uptime"].strip()
        ):
            return False
        worker_pids.add(process["pid"])
        launcher_pids.add(launcher["pid"])
        if expected_components is not None:
            component_runtime = expected_components[component].get("process_runtime")
            if (
                component_runtime.get("pid") != process["pid"]
                or not _verified_generation_valid(
                    component_runtime.get("verified_generation"),
                    descriptor=descriptor,
                    generation_root=generation_root,
                )
            ):
                return False
    return (
        len(worker_pids) == 2
        and len(launcher_pids) == 2
        and worker_pids.isdisjoint(launcher_pids)
        and value["status_worker"]["pid"] == processes["ui"]["pid"]
        and (
            expected_components is None
            or value["status_worker"]
            == expected_components["ui"]["process_runtime"]
        )
    )


def _image_inventory_valid(value: object) -> dict[str, dict[str, object]] | None:
    if not isinstance(value, dict) or set(value) != {
        "sha256",
        "path_count",
        "regular_file_bytes",
        "entries",
    }:
        return None
    entries = value["entries"]
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > MAX_IMAGE_INVENTORY_PATHS
        or value["path_count"] != len(entries)
    ):
        return None
    paths: list[str] = []
    regular_bytes = 0
    records: dict[str, dict[str, object]] = {}
    allowed_links = {
        f"/venv/bin/{name}": f"/usr/local/bin/{name}"
        for name in ("python", "python3", "python3.14")
    }
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        path = entry.get("path")
        kind = entry.get("type")
        mode = entry.get("mode")
        size = entry.get("size")
        if (
            not isinstance(path, str)
            or not PurePosixPath(path).is_absolute()
            or str(PurePosixPath(path)) != path
            or ".." in PurePosixPath(path).parts
            or "\\" in path
            or not (
                path == "/venv"
                or path.startswith("/venv/")
                or path in IMAGE_BOOTSTRAP_PATHS
            )
            or path in records
            or not isinstance(mode, str)
            or re.fullmatch(r"0[0-7]{3}", mode) is None
            or type(size) is not int
            or size < 0
        ):
            return None
        expected_fields = {"path", "type", "mode", "size"}
        if kind == "file":
            expected_fields.add("sha256")
            if not isinstance(entry.get("sha256"), str) or not _HEX_64_RE.fullmatch(
                str(entry["sha256"])
            ):
                return None
            regular_bytes += size
        elif kind == "symlink":
            expected_fields.add("link_target")
            target = entry.get("link_target")
            if not isinstance(target, str):
                return None
            resolved = _resolved_posix_link(path, target)
            if resolved is None:
                return None
            if path.startswith("/venv/") and not (
                resolved == "/venv"
                or resolved.startswith("/venv/")
                or allowed_links.get(path) == resolved
            ):
                return None
        elif kind != "directory":
            return None
        if set(entry) != expected_fields:
            return None
        paths.append(path)
        records[path] = entry
    if (
        paths != sorted(paths)
        or regular_bytes > MAX_IMAGE_INVENTORY_BYTES
        or value["regular_file_bytes"] != regular_bytes
        or not IMAGE_BOOTSTRAP_PATHS.issubset(records)
        or any(records[path].get("type") != "file" for path in IMAGE_BOOTSTRAP_PATHS)
    ):
        return None
    encoded = json.dumps(
        entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if value.get("sha256") != hashlib.sha256(encoded).hexdigest():
        return None
    return records


def _image_imports_valid(
    value: object, image_records: dict[str, dict[str, object]]
) -> bool:
    expected_versions = {"sqlalchemy": "2.0.54", "httptools": "0.8.0"}
    if not isinstance(value, dict) or set(value) != {
        "sqlalchemy",
        "httptools",
        "httptools.parser.parser",
    }:
        return False
    for module, version in expected_versions.items():
        record = value[module]
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "version"}
            or record.get("version") != version
            or not str(record.get("path") or "").startswith(
                f"/venv/lib/python3.14/site-packages/{module}/"
            )
            or image_records.get(str(record["path"]), {}).get("type") != "file"
        ):
            return False
    parser = value["httptools.parser.parser"]
    parser_path = parser.get("path") if isinstance(parser, dict) else None
    parser_file = image_records.get(str(parser_path), {})
    return (
        isinstance(parser, dict)
        and set(parser) == {"path", "version", "sha256"}
        and parser.get("version") == "0.8.0"
        and isinstance(parser_path, str)
        and parser_path.startswith(
            "/venv/lib/python3.14/site-packages/httptools/parser/parser"
        )
        and parser_path.endswith(".so")
        and parser_path != value["httptools"]["path"]
        and parser_file.get("type") == "file"
        and parser.get("sha256") == parser_file.get("sha256")
    )


def _image_status_worker_valid(
    value: object, descriptor: dict[str, object]
) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {"python_executable", "python_version", "verified_generation"}
        and value.get("python_executable") == "/venv/bin/python"
        and value.get("python_version") == descriptor["python_version"]
        and value.get("verified_generation") is None
    )


def _image_process_diagnostics_valid(
    value: object,
    *,
    descriptor: dict[str, object],
    image_records: dict[str, dict[str, object]],
) -> bool:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "pid",
            "python_executable",
            "python_prefix",
            "python_version",
            "dependencies",
        }
        or type(value.get("pid")) is not int
        or value["pid"] <= 0
        or value.get("python_executable") != "/venv/bin/python"
        or value.get("python_prefix") != "/venv"
        or value.get("python_version") != descriptor["python_version"]
    ):
        return False
    dependencies = value.get("dependencies")
    frozen_modules = {
        "requests", "cryptography", "sqlmodel", "uvicorn", "pydantic_core"
    }
    if not isinstance(dependencies, dict) or set(dependencies) != frozen_modules:
        return False
    for module, record in dependencies.items():
        if not isinstance(record, dict):
            return False
        if record.get("loaded") is False:
            if set(record) != {"loaded"}:
                return False
            continue
        path = record.get("path")
        if (
            set(record) != {"loaded", "path", "version"}
            or record.get("loaded") is not True
            or not isinstance(path, str)
            or not path.startswith(
                f"/venv/lib/python3.14/site-packages/{module}/"
            )
            or image_records.get(path, {}).get("type") != "file"
            or not isinstance(record.get("version"), str)
        ):
            return False
    return True


def _runtime_qualification_valid(
    qualification: object,
    descriptor: dict[str, object],
    config: dict[str, object],
    bundle_sha256: str,
) -> bool:
    """Require deterministic native identity and lifecycle receipts."""
    if not isinstance(qualification, dict):
        return False
    for field in ("platform_manifest_digest", "pulled_image_id"):
        value = qualification.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            return False
    generation = str(descriptor["generation_id"])
    architecture = descriptor["architecture"]
    encoded_index = qualification.get("bootstrap_index_base64")
    if not isinstance(encoded_index, str) or len(encoded_index) > 2 * 1024 * 1024:
        return False
    try:
        index_bytes = base64.b64decode(encoded_index, validate=True)
        if "sha256:" + hashlib.sha256(index_bytes).hexdigest() != config.get(
            "minimum_image_sha256"
        ):
            return False
        manifests = json.loads(index_bytes)["manifests"]
        if not isinstance(manifests, list) or any(
            not isinstance(item, dict) for item in manifests
        ):
            return False
        matches = [
            item
            for item in manifests
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == architecture
        ]
        if (
            len(matches) != 1
            or matches[0].get("digest") != qualification["platform_manifest_digest"]
        ):
            return False
    except (ValueError, TypeError, KeyError):
        return False
    if (
        qualification.get("native_host_architecture") != architecture
        or qualification.get("docker_engine_architecture") != architecture
    ):
        return False
    identity = qualification.get("container_identity_before")
    if (
        not isinstance(identity, str)
        or not _HEX_64_RE.fullmatch(identity)
        or qualification.get("container_identity_after") != identity
    ):
        return False
    signed = _signed_dependency_inventory(qualification, descriptor)
    if signed is None:
        return False
    packages, files = signed
    generation_root = f"/config/channelwatch-runtime/releases/{generation}"
    for field in ("apply_job_id", "reapply_job_id"):
        if not isinstance(qualification.get(field), str) or not re.fullmatch(
            r"[0-9a-f]{32}", str(qualification[field])
        ):
            return False
    if qualification["apply_job_id"] == qualification["reapply_job_id"]:
        return False
    for field in ("apply_runtime_components", "reapply_runtime_components"):
        components = qualification.get(field)
        if not isinstance(components, dict) or set(components) != {"core", "ui"}:
            return False
        for component in components.values():
            if not isinstance(component, dict) or any(
                component.get(key) != expected
                for key, expected in {
                    "runtime_generation_id": generation,
                    "runtime_descriptor_sha256": generation,
                    "dependency_inventory_sha256": descriptor[
                        "dependency_inventory_sha256"
                    ],
                }.items()
            ):
                return False
            if not _selected_process_valid(
                component.get("process_runtime"),
                descriptor=descriptor,
                generation_root=generation_root,
                packages=packages,
                files=files,
            ):
                return False
    selected_imports = qualification.get("selected_native_imports")
    if not _selected_imports_valid(
        selected_imports,
        generation_root=generation_root,
        packages=packages,
        files=files,
    ) or any(
        qualification.get(field) != selected_imports
        for field in (
            "selected_native_imports_after_restart",
            "selected_native_imports_after_reapply",
        )
    ):
        return False
    for field in (
        "supervisor_processes",
        "supervisor_processes_after_restart",
        "supervisor_processes_after_reapply",
    ):
        commands = qualification.get(field)
        if not isinstance(commands, dict) or set(commands) != {"core", "ui"}:
            return False
        if any(
            not _selected_supervisor_command_valid(
                command,
                component=name,
                generation_root=generation_root,
                descriptor_sha256=generation,
            )
            for name, command in commands.items()
        ):
            return False
    image_records = _image_inventory_valid(qualification.get("image_inventory_before"))
    if image_records is None:
        return False
    initial = qualification.get("initial_status")
    if (
        not isinstance(initial, dict)
        or not _image_operational_state_valid(
            qualification.get("initial_operational_state"),
            descriptor=descriptor,
            image_records=image_records,
            expected_worker=initial.get("worker_runtime"),
        )
        or not _selected_operational_state_valid(
            qualification.get("apply_operational_state"),
            descriptor=descriptor,
            generation_root=generation_root,
            packages=packages,
            files=files,
            expected_components=qualification.get("apply_runtime_components"),
            expected_commands=qualification.get("supervisor_processes"),
        )
        or not _selected_operational_state_valid(
            qualification.get("restart_operational_state"),
            descriptor=descriptor,
            generation_root=generation_root,
            packages=packages,
            files=files,
            expected_components=None,
            expected_commands=qualification.get("supervisor_processes_after_restart"),
        )
        or not _selected_operational_state_valid(
            qualification.get("reapply_operational_state"),
            descriptor=descriptor,
            generation_root=generation_root,
            packages=packages,
            files=files,
            expected_components=qualification.get("reapply_runtime_components"),
            expected_commands=qualification.get("supervisor_processes_after_reapply"),
        )
    ):
        return False
    inventory_summary = {
        key: qualification["image_inventory_before"][key]
        for key in ("sha256", "path_count", "regular_file_bytes")
    }
    if any(
        qualification.get("image_inventory_" + stage) != inventory_summary
        for stage in ("after_apply", "after_restart", "after_rollback", "after_reapply")
    ):
        return False
    image_imports = qualification.get("image_imports_before")
    if not _image_imports_valid(image_imports, image_records) or qualification.get(
        "image_imports_after_rollback"
    ) != image_imports:
        return False
    preserved = qualification.get("persistent_hashes_before")
    if (
        not isinstance(preserved, dict)
        or set(preserved) != {"/config/settings.json", "/config/encryption.key"}
        or any(
            not isinstance(value, str) or not _HEX_64_RE.fullmatch(value)
            for value in preserved.values()
        )
        or any(
            qualification.get("persistent_hashes_" + stage) != preserved
            for stage in ("after_apply", "after_restart", "after_rollback", "after_reapply")
        )
    ):
        return False
    checked = qualification.get("check")
    target = checked.get("trusted_target") if isinstance(checked, dict) else None
    if (
        not isinstance(checked, dict)
        or checked.get("update_available") is not True
        or checked.get("image_required") is not False
        or checked.get("catalog_state") != "update_available"
        or not isinstance(target, dict)
        or target.get("version") != config["version"]
        or target.get("runtime_descriptor") != descriptor
        or target.get("bundle_sha256") != bundle_sha256
    ):
        return False
    rollback = qualification.get("rollback")
    rollback_job = rollback.get("job_id") if isinstance(rollback, dict) else None
    if (
        not isinstance(rollback, dict)
        or rollback.get("operation") != "rollback"
        or rollback.get("status") not in {"restarting", "succeeded"}
        or rollback.get("rolled_back_from") != config["version"]
        or not isinstance(rollback_job, str)
        or not re.fullmatch(r"[0-9a-f]{32}", rollback_job)
        or rollback_job
        in {qualification["apply_job_id"], qualification["reapply_job_id"]}
    ):
        return False
    rollback_status = qualification.get("rollback_status")
    rollback_worker = (
        rollback_status.get("worker_runtime")
        if isinstance(rollback_status, dict)
        else None
    )
    rollback_components = qualification.get("rollback_runtime_components")
    if (
        not isinstance(rollback_status, dict)
        or rollback_status.get("current_version") != config["minimum_image_version"]
        or rollback_status.get("launcher_protocol") != 4
        or rollback_status.get("active_bundle") is not None
        or rollback_status.get("operation_busy") is not False
        or not _image_status_worker_valid(rollback_worker, descriptor)
        or not isinstance(rollback_components, dict)
        or rollback_components != {"ui": rollback_worker}
    ):
        return False
    rollback_commands = qualification.get("rollback_supervisor_processes")
    if (
        not isinstance(rollback_commands, dict)
        or set(rollback_commands) != {"core", "ui"}
        or any(
            not _image_supervisor_command_valid(command, component=name)
            for name, command in rollback_commands.items()
        )
    ):
        return False
    process_identities = qualification.get("rollback_supervisor_process_identities")
    if (
        not isinstance(process_identities, dict)
        or set(process_identities) != {"core", "ui"}
    ):
        return False
    process_ids: set[int] = set()
    for component, identity_record in process_identities.items():
        if (
            not isinstance(identity_record, dict)
            or set(identity_record) != {"pid", "command"}
            or type(identity_record.get("pid")) is not int
            or identity_record["pid"] <= 0
            or identity_record.get("command") != rollback_commands[component]
        ):
            return False
        process_ids.add(identity_record["pid"])
    if len(process_ids) != 2:
        return False
    supervisor_services = qualification.get("rollback_supervisor_services")
    if (
        not isinstance(supervisor_services, dict)
        or set(supervisor_services) != {"core", "ui"}
    ):
        return False
    for component, service in supervisor_services.items():
        if (
            not isinstance(service, dict)
            or set(service) != {"status", "pid", "uptime"}
            or service.get("status") != "RUNNING"
            or service.get("pid") != process_identities[component]["pid"]
            or not isinstance(service.get("uptime"), str)
            or not service["uptime"].strip()
        ):
            return False
    rollback_health = qualification.get("rollback_health")
    if (
        not isinstance(rollback_health, dict)
        or set(rollback_health) != {"startup", "ready"}
        or any(
            not isinstance(rollback_health[field], dict)
            or rollback_health[field].get("status") != "ready"
            for field in ("startup", "ready")
        )
        or rollback_health["ready"].get("ready") is not True
        or qualification.get("rollback_runtime_component_scope")
        != "frozen_image_ui_status_interpreter_only"
    ):
        return False
    runtime_info = qualification.get("rollback_runtime_info")
    if (
        not isinstance(runtime_info, dict)
        or runtime_info.get("diagnostic_source") != "image"
        or runtime_info.get("application_dir") != "/app"
        or runtime_info.get("application_version") != config["minimum_image_version"]
        or runtime_info.get("verified_generation") is not None
    ):
        return False
    info_process = runtime_info.get("process_runtime")
    if not isinstance(info_process, dict):
        return False
    if not _image_process_diagnostics_valid(
        info_process,
        descriptor=descriptor,
        image_records=image_records,
    ):
        return False
    restart = qualification.get("container_restart")
    return (
        isinstance(initial, dict)
        and initial.get("launcher_protocol") == 4
        and initial.get("current_version") == config["minimum_image_version"]
        and isinstance(restart, dict)
        and restart.get("status") == "passed"
        and restart.get("current_version") == config["version"]
    )


def _read_protocol_four_runtime_evidence(
    *,
    config: dict[str, object],
    catalog_path: Path,
    evidence_path: Path,
    expected_source_commit: str,
    public_keys: dict[str, str] | None,
) -> bool:
    """Verify signed descriptors plus exact two-architecture native evidence."""

    if not _protocol_four_runtime_contract_valid(config) or not _HEX_40_RE.fullmatch(
        expected_source_commit
    ):
        return False
    try:
        if not 0 < catalog_path.stat().st_size <= 4 * 1024 * 1024:
            return False
        if not 0 < evidence_path.stat().st_size <= 8 * 1024 * 1024:
            return False
        catalog_bytes = catalog_path.read_bytes()
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        catalog = json.loads(catalog_bytes.decode("utf-8"))
        from core.runtime_generation import (
            descriptor_generation_id,
            validate_runtime_descriptor_shape,
        )
        from core.update_center import (
            UPDATE_PUBLIC_KEYS,
            canonical_payload_bytes,
            verify_ed25519_signature,
        )

        if not isinstance(catalog, dict) or catalog.get("schema") != 3:
            return False
        payload = catalog.get("payload")
        signature = catalog.get("signature")
        if not isinstance(payload, dict) or not isinstance(signature, dict):
            return False
        if signature.get("alg") != "ed25519":
            return False
        verify_ed25519_signature(
            public_keys if public_keys is not None else UPDATE_PUBLIC_KEYS,
            str(signature.get("key_id") or ""),
            str(signature.get("value") or ""),
            canonical_payload_bytes(payload),
        )
        releases = payload.get("releases")
        if not isinstance(releases, list):
            return False
        matching = [
            release
            for release in releases
            if isinstance(release, dict)
            and release.get("version") == config.get("version")
        ]
        if len(matching) != 1:
            return False
        release = matching[0]
        if (
            release.get("minimum_image_version")
            != config.get("minimum_image_version")
            or release.get("compatible_launcher_protocols") != [4]
        ):
            return False
        variants = release.get("runtime_variants")
        if not isinstance(variants, list) or len(variants) != 2:
            return False
        descriptors: dict[str, dict[str, object]] = {}
        bundle_hashes: dict[str, str] = {}
        for variant in variants:
            if not isinstance(variant, dict):
                return False
            descriptor = variant.get("runtime_descriptor")
            if not isinstance(descriptor, dict):
                return False
            validate_runtime_descriptor_shape(descriptor)
            architecture = descriptor.get("architecture")
            if architecture not in PROTOCOL_FOUR_ARCHITECTURES:
                return False
            if architecture in descriptors:
                return False
            if (
                descriptor.get("application_version") != config.get("version")
                or descriptor.get("source_commit") != expected_source_commit
                or descriptor.get("generation_id")
                != descriptor_generation_id(descriptor)
            ):
                return False
            descriptors[str(architecture)] = descriptor
            bundle_hash = variant.get("bundle_sha256")
            if not isinstance(bundle_hash, str) or not _HEX_64_RE.fullmatch(bundle_hash):
                return False
            bundle_hashes[str(architecture)] = bundle_hash
        if set(descriptors) != PROTOCOL_FOUR_ARCHITECTURES:
            return False

        if not isinstance(evidence, dict):
            return False
        bootstrap = evidence.get("bootstrap")
        if not isinstance(bootstrap, dict):
            return False
        expected_architectures = {}
        for architecture, descriptor in descriptors.items():
            descriptor_sha256 = hashlib.sha256(
                canonical_payload_bytes(descriptor)
            ).hexdigest()
            expected_architectures[architecture] = {
                "status": "passed",
                "architecture": architecture,
                "source_commit": expected_source_commit,
                "descriptor_sha256": descriptor_sha256,
                "runtime_generation_id": descriptor["generation_id"],
                "dependency_inventory_sha256": descriptor[
                    "dependency_inventory_sha256"
                ],
                "target_variant_bundle_sha256": bundle_hashes[architecture],
                "python_implementation": descriptor["python_implementation"],
                "python_version": descriptor["python_version"],
                "python_cache_tag": descriptor["python_cache_tag"],
                "os_family": descriptor["os_family"],
                "libc_family": descriptor["libc_family"],
                "minimum_libc_version": descriptor["minimum_libc_version"],
                "interpreter_smoke": True,
                "native_imports": True,
                "system_libraries": True,
                "persistent_restart": True,
                "rollback": True,
            }
        expected_evidence = {
            "schema": 2,
            "status": "passed",
            "target_application_version": config.get("version"),
            "source_commit": expected_source_commit,
            "signed_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "bootstrap": {
                "status": "passed",
                "minimum_image_version": config.get("minimum_image_version"),
                "launcher_protocol": 4,
                "immutable_bootstrap_sha256": config.get(
                    "runtime_bootstrap_sha256"
                ),
                "image_index_digest": config.get("minimum_image_sha256"),
            },
        }
        if any(evidence.get(key) != value for key, value in expected_evidence.items()):
            return False
        architectures = evidence.get("architectures")
        if not isinstance(architectures, dict) or set(architectures) != PROTOCOL_FOUR_ARCHITECTURES:
            return False
        logs = evidence.get("native_logs_sha256")
        if not isinstance(logs, dict) or set(logs) != PROTOCOL_FOUR_ARCHITECTURES:
            return False
        for architecture, expected in expected_architectures.items():
            actual = architectures[architecture]
            if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in expected.items()):
                return False
            if not _runtime_qualification_valid(actual.get("qualification"), descriptors[architecture], config, expected["target_variant_bundle_sha256"]):
                return False
            hashes = logs[architecture]
            if not isinstance(hashes, dict) or set(hashes) != {"app", "feed"} or any(not isinstance(value, str) or not _HEX_64_RE.fullmatch(value) for value in hashes.values()):
                return False
        return True
    except Exception:
        return False


def apply_verified_dependency_runtime(
    result: ReleaseImpact,
    *,
    config: dict[str, object],
    catalog_path: Path | None,
    evidence_path: Path | None,
    expected_source_commit: str,
    public_keys: dict[str, str] | None = None,
) -> ReleaseImpact:
    """Require signed protocol-4 runtime proof for every in-app selection."""

    if not _declares_dependency_runtime(config):
        return result
    valid = (
        catalog_path is not None
        and evidence_path is not None
        and _read_protocol_four_runtime_evidence(
            config=config,
            catalog_path=catalog_path,
            evidence_path=evidence_path,
            expected_source_commit=expected_source_commit,
            public_keys=public_keys,
        )
    )
    if not valid:
        return _impact(
            set(result.triggering_paths) | {RUNTIME_CAPABILITY_EVIDENCE_TRIGGER},
            set(result.refresh_paths),
        )
    remaining = {
        path
        for path in result.triggering_paths
        if not any(path.startswith(prefix) for prefix in RUNTIME_PREFIXES)
    }
    return _impact(remaining, set(result.refresh_paths))


def _normalized_package_runtime(content: str | None) -> object:
    if content is None:
        return None
    parsed = json.loads(content)
    return {
        key: parsed.get(key)
        for key in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "engines",
            "packageManager",
        )
    }


def _without_release_version_lines(
    content: str | None, *, values: bool = False
) -> str | None:
    if content is None:
        return None
    ignored = ("version:", "appVersion:") if not values else ("tag:",)
    return "\n".join(
        line for line in content.splitlines() if not line.strip().startswith(ignored)
    )


def _without_docker_release_metadata(content: str | None) -> str | None:
    if content is None:
        return None
    normalized: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("ARG VERSION="):
            normalized.append("ARG VERSION=<release-version>")
        else:
            normalized.append(line)
    return "\n".join(normalized)


def _docker_release_and_launcher_metadata(
    content: str | None,
) -> tuple[str, str, int | None] | None:
    """Return normalized Dockerfile plus its single version/protocol defaults."""

    if content is None:
        return None
    normalized: list[str] = []
    versions: list[str] = []
    protocols: list[int] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("ARG VERSION="):
            versions.append(stripped.removeprefix("ARG VERSION=").strip("\"'"))
            normalized.append("ARG VERSION=<release-version>")
        elif stripped.startswith("ENV CHANNELWATCH_LAUNCHER_PROTOCOL="):
            raw = stripped.removeprefix("ENV CHANNELWATCH_LAUNCHER_PROTOCOL=").strip(
                "\"'"
            )
            try:
                protocols.append(int(raw))
            except ValueError:
                return None
            # v0.9.17 inferred protocol 2 because this default was absent;
            # v0.9.18 is the one audited transition that adds protocol 3.
            continue
        elif stripped in {
            "ARG GIT_SHA=unknown",
            'ENV CHANNELWATCH_BUILD_ID="${GIT_SHA}"',
            "ARG SOURCE_DATE_EPOCH=0",
            'ENV SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH}"',
        }:
            # Reproducible-build metadata added by the same reviewed candidate
            # does not alter the image launcher ABI.
            continue
        elif (
            stripped == "# nosemgrep"
            or stripped.startswith("# nosemgrep:")
            or stripped.startswith("# trivy:ignore:")
            or stripped.startswith("# hadolint ignore=")
        ):
            # Scanner suppression comments never affect the built image. They
            # may be narrowed or reordered as the security rules evolve, so
            # exclude only these recognized comment forms from the audited
            # launcher-protocol transition comparison.
            continue
        else:
            normalized.append(line)
    if len(versions) != 1 or len(protocols) > 1:
        return None
    return (
        "\n".join(line for line in normalized if line.strip()),
        versions[0],
        protocols[0] if protocols else None,
    )


def audited_runtime_change_paths(
    before: dict[str, str | None], after: dict[str, str | None]
) -> frozenset[str]:
    """Identify only the exact runtime changes covered by the v0.9.18 replay."""

    audited = {
        path
        for path in (
            ENTRYPOINT_COMPATIBILITY_PATH,
            RUNTIME_LAUNCHER_COMPATIBILITY_PATH,
        )
        if before.get(path) != after.get(path)
    }
    docker_before = _docker_release_and_launcher_metadata(
        before.get(LAUNCHER_PROTOCOL_COMPATIBILITY_PATH)
    )
    docker_after = _docker_release_and_launcher_metadata(
        after.get(LAUNCHER_PROTOCOL_COMPATIBILITY_PATH)
    )
    if (
        docker_before is not None
        and docker_after is not None
        and docker_before[0] == docker_after[0]
        and docker_before[1] == "0.9.17"
        and docker_before[2] in {None, 2}
        and docker_after[1:] == ("0.9.18", 3)
    ):
        audited.add(LAUNCHER_PROTOCOL_COMPATIBILITY_PATH)
    return frozenset(audited)


def classify_changes(
    before: dict[str, str | None], after: dict[str, str | None]
) -> ReleaseImpact:
    paths = sorted(set(before) | set(after))
    initial = classify_paths(paths)
    triggering = set(initial.triggering_paths)
    refresh = set(initial.refresh_paths)
    for path in paths:
        normalized = normalize_path(path)
        if normalized == ENTRYPOINT_COMPATIBILITY_PATH:
            triggering.discard(normalized)
            if not _only_fresh_notification_defaults_changed(
                before.get(path), after.get(path)
            ):
                triggering.add(normalized)
        elif normalized == "app/ui/package.json":
            refresh.discard(normalized)
            if _normalized_package_runtime(
                before.get(path)
            ) != _normalized_package_runtime(after.get(path)):
                refresh.add(normalized)
        elif normalized == "deploy/helm/channelwatch/Chart.yaml":
            refresh.discard(normalized)
            if _without_release_version_lines(
                before.get(path)
            ) != _without_release_version_lines(after.get(path)):
                refresh.add(normalized)
        elif normalized == "deploy/helm/channelwatch/values.yaml":
            refresh.discard(normalized)
            if _without_release_version_lines(
                before.get(path), values=True
            ) != _without_release_version_lines(after.get(path), values=True):
                refresh.add(normalized)
        elif normalized == "deploy/docker/Dockerfile":
            refresh.discard(normalized)
            if _without_docker_release_metadata(
                before.get(path)
            ) != _without_docker_release_metadata(after.get(path)):
                triggering.add(normalized)
            # A release-number-only Dockerfile change does not alter the
            # runtime and must not turn a complete app update into an image
            # refresh recommendation.
    return _impact(triggering, refresh)


def verify_declared_impact(
    result: ReleaseImpact,
    *,
    declared_image_required: bool,
    declared_delivery_mode: str | None = None,
) -> None:
    if result.image_required == declared_image_required and (
        declared_delivery_mode is None or result.delivery_mode == declared_delivery_mode
    ):
        return
    declared = str(declared_image_required).lower()
    required = str(result.image_required).lower()
    details = ", ".join(result.triggering_paths) or "no image-runtime paths changed"
    raise ReleaseImpactMismatch(
        f"release-config declares image_required={declared}"
        + (
            f" and delivery_mode={declared_delivery_mode}"
            if declared_delivery_mode is not None
            else ""
        )
        + f", but classifier requires {required} and delivery_mode={result.delivery_mode}: {details}"
    )


def changed_paths(base_ref: str, target_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}..{target_ref}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def file_at_ref(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--config", default="scripts/release/release-config.json")
    parser.add_argument("--compatibility-manifest", type=Path)
    parser.add_argument("--compatibility-bundle", type=Path)
    parser.add_argument("--historical-image-evidence", type=Path)
    parser.add_argument("--minimum-image-app-update-evidence", type=Path)
    parser.add_argument("--minimum-image-app-update-bundle", type=Path)
    parser.add_argument("--runtime-catalog", type=Path)
    parser.add_argument("--runtime-capability-evidence", type=Path)
    parser.add_argument(
        "--public-key",
        action="append",
        help=(
            "Test-only key-id=base64 Ed25519 public key override; omit for "
            "official release verification."
        ),
    )
    return parser.parse_args(argv)


def parse_public_key_overrides(
    values: list[str] | None,
) -> dict[str, str] | None:
    if not values:
        return None
    parsed: dict[str, str] = {}
    for item in values:
        key_id, separator, value = item.partition("=")
        key_id = key_id.strip()
        value = value.strip()
        if not separator or not key_id or not value:
            raise ValueError("--public-key must use nonblank key-id=base64 format.")
        if key_id in parsed:
            raise ValueError(f"--public-key contains duplicate key ID {key_id}.")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("--public-key must contain strict Base64.") from exc
        if len(decoded) != 32:
            raise ValueError("--public-key must decode to a 32-byte Ed25519 key.")
        parsed[key_id] = value
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    public_keys = parse_public_key_overrides(args.public_key)
    config = json.loads(open(args.config, encoding="utf-8").read())
    version_policy = validate_release_config(config)
    declared = config.get("image_required")
    if not isinstance(declared, bool):
        raise ReleaseImpactMismatch("release-config image_required must be a boolean")
    paths = changed_paths(args.base_ref, args.target_ref)
    structured = [
        path
        for path in paths
        if normalize_path(path)
        in (STRUCTURED_RELEASE_PATHS | AUDITED_RUNTIME_COMPATIBILITY_PATHS)
    ]
    before = {path: file_at_ref(args.base_ref, path) for path in structured}
    after = {path: file_at_ref(args.target_ref, path) for path in structured}
    declared_delivery_mode = config.get("delivery_mode")
    if declared_delivery_mode is not None and declared_delivery_mode not in {
        "app_update",
        "app_update_with_image_refresh",
        "image_required",
    }:
        raise ReleaseImpactMismatch("release-config delivery_mode is invalid")
    result = classify_changes(before, after)
    ordinary = classify_paths([path for path in paths if path not in structured])
    result = _impact(
        set(result.triggering_paths) | set(ordinary.triggering_paths),
        set(result.refresh_paths) | set(ordinary.refresh_paths),
    )
    result = apply_verified_runtime_compatibility(
        result,
        config=config,
        manifest_path=args.compatibility_manifest,
        bundle_path=args.compatibility_bundle,
        historical_image_evidence=args.historical_image_evidence,
        public_keys=public_keys,
        audited_change_paths=audited_runtime_change_paths(before, after),
    )
    result = apply_verified_minimum_image_app_update(
        result,
        config=config,
        evidence_path=args.minimum_image_app_update_evidence,
        bundle_path=args.minimum_image_app_update_bundle,
    )
    target_commit = ""
    if _declares_dependency_runtime(config):
        target_commit = subprocess.run(
            ["git", "rev-parse", args.target_ref],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    result = apply_verified_dependency_runtime(
        result,
        config=config,
        catalog_path=args.runtime_catalog,
        evidence_path=args.runtime_capability_evidence,
        expected_source_commit=target_commit,
        public_keys=public_keys,
    )
    result = apply_release_version_policy(result, version_policy)
    verify_declared_impact(
        result,
        declared_image_required=declared,
        declared_delivery_mode=declared_delivery_mode,
    )
    print(json.dumps(result._asdict(), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ReleaseImpactMismatch,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        print(f"release impact check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
