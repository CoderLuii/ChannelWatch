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
LEGACY_SOURCES = ["1.4.0", "1.4.1", "1.4.5", "1.6.0"]
LEGACY_SOURCE_CONTRACTS = {
    "1.6.1": LEGACY_SOURCES,
    "1.6.2": [*LEGACY_SOURCES, "1.6.1"],
    "1.6.3": [*LEGACY_SOURCES, "1.6.1", "1.6.2"],
    "1.6.4": ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3"],
    "1.6.5": ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3", "1.6.4"],
    "1.6.6": ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3", "1.6.4", "1.6.5"],
    "1.6.7": ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3", "1.6.4", "1.6.5", "1.6.6"],
    "1.6.8": ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3", "1.6.4", "1.6.5", "1.6.6", "1.6.7"],
    "1.6.9": ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3", "1.6.4", "1.6.5", "1.6.6", "1.6.7", "1.6.8"],
    "1.7.0": ["1.4.0", "1.4.1", "1.4.5", "1.6.0", "1.6.1", "1.6.2", "1.6.3", "1.6.4", "1.6.5", "1.6.6", "1.6.7", "1.6.8", "1.6.9"],
}
QUALIFICATION_MODES = ("direct", "chained")
SCENARIOS = [
    ("stock-1.4.0", "1.4.0", "1.4.0", None),
    ("overlay-1.4.0-to-1.4.1", "1.4.0", "1.4.1", "1.4.1"),
    ("overlay-1.4.0-to-1.4.5", "1.4.0", "1.4.5", "1.4.5"),
    ("stock-1.4.1", "1.4.1", "1.4.1", None),
    ("overlay-1.4.1-to-1.4.5", "1.4.1", "1.4.5", "1.4.5"),
    ("stock-1.4.5", "1.4.5", "1.4.5", None),
]


