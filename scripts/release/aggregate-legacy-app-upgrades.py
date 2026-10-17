#!/usr/bin/env python3
"""Aggregate the mandatory native same-container legacy update matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


HEX = re.compile(r"[0-9a-f]{64}")
SHA40 = re.compile(r"[0-9a-f]{40}")
REQUIRED_CHECKS = {
    "signed_assets", "update_check", "apply_job", "same_container", "same_image",
    "selected_runtime", "image_packages_unchanged", "persistent_state",
    "database_canary", "credentials", "frontend", "cli", "restart", "rollback",
    "reapply", "no_dependency_generation", "cleanup",
}
PHASES = {"before", "after_apply", "after_restart", "after_rollback", "after_reapply"}
APP_PHASES = {"after_apply", "after_restart", "after_reapply"}
LEGACY_SOURCES = ["1.4.0", "1.4.1", "1.4.5"]
SCENARIOS = [
    ("stock-1.4.0", "1.4.0", "1.4.0", None),
    ("overlay-1.4.0-to-1.4.1", "1.4.0", "1.4.1", "1.4.1"),
    ("overlay-1.4.0-to-1.4.5", "1.4.0", "1.4.5", "1.4.5"),
    ("stock-1.4.1", "1.4.1", "1.4.1", None),
    ("overlay-1.4.1-to-1.4.5", "1.4.1", "1.4.5", "1.4.5"),
    ("stock-1.4.5", "1.4.5", "1.4.5", None),
]


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _scenario_map(baselines: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scenarios = baselines.get("scenarios")
    expected = [
        {
            "id": scenario_id,
            "image_version": image_version,
            "source_application_version": source_version,
            "overlay_version": overlay_version,
        }
        for scenario_id, image_version, source_version, overlay_version in SCENARIOS
    ]
    require(scenarios == expected, "Baseline scenarios are incomplete or reordered.")
    mapped = {str(row.get("id")): row for row in scenarios if isinstance(row, dict)}
    require(len(mapped) == 6 and "None" not in mapped, "Baseline scenario IDs are invalid.")
    return mapped


def _require_selected_status(
    value: object, *, scenario: dict[str, Any], target_version: str,
    bundle_sha256: str,
) -> None:
    require(isinstance(value, dict), "Selected status evidence is missing.")
    active = value.get("active_bundle")
    worker = value.get("worker_runtime")
    require(
        value.get("current_version") == target_version
        and value.get("image_version") == scenario["image_version"]
        and value.get("launcher_protocol") == 3
        and value.get("runtime_source") == "app_bundle"
        and isinstance(active, dict)
        and str(active.get("version") or "").lstrip("v") == target_version
        and isinstance(active.get("manifest"), dict)
        and active["manifest"].get("bundle_sha256") == bundle_sha256
        and not any(field in active for field in (
            "runtime_generation_id", "runtime_descriptor_sha256",
            "dependency_inventory_sha256", "runtime_trust_sha256",
        ))
        and isinstance(worker, dict)
        and worker.get("python_executable") == "/venv/bin/python"
        and worker.get("python_version") == "3.14.8"
        and worker.get("verified_generation") is None,
        "Selected status does not prove the legacy image dependency closure.",
    )


def _require_current_check(value: object, *, target_version: str) -> None:
    require(isinstance(value, dict), "Post-activation update check is missing.")
    job = value.get("last_job")
    require(
        value.get("current_version") == target_version
        and value.get("update_available") is False
        and isinstance(job, dict)
        and job.get("operation") == "check"
        and job.get("status") == "current",
        "Post-activation update check is not current.",
    )


def aggregate(
    receipts: dict[tuple[str, str], dict[str, Any]],
    *,
    baselines: dict[str, Any],
    target_version: str,
    source_commit: str,
    catalog_sha256: str,
    bundle_sha256: str,
    evidence_directory: Path,
    release_config: dict[str, Any],
    release_config_sha256: str,
) -> dict[str, Any]:
    require(SHA40.fullmatch(source_commit) is not None, "Source commit is invalid.")
    require(HEX.fullmatch(catalog_sha256) is not None, "Catalog digest is invalid.")
    require(HEX.fullmatch(bundle_sha256) is not None, "Bundle digest is invalid.")
    require(baselines.get("schema") == 1, "Baseline fixture schema is invalid.")
    require(baselines.get("target_version") == target_version, "Target version does not match fixtures.")
    require(baselines.get("repository") == "coderluii/channelwatch", "Baseline repository is invalid.")
    require(baselines.get("legacy_sources") == LEGACY_SOURCES, "Baseline legacy sources are invalid.")
    require(release_config.get("version") == target_version, "Release configuration version is unbound.")
    require(
        release_config.get("legacy_app_update_sources") == LEGACY_SOURCES,
        "Release configuration legacy sources are unbound.",
    )
    require(HEX.fullmatch(release_config_sha256) is not None, "Release configuration digest is invalid.")
    scenarios = _scenario_map(baselines)
    expected = {(arch, scenario_id) for arch in ("amd64", "arm64") for scenario_id in scenarios}
    require(set(receipts) == expected, "Native legacy update receipt matrix is incomplete or has extras.")
    summaries = []
    for architecture, scenario_id in sorted(expected):
        receipt = receipts[(architecture, scenario_id)]
        scenario = scenarios[scenario_id]
        require(receipt.get("schema") == 1 and receipt.get("status") == "passed", "Receipt did not pass.")
        require(receipt.get("evidence_kind") == "native", "Synthetic evidence cannot satisfy the release gate.")
        require(receipt.get("architecture") == architecture, "Receipt architecture is unbound.")
        require(receipt.get("scenario") == scenario, "Receipt scenario is unbound.")
        require(receipt.get("target_version") == target_version, "Receipt target version is unbound.")
        require(receipt.get("source_commit") == source_commit, "Receipt source commit is unbound.")
        require(receipt.get("catalog_sha256") == catalog_sha256, "Receipt catalog digest is unbound.")
        require(receipt.get("bundle_sha256") == bundle_sha256, "Receipt bundle digest is unbound.")
        require(
            receipt.get("release_config_sha256") == release_config_sha256,
            "Receipt release configuration is unbound.",
        )
        image = baselines["images"][scenario["image_version"]]
        baseline = receipt.get("baseline")
        require(isinstance(baseline, dict), "Receipt baseline is missing.")
        require(baseline == {
            "repository": baselines["repository"],
            "source_commit": image["source_commit"],
            "index_digest": image["index_digest"],
            "manifest_digest": image["manifests"][architecture],
        }, "Receipt baseline identity is unbound.")
        identities = receipt.get("identity")
        require(isinstance(identities, dict) and set(identities) == PHASES, "Receipt phase identities are incomplete.")
        first = identities["before"]
        require(isinstance(first, dict) and set(first) == {"container_id", "image_id", "repo_digest"}, "Receipt identity is invalid.")
        require(all(value == first for value in identities.values()), "Container or image identity changed.")
        checks = receipt.get("checks")
        require(isinstance(checks, dict) and set(checks) == REQUIRED_CHECKS, "Receipt checks are incomplete.")
        require(all(value is True for value in checks.values()), "A mandatory legacy update check failed.")
        for name in ("apply_status", "restart_status", "reapply_status"):
            _require_selected_status(
                receipt.get(name), scenario=scenario,
                target_version=target_version, bundle_sha256=bundle_sha256,
            )
        _require_current_check(
            receipt.get("post_activation_check"), target_version=target_version
        )
        _require_current_check(
            receipt.get("post_restart_check"), target_version=target_version
        )
        rollback_status = receipt.get("rollback_status")
        require(isinstance(rollback_status, dict), "Rollback status is missing.")
        expected_runtime = "image" if scenario["overlay_version"] is None else "app_bundle"
        require(
            rollback_status.get("current_version") == scenario["source_application_version"]
            and rollback_status.get("image_version") == scenario["image_version"]
            and rollback_status.get("launcher_protocol") == 3
            and rollback_status.get("runtime_source") == expected_runtime,
            "Rollback did not restore the exact legacy source state.",
        )
        for name, operation in (
            ("apply_job", "apply"), ("rollback_job", "rollback"),
            ("reapply_job", "apply"),
        ):
            job = receipt.get(name)
            require(
                isinstance(job, dict)
                and job.get("operation") == operation
                and job.get("status") == "success",
                f"{name} is not a successful durable job.",
            )
        storage = receipt.get("runtime_storage")
        require(
            isinstance(storage, dict)
            and set(storage) == {"before", "after_apply", "after_rollback", "after_reapply"}
            and all(
                isinstance(value, dict)
                and value.get("dependency_generation_absent") is True
                for value in storage.values()
            ),
            "Dependency-generation absence evidence is incomplete.",
        )
        for name in ("browser_sha256", "cli_output_sha256"):
            phase_hashes = receipt.get(name)
            require(
                isinstance(phase_hashes, dict)
                and set(phase_hashes) == APP_PHASES | {"after_rollback"}
                and all(HEX.fullmatch(str(value)) is not None for value in phase_hashes.values()),
                f"{name} evidence is incomplete.",
            )
        logs = receipt.get("logs_sha256")
        require(isinstance(logs, dict) and set(logs) == {"app", "feed"}, "Receipt logs are incomplete.")
        log_dir = evidence_directory / f"legacy-app-upgrade-{architecture}-{scenario_id}-logs"
        for name, digest in logs.items():
            require(HEX.fullmatch(str(digest)) is not None, "Receipt log digest is invalid.")
            path = log_dir / f"{name}.log"
            require(path.is_file() and sha256_file(path) == digest, "Receipt log artifact is missing or changed.")
        summaries.append({
            "architecture": architecture,
            "scenario": scenario_id,
            "image_version": scenario["image_version"],
            "source_application_version": scenario["source_application_version"],
            "receipt_sha256": hashlib.sha256(
                (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest(),
        })
    return {
        "schema": 1,
        "status": "passed",
        "evidence_kind": "native",
        "target_version": target_version,
        "source_commit": source_commit,
        "catalog_sha256": catalog_sha256,
        "bundle_sha256": bundle_sha256,
        "release_config_sha256": release_config_sha256,
        "architectures": ["amd64", "arm64"],
        "native_receipt_count": len(summaries),
        "qualifications": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--release-config", type=Path, required=True)
    parser.add_argument("--receipts-directory", type=Path, required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baselines = json.loads(args.baselines.read_text(encoding="utf-8"))
    release_config = json.loads(args.release_config.read_text(encoding="utf-8"))
    scenarios = _scenario_map(baselines)
    receipts = {}
    for architecture in ("amd64", "arm64"):
        for scenario_id in scenarios:
            path = args.receipts_directory / f"legacy-app-upgrade-{architecture}-{scenario_id}.json"
            if not path.is_file():
                raise SystemExit(f"Missing native receipt: {path.name}")
            receipts[(architecture, scenario_id)] = json.loads(path.read_text(encoding="utf-8"))
    result = aggregate(
        receipts,
        baselines=baselines,
        target_version=args.target_version.strip().lstrip("v"),
        source_commit=args.source_commit,
        catalog_sha256=sha256_file(args.catalog),
        bundle_sha256=sha256_file(args.bundle),
        evidence_directory=args.receipts_directory,
        release_config=release_config,
        release_config_sha256=sha256_file(args.release_config),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
