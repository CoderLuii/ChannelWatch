#!/usr/bin/env python3
"""Bind two native qualification receipts to the signed release catalog."""
from __future__ import annotations

import argparse
import datetime as datetime_module
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile


HEADER_FIELDS = (
    "schema", "status", "target_application_version", "source_commit",
    "signed_catalog_sha256", "bootstrap",
)

IMAGE_STAGES = ("before", "after_apply", "after_restart", "after_rollback", "after_reapply")
HEX_RE = re.compile(r"[0-9a-f]{64}")
MAX_NATIVE_RECEIPT_BYTES = 7 * 1024 * 1024 // 2
MAX_AGGREGATE_BYTES = 8 * 1024 * 1024
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_INVENTORY_ARTIFACT_BYTES = MAX_AGGREGATE_BYTES
MAX_UPDATE_CHECK_ARTIFACT_BYTES = 8 * 1024 * 1024
UPDATE_CHECK_EVIDENCE_MODE = "protocol-4-runtime"
UPDATE_CHECK_EVIDENCE_PHASE = "initial-update-check"
MAX_ROLLBACK_STATUS_ARTIFACT_BYTES = 8 * 1024 * 1024
ROLLBACK_STATUS_EVIDENCE_PHASE = "qualification-post-rollback"
# Four individually bounded control files plus status and JSON envelope.
MAX_RECOVERY_DIAGNOSTIC_BYTES = 8 * 1024 * 1024
MAX_RECOVERY_CONTROL_BYTES = 1024 * 1024
RECOVERY_DIAGNOSTIC_VALUE_FIELDS = {
    "recovery_hold_status",
    "recovery_hold_control_state",
    "rollback_status",
    "post_rollback_control_state",
}
MAX_BUNDLE_BYTES = 80 * 1024 * 1024
MAX_EXPANDED_BYTES = 180 * 1024 * 1024
MAX_MEMBER_COUNT = 3000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
PREPARATION_HEADROOM_BYTES = 64 * 1024 * 1024
CONTROL_FILES = {
    "active.json", "rollback.json", "restart-required.json",
    "restart-required.lock", "restart-services-accepted.json",
    "activation-pending.json", "activation-core-ready.json",
    "activation-ui-ready.json",
}
TRANSIENT_CONTROL_FILES = {
    "restart-required.json", "restart-services-accepted.json",
    "activation-pending.json", "activation-core-ready.json",
    "activation-ui-ready.json",
}
TRACKED_MODULES = {
    "requests", "cryptography", "sqlmodel", "uvicorn", "pydantic_core",
    "sqlalchemy", "httptools",
}


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and HEX_RE.fullmatch(value) is not None


def _image_command_valid(command: object, component: str) -> bool:
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    expected = ["/venv/bin/python", "-u", "/app/core/runtime_launcher.py", component]
    expected.extend(
        ["--stay-alive"]
        if component == "core"
        else ["--host", "0.0.0.0", "--port", "8501", "--log-level", "warning"]
    )
    return tokens == expected


def _selected_command_valid(
    command: object, component: str, generation: str
) -> bool:
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    root = f"/config/channelwatch-runtime/releases/{generation}"
    expected = [
        root + "/environment/bin/python", "-I", "-B", "-S",
        "/app/core/runtime_worker.py", "--image-app-dir", "/app",
        "--mode", component, "--generation", root, "--activation-id",
    ]
    if tokens[: len(expected)] != expected or len(tokens) <= len(expected):
        return False
    activation_id = tokens[len(expected)]
    suffix = ["--descriptor-sha256", generation]
    if component == "ui":
        suffix.extend(
            ["--host", "0.0.0.0", "--port", "8501", "--log-level", "warning"]
        )
    suffix.extend(["--", *(["--stay-alive"] if component == "core" else [])])
    return (
        re.fullmatch(r"[0-9a-f]{32}", activation_id) is not None
        and tokens[len(expected) + 1 :] == suffix
    )


def _worker_runtime_valid(
    value: object,
    *,
    selected_generation: str | None,
    require_verified_generation: bool = True,
) -> bool:
    if not isinstance(value, dict):
        return False
    if selected_generation is None:
        return (
            set(value)
            == {"python_executable", "python_version", "verified_generation"}
            and value.get("python_executable") == "/venv/bin/python"
            and isinstance(value.get("python_version"), str)
            and bool(value["python_version"])
            and value.get("verified_generation") is None
        )
    expected_fields = {
        "pid", "python_executable", "python_prefix", "python_version", "dependencies",
    }
    if require_verified_generation:
        expected_fields.add("verified_generation")
    if set(value) != expected_fields:
        return False
    prefix = (
        f"/config/channelwatch-runtime/releases/{selected_generation}/environment"
    )
    if (
        type(value.get("pid")) is not int or value["pid"] <= 0
        or value.get("python_executable") != prefix + "/bin/python"
        or value.get("python_prefix") != prefix
        or not isinstance(value.get("python_version"), str)
        or not value["python_version"]
    ):
        return False
    if require_verified_generation:
        verified = value.get("verified_generation")
        if (
            not isinstance(verified, dict)
            or verified.get("runtime_generation_id") != selected_generation
            or verified.get("runtime_descriptor_sha256") != selected_generation
            or verified.get("path")
            != f"/config/channelwatch-runtime/releases/{selected_generation}"
            or not _valid_sha256(verified.get("dependency_inventory_sha256"))
        ):
            return False
    dependencies = value.get("dependencies")
    if not isinstance(dependencies, dict) or set(dependencies) != TRACKED_MODULES:
        return False
    for module, record in dependencies.items():
        if not isinstance(record, dict) or record.get("loaded") not in {True, False}:
            return False
        if record["loaded"] is False:
            if set(record) != {"loaded"}:
                return False
            continue
        if (
            set(record) != {"loaded", "path", "version"}
            or not isinstance(record.get("path"), str)
            or not record["path"].startswith(
                prefix.removesuffix("/environment")
                + "/dependencies/site-packages/"
            )
            or not isinstance(record.get("version"), str)
            or not record["version"]
        ):
            return False
    return dependencies["sqlalchemy"].get("loaded") is True


