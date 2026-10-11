from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/release/verify-native-image.py"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_extract_rejects_traversal_and_links(tmp_path):
    module = _module("native_image_safe_extract")
    for name, configure in (
        ("../escape", lambda info: None),
        ("link", lambda info: setattr(info, "type", tarfile.SYMTYPE)),
    ):
        archive = tmp_path / f"{name.replace('/', '-')}.tar"
        with tarfile.open(archive, "w") as bundle:
            info = tarfile.TarInfo(name)
            info.size = 0
            configure(info)
            bundle.addfile(info, io.BytesIO())
        with pytest.raises(module.NativeImageError, match="unsafe member"):
            module._safe_extract(archive, tmp_path / (archive.stem + "-out"))


def test_selected_archive_binds_exact_config_and_rootfs(tmp_path):
    module = _module("native_image_selected_archive")
    diff_id = "sha256:" + "d" * 64
    config_raw = json.dumps({"rootfs": {"diff_ids": [diff_id]}}).encode()
    config_sha = hashlib.sha256(config_raw).hexdigest()
    config_name = config_sha + ".json"
    manifest_raw = json.dumps([{
        "Config": config_name,
        "RepoTags": ["channelwatch-native:owned"],
        "Layers": ["layer.tar"],
    }]).encode()
    archive = tmp_path / "selected.tar"
    with tarfile.open(archive, "w") as bundle:
        for name, content in (("manifest.json", manifest_raw), (config_name, config_raw)):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))

    assert module._verify_selected_archive(
        archive, "sha256:" + config_sha, "channelwatch-native:owned"
    ) == [diff_id]
    with pytest.raises(module.NativeImageError, match="approved descriptor"):
        module._verify_selected_archive(
            archive, "sha256:" + "a" * 64, "channelwatch-native:owned"
        )
    unsafe = tmp_path / "unsafe-selected.tar"
    with tarfile.open(unsafe, "w") as bundle:
        link = tarfile.TarInfo("layers/layer.tar")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside.tar"
        bundle.addfile(link)
    with pytest.raises(module.NativeImageError, match="unsafe member"):
        module._verify_selected_archive(
            unsafe, "sha256:" + config_sha, "channelwatch-native:owned"
        )


def test_native_architecture_rejects_host_docker_or_requested_mismatch(monkeypatch):
    module = _module("native_image_architecture")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, "aarch64\n", "")

    with pytest.raises(module.NativeImageError, match="must match natively"):
        module._native_architecture(runner, "amd64")


def test_wait_ready_requires_the_exact_startup_health_schema(monkeypatch):
    module = _module("native_image_strict_readiness")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"status":"not_ready"}'

    times = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(module.NativeImageError, match="did not become ready"):
        module._wait_ready(8501, 1)


@pytest.mark.parametrize("title", ["", "Not ChannelWatch"])
def test_browser_smoke_rejects_blank_or_wrong_title(title):
    module = _module("native_image_browser_title_" + (title or "blank").replace(" ", "_"))

    def runner(command, **_kwargs):
        script = command[command.index("-e") + 1]
        assert "auth-bootstrap-shell" in script
        assert "auth-login-shell" in script
        assert "auth-noauth-shell" in script
        assert "title!=='ChannelWatch'" in script
        output = json.dumps({"title": title, "hydrated_shell": True}) + "\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    with pytest.raises(module.NativeImageError, match="browser evidence"):
        module._browser_smoke(runner, 8501)


@pytest.mark.parametrize(
    "identity",
    [
        {"image_version": "1.5.0", "launcher_protocol": 3},
        {"image_version": "1.4.1", "launcher_protocol": 4},
        {"image_version": "1.5.0", "launcher_protocol": True},
        {"image_version": "1.5.0", "launcher_protocol": "4"},
        {},
        [],
    ],
)
def test_image_runtime_identity_rejects_wrong_embedded_version_or_protocol(identity):
    module = _module("native_image_runtime_identity_reject")

    def runner(command, **_kwargs):
        assert command[:4] == ["docker", "exec", "owned-container", "/venv/bin/python"]
        assert "-I" in command
        assert "/app/core/runtime_launcher.py" in command[-1]
        return subprocess.CompletedProcess(command, 0, json.dumps(identity), "")

    with pytest.raises(module.NativeImageError, match="protocol-4"):
        module._image_runtime_identity(runner, "owned-container", "1.5.0")


def test_image_runtime_identity_resolves_the_image_owned_launcher():
    module = _module("native_image_runtime_identity_pass")

    def runner(command, **_kwargs):
        assert "launcher_protocol_status" in command[-1]
        assert "runpy.run_path" in command[-1]
        return subprocess.CompletedProcess(
            command, 0, '{"image_version":"1.5.0","launcher_protocol":4}', ""
        )

    assert module._image_runtime_identity(runner, "owned-container", "1.5.0") == 4


