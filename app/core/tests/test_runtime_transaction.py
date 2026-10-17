import base64
import copy
import hashlib
import io
import json
import os
import platform
import stat
import sys
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import runtime_archive, runtime_preparation, update_center
from core.runtime_generation import (
    RuntimeGeneration,
    RuntimeGenerationError,
    descriptor_generation_id,
)
from core.tests.test_update_manager import _key_pair
from core.tests.test_update_v0918 import _catalog
from core.update_center import (
    RUNTIME_ABI,
    UpdateBundleError,
    UpdateManager,
    canonical_payload_bytes,
)
from core.update_policy import UpdateAutomationService


@pytest.fixture(autouse=True)
def _protocol_four_runtime_capability(monkeypatch):
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "4")


@pytest.fixture
def _immutable_runtime_preparation_python(
    monkeypatch, immutable_image_python: Path
):
    prepare = runtime_preparation.prepare_runtime_archive_bounded

    def prepare_with_image_python(*args, **kwargs):
        return prepare(*args, image_python=immutable_image_python, **kwargs)

    monkeypatch.setattr(
        runtime_preparation,
        "prepare_runtime_archive_bounded",
        prepare_with_image_python,
    )


def _file_record(path: str, content: bytes, mode: str = "0644") -> dict:
    return {
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "mode": mode,
    }


def _runtime_fixture(version: str = "1.4.4") -> tuple[dict, bytes, dict[str, bytes]]:
    payloads = {
        "core/main.py": b"print('runtime core')\n",
        "core/release_legal/LICENSE": b"runtime license\n",
        "dependencies/site-packages/demo.py": b"VALUE = 'runtime dependency'\n",
        "ui/backend/main.py": b"print('runtime ui')\n",
    }
    records = [
        _file_record(path, content, "0755" if path == "core/main.py" else "0644")
        for path, content in sorted(payloads.items())
    ]
    dependency_record = next(
        record for record in records if record["path"].startswith("dependencies/")
    )
    machine = platform.machine().lower()
    architecture = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }[machine]
    libc_family, libc_version = platform.libc_ver()
    libc_pair = [int(part) for part in libc_version.split(".")[:2]]
    inventory = {
        "schema": 1,
        "build": {
            "builder_image_digest": "sha256:" + "f" * 64,
            "python_version": platform.python_version(),
            "pip_version": "25.3",
        },
        "platform": {
            "architecture": architecture,
            "python_implementation": "cpython",
            "python_cache_tag": sys.implementation.cache_tag,
            "python_version": platform.python_version(),
            "os_family": "linux",
            "libc_family": libc_family,
            "minimum_libc_version": ".".join(
                str(part) for part in libc_pair
            ),
            "wheel_platform": "manylinux_2_36",
        },
        "requirements": {
            "direct": ["demo"],
            "locked": [
                {"name": "demo", "version": "1.0", "hashes": ["a" * 64]}
            ],
        },
        "artifacts": [
            {
                "filename": "demo-1.0-py3-none-any.whl",
                "sha256": "a" * 64,
                "size": 1,
                "source": "https://files.pythonhosted.org/demo.whl",
            }
        ],
        "dependencies": [
            {
                "name": "demo",
                "version": "1.0",
                "license_expression": "MIT",
                "license_files": ["core/release_legal/LICENSE"],
            }
        ],
        "files": [dependency_record],
        "counts": {
            "files": 1,
            "bytes": dependency_record["size"],
        },
    }
    inventory_raw = canonical_payload_bytes(inventory)
    descriptor = {
        "schema": 1,
        "application_version": version,
        "source_commit": "a" * 40,
        "dependency_inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
        "bundle_format": "channelwatch-runtime-generation-v1",
        "required_launcher_capability": "isolated_dependencies_v1",
        "settings_schema_version": 7,
        "architecture": architecture,
        "python_implementation": "cpython",
        "python_cache_tag": sys.implementation.cache_tag,
        "python_version": platform.python_version(),
        "os_family": "linux",
        "libc_family": libc_family,
        "minimum_libc_version": libc_pair,
        "dependency_inventory_path": "dependencies/inventory.json",
        "files": records,
        "expanded_size": len(inventory_raw) + sum(len(item) for item in payloads.values()),
    }
    descriptor["generation_id"] = descriptor_generation_id(descriptor)
    return descriptor, inventory_raw, payloads


