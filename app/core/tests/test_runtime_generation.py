from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from core.runtime_generation import RuntimeGenerationError


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _descriptor_generation_id(descriptor: dict) -> str:
    unsigned_identity = {
        key: value for key, value in descriptor.items() if key != "generation_id"
    }
    return _sha256_bytes(_json_bytes(unsigned_identity))


def _refresh_generation_id(descriptor: dict) -> dict:
    descriptor["generation_id"] = _descriptor_generation_id(descriptor)
    return descriptor


def _file_record(root: Path, relative: str, *, mode: str = "0644") -> dict:
    path = root / relative
    payload = path.read_bytes()
    return {
        "path": relative,
        "sha256": _sha256_bytes(payload),
        "size": len(payload),
        "mode": mode,
    }


def _platform():
    from core.runtime_generation import RuntimePlatform

    return RuntimePlatform(
        architecture="amd64",
        python_cache_tag=sys.implementation.cache_tag,
        libc_family="glibc",
        libc_version=(2, 36),
        launcher_capabilities=frozenset({"isolated_dependencies_v1"}),
    )


def _inventory(root: Path, dependency_files: list[dict]) -> dict:
    return {
        "schema": 1,
        "build": {
            "builder_image_digest": "sha256:" + "f" * 64,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.0",
            "pip_version": "25.3",
        },
        "platform": {
            "architecture": "amd64",
            "python_implementation": "cpython",
            "python_cache_tag": sys.implementation.cache_tag,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.0",
            "os_family": "linux",
            "libc_family": "glibc",
            "minimum_libc_version": "2.36",
            "wheel_platform": "manylinux_2_36_x86_64",
        },
        "requirements": {
            "direct": ["example-package"],
            "locked": [
                {
                    "name": "example-package",
                    "version": "1.0.0",
                    "hashes": ["1" * 64],
                }
            ],
        },
        "artifacts": [
            {
                "filename": "example_package-1.0.0-py3-none-any.whl",
                "sha256": "2" * 64,
                "size": 123,
                "source": "https://files.pythonhosted.org/example.whl",
            }
        ],
        "dependencies": [
            {
                "name": "example-package",
                "version": "1.0.0",
                "license_expression": "MIT",
                "license_files": ["dependencies/licenses/example-package/LICENSE"],
            }
        ],
        "files": dependency_files,
        "counts": {
            "files": len(dependency_files),
            "bytes": sum(item["size"] for item in dependency_files),
        },
    }


def _descriptor_payload(
    root: Path, *, platform_value=None, extra: dict | None = None
) -> dict:
    selected_platform = platform_value or _platform()
    dependency_files = [
        _file_record(root, "dependencies/site-packages/example_package/__init__.py"),
        _file_record(
            root,
            "dependencies/site-packages/example_package/native_extension.so",
        ),
    ]
    inventory = _inventory(root, dependency_files)
    inventory_bytes = _json_bytes(inventory)
    inventory_path = root / "dependencies/inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_bytes(inventory_bytes)
    files = sorted(
        [
            _file_record(root, "core/main.py"),
            _file_record(root, "ui/backend/main.py"),
            *dependency_files,
            _file_record(root, "dependencies/licenses/example-package/LICENSE"),
        ],
        key=lambda item: item["path"],
    )
    descriptor = {
        "schema": 1,
        "application_version": "1.5.0",
        "source_commit": "a" * 40,
        "dependency_inventory_sha256": _sha256_bytes(inventory_bytes),
        "bundle_format": "channelwatch-runtime-generation-v1",
        "required_launcher_capability": "isolated_dependencies_v1",
        "settings_schema_version": 7,
        "architecture": selected_platform.architecture,
        "python_implementation": "cpython",
        "python_cache_tag": selected_platform.python_cache_tag,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.0",
        "os_family": "linux",
        "libc_family": selected_platform.libc_family,
        "minimum_libc_version": [2, 36],
        "dependency_inventory_path": "dependencies/inventory.json",
        "files": files,
        "expanded_size": sum(item["size"] for item in files) + len(inventory_bytes),
    }
    if extra:
        descriptor.update(extra)
    return _refresh_generation_id(descriptor)


