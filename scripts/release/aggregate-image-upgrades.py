#!/usr/bin/env python3
"""Bind the four native old-volume migrations to the sealed foundation image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any

SHA256 = re.compile(r"[0-9a-f]{64}")
SHA40 = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
ARCHITECTURES = ("amd64", "arm64")
BASELINE_VERSIONS = ("1.4.0", "1.4.1")
CLI_RUNTIME_STAGES = ("after_replace", "after_restart")
MANDATORY_IMAGE_DEPENDENCIES = ("cryptography", "pydantic_core", "sqlmodel")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_cli_runtime_info(
    receipt: dict[str, Any], *, log_directory: Path, candidate_version: str
) -> None:
    evidence = receipt.get("cli_runtime_info")
    require(
        isinstance(evidence, dict) and set(evidence) == set(CLI_RUNTIME_STAGES),
        "Image CLI runtime identity evidence is incomplete.",
    )
    for stage in CLI_RUNTIME_STAGES:
        row = evidence[stage]
        require(
            isinstance(row, dict) and set(row) == {"sha256", "identity"},
            "Image CLI runtime identity evidence is invalid.",
        )
        path = log_directory / f"candidate-runtime-info-{stage.replace('_', '-')}.json"
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError("Image CLI runtime identity bytes are unavailable.") from exc
        require(
            not path.is_symlink()
            and path.is_file()
            and 0 < len(raw) <= 1024 * 1024
            and SHA256.fullmatch(row.get("sha256", "")) is not None
            and hashlib.sha256(raw).hexdigest() == row["sha256"],
            "Image CLI runtime identity bytes are missing or changed.",
        )
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Image CLI runtime identity JSON is invalid.") from exc
        require(
            isinstance(payload, dict)
            and set(payload)
            == {
                "diagnostic_source",
                "application_dir",
                "application_version",
                "process_runtime",
                "verified_generation",
            },
            "Image CLI runtime identity fields are invalid.",
        )
        process = payload["process_runtime"]
        require(
            isinstance(process, dict)
            and set(process)
            == {
                "pid",
                "python_executable",
                "python_version",
                "python_prefix",
                "dependencies",
            }
            and type(process["pid"]) is int
            and process["pid"] > 0
            and process["python_executable"] == "/venv/bin/python"
            and process["python_version"] == "3.14.8"
            and process["python_prefix"] == "/venv",
            "Image CLI runtime process identity is invalid.",
        )
        dependencies = process["dependencies"]
        expected_dependencies = {
            "requests",
            "cryptography",
            "sqlmodel",
            "uvicorn",
            "pydantic_core",
        }
        require(
            isinstance(dependencies, dict)
            and set(dependencies) == expected_dependencies,
            "Image CLI runtime dependency fields are invalid.",
        )
        for name, dependency in dependencies.items():
            require(
                isinstance(dependency, dict)
                and type(dependency.get("loaded")) is bool,
                f"Image CLI runtime dependency {name} is invalid.",
            )
            if dependency["loaded"]:
                dependency_path = dependency.get("path")
                require(
                    set(dependency) == {"loaded", "path", "version"}
                    and isinstance(dependency_path, str)
                    and dependency_path.startswith("/venv/")
                    and ".." not in PurePosixPath(dependency_path).parts
                    and str(PurePosixPath(dependency_path)) == dependency_path
                    and isinstance(dependency.get("version"), str),
                    f"Image CLI runtime dependency {name} is not image-owned.",
                )
            else:
                require(
                    set(dependency) == {"loaded"},
                    f"Image CLI runtime dependency {name} is invalid.",
                )
        require(
            all(dependencies[name]["loaded"] is True for name in MANDATORY_IMAGE_DEPENDENCIES),
            "Mandatory image CLI runtime dependencies are not loaded.",
        )
        mandatory = {
            name: dependencies[name]["path"]
            for name in MANDATORY_IMAGE_DEPENDENCIES
        }
        identity = {
            "diagnostic_source": payload.get("diagnostic_source"),
            "application_dir": payload.get("application_dir"),
            "application_version": payload.get("application_version"),
            "python_executable": process.get("python_executable"),
            "python_version": process.get("python_version"),
            "mandatory_dependencies": mandatory,
            "verified_generation": payload.get("verified_generation"),
        }
        require(row["identity"] == identity, "Image CLI runtime receipt identity changed.")
        require(
            identity["diagnostic_source"] == "image"
            and identity["application_dir"] == "/app"
            and identity["application_version"] == candidate_version
            and identity["python_executable"] == "/venv/bin/python"
            and identity["python_version"] == "3.14.8"
            and identity["verified_generation"] is None,
            "Image CLI runtime identity is not the sealed image runtime.",
        )
        require(
            all(
                isinstance(path_value, str)
                and path_value.startswith("/venv/")
                and ".." not in PurePosixPath(path_value).parts
                and str(PurePosixPath(path_value)) == path_value
                for path_value in mandatory.values()
            ),
            "Image CLI dependency origin is not image-owned.",
        )


def aggregate(
    receipts: dict[tuple[str, str], dict[str, Any]], *, baselines: dict[str, Any],
    descriptor: dict[str, Any], candidate_version: str, source_commit: str,
    archive_sha256: str, descriptor_sha256: str, evidence_directory: Path,
) -> dict[str, Any]:
    expected = {(arch, version) for arch in ARCHITECTURES for version in BASELINE_VERSIONS}
    require(set(receipts) == expected, "All four native baseline migrations are required.")
    require(SHA40.fullmatch(source_commit) is not None, "Candidate source commit is invalid.")
    require(all(SHA256.fullmatch(value) for value in (archive_sha256, descriptor_sha256)),
            "Candidate artifact hashes are invalid.")
    version_match = VERSION.fullmatch(candidate_version)
    require(version_match is not None, "Foundation candidate version is invalid.")
    version_parts = tuple(int(part) for part in version_match.groups())
    recovery_version = baselines.get("recovers_unpublished_image_milestone", "")
    recovery_match = VERSION.fullmatch(recovery_version)
    require(recovery_match is not None and 1 <= version_parts[2] <= 9
            and tuple(int(part) for part in recovery_match.groups())
            == (version_parts[0], version_parts[1], 0),
            "Foundation recovery milestone is invalid for the candidate.")
    require(baselines.get("schema") == 1 and baselines.get("candidate_version") == candidate_version
            and set(baselines.get("images", {})) == set(BASELINE_VERSIONS),
            "Foundation baseline fixture is incomplete.")
    app = baselines["signed_app"]
    require(app.get("version") == "1.4.5" and SHA40.fullmatch(app.get("source_commit", "")) is not None,
            "Published signed app baseline is invalid.")
    index = descriptor["image_index"]["digest"]
    require(DIGEST.fullmatch(index) is not None, "Candidate index digest is invalid.")
    platforms = {row["platform"]: row for row in descriptor["platforms"]}
    require(len(descriptor["platforms"]) == 2 and set(platforms) == {"linux/amd64", "linux/arm64"},
            "Candidate native platforms are incomplete.")
    migrations = []
    image_ids: dict[str, str] = {}
    for architecture, version in sorted(expected):
        receipt = receipts[(architecture, version)]
        require(receipt.get("schema") == 1 and receipt.get("status") == "passed"
                and receipt.get("architecture") == architecture
                and receipt.get("candidate_version") == candidate_version
                and receipt.get("source_commit") == source_commit,
                "Migration receipt is failed or belongs to another candidate.")
        baseline = baselines["images"][version]
        expected_baseline = {
            "repository": baselines["repository"], "image_version": version,
            "source_commit": baseline["source_commit"], "image_index_digest": baseline["index_digest"],
            "platform_manifest_digest": baseline["manifests"][architecture],
            "signed_app_version": app["version"], "signed_app_source_commit": app["source_commit"],
            "signed_catalog_sha256": app["catalog_sha256"], "signed_bundle_sha256": app["bundle_sha256"],
            "bundle_url": app["bundle_url"],
        }
        require(all(receipt.get("baseline", {}).get(key) == value for key, value in expected_baseline.items()),
                "Migration baseline does not match the published image and signed app.")
        platform = platforms["linux/" + architecture]
        expected_candidate = {
            "index_digest": index, "manifest_digest": platform["manifest"]["digest"],
            "config_digest": platform["config"]["digest"],
            "oci_archive_sha256": archive_sha256, "descriptor_sha256": descriptor_sha256,
        }
        candidate = receipt.get("candidate", {})
        require(all(candidate.get(key) == value for key, value in expected_candidate.items()),
                "Migration used a different image artifact or native child.")
        image_id = candidate.get("image_id", "")
        require(DIGEST.fullmatch(image_id) is not None,
                "Candidate Docker image identity is invalid.")
        prior_image_id = image_ids.setdefault(architecture, image_id)
        require(prior_image_id == image_id,
                "Candidate Docker image identity changed between baselines.")
        ids = [receipt.get(name, "") for name in
               ("old_container_id", "replacement_container_id", "restarted_container_id")]
        require(all(SHA256.fullmatch(value) for value in ids) and ids[0] != ids[1] and ids[1] == ids[2],
                "Migration must replace the old container once and restart the same new container.")
        old = receipt.get("old_status", {})
        active = old.get("active_bundle")
        require(old.get("launcher_protocol") == 3 and old.get("current_version") == app["version"]
                and isinstance(active, dict) and active.get("version") == app["version"],
                "Migration did not begin with a healthy signed protocol-3 app.")
        status = receipt.get("candidate_status", {})
        worker = status.get("worker_runtime", {})
        require(status.get("current_version") == candidate_version
                and status.get("image_version") == candidate_version
                and status.get("launcher_protocol") == 4 and status.get("runtime_source") == "image"
                and status.get("active_bundle") is None
                and worker.get("python_executable") == "/venv/bin/python"
                and worker.get("verified_generation") is None,
                "Migration did not select the verified image-owned runtime.")
        deactivated = receipt.get("deactivated_active", {})
        require(all(deactivated.get(key) == value for key, value in active.items())
                and deactivated.get("deactivated_reason") == "image-version-is-current-or-newer",
                "Migration did not preserve the deactivated app identity.")
        old_control = receipt.get("old_control_inventory", {})
        resolved = receipt.get("resolved_control_inventory", {})
        require(all(old_control.get(name, {}).get("type") == "file" for name in ("active.json", "rollback.json"))
                and "activation-pending.json" not in old_control
                and "active.json" not in resolved
                and resolved.get("deactivated-active.json", {}).get("type") == "file",
                "Migration control state is incomplete or still selects the old app.")
        commands = receipt.get("image_process_commands", {})
        require(set(commands) == {"core", "ui"}, "Both image-owned process commands are required.")
        for mode, command in commands.items():
            require(isinstance(command, str), "Runtime process command is invalid.")
            argv = shlex.split(command)
            require(len(argv) >= 4 and argv[0] in {"python", "/venv/bin/python"}
                    and argv[1:4] == ["-u", "/app/core/runtime_launcher.py", mode]
                    and "/config/channelwatch-runtime/" not in command,
                    "A runtime process selected writable code.")
        preserved = receipt.get("preservation_before_sha256", {})
        require(set(preserved) == {"/config/settings.json", "/config/encryption.key"}
                and all(SHA256.fullmatch(value) for value in preserved.values())
                and receipt.get("preservation_after_replace_sha256") == preserved
                and receipt.get("preservation_after_restart_sha256") == preserved,
                "Migration changed persistent settings or the managed encryption key.")
        for field in ("external_storage_key_sha256", "cli_output_sha256", "browser_output_sha256"):
            require(SHA256.fullmatch(receipt.get(field, "")) is not None, "Migration output/key evidence is missing.")
        semantics = receipt.get("credential_api_semantics", {})
        require(set(semantics) == {"after_baseline", "after_replace", "after_restart"}
                and all(value is True for value in semantics.values()),
                "Retained credential decryption/API semantics are not proven at every stage.")
        require(re.fullmatch(r"[0-9a-f]{32}", receipt.get("baseline_job_id", "")) is not None,
                "Authenticated baseline Apply job identity is missing.")
        for field in ("api_health", "headless_browser", "cli", "runtime_ready", "different_container_on_image_upgrade",
                      "same_container_on_candidate_restart", "encrypted_settings_usable"):
            require(receipt.get(field) is True, "A required migration check did not pass.")
        verify_cli_runtime_info(
            receipt,
            log_directory=evidence_directory
            / f"native-image-upgrade-{architecture}-{version}-logs",
            candidate_version=candidate_version,
        )
        require(receipt.get("cleanup") == {"status": "passed", "failures": []}, "Migration cleanup did not pass.")
        logs = receipt.get("logs_sha256", {})
        require(isinstance(logs, dict) and len(logs) == 3, "All migration container logs are required.")
        components = set()
        for name, digest in logs.items():
            match = re.fullmatch(rf"cw-image-upgrade-{architecture}-[0-9a-f]{{10}}-(old|candidate|feed)", name)
            require(match is not None and SHA256.fullmatch(digest) is not None, "Migration log identity is invalid.")
            components.add(match.group(1))
            path = evidence_directory / f"native-image-upgrade-{architecture}-{version}-logs" / (name + ".log")
            require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= 64 * 1024 * 1024,
                    "Migration log is missing or invalid.")
            require(file_sha256(path) == digest, "Migration log bytes do not match the receipt.")
        require(components == {"old", "candidate", "feed"}, "Migration log components are incomplete.")
        migrations.append({"architecture": architecture, "baseline_image_version": version,
                           "baseline_index_digest": baseline["index_digest"],
                           "baseline_manifest_digest": baseline["manifests"][architecture],
                           "logs_sha256": logs})
    return {"schema": 1, "status": "passed", "candidate_version": candidate_version,
            "recovers_unpublished_image_milestone": recovery_version, "source_commit": source_commit,
            "oci_archive_sha256": archive_sha256, "descriptor_sha256": descriptor_sha256,
            "image_index_digest": index, "signed_app": app, "migrations": migrations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-directory", type=Path, required=True)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--descriptor-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(file_sha256(args.descriptor) == args.descriptor_sha256, "Candidate descriptor bytes changed.")
    receipts = {}
    for architecture in ARCHITECTURES:
        for version in BASELINE_VERSIONS:
            path = args.evidence_directory / f"native-image-upgrade-{architecture}-{version}.json"
            require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= 4 * 1024 * 1024,
                    "Native migration receipt is missing or invalid.")
            receipts[(architecture, version)] = json.loads(path.read_text(encoding="utf-8"))
    result = aggregate(receipts, baselines=json.loads(args.baselines.read_text(encoding="utf-8")),
                       descriptor=json.loads(args.descriptor.read_text(encoding="utf-8")),
                       candidate_version=args.candidate_version, source_commit=args.source_commit,
                       archive_sha256=args.archive_sha256,
                       descriptor_sha256=args.descriptor_sha256, evidence_directory=args.evidence_directory)
    for migration in result["migrations"]:
        name = f"native-image-upgrade-{migration['architecture']}-{migration['baseline_image_version']}.json"
        migration["receipt_sha256"] = file_sha256(args.evidence_directory / name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