def _zip_info(path: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def _runtime_bundle(
    descriptor: dict,
    inventory_raw: bytes,
    payloads: dict[str, bytes],
    *,
    archive_modes: dict[str, int] | None = None,
    archive_infos: dict[str, zipfile.ZipInfo] | None = None,
    extras: list[tuple[zipfile.ZipInfo, bytes]] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    modes = archive_modes or {}
    infos = archive_infos or {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                _zip_info("channelwatch-bundle.json"),
                canonical_payload_bytes(descriptor),
            )
            archive.writestr(
                _zip_info("dependencies/inventory.json"), inventory_raw
            )
            for record in descriptor["files"]:
                path = record["path"]
                archive.writestr(
                    infos.get(
                        path,
                        _zip_info(path, modes.get(path, int(record["mode"], 8))),
                    ),
                    payloads[path],
                )
            for info, content in extras or []:
                archive.writestr(info, content)
    return buffer.getvalue()


def _runtime_catalog(private, descriptor: dict, bundle: bytes) -> bytes:
    raw = json.loads(
        _catalog(
            private,
            bundle,
            version=descriptor["application_version"],
            sources=["1.4.3"],
            launchers=[4],
        )
    )
    raw["schema"] = 3
    release = raw["payload"]["releases"][0]
    release["minimum_image_version"] = "1.4.0"
    release["updater_protocol"] = 4
    alternate_descriptor = copy.deepcopy(descriptor)
    alternate_architecture = (
        "arm64" if descriptor["architecture"] == "amd64" else "amd64"
    )
    alternate_descriptor["architecture"] = alternate_architecture
    alternate_descriptor["generation_id"] = descriptor_generation_id(
        alternate_descriptor
    )
    alternate_digest = hashlib.sha256(
        f"{alternate_architecture} test runtime bundle".encode("ascii")
    ).digest()
    release["runtime_variants"] = [
        {
            "bundle_url": release["bundle_url"],
            "bundle_sha256": release["bundle_sha256"],
            "bundle_signature": release["bundle_signature"],
            "key_id": release["key_id"],
            "runtime_descriptor": descriptor,
        },
        {
            "bundle_url": (
                "https://github.com/CoderLuii/ChannelWatch/releases/download/"
                f"v{descriptor['application_version']}/channelwatch-runtime-"
                f"{alternate_architecture}.zip"
            ),
            "bundle_sha256": alternate_digest.hex(),
            "bundle_signature": base64.b64encode(
                private.sign(alternate_digest)
            ).decode("ascii"),
            "key_id": release["key_id"],
            "runtime_descriptor": alternate_descriptor,
        },
    ]
    raw["signature"]["value"] = base64.b64encode(
        private.sign(canonical_payload_bytes(raw["payload"]))
    ).decode("ascii")
    return json.dumps(raw).encode()


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("duplicate", "duplicate"),
        ("traversal", "unsafe"),
        ("symlink", "link"),
        ("mode", "mode"),
        ("unbound", "not signed"),
    ],
)
def test_runtime_archive_rejects_unsafe_or_unsigned_members(mutation: str, match: str):
    descriptor, inventory_raw, payloads = _runtime_fixture()
    extras: list[tuple[zipfile.ZipInfo, bytes]] = []
    modes: dict[str, int] = {}
    infos: dict[str, zipfile.ZipInfo] = {}
    if mutation == "duplicate":
        extras.append((_zip_info("core/main.py", 0o755), payloads["core/main.py"]))
    elif mutation == "traversal":
        extras.append((_zip_info("../escape.py"), b"escape"))
    elif mutation == "symlink":
        symlink = _zip_info("core/main.py", 0o755)
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        infos["core/main.py"] = symlink
    elif mutation == "mode":
        modes["ui/backend/main.py"] = 0o755
    else:
        extras.append((_zip_info("LICENSE"), b"unsigned legal copy"))
    bundle = _runtime_bundle(
        descriptor,
        inventory_raw,
        payloads,
        archive_modes=modes,
        archive_infos=infos,
        extras=extras,
    )

    with pytest.raises(UpdateBundleError, match=match):
        runtime_archive.validate_runtime_archive(bundle, descriptor)