BRIDGE_RELEASE_160 = {
    "version": "1.6.0",
    "source_commit": "b48057184752c912e4ae9b3a474374704d896073",
    "catalog_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.0/channelwatch-catalog-v1.6.0.json",
    "catalog_sha256": "bc9bcdb38c44af581835fdd4f29e2a2550eb8bb5d51b8308395a803110feb185",
    "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.0/channelwatch-app-v1.6.0.zip",
    "bundle_sha256": "f8cbdca71ef254df84b0e3b472facce8ece201b4928218caf757f4c5ddf53914",
}
BRIDGE_RELEASE_166 = {
    "version": "1.6.6",
    "source_commit": "2764b98f7b95a778162e844a1b31b81d7f149386",
    "catalog_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.6/channelwatch-catalog-v1.6.6.json",
    "catalog_sha256": "8c9ff6c68758564028b87f7afc8a5646cf067ffb531fd6ad24f59e2efefaa250",
    "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.6/channelwatch-app-v1.6.6.zip",
    "bundle_sha256": "f2d74474798882777c9bcbb3d2372c0382a844664bb4638985e0e499d9d6ac6b"
}
BRIDGE_RELEASE_167 = {
    "version": "1.6.7",
    "source_commit": "97525b9e3e3c05f91afa93703c5c420d9a84a06c",
    "catalog_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.7/channelwatch-catalog-v1.6.7.json",
    "catalog_sha256": "63cc307ed417c34e99ac8c5c566bb83c1042d53ca893071cf12af75dd8146a09",
    "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.7/channelwatch-app-v1.6.7.zip",
    "bundle_sha256": "ea598fe0a227ede06c8260b7c4fb066068a0fb7ba5be9982f0b15c086bc8f111",
}
BRIDGE_RELEASE_168 = {
    "version": "1.6.8",
    "source_commit": "de4e5393ec466e28a506cf59b825030222974c13",
    "catalog_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.8/channelwatch-catalog-v1.6.8.json",
    "catalog_sha256": "66bf26f7505470aa388bea4446c1fecabca8edf9778d0ef90a31665e84f1a9d0",
    "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.8/channelwatch-app-v1.6.8.zip",
    "bundle_sha256": "40ce63c66fd6368283572d934387e8a2907cd9c63c19265a303a409f33c9618e",
}
BRIDGE_RELEASE_169 = {
    "version": "1.6.9",
    "source_commit": "0e670ed61849ebdb613cc985573aeba14aee1e10",
    "catalog_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.9/channelwatch-catalog-v1.6.9.json",
    "catalog_sha256": "518cdd0b7cef8a114af42a23dae4649170c60ed49d98cc526b8bccc2734bea51",
    "bundle_url": "https://github.com/CoderLuii/ChannelWatch/releases/download/v1.6.9/channelwatch-app-v1.6.9.zip",
    "bundle_sha256": "eb0242c67879ad272a5dc2968379a6fde876966b4c23f9dd99f8b8ee9d34d08b"
}


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
    receipts: dict[tuple[str, str, str], dict[str, Any]],
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
    require(baselines.get("schema") == 2, "Baseline fixture schema is invalid.")
    require(baselines.get("target_version") == target_version, "Target version does not match fixtures.")
    require(baselines.get("repository") == "coderluii/channelwatch", "Baseline repository is invalid.")
    sources = LEGACY_SOURCE_CONTRACTS.get(target_version)
    require(sources is not None and baselines.get("legacy_sources") == sources, "Baseline legacy sources are invalid.")
    require(
        baselines.get("qualification_modes") == list(QUALIFICATION_MODES),
        "Baseline qualification modes are invalid.",
    )
    require(release_config.get("version") == target_version, "Release configuration version is unbound.")
    require(
        release_config.get("legacy_app_update_sources") == sources,
        "Release configuration legacy sources are unbound.",
    )
    require(HEX.fullmatch(release_config_sha256) is not None, "Release configuration digest is invalid.")
    scenarios = _scenario_map(baselines)
    expected = {
        (arch, mode, scenario_id)
        for arch in ("amd64", "arm64")
        for mode in QUALIFICATION_MODES
        for scenario_id in scenarios
    }
    require(set(receipts) == expected, "Native legacy update receipt matrix is incomplete or has extras.")
    summaries = []
    for architecture, qualification_mode, scenario_id in sorted(expected):
        receipt = receipts[(architecture, qualification_mode, scenario_id)]
        scenario = scenarios[scenario_id]
        require(receipt.get("schema") == 2 and receipt.get("status") == "passed", "Receipt did not pass.")
        require(receipt.get("evidence_kind") == "native", "Synthetic evidence cannot satisfy the release gate.")
        require(receipt.get("architecture") == architecture, "Receipt architecture is unbound.")
        require(
            receipt.get("qualification_mode") == qualification_mode,
            "Receipt qualification mode is unbound.",
        )
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
        expected_bridge = {
            "1.6.7": BRIDGE_RELEASE_166,
            "1.6.8": BRIDGE_RELEASE_167,
            "1.6.9": BRIDGE_RELEASE_168,
            "1.7.0": BRIDGE_RELEASE_169,
        }.get(target_version, BRIDGE_RELEASE_160)
        rollback_version = (
            expected_bridge["version"]
            if qualification_mode == "chained"
            else scenario["source_application_version"]
        )
        expected_runtime = (
            "app_bundle"
            if qualification_mode == "chained" or scenario["overlay_version"] is not None
            else "image"
        )
        require(
            rollback_status.get("current_version") == rollback_version
            and rollback_status.get("image_version") == scenario["image_version"]
            and rollback_status.get("launcher_protocol") == 3
            and rollback_status.get("runtime_source") == expected_runtime,
            "Rollback did not restore the exact legacy source state.",
        )
        bridge = receipt.get("bridge_qualification")
        if qualification_mode == "chained":
            require(
                baselines.get("bridge_release") == expected_bridge
                and set(expected_bridge) == {
                    "version", "source_commit", "catalog_url", "catalog_sha256",
                    "bundle_url", "bundle_sha256",
                }
                and SHA40.fullmatch(str(expected_bridge.get("source_commit") or "")) is not None
                and HEX.fullmatch(str(expected_bridge.get("catalog_sha256") or "")) is not None
                and HEX.fullmatch(str(expected_bridge.get("bundle_sha256") or "")) is not None,
                "The immutable predecessor bridge contract is invalid.",
            )
            bridge_identity = bridge.get("identity") if isinstance(bridge, dict) else None
            bridge_storage = bridge.get("runtime_storage") if isinstance(bridge, dict) else None
            bridge_status = bridge.get("status") if isinstance(bridge, dict) else None
            bridge_active = bridge_status.get("active_bundle") if isinstance(bridge_status, dict) else None
            bridge_runtime = bridge.get("selected_runtime_info") if isinstance(bridge, dict) else None
            bridge_process = bridge_runtime.get("process_runtime") if isinstance(bridge_runtime, dict) else None
            require(
                isinstance(bridge, dict)
                and bridge.get("release") == expected_bridge
                and bridge.get("catalog_sha256") == expected_bridge["catalog_sha256"]
                and bridge.get("bundle_sha256") == expected_bridge["bundle_sha256"]
                and isinstance(bridge_status, dict)
                and bridge_status.get("current_version") == expected_bridge["version"]
                and bridge_status.get("image_version") == scenario["image_version"]
                and bridge_status.get("launcher_protocol") == 3
                and bridge_status.get("runtime_source") == "app_bundle"
                and isinstance(bridge_active, dict)
                and bridge_active.get("version") == expected_bridge["version"]
                and bridge_active.get("manifest", {}).get("bundle_sha256")
                == expected_bridge["bundle_sha256"]
                and isinstance(bridge_runtime, dict)
                and bridge_runtime.get("diagnostic_source") == "selected_application"
                and bridge_runtime.get("application_version") == expected_bridge["version"]
                and bridge_runtime.get("application_dir") == bridge_active.get("path")
                and bridge_runtime.get("verified_generation") is None
                and isinstance(bridge_process, dict)
                and bridge_process.get("python_executable") == "/venv/bin/python"
                and bridge_process.get("python_prefix") == "/venv"
                and bridge_process.get("python_version") == "3.14.8"
                and isinstance(bridge_identity, dict)
                and set(bridge_identity) == {"before", "after"}
                and bridge_identity["before"] == bridge_identity["after"] == first
                and isinstance(bridge_storage, dict)
                and set(bridge_storage) == {"before", "after"}
                and all(
                    isinstance(row, dict)
                    and row.get("dependency_generation_absent") is True
                    for row in bridge_storage.values()
                ),
                "Chained receipt does not prove the immutable predecessor bridge.",
            )
        else:
            require(bridge is None, "Direct receipt unexpectedly includes bridge evidence.")
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
        log_dir = evidence_directory / f"legacy-app-upgrade-{architecture}-{qualification_mode}-{scenario_id}-logs"
        for name, digest in logs.items():
            require(HEX.fullmatch(str(digest)) is not None, "Receipt log digest is invalid.")
            path = log_dir / f"{name}.log"
            require(path.is_file() and sha256_file(path) == digest, "Receipt log artifact is missing or changed.")
        summaries.append({
            "architecture": architecture,
            "qualification_mode": qualification_mode,
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
        "direct_receipt_count": sum(
            row["qualification_mode"] == "direct" for row in summaries
        ),
        "chained_receipt_count": sum(
            row["qualification_mode"] == "chained" for row in summaries
        ),
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
        for qualification_mode in QUALIFICATION_MODES:
            for scenario_id in scenarios:
                path = args.receipts_directory / f"legacy-app-upgrade-{architecture}-{qualification_mode}-{scenario_id}.json"
                if not path.is_file():
                    raise SystemExit(f"Missing native receipt: {path.name}")
                receipts[(architecture, qualification_mode, scenario_id)] = json.loads(path.read_text(encoding="utf-8"))
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