def _generation_tree(tmp_path: Path, *, platform_value=None):
    staging = tmp_path / "staging"
    for relative, payload in {
        "core/main.py": b"print('core')\n",
        "ui/backend/main.py": b"print('ui')\n",
        "dependencies/site-packages/example_package/__init__.py": b"VERSION = '1.0.0'\n",
        "dependencies/site-packages/example_package/native_extension.so": b"signed-native-extension\n",
        "dependencies/licenses/example-package/LICENSE": b"MIT\n",
    }.items():
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    descriptor = _descriptor_payload(staging, platform_value=platform_value)
    from core.runtime_generation import descriptor_generation_id

    generation = tmp_path / descriptor_generation_id(descriptor)
    staging.rename(generation)
    (generation / "channelwatch-bundle.json").write_bytes(_json_bytes(descriptor))
    return generation, descriptor


def test_valid_descriptor_is_accepted():
    from core.runtime_generation import validate_runtime_descriptor

    validate_runtime_descriptor(_valid_descriptor_for_validation(), _platform())


@pytest.mark.parametrize("image_python", [(3, 14, 8), (3, 14, 9)])
def test_retained_python_floor_accepts_old_and_new_image_patches(monkeypatch, image_python):
    from core import runtime_generation

    descriptor = _valid_descriptor_for_validation()
    descriptor.update(python_version="3.14.8", python_cache_tag="cpython-314")
    _refresh_generation_id(descriptor)
    platform_value = replace(_platform(), python_cache_tag="cpython-314")
    monkeypatch.setattr(runtime_generation.sys, "version_info", image_python)

    runtime_generation.validate_runtime_descriptor(descriptor, platform_value)


def test_newer_python_floor_cannot_exclude_a_retained_image_silently(monkeypatch):
    from core import runtime_generation

    descriptor = _valid_descriptor_for_validation()
    descriptor.update(python_version="3.14.9", python_cache_tag="cpython-314")
    _refresh_generation_id(descriptor)
    platform_value = replace(_platform(), python_cache_tag="cpython-314")
    monkeypatch.setattr(runtime_generation.sys, "version_info", (3, 14, 8))

    with pytest.raises(RuntimeGenerationError, match="different CPython"):
        runtime_generation.validate_runtime_descriptor(descriptor, platform_value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("python_version", "99.0.0"),
        ("python_cache_tag", "cpython-999"),
    ],
)
def test_shape_accepts_future_interpreter_variant_but_full_validation_rejects(
    field, value
):
    from core.runtime_generation import (
        validate_runtime_descriptor,
        validate_runtime_descriptor_shape,
    )

    descriptor = _valid_descriptor_for_validation()
    descriptor[field] = value
    _refresh_generation_id(descriptor)

    validate_runtime_descriptor_shape(descriptor)
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(descriptor, _platform())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", 2),
        ("architecture", "arm64"),
        ("python_cache_tag", "cpython-313"),
        ("python_implementation", "pypy"),
        ("python_version", "99.0.0"),
        ("os_family", "windows"),
        ("libc_family", "musl"),
        ("minimum_libc_version", [99, 0]),
        ("required_launcher_capability", "arbitrary_exec_v1"),
        ("dependency_inventory_sha256", "not-a-hash"),
    ],
)
def test_rejects_descriptor_for_incompatible_or_malformed_runtime(field, value):
    from core.runtime_generation import validate_runtime_descriptor

    descriptor = _valid_descriptor_for_validation()
    descriptor[field] = value
    _refresh_generation_id(descriptor)
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(descriptor, _platform())


def test_rejects_missing_unknown_and_path_escape_fields():
    from core.runtime_generation import validate_runtime_descriptor

    missing = _valid_descriptor_for_validation()
    missing.pop("dependency_inventory_path")
    _refresh_generation_id(missing)
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(missing, _platform())

    unknown = {**_valid_descriptor_for_validation(), "future_mandatory": True}
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(unknown, _platform())

    escaped = _valid_descriptor_for_validation()
    escaped["files"] = [
        {"path": "../outside", "sha256": "c" * 64, "size": 1, "mode": "0644"}
    ]
    _refresh_generation_id(escaped)
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(escaped, _platform())