def test_runtime_archive_installs_to_immutable_generation_and_signed_modes(
    tmp_path: Path, monkeypatch
):
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    prepared: list[tuple[Path, dict]] = []

    def prepare(path: Path, selected_descriptor: dict) -> RuntimeGeneration:
        prepared.append((path, selected_descriptor))
        return RuntimeGeneration(
            generation_id=descriptor["generation_id"],
            app_dir=path,
            python_executable=path / "environment/bin/python",
            dependency_digest=descriptor["dependency_inventory_sha256"],
        )

    monkeypatch.setattr(runtime_archive, "prepare_runtime_generation", prepare)

    generation = runtime_archive.install_runtime_archive(
        bundle, tmp_path / "releases", descriptor
    )

    assert generation.app_dir == tmp_path / "releases" / descriptor["generation_id"]
    assert prepared == [(generation.app_dir, descriptor)]
    assert json.loads(
        (generation.app_dir / "channelwatch-bundle.json").read_text(encoding="utf-8")
    ) == descriptor
    if os.name != "nt":
        assert stat.S_IMODE((generation.app_dir / "core/main.py").stat().st_mode) == 0o755
        assert stat.S_IMODE(
            (generation.app_dir / "ui/backend/main.py").stat().st_mode
        ) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
def test_runtime_archive_normalizes_directories_under_restrictive_umask(
    tmp_path: Path, monkeypatch
):
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)

    def prepare(path: Path, selected_descriptor: dict) -> RuntimeGeneration:
        return RuntimeGeneration(
            generation_id=str(selected_descriptor["generation_id"]),
            app_dir=path,
            python_executable=path / "environment/bin/python",
            dependency_digest=str(
                selected_descriptor["dependency_inventory_sha256"]
            ),
        )

    monkeypatch.setattr(runtime_archive, "prepare_runtime_generation", prepare)
    previous_umask = os.umask(0o027)
    try:
        generation = runtime_archive.install_runtime_archive(
            bundle, tmp_path / "releases", descriptor
        )
    finally:
        os.umask(previous_umask)

    assert {
        stat.S_IMODE(path.stat().st_mode)
        for path in generation.app_dir.rglob("*")
        if path.is_dir()
    } == {0o755}