def _operational_state_valid(value: object, *, selected_generation: str | None = None) -> bool:
    expected_fields = {
        "status_worker_scope", "status_worker", "commands", "processes", "services", "health",
    }
    if selected_generation is not None:
        expected_fields.update({"runtime_components", "launchers"})
    if not isinstance(value, dict) or set(value) != expected_fields:
        return False
    expected_scope = (
        "ui_status_handler_only"
        if selected_generation is not None
        else "frozen_image_ui_status_interpreter_only"
    )
    if value.get("status_worker_scope") != expected_scope:
        return False
    commands, processes, services = (
        value.get("commands"), value.get("processes"), value.get("services")
    )
    if any(not isinstance(item, dict) or set(item) != {"core", "ui"}
           for item in (commands, processes, services)):
        return False
    worker_pids: set[int] = set()
    launcher_pids: set[int] = set()
    launchers = value.get("launchers") if selected_generation is not None else None
    if selected_generation is not None and (
        not isinstance(launchers, dict) or set(launchers) != {"core", "ui"}
    ):
        return False
    for component in ("core", "ui"):
        process, service, command = processes[component], services[component], commands[component]
        expected_process_fields = (
            {"pid", "ppid", "command"}
            if selected_generation is not None
            else {"pid", "command"}
        )
        if (
            not isinstance(process, dict) or set(process) != expected_process_fields
            or type(process.get("pid")) is not int or process["pid"] <= 0
            or process.get("command") != command
            or not isinstance(service, dict) or set(service) != {"status", "pid", "uptime"}
            or service.get("status") != "RUNNING"
            or not isinstance(service.get("uptime"), str) or not service["uptime"].strip()
        ):
            return False
        if selected_generation is None:
            if (
                service.get("pid") != process["pid"]
                or not _image_command_valid(command, component)
            ):
                return False
        else:
            launcher = launchers[component]
            if (
                not _selected_command_valid(command, component, selected_generation)
                or type(process.get("ppid")) is not int
                or not isinstance(launcher, dict)
                or set(launcher) != {"pid", "command"}
                or type(launcher.get("pid")) is not int
                or launcher["pid"] <= 0
                or not _image_command_valid(launcher.get("command"), component)
                or process["ppid"] != launcher["pid"]
                or service.get("pid") != launcher["pid"]
            ):
                return False
            launcher_pids.add(launcher["pid"])
        worker_pids.add(process["pid"])
    if (
        len(worker_pids) != 2
        or (
            selected_generation is not None
            and (len(launcher_pids) != 2 or not worker_pids.isdisjoint(launcher_pids))
        )
        or value.get("health") != {
            "startup": {"status": "ready"},
            "ready": {"status": "ready", "ready": True},
        }
    ):
        return False
    worker = value.get("status_worker")
    if not _worker_runtime_valid(worker, selected_generation=selected_generation):
        return False
    if selected_generation is not None and worker["pid"] != processes["ui"]["pid"]:
        return False
    if selected_generation is not None:
        components = value.get("runtime_components")
        if not isinstance(components, dict) or set(components) != {"core", "ui"}:
            return False
        selected_inventory = worker["verified_generation"][
            "dependency_inventory_sha256"
        ]
        for component, record in components.items():
            if (
                not isinstance(record, dict)
                or set(record)
                != {
                    "runtime_generation_id",
                    "runtime_descriptor_sha256",
                    "dependency_inventory_sha256",
                    "process_runtime",
                }
                or record.get("runtime_generation_id") != selected_generation
                or record.get("runtime_descriptor_sha256") != selected_generation
                or record.get("dependency_inventory_sha256") != selected_inventory
                or not _worker_runtime_valid(
                    record.get("process_runtime"),
                    selected_generation=selected_generation,
                    require_verified_generation=False,
                )
                or record["process_runtime"]["pid"] != processes[component]["pid"]
            ):
                return False
    return True


def _preservation_valid(result: dict) -> None:
    inventory = result.get("image_inventory")
    persistent = result.get("persistent_hashes")
    if (
        not isinstance(inventory, dict)
        or set(inventory) != {"sha256", "path_count", "regular_file_bytes"}
        or not _valid_sha256(inventory.get("sha256"))
        or type(inventory.get("path_count")) is not int or inventory["path_count"] <= 0
        or type(inventory.get("regular_file_bytes")) is not int
        or inventory["regular_file_bytes"] < 0
        or not isinstance(persistent, dict)
        or set(persistent) != {"/config/settings.json", "/config/encryption.key"}
        or any(not _valid_sha256(item) for item in persistent.values())
    ):
        raise ValueError("A live probe preservation evidence is missing or invalid.")
    if not _valid_sha256(result.get("container_id")):
        raise ValueError("A live probe container identity is invalid.")


def _rollback_valid(value: object, *, image_version: str, operational_state: dict) -> bool:
    return (
        isinstance(value, dict)
        and value.get("current_version") == image_version
        and value.get("active_bundle", True) is None
        and value.get("operation_busy") is False
        and value.get("worker_runtime") == operational_state.get("status_worker")
    )


def _control_state_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != CONTROL_FILES:
        return False
    for name, record in value.items():
        if record == {"type": "absent"}:
            continue
        if (
            not isinstance(record, dict)
            or set(record) != {"type", "mode", "size", "sha256"}
            or record.get("type") != "file"
            or not isinstance(record.get("mode"), str)
            or re.fullmatch(r"0[0-7]{3}", record["mode"]) is None
            or type(record.get("size")) is not int or record["size"] < 0
            or not _valid_sha256(record.get("sha256"))
        ):
            return False
    return all(value[name] == {"type": "absent"} for name in TRANSIENT_CONTROL_FILES)


def _recovery_control_record_valid(value: object) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == {"type", "mode", "size", "sha256", "payload"}
        and value.get("type") == "file"
        and isinstance(value.get("mode"), str)
        and re.fullmatch(r"0[0-7]{3}", value["mode"]) is not None
        and type(value.get("size")) is int
        and 0 < value["size"] <= MAX_RECOVERY_CONTROL_BYTES
        and _valid_sha256(value.get("sha256"))
        and isinstance(value.get("payload"), dict)
    )


def _recovery_marker_valid(
    value: object, *, version: str, bundle_sha256: str
) -> bool:
    payload = value.get("payload") if isinstance(value, dict) else None
    try:
        entered_at = datetime_module.datetime.fromisoformat(
            str(payload.get("entered_at")).replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        _recovery_control_record_valid(value)
        and set(payload) == {
            "schema", "mode", "failed_version", "failed_bundle_sha256", "entered_at"
        }
        and type(payload.get("schema")) is int
        and payload.get("schema") == 1
        and payload.get("mode") == "official_signed_only"
        and str(payload.get("failed_version") or "").lstrip("v") == version
        and payload.get("failed_bundle_sha256") == bundle_sha256
        and entered_at.tzinfo is not None
    )


def _rollback_startup_components_valid(value: object, *, image_version: str) -> bool:
    if not isinstance(value, dict) or set(value) != {"core", "ui"}:
        return False
    for record in value.values():
        try:
            ready_at = datetime_module.datetime.fromisoformat(
                str(record.get("ready_at")).replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError):
            return False
        if (
            not isinstance(record, dict)
            or set(record) != {"healthy", "version", "ready_at"}
            or record.get("healthy") is not True
            or str(record.get("version") or "").lstrip("v") != image_version
            or ready_at.tzinfo is None
        ):
            return False
    return True


