from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from core import runtime_archive, runtime_preparation, update_center
from core.runtime_generation import RuntimeGeneration
from core.tests.test_runtime_generation import _generation_tree, _platform
from core.tests.test_runtime_transaction import (
    _runtime_bundle,
    _runtime_catalog,
    _runtime_fixture,
)
from core.tests.test_update_manager import (
    _acknowledge_entrypoint_handoff,
    _bundle,
    _key_pair,
    _manifest,
)
from core.update_center import UpdateLockedError, UpdateManager


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _digest(index: int) -> str:
    return f"{index:064x}"


def _prepared_generation(tmp_path: Path, monkeypatch) -> RuntimeGeneration:
    from core import runtime_generation

    releases = tmp_path / "channelwatch-runtime/releases"
    releases.mkdir(parents=True)
    generation_dir, descriptor = _generation_tree(releases)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    return runtime_generation.prepare_runtime_generation(generation_dir, descriptor)


def _retain_signed_catalog(runtime_dir: Path, generation: RuntimeGeneration) -> dict:
    from core.runtime_release_trust import persist_runtime_catalog

    descriptor = json.loads(
        (generation.app_dir / "channelwatch-bundle.json").read_text(encoding="utf-8")
    )
    private, public = _key_pair()
    catalog = _runtime_catalog(private, descriptor, b"signed-runtime-bundle")
    persist_runtime_catalog(runtime_dir, descriptor, catalog, public)
    return public


def test_cleanup_preserves_every_control_and_journal_reference(tmp_path: Path):
    from core.runtime_retention import cleanup_runtime_generations

    runtime_dir = tmp_path / "channelwatch-runtime"
    releases = runtime_dir / "releases"
    releases.mkdir(parents=True)
    identifiers = [_digest(index) for index in range(1, 7)]
    for identifier in identifiers:
        (releases / identifier).mkdir()

    _write_json(runtime_dir / "active.json", {"path": str(releases / identifiers[0])})
    _write_json(
        runtime_dir / "rollback.json",
        {"previous_active": {"path": str(releases / identifiers[1])}},
    )
    _write_json(
        runtime_dir / "activation-pending.json",
        {"path": str(releases / identifiers[2])},
    )
    _write_json(
        runtime_dir / "restart-required.json",
        {"control": {"active.json": {"path": str(releases / identifiers[3])}}},
    )
    _write_json(
        runtime_dir / ".restart-required.json.candidate-owned",
        {"source_active": {"path": str(releases / identifiers[4])}},
    )
    _write_json(
        runtime_dir / "activation-worker-claim.json",
        {"path": str(releases / identifiers[5])},
    )

    result = cleanup_runtime_generations(runtime_dir)

    assert result.deleted == ()
    assert all((releases / identifier).is_dir() for identifier in identifiers)


def test_cleanup_deletes_only_fully_verified_unreferenced_generation(
    tmp_path: Path, monkeypatch
):
    from core.runtime_retention import cleanup_runtime_generations

    generation = _prepared_generation(tmp_path, monkeypatch)
    public = _retain_signed_catalog(tmp_path / "channelwatch-runtime", generation)

    result = cleanup_runtime_generations(
        tmp_path / "channelwatch-runtime", public_keys=public
    )

    assert result.deleted == (generation.app_dir,)
    assert not generation.app_dir.exists()
    assert not (
        tmp_path
        / "channelwatch-runtime/trust"
        / f"{generation.generation_id}.json"
    ).exists()


def test_cleanup_warns_if_authenticated_trust_record_cannot_be_removed(
    tmp_path: Path, monkeypatch
):
    from core import runtime_release_trust, runtime_retention

    generation = _prepared_generation(tmp_path, monkeypatch)
    runtime_dir = tmp_path / "channelwatch-runtime"
    public = _retain_signed_catalog(runtime_dir, generation)
    monkeypatch.setattr(
        runtime_release_trust,
        "delete_retained_runtime_catalog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
        raising=False,
    )

    result = runtime_retention.cleanup_runtime_generations(
        runtime_dir, public_keys=public
    )

    assert result.deleted == (generation.app_dir,)
    assert not generation.app_dir.exists()
    assert any("trust" in warning.lower() for warning in result.warnings)