def test_qualification_binds_exact_oci_and_preserves_same_container(monkeypatch, tmp_path):
    module = _module("native_image_qualification")
    archive = tmp_path / "candidate.tar"
    archive.write_bytes(b"exact archive")
    config_digest = "sha256:" + "a" * 64
    descriptor = {
        "schema": 1,
        "image_index": {"mediaType": "index", "digest": "sha256:" + "b" * 64, "size": 1},
        "platforms": [
            {
                "platform": "linux/amd64",
                "manifest": {"digest": "sha256:" + "c" * 64},
                "config": {"digest": config_digest},
            }
        ],
    }
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    monkeypatch.setattr(module, "_native_architecture", lambda _runner, requested: requested)
    monkeypatch.setattr(module, "_safe_extract", lambda _archive, destination: destination.mkdir())
    monkeypatch.setattr(module, "_load_describer", lambda: SimpleNamespace(describe_layout=lambda _path: descriptor))
    readiness_ports = []
    monkeypatch.setattr(
        module,
        "_wait_ready",
        lambda port, _timeout: readiness_ports.append(port) or b'{"status":"ready"}',
    )
    monkeypatch.setattr(module, "_verify_selected_archive", lambda *_args: ["sha256:" + "9" * 64])
    monkeypatch.setattr(
        module,
        "_preservation",
        lambda _runner, _container: {
            "/config/settings.json": "d" * 64,
            "/config/encryption.key": "e" * 64,
        },
    )

    commands = []
    loaded_tag = ""

    def runner(command, **_kwargs):
        nonlocal loaded_tag
        commands.append(command)
        output = ""
        stderr = ""
        archive_target = next((item for item in command if item.startswith("docker-archive:")), "")
        if archive_target:
            output_mount = next(item for item in command if item.startswith("--volume") is False and ":/output" in item)
            selected_path = Path(output_mount.rsplit(":/output", 1)[0]) / "selected.tar"
            assert selected_path.is_file()
            loaded_tag = archive_target.rsplit(":", 2)[-2] + ":" + archive_target.rsplit(":", 1)[-1]
        if command[:3] == ["docker", "load", "--input"]:
            output = f"Loaded image: {loaded_tag}\n"
        elif command[:3] == ["docker", "image", "inspect"]:
            output = json.dumps(
                [
                    {
                        "Architecture": "amd64",
                        "Os": "linux",
                        "Id": config_digest,
                        "RootFS": {"Layers": ["sha256:" + "9" * 64]},
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.version": "1.5.0",
                                "org.opencontainers.image.revision": "f" * 40,
                            }
                        },
                    }
                ]
            )
        elif command[:2] == ["docker", "port"]:
            port_calls = sum(1 for item in commands if item[:2] == ["docker", "port"])
            output = f"127.0.0.1:{49151 + port_calls}\n"
        elif command[:3] == ["docker", "inspect", "--format"]:
            output = "same-container\n"
        elif command[:2] == ["docker", "logs"]:
            output = "candidate log\n"
            stderr = "candidate warning\n"
        elif any("auth-bootstrap-shell" in str(item) for item in command):
            output = json.dumps({"title": "ChannelWatch", "hydrated_shell": True}) + "\n"
        elif command[:2] == ["docker", "exec"] and "launcher_protocol_status" in command[-1]:
            output = '{"image_version":"1.5.0","launcher_protocol":4}\n'
        elif command[:2] == ["docker", "exec"]:
            output = "configuration valid\n"
        return subprocess.CompletedProcess(command, 0, output, stderr)

    receipt = module.qualify(
        Namespace(
            oci_archive=archive,
            descriptor=descriptor_path,
            expected_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            expected_descriptor_sha256=hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
            source_commit="f" * 40,
            version="1.5.0",
            architecture="amd64",
            skopeo_image="skopeo@sha256:" + "1" * 64,
            log_output=tmp_path / "native.log",
            startup_timeout=1,
        ),
        runner=runner,
    )

    assert receipt["status"] == "passed"
    assert receipt["native"] is True
    assert receipt["launcher_protocol"] == 4
    assert sum("launcher_protocol_status" in command[-1] for command in commands) == 2
    assert receipt["config_digest"] == config_digest
    assert readiness_ports == [49152, 49153]
    assert receipt["settings_key_preserved"]["/config/encryption.key"] == "e" * 64
    retained_logs = (tmp_path / "native.log").read_bytes()
    assert b"candidate log" in retained_logs
    assert b"candidate warning" in retained_logs
    assert receipt["container_log_sha256"] == hashlib.sha256(retained_logs).hexdigest()
    assert any(command[:3] == ["docker", "rm", "-f"] for command in commands)
    assert any(command[:4] == ["docker", "volume", "rm", "-f"] for command in commands)
    assert ["docker", "image", "rm", "-f", loaded_tag] in commands