def _rollback_response_valid(
    value: object, *, source_version: str, target_version: str
) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("operation") != "rollback"
        or value.get("status") not in {"restarting", "validating", "success"}
        or value.get("version") != source_version
        or value.get("rolled_back_from") != source_version
        or not isinstance(value.get("job_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", value["job_id"]) is None
    ):
        return False
    status = value["status"]
    if status == "restarting":
        return value.get("restart_required") is True
    components = value.get("startup_components")
    component_names = set(components) if isinstance(components, dict) else set()
    return bool(
        value.get("rollback_applied") is True
        and value.get("restored_version") == target_version
        and isinstance(components, dict)
        and (
            component_names == {"core", "ui"}
            if status == "success"
            else len(component_names) == 1
            and component_names.issubset({"core", "ui"})
        )
        and all(
            isinstance(marker, dict)
            and set(marker) == {"healthy", "version", "ready_at"}
            and marker.get("healthy") is True
            and marker.get("version") == target_version
            and isinstance(marker.get("ready_at"), str)
            and bool(marker["ready_at"])
            for marker in components.values()
        )
        and (
            value.get("restart_required") is True
            if status == "validating"
            else value.get("restart_required") is False
            and isinstance(value.get("validated_at"), str)
            and bool(value["validated_at"])
        )
    )


def _validate_damaged_recovery(
    result: object,
    *,
    version: str,
    image_version: str,
    expected_generation: str | None,
    expected_bundle_sha256: str | None,
    expected_parser: dict | None,
    expected_image_imports: dict | None,
    recovery_diagnostics: dict | None = None,
) -> None:
    required = {
        "status", "job_id", "runtime_generation_id", "container_id",
        "initial_operational_state", "selected_state_before_damage", "native_parser",
        "damage", "no_selected_launch_after_restart", "successful_apply_job",
        "recovery_marker", "recovery_hold_operational_state",
        "recovery_hold_diagnostic", "rollback_response",
        "completed_rollback_job",
        "post_rollback_diagnostic", "selected_processes_after_recovery",
        "post_rollback_operational_state", "image_imports_after_recovery",
        "image_inventory", "persistent_hashes",
    }
    if not isinstance(result, dict) or result.get("status") != "passed":
        raise ValueError("Damaged-native recovery evidence is incomplete.")
    embedded_fields = set(result) & RECOVERY_DIAGNOSTIC_VALUE_FIELDS
    if embedded_fields not in (set(), RECOVERY_DIAGNOSTIC_VALUE_FIELDS):
        raise ValueError("Damaged-native recovery diagnostic evidence is incomplete.")
    if set(result) != required | embedded_fields:
        raise ValueError("Damaged-native recovery evidence is incomplete.")
    if embedded_fields:
        diagnostic_values = {
            field: result[field] for field in RECOVERY_DIAGNOSTIC_VALUE_FIELDS
        }
        if recovery_diagnostics is not None and diagnostic_values != recovery_diagnostics:
            raise ValueError("Recovery diagnostic artifact differs from its receipt.")
    else:
        if (
            not isinstance(recovery_diagnostics, dict)
            or set(recovery_diagnostics) != RECOVERY_DIAGNOSTIC_VALUE_FIELDS
        ):
            raise ValueError("Damaged-native recovery diagnostic evidence is incomplete.")
        diagnostic_values = recovery_diagnostics
    generation = result.get("runtime_generation_id")
    if not _valid_sha256(generation) or (
        expected_generation is not None and generation != expected_generation
    ):
        raise ValueError("Damaged-native recovery generation identity differs.")
    if not _operational_state_valid(result.get("initial_operational_state")):
        raise ValueError("Damaged-native initial image process evidence is invalid.")
    selected_state = result.get("selected_state_before_damage")
    if not _operational_state_valid(selected_state, selected_generation=generation):
        raise ValueError("Damaged-native selected process evidence is invalid.")
    parser = result.get("native_parser")
    if (
        not isinstance(parser, dict) or set(parser) != {"path", "version", "sha256"}
        or not isinstance(parser.get("path"), str)
        or not parser["path"].startswith(
            f"/config/channelwatch-runtime/releases/{generation}/dependencies/site-packages/httptools/parser/parser"
        )
        or not parser["path"].endswith(".so")
        or not isinstance(parser.get("version"), str) or not parser["version"]
        or not _valid_sha256(parser.get("sha256"))
        or expected_parser is not None and parser != expected_parser
    ):
        raise ValueError("Damaged-native parser identity is not signed.")
    damage = result.get("damage")
    if (
        not isinstance(damage, dict)
        or set(damage) != {"path", "signed_sha256", "before_sha256", "damaged_sha256"}
        or damage.get("path") != parser["path"]
        or damage.get("signed_sha256") != parser["sha256"]
        or damage.get("before_sha256") != parser["sha256"]
        or not _valid_sha256(damage.get("damaged_sha256"))
        or damage["damaged_sha256"] == parser["sha256"]
    ):
        raise ValueError("Damaged-native mutation is not bound to the signed parser.")
    monitor = result.get("no_selected_launch_after_restart")
    if (
        not isinstance(monitor, dict)
        or set(monitor) != {
            "successful_sample_count", "sampling_error_count",
            "restart_barrier_observed", "post_barrier_sample_count",
            "forbidden_selected_pids",
        }
        or type(monitor.get("successful_sample_count")) is not int
        or monitor["successful_sample_count"] <= 0
        or type(monitor.get("sampling_error_count")) is not int
        or monitor["sampling_error_count"] < 0
        or monitor.get("restart_barrier_observed") is not True
        or type(monitor.get("post_barrier_sample_count")) is not int
        or monitor["post_barrier_sample_count"] <= 0
        or monitor.get("forbidden_selected_pids") != []
    ):
        raise ValueError("Damaged generation was not rejected before execution.")
    apply_job = result.get("successful_apply_job")
    bundle_sha256 = (
        expected_bundle_sha256
        if expected_bundle_sha256 is not None
        else apply_job.get("bundle_sha256") if isinstance(apply_job, dict) else None
    )
    if (
        not isinstance(apply_job, dict)
        or apply_job.get("job_id") != result.get("job_id")
        or apply_job.get("operation") != "apply"
        or apply_job.get("status") != "success"
        or str(apply_job.get("version") or "").lstrip("v") != version
        or not _valid_sha256(bundle_sha256)
        or apply_job.get("bundle_sha256") != bundle_sha256
    ):
        raise ValueError("Damaged-native apply job is not the retained successful activation.")
    generation_root = f"/config/channelwatch-runtime/releases/{generation}"
    hold_status = diagnostic_values.get("recovery_hold_status")
    active = hold_status.get("active_bundle") if isinstance(hold_status, dict) else None
    manifest = active.get("manifest") if isinstance(active, dict) else None
    hold_state = result.get("recovery_hold_operational_state")
    if (
        not isinstance(hold_status, dict)
        or hold_status.get("current_version") != image_version
        or hold_status.get("image_version") != image_version
        or hold_status.get("launcher_protocol") != 4
        or hold_status.get("runtime_source") != "image"
        or hold_status.get("operation_busy") is not False
        or hold_status.get("rollback_available") is not True
        or hold_status.get("last_job") != apply_job
        or not isinstance(active, dict)
        or str(active.get("version") or "").lstrip("v") != version
        or active.get("path") != generation_root
        or active.get("runtime_generation_id") != generation
        or active.get("runtime_descriptor_sha256") != generation
        or not _valid_sha256(active.get("dependency_inventory_sha256"))
        or active.get("dependency_inventory_sha256")
        != selected_state["status_worker"]["verified_generation"][
            "dependency_inventory_sha256"
        ]
        or any(
            component["dependency_inventory_sha256"]
            != active.get("dependency_inventory_sha256")
            for component in selected_state["runtime_components"].values()
        )
        or not isinstance(manifest, dict)
        or manifest.get("bundle_sha256") != bundle_sha256
        or not _operational_state_valid(hold_state)
        or hold_status.get("worker_runtime") != hold_state.get("status_worker")
    ):
        raise ValueError("Damaged-native recovery hold is not bound to the healthy image workers.")
    marker = result.get("recovery_marker")
    hold_control = diagnostic_values.get("recovery_hold_control_state")
    control_names = {
        "active.json", "rollback.json", "update-job.json",
        "official-recovery-mode.json",
    }
    if (
        not _recovery_marker_valid(marker, version=version, bundle_sha256=bundle_sha256)
        or not isinstance(hold_control, dict)
        or set(hold_control) != control_names
        or any(not _recovery_control_record_valid(value) for value in hold_control.values())
        or hold_control["active.json"].get("payload") != active
        or hold_control["update-job.json"].get("payload") != apply_job
        or hold_control["official-recovery-mode.json"] != marker
    ):
        raise ValueError("Damaged-native recovery hold control evidence is invalid.")
    rollback_response = result.get("rollback_response")
    completed = result.get("completed_rollback_job")
    if (
        not _rollback_response_valid(
            rollback_response,
            source_version=version,
            target_version=image_version,
        )
        or rollback_response["job_id"] == apply_job.get("job_id")
        or not isinstance(completed, dict)
        or completed.get("job_id") != rollback_response["job_id"]
        or completed.get("operation") != "rollback"
        or completed.get("status") != "success"
        or str(completed.get("version") or "").lstrip("v") != version
        or str(completed.get("rolled_back_from") or "").lstrip("v") != version
        or str(completed.get("restored_version") or "").lstrip("v") != image_version
        or completed.get("rollback_applied") is not True
        or completed.get("restart_required") is not False
        or not _rollback_startup_components_valid(
            completed.get("startup_components"), image_version=image_version
        )
    ):
        raise ValueError("Damaged-native explicit rollback job is invalid.")
    post_state = result.get("post_rollback_operational_state")
    rollback_status = diagnostic_values.get("rollback_status")
    post_control = diagnostic_values.get("post_rollback_control_state")
    if (
        not _operational_state_valid(post_state)
        or result.get("selected_processes_after_recovery") != {}
        or not _rollback_valid(
            rollback_status,
            image_version=image_version,
            operational_state=post_state,
        )
        or rollback_status.get("launcher_protocol") != 4
        or rollback_status.get("last_job") != completed
        or not isinstance(post_control, dict)
        or set(post_control) != control_names
        or post_control.get("active.json") != {"type": "absent"}
        or post_control.get("official-recovery-mode.json") != {"type": "absent"}
        or not _recovery_control_record_valid(post_control.get("rollback.json"))
        or not _recovery_control_record_valid(post_control.get("update-job.json"))
        or post_control["update-job.json"].get("payload") != completed
    ):
        raise ValueError("Damaged-native post-rollback image evidence is invalid.")
    for name in ("recovery_hold_diagnostic", "post_rollback_diagnostic"):
        _validate_artifact_descriptor(result.get(name))
    recovered_imports = result.get("image_imports_after_recovery")
    if not _image_imports_valid(recovered_imports) or (
        expected_image_imports is not None
        and recovered_imports != expected_image_imports
    ):
        raise ValueError("Damaged-native recovery image import proof is invalid.")
    _preservation_valid(result)