def test_runtime_archive_refuses_low_space_before_creating_generation(
    tmp_path: Path, monkeypatch
):
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    releases = tmp_path / "releases"
    monkeypatch.setattr(
        runtime_archive.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )

    with pytest.raises(UpdateBundleError, match="enough free space"):
        runtime_archive.install_runtime_archive(bundle, releases, descriptor)

    assert list(releases.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
@pytest.mark.parametrize("target", ["runtime", "releases"])
def test_runtime_archive_rejects_writable_storage_ancestry(
    tmp_path: Path, monkeypatch, target: str
):
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    runtime_dir = tmp_path / "channelwatch-runtime"
    releases = runtime_dir / "releases"
    releases.mkdir(parents=True)
    selected = runtime_dir if target == "runtime" else releases
    selected.chmod(0o777)

    with pytest.raises(UpdateBundleError, match="trusted|protected"):
        runtime_archive.install_runtime_archive(bundle, releases, descriptor)


@pytest.mark.skipif(os.name == "nt", reason="read-only mode validation is POSIX-only")
def test_runtime_archive_rejects_read_only_releases_without_control_mutation(
    tmp_path: Path,
):
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    runtime_dir = tmp_path / "channelwatch-runtime"
    releases = runtime_dir / "releases"
    releases.mkdir(parents=True)
    active_path = runtime_dir / "active.json"
    rollback_path = runtime_dir / "rollback.json"
    active_before = b'{"version":"1.4.3","path":"/previous"}\n'
    rollback_before = b'{"previous_active":null}\n'
    active_path.write_bytes(active_before)
    rollback_path.write_bytes(rollback_before)
    releases.chmod(0o555)

    try:
        with pytest.raises(PermissionError):
            runtime_archive.install_runtime_archive(bundle, releases, descriptor)
    finally:
        releases.chmod(0o755)

    assert list(releases.iterdir()) == []
    assert active_path.read_bytes() == active_before
    assert rollback_path.read_bytes() == rollback_before


@pytest.mark.skipif(os.name == "nt", reason="owner validation is POSIX-only")
def test_runtime_archive_rejects_wrong_owner_ancestry_without_control_mutation(
    tmp_path: Path, monkeypatch
):
    from core import runtime_generation

    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    runtime_dir = tmp_path / "channelwatch-runtime"
    releases = runtime_dir / "releases"
    releases.mkdir(parents=True)
    active_path = runtime_dir / "active.json"
    active_before = b'{"version":"1.4.3","path":"/previous"}\n'
    active_path.write_bytes(active_before)
    current_uid = os.geteuid()
    monkeypatch.setattr(runtime_generation.os, "geteuid", lambda: current_uid + 1)

    with pytest.raises(UpdateBundleError, match="trusted|protected"):
        runtime_archive.install_runtime_archive(bundle, releases, descriptor)

    assert list(releases.iterdir()) == []
    assert active_path.read_bytes() == active_before


@pytest.mark.skipif(os.name == "nt", reason="symlink validation is POSIX-only")
def test_runtime_archive_rejects_original_symlinked_releases_path(tmp_path: Path):
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    actual = runtime_dir / "actual-releases"
    actual.mkdir()
    releases = runtime_dir / "releases"
    releases.symlink_to(actual, target_is_directory=True)

    with pytest.raises(UpdateBundleError, match="trusted|protected"):
        runtime_archive.install_runtime_archive(bundle, releases, descriptor)


def test_runtime_apply_preparation_refusal_returns_exact_terminal_job(
    tmp_path: Path, monkeypatch
):
    private, public = _key_pair()
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    catalog = _runtime_catalog(private, descriptor, bundle)
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    active_before = b'{"version":"1.4.3","path":"/previous"}\n'
    (runtime_dir / "active.json").write_bytes(active_before)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.4.3",
        image_version="1.4.1",
        launcher_protocol=4,
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else catalog,
    )

    def fail_preparation(*_args, **kwargs):
        kwargs["on_started"]()
        raise runtime_preparation.RuntimePreparationError(
            "simulated runtime preparation failure"
        )

    monkeypatch.setattr(
        runtime_preparation, "prepare_runtime_archive_bounded", fail_preparation
    )
    service = UpdateAutomationService(
        config_dir=tmp_path,
        manager_factory=lambda: manager,
    )

    job_id = "a" * 32
    result = service.apply_release(version="1.4.4", job_id=job_id)

    job = json.loads(manager.job_path.read_text(encoding="utf-8"))
    assert result == job
    assert job["job_id"] == job_id
    assert job["scheduler_attempt_id"].startswith("manual-apply@")
    assert job["status"] == "failed"
    assert job["phase"] == "preparing"
    assert job["phase_budget_seconds"] == 600
    assert job["phase_deadline_at"].endswith("Z")
    assert job["rollback_applied"] is False
    assert job["restart_required"] is False
    assert job["error"] == "simulated runtime preparation failure"
    assert manager.active_path.read_bytes() == active_before
    assert not manager.activation_pending_path.exists()
    assert not manager.restart_required_path.exists()


