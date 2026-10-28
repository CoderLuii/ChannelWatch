import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/aggregate-legacy-app-upgrades.py"
CONFIG = ROOT / "scripts/release/release-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("legacy_app_aggregate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["1.6.1", "1.6.2", "1.6.3", "1.6.4"])
def matrix(tmp_path, request):
    fixture = json.loads(
        (ROOT / "scripts/release/legacy-app-upgrade-baselines.json").read_text()
    )
    target_version = request.param
    fixture["target_version"] = target_version
    fixture["legacy_sources"] = ["1.4.0", "1.4.1", "1.4.5", "1.6.0"]
    if target_version in {"1.6.2", "1.6.3", "1.6.4"}:
        fixture["legacy_sources"].append("1.6.1")
    if target_version in {"1.6.3", "1.6.4"}:
        fixture["legacy_sources"].append("1.6.2")
    if target_version == "1.6.4":
        fixture["legacy_sources"].append("1.6.3")
    receipts = {}
    config_sha256 = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    for architecture in ("amd64", "arm64"):
      for qualification_mode in ("direct", "chained"):
        for scenario in fixture["scenarios"]:
            scenario_id = scenario["id"]
            log_dir = tmp_path / f"legacy-app-upgrade-{architecture}-{qualification_mode}-{scenario_id}-logs"
            log_dir.mkdir()
            logs = {}
            for name in ("app", "feed"):
                data = f"{architecture}-{qualification_mode}-{scenario_id}-{name}\n".encode()
                (log_dir / f"{name}.log").write_bytes(data)
                logs[name] = hashlib.sha256(data).hexdigest()
            identity = {
                "container_id": hashlib.sha256(f"{architecture}-{qualification_mode}-{scenario_id}".encode()).hexdigest(),
                "image_id": "sha256:" + ("a" if architecture == "amd64" else "b") * 64,
                "repo_digest": fixture["repository"] + "@" + fixture["images"][scenario["image_version"]]["manifests"][architecture],
            }
            selected_status = {
                "current_version": target_version,
                "image_version": scenario["image_version"],
                "launcher_protocol": 3,
                "runtime_source": "app_bundle",
                "active_bundle": {
                    "version": target_version,
                    "manifest": {"bundle_sha256": "2" * 64},
                },
                "worker_runtime": {
                    "python_executable": "/venv/bin/python",
                    "python_version": "3.14.8",
                    "verified_generation": None,
                },
            }
            current_check = {
                "current_version": target_version,
                "update_available": False,
                "last_job": {"operation": "check", "status": "current"},
            }
            rollback_status = {
                "current_version": "1.6.0" if qualification_mode == "chained" else scenario["source_application_version"],
                "image_version": scenario["image_version"],
                "launcher_protocol": 3,
                "runtime_source": "app_bundle" if qualification_mode == "chained" or scenario["overlay_version"] is not None else "image",
            }
            receipt = {
                "schema": 2,
                "status": "passed",
                "evidence_kind": "native",
                "architecture": architecture,
                "qualification_mode": qualification_mode,
                "scenario": scenario,
                "target_version": target_version,
                "source_commit": "f" * 40,
                "catalog_sha256": "1" * 64,
                "bundle_sha256": "2" * 64,
                "release_config_sha256": config_sha256,
                "baseline": {
                    "repository": fixture["repository"],
                    "source_commit": fixture["images"][scenario["image_version"]]["source_commit"],
                    "index_digest": fixture["images"][scenario["image_version"]]["index_digest"],
                    "manifest_digest": fixture["images"][scenario["image_version"]]["manifests"][architecture],
                },
                "identity": {phase: copy.deepcopy(identity) for phase in ("before", "after_apply", "after_restart", "after_rollback", "after_reapply")},
                "apply_status": copy.deepcopy(selected_status),
                "restart_status": copy.deepcopy(selected_status),
                "reapply_status": copy.deepcopy(selected_status),
                "rollback_status": rollback_status,
                "post_activation_check": copy.deepcopy(current_check),
                "post_restart_check": copy.deepcopy(current_check),
                "apply_job": {"operation": "apply", "status": "success"},
                "rollback_job": {"operation": "rollback", "status": "success"},
                "reapply_job": {"operation": "apply", "status": "success"},
                "runtime_storage": {
                    phase: {"dependency_generation_absent": True}
                    for phase in ("before", "after_apply", "after_rollback", "after_reapply")
                },
                "browser_sha256": {
                    phase: "3" * 64
                    for phase in ("after_apply", "after_restart", "after_rollback", "after_reapply")
                },
                "cli_output_sha256": {
                    phase: "4" * 64
                    for phase in ("after_apply", "after_restart", "after_rollback", "after_reapply")
                },
                "checks": {name: True for name in (
                    "signed_assets", "update_check", "apply_job", "same_container", "same_image",
                    "selected_runtime", "image_packages_unchanged", "persistent_state", "database_canary",
                    "credentials", "frontend", "cli", "restart", "rollback", "reapply",
                    "no_dependency_generation", "cleanup",
                )},
                "logs_sha256": logs,
            }
            if qualification_mode == "chained":
                receipt["bridge_qualification"] = {
                    "release": fixture["bridge_release"],
                    "catalog_sha256": fixture["bridge_release"]["catalog_sha256"],
                    "bundle_sha256": fixture["bridge_release"]["bundle_sha256"],
                    "status": {
                        "current_version": "1.6.0",
                        "image_version": scenario["image_version"],
                        "launcher_protocol": 3,
                        "runtime_source": "app_bundle",
                        "active_bundle": {
                            "version": "1.6.0",
                            "path": "/config/app-versions/1.6.0",
                            "manifest": {
                                "bundle_sha256": fixture["bridge_release"]["bundle_sha256"]
                            },
                        },
                    },
                    "selected_runtime_info": {
                        "diagnostic_source": "selected_application",
                        "application_version": "1.6.0",
                        "application_dir": "/config/app-versions/1.6.0",
                        "verified_generation": None,
                        "process_runtime": {
                            "python_executable": "/venv/bin/python",
                            "python_prefix": "/venv",
                            "python_version": "3.14.8",
                        },
                    },
                    "identity": {"before": identity, "after": copy.deepcopy(identity)},
                    "runtime_storage": {
                        "before": {"dependency_generation_absent": True},
                        "after": {"dependency_generation_absent": True},
                    },
                }
            receipts[(architecture, qualification_mode, scenario_id)] = receipt
    return fixture, receipts, tmp_path


