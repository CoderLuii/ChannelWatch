import json
import sys
from pathlib import Path

import pytest

from core import runtime_launcher
from core.update_center import (
    RESTART_CONTROL_FILES,
    RUNTIME_ABI,
    RUNTIME_CONTROL_MAX_BYTES,
    UpdateLockedError,
    UpdateManager,
    _runtime_active_metadata,
)


IDENTITY = {
    "runtime_generation_id": "a" * 64,
    "runtime_descriptor_sha256": "b" * 64,
    "dependency_inventory_sha256": "c" * 64,
}


@pytest.fixture(autouse=True)
def _clear_verified_runtime_claim(monkeypatch):
    monkeypatch.setattr(
        sys, "_channelwatch_runtime_generation_identity", None, raising=False
    )


def _activation(path: Path, **overrides) -> dict:
    return {
        "version": "1.4.4",
        "activation_id": "activation-1",
        "path": str(path.resolve()),
        **IDENTITY,
        **overrides,
    }


def _seed_activation(tmp_path: Path) -> tuple[UpdateManager, Path, dict]:
    app_dir = tmp_path / "channelwatch-runtime/releases" / ("a" * 64)
    app_dir.mkdir(parents=True)
    runtime_dir = tmp_path / "channelwatch-runtime"
    active = _activation(app_dir)
    pending = {**active, "job_id": "job-1"}
    (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")
    (runtime_dir / "activation-pending.json").write_text(
        json.dumps(pending), encoding="utf-8"
    )
    (runtime_dir / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "job-1",
                "operation": "apply",
                "status": "restarting",
                "version": "1.4.4",
            }
        ),
        encoding="utf-8",
    )
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.4.4",
        image_version="1.4.1",
        launcher_protocol=3,
    )
    return manager, app_dir, active


def test_verified_runtime_claim_is_single_assignment_and_copy_safe(
    tmp_path: Path, monkeypatch
):
    from core import runtime_identity

    app_dir = tmp_path / "generation"
    app_dir.mkdir()
    claim = {"path": str(app_dir.resolve()), **IDENTITY}

    runtime_identity.set_verified_runtime_claim(claim)
    runtime_identity.set_verified_runtime_claim(dict(claim))
    returned = runtime_identity.get_verified_runtime_claim()
    assert returned == claim
    returned["runtime_generation_id"] = "d" * 64
    assert runtime_identity.get_verified_runtime_claim() == claim

    with pytest.raises(RuntimeError, match="already set"):
        runtime_identity.set_verified_runtime_claim(
            {**claim, "runtime_generation_id": "d" * 64}
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"runtime_generation_id": "d" * 64},
        {"runtime_descriptor_sha256": "d" * 64},
        {"dependency_inventory_sha256": "d" * 64},
        {"path": "/wrong-generation"},
        {"dependency_inventory_sha256": None},
        {"runtime_generation_id": "NOT-A-HASH"},
        {"runtime_trust_sha256": "d" * 64},
    ],
)
def test_activation_records_reject_dependency_identity_mismatch_or_partial(
    tmp_path: Path, mutation: dict
):
    from core.runtime_identity import activation_records_match

    active = _activation(tmp_path)
    pending = {**active, **mutation}

    assert activation_records_match(pending, active) is False


def test_activation_records_preserve_legacy_nullable_path():
    from core.runtime_identity import activation_records_match

    active = {"version": "1.4.3", "activation_id": "legacy", "path": None}
    pending = {"version": "1.4.3", "activation_id": "legacy"}

    assert activation_records_match(pending, active) is True


def test_activation_records_require_matching_well_formed_runtime_trust(tmp_path: Path):
    from core.runtime_identity import activation_records_match

    active = {**_activation(tmp_path), "runtime_trust_sha256": "e" * 64}
    assert activation_records_match(dict(active), active) is True
    assert activation_records_match(
        {**active, "runtime_trust_sha256": "E" * 64}, active
    ) is False
    pending = dict(active)
    pending.pop("runtime_trust_sha256")
    assert activation_records_match(pending, active) is False