def test_rejects_duplicate_paths_privileged_modes_and_reserved_members():
    from core.runtime_generation import validate_runtime_descriptor

    descriptor = _valid_descriptor_for_validation()
    record = descriptor["files"][0]
    descriptor["files"] = [record, dict(record)]
    _refresh_generation_id(descriptor)
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(descriptor, _platform())

    privileged = _valid_descriptor_for_validation()
    privileged["files"][0]["mode"] = "4755"
    _refresh_generation_id(privileged)
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(privileged, _platform())

    reserved = _valid_descriptor_for_validation()
    reserved["files"][0]["path"] = "prepared.json"
    _refresh_generation_id(reserved)
    with pytest.raises(RuntimeGenerationError):
        validate_runtime_descriptor(reserved, _platform())


def _valid_descriptor_for_validation() -> dict:
    descriptor = {
        "schema": 1,
        "generation_id": "",
        "application_version": "1.5.0",
        "source_commit": "a" * 40,
        "dependency_inventory_sha256": "b" * 64,
        "bundle_format": "channelwatch-runtime-generation-v1",
        "required_launcher_capability": "isolated_dependencies_v1",
        "settings_schema_version": 7,
        "architecture": "amd64",
        "python_implementation": "cpython",
        "python_cache_tag": sys.implementation.cache_tag,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.0",
        "os_family": "linux",
        "libc_family": "glibc",
        "minimum_libc_version": [2, 36],
        "dependency_inventory_path": "dependencies/inventory.json",
        "files": [
            {
                "path": "core/main.py",
                "sha256": "c" * 64,
                "size": 12,
                "mode": "0644",
            },
            {
                "path": "dependencies/site-packages/example_package/__init__.py",
                "sha256": "d" * 64,
                "size": 1,
                "mode": "0644",
            },
            {
                "path": "ui/backend/main.py",
                "sha256": "e" * 64,
                "size": 12,
                "mode": "0644",
            },
        ],
        "expanded_size": 25,
    }
    return _refresh_generation_id(descriptor)


def test_detect_runtime_platform_normalizes_linux(monkeypatch):
    from core import runtime_generation

    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "libc_ver", lambda: ("glibc", "2.36"))
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "4")

    detected = runtime_generation.detect_runtime_platform()

    assert detected.architecture == "amd64"
    assert detected.python_cache_tag == sys.implementation.cache_tag
    assert detected.libc_family == "glibc"
    assert detected.libc_version == (2, 36)
    assert "isolated_dependencies_v1" in detected.launcher_capabilities


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        (None, frozenset()),
        (3, frozenset()),
        (4, frozenset({"isolated_dependencies_v1"})),
    ],
)
def test_detect_runtime_platform_gates_capability_on_launcher_protocol(
    monkeypatch, protocol, expected
):
    from core import runtime_generation

    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "libc_ver", lambda: ("glibc", "2.36"))
    monkeypatch.delenv("CHANNELWATCH_LAUNCHER_PROTOCOL", raising=False)

    detected = runtime_generation.detect_runtime_platform(launcher_protocol=protocol)

    assert detected.launcher_capabilities == expected


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [("3", frozenset()), ("4", frozenset({"isolated_dependencies_v1"}))],
)
def test_detect_runtime_platform_reads_image_launcher_protocol(
    monkeypatch, protocol, expected
):
    from core import runtime_generation

    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "libc_ver", lambda: ("glibc", "2.36"))
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", protocol)

    detected = runtime_generation.detect_runtime_platform()

    assert detected.launcher_capabilities == expected


def test_prepare_creates_final_local_venv_receipt_and_verifies(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)

    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )

    assert generation.generation_id == generation_dir.name
    assert generation.app_dir == generation_dir.resolve()
    assert generation.python_executable.exists()
    assert generation.python_executable.is_relative_to(generation_dir / "environment")
    if os.name != "nt":
        assert generation.python_executable.is_symlink()
        assert generation.python_executable.resolve() == Path(sys.executable).resolve()
    assert not list((generation_dir / "environment").rglob("pip*"))
    receipt = json.loads((generation_dir / "prepared.json").read_text())
    assert set(receipt) == {
        "schema",
        "generation_id",
        "descriptor_sha256",
        "dependency_digest",
        "application_version",
        "architecture",
        "python_cache_tag",
        "libc_family",
        "libc_version",
        "python_executable",
        "environment_files",
    }
    assert receipt["generation_id"] == generation.generation_id
    assert receipt["descriptor_sha256"] == generation.generation_id
    assert receipt["dependency_digest"] == descriptor["dependency_inventory_sha256"]
    assert receipt["python_executable"] == "environment/bin/python"
    environment_paths = [record["path"] for record in receipt["environment_files"]]
    assert environment_paths == sorted(environment_paths)
    assert "environment/pyvenv.cfg" in environment_paths
    assert {
        record["kind"] for record in receipt["environment_files"]
    } == {"regular", "symlink"}
    runtime_generation.verify_runtime_generation(generation)


