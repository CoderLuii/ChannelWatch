#!/usr/bin/env python3
"""Classify whether a ChannelWatch release requires a new container image."""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import importlib.util
import hashlib
import json
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

IMAGE_REFRESH_PATHS = {
    "app/core/helpers/atomic_io.py",
    "app/core/helpers/migration.py",
    "app/core/update_catalog.py",
    "app/core/update_policy.py",
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
    normalized = "".join(lines[:start]) + "DEFAULT_SETTINGS = {}\n" + "".join(lines[end:])
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
    """Make a post-v1 minor milestone image-required regardless of path mix."""
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
        f"{HISTORICAL_SUCCESS_SCENARIO}:{tag.lstrip('v')}"
        for tag in LEGACY_BRIDGE_TAGS
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
        recovery_mismatches = _historical_field_mismatches(
            recovery, recovery_expected
        )
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
            versions.append(stripped.removeprefix("ARG VERSION=").strip('"\''))
            normalized.append("ARG VERSION=<release-version>")
        elif stripped.startswith("ENV CHANNELWATCH_LAUNCHER_PROTOCOL="):
            raw = stripped.removeprefix(
                "ENV CHANNELWATCH_LAUNCHER_PROTOCOL="
            ).strip('"\'')
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