def _image_imports_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "sqlalchemy", "httptools", "httptools.parser.parser"
    }:
        return False
    for module, version in (("sqlalchemy", "2.0.54"), ("httptools", "0.8.0")):
        record = value[module]
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "version"}
            or record.get("version") != version
            or not isinstance(record.get("path"), str)
            or not record["path"].startswith(
                f"/venv/lib/python3.14/site-packages/{module}/"
            )
        ):
            return False
    parser = value["httptools.parser.parser"]
    return (
        isinstance(parser, dict)
        and set(parser) == {"path", "version", "sha256"}
        and parser.get("version") == "0.8.0"
        and isinstance(parser.get("path"), str)
        and parser["path"].startswith(
            "/venv/lib/python3.14/site-packages/httptools/parser/parser"
        )
        and parser["path"].endswith(".so")
        and parser["path"] != value["httptools"]["path"]
        and _valid_sha256(parser.get("sha256"))
    )


def _validate_filesystem_refusal(
    kind: str,
    result: object,
    *,
    version: str,
    image_version: str,
    expected_bundle_sha256: str | None,
    expected_required_bytes: int | None,
) -> None:
    required_fields = {
        "status", "container_id", "initial_operational_state", "job", "mount",
        "required_bytes", "control_state_before", "control_state_after",
        "control_state", "health_samples", "operational_state_after",
        "rollback_status", "image_inventory", "persistent_hashes",
    }
    if not isinstance(result, dict) or set(result) != required_fields or result.get("status") != "passed":
        raise ValueError(f"{kind} filesystem refusal evidence is incomplete.")
    initial, after = result.get("initial_operational_state"), result.get("operational_state_after")
    if not _operational_state_valid(initial) or not _operational_state_valid(after):
        raise ValueError("Filesystem refusal image process evidence is invalid.")
    job = result.get("job")
    reason = {
        "noexec": "Runtime storage does not permit execution.",
        "low_space": "Runtime storage does not have enough free space for this generation.",
    }[kind]
    if (
        not isinstance(job, dict) or job.get("status") != "failed"
        or job.get("operation") != "apply"
        or str(job.get("version") or "").lstrip("v") != version
        or expected_bundle_sha256 is not None
        and job.get("bundle_sha256") != expected_bundle_sha256
        or job.get("rollback_applied") is not False
        or job.get("restart_required") is not False
        or reason not in str(job.get("error") or "")
    ):
        raise ValueError(f"{kind} refusal is not bound to its exact pre-handoff failure.")
    mount, required_bytes = result.get("mount"), result.get("required_bytes")
    if (
        not isinstance(mount, dict)
        or set(mount) != {"mount_type", "mount_options", "uid", "gid", "mode", "free_bytes"}
        or mount.get("mount_type") != "tmpfs"
        or not isinstance(mount.get("mount_options"), list)
        or type(mount.get("uid")) is not int or type(mount.get("gid")) is not int
        or not isinstance(mount.get("mode"), str)
        or re.fullmatch(r"0[0-7]{3}", mount["mode"]) is None
        or type(mount.get("free_bytes")) is not int or mount["free_bytes"] < 0
        or type(required_bytes) is not int or required_bytes <= 0
        or expected_required_bytes is not None and required_bytes != expected_required_bytes
        or kind == "noexec" and "noexec" not in mount["mount_options"]
        or kind == "low_space" and mount["free_bytes"] >= required_bytes
    ):
        raise ValueError("A real filesystem refusal mount does not prove its refusal condition.")
    before, after_control = result.get("control_state_before"), result.get("control_state_after")
    if (
        not _control_state_valid(before)
        or not _control_state_valid(after_control)
        or before != after_control
        or result.get("control_state") != after_control
    ):
        raise ValueError("Filesystem refusal changed selection or handoff control state.")
    samples = result.get("health_samples")
    if not isinstance(samples, list) or [sample.get("phase") for sample in samples if isinstance(sample, dict)] != ["before", "during", "after"]:
        raise ValueError("Filesystem refusal health samples are incomplete.")
    sample_states = []
    for sample in samples:
        state = {key: value for key, value in sample.items() if key != "phase"}
        if not _operational_state_valid(state):
            raise ValueError("Filesystem refusal health sample is invalid.")
        sample_states.append(state)
    if sample_states[0] != initial or sample_states[-1] != after:
        raise ValueError("Filesystem refusal health phases do not bind initial and final workers.")
    if not _rollback_valid(
        result.get("rollback_status"), image_version=image_version, operational_state=after
    ):
        raise ValueError("Filesystem refusal did not preserve the current image runtime.")
    _preservation_valid(result)


