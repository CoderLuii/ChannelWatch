from __future__ import annotations

import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core import runtime_preparation


ROOT = Path(__file__).resolve().parents[3]


def _fixture_image(tmp_path: Path, body: str) -> Path:
    image = tmp_path / "image"
    core = image / "core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "runtime_preparation.py").write_bytes(
        (ROOT / "app/core/runtime_preparation.py").read_bytes()
    )
    (core / "runtime_generation.py").write_text(
        "from dataclasses import dataclass\n"
        "from pathlib import Path\n"
        "@dataclass(frozen=True)\n"
        "class RuntimeGeneration:\n"
        "    generation_id: str\n"
        "    app_dir: Path\n"
        "    python_executable: Path\n"
        "    dependency_digest: str\n"
        "def detect_runtime_platform(): return object()\n"
        "def validate_runtime_descriptor(descriptor, platform):\n"
        "    assert descriptor['generation_id'] == 'a' * 64\n"
        "def verify_runtime_generation(generation):\n"
        "    assert generation.app_dir.name == generation.generation_id\n"
        "    assert generation.python_executable == "
        "generation.app_dir / 'environment/bin/python'\n",
        encoding="utf-8",
    )
    (core / "runtime_archive.py").write_text(body, encoding="utf-8")
    return image


def _descriptor() -> dict[str, object]:
    return {
        "generation_id": "a" * 64,
        "dependency_inventory_sha256": "b" * 64,
    }


