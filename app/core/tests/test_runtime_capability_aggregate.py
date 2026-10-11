from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from core.tests.test_release_capability_policy import SOURCE_COMMIT, _config, _evidence, _signed_catalog


def _module():
    path = Path(__file__).resolve().parents[3] / "scripts/release/aggregate-runtime-capability.py"
    spec = importlib.util.spec_from_file_location("runtime_capability_aggregate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker_runtime(pid: int, *, selected_root: str | None = None) -> dict:
    if selected_root is None:
        return {
            "python_executable": "/venv/bin/python",
            "python_version": "3.14.0",
            "verified_generation": None,
        }
    prefix = selected_root + "/environment" if selected_root else "/venv"
    site = (
        selected_root + "/dependencies/site-packages"
        if selected_root
        else "/venv/lib/python3.14/site-packages"
    )
    dependencies = {
        module: {
            "loaded": True,
            "path": f"{site}/{module}/__init__.py",
            "version": "2.0.54" if module == "sqlalchemy" else "1.0.0",
        }
        for module in (
            "requests",
            "cryptography",
            "sqlmodel",
            "uvicorn",
            "pydantic_core",
            "sqlalchemy",
            "httptools",
        )
    }
    return {
        "pid": pid,
        "python_executable": prefix + "/bin/python",
        "python_prefix": prefix,
        "python_version": "3.14.0",
        "dependencies": dependencies,
        "verified_generation": {
            "path": selected_root,
            "runtime_generation_id": selected_root.rsplit("/", 1)[-1],
            "runtime_descriptor_sha256": selected_root.rsplit("/", 1)[-1],
            "dependency_inventory_sha256": "d" * 64,
        },
    }


def _image_command(component: str) -> str:
    suffix = (
        "core --stay-alive"
        if component == "core"
        else "ui --host 0.0.0.0 --port 8501 --log-level warning"
    )
    return f"/venv/bin/python -u /app/core/runtime_launcher.py {suffix}"


def _image_operational_state(*, core_pid: int = 11, ui_pid: int = 12) -> dict:
    commands = {component: _image_command(component) for component in ("core", "ui")}
    pids = {"core": core_pid, "ui": ui_pid}
    return {
        "status_worker_scope": "frozen_image_ui_status_interpreter_only",
        "status_worker": _worker_runtime(ui_pid),
        "commands": commands,
        "processes": {
            component: {"pid": pids[component], "command": commands[component]}
            for component in ("core", "ui")
        },
        "services": {
            component: {
                "status": "RUNNING",
                "pid": pids[component],
                "uptime": "0:00:05",
            }
            for component in ("core", "ui")
        },
        "health": {
            "startup": {"status": "ready"},
            "ready": {"status": "ready", "ready": True},
        },
    }


def _selected_operational_state(generation: str) -> dict:
    root = "/config/channelwatch-runtime/releases/" + generation
    state = _image_operational_state(core_pid=21, ui_pid=22)
    state["status_worker_scope"] = "ui_status_handler_only"
    state["status_worker"] = _worker_runtime(22, selected_root=root)
    launcher_pids = {"core": 31, "ui": 32}
    state["launchers"] = {
        component: {
            "pid": launcher_pids[component],
            "command": _image_command(component),
        }
        for component in ("core", "ui")
    }
    commands = {
        component: (
            f"{root}/environment/bin/python -I -B -S /app/core/runtime_worker.py "
            f"--image-app-dir /app --mode {component} --generation {root} "
            f"--activation-id {'1' * 32} --descriptor-sha256 {generation} --"
            + (
                " --stay-alive"
                if component == "core"
                else ""
            )
        )
        for component in ("core", "ui")
    }
    commands["ui"] = commands["ui"].replace(
        f"--descriptor-sha256 {generation} --",
        f"--descriptor-sha256 {generation} --host 0.0.0.0 --port 8501 "
        "--log-level warning --",
    )
    state["commands"] = commands
    state["processes"] = {
        component: {
            "pid": pid,
            "ppid": launcher_pids[component],
            "command": commands[component],
        }
        for component, pid in (("core", 21), ("ui", 22))
    }
    state["services"] = {
        component: {
            "status": "RUNNING",
            "pid": launcher_pids[component],
            "uptime": "0:00:05",
        }
        for component in ("core", "ui")
    }
    state["runtime_components"] = {
        component: {
            "runtime_generation_id": generation,
            "runtime_descriptor_sha256": generation,
            "dependency_inventory_sha256": "d" * 64,
            "process_runtime": _worker_runtime(pid, selected_root=root),
        }
        for component, pid in (("core", 21), ("ui", 22))
    }
    return state


def _control_state() -> dict:
    state = {
        name: {"type": "absent"}
        for name in (
            "active.json",
            "rollback.json",
            "restart-required.json",
            "restart-required.lock",
            "restart-services-accepted.json",
            "activation-pending.json",
            "activation-core-ready.json",
            "activation-ui-ready.json",
        )
    }
    state["active.json"] = {
        "type": "file",
        "mode": "0600",
        "size": 2,
        "sha256": "1" * 64,
    }
    return state


def _publication_proof(
    version="1.5.4",
    image_version="1.5.3",
    *,
    generation="a" * 64,
    bundle_sha256="b" * 64,
    expanded_bytes=200,
    member_count=2,
    parser_record: dict | None = None,
    image_imports: dict | None = None,
):
    return {
        "damaged_native_recovery": _probe_proof(
            image_version,
            damaged=True,
            version=version,
            generation=generation,
            bundle_sha256=bundle_sha256,
            parser_record=parser_record,
            image_imports=image_imports,
        ),
        "filesystem_refusals": {
            "noexec": _probe_proof(
                image_version, version=version, bundle_sha256=bundle_sha256, kind="noexec"
            , required_bytes=100 + expanded_bytes + 64 * 1024 * 1024
            ),
            "low_space": _probe_proof(
                image_version,
                version=version,
                bundle_sha256=bundle_sha256,
                kind="low_space",
                required_bytes=100 + expanded_bytes + 64 * 1024 * 1024,
            ),
        },
        "resource_observations": {
            "bundle_bytes": 100,
            "expanded_bytes": expanded_bytes,
            "member_count": member_count,
            "preparation_duration_seconds": 1.0, "restart_duration_seconds": 2.0,
            "rollback_duration_seconds": 1.0, "reapply_duration_seconds": 1.0,
            "selected_generation_disk_bytes": max(300, expanded_bytes),
            "free_bytes_before": 100000000,
            "free_bytes_after": 99900000,
            "sample_count": 2,
            "peak_container_memory_bytes": 1048576,
        },
        "live_ui": {
            "status": "passed", "apply_requests": 1, "rollback_requests": 1,
            "target_version": version, "final_version": image_version,
            "final_active_bundle": None, "observed_progress": True,
            "console_errors": [], "page_errors": [],
            "final_health": {
                "startup": {"status": "ready"},
                "ready": {"status": "ready", "ready": True},
            },
            "page_url": "http://127.0.0.1:8501/#settings:updates",
            "page_title": "ChannelWatch",
            "screenshots": {name: {"file": name + ".png", "sha256": "a" * 64, "size": 1}
                            for name in ("selected", "rollback")},
            "container_id_before": "e" * 64,
            "container_id_after": "e" * 64,
            "initial_operational_state": _image_operational_state(),
            "final_operational_state": _image_operational_state(),
            "evidence_json": {
                "file": "runtime-update-live.json",
                "sha256": "a" * 64,
                "size": 1,
            },
            "playwright_log": {
                "file": "runtime-update-live.log",
                "sha256": "a" * 64,
                "size": 1,
            },
        },
    }


def _probe_proof(
    image_version,
    *,
    damaged=False,
    version="1.5.4",
    generation="a" * 64,
    bundle_sha256="b" * 64,
    kind="noexec",
    parser_record: dict | None = None,
    image_imports: dict | None = None,
    required_bytes: int | None = None,
):
    initial = _image_operational_state()
    recovered = _image_operational_state(core_pid=31, ui_pid=32)
    inventory = {"sha256": "c" * 64, "path_count": 10, "regular_file_bytes": 100}
    persistent = {
        path: "d" * 64
        for path in ("/config/settings.json", "/config/encryption.key")
    }
    if damaged:
        root = "/config/channelwatch-runtime/releases/" + generation
        parser = copy.deepcopy(parser_record) if parser_record is not None else {
            "path": root + "/dependencies/site-packages/httptools/parser/parser.so",
            "version": "0.9.0",
            "sha256": "f" * 64,
        }
        parser_path = parser["path"]
        successful_apply = {
            "status": "success",
            "job_id": "b" * 32,
            "operation": "apply",
            "version": version,
            "bundle_sha256": bundle_sha256,
        }
        active = {
            "version": version,
            "path": root,
            "runtime_generation_id": generation,
            "runtime_descriptor_sha256": generation,
            "dependency_inventory_sha256": "d" * 64,
            "manifest": {"bundle_sha256": bundle_sha256},
        }
        marker_payload = {
            "schema": 1,
            "mode": "official_signed_only",
            "failed_version": version,
            "failed_bundle_sha256": bundle_sha256,
            "entered_at": "2026-10-12T03:00:00Z",
        }
        def control_record(payload, digest):
            return {
                "type": "file", "mode": "0600", "size": 100,
                "sha256": digest * 64, "payload": copy.deepcopy(payload),
            }
        recovery_control = {
            "active.json": control_record(active, "1"),
            "rollback.json": control_record({"target_version": version}, "2"),
            "update-job.json": control_record(successful_apply, "3"),
            "official-recovery-mode.json": control_record(marker_payload, "4"),
        }
        completed_rollback = {
            "job_id": "c" * 32,
            "operation": "rollback",
            "status": "success",
            "version": version,
            "rolled_back_from": version,
            "restored_version": image_version,
            "rollback_applied": True,
            "restart_required": False,
            "startup_components": {
                component: {
                    "healthy": True, "version": image_version,
                    "ready_at": "2026-10-12T03:01:00Z",
                }
                for component in ("core", "ui")
            },
        }
        post_control = {
            "active.json": {"type": "absent"},
            "rollback.json": control_record({"target_version": version}, "2"),
            "update-job.json": control_record(completed_rollback, "5"),
            "official-recovery-mode.json": {"type": "absent"},
        }
        recovery_status = {
            "current_version": image_version,
            "image_version": image_version,
            "launcher_protocol": 4,
            "runtime_source": "image",
            "active_bundle": copy.deepcopy(active),
            "operation_busy": False,
            "rollback_available": True,
            "worker_runtime": copy.deepcopy(recovered["status_worker"]),
            "last_job": copy.deepcopy(successful_apply),
        }
        return {
            "status": "passed",
            "container_id": "a" * 64,
            "job_id": "b" * 32,
            "runtime_generation_id": generation,
            "initial_operational_state": initial,
            "selected_state_before_damage": _selected_operational_state(generation),
            "native_parser": parser,
            "damage": {
                "path": parser_path,
                "signed_sha256": parser["sha256"],
                "before_sha256": parser["sha256"],
                "damaged_sha256": "0" * 64,
            },
            "no_selected_launch_after_restart": {
                "successful_sample_count": 2,
                "sampling_error_count": 0,
                "restart_barrier_observed": True,
                "post_barrier_sample_count": 1,
                "forbidden_selected_pids": [],
            },
            "successful_apply_job": successful_apply,
            "recovery_hold_status": recovery_status,
            "recovery_marker": copy.deepcopy(
                recovery_control["official-recovery-mode.json"]
            ),
            "recovery_hold_operational_state": copy.deepcopy(recovered),
            "recovery_hold_control_state": recovery_control,
            "recovery_hold_diagnostic": {
                "file": "damaged-native-recovery-hold.json", "sha256": "6" * 64, "size": 1,
            },
            "rollback_response": {
                "job_id": "c" * 32, "operation": "rollback", "status": "restarting",
                "rolled_back_from": version,
            },
            "rollback_status": {
                "current_version": image_version,
                "launcher_protocol": 4,
                "active_bundle": None,
                "operation_busy": False,
                "worker_runtime": copy.deepcopy(recovered["status_worker"]),
                "last_job": copy.deepcopy(completed_rollback),
            },
            "completed_rollback_job": completed_rollback,
            "post_rollback_control_state": post_control,
            "post_rollback_diagnostic": {
                "file": "damaged-native-post-rollback.json", "sha256": "7" * 64, "size": 1,
            },
            "selected_processes_after_recovery": {},
            "post_rollback_operational_state": recovered,
            "image_imports_after_recovery": copy.deepcopy(
                image_imports if image_imports is not None else _image_imports()
            ),
            "image_inventory": inventory,
            "persistent_hashes": persistent,
        }
    reason = {
        "noexec": "Runtime storage does not permit execution.",
        "low_space": "Runtime storage does not have enough free space for this generation.",
    }[kind]
    job = {
        "status": "failed",
        "job_id": "b" * 32,
        "operation": "apply",
        "version": version,
        "bundle_sha256": bundle_sha256,
        "rollback_applied": False,
        "restart_required": False,
        "error": reason,
    }
    control = _control_state()
    required_bytes = required_bytes or 64 * 1024 * 1024 + 300
    mount = {
        "mount_type": "tmpfs",
        "mount_options": ["rw", "noexec" if kind == "noexec" else "exec"],
        "uid": 1000,
        "gid": 1000,
        "mode": "0755",
        "free_bytes": required_bytes + 1 if kind == "noexec" else 10,
    }
    return {
        "status": "passed",
        "container_id": "a" * 64,
        "initial_operational_state": initial,
        "job": job,
        "mount": mount,
        "required_bytes": required_bytes,
        "control_state_before": copy.deepcopy(control),
        "control_state_after": copy.deepcopy(control),
        "control_state": copy.deepcopy(control),
        "health_samples": [
            {"phase": phase, **copy.deepcopy(initial)}
            for phase in ("before", "during", "after")
        ],
        "operational_state_after": copy.deepcopy(initial),
        "rollback_status": {
            "current_version": image_version,
            "active_bundle": None,
            "operation_busy": False,
            "worker_runtime": copy.deepcopy(initial["status_worker"]),
            "last_job": copy.deepcopy(job),
        },
        "image_inventory": inventory,
        "persistent_hashes": persistent,
    }


def _image_imports() -> dict:
    root = "/venv/lib/python3.14/site-packages"
    parser = root + "/httptools/parser/parser.cpython-314-x86_64-linux-gnu.so"
    return {
        "sqlalchemy": {"path": root + "/sqlalchemy/__init__.py", "version": "2.0.54"},
        "httptools": {"path": root + "/httptools/__init__.py", "version": "0.8.0"},
        "httptools.parser.parser": {
            "path": parser,
            "version": "0.8.0",
            "sha256": hashlib.sha256(parser.encode()).hexdigest(),
        },
    }


def test_publication_requires_both_native_faults_and_amd64_live_checks():
    module = _module()
    module.validate_release_qualification("amd64", _publication_proof(), "1.5.4", "1.5.3")
    module.validate_release_qualification(
        "arm64", {"damaged_native_recovery": _probe_proof("1.5.3", damaged=True)}, "1.5.4", "1.5.3"
    )


@pytest.mark.parametrize("mutation", [
    lambda proof: proof.pop("damaged_native_recovery"),
    lambda proof: proof["damaged_native_recovery"].update(status="skipped"),
    lambda proof: proof["damaged_native_recovery"].update(runtime_generation_id="0" * 64),
    lambda proof: proof["damaged_native_recovery"]["native_parser"].update(sha256="0" * 64),
    lambda proof: proof["damaged_native_recovery"]["successful_apply_job"].update(status="failed"),
    lambda proof: proof["damaged_native_recovery"]["no_selected_launch_after_restart"].update(forbidden_selected_pids=[99]),
    lambda proof: proof["damaged_native_recovery"]["selected_state_before_damage"]["commands"].update(core=proof["damaged_native_recovery"]["selected_state_before_damage"]["commands"]["core"] + " --unexpected"),
    lambda proof: proof["damaged_native_recovery"]["selected_state_before_damage"]["processes"]["core"].update(ppid=999),
    lambda proof: proof["damaged_native_recovery"]["selected_state_before_damage"]["launchers"]["ui"].update(command="/venv/bin/python /tmp/evil --mode ui"),
    lambda proof: proof["damaged_native_recovery"]["recovery_hold_operational_state"]["services"]["ui"].update(pid=999),
    lambda proof: proof["damaged_native_recovery"]["post_rollback_operational_state"]["health"]["ready"].update(ready=False),
    lambda proof: proof["damaged_native_recovery"]["recovery_hold_status"]["worker_runtime"].update(python_executable="/tmp/python"),
    lambda proof: proof["damaged_native_recovery"]["recovery_hold_status"]["active_bundle"].update(runtime_generation_id="0" * 64),
    lambda proof: proof["damaged_native_recovery"]["recovery_hold_status"]["active_bundle"].update(version="1.5.5"),
    lambda proof: proof["damaged_native_recovery"]["recovery_hold_status"]["active_bundle"]["manifest"].update(bundle_sha256="0" * 64),
    lambda proof: proof["damaged_native_recovery"]["recovery_hold_status"]["active_bundle"].update(dependency_inventory_sha256="0" * 64),
    lambda proof: proof["damaged_native_recovery"]["recovery_marker"]["payload"].update(schema=True),
    lambda proof: proof["damaged_native_recovery"]["recovery_marker"]["payload"].update(mode="automatic"),
    lambda proof: proof["damaged_native_recovery"]["successful_apply_job"].update(job_id="0" * 32),
    lambda proof: proof["damaged_native_recovery"]["recovery_hold_status"].update(operation_busy=True),
    lambda proof: proof["damaged_native_recovery"]["rollback_response"].update(job_id="9" * 32),
    lambda proof: proof["damaged_native_recovery"]["completed_rollback_job"].update(job_id="8" * 32),
    lambda proof: proof["damaged_native_recovery"]["completed_rollback_job"]["startup_components"]["ui"].update(healthy=False),
    lambda proof: proof["damaged_native_recovery"]["post_rollback_control_state"].update(**{"official-recovery-mode.json": copy.deepcopy(proof["damaged_native_recovery"]["recovery_marker"])}),
    lambda proof: proof["damaged_native_recovery"].pop("image_imports_after_recovery"),
    lambda proof: proof["damaged_native_recovery"]["image_imports_after_recovery"]["sqlalchemy"].update(version="2.1.4"),
    lambda proof: proof.pop("filesystem_refusals"),
    lambda proof: proof["filesystem_refusals"].pop("noexec"),
    lambda proof: proof["filesystem_refusals"]["low_space"].update(status="unknown"),
    lambda proof: proof["filesystem_refusals"]["noexec"]["control_state_before"]["active.json"].update(sha256="0" * 64),
    lambda proof: proof["filesystem_refusals"]["noexec"]["control_state_after"].update(**{"restart-required.json": {"type": "file", "mode": "0600", "size": 2, "sha256": "0" * 64}}),
    lambda proof: proof["filesystem_refusals"]["noexec"]["health_samples"].pop(),
    lambda proof: proof["filesystem_refusals"]["low_space"]["health_samples"][1]["health"]["ready"].update(ready=False),
    lambda proof: proof["filesystem_refusals"]["noexec"]["job"].update(error="unrelated failure"),
    lambda proof: proof.pop("resource_observations"),
    lambda proof: proof["resource_observations"].update(sample_count=0),
    lambda proof: proof["resource_observations"].update(bundle_bytes=True),
    lambda proof: proof["resource_observations"].update(restart_duration_seconds=float("nan")),
    lambda proof: proof["resource_observations"].update(bundle_bytes=80 * 1024 * 1024 + 1),
    lambda proof: proof["resource_observations"].update(expanded_bytes=180 * 1024 * 1024 + 1),
    lambda proof: proof["resource_observations"].update(member_count=3001),
    lambda proof: proof["resource_observations"].update(free_bytes_before=1),
    lambda proof: proof.pop("live_ui"),
    lambda proof: proof["live_ui"].update(apply_requests=2),
    lambda proof: proof["live_ui"].update(target_version="1.5.5"),
    lambda proof: proof["live_ui"].update(final_version="1.5.4"),
    lambda proof: proof["live_ui"].update(observed_progress=False),
    lambda proof: proof["live_ui"].update(console_errors=["failed"]),
    lambda proof: proof["live_ui"].update(container_id_after="0" * 64),
    lambda proof: proof["live_ui"]["final_health"]["ready"].update(ready=False),
    lambda proof: proof["live_ui"]["final_operational_state"]["services"]["ui"].update(pid=999),
    lambda proof: proof["live_ui"]["screenshots"].pop("rollback"),
])
def test_publication_rejects_missing_skipped_or_invalid_release_checks(mutation):
    proof = _publication_proof()
    mutation(proof)
    with pytest.raises(ValueError):
        _module().validate_release_qualification("amd64", proof, "1.5.4", "1.5.3")


def _receipts(tmp_path):
    catalog, _keys, descriptors, inventories = _signed_catalog(tmp_path)
    aggregate = _evidence(catalog, descriptors, inventories)
    for name, architecture in aggregate["architectures"].items():
        descriptor = descriptors[name]
        architecture["release_qualification"] = _publication_proof(
            aggregate["target_application_version"],
            aggregate["bootstrap"]["minimum_image_version"],
            generation=descriptor["generation_id"],
            bundle_sha256=architecture["target_variant_bundle_sha256"],
            expanded_bytes=descriptor["expanded_size"],
            member_count=len(descriptor["files"]) + 2,
            parser_record=architecture["qualification"]["selected_native_imports"][
                "httptools.parser.parser"
            ],
            image_imports=architecture["qualification"]["image_imports_after_rollback"],
        ) if name == "amd64" else {
            "damaged_native_recovery": _probe_proof(
                aggregate["bootstrap"]["minimum_image_version"],
                damaged=True,
                version=aggregate["target_application_version"],
                generation=descriptor["generation_id"],
                bundle_sha256=architecture["target_variant_bundle_sha256"],
                parser_record=architecture["qualification"]["selected_native_imports"][
                    "httptools.parser.parser"
                ],
                image_imports=architecture["qualification"]["image_imports_after_rollback"],
            )
        }
    header = {key: value for key, value in aggregate.items() if key not in {"architectures", "native_logs_sha256"}}
    receipts = {
        name: {**copy.deepcopy(header), "architecture": copy.deepcopy(value), "cleanup": {"status": "passed", "failures": []}, "logs_sha256": aggregate["native_logs_sha256"][name], "serialized_size_bytes": 100}
        for name, value in aggregate["architectures"].items()
    }
    for name, receipt in receipts.items():
        _retain_probe_logs(tmp_path, name, receipt)
        aggregate["architectures"][name]["release_log_artifacts"] = receipt["release_log_artifacts"]
    return aggregate, receipts


def test_aggregate_retains_exact_native_receipts(tmp_path):
    expected, receipts = _receipts(tmp_path)
    assert _module().aggregate(receipts) == expected


@pytest.mark.parametrize("mutation", [
    lambda receipts: receipts.pop("arm64"),
    lambda receipts: receipts["arm64"].update(status="failed"),
    lambda receipts: receipts["arm64"].update(signed_catalog_sha256="0" * 64),
    lambda receipts: receipts["arm64"]["architecture"].update(architecture="amd64"),
    lambda receipts: receipts["amd64"].update(schema=1),
    lambda receipts: receipts["amd64"]["logs_sha256"].pop("feed"),
    lambda receipts: receipts["amd64"]["logs_sha256"].pop("app"),
    lambda receipts: receipts["amd64"]["logs_sha256"].update(extra="a" * 64),
    lambda receipts: receipts["arm64"].update(cleanup={"status": "failed", "failures": ["container"]}),
])
def test_aggregate_rejects_missing_failed_or_mismatched_proof(tmp_path, mutation):
    _expected, receipts = _receipts(tmp_path)
    mutation(receipts)
    with pytest.raises(ValueError):
        _module().aggregate(receipts)


@pytest.mark.parametrize("tamper_log", [False, True])
def test_cli_verifies_signed_catalog_and_actual_logs_before_publication(tmp_path, monkeypatch, tamper_log):
    from core import update_center

    catalog, keys, descriptors, inventories = _signed_catalog(tmp_path)
    expected = _evidence(catalog, descriptors, inventories)
    receipt_paths = {}
    for name in ("amd64", "arm64"):
        path = tmp_path / (name + ".json")
        architecture = expected["architectures"][name]
        descriptor = descriptors[name]
        architecture["release_qualification"] = _publication_proof(
            expected["target_application_version"],
            expected["bootstrap"]["minimum_image_version"],
            generation=descriptor["generation_id"],
            bundle_sha256=architecture["target_variant_bundle_sha256"],
            expanded_bytes=descriptor["expanded_size"],
            member_count=len(descriptor["files"]) + 2,
            parser_record=architecture["qualification"]["selected_native_imports"][
                "httptools.parser.parser"
            ],
            image_imports=architecture["qualification"]["image_imports_after_rollback"],
        ) if name == "amd64" else {
            "damaged_native_recovery": _probe_proof(
                expected["bootstrap"]["minimum_image_version"],
                damaged=True,
                version=expected["target_application_version"],
                generation=descriptor["generation_id"],
                bundle_sha256=architecture["target_variant_bundle_sha256"],
                parser_record=architecture["qualification"]["selected_native_imports"][
                    "httptools.parser.parser"
                ],
                image_imports=architecture["qualification"]["image_imports_after_rollback"],
            )
        }
        _retain_inventory_stages(tmp_path, name, architecture["qualification"])
        if name == "amd64":
            _retain_live_ui(tmp_path, architecture["release_qualification"]["live_ui"])
        hashes = {}
        for component in ("app", "feed"):
            data = (name + " " + component + " verification\n").encode()
            path.with_name(path.stem + "-" + component + ".log").write_bytes(data)
            hashes[component] = hashlib.sha256(data).hexdigest()
        receipt = {key: value for key, value in expected.items() if key not in {"architectures", "native_logs_sha256"}}
        receipt.update(architecture=expected["architectures"][name], logs_sha256=hashes,
                       cleanup={"status": "passed", "failures": []})
        _retain_probe_logs(tmp_path, name, receipt)
        expected["architectures"][name]["release_log_artifacts"] = receipt["release_log_artifacts"]
        _write_receipt(path, receipt)
        receipt_paths[name] = path
    if tamper_log:
        (tmp_path / "arm64-app.log").write_text("changed log\n")
    config = tmp_path / "release-config.json"
    config.write_text(json.dumps(_config()))
    output = tmp_path / "aggregate.json"
    monkeypatch.setattr(update_center, "UPDATE_PUBLIC_KEYS", keys)
    monkeypatch.setattr("sys.argv", ["aggregate-runtime-capability.py", "--amd64-evidence", str(receipt_paths["amd64"]),
                                    "--arm64-evidence", str(receipt_paths["arm64"]), "--catalog", str(catalog),
                                    "--release-config", str(config), "--source-commit", SOURCE_COMMIT, "--output", str(output)])
    if tamper_log:
        with pytest.raises(ValueError, match="log hash"):
            _module().main()
        assert not output.exists()
    else:
        assert _module().main() == 0
        actual = json.loads(output.read_text())
        assert actual["architectures"] == expected["architectures"]


def _retain(tmp_path, name, raw):
    (tmp_path / name).write_bytes(raw)
    return {"file": name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _write_receipt(path: Path, receipt: dict) -> None:
    damaged = receipt["architecture"]["release_qualification"][
        "damaged_native_recovery"
    ]
    _retain_recovery_diagnostics(path.parent, path.stem, damaged)
    receipt["serialized_size_bytes"] = 0
    for _ in range(4):
        encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        size = len(encoded.encode())
        if receipt["serialized_size_bytes"] == size:
            break
        receipt["serialized_size_bytes"] = size
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _retain_probe_logs(tmp_path, name, receipt):
    suffixes = {"damaged-native", "noexec", "low-space", "live-ui"} if name == "amd64" else {"damaged-native"}
    receipt["release_log_artifacts"] = {suffix: _retain(tmp_path, name + "-" + suffix + ".log", b"probe completed\n")
                                        for suffix in sorted(suffixes)}
    receipt["release_logs_sha256"] = {suffix: descriptor["sha256"]
                                     for suffix, descriptor in receipt["release_log_artifacts"].items()}


def _retain_recovery_diagnostics(tmp_path, name, damaged):
    for field, phase, status_field, control_field in (
        (
            "recovery_hold_diagnostic", "recovery-hold",
            "recovery_hold_status", "recovery_hold_control_state",
        ),
        (
            "post_rollback_diagnostic", "post-rollback",
            "rollback_status", "post_rollback_control_state",
        ),
    ):
        raw = (json.dumps({
            "schema": 1,
            "phase": phase,
            "status": damaged[status_field],
            "control": damaged[control_field],
        }, indent=2, sort_keys=True) + "\n").encode()
        damaged[field] = _retain(tmp_path, f"{name}-{phase}.json", raw)


@pytest.mark.parametrize(
    "field",
    ["recovery_hold_diagnostic", "post_rollback_diagnostic"],
)
def test_recovery_diagnostic_artifacts_are_reopened_and_bound(tmp_path, field):
    damaged = _probe_proof("1.5.3", damaged=True)
    _retain_recovery_diagnostics(tmp_path, "amd64", damaged)
    module = _module()
    module.verify_recovery_diagnostic_artifacts(tmp_path, damaged)

    (tmp_path / damaged[field]["file"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash|size"):
        module.verify_recovery_diagnostic_artifacts(tmp_path, damaged)


def _retain_inventory_stages(tmp_path, name, qualification):
    raw = json.dumps(qualification["image_inventory_before"], sort_keys=True).encode()
    qualification["image_inventory_artifacts"] = {
        stage: _retain(tmp_path, name + "-" + stage + ".json", raw)
        for stage in _module().IMAGE_STAGES
    }


def _retain_live_ui(tmp_path, ui):
    ui["screenshots"] = {
        name: _retain(tmp_path, name + ".png", b"\x89PNG\r\n\x1a\n" + name.encode())
        for name in ("selected", "rollback")
    }
    raw_ui = json.dumps(
        {
            key: value
            for key, value in ui.items()
            if key
            not in {
                "container_id_before",
                "container_id_after",
                "initial_operational_state",
                "final_operational_state",
                "evidence_json",
                "playwright_log",
            }
        }
    ).encode()
    ui["evidence_json"] = _retain(tmp_path, "runtime-update-live.json", raw_ui)
    ui["playwright_log"] = _retain(tmp_path, "runtime-update-live.log", b"1 passed\n")


@pytest.mark.parametrize("stage", ["before", "after_apply", "after_restart", "after_rollback", "after_reapply"])
def test_publication_reads_actual_full_inventory_at_every_stage(tmp_path, stage):
    expected, receipts = _receipts(tmp_path)
    qualification = receipts["amd64"]["architecture"]["qualification"]
    _retain_inventory_stages(tmp_path, "amd64", qualification)
    _module().verify_image_inventory_artifacts(tmp_path, qualification)
    descriptor = qualification["image_inventory_artifacts"][stage]
    changed = copy.deepcopy(qualification["image_inventory_before"])
    changed["entries"][0]["mode"] = "0777"
    raw = json.dumps(changed).encode()
    qualification["image_inventory_artifacts"][stage] = _retain(tmp_path, descriptor["file"], raw)
    with pytest.raises(ValueError, match="inventory changed"):
        _module().verify_image_inventory_artifacts(tmp_path, qualification)


@pytest.mark.parametrize(
    "name",
    ["../evidence.json", "/evidence.json", "a/b", "a\\b", "C:evidence.json", ".", ".."],
)
def test_retained_artifact_cannot_escape_evidence_directory(tmp_path, name):
    with pytest.raises(ValueError, match="path or digest"):
        _module().read_retained_artifact(tmp_path, {"file": name, "sha256": "a" * 64, "size": 1}, limit=100)


@pytest.mark.parametrize("mutation", [
    lambda descriptor: descriptor.update(size=True),
    lambda descriptor: descriptor.update(size=99),
    lambda descriptor: descriptor.update(sha256="b" * 64),
])
def test_retained_artifact_rejects_changed_hash_or_size(tmp_path, mutation):
    descriptor = _retain(tmp_path, "evidence.json", b"original")
    mutation(descriptor)
    with pytest.raises(ValueError, match="path or digest|hash or size"):
        _module().read_retained_artifact(tmp_path, descriptor, limit=100)


def test_retained_artifact_rejects_missing_and_symlinked_files(tmp_path):
    module = _module()
    descriptor = {"file": "missing.log", "sha256": "a" * 64, "size": 1}
    with pytest.raises(ValueError, match="missing"):
        module.read_retained_artifact(tmp_path, descriptor, limit=100)
    target = tmp_path / "target.log"
    target.write_bytes(b"x")
    link = tmp_path / "linked.log"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink support is required")
    descriptor = {
        "file": link.name,
        "sha256": hashlib.sha256(b"x").hexdigest(),
        "size": 1,
    }
    with pytest.raises(ValueError, match="missing"):
        module.read_retained_artifact(tmp_path, descriptor, limit=100)


def test_bounded_json_input_and_final_serialization_fail_before_write(tmp_path):
    module = _module()
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"{" + b" " * module.MAX_NATIVE_RECEIPT_BYTES + b"}")
    with pytest.raises(ValueError, match="input exceeds"):
        module.read_json_bounded(receipt, limit=module.MAX_NATIVE_RECEIPT_BYTES)

    output = tmp_path / "aggregate.json"
    with pytest.raises(ValueError, match="8 MiB"):
        module.serialize_aggregate({"blob": "x" * module.MAX_AGGREGATE_BYTES})
    assert not output.exists()


@pytest.mark.parametrize("reader", ["artifact", "json"])
def test_bounded_read_rejects_file_growth_during_read(tmp_path, monkeypatch, reader):
    module = _module()
    path = tmp_path / "evidence.json"
    raw = b'{"status":"passed"}'
    path.write_bytes(raw)
    original_fstat = module.os.fstat
    calls = 0

    def changed_fstat(fd):
        nonlocal calls
        calls += 1
        result = original_fstat(fd)
        if calls == 2:
            fields = list(result)
            fields[6] += 1
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(module.os, "fstat", changed_fstat)
    with pytest.raises(ValueError, match="changed while it was read"):
        if reader == "artifact":
            module.read_retained_artifact(
                tmp_path,
                {
                    "file": path.name,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                },
                limit=100,
            )
        else:
            module.read_json_bounded(path, limit=100)


@pytest.mark.parametrize("artifact", ["evidence_json", "playwright_log", "selected", "rollback"])
def test_live_ui_actual_artifacts_fail_closed(tmp_path, monkeypatch, artifact):
    from core import update_center

    catalog, keys, descriptors, inventories = _signed_catalog(tmp_path)
    expected = _evidence(catalog, descriptors, inventories)
    receipt_paths = {}
    for name in ("amd64", "arm64"):
        architecture = expected["architectures"][name]
        descriptor = descriptors[name]
        architecture["release_qualification"] = _publication_proof(
            expected["target_application_version"],
            expected["bootstrap"]["minimum_image_version"],
            generation=descriptor["generation_id"],
            bundle_sha256=architecture["target_variant_bundle_sha256"],
            expanded_bytes=descriptor["expanded_size"],
            member_count=len(descriptor["files"]) + 2,
            parser_record=architecture["qualification"]["selected_native_imports"][
                "httptools.parser.parser"
            ],
            image_imports=architecture["qualification"]["image_imports_after_rollback"],
        ) if name == "amd64" else {
            "damaged_native_recovery": _probe_proof(
                expected["bootstrap"]["minimum_image_version"],
                damaged=True,
                version=expected["target_application_version"],
                generation=descriptor["generation_id"],
                bundle_sha256=architecture["target_variant_bundle_sha256"],
                parser_record=architecture["qualification"]["selected_native_imports"][
                    "httptools.parser.parser"
                ],
                image_imports=architecture["qualification"]["image_imports_after_rollback"],
            )
        }
        _retain_inventory_stages(tmp_path, name, architecture["qualification"])
        if name == "amd64":
            _retain_live_ui(tmp_path, architecture["release_qualification"]["live_ui"])
        path = tmp_path / f"{name}.json"
        hashes = {}
        for component in ("app", "feed"):
            raw = f"{name}-{component}\n".encode()
            path.with_name(f"{name}-{component}.log").write_bytes(raw)
            hashes[component] = hashlib.sha256(raw).hexdigest()
        receipt = {
            key: value
            for key, value in expected.items()
            if key not in {"architectures", "native_logs_sha256"}
        }
        receipt.update(
            architecture=architecture,
            logs_sha256=hashes,
            cleanup={"status": "passed", "failures": []},
        )
        _retain_probe_logs(tmp_path, name, receipt)
        _write_receipt(path, receipt)
        receipt_paths[name] = path
    ui = expected["architectures"]["amd64"]["release_qualification"]["live_ui"]
    descriptor = ui["screenshots"][artifact] if artifact in {"selected", "rollback"} else ui[artifact]
    (tmp_path / descriptor["file"]).write_bytes(b"tampered")
    config = tmp_path / "release-config.json"
    config.write_text(json.dumps(_config()), encoding="utf-8")
    output = tmp_path / "aggregate.json"
    monkeypatch.setattr(update_center, "UPDATE_PUBLIC_KEYS", keys)
    monkeypatch.setattr(
        "sys.argv",
        [
            "aggregate-runtime-capability.py",
            "--amd64-evidence",
            str(receipt_paths["amd64"]),
            "--arm64-evidence",
            str(receipt_paths["arm64"]),
            "--catalog",
            str(catalog),
            "--release-config",
            str(config),
            "--source-commit",
            SOURCE_COMMIT,
            "--output",
            str(output),
        ],
    )
    with pytest.raises(ValueError, match="hash|size"):
        _module().main()
    assert not output.exists()