def _validate_resources(value: object, *, descriptor: dict | None) -> None:
    integer_fields = {
        "bundle_bytes", "expanded_bytes", "member_count", "selected_generation_disk_bytes",
        "free_bytes_before", "free_bytes_after", "sample_count", "peak_container_memory_bytes",
    }
    duration_fields = {
        "preparation_duration_seconds", "restart_duration_seconds",
        "rollback_duration_seconds", "reapply_duration_seconds",
    }
    if not isinstance(value, dict) or set(value) != integer_fields | duration_fields:
        raise ValueError("Runtime resource observations are missing or incomplete.")
    if any(type(value[key]) is not int or value[key] <= 0 for key in integer_fields):
        raise ValueError("Runtime resource observation is not positive.")
    if any(type(value[key]) not in {int, float} or not math.isfinite(value[key]) or value[key] < 0 for key in duration_fields):
        raise ValueError("Runtime duration observation is invalid.")
    if (
        value["bundle_bytes"] > MAX_BUNDLE_BYTES
        or value["expanded_bytes"] > MAX_EXPANDED_BYTES
        or value["member_count"] > MAX_MEMBER_COUNT
        or descriptor is not None
        and value["expanded_bytes"] != descriptor.get("expanded_size")
        or descriptor is not None
        and value["member_count"] != len(descriptor.get("files", [])) + 2
        or descriptor is not None
        and any(
            not isinstance(record, dict)
            or type(record.get("size")) is not int
            or record["size"] < 0
            or record["size"] > MAX_MEMBER_BYTES
            for record in descriptor.get("files", [])
        )
        or value["free_bytes_before"]
        < value["bundle_bytes"] + value["expanded_bytes"] + PREPARATION_HEADROOM_BYTES
    ):
        raise ValueError("Runtime resource observations violate signed archive or headroom limits.")


def _validate_live_ui(value: object, *, version: str, image_version: str) -> None:
    required = {
        "status", "apply_requests", "rollback_requests", "target_version",
        "final_version", "final_active_bundle", "observed_progress", "console_errors",
        "page_errors", "final_health", "expected_console_events", "page_url",
        "page_title", "screenshots",
        "container_id_before", "container_id_after", "initial_operational_state",
        "final_operational_state", "evidence_json", "playwright_log",
    }
    expected_health = {
        "startup": {"status": "ready"},
        "ready": {"status": "ready", "ready": True},
    }
    expected_console_events = (
        value.get("expected_console_events") if isinstance(value, dict) else None
    )
    if (
        not isinstance(value, dict) or set(value) != required
        or value.get("status") != "passed" or value.get("apply_requests") != 1
        or value.get("rollback_requests") != 1 or value.get("target_version") != version
        or value.get("final_version") != image_version
        or value.get("final_active_bundle", True) is not None
        or value.get("observed_progress") is not True
        or value.get("console_errors") != [] or value.get("page_errors") != []
        or not isinstance(expected_console_events, dict)
        or set(expected_console_events) != {
            "unauthenticated_bootstrap",
            "apply_restart_transport",
            "rollback_restart_transport",
        }
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in expected_console_events.values()
        )
        or value.get("final_health") != expected_health
        or not isinstance(value.get("page_url"), str) or not value["page_url"]
        or not isinstance(value.get("page_title"), str) or not value["page_title"].strip()
        or not _valid_sha256(value.get("container_id_before"))
        or value.get("container_id_after") != value.get("container_id_before")
        or not _operational_state_valid(value.get("initial_operational_state"))
        or not _operational_state_valid(value.get("final_operational_state"))
        or value["final_operational_state"]["health"] != value["final_health"]
    ):
        raise ValueError("Live headless Update Center Apply/Rollback did not pass.")
    screenshots = value.get("screenshots")
    if not isinstance(screenshots, dict) or set(screenshots) != {"selected", "rollback"}:
        raise ValueError("Both live Update Center screenshots are required.")
    for descriptor in [*screenshots.values(), value["evidence_json"], value["playwright_log"]]:
        _validate_artifact_descriptor(descriptor)


