from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
BRIDGE_PATH = ROOT / "app" / "core" / "runtime_dependency_bridge.py"


def _isolated_signal_test(test_name: str) -> bool:
    if os.environ.get("CHANNELWATCH_SIGNAL_TEST_CHILD") == test_name:
        return False
    environment = os.environ.copy()
    environment["CHANNELWATCH_SIGNAL_TEST_CHILD"] = test_name
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", f"{__file__}::{test_name}"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return True


def _load_bridge():
    spec = importlib.util.spec_from_file_location(
        "runtime_dependency_bridge_under_test", BRIDGE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prepared_generation(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    config_dir = tmp_path / "config"
    runtime_dir = config_dir / "channelwatch-runtime"
    bootstrap_files = {
        "core/runtime_dependency_bridge.py": b"# bridge\n",
        "core/runtime_generation.py": (
            b"class RuntimeGeneration:\n"
            b"    def __init__(self, **values): self.__dict__.update(values)\n"
            b"def verify_runtime_generation(runtime): return None\n"
        ),
        "core/runtime_worker.py": b"# worker\n",
    }
    mode_probe = tmp_path / "worker-mode-probe.py"
    mode_probe.write_bytes(b"probe\n")
    mode_probe.chmod(0o644)
    worker_mode = f"0{stat.S_IMODE(mode_probe.stat().st_mode):03o}"
    mode_probe.unlink()
    descriptor: dict[str, object] = {
        "schema": 1,
        "bundle_format": "channelwatch-runtime-generation-v1",
        "required_launcher_capability": "isolated_dependencies_v1",
        "dependency_inventory_sha256": "b" * 64,
        "files": [
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mode": worker_mode,
                "size": len(raw),
            }
            for relative, raw in sorted(bootstrap_files.items())
        ],
    }
    descriptor_sha256 = _canonical_sha256(descriptor)
    descriptor["generation_id"] = descriptor_sha256
    generation = runtime_dir / "releases" / descriptor_sha256
    (generation / "core").mkdir(parents=True)
    interpreter = generation / "environment" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    if os.name == "nt":
        os.link(sys.executable, interpreter)
    else:
        interpreter.symlink_to(sys.executable)
    for relative, raw in bootstrap_files.items():
        path = generation / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(int(worker_mode, 8))
    (generation / "channelwatch-bundle.json").write_text(
        json.dumps(descriptor), encoding="utf-8"
    )
    (generation / "prepared.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "generation_id": descriptor_sha256,
                "descriptor_sha256": descriptor_sha256,
                "dependency_digest": "b" * 64,
                "python_executable": "environment/bin/python",
            }
        ),
        encoding="utf-8",
    )
    active = {
        "path": str(generation),
        "activation_id": "activation-1",
        "runtime_generation_id": descriptor_sha256,
        "runtime_descriptor_sha256": descriptor_sha256,
        "dependency_inventory_sha256": "b" * 64,
    }
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "active.json").write_text(json.dumps(active), encoding="utf-8")
    return config_dir, generation, active