def test_readiness_marker_requires_complete_matching_dependency_identity(tmp_path: Path):
    from core.runtime_identity import readiness_marker_matches

    pending = _activation(tmp_path)
    marker = {**pending, "component": "core", "healthy": True}
    assert readiness_marker_matches(marker, pending, component="core") is True

    marker.pop("runtime_descriptor_sha256")
    assert readiness_marker_matches(marker, pending, component="core") is False


def test_launcher_helpers_reject_same_activation_with_different_runtime_identity(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    active = _activation(tmp_path / "generation")
    pending = {**active, "dependency_inventory_sha256": "d" * 64}
    marker = {**pending, "component": "core", "healthy": True}
    (runtime_dir / "activation-core-ready.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    assert runtime_launcher._pending_matches_active(pending, active) is False
    assert runtime_launcher._marker_matches_pending("core", active) is False


def test_dependency_readiness_uses_process_attestation_and_identity_quorum(
    tmp_path: Path, monkeypatch
):
    from core import runtime_identity, update_center

    manager, app_dir, active = _seed_activation(tmp_path)
    diagnostics = iter(
        (
            {"pid": 101, "python_executable": "/runtime/python-core"},
            {"pid": 202, "python_executable": "/runtime/python-ui"},
        )
    )
    monkeypatch.setattr(
        update_center, "runtime_process_diagnostics", lambda: next(diagnostics)
    )
    monkeypatch.setattr(
        sys,
        "_channelwatch_runtime_generation_identity",
        {"path": str(app_dir.resolve()), **IDENTITY},
        raising=False,
    )

    for component in ("core", "ui"):
        manager.record_startup_success(
            component=component,
            running_version="1.4.4",
            activation_id=active["activation_id"],
            healthy=True,
            running_app_path=app_dir,
            verified_runtime_claim=runtime_identity.get_verified_runtime_claim(),
        )

    job = json.loads(manager.job_path.read_text(encoding="utf-8"))
    assert job["status"] == "success"
    assert job["runtime_components"] == {
        "core": {
            **IDENTITY,
            "process_runtime": {
                "pid": 101,
                "python_executable": "/runtime/python-core",
            },
        },
        "ui": {
            **IDENTITY,
            "process_runtime": {
                "pid": 202,
                "python_executable": "/runtime/python-ui",
            },
        },
    }
    assert not manager.activation_pending_path.exists()


def test_legacy_readiness_omits_process_runtime_diagnostics(
    tmp_path: Path, monkeypatch
):
    from core import update_center

    app_dir = tmp_path / "legacy-generation"
    app_dir.mkdir()
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    active = {
        "version": "1.4.4",
        "activation_id": "legacy-activation",
        "path": str(app_dir.resolve()),
    }
    (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")
    (runtime_dir / "activation-pending.json").write_text(
        json.dumps({**active, "job_id": "legacy-job"}), encoding="utf-8"
    )
    manager = UpdateManager(config_dir=tmp_path, current_version="1.4.4")
    monkeypatch.setattr(
        update_center,
        "runtime_process_diagnostics",
        lambda: pytest.fail("legacy readiness must not collect runtime diagnostics"),
    )

    manager.record_startup_success(
        component="core",
        running_version="1.4.4",
        activation_id="legacy-activation",
        healthy=True,
    )

    marker = json.loads(
        manager.activation_ready_path("core").read_text(encoding="utf-8")
    )
    assert "process_runtime" not in marker


def test_oversized_process_diagnostics_cannot_complete_or_claim_activation(
    tmp_path: Path, monkeypatch
):
    from core import runtime_identity, update_center

    manager, app_dir, active = _seed_activation(tmp_path)
    claim = {"path": str(app_dir.resolve()), **IDENTITY}
    monkeypatch.setattr(
        sys, "_channelwatch_runtime_generation_identity", claim, raising=False
    )
    monkeypatch.setattr(
        update_center,
        "runtime_process_diagnostics",
        lambda: {"python_executable": "x" * 20_000},
    )

    for component in ("core", "ui"):
        manager.record_startup_success(
            component=component,
            running_version="1.4.4",
            activation_id=active["activation_id"],
            healthy=True,
            running_app_path=app_dir,
            verified_runtime_claim=runtime_identity.get_verified_runtime_claim(),
        )

    assert manager.activation_pending_path.exists()
    assert not list(manager.runtime_dir.glob("activation-completed-*.json"))
    assert json.loads(manager.job_path.read_text(encoding="utf-8"))["status"] != (
        "success"
    )


def test_wrong_process_runtime_identity_cannot_publish_readiness(
    tmp_path: Path, monkeypatch
):
    from core import runtime_identity

    manager, app_dir, active = _seed_activation(tmp_path)
    monkeypatch.setattr(
        sys,
        "_channelwatch_runtime_generation_identity",
        {
            "path": str(app_dir.resolve()),
            **IDENTITY,
            "runtime_descriptor_sha256": "d" * 64,
        },
        raising=False,
    )

    manager.record_startup_success(
        component="core",
        running_version="1.4.4",
        activation_id=active["activation_id"],
        healthy=True,
        running_app_path=app_dir,
        verified_runtime_claim=runtime_identity.get_verified_runtime_claim(),
    )

    assert not manager.activation_ready_path("core").exists()
    assert json.loads(manager.job_path.read_text(encoding="utf-8"))["status"] != "success"


def test_large_runtime_descriptor_stays_out_of_bounded_restart_journal(tmp_path: Path):
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.4.3",
        image_version="1.4.1",
        launcher_protocol=3,
    )
    descriptor = {
        "source_commit": "d" * 40,
        "bundle_format": "channelwatch-runtime-generation-v1",
        "files": [
            {
                "path": f"dependencies/site-packages/package_{index:04d}.py",
                "sha256": f"{index:064x}",
                "size": 1,
                "mode": "0644",
            }
            for index in range(2750)
        ],
    }
    assert len(json.dumps(descriptor, indent=2).encode("utf-8")) > (
        RUNTIME_CONTROL_MAX_BYTES
    )
    metadata = _runtime_active_metadata(
        {
            "version": "1.4.4",
            "version_tag": "v1.4.4",
            "runtime_abi": RUNTIME_ABI,
            "settings_schema_version": 7,
        },
        descriptor,
    )
    active = {
        "version": "1.4.4",
        "activation_id": "activation-1",
        "path": str((tmp_path / "generation").resolve()),
        **IDENTITY,
        "metadata": metadata,
    }
    control = {name: None for name in RESTART_CONTROL_FILES}
    control["active.json"] = active
    journal = manager._build_restart_journal(
        reason="runtime_transition",
        operation="apply",
        phase="commit",
        job_id="job-1",
        source_active=None,
        control=control,
    )

    serialized = json.dumps(journal, indent=2).encode("utf-8")
    assert len(serialized) <= RUNTIME_CONTROL_MAX_BYTES
    assert set(metadata) == {
        "version",
        "version_tag",
        "runtime_abi",
        "settings_schema_version",
        "git_sha",
        "bundle_type",
    }
    assert "files" not in journal["control"]["active.json"]["metadata"]


def test_oversized_restart_journal_is_rejected_before_publication(tmp_path: Path):
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.4.3",
        image_version="1.4.1",
        launcher_protocol=3,
    )
    control = {name: None for name in RESTART_CONTROL_FILES}
    control["active.json"] = {
        "version": "1.4.4",
        "activation_id": "activation-1",
        "path": str((tmp_path / "generation").resolve()),
        "metadata": {"oversized": "x" * RUNTIME_CONTROL_MAX_BYTES},
    }
    journal = manager._build_restart_journal(
        reason="runtime_transition",
        operation="apply",
        phase="commit",
        job_id="job-1",
        source_active=None,
        control=control,
    )

    with pytest.raises(UpdateLockedError, match="exceeds the safe size limit"):
        manager._write_restart_journal(journal)

    assert not manager.restart_required_path.exists()
    assert not list(manager.runtime_dir.glob(".restart-required.json.candidate-*"))