def test_cleanup_preserves_unsigned_unreferenced_generation(
    tmp_path: Path, monkeypatch
):
    from core.runtime_retention import cleanup_runtime_generations

    generation = _prepared_generation(tmp_path, monkeypatch)

    result = cleanup_runtime_generations(
        tmp_path / "channelwatch-runtime", public_keys={}
    )

    assert result.deleted == ()
    assert generation.app_dir.is_dir()
    assert result.warnings


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
@pytest.mark.parametrize("target", ["runtime", "releases"])
def test_cleanup_skips_writable_runtime_storage(
    tmp_path: Path, monkeypatch, target: str
):
    from core.runtime_retention import cleanup_runtime_generations

    generation = _prepared_generation(tmp_path, monkeypatch)
    runtime_dir = tmp_path / "channelwatch-runtime"
    selected = runtime_dir if target == "runtime" else runtime_dir / "releases"
    selected.chmod(0o777)

    result = cleanup_runtime_generations(runtime_dir, public_keys={})

    assert result.deleted == ()
    assert generation.app_dir.is_dir()
    assert result.warnings


def test_cleanup_preserves_generation_with_forged_trust(tmp_path: Path, monkeypatch):
    from core.runtime_retention import cleanup_runtime_generations

    generation = _prepared_generation(tmp_path, monkeypatch)
    runtime_dir = tmp_path / "channelwatch-runtime"
    public = _retain_signed_catalog(runtime_dir, generation)
    trust = runtime_dir / "trust" / f"{generation.generation_id}.json"
    forged = json.loads(trust.read_text(encoding="utf-8"))
    forged["payload"]["releases"][0]["version"] = "99.0.0"
    trust.write_text(json.dumps(forged), encoding="utf-8")

    result = cleanup_runtime_generations(runtime_dir, public_keys=public)

    assert result.deleted == ()
    assert generation.app_dir.is_dir()
    assert result.warnings


def test_cleanup_preserves_quarantined_generation_version(tmp_path: Path, monkeypatch):
    from core.runtime_retention import cleanup_runtime_generations

    generation = _prepared_generation(tmp_path, monkeypatch)
    runtime_dir = tmp_path / "channelwatch-runtime"
    _write_json(
        runtime_dir / "update-scheduler.json",
        {"quarantines": {"release": {"version": "1.5.0"}}},
    )

    result = cleanup_runtime_generations(runtime_dir)

    assert result.deleted == ()
    assert generation.app_dir.is_dir()


def test_cleanup_preserves_generation_held_by_activation_claim(
    tmp_path: Path, monkeypatch
):
    from core.runtime_retention import cleanup_runtime_generations

    generation = _prepared_generation(tmp_path, monkeypatch)
    runtime_dir = tmp_path / "channelwatch-runtime"
    _write_json(
        runtime_dir / "activation-completed-core-123-activation-id.json",
        {"path": str(generation.app_dir)},
    )

    result = cleanup_runtime_generations(runtime_dir)

    assert result.deleted == ()
    assert generation.app_dir.is_dir()


def test_cleanup_preserves_marker_only_unknown_nonowned_and_symlink_entries(
    tmp_path: Path, monkeypatch
):
    from core.runtime_retention import cleanup_runtime_generations

    runtime_dir = tmp_path / "channelwatch-runtime"
    releases = runtime_dir / "releases"
    releases.mkdir(parents=True)
    marker_only = releases / _digest(10)
    marker_only.mkdir()
    _write_json(marker_only / "prepared.json", {"status": "prepared"})
    unknown = releases / "operator-copy"
    unknown.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = releases / _digest(11)
    linked.symlink_to(outside, target_is_directory=True)
    nonowned = releases / _digest(12)
    nonowned.mkdir()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.chown(nonowned, 65534, 65534)
    else:
        monkeypatch.setattr(
            "core.runtime_retention._entry_owned_by_current_user",
            lambda path: path != nonowned,
        )

    result = cleanup_runtime_generations(runtime_dir)

    assert result.deleted == ()
    assert marker_only.is_dir()
    assert unknown.is_dir()
    assert linked.is_symlink()
    assert nonowned.is_dir()
    assert result.warnings