def test_bridge_module_is_stdlib_only_before_selected_dependencies_load():
    tree = ast.parse(BRIDGE_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots <= set(sys.stdlib_module_names) | {"__future__"}


def test_core_package_does_not_auto_start_the_legacy_dependency_bridge():
    source = (ROOT / "app" / "core" / "__init__.py").read_text(encoding="utf-8")

    assert "bootstrap_dependency_worker" not in source
    assert "runtime_dependency_bridge" not in source
    assert source.index("_channelwatch_runtime_worker_validated") < source.index(
        "from sqlmodel"
    )


def test_selected_generation_import_does_not_start_legacy_dependency_worker(
    tmp_path: Path,
):
    staging = tmp_path / "staging"
    core_dir = staging / "core"
    core_dir.mkdir(parents=True)
    for name in ("__init__.py", "runtime_dependency_bridge.py"):
        (core_dir / name).write_bytes((ROOT / "app" / "core" / name).read_bytes())
    (core_dir / "runtime_generation.py").write_text(
        "class RuntimeGeneration:\n"
        "    def __init__(self, **values): self.__dict__.update(values)\n"
        "def verify_runtime_generation(runtime): return None\n",
        encoding="utf-8",
    )
    worker_marker = tmp_path / "legacy-worker-ran"
    (core_dir / "runtime_worker.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(worker_marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    files = []
    for path in sorted(core_dir.iterdir()):
        raw = path.read_bytes()
        files.append(
            {
                "path": f"core/{path.name}",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mode": f"0{stat.S_IMODE(path.stat().st_mode):03o}",
                "size": len(raw),
            }
        )
    descriptor: dict[str, object] = {
        "schema": 1,
        "bundle_format": "channelwatch-runtime-generation-v1",
        "required_launcher_capability": "isolated_dependencies_v1",
        "dependency_inventory_sha256": "b" * 64,
        "files": files,
    }
    generation_id = _canonical_sha256(descriptor)
    descriptor["generation_id"] = generation_id
    runtime_dir = tmp_path / "config" / "channelwatch-runtime"
    generation = runtime_dir / "releases" / generation_id
    generation.parent.mkdir(parents=True)
    staging.rename(generation)
    (generation / "channelwatch-bundle.json").write_text(
        json.dumps(descriptor), encoding="utf-8"
    )
    interpreter = generation / "environment" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    if os.name == "nt":
        os.link(sys.executable, interpreter)
    else:
        interpreter.symlink_to(sys.executable)
    (generation / "prepared.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "generation_id": generation_id,
                "descriptor_sha256": generation_id,
                "dependency_digest": "b" * 64,
                "python_executable": "environment/bin/python",
            }
        ),
        encoding="utf-8",
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "active.json").write_text(
        json.dumps(
            {
                "path": str(generation),
                "activation_id": "activation-1",
                "runtime_generation_id": generation_id,
                "runtime_descriptor_sha256": generation_id,
                "dependency_inventory_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment["CONFIG_PATH"] = str(tmp_path / "config")
    environment["CHANNELWATCH_APP_DIR"] = str(generation)
    environment["CHANNELWATCH_ACTIVATION_ID"] = "activation-1"
    environment["CHANNELWATCH_LAUNCHER_PROTOCOL"] = "3"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import sys; sys.path.insert(0, {str(generation)!r}); "
                "sys.argv = ['python -m core.main']; "
                "exec('try:\\n import core\\nexcept BaseException:\\n pass')"
            ),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert not worker_marker.exists()


def test_bridge_never_execs_away_image_parent_or_watchdogs():
    tree = ast.parse(BRIDGE_PATH.read_text(encoding="utf-8"))
    called_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert not called_names.intersection(
        {"execv", "execve", "execvp", "execvpe", "execl", "execlp", "execlpe"}
    )


def test_bridge_builds_fresh_worker_argv_from_verified_generation(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir, generation, active = _prepared_generation(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(generation))
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "3")
    monkeypatch.setattr(bridge.sys, "argv", ["python -m core.main", "--stay-alive"])

    invocation = bridge.validated_worker_invocation()

    assert invocation is not None
    assert invocation.mode == "core"
    assert invocation.generation == generation.resolve()
    assert invocation.activation_id == active["activation_id"]
    assert invocation.argv == (
        str(generation / "environment" / "bin" / "python"),
        "-I",
        "-B",
        "-S",
        "-c",
        bridge._WORKER_SNAPSHOT_BOOTSTRAP,
        str(generation / "core" / "runtime_worker.py"),
        "--mode",
        "core",
        "--generation",
        str(generation.resolve()),
        "--activation-id",
        "activation-1",
        "--descriptor-sha256",
        active["runtime_descriptor_sha256"],
        "--",
        "--stay-alive",
    )


def test_image_boundary_launches_only_the_image_owned_worker(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    generation = tmp_path / "runtime/releases/generation-1"
    (generation / "core").mkdir(parents=True)
    generation_worker = generation / "core/runtime_worker.py"
    generation_worker.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    observed = []
    monkeypatch.setattr(
        bridge,
        "_validate_explicit_generation",
        lambda **_kwargs: generation.resolve(),
    )
    monkeypatch.setattr(
        bridge,
        "supervise_worker",
        lambda invocation, **kwargs: observed.append((invocation, kwargs)),
    )

    bridge.supervise_image_worker(
        image_app_dir=ROOT / "app",
        runtime_dir=tmp_path / "runtime",
        generation=generation,
        activation_id="activation-1",
        descriptor_sha256="a" * 64,
        mode="core",
        app_args=("--stay-alive",),
        host=None,
        port=None,
        log_level=None,
    )

    invocation, kwargs = observed[0]
    image_worker = ROOT / "app/core/runtime_worker.py"
    assert invocation.argv[:5] == (
        str(generation.resolve() / "environment/bin/python"),
        "-I",
        "-B",
        "-S",
        str(image_worker),
    )
    assert str(generation_worker) not in invocation.argv
    assert kwargs == {"image_app_dir": (ROOT / "app").resolve()}


@pytest.mark.skipif(os.name == "nt", reason="POSIX generation interpreter layout")
def test_verified_generation_interpreter_keeps_isolated_stdlib_paths(tmp_path: Path):
    environment = tmp_path / "generation/environment"
    interpreter = environment / "bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    base_prefix = Path(sys.base_prefix)
    (environment / "pyvenv.cfg").write_text(
        f"home = {base_prefix / 'bin'}\n"
        "include-system-site-packages = false\n"
        f"version = {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-B",
            "-S",
            "-c",
            (
                "import json,sys; print(json.dumps({"
                "'executable':sys.executable,'prefix':sys.prefix,'path':sys.path}))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    observed = json.loads(completed.stdout)
    assert observed["executable"] == str(interpreter)
    assert observed["prefix"] == str(environment)
    assert any("/lib/python3." in entry for entry in observed["path"])
    assert all("site-packages" not in entry for entry in observed["path"])
    assert str(tmp_path / "generation") not in observed["path"]


def test_app_only_selected_bundle_remains_on_image_dependency_path(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir = tmp_path / "config"
    runtime_dir = config_dir / "channelwatch-runtime"
    bundle = runtime_dir / "releases" / "v1.4.3"
    bundle.mkdir(parents=True)
    (bundle / "channelwatch-bundle.json").write_text(
        json.dumps({"version": "1.4.3", "runtime_abi": "channelwatch-runtime-v1"}),
        encoding="utf-8",
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "active.json").write_text(
        json.dumps({"path": str(bundle), "activation_id": "activation-1"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(bundle))
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "3")
    monkeypatch.setattr(bridge.sys, "argv", ["python -m core.main"])

    assert bridge.validated_worker_invocation() is None


def test_parent_rejects_tampered_worker_before_spawning_interpreter(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir, generation, _active = _prepared_generation(tmp_path)
    (generation / "core" / "runtime_worker.py").write_text(
        "# replaced worker\n", encoding="utf-8"
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(generation))
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "3")
    monkeypatch.setattr(bridge.sys, "argv", ["python -m core.main"])

    with pytest.raises(bridge.RuntimeDependencyWorkerError, match="metadata changed"):
        bridge.validated_worker_invocation()


def test_parent_rejects_hardlinked_worker_before_spawning_interpreter(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir, generation, _active = _prepared_generation(tmp_path)
    worker = generation / "core" / "runtime_worker.py"
    os.link(worker, tmp_path / "worker-alias.py")
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(generation))
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "3")
    monkeypatch.setattr(bridge.sys, "argv", ["python -m core.main"])

    with pytest.raises(bridge.RuntimeDependencyWorkerError, match="unsafe"):
        bridge.validated_worker_invocation()


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv interpreter layout")
def test_bridge_accepts_verified_venv_interpreter_symlink(tmp_path: Path, monkeypatch):
    bridge = _load_bridge()
    config_dir, generation, _active = _prepared_generation(tmp_path)
    interpreter = generation / "environment" / "bin" / "python"
    assert interpreter.is_symlink()
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(generation))
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "3")
    monkeypatch.setattr(bridge.sys, "argv", ["python -m core.main"])

    invocation = bridge.validated_worker_invocation()

    assert invocation is not None
    assert invocation.argv[0] == str(interpreter)


def test_environment_flag_alone_cannot_bypass_parent_bridge(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir, generation, _active = _prepared_generation(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    for name in ("PYTHONHOME", "PYTHONPATH", "LD_AUDIT", "LD_LIBRARY_PATH", "LD_PRELOAD"):
        monkeypatch.setenv(name, "untrusted")
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(generation))
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER", "1")
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_MODE", "core")
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_GENERATION", str(generation))
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "3")
    monkeypatch.setattr(bridge.sys, "executable", sys.executable)
    monkeypatch.setattr(bridge.sys, "argv", ["python -m core.main"])

    assert bridge.is_validated_worker_process() is False
    assert bridge.validated_worker_invocation() is not None


def test_valid_worker_guard_requires_mode_paths_and_generation_identity(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir, generation, active = _prepared_generation(tmp_path)
    interpreter = generation / "environment" / "bin" / "python"
    worker = generation / "core" / "runtime_worker.py"
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(generation))
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER", "1")
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_MODE", "ui")
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_GENERATION", str(generation))
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv(
        "CHANNELWATCH_RUNTIME_WORKER_DESCRIPTOR_SHA256",
        str(active["runtime_descriptor_sha256"]),
    )
    monkeypatch.setattr(bridge.sys, "executable", str(interpreter))
    monkeypatch.setattr(
        bridge.sys,
        "argv",
        [
            str(worker),
            "--mode",
            "ui",
            "--generation",
            str(generation),
            "--activation-id",
            "activation-1",
            "--descriptor-sha256",
            str(active["runtime_descriptor_sha256"]),
            "--",
            "--host",
            "127.0.0.1",
        ],
    )

    assert bridge.is_validated_worker_process() is True

    monkeypatch.setattr(
        bridge.sys,
        "_channelwatch_runtime_worker_validated",
        (
            "ui",
            str(generation),
            "activation-1",
            str(active["runtime_descriptor_sha256"]),
        ),
        raising=False,
    )
    monkeypatch.setattr(bridge.sys, "argv", ["python -m core.main"])
    assert bridge.is_validated_worker_process() is True

    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_MODE", "core")
    assert bridge.is_validated_worker_process() is False


class _FakeProcess:
    def __init__(self, returncode: int):
        self.pid = 4321
        self.returncode = returncode
        self.wait_calls: list[float | None] = []
        self.on_wait = None
        self.stdin = SimpleNamespace(
            write=lambda payload: len(payload),
            close=lambda: None,
        )

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.on_wait is not None:
            callback, self.on_wait = self.on_wait, None
            callback()
        return self.returncode


def test_supervised_parent_forwards_term_and_reaps_process_group_before_exit(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir, generation, _active = _prepared_generation(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    process = _FakeProcess(0)
    popen_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    forwarded: list[tuple[int, int]] = []
    installed_handlers: dict[int, object] = {}
    order: list[str] = []

    def fake_popen(argv, **kwargs):
        order.append("spawn")
        popen_calls.append((tuple(argv), kwargs))
        return process

    monkeypatch.setattr(bridge.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        bridge,
        "_verify_generation_for_launch",
        lambda *_args: order.append("verify") or b"# verified worker\n",
    )
    monkeypatch.setattr(
        bridge.signal,
        "signal",
        lambda number, handler: installed_handlers.setdefault(number, handler),
    )
    monkeypatch.setattr(
        bridge, "_signal_group", lambda pid, sig: forwarded.append((pid, sig))
    )
    monkeypatch.setattr(bridge, "_process_group_exists", lambda _pid: False)
    invocation = bridge.WorkerInvocation(
        mode="core",
        generation=generation,
        activation_id="activation-1",
        descriptor_sha256="a" * 64,
        argv=("python", "worker.py"),
    )
    process.on_wait = lambda: installed_handlers[signal.SIGTERM](signal.SIGTERM, None)

    with pytest.raises(SystemExit) as exc_info:
        bridge.supervise_worker(invocation)

    assert exc_info.value.code == 0
    assert popen_calls[0][1]["start_new_session"] is True
    child_environment = popen_calls[0][1]["env"]
    assert all(
        name not in child_environment
        for name in ("PYTHONHOME", "PYTHONPATH", "LD_AUDIT", "LD_LIBRARY_PATH", "LD_PRELOAD")
    )
    assert order == ["verify", "spawn"]
    assert forwarded == [(process.pid, signal.SIGTERM)]
    assert process.wait_calls


def test_worker_environment_strips_all_loader_prefixes_and_keeps_app_config(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    for name in ("PYTHONFAKE", "LD_FAKE", "GLIBC_TUNABLES", "GLIBC_FAKE"):
        monkeypatch.setenv(name, "untrusted")
    monkeypatch.setenv("CHANNELS_DVR_HOST", "dvr.example.test")
    monkeypatch.setenv("CW_ADMIN_USER", "admin")
    monkeypatch.setenv("CUSTOM_WEBHOOK_SECRET", "preserved")
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "stale-supervisor-activation")
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "stale-activation")
    invocation = bridge.WorkerInvocation(
        mode="core",
        generation=tmp_path,
        activation_id="activation-1",
        descriptor_sha256="a" * 64,
        argv=("python", "worker.py"),
    )

    environment = bridge._worker_environment(invocation)

    assert all(
        not name.startswith(("PYTHON", "LD_", "GLIBC_"))
        or name in {"PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"}
        for name in environment
    )
    assert environment["CHANNELS_DVR_HOST"] == "dvr.example.test"
    assert environment["CW_ADMIN_USER"] == "admin"
    assert environment["CUSTOM_WEBHOOK_SECRET"] == "preserved"
    assert environment["CHANNELWATCH_ACTIVATION_ID"] == "activation-1"
    assert environment["CHANNELWATCH_RUNTIME_WORKER_ACTIVATION_ID"] == "activation-1"
    assert environment["CHANNELWATCH_ACTIVATION_ID"] == "activation-1"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal mask behavior")
def test_term_arriving_inside_spawn_is_forwarded_after_handler_installation(
    tmp_path: Path, monkeypatch
):
    if _isolated_signal_test(
        "test_term_arriving_inside_spawn_is_forwarded_after_handler_installation"
    ):
        return
    bridge = _load_bridge()
    source = b"import time\ntime.sleep(60)\n"
    real_popen = bridge.subprocess.Popen
    spawned = []

    def term_during_spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(
        bridge, "_verify_generation_for_launch", lambda *_args: source
    )
    monkeypatch.setattr(bridge.subprocess, "Popen", term_during_spawn)
    invocation = bridge.WorkerInvocation(
        mode="core",
        generation=tmp_path,
        activation_id="activation-1",
        descriptor_sha256="a" * 64,
        argv=(
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            bridge._WORKER_SNAPSHOT_BOOTSTRAP,
            str(tmp_path / "worker.py"),
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        bridge.supervise_worker(invocation)

    assert exc_info.value.code == 0
    assert spawned[0].poll() is not None
    assert not bridge._process_group_exists(spawned[0].pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group behavior")
def test_real_worker_group_is_killed_before_parent_exits_when_descendant_holds_port(
    tmp_path: Path,
    monkeypatch,
):
    if _isolated_signal_test(
        "test_real_worker_group_is_killed_before_parent_exits_when_descendant_holds_port"
    ):
        return
    bridge = _load_bridge()
    monkeypatch.setattr(
        bridge,
        "_verify_generation_for_launch",
        lambda *_args: script.read_bytes(),
    )
    marker = tmp_path / "worker-group.json"
    script = tmp_path / "ignore-term-worker.py"
    script.write_text(
        """
import json
import signal
import socket
import subprocess
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
child_code = '''
import json, os, signal, socket, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(1)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"pid": os.getpid(), "port": listener.getsockname()[1]}, handle)
while True:
    time.sleep(1)
'''
subprocess.Popen([sys.executable, "-I", "-B", "-c", child_code, sys.argv[1]])
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    invocation = bridge.WorkerInvocation(
        mode="core",
        generation=tmp_path,
        activation_id="activation-1",
        descriptor_sha256="a" * 64,
        argv=(
            str(
                Path("/usr/bin/python3")
                if Path("/usr/bin/python3").is_file()
                else sys.executable
            ),
            "-I",
            "-B",
            "-S",
            "-c",
            bridge._WORKER_SNAPSHOT_BOOTSTRAP,
            str(script),
            str(marker),
        ),
    )

    def stop_parent_after_descendant_starts() -> None:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        os.kill(os.getpid(), signal.SIGTERM)

    stopper = threading.Thread(target=stop_parent_after_descendant_starts)
    stopper.start()
    with pytest.raises(SystemExit) as exc_info:
        bridge.supervise_worker(invocation)
    stopper.join(timeout=2)

    assert exc_info.value.code == 0
    state = json.loads(marker.read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.settimeout(0.2)
        assert probe.connect_ex(("127.0.0.1", state["port"])) != 0
    with pytest.raises(ProcessLookupError):
        os.kill(state["pid"], 0)


@pytest.mark.parametrize("returncode", [0, 7, -9])
def test_unexpected_worker_exit_raises_for_launcher_rollback(
    tmp_path: Path, monkeypatch, returncode: int
):
    bridge = _load_bridge()
    _config_dir, generation, _active = _prepared_generation(tmp_path)
    process = _FakeProcess(returncode)
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        bridge,
        "_verify_generation_for_launch",
        lambda *_args: b"# verified worker\n",
    )
    monkeypatch.setattr(bridge.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(bridge, "_process_group_exists", lambda _pid: False)
    invocation = bridge.WorkerInvocation(
        mode="core",
        generation=generation,
        activation_id="activation-1",
        descriptor_sha256="a" * 64,
        argv=("python", "worker.py"),
    )

    with pytest.raises(bridge.RuntimeDependencyWorkerError, match="exited unexpectedly"):
        bridge.supervise_worker(invocation)


def test_ui_mode_is_selected_before_uvicorn_can_import_or_bind(
    tmp_path: Path, monkeypatch
):
    bridge = _load_bridge()
    config_dir, generation, _active = _prepared_generation(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_dir))
    monkeypatch.setenv("CHANNELWATCH_APP_DIR", str(generation))
    monkeypatch.setenv("CHANNELWATCH_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_LAUNCHER_PROTOCOL", "3")
    monkeypatch.setattr(
        bridge.sys,
        "argv",
        ["/app/core/runtime_launcher.py", "ui", "--port", "8501"],
    )
    original_uvicorn = bridge.sys.modules.get("uvicorn")

    invocation = bridge.validated_worker_invocation()

    assert invocation is not None
    assert invocation.mode == "ui"
    assert invocation.argv[-3:] == ("--", "--port", "8501")
    assert bridge.sys.modules.get("uvicorn") is original_uvicorn
