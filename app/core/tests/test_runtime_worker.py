from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import runpy
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
WORKER_PATH = ROOT / "app" / "core" / "runtime_worker.py"


def _load_worker():
    spec = importlib.util.spec_from_file_location("runtime_worker_under_test", WORKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_worker_has_no_global_dependency_imports_before_generation_validation():
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
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


def test_worker_rejects_mode_or_identity_not_bound_to_invocation(
    tmp_path: Path, monkeypatch
):
    worker = _load_worker()
    generation = tmp_path / "generation"
    generation.mkdir()
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER", "1")
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_MODE", "ui")
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_GENERATION", str(generation))
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_ACTIVATION_ID", "activation-1")
    monkeypatch.setenv("CHANNELWATCH_RUNTIME_WORKER_DESCRIPTOR_SHA256", "a" * 64)

    with pytest.raises(RuntimeError, match="invocation identity"):
        worker.validate_invocation_environment(
            mode="core",
            generation=generation,
            activation_id="activation-1",
            descriptor_sha256="a" * 64,
        )


def test_bound_module_executes_the_verified_source_snapshot(tmp_path, monkeypatch):
    worker = _load_worker()
    generation = tmp_path / "generation"
    module_path = generation / "core" / "runtime_generation.py"
    module_path.parent.mkdir(parents=True)
    source = b'VALUE = "verified snapshot"\n'
    module_path.write_bytes(source)
    module_path.chmod(0o644)
    identity = {
        "schema": 1,
        "files": [
            {
                "path": "core/runtime_generation.py",
                "sha256": hashlib.sha256(source).hexdigest(),
                "mode": "0644",
                "size": len(source),
            }
        ],
    }
    canonical = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    descriptor_sha256 = hashlib.sha256(canonical).hexdigest()
    identity["generation_id"] = descriptor_sha256
    (generation / "channelwatch-bundle.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )
    real_read = worker._read_snapshot

    def replace_after_snapshot(path, **kwargs):
        snapshot = real_read(path, **kwargs)
        path.write_text('raise RuntimeError("reloaded mutable path")\n', encoding="utf-8")
        return snapshot

    monkeypatch.setattr(worker, "_read_snapshot", replace_after_snapshot)

    module = worker._load_bound_module(
        generation,
        descriptor_sha256,
        "core/runtime_generation.py",
        "_snapshot_test_module",
    )

    assert module.VALUE == "verified snapshot"
    assert stat.S_IMODE(module_path.stat().st_mode) == 0o644


def test_worker_verifies_generation_before_installing_import_paths(
    tmp_path: Path, monkeypatch
):
    worker = _load_worker()
    generation = tmp_path / "generation"
    site_packages = generation / "dependencies" / "site-packages"
    site_packages.mkdir(parents=True)
    (generation / "prepared.json").write_text(
        '{"generation_id":"generation-1","python_executable":'
        '"environment/bin/python","dependency_digest":"' + "b" * 64 + '"}',
        encoding="utf-8",
    )
    calls: list[Path] = []
    claims: list[dict[str, str]] = []
    runtime_reference = SimpleNamespace
    fake_generation_module = SimpleNamespace(
        RuntimeGeneration=runtime_reference,
        verify_runtime_generation=lambda value: calls.append(value.app_dir),
    )
    original_path = [str(ROOT / "app"), "/venv/lib/python3.14/site-packages", "/usr/lib/python3.14"]
    monkeypatch.setattr(worker.sys, "path", original_path.copy())
    monkeypatch.setattr(worker.sys, "prefix", str(generation / "environment"))
    monkeypatch.setenv("CHANNELWATCH_IMAGE_APP_DIR", str(ROOT / "app"))
    monkeypatch.setattr(
        worker,
        "_load_runtime_generation_module",
        lambda _generation, _descriptor: fake_generation_module,
    )
    monkeypatch.setattr(
        worker,
        "_set_verified_runtime_claim",
        lambda _generation, _descriptor, claim: claims.append(claim),
    )

    result = worker.verify_and_install_generation(generation, "a" * 64)

    assert result is None
    assert calls == [generation.resolve()]
    assert worker.sys.path[0] == str(generation.resolve())
    assert worker.sys.path[1] == str(site_packages.resolve())
    assert "/venv/lib/python3.14/site-packages" not in worker.sys.path
    assert str(ROOT / "app") not in worker.sys.path
    assert "/usr/lib/python3.14" in worker.sys.path
    assert claims == [{
        "path": str(generation.resolve()),
        "runtime_generation_id": "generation-1",
        "runtime_descriptor_sha256": "a" * 64,
        "dependency_inventory_sha256": "b" * 64,
    }]


def test_image_worker_uses_only_image_verifier_and_identity_modules(
    tmp_path: Path, monkeypatch
):
    worker = _load_worker()
    generation = tmp_path / "generation"
    site_packages = generation / "dependencies/site-packages"
    site_packages.mkdir(parents=True)
    (generation / "prepared.json").write_text(
        json.dumps(
            {
                "generation_id": "a" * 64,
                "python_executable": "environment/bin/python",
                "dependency_digest": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    order = []
    verifier = SimpleNamespace(
        RuntimeGeneration=SimpleNamespace,
        verify_runtime_generation=lambda _runtime: order.append("verify"),
    )
    identity = SimpleNamespace(
        set_verified_runtime_claim=lambda _claim: order.append("claim")
    )

    def load_image(_image, relative, _name):
        order.append(relative)
        if relative == "core/runtime_generation.py":
            return verifier
        return identity

    monkeypatch.setattr(worker, "_load_image_module", load_image)
    monkeypatch.setattr(
        worker,
        "_load_runtime_generation_module",
        lambda *_args: pytest.fail("generation verifier became authority"),
    )
    monkeypatch.setattr(
        worker,
        "_install_import_paths",
        lambda *_args: order.append("paths"),
    )

    worker.verify_and_install_generation(
        generation, "a" * 64, ROOT / "app"
    )

    assert order == [
        "core/runtime_generation.py",
        "verify",
        "core/runtime_identity.py",
        "claim",
        "paths",
    ]


def test_failed_image_verification_cannot_run_selected_application(
    tmp_path: Path, monkeypatch
):
    worker = _load_worker()
    generation = tmp_path / "generation"
    generation.mkdir()
    ran = []
    monkeypatch.setenv("CHANNELWATCH_IMAGE_APP_DIR", str(ROOT / "app"))
    monkeypatch.setattr(worker, "validate_invocation_environment", lambda **_kwargs: None)
    monkeypatch.setattr(
        worker,
        "verify_and_install_generation",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("verification failed")),
    )
    monkeypatch.setattr(
        worker,
        "run_selected_application",
        lambda *_args, **_kwargs: ran.append(True),
    )

    with pytest.raises(RuntimeError, match="verification failed"):
        worker.main(
            [
                "--image-app-dir",
                str(ROOT / "app"),
                "--mode",
                "core",
                "--generation",
                str(generation),
                "--activation-id",
                "activation-1",
                "--descriptor-sha256",
                "a" * 64,
            ]
        )

    assert ran == []


def test_isolated_failed_verification_cannot_import_selected_initializer(
    tmp_path: Path,
):
    generation = tmp_path / "generation"
    selected_core = generation / "core"
    selected_core.mkdir(parents=True)
    marker = tmp_path / "selected-initializer-ran"
    (selected_core / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    descriptor_sha256 = "a" * 64
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PYTHON", "LD_", "GLIBC_"))
    }
    environment.update(
        {
            "CHANNELWATCH_RUNTIME_WORKER": "1",
            "CHANNELWATCH_RUNTIME_WORKER_MODE": "core",
            "CHANNELWATCH_RUNTIME_WORKER_GENERATION": str(generation.resolve()),
            "CHANNELWATCH_RUNTIME_WORKER_ACTIVATION_ID": "activation-1",
            "CHANNELWATCH_RUNTIME_WORKER_DESCRIPTOR_SHA256": descriptor_sha256,
            "CHANNELWATCH_IMAGE_APP_DIR": str((ROOT / "app").resolve()),
        }
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            str(WORKER_PATH),
            "--image-app-dir",
            str(ROOT / "app"),
            "--mode",
            "core",
            "--generation",
            str(generation),
            "--activation-id",
            "activation-1",
            "--descriptor-sha256",
            descriptor_sha256,
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not marker.exists()


def test_worker_runs_core_with_original_application_arguments(monkeypatch, tmp_path: Path):
    worker = _load_worker()
    observed: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        worker.runpy,
        "run_module",
        lambda name, run_name: observed.append((name, list(worker.sys.argv))),
    )

    worker.run_selected_application("core", ["--stay-alive"], host=None, port=None, log_level=None)

    assert observed == [("core.main", ["python -m core.main", "--stay-alive"])]


def test_worker_imports_generation_uvicorn_only_after_validation(monkeypatch):
    worker = _load_worker()
    calls: list[dict[str, object]] = []

    class FakeUvicorn:
        @staticmethod
        def run(app, **kwargs):
            calls.append({"app": app, **kwargs})

    real_import = worker.importlib.import_module

    def import_module(name: str):
        if name == "uvicorn":
            return FakeUvicorn
        return real_import(name)

    monkeypatch.setattr(worker.importlib, "import_module", import_module)

    worker.run_selected_application(
        "ui", [], host="127.0.0.1", port=8501, log_level="warning"
    )

    assert calls == [
        {
            "app": "ui.backend.main:app",
            "host": "127.0.0.1",
            "port": 8501,
            "log_level": "warning",
            "proxy_headers": False,
        }
    ]


def test_worker_main_validates_before_running_selected_application(
    tmp_path: Path, monkeypatch
):
    worker = _load_worker()
    generation = tmp_path / "generation"
    generation.mkdir()
    order: list[str] = []
    monkeypatch.setattr(
        worker,
        "validate_invocation_environment",
        lambda **_kwargs: order.append("guard"),
    )
    monkeypatch.setattr(
        worker,
        "verify_and_install_generation",
        lambda *_args: order.append("generation") or SimpleNamespace(),
    )
    monkeypatch.setattr(
        worker,
        "run_selected_application",
        lambda *_args, **_kwargs: order.append("application"),
    )

    assert worker.main(
        [
            "--image-app-dir",
            str(ROOT / "app"),
            "--mode",
            "core",
            "--generation",
            str(generation),
            "--activation-id",
            "activation-1",
            "--descriptor-sha256",
            "a" * 64,
            "--",
            "--stay-alive",
        ]
    ) == 0
    assert order == ["guard", "generation", "application"]