def aggregate(matrix):
    fixture, receipts, root = matrix
    release_config = json.loads(CONFIG.read_text())
    release_config["version"] = fixture["target_version"]
    release_config["legacy_app_update_sources"] = fixture["legacy_sources"]
    return load_module().aggregate(
        receipts,
        baselines=fixture,
        target_version=fixture["target_version"],
        source_commit="f" * 40,
        catalog_sha256="1" * 64,
        bundle_sha256="2" * 64,
        evidence_directory=root,
        release_config=release_config,
        release_config_sha256=hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
    )


def test_exact_twenty_four_receipt_native_matrix_passes(matrix):
    result = aggregate(matrix)
    assert result["status"] == "passed"
    assert result["native_receipt_count"] == 24
    assert result["direct_receipt_count"] == 12
    assert result["chained_receipt_count"] == 12
    assert result["architectures"] == ["amd64", "arm64"]


@pytest.mark.parametrize("case", [
    "missing", "extra", "synthetic", "arch", "source", "hash", "config",
    "identity", "check", "selected", "post_check", "job", "storage", "log",
    "mode", "bridge", "bridge_identity", "bridge_storage",
    "bridge_runtime", "chained_rollback",
])
def test_incomplete_or_unbound_matrix_is_rejected(matrix, case):
    fixture, receipts, root = matrix
    row = receipts[("arm64", "direct", "stock-1.4.0")]
    if case == "missing":
        receipts.pop(("arm64", "direct", "stock-1.4.0"))
    elif case == "extra":
        receipts[("arm64", "direct", "invented")] = copy.deepcopy(row)
    elif case == "synthetic":
        row["evidence_kind"] = "synthetic"
    elif case == "arch":
        row["architecture"] = "amd64"
    elif case == "source":
        row["source_commit"] = "0" * 40
    elif case == "hash":
        row["bundle_sha256"] = "0" * 64
    elif case == "config":
        row["release_config_sha256"] = "0" * 64
    elif case == "identity":
        row["identity"]["after_restart"]["container_id"] = "0" * 64
    elif case == "check":
        row["checks"]["rollback"] = False
    elif case == "selected":
        row["apply_status"]["runtime_source"] = "image"
    elif case == "post_check":
        row["post_activation_check"]["last_job"]["status"] = "available"
    elif case == "job":
        row["rollback_job"]["status"] = "restarting"
    elif case == "storage":
        row["runtime_storage"]["after_apply"]["dependency_generation_absent"] = False
    elif case == "log":
        (root / "legacy-app-upgrade-arm64-direct-stock-1.4.0-logs" / "app.log").write_text("tampered")
    elif case == "mode":
        row["qualification_mode"] = "chained"
    elif case == "bridge":
        receipts[("arm64", "chained", "stock-1.4.0")]["bridge_qualification"][
            "bundle_sha256"
        ] = "0" * 64
    elif case == "bridge_identity":
        receipts[("arm64", "chained", "stock-1.4.0")]["bridge_qualification"][
            "identity"
        ].pop("before")
    elif case == "bridge_storage":
        receipts[("arm64", "chained", "stock-1.4.0")]["bridge_qualification"][
            "runtime_storage"
        ].pop("before")
    elif case == "bridge_runtime":
        receipts[("arm64", "chained", "stock-1.4.0")]["bridge_qualification"][
            "selected_runtime_info"
        ]["application_version"] = "1.5.3"
    elif case == "chained_rollback":
        receipts[("arm64", "chained", "stock-1.4.0")]["rollback_status"][
            "current_version"
        ] = "1.4.0"
    with pytest.raises(ValueError):
        aggregate(matrix)
