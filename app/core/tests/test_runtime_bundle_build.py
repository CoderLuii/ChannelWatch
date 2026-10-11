import hashlib
import importlib.util
import json
import os
import platform
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest

from core import runtime_archive
from core.runtime_generation import detect_runtime_platform
from core.tests.test_runtime_dependency_build import (
    _build as _build_dependencies,
)
from core.tests.test_runtime_dependency_build import (
    _load_builder as _load_dependency_builder,
)
from core.tests.test_runtime_dependency_build import _write_wheel

@pytest.fixture(autouse=True)
def _protocol_four_runtime_capability(monkeypatch):
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "4")



ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/build-runtime-bundle.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("runtime_bundle_builder", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _record(path: str, content: bytes, mode: str = "0644") -> dict:
    return {
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "mode": mode,
    }


def _write(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def _payload(root: Path) -> tuple[Path, Path]:
    app_root = root / "application"
    dependency_root = root / "dependency"
    app_files = {
        "core/main.py": (b"print('core')\n", 0o755),
        "core/release_legal/LICENSE": (b"application license\n", 0o644),
        "core/release_legal/NOTICE": (b"application notice\n", 0o644),
        "core/release_legal/THIRD_PARTY_LICENSES.md": (b"third party\n", 0o644),
        "ui/backend/main.py": (b"print('ui')\n", 0o644),
    }
    dependency_files = {
        "dependencies/site-packages/demo.py": b"VALUE = 1\n",
        "dependencies/site-packages/demo-1.0.dist-info/LICENSE": b"MIT\n",
    }
    for relative, (content, mode) in app_files.items():
        _write(app_root / relative, content, mode)
    for relative, content in dependency_files.items():
        _write(dependency_root / relative, content)

    runtime_platform = detect_runtime_platform()
    libc_version = ".".join(str(item) for item in runtime_platform.libc_version)
    dependency_records = [
        _record(relative, content)
        for relative, content in sorted(dependency_files.items())
    ]
    inventory = {
        "schema": 1,
        "build": {
            "builder_image_digest": "sha256:" + "f" * 64,
            "python_version": platform.python_version(),
            "pip_version": "25.3",
        },
        "platform": {
            "architecture": runtime_platform.architecture,
            "python_implementation": "cpython",
            "python_cache_tag": runtime_platform.python_cache_tag,
            "python_version": platform.python_version(),
            "os_family": "linux",
            "libc_family": runtime_platform.libc_family,
            "minimum_libc_version": libc_version,
            "wheel_platform": "manylinux_2_36",
        },
        "requirements": {
            "direct": ["demo"],
            "locked": [{"name": "demo", "version": "1.0", "hashes": ["a" * 64]}],
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
                "license_files": [
                    "dependencies/site-packages/demo-1.0.dist-info/LICENSE"
                ],
            }
        ],
        "files": dependency_records,
        "counts": {
            "files": len(dependency_records),
            "bytes": sum(record["size"] for record in dependency_records),
        },
    }
    inventory_path = dependency_root / "dependencies/inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return app_root, dependency_root


def _build(module, app_root: Path, dependency_root: Path, output: Path) -> dict:
    return module.build_runtime_bundle(
        app_root=app_root,
        dependency_root=dependency_root,
        output_path=output,
        application_version="1.4.4",
        source_commit="a" * 40,
        settings_schema_version=7,
        source_date_epoch=1_700_000_000,
    )


def test_runtime_bundle_is_root_independent_and_validator_compatible(tmp_path: Path):
    module = _load_builder()
    first_app, first_dependencies = _payload(tmp_path / "first")
    second_app, second_dependencies = _payload(tmp_path / "second")
    changed_paths = (
        second_app / "core/main.py",
        second_dependencies / "dependencies/site-packages/demo.py",
    )
    for path in changed_paths:
        os.utime(path, (time.time() + 200, time.time() + 200))

    first_output = tmp_path / "first.zip"
    second_output = tmp_path / "second.zip"
    first_descriptor = _build(module, first_app, first_dependencies, first_output)
    second_descriptor = _build(module, second_app, second_dependencies, second_output)

    assert first_output.read_bytes() == second_output.read_bytes()
    assert first_descriptor == second_descriptor
    runtime_archive.validate_runtime_archive(first_output.read_bytes(), first_descriptor)
    with zipfile.ZipFile(first_output) as archive:
        assert archive.namelist() == sorted(
            [
                "channelwatch-bundle.json",
                "dependencies/inventory.json",
                *(record["path"] for record in first_descriptor["files"]),
            ]
        )
        modes = {
            info.filename: stat.S_IMODE(info.external_attr >> 16)
            for info in archive.infolist()
        }
    assert modes["core/main.py"] == 0o755
    assert set(modes.values()) <= {0o644, 0o755}
    assert first_descriptor["source_commit"] == "a" * 40
    assert first_descriptor["application_version"] == "1.4.4"
    assert first_descriptor["libc_family"] == detect_runtime_platform().libc_family


def test_official_dependency_builder_output_packages_without_schema_drift(
    tmp_path: Path,
):
    dependency_builder = _load_dependency_builder()
    bundle_builder = _load_builder()
    wheel = _write_wheel(tmp_path)
    build_root = tmp_path / "dependency-build"
    build_root.mkdir()
    dependency_root, inventory = _build_dependencies(
        dependency_builder, build_root, [wheel]
    )
    app_root, _fixture_dependencies = _payload(tmp_path / "application-fixture")
    output = tmp_path / "runtime.zip"

    descriptor = _build(bundle_builder, app_root, dependency_root, output)

    assert inventory["build"] == json.loads(
        (dependency_root / "dependencies/inventory.json").read_text(encoding="utf-8")
    )["build"]
    runtime_archive.validate_runtime_archive(output.read_bytes(), descriptor)


def test_runtime_bundle_rejects_dependency_inventory_tamper(tmp_path: Path):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    (dependency_root / "dependencies/site-packages/demo.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )

    with pytest.raises(module.RuntimeBundleBuildError, match="inventory"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("builder_image_digest", "sha256:not-a-digest"),
        ("python_version", "3.13.0"),
        ("pip_version", "latest"),
    ],
)
def test_runtime_bundle_rejects_invalid_dependency_build_provenance(
    tmp_path: Path, field: str, value: str
):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    inventory_path = dependency_root / "dependencies/inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["build"][field] = value
    inventory_path.write_text(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(module.RuntimeBundleBuildError, match="build|builder|pip"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
def test_runtime_bundle_rejects_links(tmp_path: Path):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    (app_root / "core/link.py").symlink_to("main.py")

    with pytest.raises(module.RuntimeBundleBuildError, match="symbolic link"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


@pytest.mark.skipif(os.name == "nt", reason="POSIX backslash filename fixture")
def test_runtime_bundle_rejects_unsafe_paths(tmp_path: Path):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    _write(app_root / "core/bad\\name.py", b"bad\n")

    with pytest.raises(module.RuntimeBundleBuildError, match="safe relative path"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


def test_runtime_bundle_rejects_casefold_path_collisions(tmp_path: Path):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    _write(app_root / "core/Feature.py", b"first\n")
    _write(app_root / "core/feature.py", b"second\n")

    with pytest.raises(module.RuntimeBundleBuildError, match="collision"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


def test_runtime_bundle_rejects_member_count_over_archive_limit(tmp_path: Path, monkeypatch):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    monkeypatch.setattr(module, "MAX_BUNDLE_MEMBER_COUNT", 8)

    with pytest.raises(module.RuntimeBundleBuildError, match="too many members"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


@pytest.mark.parametrize(
    ("limit_name", "match"),
    [
        ("MAX_BUNDLE_MEMBER_BYTES", "member size"),
        ("MAX_BUNDLE_TOTAL_UNCOMPRESSED_BYTES", "expanded size"),
        ("MAX_BUNDLE_BYTES", "download size"),
    ],
)
def test_runtime_bundle_rejects_archive_size_limits(
    tmp_path: Path, monkeypatch, limit_name: str, match: str
):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    monkeypatch.setattr(module, limit_name, 1)

    with pytest.raises(module.RuntimeBundleBuildError, match=match):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO fixture")
def test_runtime_bundle_rejects_special_files(tmp_path: Path):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    os.mkfifo(app_root / "core/pipe")

    with pytest.raises(module.RuntimeBundleBuildError, match="special entry"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")


def test_runtime_bundle_refuses_existing_output_without_modifying_it(tmp_path: Path):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    output = tmp_path / "bundle.zip"
    output.write_bytes(b"existing")

    with pytest.raises(module.RuntimeBundleBuildError, match="already exists"):
        _build(module, app_root, dependency_root, output)

    assert output.read_bytes() == b"existing"


@pytest.mark.parametrize("relative", ["prepared.json", "environment/pyvenv.cfg"])
def test_runtime_bundle_rejects_local_runtime_entries(tmp_path: Path, relative: str):
    module = _load_builder()
    app_root, dependency_root = _payload(tmp_path)
    _write(app_root / relative, b"local\n")

    with pytest.raises(module.RuntimeBundleBuildError, match="runtime path|application roots"):
        _build(module, app_root, dependency_root, tmp_path / "bundle.zip")