def test_qualification_preserves_failure_logs_and_checks_cleanup(monkeypatch, tmp_path):
    module = _module("native_image_failure_cleanup")
    archive = tmp_path / "candidate.tar"
    archive.write_bytes(b"exact archive")
    config_digest = "sha256:" + "a" * 64
    descriptor = {
        "schema": 1,
        "image_index": {"mediaType": "index", "digest": "sha256:" + "b" * 64, "size": 1},
        "platforms": [{
            "platform": "linux/amd64",
            "manifest": {"digest": "sha256:" + "c" * 64},
            "config": {"digest": config_digest},
        }],
    }
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    monkeypatch.setattr(module, "_native_architecture", lambda _runner, requested: requested)
    monkeypatch.setattr(module, "_safe_extract", lambda _archive, destination: destination.mkdir())
    monkeypatch.setattr(module, "_load_describer", lambda: SimpleNamespace(describe_layout=lambda _path: descriptor))
    monkeypatch.setattr(module, "_wait_ready", lambda _port, _timeout: b'{"status":"ready"}')
    monkeypatch.setattr(module, "_verify_selected_archive", lambda *_args: ["sha256:" + "9" * 64])

    commands = []
    loaded_tag = ""

    def runner(command, **_kwargs):
        nonlocal loaded_tag
        commands.append(command)
        archive_target = next((item for item in command if item.startswith("docker-archive:")), "")
        if archive_target:
            loaded_tag = archive_target.rsplit(":", 2)[-2] + ":" + archive_target.rsplit(":", 1)[-1]
        if command[:3] == ["docker", "load", "--input"]:
            return subprocess.CompletedProcess(command, 0, f"Loaded image: {loaded_tag}\n", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                "Architecture": "amd64", "Os": "linux", "Id": config_digest,
                "RootFS": {"Layers": ["sha256:" + "9" * 64]},
                "Config": {"Labels": {
                    "org.opencontainers.image.version": "1.5.0",
                    "org.opencontainers.image.revision": "f" * 40,
                }},
            }]), "")
        if command[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(command, 0, "127.0.0.1:49152\n", "")
        if any("auth-bootstrap-shell" in str(item) for item in command):
            return subprocess.CompletedProcess(command, 2, "", "browser failed")
        if command[:2] == ["docker", "exec"] and "launcher_protocol_status" in command[-1]:
            return subprocess.CompletedProcess(
                command, 0, '{"image_version":"1.5.0","launcher_protocol":4}', ""
            )
        if command[:2] == ["docker", "logs"]:
            return subprocess.CompletedProcess(command, 0, "stdout log\n", "stderr log\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    args = Namespace(
        oci_archive=archive, descriptor=descriptor_path,
        expected_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        expected_descriptor_sha256=hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
        source_commit="f" * 40, version="1.5.0", architecture="amd64",
        skopeo_image="skopeo@sha256:" + "1" * 64,
        log_output=tmp_path / "failed.log", startup_timeout=1,
    )
    with pytest.raises(module.NativeImageError, match="Command failed"):
        module.qualify(args, runner=runner)

    assert b"stdout log" in args.log_output.read_bytes()
    assert b"stderr log" in args.log_output.read_bytes()
    assert any(command[:3] == ["docker", "rm", "-f"] for command in commands)
    assert ["docker", "image", "rm", "-f", loaded_tag] in commands


def test_cleanup_reports_owned_resource_removal_failures():
    module = _module("native_image_cleanup_failure")

    def runner(command, **_kwargs):
        returncode = 1 if command[:3] == ["docker", "image", "rm"] else 0
        return subprocess.CompletedProcess(command, returncode, "", "remove failed" if returncode else "")

    failures = module._cleanup_resources(
        runner,
        container="owned-container",
        container_created=True,
        volume="owned-volume",
        volume_created=True,
        image="owned-image",
    )

    assert failures == ["image"]


def test_main_writes_failure_receipt(monkeypatch, tmp_path):
    module = _module("native_image_failure_receipt")
    archive = tmp_path / "candidate.tar"
    descriptor = tmp_path / "descriptor.json"
    archive.write_bytes(b"archive")
    descriptor.write_text("{}", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    log = tmp_path / "native.log"
    log.write_bytes(b"diagnostic log")
    def fail_qualification(args):
        args.log_output.write_bytes(b"diagnostic log")
        raise module.NativeImageError("native check failed")

    monkeypatch.setattr(module, "qualify", fail_qualification)
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--oci-archive", str(archive), "--descriptor", str(descriptor),
        "--expected-archive-sha256", hashlib.sha256(archive.read_bytes()).hexdigest(),
        "--expected-descriptor-sha256", hashlib.sha256(descriptor.read_bytes()).hexdigest(),
        "--source-commit", "f" * 40, "--version", "1.5.0", "--architecture", "amd64",
        "--skopeo-image", "skopeo@sha256:" + "1" * 64, "--receipt", str(receipt),
        "--log-output", str(log),
    ])

    assert module.main() == 1
    failed = json.loads(receipt.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["architecture"] == "amd64"
    assert failed["container_log_sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()
