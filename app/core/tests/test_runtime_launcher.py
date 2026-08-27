import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import runtime_launcher
from core.update_center import (
    RUNTIME_ABI,
    UpdateManager,
    launcher_compatibility_status,
    resolve_active_app_dir,
)

_START_RESTART_REQUIRED_WATCHDOG = runtime_launcher.start_restart_required_watchdog


def test_selected_bundle_replaces_image_owned_sqlmodel_registry(tmp_path: Path):
    """A protocol-3 bundle can import models after image-side update lookup."""

    config_dir = tmp_path / "config"
    selected_bundle = config_dir / "channelwatch-runtime" / "releases" / "v1.0.1"
    selected_bundle.mkdir(parents=True)
    probe = """
import importlib
import os
import sys

os.environ.pop("CHANNELWATCH_APP_DIR", None)
import core
import core.storage.models
from sqlmodel import SQLModel

assert "dvr_server" in SQLModel.metadata.tables
os.environ["CONFIG_PATH"] = sys.argv[1]
os.environ["CHANNELWATCH_IMAGE_APP_DIR"] = "/app"
os.environ["CHANNELWATCH_APP_DIR"] = sys.argv[2]
assert core._reset_sqlmodel_registry_for_selected_bundle() is True
assert not SQLModel.metadata.tables
for name in tuple(sys.modules):
    if name == "core.storage" or name.startswith("core.storage."):
        sys.modules.pop(name, None)
importlib.invalidate_caches()
models = importlib.import_module("core.storage.models")
assert models.DvrServer.__table__.name == "dvr_server"
assert "dvr_server" in SQLModel.metadata.tables
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe, str(config_dir), str(selected_bundle)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "app")},
    )

    assert completed.returncode == 0, completed.stderr


class _SimulatedPowerLoss(BaseException):
    pass


_RESTART_REPLAY_PHASES = (
    "journal",
    "activation-state-removed",
    "control:rollback.json",
    "control:activation-pending.json",
    "control:activation-core-ready.json",
    "control:activation-ui-ready.json",
    "control:update-job.json",
    "control:active.json",
    "control:fsynced",
)


@pytest.fixture(autouse=True)
def _disable_restart_required_watchdog(monkeypatch):
    """Keep launcher unit tests from leaving a polling daemon behind."""

    monkeypatch.setattr(
        runtime_launcher, "start_restart_required_watchdog", lambda: None
    )


def _bundle(root: Path, version: str = "0.9.10") -> Path:
    bundle_dir = root / "channelwatch-runtime" / "releases" / f"v{version}"
    (bundle_dir / "core").mkdir(parents=True)
    (bundle_dir / "ui" / "backend").mkdir(parents=True)
    (bundle_dir / "core" / "main.py").write_text("core")
    (bundle_dir / "ui" / "backend" / "main.py").write_text("ui")
    return bundle_dir


def _write_active(
    config_dir: Path,
    bundle_dir: Path,
    *,
    version: str = "0.9.10",
    abi: str = RUNTIME_ABI,
    schema: int = 7,
):
    active = {
        "version": version,
        "path": str(bundle_dir),
        "runtime_abi": abi,
        "settings_schema_version": schema,
    }
    active_path = config_dir / "channelwatch-runtime" / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(active))


@pytest.mark.parametrize("image_version", ["0.9.9", "0.9.10", "unknown"])
def test_launcher_compatibility_rejects_images_without_a_safe_bridge(image_version):
    status = launcher_compatibility_status(image_version=image_version)

    assert status["minimum_safe_image_version"] == "0.9.11"
    assert status["safe_for_app_updates"] is False


@pytest.mark.parametrize("image_version", ["0.9.11", "0.9.15", "0.9.18"])
def test_launcher_compatibility_accepts_verified_bridge_images(image_version):
    status = launcher_compatibility_status(image_version=image_version)

    assert status["minimum_safe_image_version"] == "0.9.11"
    assert status["safe_for_app_updates"] is True


def test_newer_compatible_active_bundle_wins(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path)
    _write_active(tmp_path, bundle_dir)

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.9",
        settings_schema_version=7,
    )

    assert selection.source == "bundle"
    assert selection.app_dir == bundle_dir.resolve()


def test_image_wins_when_image_version_is_current_or_newer(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path, "0.9.10")
    _write_active(tmp_path, bundle_dir, version="0.9.10")

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.10",
        settings_schema_version=7,
    )

    assert selection.source == "image"
    assert selection.reason == "image-version-is-current-or-newer"
    assert not (tmp_path / "channelwatch-runtime" / "active.json").exists()
    assert (tmp_path / "channelwatch-runtime" / "deactivated-active.json").exists()


def test_abi_mismatch_falls_back_to_image(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path)
    _write_active(tmp_path, bundle_dir, abi="other-runtime")

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.9",
        settings_schema_version=7,
    )

    assert selection.source == "image"
    assert selection.reason == "active-bundle-abi-mismatch"


def test_corrupt_or_missing_active_bundle_falls_back_to_image(tmp_path: Path):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(exist_ok=True)
    (runtime_dir / "active.json").write_text("{not json")

    selection = resolve_active_app_dir(
        config_dir=tmp_path,
        image_app_dir=image_dir,
        image_version="0.9.9",
        settings_schema_version=7,
    )

    assert selection.source == "image"
    assert selection.reason == "no-active-bundle"


def test_runtime_launcher_records_failed_activation_and_restores_previous_bundle(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    active_path = runtime_dir / "active.json"
    rollback_path = runtime_dir / "rollback.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(
        json.dumps(
            {
                "version": "0.9.10",
                "path": str(current_dir),
                "runtime_abi": RUNTIME_ABI,
                "settings_schema_version": 7,
            }
        )
    )
    rollback_path.write_text(
        json.dumps(
            {
                "previous_active": {
                    "version": "0.9.9",
                    "path": str(previous_dir),
                    "runtime_abi": RUNTIME_ABI,
                    "settings_schema_version": 7,
                }
            }
        )
    )

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    runtime_launcher.rollback_failed_activation("import exploded")

    active = json.loads(active_path.read_text())
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert active["version"] == "0.9.9"
    assert job["status"] == "failed"
    assert job["rollback_applied"] is True
    assert job["rolled_back_from"] == "0.9.10"
    assert job["rolled_back_to"] == "0.9.9"
    restart_required = json.loads((runtime_dir / "restart-required.json").read_text())
    assert restart_required["schema"] == 2
    assert restart_required["reason"] == "activation_rollback"
    assert restart_required["operation"] == "activation_rollback"
    assert restart_required["phase"] == "commit"
    assert restart_required["source_active"]["version"] == "0.9.10"
    assert restart_required["control"]["active.json"]["version"] == "0.9.9"
    assert restart_required["control"]["update-job.json"] == job


def test_protocol_one_activation_rollback_restores_controls_without_journal(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.18")
    previous_dir = _bundle(tmp_path, "0.9.17")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "active.json").write_text(
        json.dumps({"version": "0.9.18", "path": str(current_dir)}),
        encoding="utf-8",
    )
    (runtime_dir / "rollback.json").write_text(
        json.dumps(
            {
                "previous_active": {
                    "version": "0.9.17",
                    "path": str(previous_dir),
                }
            }
        ),
        encoding="utf-8",
    )
    for name in (
        runtime_launcher.ACTIVATION_PENDING_FILE,
        "activation-core-ready.json",
        "activation-ui-ready.json",
    ):
        (runtime_dir / name).write_text("{}", encoding="utf-8")

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.15")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    runtime_launcher.rollback_failed_activation("protocol one startup failed")

    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.17"
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["status"] == "failed"
    )
    assert not (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).exists()
    assert not (runtime_dir / "activation-core-ready.json").exists()
    assert not (runtime_dir / "activation-ui-ready.json").exists()
    assert not (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).exists()


@pytest.mark.parametrize("crash_phase", _RESTART_REPLAY_PHASES)
def test_runtime_activation_rollback_power_loss_is_replayable_and_blocks_launch(
    tmp_path: Path, monkeypatch, crash_phase: str
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setattr(runtime_launcher, "_restart_transition_checkpoint", crash)
    with pytest.raises(_SimulatedPowerLoss, match=crash_phase):
        runtime_launcher.rollback_failed_activation("simulated startup failure")

    journal = json.loads(
        (runtime_dir / "restart-required.json").read_text(encoding="utf-8")
    )
    assert set(journal) == runtime_launcher.RESTART_JOURNAL_FIELDS
    assert journal["schema"] == 2
    assert journal["operation"] == "activation_rollback"
    assert journal["phase"] == "commit"
    assert set(journal["control"]) == set(runtime_launcher.RESTART_CONTROL_FILES)

    monkeypatch.setattr(
        runtime_launcher, "_restart_transition_checkpoint", lambda _phase: None
    )
    monkeypatch.setattr(
        runtime_launcher,
        "time",
        SimpleNamespace(sleep=lambda _delay: None),
    )
    monkeypatch.setattr(runtime_launcher, "request_container_restart", lambda: None)
    monkeypatch.setattr(
        runtime_launcher,
        "selected_app_dir",
        lambda: pytest.fail("a journaled rollback must block pinned app launch"),
    )
    assert runtime_launcher.main(["core"]) == 1
    runtime_launcher.apply_restart_journal()
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.9"
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["status"] == "failed"
    )


def _write_pending_activation(
    runtime_dir: Path,
    current_dir: Path,
    previous_dir: Path,
    *,
    activation_id: str = "activation-1",
) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "active.json").write_text(
        json.dumps(
            {
                "version": "0.9.10",
                "activation_id": activation_id,
                "path": str(current_dir),
                "runtime_abi": RUNTIME_ABI,
                "settings_schema_version": 7,
            }
        )
    )
    (runtime_dir / "rollback.json").write_text(
        json.dumps(
            {
                "previous_active": {
                    "version": "0.9.9",
                    "path": str(previous_dir),
                    "runtime_abi": RUNTIME_ABI,
                    "settings_schema_version": 7,
                }
            }
        )
    )
    (runtime_dir / "activation-pending.json").write_text(
        json.dumps(
            {
                "job_id": "job-1",
                "version": "0.9.10",
                "activation_id": activation_id,
                "path": str(current_dir),
                "deadline_at": (datetime.now(timezone.utc) - timedelta(seconds=1))
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
    )


def test_bundle_startup_failure_requests_whole_container_restart(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    runtime_dir = tmp_path / "channelwatch-runtime"
    _write_pending_activation(runtime_dir, bundle_dir, previous_dir)
    restart_requests: list[bool] = []

    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: bundle_dir)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher, "start_activation_watchdog", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda _args: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )

    assert runtime_launcher.main(["core", "--stay-alive"]) == 1
    assert restart_requests == [True]
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.9"
    assert (runtime_dir / "restart-required.json").is_file()


def test_restart_required_sentinel_blocks_and_retries_each_child_launch(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(exist_ok=True)
    sentinel = runtime_dir / "restart-required.json"
    sentinel.write_text("{malformed but still fail closed", encoding="utf-8")
    restart_attempts: list[bool] = []
    delays: list[float] = []

    def reject_restart():
        restart_attempts.append(True)
        raise RuntimeError("supervisor unavailable")

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "time",
        SimpleNamespace(sleep=delays.append),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        reject_restart,
    )
    monkeypatch.setattr(
        runtime_launcher,
        "selected_app_dir",
        lambda: pytest.fail("application selection must remain blocked"),
    )

    assert runtime_launcher.main(["core"]) == 1
    assert runtime_launcher.main(["ui"]) == 1
    assert restart_attempts == [True, True]
    assert delays == [runtime_launcher.RESTART_REQUIRED_PRELAUNCH_DELAY_SECONDS] * 2
    assert all(delay > 1.0 for delay in delays)
    assert sentinel.is_file()


def _write_protocol_three_apply_journal(
    runtime_dir: Path,
    target_dir: Path,
    *,
    operation: str = "apply",
) -> dict[str, object]:
    active = {
        "version": "0.9.19",
        "path": str(target_dir),
        "activation_id": "protocol-three-activation",
        "runtime_abi": RUNTIME_ABI,
        "settings_schema_version": 7,
    }
    pending = {
        **active,
        "job_id": "protocol-three-job",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=120))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    control = {
        "active.json": active,
        "rollback.json": {
            "target_version": "0.9.19",
            "previous_active": None,
        },
        "activation-pending.json": pending,
        "activation-core-ready.json": None,
        "activation-ui-ready.json": None,
        "update-job.json": {
            "job_id": "protocol-three-job",
            "operation": "apply",
            "status": "restarting",
            "version": "0.9.19",
        },
    }
    journal = runtime_launcher._build_restart_journal(
        reason=(
            "activation_rollback"
            if operation == "activation_rollback"
            else "runtime_transition"
        ),
        operation=operation,
        phase="commit",
        job_id="protocol-three-job",
        source_active=None,
        control=control,
    )
    runtime_launcher._write_restart_journal(journal)
    return journal


def _protocol_three_old_processes() -> dict[str, dict[str, int]]:
    return {
        "core": {"pid": 101, "start": 1_700_000_001},
        "ui": {"pid": 102, "start": 1_700_000_002},
    }


def test_protocol_three_child_consumes_valid_restart_journal_before_launch(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    restart_attempts: list[bool] = []

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    journal = _write_protocol_three_apply_journal(runtime_dir, target_dir)
    assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
        old_processes=_protocol_three_old_processes()
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_attempts.append(True),
    )

    assert runtime_launcher.handoff_required_restart_before_launch() is False
    assert restart_attempts == []
    assert not (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).exists()
    assert (
        json.loads((runtime_dir / "active.json").read_text())
        == journal["control"]["active.json"]
    )
    assert json.loads((runtime_dir / "activation-pending.json").read_text()) == (
        journal["control"]["activation-pending.json"]
    )
    assert json.loads((runtime_dir / "update-job.json").read_text())["status"] == (
        "restarting"
    )
    assert runtime_launcher.handoff_required_restart_before_launch() is False


def test_protocol_three_journal_acknowledgement_is_power_loss_replayable(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    journal = _write_protocol_three_apply_journal(runtime_dir, target_dir)
    assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
        old_processes=_protocol_three_old_processes()
    )

    def crash(phase: str) -> None:
        if phase == "journal:before-clear":
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "_restart_transition_checkpoint", crash)

    with pytest.raises(_SimulatedPowerLoss, match="journal:before-clear"):
        runtime_launcher.consume_protocol_three_restart_journal_before_launch()

    assert (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).is_file()
    assert (
        json.loads((runtime_dir / "active.json").read_text())
        == journal["control"]["active.json"]
    )

    monkeypatch.setattr(
        runtime_launcher, "_restart_transition_checkpoint", lambda _phase: None
    )
    assert not (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).exists()
    assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
        old_processes=_protocol_three_old_processes()
    )
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
    assert not (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).exists()


def test_protocol_three_child_cannot_consume_before_restart_handoff_is_accepted(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    restart_attempts: list[bool] = []

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "time",
        SimpleNamespace(sleep=lambda _delay: None),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_attempts.append(True),
    )

    assert runtime_launcher.handoff_required_restart_before_launch() is True
    assert restart_attempts == [True]
    assert (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).is_file()
    assert not (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).exists()


def test_protocol_three_recovers_abandoned_same_inode_journal_candidate(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    journal = _write_protocol_three_apply_journal(runtime_dir, target_dir)
    canonical = runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE
    abandoned = runtime_dir / ".restart-required.json.candidate-crashed"
    os.link(canonical, abandoned)

    assert canonical.stat().st_nlink == 2
    assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
        old_processes=_protocol_three_old_processes()
    )
    assert not abandoned.exists()
    assert canonical.stat().st_nlink == 1
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
    assert not canonical.exists()
    assert (
        json.loads((runtime_dir / "active.json").read_text())
        == journal["control"]["active.json"]
    )


def test_protocol_three_never_follows_nonregular_abandoned_candidate(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)
    candidate = runtime_dir / ".restart-required.json.candidate-untrusted"
    candidate.symlink_to(runtime_dir / "missing-target")

    assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
        old_processes=_protocol_three_old_processes()
    )
    assert candidate.is_symlink()
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
    assert candidate.is_symlink()


def test_protocol_three_concurrent_children_consume_one_accepted_journal(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)
    assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
        old_processes=_protocol_three_old_processes()
    )
    barrier = threading.Barrier(3)
    results: list[bool] = []
    failures: list[BaseException] = []

    def consume() -> None:
        try:
            barrier.wait()
            results.append(
                runtime_launcher.consume_protocol_three_restart_journal_before_launch()
            )
        except BaseException as exc:
            failures.append(exc)

    workers = [threading.Thread(target=consume) for _index in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=5)

    assert failures == []
    assert all(not worker.is_alive() for worker in workers)
    assert sorted(results) == [False, True]
    assert not (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).exists()
    assert not (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).exists()


@pytest.mark.parametrize("operation", ["manual_rollback", "activation_rollback"])
def test_protocol_three_consumes_accepted_rollback_journal(
    tmp_path: Path, monkeypatch, operation: str
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    target_dir = _bundle(tmp_path, "0.9.17")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(
        runtime_dir,
        target_dir,
        operation=operation,
    )

    assert runtime_launcher.accept_protocol_three_restart_handoff_if_present(
        old_processes=_protocol_three_old_processes()
    )
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()
    assert not (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).exists()


def test_protocol_three_malformed_restart_journal_remains_fail_closed(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir(exist_ok=True)
    sentinel = runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE
    sentinel.write_text("{malformed", encoding="utf-8")
    restart_attempts: list[bool] = []

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "time",
        SimpleNamespace(sleep=lambda _delay: None),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_attempts.append(True),
    )

    assert runtime_launcher.handoff_required_restart_before_launch() is True
    assert restart_attempts == [True]
    assert sentinel.is_file()


@pytest.mark.parametrize("sentinel_kind", ["directory", "broken-symlink"])
def test_any_restart_journal_filesystem_object_blocks_application_launch(
    tmp_path: Path, monkeypatch, sentinel_kind: str
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    sentinel = runtime_dir / "restart-required.json"
    if sentinel_kind == "directory":
        sentinel.mkdir()
    else:
        sentinel.symlink_to(runtime_dir / "missing-journal-target.json")
    restart_attempts: list[bool] = []

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "time",
        SimpleNamespace(sleep=lambda _delay: None),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_attempts.append(True),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "selected_app_dir",
        lambda: pytest.fail("application selection must remain blocked"),
    )

    assert runtime_launcher.main(["core"]) == 1
    assert restart_attempts == [True]
    assert runtime_launcher.restart_journal_present() is True


def test_restart_required_watchdog_hands_off_sentinel_created_after_start(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    captured: dict[str, object] = {}
    restart_attempts: list[bool] = []
    signals: list[tuple[int, int]] = []

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            captured.update(target=target, daemon=daemon, name=name)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_attempts.append(True),
    )
    monkeypatch.setattr(runtime_launcher.os, "getpid", lambda: 4321)
    monkeypatch.setattr(
        runtime_launcher.os,
        "kill",
        lambda pid, signum: signals.append((pid, signum)),
    )

    thread = _START_RESTART_REQUIRED_WATCHDOG()
    assert isinstance(thread, FakeThread)
    assert captured == {
        "target": runtime_launcher._restart_required_watchdog_loop,
        "daemon": True,
        "name": "restart-required-watchdog",
        "started": True,
    }

    sentinel = runtime_dir / "restart-required.json"
    sentinel.write_text("{}", encoding="utf-8")
    captured["target"]()

    assert restart_attempts == [True]
    assert signals == [(4321, runtime_launcher.signal.SIGTERM)]
    assert sentinel.is_file()


def test_restart_required_watchdog_terminates_child_when_handoff_fails(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    sentinel = runtime_dir / "restart-required.json"
    sentinel.write_text("{}", encoding="utf-8")
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: (_ for _ in ()).throw(RuntimeError("supervisor unavailable")),
    )
    monkeypatch.setattr(runtime_launcher.os, "getpid", lambda: 4321)
    monkeypatch.setattr(
        runtime_launcher.os,
        "kill",
        lambda pid, signum: signals.append((pid, signum)),
    )

    runtime_launcher._restart_required_watchdog_loop()

    assert signals == [(4321, runtime_launcher.signal.SIGTERM)]
    assert sentinel.is_file()


def test_runtime_rollback_restores_failed_selection_if_sentinel_write_fails(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    failed_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, failed_dir, previous_dir)
    original_link = runtime_launcher.os.link

    def fail_sentinel(source, destination):
        if Path(destination) == runtime_dir / "restart-required.json":
            raise OSError("sentinel storage unavailable")
        return original_link(source, destination)

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher.os, "link", fail_sentinel)

    with pytest.raises(OSError, match="sentinel storage unavailable"):
        runtime_launcher.rollback_failed_activation("startup failed")

    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.10"
    assert (runtime_dir / "activation-pending.json").is_file()
    assert not (runtime_dir / "restart-required.json").exists()


def test_bundle_system_exit_requests_whole_container_restart(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    runtime_dir = tmp_path / "channelwatch-runtime"
    _write_pending_activation(runtime_dir, bundle_dir, previous_dir)
    restart_requests: list[bool] = []

    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: bundle_dir)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher, "start_activation_watchdog", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_launcher,
        "run_ui",
        lambda _args: (_ for _ in ()).throw(SystemExit(3)),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )

    assert runtime_launcher.main(["ui"]) == 1
    assert restart_requests == [True]


def test_bundle_failure_restores_pending_claim_when_rollback_write_fails(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    runtime_dir = tmp_path / "channelwatch-runtime"
    _write_pending_activation(runtime_dir, bundle_dir, previous_dir)

    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: bundle_dir)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher, "start_activation_watchdog", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda _args: (_ for _ in ()).throw(RuntimeError("startup failed")),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "rollback_failed_activation",
        lambda _error, **_kwargs: (_ for _ in ()).throw(
            OSError("config storage unavailable")
        ),
    )

    with pytest.raises(OSError, match="storage unavailable"):
        runtime_launcher.main(["core"])

    assert (runtime_dir / "activation-pending.json").exists()
    assert not list(runtime_dir.glob("activation-failed-launcher-*.json"))


def test_core_launcher_passes_option_like_args_to_core(tmp_path: Path, monkeypatch):
    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda args: captured.setdefault("app_args", list(args.app_args)),
    )

    assert runtime_launcher.main(["core", "--stay-alive"]) == 0
    assert captured["app_args"] == ["--stay-alive"]


def test_ui_launcher_rejects_unknown_args(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)

    try:
        runtime_launcher.main(["ui", "--bogus"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("UI launcher accepted an unknown argument")


def test_ui_launcher_disables_uvicorn_forwarded_header_preprocessing(monkeypatch):
    import uvicorn

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    runtime_launcher.run_ui(
        SimpleNamespace(host="127.0.0.1", port=18501, log_level="warning")
    )

    assert calls == [
        (
            ("ui.backend.main:app",),
            {
                "host": "127.0.0.1",
                "port": 18501,
                "log_level": "warning",
                "proxy_headers": False,
            },
        )
    ]


def test_local_development_docs_use_the_hardened_ui_launcher():
    repository_dir = Path(__file__).resolve().parents[3]
    instructions = (
        repository_dir / "docs" / "how-to" / "run-local-development.md"
    ).read_text(encoding="utf-8")

    assert "python -m core.runtime_launcher ui" in instructions
    assert "python -m uvicorn ui.backend.main:app" not in instructions


def test_activation_watchdog_rolls_back_when_component_marker_is_missing(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    restart_requests: list[bool] = []

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )

    assert runtime_launcher.enforce_activation_deadline() is True
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.9"
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["job_id"] == "job-1"
    )
    assert restart_requests == [True]
    assert not (runtime_dir / "activation-pending.json").exists()


def test_activation_watchdog_rejects_stale_readiness_marker(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    marker = {
        "version": "0.9.10",
        "activation_id": "activation-1",
        "path": str(current_dir),
        "healthy": True,
    }
    (runtime_dir / "activation-core-ready.json").write_text(
        json.dumps({**marker, "component": "core"})
    )
    (runtime_dir / "activation-ui-ready.json").write_text(
        json.dumps({**marker, "component": "ui", "activation_id": "old"})
    )
    restart_requests: list[bool] = []

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )

    assert runtime_launcher.enforce_activation_deadline() is True
    assert restart_requests == [True]


def test_activation_watchdog_recovers_success_when_both_components_are_ready(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    marker = {
        "version": "0.9.10",
        "activation_id": "activation-1",
        "path": str(current_dir),
        "healthy": True,
    }
    for component in ("core", "ui"):
        (runtime_dir / f"activation-{component}-ready.json").write_text(
            json.dumps({**marker, "component": component})
        )
    restart_requests: list[bool] = []

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )

    assert runtime_launcher.enforce_activation_deadline() is False
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.10"
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["status"] == "success"
    )
    assert restart_requests == []
    assert not (runtime_dir / "activation-pending.json").exists()


def test_deadline_claim_wins_before_late_quorum_without_live_claim_recovery(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    marker = {
        "version": "0.9.10",
        "activation_id": "activation-1",
        "path": str(current_dir),
        "healthy": True,
    }
    (runtime_dir / "activation-core-ready.json").write_text(
        json.dumps({**marker, "component": "core"})
    )
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "request_container_restart", lambda: None)

    deadline_claimed = threading.Event()
    release_deadline = threading.Event()
    original_claim = runtime_launcher._claim_pending

    def pause_after_deadline_claim(pending, *, claimant):
        claim = original_claim(pending, claimant=claimant)
        if claimant == "failed-watchdog" and claim is not None:
            deadline_claimed.set()
            assert release_deadline.wait(timeout=5)
        return claim

    monkeypatch.setattr(runtime_launcher, "_claim_pending", pause_after_deadline_claim)
    deadline_result: list[bool] = []
    readiness_finished = threading.Event()
    errors: list[BaseException] = []

    def run_deadline() -> None:
        try:
            deadline_result.append(runtime_launcher.enforce_activation_deadline())
        except BaseException as exc:
            errors.append(exc)

    def record_ui_ready() -> None:
        try:
            UpdateManager(
                config_dir=tmp_path,
                current_version="0.9.10",
            ).record_startup_success(
                component="ui",
                running_version="0.9.10",
                activation_id="activation-1",
                healthy=True,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            readiness_finished.set()

    deadline_thread = threading.Thread(target=run_deadline)
    deadline_thread.start()
    assert deadline_claimed.wait(timeout=5)
    readiness_thread = threading.Thread(target=record_ui_ready)
    readiness_thread.start()
    assert not readiness_finished.wait(timeout=0.1)

    release_deadline.set()
    deadline_thread.join(timeout=5)
    readiness_thread.join(timeout=5)

    assert not deadline_thread.is_alive()
    assert not readiness_thread.is_alive()
    assert errors == []
    assert deadline_result == [True]
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.9"
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["status"] == "failed"
    )
    assert (runtime_dir / "restart-required.json").is_file()


def test_quorum_claim_wins_before_deadline_and_cannot_be_rolled_back(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    marker = {
        "version": "0.9.10",
        "activation_id": "activation-1",
        "path": str(current_dir),
        "healthy": True,
    }
    (runtime_dir / "activation-core-ready.json").write_text(
        json.dumps({**marker, "component": "core"})
    )
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "request_container_restart", lambda: None)

    healthcheck_entered = threading.Event()
    release_healthcheck = threading.Event()

    def healthy_after_deadline_waits() -> bool:
        healthcheck_entered.set()
        assert release_healthcheck.wait(timeout=5)
        return True

    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.10",
        healthcheck_callable=healthy_after_deadline_waits,
    )
    deadline_result: list[bool] = []
    errors: list[BaseException] = []

    def record_ui_ready() -> None:
        try:
            manager.record_startup_success(
                component="ui",
                running_version="0.9.10",
                activation_id="activation-1",
                healthy=True,
            )
        except BaseException as exc:
            errors.append(exc)

    def run_deadline() -> None:
        try:
            deadline_result.append(runtime_launcher.enforce_activation_deadline())
        except BaseException as exc:
            errors.append(exc)

    readiness_thread = threading.Thread(target=record_ui_ready)
    readiness_thread.start()
    assert healthcheck_entered.wait(timeout=5)
    deadline_thread = threading.Thread(target=run_deadline)
    deadline_thread.start()

    release_healthcheck.set()
    readiness_thread.join(timeout=5)
    deadline_thread.join(timeout=5)

    assert not readiness_thread.is_alive()
    assert not deadline_thread.is_alive()
    assert errors == []
    assert deadline_result == [False]
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.10"
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["status"] == "success"
    )
    assert not (runtime_dir / "restart-required.json").exists()


def test_abandoned_outcome_lock_inode_does_not_block_claim_crash_recovery(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending = json.loads(
        (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).read_text()
    )
    claim = runtime_launcher._claim_pending(pending, claimant="crashed-owner")
    assert claim is not None

    # The stable inode is deliberately never unlinked. A process crash releases
    # the kernel flock, so a later launcher can use the same inode to recover the
    # claimant-specific transaction record.
    with runtime_launcher._activation_outcome_lock():
        pass
    outcome_lock = runtime_dir / runtime_launcher.ACTIVATION_OUTCOME_LOCK_FILE
    assert outcome_lock.is_file()
    assert runtime_launcher.is_pending_activation(current_dir) is True
    assert (
        json.loads((runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).read_text())
        == pending
    )


def test_outcome_lock_rejects_hard_link_without_chmodding_external_inode(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    external = tmp_path / "outside-lock"
    external.write_bytes(b"external lock bytes")
    external.chmod(0o640)
    original_bytes = external.read_bytes()
    original_mode = external.stat().st_mode
    original_owner = (external.stat().st_uid, external.stat().st_gid)
    os.link(
        external,
        runtime_dir / runtime_launcher.ACTIVATION_OUTCOME_LOCK_FILE,
    )
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    with pytest.raises(RuntimeError, match="single-link regular file"):
        with runtime_launcher._activation_outcome_lock():
            pytest.fail("hard-linked lock must never be acquired")

    assert external.read_bytes() == original_bytes
    assert external.stat().st_mode == original_mode
    assert (external.stat().st_uid, external.stat().st_gid) == original_owner


def test_successfully_activated_bundle_crash_does_not_request_rollback(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    bundle_dir = _bundle(tmp_path, "0.9.10")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "active.json").write_text(
        json.dumps(
            {
                "version": "0.9.10",
                "activation_id": "activation-1",
                "path": str(bundle_dir),
            }
        )
    )
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)

    rollback_requests: list[str] = []
    restart_requests: list[bool] = []
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", tmp_path / "image")
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: bundle_dir)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda _args: (_ for _ in ()).throw(RuntimeError("post-success crash")),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "rollback_failed_activation",
        lambda error: rollback_requests.append(error),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )

    assert runtime_launcher.is_pending_activation(bundle_dir) is False
    assert runtime_launcher.main(["core"]) == 1
    assert rollback_requests == []
    assert restart_requests == []


def test_protocol_three_post_success_crash_enters_official_recovery_mode(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    bundle_dir = _bundle(tmp_path, "0.9.18")
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    digest = "a" * 64
    _write_active(tmp_path, bundle_dir, version="0.9.18")
    active_path = runtime_dir / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active["manifest"] = {"bundle_sha256": digest}
    active_path.write_text(json.dumps(active), encoding="utf-8")

    restart_requests: list[bool] = []
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: bundle_dir)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher, "start_activation_watchdog", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda _args: (_ for _ in ()).throw(RuntimeError("post-success crash")),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )

    assert runtime_launcher.main(["core"]) == 1
    marker = json.loads(
        (runtime_dir / runtime_launcher.RECOVERY_MODE_FILE).read_text(encoding="utf-8")
    )
    assert restart_requests == [True]
    assert marker["mode"] == "official_signed_only"
    assert marker["failed_version"] == "0.9.18"
    assert marker["failed_bundle_sha256"] == digest
    assert "path" not in marker
    assert "error" not in marker


def test_protocol_three_recovery_hold_uses_exact_version_and_digest(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    bundle_dir = _bundle(tmp_path, "0.9.18")
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    digest = "b" * 64
    _write_active(tmp_path, bundle_dir, version="0.9.18")
    active_path = runtime_dir / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active["manifest"] = {"bundle_sha256": digest}
    active_path.write_text(json.dumps(active), encoding="utf-8")

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    assert runtime_launcher.enter_official_recovery_mode(bundle_dir) is True
    assert runtime_launcher.selected_app_dir() == image_dir

    # Replacing the selected signed asset with a different digest releases the
    # failed identity instead of permanently pinning the image runtime.
    active["manifest"] = {"bundle_sha256": "c" * 64}
    active_path.write_text(json.dumps(active), encoding="utf-8")
    runtime_launcher.selected_app_dir()
    assert not (runtime_dir / runtime_launcher.RECOVERY_MODE_FILE).exists()
    assert runtime_launcher.RECOVERY_MODE_ENV not in os.environ


@pytest.mark.parametrize(
    ("version", "protocol"),
    [
        ("not-a-version", 0),
        ("0.9.12", 1),
        ("0.9.16", 2),
        ("0.9.18", runtime_launcher.LAUNCHER_PROTOCOL_RECOVERY_CAPABLE),
    ],
)
def test_image_launcher_protocol_boundaries(version, protocol):
    assert runtime_launcher.image_launcher_protocol(version) == protocol


def test_launcher_protocol_status_and_legacy_selection_fallback(
    tmp_path: Path, monkeypatch
):
    fallback = tmp_path / "legacy-active"
    fallback.mkdir()
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.15")
    monkeypatch.setenv("CHANNELWATCH_ACTIVE_APP_DIR", str(fallback))
    monkeypatch.setenv(runtime_launcher.RECOVERY_MODE_ENV, "1")

    status = runtime_launcher.launcher_protocol_status()

    assert status == {
        "image_version": "0.9.15",
        "launcher_protocol": 1,
        "recovery_capable": False,
        "recovery_mode": True,
    }
    assert runtime_launcher.selected_app_dir() == fallback.resolve()


def test_protocol_three_selection_failure_falls_back_and_invalid_schema_defaults(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    image_dir = tmp_path / "image"
    fallback = tmp_path / "pinned"
    runtime_dir.mkdir()
    image_dir.mkdir()
    fallback.mkdir()
    messages = []
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setenv("CHANNELWATCH_ACTIVE_APP_DIR", str(fallback))
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "log", messages.append)
    monkeypatch.setattr(
        "core.update_center.resolve_active_app_dir",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("selection failed")),
    )
    monkeypatch.setattr(runtime_launcher, "load_json", lambda *_args: [])

    assert runtime_launcher._image_settings_schema_version() == 7
    assert runtime_launcher.selected_app_dir() == fallback.resolve()
    assert messages and "failed safely" in messages[0]


def test_official_recovery_entry_rejects_unsupported_or_unselected_runtime(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    image_dir = tmp_path / "image"
    bundle_dir = tmp_path / "bundle"
    other_dir = tmp_path / "other"
    for path in (runtime_dir, image_dir, bundle_dir, other_dir):
        path.mkdir()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.17")
    assert runtime_launcher.enter_official_recovery_mode(bundle_dir) is False

    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    assert runtime_launcher.enter_official_recovery_mode(bundle_dir) is False
    (runtime_dir / "active.json").write_text(
        json.dumps({"version": "0.9.18", "path": str(other_dir)}),
        encoding="utf-8",
    )
    assert runtime_launcher.enter_official_recovery_mode(bundle_dir) is False
    (runtime_dir / "active.json").write_text(
        json.dumps({"version": "", "path": str(bundle_dir)}),
        encoding="utf-8",
    )
    assert runtime_launcher.enter_official_recovery_mode(bundle_dir) is False


def test_protocol_three_image_recovery_ignores_failed_bundle_static_ui(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_static = image_dir / "ui" / "backend" / "static_ui"
    failed_static = tmp_path / "failed-bundle" / "ui" / "backend" / "static_ui"
    image_static.mkdir(parents=True)
    failed_static.mkdir(parents=True)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_STATIC_UI_DIR", image_static)
    monkeypatch.setenv("CHANNELWATCH_ACTIVE_STATIC_UI_DIR", str(failed_static))

    assert runtime_launcher.selected_static_ui_dir(image_dir) == image_static


def test_prepare_import_path_tracks_only_the_selected_activation(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    app_dir = tmp_path / "active-app"
    other_dir = tmp_path / "other-app"
    runtime_dir.mkdir()
    app_dir.mkdir()
    other_dir.mkdir()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.chdir(tmp_path)

    # Missing/corrupt active state must clear stale generation metadata.
    monkeypatch.setenv(runtime_launcher.ACTIVATION_ID_ENV, "stale")
    monkeypatch.setenv(runtime_launcher.ACTIVATION_VERSION_ENV, "9.9.9")
    runtime_launcher.prepare_import_path(app_dir)
    assert runtime_launcher.ACTIVATION_ID_ENV not in os.environ
    assert runtime_launcher.ACTIVATION_VERSION_ENV not in os.environ

    (runtime_dir / "active.json").write_text(
        json.dumps(
            {
                "path": str(app_dir),
                "activation_id": "generation-42",
                "version": "0.9.16",
            }
        )
    )
    runtime_launcher.prepare_import_path(app_dir)
    assert os.environ[runtime_launcher.ACTIVATION_ID_ENV] == "generation-42"
    assert os.environ[runtime_launcher.ACTIVATION_VERSION_ENV] == "0.9.16"
    assert os.environ["CHANNELWATCH_APP_DIR"] == str(app_dir)
    assert os.environ["CW_STATIC_UI_DIR"].endswith("ui/backend/static_ui")

    runtime_launcher.prepare_import_path(other_dir)
    assert runtime_launcher.ACTIVATION_ID_ENV not in os.environ
    assert runtime_launcher.ACTIVATION_VERSION_ENV not in os.environ


def test_prepare_import_path_evicts_image_package_before_selected_bundle_import(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    bundle_dir = tmp_path / "bundle"
    bundle_core = bundle_dir / "core"
    runtime_dir.mkdir()
    bundle_core.mkdir(parents=True)
    (bundle_core / "__init__.py").write_text(
        '__version__ = "0.9.19"\n', encoding="utf-8"
    )
    (bundle_core / "identity.py").write_text(
        "from core import __version__\nSELECTED_VERSION = __version__\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    image_root = Path(runtime_launcher.__file__).resolve().parents[1]
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_root)
    original_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "core"
        or name.startswith("core.")
        or name == "ui"
        or name.startswith("ui.")
    }
    original_path = list(sys.path)
    try:
        assert "core.runtime_launcher" in sys.modules

        runtime_launcher.prepare_import_path(bundle_dir)

        assert "core" not in sys.modules
        assert "core.runtime_launcher" not in sys.modules
        from core.identity import SELECTED_VERSION

        assert SELECTED_VERSION == "0.9.19"
        assert Path(sys.modules["core"].__file__).resolve().is_relative_to(bundle_dir)
    finally:
        for name in tuple(sys.modules):
            if (
                name == "core"
                or name.startswith("core.")
                or name == "ui"
                or name.startswith("ui.")
            ):
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)
        sys.path[:] = original_path


def test_coordinated_restart_rejects_unsupported_or_invalid_supervisor(
    monkeypatch,
):
    monkeypatch.setattr(runtime_launcher, "_supervisor_parent_pid", lambda: None)
    with pytest.raises(RuntimeError, match="requires supervisord as the direct parent"):
        runtime_launcher.request_container_restart()


def test_coordinated_restart_signals_supervisor(monkeypatch):
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(runtime_launcher, "_supervisor_parent_pid", lambda: 1)
    monkeypatch.setattr(
        runtime_launcher.os, "kill", lambda pid, sig: signals.append((pid, sig))
    )

    runtime_launcher.request_container_restart()

    assert signals == [(1, runtime_launcher.signal.SIGTERM)]


def test_coordinated_restart_propagates_supervisor_signal_failure(monkeypatch):
    monkeypatch.setattr(runtime_launcher, "_supervisor_parent_pid", lambda: 73)
    monkeypatch.setattr(
        runtime_launcher.os,
        "kill",
        lambda _pid, _sig: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(PermissionError, match="denied"):
        runtime_launcher.request_container_restart()


def test_protocol_three_restart_uses_image_owned_helper(monkeypatch):
    calls = []
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(
        runtime_launcher,
        "_spawn_protocol_three_restart_helper",
        lambda: calls.append("helper"),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "_supervisor_parent_pid",
        lambda: (_ for _ in ()).throw(AssertionError("must not stop PID 1")),
    )

    runtime_launcher.request_container_restart()

    assert calls == ["helper"]


def test_image_owned_helper_performs_ordinary_restart_without_update_journal(
    tmp_path: Path,
    monkeypatch,
):
    class FakeSupervisor:
        def __init__(self):
            self.identities = _protocol_three_old_processes()
            self.calls: list[tuple[str, str]] = []

        def getProcessInfo(self, name):
            return {"name": name, **self.identities[name], "statename": "RUNNING"}

        def signalProcess(self, name, signal_name):
            self.calls.append((name, signal_name))
            self.identities[name] = {
                "pid": self.identities[name]["pid"] + 100,
                "start": self.identities[name]["start"] + 10,
            }
            return True

    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    supervisor = FakeSupervisor()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "_validated_supervisor_proxy",
        lambda _path: SimpleNamespace(supervisor=supervisor),
    )
    monkeypatch.setattr(runtime_launcher.time, "sleep", lambda _seconds: None)
    read_fd, write_fd = os.pipe()

    assert (
        runtime_launcher.restart_supervisor_services(
            socket_path=supervisor_runtime / "supervisor.sock",
            ack_fd=write_fd,
        )
        == 0
    )

    assert os.read(read_fd, 64) == b"ready\n"
    os.close(read_fd)
    assert supervisor.calls == [("core", "TERM"), ("ui", "TERM")]
    assert not runtime_launcher.restart_journal_present()
    assert not runtime_launcher.protocol_three_handoff_present()


@pytest.mark.parametrize(
    ("unavailable_process", "state"),
    [("core", "STOPPED"), ("ui", "FATAL")],
)
def test_ordinary_restart_recovers_one_stopped_child_before_restart(
    tmp_path: Path,
    monkeypatch,
    unavailable_process: str,
    state: str,
):
    class FakeSupervisor:
        def __init__(self):
            self.identities = _protocol_three_old_processes()
            self.states = {"core": "RUNNING", "ui": "RUNNING"}
            self.identities[unavailable_process] = {"pid": 0, "start": 0}
            self.states[unavailable_process] = state
            self.calls: list[tuple[str, str, object]] = []

        def getProcessInfo(self, name):
            return {
                "name": name,
                **self.identities[name],
                "statename": self.states[name],
            }

        def startProcess(self, name, wait):
            self.calls.append(("start", name, wait))
            self.identities[name] = {"pid": 301, "start": 1_700_000_003}
            self.states[name] = "RUNNING"
            return True

        def signalProcess(self, name, signal_name):
            self.calls.append(("signal", name, signal_name))
            self.identities[name] = {
                "pid": self.identities[name]["pid"] + 100,
                "start": self.identities[name]["start"] + 10,
            }
            return True

    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    supervisor = FakeSupervisor()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "_validated_supervisor_proxy",
        lambda _path: SimpleNamespace(supervisor=supervisor),
    )
    monkeypatch.setattr(runtime_launcher.time, "sleep", lambda _seconds: None)
    read_fd, write_fd = os.pipe()

    assert (
        runtime_launcher.restart_supervisor_services(
            socket_path=supervisor_runtime / "supervisor.sock",
            ack_fd=write_fd,
        )
        == 0
    )

    assert os.read(read_fd, 64) == b"ready\n"
    os.close(read_fd)
    assert supervisor.calls == [
        ("start", unavailable_process, False),
        ("signal", "core", "TERM"),
        ("signal", "ui", "TERM"),
    ]
    assert all(supervisor.states[name] == "RUNNING" for name in ("core", "ui"))
    assert not runtime_launcher.restart_journal_present()
    assert not runtime_launcher.protocol_three_handoff_present()


@pytest.mark.parametrize(
    ("unavailable_process", "state"),
    [("core", "STOPPED"), ("ui", "FATAL")],
)
def test_update_handoff_rejects_unsignalable_child_before_acknowledgement(
    tmp_path: Path,
    monkeypatch,
    unavailable_process: str,
    state: str,
):
    class FakeSupervisor:
        def __init__(self):
            self.identities = _protocol_three_old_processes()
            self.calls: list[tuple[str, str]] = []

        def getProcessInfo(self, name):
            if name == unavailable_process:
                return {"name": name, "pid": 0, "start": 0, "statename": state}
            return {"name": name, **self.identities[name], "statename": "RUNNING"}

        def signalProcess(self, name, signal_name):
            self.calls.append((name, signal_name))
            return True

    runtime_dir = tmp_path / "channelwatch-runtime"
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)
    supervisor = FakeSupervisor()
    monkeypatch.setattr(
        runtime_launcher,
        "_validated_supervisor_proxy",
        lambda _path: SimpleNamespace(supervisor=supervisor),
    )
    read_fd, write_fd = os.pipe()

    assert (
        runtime_launcher.restart_supervisor_services(
            socket_path=supervisor_runtime / "supervisor.sock",
            ack_fd=write_fd,
        )
        == 1
    )

    assert os.read(read_fd, 64) == b"error:RuntimeError\n"
    os.close(read_fd)
    assert supervisor.calls == []
    assert runtime_launcher.restart_journal_present()
    assert not runtime_launcher.protocol_three_handoff_present()


def test_image_owned_restart_helper_signals_both_old_generations_before_handoff(
    tmp_path: Path,
    monkeypatch,
):
    class FakeSupervisor:
        def __init__(self):
            self.identities = _protocol_three_old_processes()
            self.calls: list[tuple[str, str]] = []

        def getProcessInfo(self, name):
            return {"name": name, **self.identities[name], "statename": "RUNNING"}

        def signalProcess(self, name, signal_name):
            self.calls.append((name, signal_name))
            self.identities[name] = {
                "pid": self.identities[name]["pid"] + 100,
                "start": self.identities[name]["start"] + 10,
            }
            return True

    runtime_dir = tmp_path / "channelwatch-runtime"
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)

    supervisor = FakeSupervisor()
    proxy = SimpleNamespace(supervisor=supervisor)
    monkeypatch.setattr(
        runtime_launcher, "_validated_supervisor_proxy", lambda _path: proxy
    )
    monkeypatch.setattr(runtime_launcher.time, "sleep", lambda _seconds: None)
    read_fd, write_fd = os.pipe()

    result = runtime_launcher.restart_supervisor_services(
        socket_path=supervisor_runtime / "supervisor.sock",
        ack_fd=write_fd,
    )

    assert os.read(read_fd, 64) == b"ready\n"
    os.close(read_fd)
    assert result == 0
    assert supervisor.calls == [("core", "TERM"), ("ui", "TERM")]
    marker = json.loads(
        (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).read_text()
    )
    assert marker["old_processes"] == _protocol_three_old_processes()
    assert (
        runtime_launcher.stat_module.S_IMODE(
            (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).stat().st_mode
        )
        == 0o600
    )
    duplicate_read_fd, duplicate_write_fd = os.pipe()
    assert (
        runtime_launcher.restart_supervisor_services(
            socket_path=supervisor_runtime / "supervisor.sock",
            ack_fd=duplicate_write_fd,
        )
        == 0
    )
    assert os.read(duplicate_read_fd, 64) == b"ready\n"
    os.close(duplicate_read_fd)
    assert supervisor.calls == [("core", "TERM"), ("ui", "TERM")]
    assert runtime_launcher.consume_protocol_three_restart_journal_before_launch()


@pytest.mark.parametrize("crash_phase", ["signal-sent:core", "signal-sent:ui"])
def test_restart_helper_death_before_stop_barrier_never_publishes_handoff(
    tmp_path: Path,
    monkeypatch,
    crash_phase: str,
):
    class FakeSupervisor:
        def __init__(self):
            self.identities = _protocol_three_old_processes()
            self.calls: list[str] = []

        def getProcessInfo(self, name):
            return {"name": name, **self.identities[name], "statename": "RUNNING"}

        def signalProcess(self, name, signal_name):
            assert signal_name == "TERM"
            self.calls.append(name)
            self.identities[name] = {
                "pid": self.identities[name]["pid"] + 100,
                "start": self.identities[name]["start"] + 10,
            }
            return True

    runtime_dir = tmp_path / "channelwatch-runtime"
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.18")
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)
    supervisor = FakeSupervisor()
    monkeypatch.setattr(
        runtime_launcher,
        "_validated_supervisor_proxy",
        lambda _path: SimpleNamespace(supervisor=supervisor),
    )
    monkeypatch.setattr(runtime_launcher.time, "sleep", lambda _seconds: None)

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise _SimulatedPowerLoss(phase)

    monkeypatch.setattr(
        runtime_launcher,
        "_protocol_three_restart_helper_checkpoint",
        crash,
    )
    read_fd, write_fd = os.pipe()

    with pytest.raises(_SimulatedPowerLoss, match=crash_phase):
        runtime_launcher.restart_supervisor_services(
            socket_path=supervisor_runtime / "supervisor.sock",
            ack_fd=write_fd,
        )

    assert os.read(read_fd, 64) == b"ready\n"
    os.close(read_fd)
    expected_calls = ["core"] if crash_phase.endswith("core") else ["core", "ui"]
    assert supervisor.calls == expected_calls
    assert not (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).exists()
    assert (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).is_file()


def test_restart_helper_broken_ack_pipe_changes_no_process_or_handoff(
    tmp_path: Path,
    monkeypatch,
):
    class FakeSupervisor:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def getProcessInfo(self, name):
            return {
                "name": name,
                **_protocol_three_old_processes()[name],
                "statename": "RUNNING",
            }

        def signalProcess(self, name, signal_name):
            self.calls.append((name, signal_name))
            return True

    runtime_dir = tmp_path / "channelwatch-runtime"
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)
    supervisor = FakeSupervisor()
    monkeypatch.setattr(
        runtime_launcher,
        "_validated_supervisor_proxy",
        lambda _path: SimpleNamespace(supervisor=supervisor),
    )
    read_fd, write_fd = os.pipe()
    os.close(read_fd)

    assert (
        runtime_launcher.restart_supervisor_services(
            socket_path=supervisor_runtime / "supervisor.sock",
            ack_fd=write_fd,
        )
        == 1
    )

    assert supervisor.calls == []
    assert not (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).exists()
    assert (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).is_file()


def test_restart_helper_barrier_timeout_preserves_unaccepted_journal(
    tmp_path: Path,
    monkeypatch,
):
    class FakeSupervisor:
        def __init__(self):
            self.identities = _protocol_three_old_processes()
            self.calls: list[str] = []

        def getProcessInfo(self, name):
            return {"name": name, **self.identities[name], "statename": "RUNNING"}

        def signalProcess(self, name, signal_name):
            assert signal_name == "TERM"
            self.calls.append(name)
            return True

    runtime_dir = tmp_path / "channelwatch-runtime"
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    target_dir = _bundle(tmp_path, "0.9.19")
    runtime_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_protocol_three_apply_journal(runtime_dir, target_dir)
    supervisor = FakeSupervisor()
    monkeypatch.setattr(
        runtime_launcher,
        "_validated_supervisor_proxy",
        lambda _path: SimpleNamespace(supervisor=supervisor),
    )
    monotonic_values = iter([0.0, 11.0])
    monkeypatch.setattr(
        runtime_launcher.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(runtime_launcher.time, "sleep", lambda _seconds: None)
    read_fd, write_fd = os.pipe()

    assert (
        runtime_launcher.restart_supervisor_services(
            socket_path=supervisor_runtime / "supervisor.sock",
            ack_fd=write_fd,
        )
        == 1
    )

    assert os.read(read_fd, 64) == b"ready\n"
    os.close(read_fd)
    assert supervisor.calls == ["core", "ui"]
    assert not (runtime_dir / runtime_launcher.PROTOCOL_THREE_HANDOFF_FILE).exists()
    assert (runtime_dir / runtime_launcher.RESTART_REQUIRED_FILE).is_file()


def test_restart_helper_lock_rejects_duplicate_owner(tmp_path: Path):
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    socket_path = supervisor_runtime / "supervisor.sock"

    with runtime_launcher._protocol_three_restart_helper_lock(socket_path):
        with pytest.raises(RuntimeError, match="already active"):
            with runtime_launcher._protocol_three_restart_helper_lock(socket_path):
                pytest.fail("a duplicate helper must never acquire ownership")


@pytest.mark.parametrize("lock_kind", ["symlink", "hardlink"])
def test_restart_helper_lock_rejects_untrusted_inode_without_mutation(
    tmp_path: Path,
    lock_kind: str,
):
    supervisor_runtime = tmp_path / "supervisor-runtime"
    supervisor_runtime.mkdir(mode=0o700)
    socket_path = supervisor_runtime / "supervisor.sock"
    lock_path = (
        supervisor_runtime / runtime_launcher.PROTOCOL_THREE_RESTART_HELPER_LOCK_FILE
    )
    external = tmp_path / "external-lock"
    external.write_bytes(b"external")
    external.chmod(0o640)
    original_bytes = external.read_bytes()
    original_mode = external.stat().st_mode
    if lock_kind == "symlink":
        lock_path.symlink_to(external)
    else:
        os.link(external, lock_path)

    with pytest.raises(RuntimeError, match="cannot be opened|single-link"):
        with runtime_launcher._protocol_three_restart_helper_lock(socket_path):
            pytest.fail("an untrusted helper lock must never be acquired")

    assert external.read_bytes() == original_bytes
    assert external.stat().st_mode == original_mode


def test_protocol_one_bridge_precedes_import_failure_and_restarts_coherently(
    tmp_path: Path, monkeypatch
):
    config_dir = tmp_path / "config"
    runtime_dir = config_dir / "channelwatch-runtime"
    bundle_dir = runtime_dir / "releases" / "v0.9.18"
    image_dir = tmp_path / "image"
    bundle_dir.mkdir(parents=True)
    (image_dir / "core").mkdir(parents=True)
    digest = "b" * 64
    (runtime_dir / "active.json").write_text(
        json.dumps(
            {
                "version": "0.9.18",
                "path": str(bundle_dir),
                "manifest": {"bundle_sha256": digest},
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "rollback.json").write_text(
        json.dumps({"target_version": "0.9.18", "previous_active": None}),
        encoding="utf-8",
    )
    (runtime_dir / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "legacy-job",
                "operation": "apply",
                "status": "restarting",
                "version": "0.9.18",
            }
        ),
        encoding="utf-8",
    )
    historical = SimpleNamespace(
        __file__=str(image_dir / "core" / "runtime_launcher.py"),
        rollback_failed_activation=lambda _error: None,
    )
    watchdogs: list[Path] = []
    restarts: list[bool] = []
    monkeypatch.setitem(runtime_launcher.sys.modules, "__main__", historical)
    monkeypatch.setattr(runtime_launcher, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "start_activation_watchdog", watchdogs.append)
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restarts.append(True),
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.15")
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(bundle_dir))

    assert runtime_launcher.install_historical_launcher_bridge() is True
    pending = json.loads((runtime_dir / "activation-pending.json").read_text())
    assert pending["job_id"] == "legacy-job"
    assert pending["bundle_sha256"] == digest
    assert watchdogs == [bundle_dir]

    with pytest.raises(SystemExit) as stopped:
        historical.rollback_failed_activation("candidate UI import failed")
    assert stopped.value.code == 75
    assert restarts == [True]
    assert not (runtime_dir / "active.json").exists()
    assert not list(runtime_dir.glob("activation-*.json"))
    failed = json.loads((runtime_dir / "update-job.json").read_text())
    assert failed["job_id"] == "legacy-job"
    assert failed["bundle_sha256"] == digest
    assert failed["rollback_applied"] is True
    scheduler = json.loads((runtime_dir / "update-scheduler.json").read_text())
    assert scheduler["quarantines"][f"0.9.18:{digest}"]["reason"] == (
        "activation_failed"
    )


def test_protocol_one_bridge_retries_rejected_parent_handoff_before_app_import(
    tmp_path: Path, monkeypatch
):
    config_dir = tmp_path / "config"
    runtime_dir = config_dir / "channelwatch-runtime"
    bundle_dir = runtime_dir / "releases" / "v0.9.18"
    image_dir = tmp_path / "image"
    bundle_dir.mkdir(parents=True)
    (image_dir / "core").mkdir(parents=True)
    digest = "c" * 64
    (runtime_dir / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "legacy-job",
                "operation": "apply",
                "status": "failed",
                "version": "0.9.18",
                "rolled_back_from": "0.9.18",
                "rolled_back_to": "image",
                "rollback_applied": True,
                "bundle_sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    historical = SimpleNamespace(
        __file__=str(image_dir / "core" / "runtime_launcher.py"),
        rollback_failed_activation=lambda _error: None,
    )
    attempts: list[bool] = []
    monkeypatch.setitem(runtime_launcher.sys.modules, "__main__", historical)
    monkeypatch.setattr(runtime_launcher, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: attempts.append(True),
    )
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.15")
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(bundle_dir))

    with pytest.raises(SystemExit) as stopped:
        runtime_launcher.install_historical_launcher_bridge()
    assert stopped.value.code == 75
    assert attempts == [True]
    assert not (runtime_dir / "active.json").exists()
    assert (
        json.loads((runtime_dir / "update-job.json").read_text())["bundle_sha256"]
        == digest
    )


def test_protocol_two_bridge_enriches_historical_rollback_with_exact_quarantine(
    tmp_path: Path, monkeypatch
):
    config_dir = tmp_path / "config"
    runtime_dir = config_dir / "channelwatch-runtime"
    bundle_dir = runtime_dir / "releases" / "v0.9.18"
    image_dir = tmp_path / "image"
    bundle_dir.mkdir(parents=True)
    (image_dir / "core").mkdir(parents=True)
    digest = "d" * 64
    activation_id = "activation-protocol-two"
    pending = {
        "job_id": "protocol-two-job",
        "version": "0.9.18",
        "scheduler_attempt_id": "activation@protocol-two-job",
        "bundle_sha256": digest,
        "activation_id": activation_id,
        "path": str(bundle_dir),
    }
    (runtime_dir / "active.json").write_text(
        json.dumps(
            {
                "version": "0.9.18",
                "path": str(bundle_dir),
                "activation_id": activation_id,
                "activation_protocol": 2,
                "manifest": {"bundle_sha256": digest},
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "rollback.json").write_text(
        json.dumps({"target_version": "0.9.18", "previous_active": None}),
        encoding="utf-8",
    )
    (runtime_dir / "update-job.json").write_text(
        json.dumps(
            {
                "job_id": "protocol-two-job",
                "operation": "apply",
                "status": "validating",
                "version": "0.9.18",
                "bundle_sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "activation-pending.json").write_text(
        json.dumps(pending), encoding="utf-8"
    )
    original_calls: list[str] = []
    historical = SimpleNamespace(
        __file__=str(image_dir / "core" / "runtime_launcher.py"),
        rollback_failed_activation=lambda error, **_kwargs: original_calls.append(
            error
        ),
        request_container_restart=lambda: None,
    )
    monkeypatch.setitem(runtime_launcher.sys.modules, "__main__", historical)
    monkeypatch.setattr(runtime_launcher, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setenv("CHANNELWATCH_IMAGE_VERSION", "0.9.17")
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(bundle_dir))

    assert runtime_launcher.install_historical_launcher_bridge() is True
    historical.rollback_failed_activation(
        "candidate core import failed",
        pending=pending,
        _outcome_lock_held=True,
    )

    assert original_calls == []
    assert not (runtime_dir / "active.json").exists()
    failed = json.loads((runtime_dir / "update-job.json").read_text())
    assert failed["job_id"] == "protocol-two-job"
    assert failed["bundle_sha256"] == digest
    assert failed["rollback_applied"] is True
    restart = json.loads((runtime_dir / "restart-required.json").read_text())
    assert restart["job_id"] == "protocol-two-job"
    scheduler = json.loads((runtime_dir / "update-scheduler.json").read_text())
    assert scheduler["quarantines"][f"0.9.18:{digest}"]["reason"] == (
        "activation_failed"
    )


def test_supervisor_identity_rejects_arbitrary_direct_parent(monkeypatch):
    monkeypatch.setattr(runtime_launcher.os, "getppid", lambda: 42)
    monkeypatch.setattr(
        runtime_launcher.Path,
        "read_bytes",
        lambda _path: b"/bin/sh\x00-c\x00echo supervisor.supervisord",
    )
    assert runtime_launcher._supervisor_parent_pid() is None


@pytest.mark.parametrize("parent_pid", [0, -1])
def test_supervisor_identity_rejects_invalid_parent_pid(parent_pid, monkeypatch):
    monkeypatch.setattr(runtime_launcher.os, "getppid", lambda: parent_pid)
    monkeypatch.setattr(
        runtime_launcher.Path,
        "read_bytes",
        lambda _path: pytest.fail("invalid parent PID must not read /proc"),
    )

    assert runtime_launcher._supervisor_parent_pid() is None


def test_supervisor_identity_rejects_unreadable_proc_identity(monkeypatch):
    monkeypatch.setattr(runtime_launcher.os, "getppid", lambda: 73)
    monkeypatch.setattr(
        runtime_launcher.Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(OSError("unreadable")),
    )

    assert runtime_launcher._supervisor_parent_pid() is None


def test_supervisor_identity_accepts_verified_non_pid_one_parent(monkeypatch):
    monkeypatch.setattr(runtime_launcher.os, "getppid", lambda: 73)
    monkeypatch.setattr(
        runtime_launcher.Path,
        "read_bytes",
        lambda _path: b"/usr/bin/python\x00-m\x00supervisor.supervisord",
    )
    assert runtime_launcher._supervisor_parent_pid() == 73


def test_parse_utc_rejects_invalid_values_and_normalizes_naive_time():
    assert runtime_launcher._parse_utc("not-a-timestamp") is None
    assert runtime_launcher._parse_utc(None) is None
    assert runtime_launcher._parse_utc("2026-08-21T12:30:00") == datetime(
        2026, 8, 21, 12, 30, tzinfo=timezone.utc
    )


def test_pending_identity_rejects_mismatch_and_resolution_failure(tmp_path: Path):
    pending = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": str(tmp_path),
    }
    assert (
        runtime_launcher._pending_matches_active(
            pending, {**pending, "activation_id": "generation-b"}
        )
        is False
    )

    class UnresolvablePath:
        def resolve(self):
            raise OSError("mount disappeared")

    assert (
        runtime_launcher._pending_matches_active(
            pending, dict(pending), UnresolvablePath()
        )
        is False
    )


def test_pending_claim_handles_lost_race_and_unexpected_generation(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": "/bundle/a",
    }

    assert runtime_launcher._claim_pending(pending, claimant="test") is None

    (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).write_text(
        json.dumps({**pending, "activation_id": "generation-b"})
    )
    assert runtime_launcher._claim_pending(pending, claimant="test") is None
    preserved = runtime_dir / "activation-test-generation-a.json"
    assert json.loads(preserved.read_text())["activation_id"] == "generation-b"


def test_two_same_generation_claimants_have_one_durable_winner(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending = {
        "activation_id": "generation-a",
        "version": "0.9.16",
        "path": "/bundle/a",
    }
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    pending_path.write_text(json.dumps(pending))
    winner = runtime_launcher._claim_pending(pending, claimant="winner")
    loser = runtime_launcher._claim_pending(pending, claimant="loser")

    assert winner is not None and winner.exists()
    assert loser is None
    assert not pending_path.exists()


def test_startup_recovers_claim_interrupted_after_atomic_rename(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending = json.loads(
        (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).read_text()
    )

    claim = runtime_launcher._claim_pending(pending, claimant="crashed-process")
    assert claim is not None
    assert not (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).exists()

    # Simulate a fresh process after SIGKILL/power loss: the claimant never
    # restored its rename, but startup reconstructs the canonical pending file.
    assert runtime_launcher.recover_claimed_activation(current_dir) is True
    recovered = json.loads(
        (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).read_text()
    )
    assert recovered == pending

    restart_requests: list[bool] = []
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: restart_requests.append(True),
    )
    assert runtime_launcher.enforce_activation_deadline() is True
    assert restart_requests == [True]
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.9"
    assert not list(runtime_dir.glob("activation-*.json"))


def test_two_launcher_recoverers_publish_one_intact_pending_state(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    pending = json.loads(pending_path.read_text())
    claim_path = runtime_dir / "activation-crashed-activation-1.json"
    os.replace(pending_path, claim_path)

    barrier = threading.Barrier(2)
    real_link = os.link

    def synchronized_link(source, target):
        if Path(source) == claim_path:
            barrier.wait(timeout=2)
        return real_link(source, target)

    monkeypatch.setattr(runtime_launcher.os, "link", synchronized_link)
    results: list[bool] = []
    errors: list[BaseException] = []

    def recover() -> None:
        try:
            results.append(runtime_launcher.recover_claimed_activation(current_dir))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert results == [True, True]
    assert json.loads(pending_path.read_text()) == pending


def test_late_launcher_recoverer_cannot_recreate_completed_pending_state(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    claim_path = runtime_dir / "activation-crashed-activation-1.json"
    os.replace(pending_path, claim_path)

    late_entered = threading.Event()
    release_late = threading.Event()
    real_link = os.link

    def controlled_link(source, target):
        if threading.current_thread().name == "late-recoverer":
            late_entered.set()
            assert release_late.wait(timeout=2)
        return real_link(source, target)

    monkeypatch.setattr(runtime_launcher.os, "link", controlled_link)
    late_results: list[bool] = []
    late = threading.Thread(
        target=lambda: late_results.append(
            runtime_launcher.recover_claimed_activation(current_dir)
        ),
        name="late-recoverer",
    )
    late.start()
    assert late_entered.wait(timeout=2)

    assert runtime_launcher.recover_claimed_activation(current_dir) is True
    # Simulate the first process immediately claiming and completing pending.
    pending_path.unlink()
    release_late.set()
    late.join(timeout=5)

    assert not late.is_alive()
    assert late_results == [False]
    assert not pending_path.exists()


def test_recovery_fails_closed_when_hard_link_publication_is_unavailable(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    claim_path = runtime_dir / "activation-crashed-activation-1.json"
    os.replace(pending_path, claim_path)
    monkeypatch.setattr(
        runtime_launcher.os,
        "link",
        lambda *_args: (_ for _ in ()).throw(PermissionError("unsupported")),
    )

    assert runtime_launcher.recover_claimed_activation(current_dir) is False
    assert not pending_path.exists()
    assert claim_path.exists()


def test_startup_recovers_matching_claimant_file(tmp_path: Path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    legacy_claim = runtime_dir / "activation-failed-watchdog-activation-1.json"
    os.replace(pending_path, legacy_claim)

    assert runtime_launcher.recover_claimed_activation(current_dir) is True
    assert json.loads(pending_path.read_text())["activation_id"] == "activation-1"


def test_startup_preserves_but_never_recovers_unrelated_claim(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    pending_path.unlink()
    stale_claim = runtime_dir / "activation-failed-watchdog-stale.json"
    stale_claim.write_text(
        json.dumps(
            {
                "activation_id": "other-generation",
                "version": "0.9.99",
                "path": str(tmp_path / "other-bundle"),
            }
        )
    )

    assert runtime_launcher.recover_claimed_activation(current_dir) is False
    assert not pending_path.exists()
    assert stale_claim.exists()


def test_activation_deadline_discards_stale_generation_and_waits_for_future_deadline(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    _write_pending_activation(runtime_dir, current_dir, previous_dir)

    active_path = runtime_dir / "active.json"
    active = json.loads(active_path.read_text())
    active_path.write_text(json.dumps({**active, "activation_id": "new-generation"}))
    assert runtime_launcher.enforce_activation_deadline() is False
    assert not (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).exists()

    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    pending = json.loads(pending_path.read_text())
    pending_path.write_text(
        json.dumps(
            {
                **pending,
                "deadline_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=2)
                ).isoformat(),
            }
        )
    )
    assert runtime_launcher.enforce_activation_deadline() is False
    assert pending_path.exists()


def test_activation_success_claim_is_restored_when_completion_write_fails(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    pending = json.loads(
        (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).read_text()
    )
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher, "_marker_matches_pending", lambda *_args: True
    )
    monkeypatch.setattr(
        runtime_launcher,
        "_complete_activation_from_markers",
        lambda _pending: (_ for _ in ()).throw(OSError("job write failed")),
    )

    with pytest.raises(OSError, match="job write failed"):
        runtime_launcher.enforce_activation_deadline()

    restored = json.loads(
        (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).read_text()
    )
    assert restored == pending
    assert not list(runtime_dir.glob("activation-completed-watchdog-*.json"))


def test_public_rollback_cannot_overwrite_launcher_completion_claim(
    tmp_path: Path, monkeypatch
):
    from core.update_center import UpdateLockedError, UpdateManager

    runtime_dir = tmp_path / "channelwatch-runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    pending = json.loads(
        (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).read_text()
    )
    for component in ("core", "ui"):
        (runtime_dir / f"activation-{component}-ready.json").write_text(
            json.dumps(
                {
                    "component": component,
                    "version": pending["version"],
                    "activation_id": pending["activation_id"],
                    "path": pending["path"],
                    "healthy": True,
                }
            )
        )

    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        restart_callable=lambda: True,
    )
    original_complete = runtime_launcher._complete_activation_from_markers
    rollback_rejected = False

    def attempt_rollback_while_launcher_owns_claim(marker_pending):
        nonlocal rollback_rejected
        with pytest.raises(
            UpdateLockedError,
            match="pending startup validation",
        ):
            manager.rollback()
        rollback_rejected = True
        return original_complete(marker_pending)

    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "_complete_activation_from_markers",
        attempt_rollback_while_launcher_owns_claim,
    )

    assert runtime_launcher.enforce_activation_deadline() is False

    active = json.loads((runtime_dir / "active.json").read_text())
    job = json.loads((runtime_dir / "update-job.json").read_text())
    assert rollback_rejected is True
    assert active["activation_id"] == pending["activation_id"]
    assert active["version"] == "0.9.10"
    assert job["status"] == "success"
    assert job["version"] == "0.9.10"
    assert not (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).exists()


def test_activation_failure_claim_is_restored_when_rollback_fails(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    current_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    current_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, current_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "rollback_failed_activation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("rollback storage failed")
        ),
    )

    with pytest.raises(OSError, match="rollback storage failed"):
        runtime_launcher.enforce_activation_deadline()

    assert (runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE).exists()
    assert not list(runtime_dir.glob("activation-failed-watchdog-*.json"))


def test_activation_watchdog_survives_transient_error(tmp_path: Path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    pending_path = runtime_dir / runtime_launcher.ACTIVATION_PENDING_FILE
    pending_path.write_text("{}")
    calls: list[str] = []
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        runtime_launcher,
        "enforce_activation_deadline",
        lambda: (_ for _ in ()).throw(RuntimeError("temporary failure")),
    )
    monkeypatch.setattr(runtime_launcher, "log", calls.append)
    monkeypatch.setattr(
        runtime_launcher,
        "time",
        SimpleNamespace(sleep=lambda _seconds: pending_path.unlink()),
    )

    runtime_launcher._activation_watchdog_loop()

    assert calls == ["Activation watchdog error: temporary failure"]


def test_start_activation_watchdog_starts_only_for_matching_generation(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "runtime"
    app_dir = tmp_path / "bundle"
    previous_dir = tmp_path / "previous"
    app_dir.mkdir()
    previous_dir.mkdir()
    _write_pending_activation(runtime_dir, app_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    started: list[bool] = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(True)

    monkeypatch.setattr(runtime_launcher.threading, "Thread", FakeThread)

    thread = runtime_launcher.start_activation_watchdog(app_dir)
    assert thread is not None
    assert thread.kwargs["name"] == "activation-watchdog"
    assert thread.kwargs["daemon"] is True
    assert started == [True]
    assert runtime_launcher.start_activation_watchdog(tmp_path / "wrong") is None


def test_image_keyboard_interrupt_and_system_exit_preserve_exit_semantics(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: image_dir)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher, "start_activation_watchdog", lambda _path: None
    )

    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda _args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        runtime_launcher.main(["core"])

    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda _args: (_ for _ in ()).throw(SystemExit(7)),
    )
    with pytest.raises(SystemExit) as exc_info:
        runtime_launcher.main(["core"])
    assert exc_info.value.code == 7


def test_bundle_restart_request_failure_is_logged_after_durable_rollback(
    tmp_path: Path, monkeypatch
):
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    bundle_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    runtime_dir = tmp_path / "channelwatch-runtime"
    _write_pending_activation(runtime_dir, bundle_dir, previous_dir)
    messages: list[str] = []
    monkeypatch.setattr(runtime_launcher, "IMAGE_APP_DIR", image_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_launcher, "selected_app_dir", lambda: bundle_dir)
    monkeypatch.setattr(runtime_launcher, "prepare_import_path", lambda _path: None)
    monkeypatch.setattr(
        runtime_launcher, "start_activation_watchdog", lambda _path: None
    )
    monkeypatch.setattr(
        runtime_launcher,
        "run_core",
        lambda _args: (_ for _ in ()).throw(RuntimeError("startup failed")),
    )
    monkeypatch.setattr(
        runtime_launcher,
        "request_container_restart",
        lambda: (_ for _ in ()).throw(RuntimeError("supervisor unavailable")),
    )
    monkeypatch.setattr(runtime_launcher, "log", messages.append)

    assert runtime_launcher.main(["core"]) == 1
    assert any("supervisor unavailable" in message for message in messages)
    assert json.loads((runtime_dir / "active.json").read_text())["version"] == "0.9.9"


def test_launcher_initial_journal_publish_never_clobbers_foreign_owner(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    failed_dir = _bundle(tmp_path, "0.9.10")
    previous_dir = _bundle(tmp_path, "0.9.9")
    _write_pending_activation(runtime_dir, failed_dir, previous_dir)
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    foreign_control = runtime_launcher._read_control_state()
    foreign_control["update-job.json"] = {
        "job_id": "foreign-launcher",
        "status": "failed",
    }
    foreign = runtime_launcher._build_restart_journal(
        reason="activation_rollback",
        operation="activation_rollback",
        phase="commit",
        job_id="foreign-launcher",
        source_active=foreign_control["active.json"],
        control=foreign_control,
    )

    def publish_foreign(phase: str) -> None:
        if phase == "journal:before-create":
            runtime_launcher.atomic_write_json(
                runtime_launcher.restart_required_path(), foreign
            )

    monkeypatch.setattr(
        runtime_launcher, "_restart_transition_checkpoint", publish_foreign
    )

    with pytest.raises(RuntimeError, match="won publication"):
        runtime_launcher.rollback_failed_activation("simulated failure")

    assert (
        runtime_launcher.load_json(runtime_launcher.restart_required_path(), None)
        == foreign
    )
    assert (
        runtime_launcher.load_json(runtime_dir / "active.json", None)["version"]
        == "0.9.10"
    )


def test_launcher_stale_local_journal_cannot_replay_under_foreign_owner(
    tmp_path: Path, monkeypatch
):
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(runtime_launcher, "RUNTIME_DIR", runtime_dir)
    seed_active = {"version": "seed", "path": "/seed"}
    runtime_launcher.atomic_write_json(runtime_dir / "active.json", seed_active)
    stale_control = runtime_launcher._read_control_state()
    stale_control["active.json"] = {"version": "stale", "path": "/stale"}
    stale = runtime_launcher._build_restart_journal(
        reason="activation_rollback",
        operation="activation_rollback",
        phase="commit",
        job_id="stale",
        source_active=seed_active,
        control=stale_control,
    )
    runtime_launcher._write_restart_journal(stale)
    foreign_control = runtime_launcher._read_control_state()
    foreign_control["update-job.json"] = {
        "job_id": "foreign",
        "status": "failed",
    }
    foreign = runtime_launcher._build_restart_journal(
        reason="activation_rollback",
        operation="activation_rollback",
        phase="commit",
        job_id="foreign",
        source_active=seed_active,
        control=foreign_control,
    )
    runtime_launcher.atomic_write_json(
        runtime_launcher.restart_required_path(), foreign
    )

    with pytest.raises(RuntimeError, match="another generation"):
        runtime_launcher.apply_restart_journal(stale)

    assert runtime_launcher.load_json(runtime_dir / "active.json", None) == seed_active
    assert (
        runtime_launcher.load_json(runtime_launcher.restart_required_path(), None)
        == foreign
    )