def validate_release_qualification(
    architecture: str,
    proof: dict,
    version: str,
    image_version: str,
    *,
    qualification: dict | None = None,
    bundle_sha256: str | None = None,
    recovery_diagnostics: dict | None = None,
    raw_update_check: dict | None = None,
) -> None:
    """Require live release checks without changing runtime compatibility rules."""
    if not isinstance(proof, dict):
        raise ValueError("Native release qualification is missing.")
    descriptor = None
    expected_parser = None
    expected_image_imports = None
    if qualification is not None:
        checked = qualification.get("check")
        if raw_update_check is None or not _update_check_summary_matches(
            checked, raw_update_check
        ):
            raise ValueError("Initial update-check artifact differs from its receipt.")
        target = checked.get("trusted_target") if isinstance(checked, dict) else None
        descriptor = target.get("runtime_descriptor") if isinstance(target, dict) else None
        imports = qualification.get("selected_native_imports")
        expected_parser = imports.get("httptools.parser.parser") if isinstance(imports, dict) else None
        image_imports = qualification.get("image_imports_after_rollback")
        expected_image_imports = image_imports if isinstance(image_imports, dict) else None
    expected_generation = descriptor.get("generation_id") if isinstance(descriptor, dict) else None
    expected_expanded = descriptor.get("expanded_size") if isinstance(descriptor, dict) else None
    _validate_damaged_recovery(
        proof.get("damaged_native_recovery"), version=version, image_version=image_version,
        expected_generation=expected_generation, expected_bundle_sha256=bundle_sha256,
        expected_parser=expected_parser, expected_image_imports=expected_image_imports,
        recovery_diagnostics=recovery_diagnostics,
    )
    if architecture == "arm64":
        return
    refusals = proof.get("filesystem_refusals")
    if not isinstance(refusals, dict) or set(refusals) != {"noexec", "low_space"}:
        raise ValueError("Both real filesystem refusal probes are required.")
    for kind, result in refusals.items():
        expected_required = (
            proof.get("resource_observations", {}).get("bundle_bytes")
            + expected_expanded + PREPARATION_HEADROOM_BYTES
            if isinstance(expected_expanded, int)
            and type(proof.get("resource_observations", {}).get("bundle_bytes")) is int
            else None
        )
        _validate_filesystem_refusal(
            kind, result, version=version, image_version=image_version,
            expected_bundle_sha256=bundle_sha256,
            expected_required_bytes=expected_required,
        )
    _validate_resources(
        proof.get("resource_observations"), descriptor=descriptor
    )
    _validate_live_ui(proof.get("live_ui"), version=version, image_version=image_version)


def _validate_artifact_descriptor(descriptor: object) -> None:
    if not isinstance(descriptor, dict) or set(descriptor) != {"file", "sha256", "size"}:
        raise ValueError("Retained evidence artifact descriptor is missing.")
    name, expected = descriptor.get("file"), descriptor.get("sha256")
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or "/" in name or "\\" in name or ":" in name or "\x00" in name
            or not _valid_sha256(expected)
            or type(descriptor.get("size")) is not int or descriptor["size"] <= 0):
        raise ValueError("Retained evidence artifact path or digest is invalid.")