@pytest.mark.parametrize("as_file", [False, True])
def test_bounded_preparation_accepts_bytes_and_file_descriptors(
    tmp_path: Path, monkeypatch, immutable_image_python: Path, as_file: bool
):
    marker = tmp_path / "environment.json"
    image = _fixture_image(
        tmp_path,
        """
import json
import os
from pathlib import Path
from types import SimpleNamespace

def install_runtime_archive(source, releases_dir, descriptor):
    payload = source.read()
    assert payload == b"signed archive"
    generation = Path(releases_dir) / descriptor["generation_id"]
    generation.mkdir(parents=True)
    (generation / "environment/bin").mkdir(parents=True)
    (generation / "environment/bin/python").write_bytes(b"")
    Path(os.environ["PREPARATION_MARKER"]).write_text(json.dumps({
        "python": sorted(name for name in os.environ if name.startswith("PYTHON")),
        "ld": sorted(name for name in os.environ if name.startswith("LD_")),
        "glibc": sorted(name for name in os.environ if name.startswith("GLIBC_")),
    }), encoding="utf-8")
    return SimpleNamespace(
        generation_id=descriptor["generation_id"],
        app_dir=generation,
        python_executable=generation / "environment/bin/python",
        dependency_digest=descriptor["dependency_inventory_sha256"],
    )
""",
    )
    releases = tmp_path / "releases"
    releases.mkdir()
    monkeypatch.setenv("PYTHONUNTRUSTED", "yes")
    monkeypatch.setenv("LD_UNTRUSTED", "yes")
    monkeypatch.setenv("GLIBC_TUNABLES", "glibc.malloc.check=3")
    monkeypatch.setenv("PREPARATION_MARKER", str(marker))
    source: bytes | io.BufferedRandom
    if as_file:
        source = (tmp_path / "archive.zip").open("w+b")
        source.write(b"signed archive")
        source.seek(0)
    else:
        source = b"signed archive"
    try:
        result = runtime_preparation.prepare_runtime_archive_bounded(
            source,
            releases,
            _descriptor(),
            timeout_seconds=5,
            image_app_dir=image,
            image_python=immutable_image_python,
        )
    finally:
        if not isinstance(source, bytes):
            source.close()

    assert result.generation_id == "a" * 64
    assert result.app_dir == releases / ("a" * 64)
    observed = json.loads(marker.read_text(encoding="utf-8"))
    assert "PYTHONUNTRUSTED" not in observed["python"]
    assert "LD_UNTRUSTED" not in observed["ld"]
    assert "GLIBC_TUNABLES" not in observed["glibc"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups and fd passing")
def test_timeout_reaps_descendant_and_preserves_active_control(
    tmp_path: Path, monkeypatch, immutable_image_python: Path
):
    marker = tmp_path / "descendant.json"
    image = _fixture_image(
        tmp_path,
        """
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

def install_runtime_archive(source, releases_dir, descriptor):
    child = '''
import json, os, signal, socket, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 0))
sock.listen()
open(sys.argv[1], "w").write(json.dumps({"pid": os.getpid(), "port": sock.getsockname()[1]}))
while True: time.sleep(1)
'''
    subprocess.Popen([sys.executable, "-I", "-B", "-c", child, os.environ["PREPARATION_MARKER"]])
    deadline = time.monotonic() + 5
    while not Path(os.environ["PREPARATION_MARKER"]).exists():
        if time.monotonic() >= deadline: raise RuntimeError("descendant did not start")
        time.sleep(0.01)
    while True: time.sleep(1)
""",
    )
    runtime = tmp_path / "runtime"
    releases = runtime / "releases"
    releases.mkdir(parents=True)
    active = runtime / "active.json"
    active_before = b'{"version":"1.4.3","path":"/previous"}\n'
    active.write_bytes(active_before)
    monkeypatch.setenv("PREPARATION_MARKER", str(marker))
    monkeypatch.setattr(runtime_preparation, "GROUP_TERM_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(runtime_preparation, "GROUP_KILL_TIMEOUT_SECONDS", 1.0)

    def prepare() -> runtime_preparation.PreparedRuntimeGeneration:
        return runtime_preparation.prepare_runtime_archive_bounded(
            b"archive",
            releases,
            _descriptor(),
            timeout_seconds=5,
            image_app_dir=image,
            image_python=immutable_image_python,
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(prepare)
        deadline = time.monotonic() + 4
        while not marker.exists():
            if time.monotonic() >= deadline:
                pytest.fail("preparation descendant did not start before the test deadline")
            time.sleep(0.01)
        with pytest.raises(runtime_preparation.RuntimePreparationTimeout):
            result.result(timeout=8)

    assert active.read_bytes() == active_before
    descendant = json.loads(marker.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(descendant["pid"], 0)
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", descendant["port"]))
    finally:
        probe.close()


def test_child_failure_does_not_publish_active_control(
    tmp_path: Path, immutable_image_python: Path
):
    image = _fixture_image(
        tmp_path,
        "def install_runtime_archive(*_args):\n    raise RuntimeError('rejected archive')\n",
    )
    runtime = tmp_path / "runtime"
    releases = runtime / "releases"
    releases.mkdir(parents=True)
    active = runtime / "active.json"
    active_before = b'{"path":"/previous"}\n'
    active.write_bytes(active_before)

    with pytest.raises(runtime_preparation.RuntimePreparationError, match="rejected archive"):
        runtime_preparation.prepare_runtime_archive_bounded(
            b"archive",
            releases,
            _descriptor(),
            timeout_seconds=5,
            image_app_dir=image,
            image_python=immutable_image_python,
        )

    assert active.read_bytes() == active_before


@pytest.mark.skipif(os.name == "nt", reason="Linux parent-death signal and process groups")
def test_parent_death_reaps_background_preparation_and_descendant(
    tmp_path: Path, monkeypatch, immutable_image_python: Path
):
    marker = tmp_path / "parent-death.json"
    image = _fixture_image(
        tmp_path,
        """
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

def install_runtime_archive(source, releases_dir, descriptor):
    child = '''
import json, os, signal, socket, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 0))
sock.listen()
marker = Path(sys.argv[1])
temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps({
    "pid": os.getpid(),
    "port": sock.getsockname()[1],
    "helper_pid": os.getppid(),
}), encoding="utf-8")
os.replace(temporary, marker)
while True: time.sleep(1)
'''
    subprocess.Popen([sys.executable, "-I", "-B", "-c", child, os.environ["PREPARATION_MARKER"]])
    deadline = time.monotonic() + 5
    while not Path(os.environ["PREPARATION_MARKER"]).exists():
        if time.monotonic() >= deadline: raise RuntimeError("descendant did not start")
        time.sleep(0.01)
    while True: time.sleep(1)
""",
    )
    releases = tmp_path / "releases"
    releases.mkdir()
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        """
import os
import sys
from pathlib import Path
from core.runtime_preparation import prepare_runtime_archive_bounded

prepare_runtime_archive_bounded(
    b"archive",
    Path(sys.argv[1]),
    {"generation_id": "a" * 64, "dependency_inventory_sha256": "b" * 64},
    timeout_seconds=30,
    image_app_dir=Path(sys.argv[2]),
    image_python=Path(sys.argv[3]),
)
""",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "app")
    environment["PREPARATION_MARKER"] = str(marker)
    parent = subprocess.Popen(
        [
            sys.executable,
            str(parent_script),
            str(releases),
            str(image),
            str(immutable_image_python),
        ],
        env=environment,
    )
    try:
        deadline = time.monotonic() + 8
        while not marker.exists():
            if parent.poll() is not None:
                pytest.fail(f"preparation parent exited early with {parent.returncode}")
            if time.monotonic() >= deadline:
                pytest.fail("background preparation did not start")
            time.sleep(0.02)
        observed = json.loads(marker.read_text(encoding="utf-8"))

        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            alive = []
            for pid in (observed["helper_pid"], observed["pid"]):
                try:
                    os.kill(pid, 0)
                    alive.append(pid)
                except ProcessLookupError:
                    pass
            if not alive:
                break
            time.sleep(0.05)
        assert not alive
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", observed["port"]))
        finally:
            probe.close()
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode validation")
def test_bounded_preparation_rejects_writable_image_python(
    tmp_path: Path, immutable_image_python: Path
):
    image = _fixture_image(
        tmp_path,
        "def install_runtime_archive(*_args):\n    raise AssertionError('unreachable')\n",
    )
    releases = tmp_path / "releases"
    releases.mkdir()
    immutable_image_python.chmod(0o775)

    with pytest.raises(
        runtime_preparation.RuntimePreparationError,
        match="The image Python interpreter is not trusted",
    ):
        runtime_preparation.prepare_runtime_archive_bounded(
            b"archive",
            releases,
            _descriptor(),
            timeout_seconds=5,
            image_app_dir=image,
            image_python=immutable_image_python,
        )


def test_descriptor_transport_is_raw_and_bounded():
    descriptor = _descriptor()
    payload = runtime_preparation._descriptor_bytes(descriptor)
    assert json.loads(payload) == descriptor
    assert not payload.startswith(b'{"descriptor":')
    with pytest.raises(
        runtime_preparation.RuntimePreparationError, match="exceeds the safe limit"
    ):
        runtime_preparation._descriptor_bytes(
            {"padding": "x" * runtime_preparation.MAX_DESCRIPTOR_BYTES}
        )
