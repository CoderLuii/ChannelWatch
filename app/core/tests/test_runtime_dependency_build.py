import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import platform as host_platform
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
BUILDER_IMAGE_DIGEST = "sha256:" + "a" * 64


def _load_builder():
    path = ROOT / "scripts/release/build-runtime-dependencies.py"
    spec = importlib.util.spec_from_file_location("runtime_dependency_builder", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _write_wheel(
    directory: Path,
    *,
    name: str = "example-app",
    version: str = "1.0.0",
    tag: str = "py3-none-any",
    requires: tuple[str, ...] = (),
    native_member: bytes | None = None,
    unsafe_member: str | None = None,
    symlink_member: str | None = None,
    device_member: str | None = None,
    console_scripts: tuple[tuple[str, str], ...] = (),
    package_data: dict[str, bytes] | None = None,
) -> Path:
    normalized = name.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    metadata_lines = [
        "Metadata-Version: 2.4",
        f"Name: {name}",
        f"Version: {version}",
        "License-Expression: MIT",
        "Requires-Python: >=3.9",
    ]
    metadata_lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    python_tag, abi_tag, platform_tag = tag.split("-", 2)
    wheel_tags = [
        f"Tag: {python_value}-{abi_value}-{platform_value}"
        for python_value in python_tag.split(".")
        for abi_value in abi_tag.split(".")
        for platform_value in platform_tag.split(".")
    ]
    files = {
        f"{normalized}/__init__.py": b'VALUE = "fixture"\n',
        f"{dist_info}/METADATA": ("\n".join(metadata_lines) + "\n").encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: channelwatch-test\n"
            f"Root-Is-Purelib: {'false' if native_member is not None else 'true'}\n"
            + "\n".join(wheel_tags)
            + "\n"
        ).encode(),
        f"{dist_info}/licenses/LICENSE.txt": b"MIT fixture license\n",
    }
    if native_member is not None:
        files[f"{normalized}/native.so"] = native_member
    files.update(package_data or {})
    if console_scripts:
        files[f"{dist_info}/entry_points.txt"] = (
            "[console_scripts]\n"
            + "".join(f"{name} = {target}\n" for name, target in console_scripts)
        ).encode()
    if unsafe_member is not None:
        files[unsafe_member] = b"escape\n"

    rows = [
        (member, _record_hash(data), str(len(data)))
        for member, data in sorted(files.items())
    ]
    rows.append((f"{dist_info}/RECORD", "", ""))
    record_buffer = io.StringIO(newline="")
    csv.writer(record_buffer, lineterminator="\n").writerows(rows)
    files[f"{dist_info}/RECORD"] = record_buffer.getvalue().encode()

    wheel = directory / f"{normalized}-{version}-{tag}.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, data in sorted(files.items()):
            info = zipfile.ZipInfo(member, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
        if symlink_member is not None:
            info = zipfile.ZipInfo(symlink_member, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../outside")
        if device_member is not None:
            info = zipfile.ZipInfo(device_member, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFCHR | 0o600) << 16
            archive.writestr(info, b"")
    return wheel


def _write_inputs(
    tmp_path: Path,
    wheels: list[Path],
    *,
    requested: tuple[str, ...] = ("example-app==1.0.0",),
) -> tuple[Path, Path, Path]:
    requirements = tmp_path / "runtime.txt"
    requirements.write_text("\n".join(requested) + "\n", encoding="utf-8")
    lock = tmp_path / "runtime.lock"
    lines = []
    for wheel in wheels:
        parts = wheel.name[:-4].rsplit("-", 4)
        name = parts[0].replace("_", "-")
        version = parts[1]
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    lock.write_text("\n".join(lines) + "\n", encoding="utf-8")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    for wheel in wheels:
        wheel.replace(wheelhouse / wheel.name)
    return requirements, lock, wheelhouse


def _platform(module, *, wheel_platform: str = "manylinux_2_36_x86_64"):
    libc_family, libc_version = host_platform.libc_ver()
    libc_baseline = ".".join(libc_version.split(".")[:2])
    return module.TargetPlatform(
        architecture="amd64",
        python_implementation="cpython",
        python_cache_tag="cpython-314",
        python_version="3.14.8",
        os_family="linux",
        libc_family=libc_family,
        minimum_libc_version=libc_baseline,
        wheel_platform=wheel_platform,
        compatible_wheel_tags=(
            "py3-none-any",
            f"cp314-cp314-{wheel_platform}",
        ),
    )


def _build(module, tmp_path: Path, wheels: list[Path], **kwargs):
    requirements, lock, wheelhouse = _write_inputs(tmp_path, wheels, **kwargs)
    output = tmp_path / "output"
    inventory_path = module.build_runtime_dependencies(
        requirements_path=requirements,
        lock_path=lock,
        output_dir=output,
        wheelhouse=wheelhouse,
        platform=_platform(module),
        python_executable=sys.executable,
        builder_image_digest=BUILDER_IMAGE_DIGEST,
        acquire=False,
    )
    return output, json.loads(inventory_path.read_text(encoding="utf-8"))


def test_builds_real_wheel_offline_and_emits_strict_inventory(tmp_path):
    module = _load_builder()
    wheel = _write_wheel(tmp_path)

    output, inventory = _build(module, tmp_path, [wheel])

    assert (output / "dependencies/site-packages/example_app/__init__.py").is_file()
    assert set(inventory) == {
        "schema",
        "build",
        "platform",
        "requirements",
        "artifacts",
        "dependencies",
        "files",
        "counts",
    }
    assert set(inventory["build"]) == {
        "builder_image_digest",
        "python_version",
        "pip_version",
    }
    assert inventory["build"]["builder_image_digest"] == BUILDER_IMAGE_DIGEST
    assert inventory["build"]["python_version"] == "3.14.8"
    assert inventory["build"]["pip_version"]
    assert set(inventory["platform"]) == {
        "architecture",
        "python_implementation",
        "python_cache_tag",
        "python_version",
        "os_family",
        "libc_family",
        "minimum_libc_version",
        "wheel_platform",
    }
    assert set(inventory["requirements"]) == {"direct", "locked"}
    assert set(inventory["requirements"]["locked"][0]) == {"name", "version", "hashes"}
    assert set(inventory["artifacts"][0]) == {"filename", "sha256", "size", "source"}
    assert set(inventory["dependencies"][0]) == {
        "name",
        "version",
        "license_expression",
        "license_files",
    }
    assert set(inventory["files"][0]) == {"path", "sha256", "size", "mode"}
    assert set(inventory["counts"]) == {"files", "bytes"}
    assert inventory["requirements"]["direct"] == ["example-app"]
    assert inventory["requirements"]["locked"][0]["name"] == "example-app"
    assert inventory["dependencies"] == [
        {
            "name": "example-app",
            "version": "1.0.0",
            "license_expression": "MIT",
            "license_files": [
                "dependencies/site-packages/example_app-1.0.0.dist-info/licenses/LICENSE.txt"
            ],
        }
    ]
    assert inventory["counts"]["files"] == len(inventory["files"])
    assert inventory["counts"]["bytes"] == sum(
        item["size"] for item in inventory["files"]
    )
    assert all(
        item["path"].startswith("dependencies/site-packages/")
        for item in inventory["files"]
    )
    assert all(item["mode"] in {"0644", "0755"} for item in inventory["files"])
    assert inventory["artifacts"][0]["source"] == "https://pypi.org/simple"


@pytest.mark.parametrize("wheel_tag", ["py2.py3-none-any", "py3.py314-none-any"])
def test_accepts_valid_compressed_pure_python_wheel_tags(tmp_path, wheel_tag):
    module = _load_builder()
    wheel = _write_wheel(tmp_path, tag=wheel_tag)

    output, _ = _build(module, tmp_path, [wheel])

    assert (output / "dependencies/site-packages/example_app/__init__.py").is_file()


def test_preserves_required_transitive_dependency_instead_of_omitting_it(tmp_path):
    module = _load_builder()
    app_wheel = _write_wheel(tmp_path, requires=("example-helper==2.0.0",))
    helper_wheel = _write_wheel(tmp_path, name="example-helper", version="2.0.0")

    _, inventory = _build(module, tmp_path, [app_wheel, helper_wheel])

    assert [item["name"] for item in inventory["dependencies"]] == [
        "example-app",
        "example-helper",
    ]


def test_allows_management_package_only_when_required_transitively(tmp_path):
    module = _load_builder()
    app_wheel = _write_wheel(tmp_path, requires=("supervisor==4.3.0",))
    supervisor_wheel = _write_wheel(tmp_path, name="supervisor", version="4.3.0")

    _, inventory = _build(module, tmp_path, [app_wheel, supervisor_wheel])

    assert [item["name"] for item in inventory["dependencies"]] == [
        "example-app",
        "supervisor",
    ]


def test_rejects_unpinned_transitive_dependency(tmp_path):
    module = _load_builder()
    app_wheel = _write_wheel(tmp_path, requires=("example-helper==2.0.0",))
    requirements, lock, wheelhouse = _write_inputs(tmp_path, [app_wheel])

    with pytest.raises(module.DependencyBuildError, match="Dependency build command failed"):
        module.build_runtime_dependencies(
            requirements_path=requirements,
            lock_path=lock,
            output_dir=tmp_path / "output",
            wheelhouse=wheelhouse,
            platform=_platform(module),
            python_executable=sys.executable,
            builder_image_digest=BUILDER_IMAGE_DIGEST,
            acquire=False,
        )


def test_rejects_locked_package_outside_requested_dependency_closure(tmp_path):
    module = _load_builder()
    app_wheel = _write_wheel(tmp_path)
    unused_wheel = _write_wheel(tmp_path, name="unused-helper", version="2.0.0")
    requirements, lock, wheelhouse = _write_inputs(tmp_path, [app_wheel, unused_wheel])

    with pytest.raises(
        module.DependencyBuildError,
        match="outside the requested dependency closure",
    ):
        module.build_runtime_dependencies(
            requirements_path=requirements,
            lock_path=lock,
            output_dir=tmp_path / "output",
            wheelhouse=wheelhouse,
            platform=_platform(module),
            python_executable=sys.executable,
            builder_image_digest=BUILDER_IMAGE_DIGEST,
            acquire=False,
        )


@pytest.mark.parametrize(
    ("lock_text", "error"),
    [
        ("example-app>=1.0 --hash=sha256:" + "a" * 64, "exactly pinned"),
        ("example-app==1.0.0", "SHA-256 hash"),
        ("example-app==1.0.0 --hash=sha512:" + "a" * 128, "SHA-256"),
        ("example-app==1.0.0+custom --hash=sha256:" + "a" * 64, "Custom or local-version"),
        ("--extra-index-url https://example.com/simple", "Unsupported lock entry"),
        (
            "example-app @ https://example.com/example.whl --hash=sha256:" + "a" * 64,
            "Unsupported lock entry",
        ),
    ],
)
def test_rejects_incomplete_or_untrusted_lock_entries(tmp_path, lock_text, error):
    module = _load_builder()
    lock = tmp_path / "runtime.lock"
    lock.write_text(lock_text + "\n", encoding="utf-8")

    with pytest.raises(module.DependencyBuildError, match=error):
        module.parse_locked_requirements(lock)


def test_existing_constraints_are_not_treated_as_a_secure_hash_lock():
    module = _load_builder()

    with pytest.raises(module.DependencyBuildError, match="SHA-256 hash"):
        module.parse_locked_requirements(
            ROOT / "deploy/requirements/runtime.constraints.txt"
        )


def test_rejects_malformed_requested_requirement_instead_of_prefix_matching(tmp_path):
    module = _load_builder()
    requirements = tmp_path / "runtime.txt"
    requirements.write_text("example-app ??? unexpected\n", encoding="utf-8")

    with pytest.raises(module.DependencyBuildError, match="Unsupported requested requirement"):
        module.parse_requested_requirements(requirements)


@pytest.mark.parametrize("name", ["pip", "setuptools", "supervisor"])
def test_rejects_runtime_management_tools_when_directly_requested(tmp_path, name):
    module = _load_builder()
    requirements = tmp_path / "runtime.txt"
    requirements.write_text(f"{name}==1.0.0\n", encoding="utf-8")

    with pytest.raises(module.DependencyBuildError, match="image-owned management dependency"):
        module.parse_requested_requirements(requirements)


def test_rejects_source_distributions_and_unknown_wheelhouse_artifacts(tmp_path):
    module = _load_builder()
    lock = tmp_path / "runtime.lock"
    lock.write_text("example-app==1.0.0 --hash=sha256:" + "a" * 64 + "\n")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "example-app-1.0.0.tar.gz").write_bytes(b"source")

    with pytest.raises(module.DependencyBuildError, match="binary wheels only"):
        module.validate_wheelhouse(
            wheelhouse, module.parse_locked_requirements(lock), _platform(module)
        )


def test_rejects_native_wheel_for_wrong_platform(tmp_path):
    module = _load_builder()
    wheel = _write_wheel(
        tmp_path,
        tag="cp314-cp314-manylinux_2_36_aarch64",
        native_member=b"not an ELF",
    )
    _, lock, wheelhouse = _write_inputs(tmp_path, [wheel])

    with pytest.raises(module.DependencyBuildError, match="incompatible wheel tag"):
        module.validate_wheelhouse(
            wheelhouse, module.parse_locked_requirements(lock), _platform(module)
        )


@pytest.mark.parametrize(
    "wheel_factory,error",
    [
        (lambda root: _write_wheel(root, unsafe_member="../outside.py"), "unsafe path"),
        (lambda root: _write_wheel(root, symlink_member="example_app/link"), "non-regular"),
        (lambda root: _write_wheel(root, device_member="example_app/device"), "non-regular"),
    ],
)
def test_rejects_unsafe_wheel_members(tmp_path, wheel_factory, error):
    module = _load_builder()
    wheel = wheel_factory(tmp_path)
    _, lock, wheelhouse = _write_inputs(tmp_path, [wheel])

    with pytest.raises(module.DependencyBuildError, match=error):
        module.validate_wheelhouse(
            wheelhouse, module.parse_locked_requirements(lock), _platform(module)
        )


def test_rejects_native_dependency_with_unresolved_library(tmp_path, monkeypatch):
    module = _load_builder()
    wheel = _write_wheel(
        tmp_path,
        tag="cp314-cp314-manylinux_2_36_x86_64",
        native_member=b"ELF fixture",
    )
    requirements, lock, wheelhouse = _write_inputs(tmp_path, [wheel])

    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command[0] == "ldd":
            return subprocess.CompletedProcess(command, 0, "libmissing.so => not found\n", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.DependencyBuildError, match="unresolved native library"):
        module.build_runtime_dependencies(
            requirements_path=requirements,
            lock_path=lock,
            output_dir=tmp_path / "output",
            wheelhouse=wheelhouse,
            platform=_platform(module),
            python_executable=sys.executable,
            builder_image_digest=BUILDER_IMAGE_DIGEST,
            acquire=False,
        )


def test_build_is_deterministic_for_identical_locked_inputs(tmp_path):
    module = _load_builder()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_wheel = _write_wheel(first_root)
    second_wheel = _write_wheel(second_root)

    first_output, first_inventory = _build(module, first_root, [first_wheel])
    second_output, second_inventory = _build(module, second_root, [second_wheel])

    assert first_inventory == second_inventory
    assert (first_output / "dependencies/inventory.json").read_bytes() == (
        second_output / "dependencies/inventory.json"
    ).read_bytes()


def test_file_inventory_uses_serialized_posix_path_order(tmp_path):
    module = _load_builder()
    site_packages = tmp_path / "dependencies/site-packages"
    nested = site_packages / "example_app/foo/bar.py"
    sibling = site_packages / "example_app/foo.py"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"nested\n")
    sibling.write_bytes(b"sibling\n")

    paths = [item["path"] for item in module._file_inventory(site_packages)]

    assert paths == sorted(paths)
    assert paths == [
        "dependencies/site-packages/example_app/foo.py",
        "dependencies/site-packages/example_app/foo/bar.py",
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX interpreter path portability")
def test_console_scripts_are_removed_and_build_is_root_independent(tmp_path):
    module = _load_builder()
    inventories = []
    outputs = []
    interpreter_paths = []

    for root_name in ("first-root", "second-root"):
        root = tmp_path / root_name
        root.mkdir()
        interpreter = root / "toolchain/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        interpreter_paths.append(str(interpreter))
        wheel = _write_wheel(
            root,
            console_scripts=(("example-cli", "example_app:main"),),
            package_data={"example_app/data/template.txt": b"keep this data\n"},
        )
        requirements, lock, wheelhouse = _write_inputs(root, [wheel])
        output = root / "output"
        inventory_path = module.build_runtime_dependencies(
            requirements_path=requirements,
            lock_path=lock,
            output_dir=output,
            wheelhouse=wheelhouse,
            platform=_platform(module),
            python_executable=str(interpreter),
            builder_image_digest=BUILDER_IMAGE_DIGEST,
            acquire=False,
        )
        outputs.append(output)
        inventories.append(json.loads(inventory_path.read_text(encoding="utf-8")))

    assert inventories[0] == inventories[1]
    for output in outputs:
        site_packages = output / "dependencies/site-packages"
        assert not (site_packages / "bin").exists()
        assert (site_packages / "example_app/data/template.txt").read_bytes() == (
            b"keep this data\n"
        )
        payload = b"\n".join(
            path.read_bytes() for path in site_packages.rglob("*") if path.is_file()
        )
        assert all(path.encode() not in payload for path in interpreter_paths)


def test_rejects_host_libc_newer_than_declared_target_baseline(monkeypatch):
    module = _load_builder()
    target = _platform(module)
    target = target._replace(libc_family="glibc", minimum_libc_version="2.36")
    monkeypatch.setattr(module.host_platform, "libc_ver", lambda: ("glibc", "2.39"))

    with pytest.raises(module.DependencyBuildError, match="exact target libc baseline"):
        module._validate_native_host(target)


def test_rejects_host_libc_family_different_from_target(monkeypatch):
    module = _load_builder()
    target = _platform(module)._replace(
        libc_family="glibc", minimum_libc_version="2.36"
    )
    monkeypatch.setattr(module.host_platform, "libc_ver", lambda: ("musl", "1.2.5"))

    with pytest.raises(module.DependencyBuildError, match="exact target libc baseline"):
        module._validate_native_host(target)


@pytest.mark.parametrize(
    "identity",
    ["channelwatch:latest", "sha256:" + "A" * 64, "sha256:short"],
)
def test_rejects_mutable_or_malformed_builder_image_identity(tmp_path, identity):
    module = _load_builder()
    wheel = _write_wheel(tmp_path)
    requirements, lock, wheelhouse = _write_inputs(tmp_path, [wheel])

    with pytest.raises(module.DependencyBuildError, match="immutable sha256 digest"):
        module.build_runtime_dependencies(
            requirements_path=requirements,
            lock_path=lock,
            output_dir=tmp_path / "output",
            wheelhouse=wheelhouse,
            platform=_platform(module),
            python_executable=sys.executable,
            builder_image_digest=identity,
            acquire=False,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("pip_version", ["", "latest", "26.0 injected", "26/0"])
def test_rejects_unbounded_pip_provenance(monkeypatch, pip_version):
    module = _load_builder()
    target = _platform(module)
    output = json.dumps(
        [
            target.python_implementation,
            target.python_cache_tag,
            target.python_version,
            pip_version,
        ]
    )
    monkeypatch.setattr(
        module,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, output, ""),
    )

    with pytest.raises(module.DependencyBuildError, match="target pip version"):
        module._validate_python_identity(sys.executable, target)


def test_acquisition_uses_hashes_binary_only_and_offline_install_flags(tmp_path, monkeypatch):
    module = _load_builder()
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    lock = tmp_path / "runtime.lock"
    lock.write_text("example-app==1.0.0 --hash=sha256:" + "a" * 64 + "\n")
    wheelhouse = tmp_path / "wheelhouse"
    target = tmp_path / "site-packages"

    module.acquire_wheels(
        python_executable=sys.executable,
        lock_path=lock,
        wheelhouse=wheelhouse,
    )
    module.install_wheels_offline(
        python_executable=sys.executable,
        lock_path=lock,
        wheelhouse=wheelhouse,
        target=target,
    )

    assert "--require-hashes" in commands[0]
    assert "--only-binary=:all:" in commands[0]
    assert "download" in commands[0]
    assert "--no-index" in commands[1]
    assert "--find-links" in commands[1]
    assert "--require-hashes" in commands[1]
    assert "--only-binary=:all:" in commands[1]
    assert "install" in commands[1]