def _read_regular_file_bounded(path: Path, *, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Bounded input is missing or is not a regular file.") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError("Bounded input exceeds its limit or is not a regular file.")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        identity_before = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        )
        if (
            len(raw) > limit
            or len(raw) != before.st_size
            or identity_after != identity_before
        ):
            raise ValueError("Bounded input changed while it was read.")
        return raw
    finally:
        os.close(fd)


def read_retained_artifact(root: Path, descriptor: dict, *, limit: int, require_size: bool = True) -> bytes:
    _validate_artifact_descriptor(descriptor)
    name, expected = descriptor["file"], descriptor["sha256"]
    path = root / name
    raw = _read_regular_file_bounded(path, limit=limit)
    if (hashlib.sha256(raw).hexdigest() != expected or
            require_size and (type(descriptor.get("size")) is not int or descriptor["size"] != len(raw))):
        raise ValueError("Retained evidence artifact hash or size does not match.")
    return raw


def read_json_bounded(path: Path, *, limit: int) -> dict:
    return _decode_json_object(_read_regular_file_bounded(path, limit=limit))


def _reject_json_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def _decode_json_object(raw: bytes) -> dict:
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("JSON input is invalid.") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object.")
    return value


def serialize_aggregate(value: dict) -> bytes:
    try:
        encoded = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Final native evidence is not valid JSON.") from exc
    if len(encoded) > MAX_AGGREGATE_BYTES:
        raise ValueError("Final native evidence exceeds the classifier 8 MiB bound.")
    return encoded


def verify_image_inventory_artifacts(root: Path, qualification: dict) -> None:
    descriptors = qualification.get("image_inventory_artifacts")
    baseline = qualification.get("image_inventory_before")
    if not isinstance(descriptors, dict) or set(descriptors) != set(IMAGE_STAGES) or not isinstance(baseline, dict):
        raise ValueError("Every lifecycle image inventory artifact is required.")
    for stage in IMAGE_STAGES:
        actual = json.loads(
            read_retained_artifact(
                root, descriptors[stage], limit=MAX_INVENTORY_ARTIFACT_BYTES
            )
        )
        if actual != baseline:
            raise ValueError("Retained image inventory changed during " + stage)
        expected = qualification["image_inventory_" + stage]
        observed = actual if stage == "before" else {key: actual[key] for key in ("sha256", "path_count", "regular_file_bytes")}
        if observed != expected:
            raise ValueError("Image inventory artifact differs from its receipt.")


def verify_recovery_diagnostic_artifacts(root: Path, result: dict) -> dict:
    expectations = {
        "recovery_hold_diagnostic": (
            "recovery-hold", "recovery_hold_status", "recovery_hold_control_state"
        ),
        "post_rollback_diagnostic": (
            "post-rollback", "rollback_status", "post_rollback_control_state"
        ),
    }
    recovered = {}
    for name, (phase, status_field, control_field) in expectations.items():
        raw = read_retained_artifact(
            root, result.get(name), limit=MAX_RECOVERY_DIAGNOSTIC_BYTES
        )
        actual = _decode_json_object(raw)
        if (
            set(actual) != {"schema", "phase", "status", "control"}
            or actual.get("schema") != 1
            or actual.get("phase") != phase
        ):
            raise ValueError("Recovery diagnostic artifact differs from its receipt.")
        recovered[status_field] = actual["status"]
        recovered[control_field] = actual["control"]
        if (
            status_field in result and result[status_field] != actual["status"]
            or control_field in result and result[control_field] != actual["control"]
        ):
            raise ValueError("Recovery diagnostic artifact differs from its receipt.")
    return recovered


def _update_check_summary_matches(summary: object, status: object) -> bool:
    if (
        not isinstance(summary, dict)
        or set(summary)
        != {
            "update_available",
            "image_required",
            "catalog_state",
            "trusted_target",
            "raw_status_artifact",
        }
        or not isinstance(status, dict)
    ):
        return False
    target = status.get("trusted_target")
    compact_target = summary.get("trusted_target")
    return (
        isinstance(target, dict)
        and isinstance(compact_target, dict)
        and set(compact_target)
        == {"version", "runtime_descriptor", "bundle_sha256"}
        and summary.get("update_available") == status.get("update_available")
        and summary.get("image_required") == status.get("image_required")
        and summary.get("catalog_state") == status.get("catalog_state")
        and compact_target
        == {
            "version": target.get("version"),
            "runtime_descriptor": target.get("runtime_descriptor"),
            "bundle_sha256": target.get("bundle_sha256"),
        }
    )


def verify_update_check_artifact(
    root: Path,
    qualification: dict,
    *,
    expected_architecture: str,
    expected_source_commit: str,
) -> dict:
    if not isinstance(qualification, dict):
        raise ValueError("Native runtime qualification is missing.")
    summary = qualification.get("check")
    artifact = (
        summary.get("raw_status_artifact") if isinstance(summary, dict) else None
    )
    raw = read_retained_artifact(
        root, artifact, limit=MAX_UPDATE_CHECK_ARTIFACT_BYTES
    )
    actual = _decode_json_object(raw)
    if (
        set(actual)
        != {
            "schema",
            "mode",
            "phase",
            "architecture",
            "source_commit",
            "status",
        }
        or type(actual.get("schema")) is not int
        or actual["schema"] != 1
        or actual.get("mode") != UPDATE_CHECK_EVIDENCE_MODE
        or actual.get("phase") != UPDATE_CHECK_EVIDENCE_PHASE
        or actual.get("architecture") != expected_architecture
        or actual.get("source_commit") != expected_source_commit
        or not _update_check_summary_matches(summary, actual.get("status"))
    ):
        raise ValueError("Initial update-check artifact differs from its receipt.")
    return actual["status"]


def _rollback_status_summary_matches(summary: object, status: object) -> bool:
    fields = (
        "current_version",
        "launcher_protocol",
        "active_bundle",
        "operation_busy",
        "worker_runtime",
    )
    if (
        not isinstance(summary, dict)
        or set(summary)
        != {
            "current_version",
            "launcher_protocol",
            "active_bundle",
            "operation_busy",
            "worker_runtime",
            "raw_status_artifact",
        }
        or not isinstance(status, dict)
        or any(key not in status for key in fields)
    ):
        return False
    try:
        compact = json.dumps(
            {key: summary[key] for key in fields},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        retained = json.dumps(
            {key: status[key] for key in fields},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return False
    return compact == retained


def verify_rollback_status_artifact(
    root: Path,
    qualification: dict,
    *,
    expected_architecture: str,
    expected_source_commit: str,
) -> dict:
    if not isinstance(qualification, dict):
        raise ValueError("Native runtime qualification is missing.")
    summary = qualification.get("rollback_status")
    artifact = (
        summary.get("raw_status_artifact") if isinstance(summary, dict) else None
    )
    raw = read_retained_artifact(
        root, artifact, limit=MAX_ROLLBACK_STATUS_ARTIFACT_BYTES
    )
    actual = _decode_json_object(raw)
    if (
        set(actual)
        != {
            "schema",
            "mode",
            "phase",
            "architecture",
            "source_commit",
            "status",
        }
        or type(actual.get("schema")) is not int
        or actual["schema"] != 1
        or actual.get("mode") != UPDATE_CHECK_EVIDENCE_MODE
        or actual.get("phase") != ROLLBACK_STATUS_EVIDENCE_PHASE
        or actual.get("architecture") != expected_architecture
        or actual.get("source_commit") != expected_source_commit
        or not _rollback_status_summary_matches(summary, actual.get("status"))
    ):
        raise ValueError("Post-rollback status artifact differs from its receipt.")
    return actual["status"]


def _validate_native_receipt(
    name: str,
    receipt: dict,
    *,
    header: dict,
    recovery_diagnostic: dict,
    update_check: dict,
    rollback_status: dict,
) -> tuple[dict, dict]:
    if any(receipt.get(key) != value for key, value in header.items()):
        raise ValueError("Native receipts describe different release artifacts.")
    if receipt.get("cleanup") != {"status": "passed", "failures": []}:
        raise ValueError("Native qualification cleanup did not pass.")
    if (
        type(receipt.get("serialized_size_bytes")) is not int
        or receipt["serialized_size_bytes"] <= 0
        or receipt["serialized_size_bytes"] > MAX_NATIVE_RECEIPT_BYTES
    ):
        raise ValueError("Native qualification serialized size is invalid.")
    architecture = receipt.get("architecture")
    if (
        not isinstance(architecture, dict)
        or architecture.get("architecture") != name
        or architecture.get("status") != "passed"
    ):
        raise ValueError("Native architecture receipt is missing or mismatched.")
    qualification = architecture.get("qualification")
    if not isinstance(qualification, dict) or not _rollback_status_summary_matches(
        qualification.get("rollback_status"), rollback_status
    ):
        raise ValueError("Post-rollback status artifact differs from its receipt.")
    rollback_operational_state = {
        "status_worker_scope": qualification.get("rollback_runtime_component_scope"),
        "status_worker": rollback_status.get("worker_runtime"),
        "commands": qualification.get("rollback_supervisor_processes"),
        "processes": qualification.get("rollback_supervisor_process_identities"),
        "services": qualification.get("rollback_supervisor_services"),
        "health": qualification.get("rollback_health"),
    }
    if (
        not _operational_state_valid(rollback_operational_state)
        or not _rollback_valid(
            rollback_status,
            image_version=header["bootstrap"]["minimum_image_version"],
            operational_state=rollback_operational_state,
        )
        or rollback_status.get("launcher_protocol") != 4
        or qualification.get("rollback_runtime_components")
        != {"ui": rollback_status.get("worker_runtime")}
    ):
        raise ValueError("Native qualification post-rollback image status is invalid.")
    validate_release_qualification(
        name,
        architecture.get("release_qualification"),
        header["target_application_version"],
        header["bootstrap"]["minimum_image_version"],
        qualification=qualification,
        bundle_sha256=architecture.get("target_variant_bundle_sha256"),
        recovery_diagnostics=recovery_diagnostic,
        raw_update_check=update_check,
    )
    hashes = receipt.get("logs_sha256")
    if not isinstance(hashes, dict) or set(hashes) != {"app", "feed"}:
        raise ValueError("Native qualification logs are missing.")
    required_logs = (
        {"damaged-native", "noexec", "low-space", "live-ui"}
        if name == "amd64"
        else {"damaged-native"}
    )
    release_logs = receipt.get("release_log_artifacts")
    release_hashes = receipt.get("release_logs_sha256")
    if (
        not isinstance(release_logs, dict)
        or set(release_logs) != required_logs
        or not isinstance(release_hashes, dict)
        or set(release_hashes) != required_logs
        or any(
            not isinstance(item, dict) or item.get("sha256") != release_hashes[key]
            for key, item in release_logs.items()
        )
    ):
        raise ValueError("Native fault and live probe logs are missing or mismatched.")
    return {**architecture, "release_log_artifacts": release_logs}, hashes


def aggregate(
    receipts: dict[str, dict],
    *,
    recovery_diagnostics: dict[str, dict] | None = None,
    update_checks: dict[str, dict] | None = None,
    rollback_statuses: dict[str, dict] | None = None,
) -> dict:
    if set(receipts) != {"amd64", "arm64"}:
        raise ValueError("Both native architecture receipts are required.")
    if update_checks is None or set(update_checks) != {"amd64", "arm64"}:
        raise ValueError("Both retained initial update-check artifacts are required.")
    if rollback_statuses is None or set(rollback_statuses) != {"amd64", "arm64"}:
        raise ValueError("Both retained post-rollback status artifacts are required.")
    first = receipts["amd64"]
    if first.get("schema") != 2 or first.get("status") != "passed":
        raise ValueError("Native qualification did not pass with schema 2.")
    header = {key: first[key] for key in HEADER_FIELDS}
    architectures = {}
    logs = {}
    for name, receipt in receipts.items():
        architectures[name], logs[name] = _validate_native_receipt(
            name,
            receipt,
            header=header,
            recovery_diagnostic=(
                recovery_diagnostics.get(name)
                if recovery_diagnostics is not None
                else None
            ),
            update_check=update_checks[name],
            rollback_status=rollback_statuses[name],
        )
    return {**header, "architectures": architectures, "native_logs_sha256": logs}


def _release_classifier():
    spec = importlib.util.spec_from_file_location(
        "release_impact", Path(__file__).with_name("classify-release-impact.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Release classifier could not be loaded.")
    classifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(classifier)
    return classifier


def _read_native_receipt(path: Path) -> dict:
    raw = _read_regular_file_bounded(path, limit=MAX_NATIVE_RECEIPT_BYTES)
    receipt = _decode_json_object(raw)
    if receipt.get("serialized_size_bytes") != len(raw):
        raise ValueError("Native qualification serialized size does not match its file.")
    return receipt


def _verify_native_receipt_files(
    path: Path,
    name: str,
    architecture: dict,
    hashes: dict,
) -> None:
    for component, expected_hash in hashes.items():
        if component not in {"app", "feed"}:
            raise ValueError("Native qualification log component is invalid.")
        log = path.with_name(path.stem + "-" + component + ".log")
        raw_log = _read_regular_file_bounded(log, limit=64 * 1024 * 1024)
        if hashlib.sha256(raw_log).hexdigest() != expected_hash:
            raise ValueError("Native qualification log hash does not match its receipt.")
    verify_image_inventory_artifacts(path.parent, architecture["qualification"])
    for descriptor in architecture["release_log_artifacts"].values():
        read_retained_artifact(path.parent, descriptor, limit=64 * 1024 * 1024)
    if name != "amd64":
        return
    ui = architecture["release_qualification"]["live_ui"]
    raw_ui = json.loads(
        read_retained_artifact(path.parent, ui.get("evidence_json"), limit=64 * 1024)
    )
    expected_raw_ui = {
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
    if raw_ui != expected_raw_ui:
        raise ValueError("Live UI artifact differs from its receipt.")
    read_retained_artifact(
        path.parent, ui.get("playwright_log"), limit=64 * 1024 * 1024
    )
    for screenshot in ui["screenshots"].values():
        raw = read_retained_artifact(path.parent, screenshot, limit=10 * 1024 * 1024)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Live UI screenshot is not a PNG.")


def validate_native_receipt_file(
    path: Path,
    *,
    architecture: str,
    catalog_path: Path,
    release_config: dict,
    source_commit: str,
) -> tuple[dict, dict, dict, dict]:
    receipt = _read_native_receipt(path)
    if receipt.get("schema") != 2 or receipt.get("status") != "passed":
        raise ValueError("Native qualification did not pass with schema 2.")
    header = {key: receipt[key] for key in HEADER_FIELDS}
    qualification = receipt["architecture"]["qualification"]
    update_check = verify_update_check_artifact(
        path.parent,
        qualification,
        expected_architecture=architecture,
        expected_source_commit=source_commit,
    )
    rollback_status = verify_rollback_status_artifact(
        path.parent,
        qualification,
        expected_architecture=architecture,
        expected_source_commit=source_commit,
    )
    damaged = receipt["architecture"]["release_qualification"][
        "damaged_native_recovery"
    ]
    recovery_diagnostic = verify_recovery_diagnostic_artifacts(path.parent, damaged)
    validated_architecture, hashes = _validate_native_receipt(
        architecture,
        receipt,
        header=header,
        recovery_diagnostic=recovery_diagnostic,
        update_check=update_check,
        rollback_status=rollback_status,
    )
    _verify_native_receipt_files(path, architecture, validated_architecture, hashes)
    classifier = _release_classifier()
    if not classifier.validate_protocol_four_runtime_architecture_receipt(
        config=release_config,
        catalog_path=catalog_path,
        receipt=receipt,
        architecture=architecture,
        expected_source_commit=source_commit,
        public_keys=None,
    ):
        raise ValueError(
            "Native architecture evidence is incompatible with the signed release contract."
        )
    return receipt, recovery_diagnostic, update_check, rollback_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amd64-evidence", type=Path)
    parser.add_argument("--arm64-evidence", type=Path)
    parser.add_argument("--validate-evidence", type=Path)
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--release-config", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    read_json_bounded(args.catalog, limit=MAX_CATALOG_BYTES)
    release_config = read_json_bounded(args.release_config, limit=MAX_CONFIG_BYTES)
    if args.validate_evidence is not None:
        if (
            args.architecture is None
            or args.amd64_evidence is not None
            or args.arm64_evidence is not None
            or args.output is not None
        ):
            parser.error(
                "--validate-evidence requires --architecture and cannot be combined "
                "with aggregate inputs or --output"
            )
        validate_native_receipt_file(
            args.validate_evidence,
            architecture=args.architecture,
            catalog_path=args.catalog,
            release_config=release_config,
            source_commit=args.source_commit,
        )
        print(json.dumps({"status": "passed", "architecture": args.architecture}))
        return 0
    if (
        args.amd64_evidence is None
        or args.arm64_evidence is None
        or args.output is None
        or args.architecture is not None
    ):
        parser.error(
            "aggregate mode requires --amd64-evidence, --arm64-evidence, and --output"
        )
    receipt_paths = {
        "amd64": args.amd64_evidence, "arm64": args.arm64_evidence,
    }
    receipts = {}
    recovery_diagnostics = {}
    update_checks = {}
    rollback_statuses = {}
    for name, path in receipt_paths.items():
        (
            receipts[name],
            recovery_diagnostics[name],
            update_checks[name],
            rollback_statuses[name],
        ) = validate_native_receipt_file(
            path,
            architecture=name,
            catalog_path=args.catalog,
            release_config=release_config,
            source_commit=args.source_commit,
        )
    result = aggregate(
        receipts,
        recovery_diagnostics=recovery_diagnostics,
        update_checks=update_checks,
        rollback_statuses=rollback_statuses,
    )
    classifier = _release_classifier()
    encoded_result = serialize_aggregate(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-capability-", dir=args.output.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded_result)
            output.flush()
            os.fsync(output.fileno())
        if not classifier._read_protocol_four_runtime_evidence(
            config=release_config,
            catalog_path=args.catalog, evidence_path=temporary_path,
            expected_source_commit=args.source_commit, public_keys=None,
        ):
            raise ValueError("Native evidence is incompatible with the signed release contract.")
        os.replace(temporary_path, args.output)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(json.dumps({"status": "passed", "architectures": sorted(result["architectures"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