def test_cleanup_failure_is_returned_as_warning(tmp_path: Path, monkeypatch):
    from core import runtime_retention

    generation = _prepared_generation(tmp_path, monkeypatch)
    public = _retain_signed_catalog(tmp_path / "channelwatch-runtime", generation)
    monkeypatch.setattr(
        runtime_retention.shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )

    result = runtime_retention.cleanup_runtime_generations(
        tmp_path / "channelwatch-runtime", public_keys=public
    )

    assert result.deleted == ()
    assert generation.app_dir.is_dir()
    assert any("could not be removed" in warning for warning in result.warnings)


def test_cleanup_preserves_generation_with_tampered_environment(
    tmp_path: Path, monkeypatch
):
    from core.runtime_retention import cleanup_runtime_generations

    generation = _prepared_generation(tmp_path, monkeypatch)
    (generation.app_dir / "environment/pyvenv.cfg").write_text(
        "tampered\n", encoding="utf-8"
    )

    result = cleanup_runtime_generations(tmp_path / "channelwatch-runtime")

    assert result.deleted == ()
    assert generation.app_dir.is_dir()
    assert result.warnings


def test_successful_activation_keeps_success_when_cleanup_warns(
    tmp_path: Path, monkeypatch
):
    from core import runtime_retention

    private, public = _key_pair()
    bundle = _bundle()
    manifest = _manifest(private, bundle)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="0.9.9",
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else manifest,
        restart_callable=lambda: True,
    )
    monkeypatch.setattr(
        runtime_retention,
        "cleanup_runtime_generations",
        lambda _runtime_dir, **_kwargs: runtime_retention.RuntimeRetentionResult(
            deleted=(), retained=(), warnings=("cleanup busy",)
        ),
    )
    manager.check()
    manager.apply()
    _acknowledge_entrypoint_handoff(manager)
    active = json.loads(manager.active_path.read_text(encoding="utf-8"))

    for component in ("core", "ui"):
        manager.record_startup_success(
            component=component,
            running_version="0.9.10",
            activation_id=active["activation_id"],
            healthy=True,
        )

    job = json.loads(manager.job_path.read_text(encoding="utf-8"))
    assert job["status"] == "success"
    assert job["cleanup_warnings"] == ["cleanup busy"]


def test_manager_lock_allows_only_one_runtime_preparation_and_reports_phase(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "4")
    private, public = _key_pair()
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    catalog = _runtime_catalog(private, descriptor, bundle)
    entered = threading.Event()
    release = threading.Event()
    prepare_count = 0

    def prepare(
        _source, releases: Path, _descriptor: dict, *, on_started
    ) -> RuntimeGeneration:
        nonlocal prepare_count
        prepare_count += 1
        on_started()
        entered.set()
        assert release.wait(timeout=5)
        path = releases / descriptor["generation_id"]
        return RuntimeGeneration(
            generation_id=descriptor["generation_id"],
            app_dir=path,
            python_executable=path / "environment/bin/python",
            dependency_digest=descriptor["dependency_inventory_sha256"],
        )

    monkeypatch.setattr(
        runtime_preparation, "prepare_runtime_archive_bounded", prepare
    )

    def manager() -> UpdateManager:
        return UpdateManager(
            config_dir=tmp_path,
            current_version="1.4.3",
            image_version="1.4.1",
            launcher_protocol=4,
            public_keys=public,
            fetcher=lambda url, max_bytes: (
                bundle if url.endswith(".zip") else catalog
            ),
            restart_callable=lambda: True,
        )

    errors: list[BaseException] = []

    def apply_first() -> None:
        try:
            manager().apply()
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=apply_first)
    first.start()
    assert entered.wait(timeout=5), errors
    preparing = json.loads(
        (tmp_path / "channelwatch-runtime/update-job.json").read_text()
    )
    assert preparing["status"] == "applying"
    assert preparing["phase"] == "preparing"

    with pytest.raises(UpdateLockedError):
        manager().apply()

    release.set()
    first.join(timeout=10)
    assert not first.is_alive()
    assert errors == []
    assert prepare_count == 1
