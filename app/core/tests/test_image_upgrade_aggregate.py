"""Require complete, artifact-bound proof for the one-time foundation migration."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/aggregate-image-upgrades.py"


def load_module():
    spec = importlib.util.spec_from_file_location("image_upgrade_aggregate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def evidence(tmp_path):
    baselines = json.loads((ROOT / "scripts/release/image-upgrade-baselines.json").read_text())
    candidate_version = baselines["candidate_version"]
    descriptor = {
        "image_index": {"digest": "sha256:" + "a" * 64},
        "platforms": [
            {"platform": "linux/" + arch,
             "manifest": {"digest": "sha256:" + char * 64},
             "config": {"digest": "sha256:" + "c" * 64}}
            for arch, char in (("amd64", "d"), ("arm64", "e"))
        ],
    }
    source = "f" * 40
    records = {}
    for arch in ("amd64", "arm64"):
        for version, baseline in baselines["images"].items():
            active = {"version": "1.4.5", "path": "/config/channelwatch-runtime/releases/old"}
            preserved = {"/config/settings.json": "1" * 64, "/config/encryption.key": "2" * 64}
            prefix = f"cw-image-upgrade-{arch}-{version.replace('.', ''):0>10}"
            log_dir = tmp_path / f"native-image-upgrade-{arch}-{version}-logs"
            log_dir.mkdir()
            logs = {}
            for component in ("old", "candidate", "feed"):
                name = prefix + "-" + component
                data = f"{component} healthy\n".encode()
                (log_dir / (name + ".log")).write_bytes(data)
                logs[name] = hashlib.sha256(data).hexdigest()
            cli_runtime_info = {}
            mandatory_dependencies = {
                dependency: (
                    "/venv/lib/python3.14/site-packages/"
                    f"{dependency}/__init__.py"
                )
                for dependency in ("cryptography", "pydantic_core", "sqlmodel")
            }
            process_runtime = {
                "pid": 123,
                "python_executable": "/venv/bin/python",
                "python_version": "3.14.8",
                "python_prefix": "/venv",
                "dependencies": {
                    "requests": {"loaded": False},
                    "uvicorn": {"loaded": False},
                    **{
                        name: {"loaded": True, "path": path, "version": "test"}
                        for name, path in mandatory_dependencies.items()
                    },
                },
            }
            for stage in ("after_replace", "after_restart"):
                name = f"candidate-runtime-info-{stage.replace('_', '-')}.json"
                data = (
                    json.dumps(
                        {
                            "application_dir": "/app",
                            "application_version": candidate_version,
                            "diagnostic_source": "image",
                            "process_runtime": process_runtime,
                            "verified_generation": None,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
                (log_dir / name).write_bytes(data)
                cli_runtime_info[stage] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "identity": {
                        "diagnostic_source": "image",
                        "application_dir": "/app",
                        "application_version": candidate_version,
                        "python_executable": "/venv/bin/python",
                        "python_version": "3.14.8",
                        "mandatory_dependencies": mandatory_dependencies,
                        "verified_generation": None,
                    },
                }
            app = baselines["signed_app"]
            platform = next(row for row in descriptor["platforms"] if row["platform"] == "linux/" + arch)
            receipt = {
                "schema": 1, "status": "passed", "architecture": arch,
                "candidate_version": candidate_version, "source_commit": source,
                "baseline": {
                    "repository": baselines["repository"], "image_version": version,
                    "source_commit": baseline["source_commit"],
                    "image_index_digest": baseline["index_digest"],
                    "platform_manifest_digest": baseline["manifests"][arch],
                    "image_id": "sha256:" + "b" * 64,
                    "signed_app_version": app["version"], "signed_app_source_commit": app["source_commit"],
                    "signed_catalog_sha256": app["catalog_sha256"],
                    "signed_bundle_sha256": app["bundle_sha256"], "bundle_url": app["bundle_url"],
                },
                "candidate": {
                    "index_digest": descriptor["image_index"]["digest"],
                    "manifest_digest": platform["manifest"]["digest"],
                    "config_digest": platform["config"]["digest"],
                    "oci_archive_sha256": "3" * 64, "descriptor_sha256": "4" * 64,
                    "image_id": "sha256:" + ("1" if arch == "amd64" else "2") * 64,
                },
                "baseline_job_id": "a" * 32,
                "old_container_id": "5" * 64, "replacement_container_id": "6" * 64,
                "restarted_container_id": "6" * 64,
                "old_status": {"current_version": "1.4.5", "launcher_protocol": 3, "active_bundle": active},
                "candidate_status": {"current_version": candidate_version, "image_version": candidate_version,
                                     "launcher_protocol": 4, "runtime_source": "image", "active_bundle": None,
                                     "worker_runtime": {"python_executable": "/venv/bin/python", "verified_generation": None}},
                "old_control_inventory": {"active.json": {"type": "file"}, "rollback.json": {"type": "file"}},
                "resolved_control_inventory": {"deactivated-active.json": {"type": "file"}},
                "deactivated_active": {**active, "deactivated_reason": "image-version-is-current-or-newer"},
                "image_process_commands": {
                    mode: f"python -u /app/core/runtime_launcher.py {mode}" for mode in ("core", "ui")
                },
                "preservation_before_sha256": preserved,
                "preservation_after_replace_sha256": copy.deepcopy(preserved),
                "preservation_after_restart_sha256": copy.deepcopy(preserved),
                "external_storage_key_sha256": "7" * 64,
                "credential_api_semantics": {"after_baseline": True, "after_replace": True, "after_restart": True},
                "cli_output_sha256": "8" * 64, "browser_output_sha256": "9" * 64,
                "cli_runtime_info": cli_runtime_info,
                "logs_sha256": logs, "cleanup": {"status": "passed", "failures": []},
            }
            for name in ("api_health", "headless_browser", "cli", "runtime_ready",
                         "different_container_on_image_upgrade", "same_container_on_candidate_restart",
                         "encrypted_settings_usable"):
                receipt[name] = True
            records[(arch, version)] = receipt
    return baselines, descriptor, source, records, tmp_path


def run_aggregate(evidence):
    baselines, descriptor, source, records, directory = evidence
    return load_module().aggregate(records, baselines=baselines, descriptor=descriptor,
                                   candidate_version=baselines["candidate_version"], source_commit=source,
                                   archive_sha256="3" * 64,
                                   descriptor_sha256="4" * 64, evidence_directory=directory)


def rewrite_cli_runtime_info(evidence, *, mutation):
    receipt = evidence[3][("arm64", "1.4.0")]
    path = (
        evidence[4]
        / "native-image-upgrade-arm64-1.4.0-logs"
        / "candidate-runtime-info-after-restart.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    receipt["cli_runtime_info"]["after_restart"]["sha256"] = hashlib.sha256(
        data
    ).hexdigest()


def test_complete_native_migration_matrix_is_bound_to_candidate(evidence):
    records = evidence[3]
    assert records[("amd64", "1.4.0")]["candidate"]["image_id"] != (
        records[("arm64", "1.4.0")]["candidate"]["image_id"]
    )
    assert records[("amd64", "1.4.0")]["candidate"]["image_id"] != (
        records[("amd64", "1.4.0")]["candidate"]["config_digest"]
    )
    result = run_aggregate(evidence)
    assert result["status"] == "passed"
    assert result["candidate_version"] == "1.5.3"
    assert result["recovers_unpublished_image_milestone"] == "1.5.0"
    assert [(row["architecture"], row["baseline_image_version"]) for row in result["migrations"]] == [
        ("amd64", "1.4.0"), ("amd64", "1.4.1"), ("arm64", "1.4.0"), ("arm64", "1.4.1")
    ]


def test_candidate_version_must_match_the_recovery_fixture(evidence):
    baselines, descriptor, source, records, directory = evidence
    with pytest.raises(ValueError, match="candidate"):
        load_module().aggregate(
            records,
            baselines=baselines,
            descriptor=descriptor,
            candidate_version="1.5.0",
            source_commit=source,
            archive_sha256="3" * 64,
            descriptor_sha256="4" * 64,
            evidence_directory=directory,
        )


def test_later_patch_can_recover_the_same_canonical_image_milestone(evidence):
    baselines, _descriptor, _source, records, directory = evidence
    baselines["candidate_version"] = "2.7.3"
    baselines["recovers_unpublished_image_milestone"] = "2.7.0"
    for (architecture, baseline_version), receipt in records.items():
        receipt["candidate_version"] = "2.7.3"
        receipt["candidate_status"]["current_version"] = "2.7.3"
        receipt["candidate_status"]["image_version"] = "2.7.3"
        for stage in ("after_replace", "after_restart"):
            path = (
                directory
                / f"native-image-upgrade-{architecture}-{baseline_version}-logs"
                / f"candidate-runtime-info-{stage.replace('_', '-')}.json"
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["application_version"] = "2.7.3"
            data = (
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            path.write_bytes(data)
            receipt["cli_runtime_info"][stage]["sha256"] = hashlib.sha256(
                data
            ).hexdigest()
            receipt["cli_runtime_info"][stage]["identity"][
                "application_version"
            ] = "2.7.3"

    result = run_aggregate(evidence)

    assert result["candidate_version"] == "2.7.3"
    assert result["recovers_unpublished_image_milestone"] == "2.7.0"


def test_candidate_patch_above_recovery_policy_bound_is_rejected(evidence):
    baselines, _descriptor, _source, records, _directory = evidence
    baselines["candidate_version"] = "2.7.10"
    baselines["recovers_unpublished_image_milestone"] = "2.7.0"
    for receipt in records.values():
        receipt["candidate_version"] = "2.7.10"
        receipt["candidate_status"]["current_version"] = "2.7.10"
        receipt["candidate_status"]["image_version"] = "2.7.10"

    with pytest.raises(ValueError, match="recovery milestone"):
        run_aggregate(evidence)


def test_docker_image_id_may_equal_oci_config_digest(evidence):
    descriptor = evidence[1]
    for (architecture, _version), receipt in evidence[3].items():
        platform = next(
            row
            for row in descriptor["platforms"]
            if row["platform"] == "linux/" + architecture
        )
        receipt["candidate"]["image_id"] = platform["config"]["digest"]

    assert run_aggregate(evidence)["status"] == "passed"


@pytest.mark.parametrize("case", ["missing", "extra", "source", "candidate", "status-version",
                                  "status-image-version", "recovery-missing", "recovery-wrong-line",
                                  "recovery-nonzero-patch",
                                  "manifest", "config", "archive",
                                  "descriptor", "image-id-missing", "image-id-malformed",
                                  "image-id-inconsistent", "baseline", "app", "key", "external-key",
                                  "old-id", "restart-id", "old-runtime", "active", "deactivated",
                                  "command", "boolean", "cleanup", "log", "decrypt",
                                  "cli-runtime-missing", "cli-runtime-digest",
                                  "cli-runtime-identity", "cli-runtime-file",
                                  "cli-runtime-schema", "cli-runtime-bool-pid",
                                  "cli-runtime-prefix", "cli-runtime-unloaded-required",
                                  "cli-runtime-loaded-origin",
                                  "cli-runtime-minimal-dependency"])
def test_incomplete_or_unbound_migration_proof_is_rejected(evidence, case):
    records = evidence[3]
    row = records[("arm64", "1.4.0")]
    if case == "missing":
        records.pop(("arm64", "1.4.0"))
    elif case == "extra":
        records[("arm64", "1.4.2")] = copy.deepcopy(row)
    elif case == "source":
        row["source_commit"] = "0" * 40
    elif case == "candidate":
        row["candidate_version"] = "1.5.0"
    elif case == "status-version":
        row["candidate_status"]["current_version"] = "1.5.0"
    elif case == "status-image-version":
        row["candidate_status"]["image_version"] = "1.5.0"
    elif case == "recovery-missing":
        evidence[0].pop("recovers_unpublished_image_milestone")
    elif case == "recovery-wrong-line":
        evidence[0]["recovers_unpublished_image_milestone"] = "2.5.0"
    elif case == "recovery-nonzero-patch":
        evidence[0]["recovers_unpublished_image_milestone"] = "1.5.1"
    elif case == "manifest":
        row["candidate"]["manifest_digest"] = "sha256:" + "0" * 64
    elif case == "config":
        row["candidate"]["config_digest"] = "sha256:" + "0" * 64
    elif case == "archive":
        row["candidate"]["oci_archive_sha256"] = "0" * 64
    elif case == "descriptor":
        row["candidate"]["descriptor_sha256"] = "0" * 64
    elif case == "image-id-missing":
        row["candidate"].pop("image_id")
    elif case == "image-id-malformed":
        row["candidate"]["image_id"] = "sha256:not-a-digest"
    elif case == "image-id-inconsistent":
        row["candidate"]["image_id"] = "sha256:" + "0" * 64
    elif case == "baseline":
        row["baseline"]["platform_manifest_digest"] = "sha256:" + "0" * 64
    elif case == "app":
        row["baseline"]["signed_app_source_commit"] = "0" * 40
    elif case == "key":
        row["preservation_after_replace_sha256"]["/config/encryption.key"] = "0" * 64
    elif case == "external-key":
        row["external_storage_key_sha256"] = ""
    elif case == "old-id":
        row["old_container_id"] = row["replacement_container_id"]
    elif case == "restart-id":
        row["restarted_container_id"] = "0" * 64
    elif case == "old-runtime":
        row["old_status"]["launcher_protocol"] = 4
    elif case == "active":
        row["candidate_status"]["active_bundle"] = row["old_status"]["active_bundle"]
    elif case == "deactivated":
        row["deactivated_active"]["deactivated_reason"] = "wrong"
    elif case == "command":
        row["image_process_commands"]["ui"] = "/config/channelwatch-runtime/releases/old/python --mode ui"
    elif case == "boolean":
        row["headless_browser"] = "true"
    elif case == "cleanup":
        row["cleanup"]["status"] = "failed"
    elif case == "log":
        name = next(iter(row["logs_sha256"]))
        (evidence[4] / "native-image-upgrade-arm64-1.4.0-logs" / (name + ".log")).write_text("tampered")
    elif case == "decrypt":
        row["credential_api_semantics"]["after_restart"] = False
    elif case == "cli-runtime-missing":
        row.pop("cli_runtime_info")
    elif case == "cli-runtime-digest":
        row["cli_runtime_info"]["after_restart"]["sha256"] = "0" * 64
    elif case == "cli-runtime-identity":
        row["cli_runtime_info"]["after_replace"]["identity"][
            "diagnostic_source"
        ] = "selected_application"
    elif case == "cli-runtime-file":
        path = (
            evidence[4]
            / "native-image-upgrade-arm64-1.4.0-logs"
            / "candidate-runtime-info-after-restart.json"
        )
        path.write_text("{}\n", encoding="utf-8")
    elif case == "cli-runtime-schema":
        rewrite_cli_runtime_info(
            evidence, mutation=lambda payload: payload.update(unexpected=True)
        )
    elif case == "cli-runtime-bool-pid":
        rewrite_cli_runtime_info(
            evidence,
            mutation=lambda payload: payload["process_runtime"].update(pid=True),
        )
    elif case == "cli-runtime-prefix":
        rewrite_cli_runtime_info(
            evidence,
            mutation=lambda payload: payload["process_runtime"].update(
                python_prefix="/config/channelwatch-runtime"
            ),
        )
    elif case == "cli-runtime-unloaded-required":
        rewrite_cli_runtime_info(
            evidence,
            mutation=lambda payload: payload["process_runtime"]["dependencies"][
                "cryptography"
            ].update(loaded=False),
        )
    elif case == "cli-runtime-loaded-origin":
        rewrite_cli_runtime_info(
            evidence,
            mutation=lambda payload: payload["process_runtime"]["dependencies"].update(
                requests={
                    "loaded": True,
                    "path": "/config/selected/requests/__init__.py",
                    "version": "test",
                }
            ),
        )
    elif case == "cli-runtime-minimal-dependency":
        rewrite_cli_runtime_info(
            evidence,
            mutation=lambda payload: payload["process_runtime"]["dependencies"].update(
                cryptography={
                    "path": "/venv/lib/python3.14/site-packages/cryptography/__init__.py"
                }
            ),
        )
    with pytest.raises(ValueError):
        run_aggregate(evidence)


def test_runtime_info_bytes_are_read_once_for_hash_and_schema_validation(
    evidence, monkeypatch
):
    original_read_bytes = Path.read_bytes
    reads: dict[Path, int] = {}

    def tracked_read_bytes(path):
        if path.name.startswith("candidate-runtime-info-"):
            reads[path] = reads.get(path, 0) + 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    assert run_aggregate(evidence)["status"] == "passed"
    assert len(reads) == 8
    assert set(reads.values()) == {1}


def test_real_supervisor_commands_from_helper_are_accepted(evidence, monkeypatch):
    helper_spec = importlib.util.spec_from_file_location("real_image_upgrade_helper", ROOT / "scripts/release/verify-image-upgrade.py")
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    import subprocess

    commands = "PID COMMAND\n101 python -u /app/core/runtime_launcher.py core --stay-alive\n202 python -u /app/core/runtime_launcher.py ui --host 0.0.0.0 --port 8501 --log-level warning\n"
    monkeypatch.setattr(helper, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, commands, ""))
    for receipt in evidence[3].values():
        receipt["image_process_commands"] = helper.image_process_commands("owned-candidate")
    assert run_aggregate(evidence)["status"] == "passed"


def test_workflow_requires_old_volume_migrations_before_foundation_publication():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docker-publish.yml").read_text())
    native = workflow["jobs"]["native-image-qualification"]
    steps = native["steps"]
    migration = next(step for step in steps if step.get("name") == "Qualify published older installations on the foundation image")
    verifier = next(step for step in steps if step.get("name") == "Qualify exact native OCI child without rebuilding")
    assert steps.index(verifier) < steps.index(migration)
    assert ".recovers_unpublished_image_milestone // empty" in migration["run"]
    assert ".recovers_unpublished_image_milestone" in migration["run"]
    assert 'test "${RELEASE_TAG}" = "v${version}"' in migration["run"]
    assert '!= "1.5.0"' not in migration["run"]
    assert 'for baseline in 1.4.0 1.4.1' in migration["run"]
    assert "verify-image-upgrade.py" in migration["run"]
    assert "--baseline-app-source-commit" in migration["run"]
    assert "sha256sum --check" in migration["run"]
    assert "setup-qemu" not in str(native)
    assert "native-image-upgrade-" in str(steps[-1]["with"]["path"])
    release = workflow["jobs"]["build-update-bundle-and-release"]
    binding = next(step for step in release["steps"] if step.get("name") == "Bind older-installation migration evidence to the foundation candidate")
    assert "aggregate-image-upgrades.py" in binding["run"]
    assert "--candidate-version \"${version}\"" in binding["run"]
    assert ".recovers_unpublished_image_milestone // empty" in binding["run"]
    assert 'test "${config_version}" = "${version}"' in binding["run"]
    assert "--descriptor" in binding["run"]
    assert "--source-commit" in binding["run"]
    assert "channelwatch-image-upgrade-${TAG}-evidence.json" in str(release)


def test_recovery_migration_assets_are_mandatory_through_public_verification():
    workflow = (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    release = workflow.split("  build-update-bundle-and-release:", 1)[1].split(
        "\n  build-and-push:", 1
    )[0]
    image = workflow.split("  build-and-push:", 1)[1].split(
        "\n  publish-github-release:", 1
    )[0]
    public = workflow.split("  verify-public-release-assets:", 1)[1].split(
        "\n  sync-site:", 1
    )[0]

    assert release.count(".recovers_unpublished_image_milestone // empty") >= 2
    assert 'if [ "${TAG}" = "v1.5.0" ]' not in release
    for name in (
        "channelwatch-image-upgrade-${TAG}-evidence.json",
        "channelwatch-image-upgrade-${TAG}-logs.tar",
        "channelwatch-image-upgrade-${TAG}-${architecture}-${baseline}.json",
    ):
        assert name in release
        assert name in image
    assert ".recovers_unpublished_image_milestone // empty" in image
    assert ".recovers_unpublished_image_milestone // empty" in public
    assert "expected_checksum_count=$((expected_checksum_count + 6))" in public