@pytest.mark.skipif(os.name == "nt", reason="umask behavior is POSIX-only")
def test_prepare_normalizes_local_environment_under_restrictive_umask(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    generation_dir.chmod(0o700)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)

    previous_umask = os.umask(0o027)
    try:
        generation = runtime_generation.prepare_runtime_generation(
            generation_dir, descriptor
        )
    finally:
        os.umask(previous_umask)

    environment = generation_dir / "environment"
    assert stat.S_IMODE(generation_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(environment.stat().st_mode) == 0o755
    for path in environment.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            assert stat.S_IMODE(metadata.st_mode) == 0o755
        elif stat.S_ISREG(metadata.st_mode):
            assert stat.S_IMODE(metadata.st_mode) == 0o644
    runtime_generation.verify_runtime_generation(generation)


def test_prepare_is_idempotent_for_unchanged_verified_environment(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    first = runtime_generation.prepare_runtime_generation(generation_dir, descriptor)

    second = runtime_generation.prepare_runtime_generation(generation_dir, descriptor)

    assert second == first


@pytest.mark.skipif(os.name == "nt", reason="generation locking is POSIX-only")
def test_concurrent_prepare_builds_the_local_environment_once(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    real_create = runtime_generation._create_environment
    real_lock = runtime_generation._generation_prepare_lock
    create_entered = threading.Event()
    second_lock_attempted = threading.Event()
    release_create = threading.Event()
    lock_attempts = 0
    create_count = 0

    @contextmanager
    def observed_lock(root):
        nonlocal lock_attempts
        lock_attempts += 1
        if lock_attempts == 2:
            second_lock_attempted.set()
        with real_lock(root):
            yield

    def controlled_create(environment):
        nonlocal create_count
        create_count += 1
        create_entered.set()
        assert release_create.wait(timeout=5)
        real_create(environment)

    monkeypatch.setattr(runtime_generation, "_generation_prepare_lock", observed_lock)
    monkeypatch.setattr(runtime_generation, "_create_environment", controlled_create)
    results = []
    errors = []

    def prepare():
        try:
            results.append(
                runtime_generation.prepare_runtime_generation(
                    generation_dir, descriptor
                )
            )
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=prepare)
    second = threading.Thread(target=prepare)
    first.start()
    assert create_entered.wait(timeout=5)
    second.start()
    assert second_lock_attempted.wait(timeout=5)
    release_create.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert create_count == 1


@pytest.mark.skipif(os.name == "nt", reason="generation locking is POSIX-only")
def test_prepare_rejects_symlinked_generation_lock(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    outside = tmp_path / "outside-lock"
    outside.write_text("operator-owned", encoding="utf-8")
    lock = tmp_path / f".prepare-{generation_dir.name}.lock"
    lock.symlink_to(outside)

    with pytest.raises(RuntimeGenerationError, match="lock"):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)

    assert outside.read_text(encoding="utf-8") == "operator-owned"
    assert not (generation_dir / "environment").exists()


@pytest.mark.skipif(os.name == "nt", reason="generation locking is POSIX-only")
def test_prepare_rejects_insecure_generation_lock_mode(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    lock = tmp_path / f".prepare-{generation_dir.name}.lock"
    lock.write_bytes(b"")
    lock.chmod(0o644)

    with pytest.raises(RuntimeGenerationError, match="not trusted"):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
@pytest.mark.parametrize("target", ["generation", "releases", "runtime"])
def test_prepare_rejects_writable_generation_storage(
    tmp_path, monkeypatch, target
):
    from core import runtime_generation

    releases = tmp_path / "channelwatch-runtime/releases"
    releases.mkdir(parents=True)
    generation_dir, descriptor = _generation_tree(releases)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    selected = {
        "generation": generation_dir,
        "releases": releases,
        "runtime": releases.parent,
    }[target]
    selected.chmod(0o777)

    with pytest.raises(RuntimeGenerationError, match="trusted"):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)

    assert not (generation_dir / "environment").exists()


def test_prepare_rejects_tampered_inventory_and_payload_without_receipt(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    (generation_dir / "dependencies/inventory.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)
    assert not (generation_dir / "prepared.json").exists()
    assert not (generation_dir / "prepared.json").exists()
    assert not (generation_dir / "environment").exists()

    generation_dir, descriptor = _generation_tree(tmp_path / "second")
    (generation_dir / "core/main.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)
    assert not (generation_dir / "prepared.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="isolated image verification is POSIX-only")
def test_isolated_verifier_rejects_tampered_payload_without_loading_initializer(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    marker = tmp_path / "initializer-ran"
    (generation_dir / "core/main.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    module_path = Path(runtime_generation.__file__).resolve()
    code = f"""
import importlib.util
import pathlib
import sys
spec = importlib.util.spec_from_file_location(
    "isolated_runtime_generation", {str(module_path)!r}
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
generation = module.RuntimeGeneration(
    generation_id={generation.generation_id!r},
    app_dir=pathlib.Path({str(generation.app_dir)!r}),
    python_executable=pathlib.Path({str(generation.python_executable)!r}),
    dependency_digest={generation.dependency_digest!r},
)
try:
    module.verify_runtime_generation(generation)
except module.RuntimeGenerationError:
    pass
else:
    raise SystemExit("tampered payload was accepted")
if "core.update_center" in sys.modules:
    raise SystemExit("image verifier imported core.update_center")
"""
    environment = os.environ.copy()
    environment["CHANNELWATCH_LAUNCHER_PROTOCOL"] = "4"

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_prepare_rejects_symlinked_payload(tmp_path, monkeypatch):
    from core import runtime_generation

    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    target = generation_dir / "core/main.py"
    target.unlink()
    target.symlink_to(generation_dir / "ui/backend/main.py")

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)


def test_verify_rejects_unbound_payload_directory(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    (generation_dir / "dependencies/site-packages/unsigned_namespace").mkdir()

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
def test_verify_rejects_payload_directory_mode_change(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    (generation_dir / "dependencies/site-packages").chmod(0o700)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


def test_prepare_failure_removes_owned_partial_environment(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)

    def fail_venv(_environment):
        (_environment / "partial").mkdir(parents=True)
        raise OSError("ENOSPC")

    monkeypatch.setattr(runtime_generation, "_create_environment", fail_venv)
    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)
    assert not (generation_dir / "environment").exists()
    assert not (generation_dir / "prepared.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="runtime generation recovery is POSIX-only")
def test_prepare_recovers_exact_owned_transients_after_interruption(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    releases = tmp_path / "channelwatch-runtime" / "releases"
    releases.mkdir(parents=True)
    generation_dir, descriptor = _generation_tree(releases)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    probe = generation_dir / (".channelwatch-exec-probe-" + "a" * 32)
    probe.write_bytes(b"#!/bin/sh\nexit 0\n")
    probe.chmod(0o700)
    receipt_temp = generation_dir / (".prepared.json." + "b" * 32 + ".tmp")
    receipt_temp.write_text("partial", encoding="utf-8")
    receipt_temp.chmod(0o644)
    partial_environment = generation_dir / "environment"
    (partial_environment / "bin").mkdir(parents=True)
    (partial_environment / "bin/partial").write_text("partial", encoding="utf-8")

    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )

    runtime_generation.verify_runtime_generation(generation)
    assert not probe.exists()
    assert not receipt_temp.exists()
    assert not (partial_environment / "bin/partial").exists()


@pytest.mark.skipif(os.name == "nt", reason="writer interruption is POSIX-only")
def test_prepare_recovers_receipt_writer_sigkill_residue_under_private_umask(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    releases = tmp_path / "channelwatch-runtime" / "releases"
    releases.mkdir(parents=True)
    generation_dir, descriptor = _generation_tree(releases)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    child = os.fork()
    if child == 0:
        os.umask(0o027)
        real_write = runtime_generation._write_all

        def write_then_kill(file_descriptor, payload):
            real_write(file_descriptor, payload)
            os.kill(os.getpid(), 9)

        runtime_generation._write_all = write_then_kill
        runtime_generation._atomic_write_json(
            generation_dir / "prepared.json", {"partial": True}
        )
        os._exit(3)
    _pid, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status)
    receipt_temps = list(generation_dir.glob(".prepared.json.*.tmp"))
    assert len(receipt_temps) == 1
    assert stat.S_IMODE(receipt_temps[0].lstat().st_mode) == 0o644

    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )

    runtime_generation.verify_runtime_generation(generation)
    assert not receipt_temps[0].exists()


@pytest.mark.skipif(os.name == "nt", reason="runtime generation recovery is POSIX-only")
@pytest.mark.parametrize("control_name", ["active.json", "rollback.json"])
def test_prepare_does_not_clean_referenced_incomplete_generation(
    tmp_path, monkeypatch, control_name: str
):
    from core import runtime_generation

    runtime_dir = tmp_path / "channelwatch-runtime"
    releases = runtime_dir / "releases"
    releases.mkdir(parents=True)
    generation_dir, descriptor = _generation_tree(releases)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    probe = generation_dir / (".channelwatch-exec-probe-" + "c" * 32)
    probe.write_bytes(b"#!/bin/sh\nexit 0\n")
    probe.chmod(0o700)
    (runtime_dir / control_name).write_text(
        json.dumps(
            {
                "runtime_generation_id": generation_dir.name,
                "path": str(generation_dir),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeGenerationError, match="referenced"):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)

    assert probe.exists()


@pytest.mark.skipif(os.name == "nt", reason="runtime generation recovery is POSIX-only")
@pytest.mark.parametrize("unsafe_kind", ["wrong_mode", "hardlink", "unknown"])
def test_prepare_rejects_unsafe_or_unknown_interrupted_entries(
    tmp_path, monkeypatch, unsafe_kind: str
):
    from core import runtime_generation

    releases = tmp_path / "channelwatch-runtime" / "releases"
    releases.mkdir(parents=True)
    generation_dir, descriptor = _generation_tree(releases)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    if unsafe_kind == "unknown":
        target = generation_dir / ".unexpected-local-file"
    else:
        target = generation_dir / (".channelwatch-exec-probe-" + "d" * 32)
    target.write_bytes(b"#!/bin/sh\nexit 0\n")
    target.chmod(0o700 if unsafe_kind != "wrong_mode" else 0o600)
    alias = None
    if unsafe_kind == "hardlink":
        alias = tmp_path / "probe-alias"
        os.link(target, alias)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)

    assert target.exists()
    if alias is not None:
        assert alias.exists()


@pytest.mark.skipif(os.name == "nt", reason="runtime generation recovery is POSIX-only")
@pytest.mark.parametrize("unsafe_control", ["malformed", "symlink", "hardlink"])
def test_prepare_does_not_clean_when_reference_control_is_untrusted(
    tmp_path, monkeypatch, unsafe_control: str
):
    from core import runtime_generation

    runtime_dir = tmp_path / "channelwatch-runtime"
    releases = runtime_dir / "releases"
    releases.mkdir(parents=True)
    generation_dir, descriptor = _generation_tree(releases)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    probe = generation_dir / (".channelwatch-exec-probe-" + "e" * 32)
    probe.write_bytes(b"#!/bin/sh\nexit 0\n")
    probe.chmod(0o700)
    control = runtime_dir / "active.json"
    outside = tmp_path / "outside-control.json"
    if unsafe_control == "malformed":
        control.write_text("{", encoding="utf-8")
    else:
        outside.write_text("{}", encoding="utf-8")
        if unsafe_control == "symlink":
            control.symlink_to(outside)
        else:
            os.link(outside, control)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)

    assert probe.exists()
    if outside.exists():
        assert outside.read_text(encoding="utf-8") == "{}"


def test_prepare_rejects_noexec_target_before_environment_creation(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    monkeypatch.setattr(
        runtime_generation,
        "_assert_executable_directory",
        lambda _path: (_ for _ in ()).throw(RuntimeGenerationError("noexec")),
    )
    monkeypatch.setattr(
        runtime_generation,
        "_create_environment",
        lambda _path: pytest.fail("venv creation must not start on noexec storage"),
    )

    with pytest.raises(RuntimeGenerationError, match="noexec"):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)
    assert not (generation_dir / "prepared.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="execution probe is POSIX-only")
def test_executable_storage_probe_runs_and_removes_probe(tmp_path):
    from core.runtime_generation import _assert_executable_directory

    _assert_executable_directory(tmp_path)

    assert not list(tmp_path.glob(".channelwatch-exec-probe-*"))


@pytest.mark.skipif(os.name == "nt", reason="noexec mounts are POSIX-only")
def test_executable_storage_probe_rejects_real_noexec_mount():
    from core.runtime_generation import _assert_executable_directory

    noexec_dir = os.environ.get("CHANNELWATCH_TEST_NOEXEC_DIR")
    if not noexec_dir:
        pytest.skip("set CHANNELWATCH_TEST_NOEXEC_DIR to an owned noexec mount")

    with pytest.raises(RuntimeGenerationError, match="does not permit execution"):
        _assert_executable_directory(Path(noexec_dir))


@pytest.mark.parametrize(
    "relative_path",
    [
        "dependencies/site-packages/example_package/__init__.py",
        "dependencies/site-packages/example_package/native_extension.so",
    ],
)
def test_verify_rejects_signed_dependency_tampering_after_preparation(
    tmp_path, monkeypatch, relative_path
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    active_path = tmp_path / "channelwatch-runtime/active.json"
    active_path.parent.mkdir(parents=True)
    active_before = b'{"path":"/previous/app","version":"1.4.3"}\n'
    active_path.write_bytes(active_before)
    (generation_dir / relative_path).write_bytes(b"tampered-after-preparation\n")

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)

    assert active_path.read_bytes() == active_before


def test_verify_rejects_tampered_receipt(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    receipt_path = generation_dir / "prepared.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["dependency_digest"] = "f" * 64
    receipt_path.write_bytes(_json_bytes(receipt))

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


@pytest.mark.skipif(os.name == "nt", reason="symlink validation is POSIX-only")
def test_verify_rejects_environment_symlink_outside_trusted_roots(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    (generation_dir / "environment/escape").symlink_to(outside)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


@pytest.mark.parametrize(
    "relative",
    [
        "idempotency-marker",
        "lib/python3.14/site-packages/startup.pth",
        "lib/python3.14/site-packages/sitecustomize.py",
        "lib/python3.14/site-packages/attack.so",
    ],
)
def test_verify_rejects_unbound_environment_files(tmp_path, monkeypatch, relative):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    injected = generation_dir / "environment" / relative
    injected.parent.mkdir(parents=True, exist_ok=True)
    injected.write_text("injected", encoding="utf-8")

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


def test_verify_rejects_unbound_environment_directory(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    injected = generation_dir / "environment/lib/python3.14/site-packages/unsigned"
    injected.mkdir(parents=True)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
def test_verify_rejects_environment_directory_mode_change(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    (generation_dir / "environment/bin").chmod(0o700)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


def test_verify_rejects_modified_pyvenv_configuration(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    pyvenv = generation_dir / "environment/pyvenv.cfg"
    pyvenv.write_text(
        pyvenv.read_text(encoding="utf-8")
        + "include-system-site-packages = true\n"
    )

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


@pytest.mark.skipif(os.name == "nt", reason="mode validation is POSIX-only")
def test_verify_rejects_modified_environment_file_mode(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    (generation_dir / "environment/pyvenv.cfg").chmod(0o600)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


@pytest.mark.skipif(os.name == "nt", reason="symlink validation is POSIX-only")
def test_verify_rejects_replaced_environment_interpreter_link(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    python_path = generation_dir / "environment/bin/python"
    python_path.unlink()
    python_path.symlink_to("/bin/sh")

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


def test_verify_rejects_deleted_prepared_interpreter_without_changing_selection(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    active_path = tmp_path / "channelwatch-runtime/active.json"
    active_path.parent.mkdir(parents=True)
    active_before = b'{"path":"/previous/app","version":"1.4.3"}\n'
    active_path.write_bytes(active_before)
    generation.python_executable.unlink()

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)

    assert active_path.read_bytes() == active_before


@pytest.mark.skipif(os.name == "nt", reason="symlink hard links are POSIX-only")
def test_verify_rejects_hard_linked_environment_interpreter_link(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    python_path = generation_dir / "environment/bin/python"
    outside_alias = tmp_path / "environment-python-alias"
    os.link(python_path, outside_alias, follow_symlinks=False)
    assert python_path.lstat().st_nlink == 2

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


@pytest.mark.skipif(os.name == "nt", reason="symlink validation is POSIX-only")
def test_verify_rejects_new_environment_symlink_even_when_target_stays_inside(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )
    (generation_dir / "environment/new-link").symlink_to("pyvenv.cfg")

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.verify_runtime_generation(generation)


def test_prepare_discards_unreceipted_prebuilt_environment(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    copied = generation_dir / "environment/copied-from-build-machine"
    copied.mkdir(parents=True)

    generation = runtime_generation.prepare_runtime_generation(
        generation_dir, descriptor
    )

    assert not copied.exists()
    runtime_generation.verify_runtime_generation(generation)


def test_prepare_rejects_inventory_platform_or_count_mismatch(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    inventory_path = generation_dir / "dependencies/inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["counts"]["bytes"] += 1
    inventory_path.write_bytes(_json_bytes(inventory))
    descriptor["dependency_inventory_sha256"] = _sha256_bytes(
        inventory_path.read_bytes()
    )
    _refresh_generation_id(descriptor)
    wrong_path = generation_dir.with_name(descriptor["generation_id"])
    generation_dir.rename(wrong_path)
    (wrong_path / "channelwatch-bundle.json").write_bytes(_json_bytes(descriptor))

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(wrong_path, descriptor)


@pytest.mark.parametrize(
    ("field", "value"),
    [("libc_family", "musl"), ("minimum_libc_version", "2.37")],
)
def test_prepare_rejects_inventory_libc_mismatch(
    tmp_path, monkeypatch, field, value
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    inventory_path = generation_dir / "dependencies/inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["platform"][field] = value
    inventory_path.write_bytes(_json_bytes(inventory))
    descriptor["dependency_inventory_sha256"] = _sha256_bytes(
        inventory_path.read_bytes()
    )
    _refresh_generation_id(descriptor)
    wrong_path = generation_dir.with_name(descriptor["generation_id"])
    generation_dir.rename(wrong_path)
    (wrong_path / "channelwatch-bundle.json").write_bytes(_json_bytes(descriptor))

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(wrong_path, descriptor)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("builder_image_digest", "sha256:not-a-digest"),
        ("python_version", "3.13.0"),
        ("pip_version", ""),
    ],
)
def test_prepare_rejects_invalid_dependency_build_provenance(
    tmp_path, monkeypatch, field, value
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    inventory_path = generation_dir / "dependencies/inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["build"][field] = value
    inventory_path.write_bytes(_json_bytes(inventory))
    descriptor["dependency_inventory_sha256"] = _sha256_bytes(
        inventory_path.read_bytes()
    )
    _refresh_generation_id(descriptor)
    wrong_path = generation_dir.with_name(descriptor["generation_id"])
    generation_dir.rename(wrong_path)
    (wrong_path / "channelwatch-bundle.json").write_bytes(_json_bytes(descriptor))

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(wrong_path, descriptor)


@pytest.mark.skipif(os.name == "nt", reason="mode enforcement is POSIX-only")
def test_prepare_rejects_mode_mismatch(tmp_path, monkeypatch):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    payload = generation_dir / "core/main.py"
    payload.chmod(0o755)

    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(generation_dir, descriptor)


def test_descriptor_digest_is_canonical_and_generation_path_is_enforced(
    tmp_path, monkeypatch
):
    from core import runtime_generation

    generation_dir, descriptor = _generation_tree(tmp_path)
    monkeypatch.setattr(runtime_generation, "detect_runtime_platform", _platform)
    assert runtime_generation.descriptor_generation_id(descriptor) == generation_dir.name

    wrong = generation_dir.with_name("wrong-generation")
    generation_dir.rename(wrong)
    with pytest.raises(RuntimeGenerationError):
        runtime_generation.prepare_runtime_generation(wrong, descriptor)