@pytest.mark.parametrize("handoff", ["journal", "pending"])
def test_pre_handoff_failure_does_not_replace_accepted_activation(
    tmp_path: Path, handoff: str
):
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.4.3",
        image_version="1.4.1",
        launcher_protocol=4,
    )
    job = manager._write_job(
        {
            "job_id": "a" * 32,
            "operation": "apply",
            "status": "applying",
            "version": "1.4.4",
            "bundle_sha256": "b" * 64,
            "scheduler_attempt_id": "manual-apply@test",
        }
    )
    active_before = b'{"version":"1.4.3","path":"/previous"}\n'
    manager.active_path.write_bytes(active_before)
    barrier = (
        manager.restart_required_path
        if handoff == "journal"
        else manager.activation_pending_path
    )
    barrier.write_text("{}\n", encoding="utf-8")

    assert manager.record_pre_handoff_apply_failure(
        job_id="a" * 32,
        scheduler_attempt_id="manual-apply@test",
        version="1.4.4",
        bundle_sha256="b" * 64,
        error="late failure",
    ) is None

    assert json.loads(manager.job_path.read_text(encoding="utf-8")) == job
    assert manager.active_path.read_bytes() == active_before


def test_runtime_apply_rejected_archive_keeps_active_pointer_byte_identical(
    tmp_path: Path,
    _immutable_runtime_preparation_python,
):
    private, public = _key_pair()
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(
        descriptor,
        inventory_raw,
        payloads,
        extras=[(_zip_info("LICENSE"), b"unsigned legal copy")],
    )
    catalog = _runtime_catalog(private, descriptor, bundle)
    runtime_dir = tmp_path / "channelwatch-runtime"
    runtime_dir.mkdir()
    active_before = b'{"version":"1.4.3","path":"/previous"}\n'
    (runtime_dir / "active.json").write_bytes(active_before)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.4.3",
        image_version="1.4.1",
        launcher_protocol=4,
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else catalog,
    )

    with pytest.raises(UpdateBundleError, match="not signed"):
        manager.apply()

    assert manager.active_path.read_bytes() == active_before
    assert not manager.restart_required_path.exists()


def test_runtime_apply_journal_binds_generation_and_dependency_identity(
    tmp_path: Path, monkeypatch, _immutable_runtime_preparation_python
):
    private, public = _key_pair()
    descriptor, inventory_raw, payloads = _runtime_fixture()
    bundle = _runtime_bundle(descriptor, inventory_raw, payloads)
    catalog = _runtime_catalog(private, descriptor, bundle)

    def prepare(path: Path, _descriptor: dict) -> RuntimeGeneration:
        return RuntimeGeneration(
            generation_id=descriptor["generation_id"],
            app_dir=path,
            python_executable=path / "environment/bin/python",
            dependency_digest=descriptor["dependency_inventory_sha256"],
        )

    monkeypatch.setattr(runtime_archive, "prepare_runtime_generation", prepare)
    manager = UpdateManager(
        config_dir=tmp_path,
        current_version="1.4.3",
        image_version="1.4.1",
        launcher_protocol=4,
        public_keys=public,
        fetcher=lambda url, max_bytes: bundle if url.endswith(".zip") else catalog,
        restart_callable=lambda: True,
    )

    manager.apply()

    journal = json.loads(manager.restart_required_path.read_text(encoding="utf-8"))
    active = journal["control"]["active.json"]
    assert active["path"] == str(manager.releases_dir / descriptor["generation_id"])
    assert active["runtime_generation_id"] == descriptor["generation_id"]
    assert active["runtime_descriptor_sha256"] == descriptor_generation_id(descriptor)
    assert active["dependency_inventory_sha256"] == descriptor[
        "dependency_inventory_sha256"
    ]
