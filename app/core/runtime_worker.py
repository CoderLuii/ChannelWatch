"""Run one verified runtime generation in an isolated interpreter."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import runpy
import signal
import stat
import sys
import types
from pathlib import Path
from typing import Any


def _unblock_supervisor_signals() -> None:
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(
            signal.SIG_UNBLOCK, (signal.SIGTERM, signal.SIGINT)
        )


_unblock_supervisor_signals()


def validate_invocation_environment(
    *,
    mode: str,
    generation: Path,
    activation_id: str,
    descriptor_sha256: str,
    image_app_dir: Path | None = None,
) -> None:
    expected = {
        "CHANNELWATCH_RUNTIME_WORKER": "1",
        "CHANNELWATCH_RUNTIME_WORKER_MODE": mode,
        "CHANNELWATCH_RUNTIME_WORKER_GENERATION": str(generation.resolve()),
        "CHANNELWATCH_RUNTIME_WORKER_ACTIVATION_ID": activation_id,
        "CHANNELWATCH_RUNTIME_WORKER_DESCRIPTOR_SHA256": descriptor_sha256,
    }
    if image_app_dir is not None:
        expected["CHANNELWATCH_IMAGE_APP_DIR"] = str(image_app_dir.resolve())
    if any(os.environ.get(name) != value for name, value in expected.items()):
        raise RuntimeError("Runtime worker invocation identity is invalid.")


def _site_packages_path(generation: Path) -> Path:
    dependencies = (generation / "dependencies").resolve()
    site_packages = (dependencies / "site-packages").resolve()
    try:
        site_packages.relative_to(dependencies)
    except ValueError as exc:
        raise RuntimeError("Runtime dependency path escapes its generation.") from exc
    if not site_packages.is_dir():
        raise RuntimeError("Runtime dependency site-packages path is invalid.")
    return site_packages


def _install_import_paths(generation: Path, site_packages: Path) -> None:
    image_app = Path(os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app"))
    generation_text = str(generation)
    site_packages_text = str(site_packages)
    retained: list[str] = []
    for item in sys.path:
        if not item:
            continue
        normalized = os.path.normcase(os.path.abspath(item))
        if normalized in {
            os.path.normcase(os.path.abspath(generation_text)),
            os.path.normcase(os.path.abspath(site_packages_text)),
        }:
            continue
        try:
            Path(item).resolve().relative_to(image_app.resolve())
        except (OSError, ValueError):
            pass
        else:
            continue
        if "site-packages" in normalized or "dist-packages" in normalized:
            continue
        retained.append(item)
    sys.path = [generation_text, site_packages_text, *retained]
    os.environ["PYTHONPATH"] = generation_text
    importlib.invalidate_caches()


def _read_snapshot(path: Path, *, size: int, mode: int, sha256: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size != size
            or stat.S_IMODE(opened.st_mode) != mode
        ):
            raise RuntimeError("Runtime generation verifier metadata changed.")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        source = b"".join(chunks)
        final = os.fstat(descriptor)
        named = path.lstat()
        identity = (opened.st_dev, opened.st_ino)
        if (
            identity != (final.st_dev, final.st_ino)
            or identity != (named.st_dev, named.st_ino)
            or opened.st_mtime_ns != final.st_mtime_ns
            or opened.st_ctime_ns != final.st_ctime_ns
            or named.st_nlink != 1
        ):
            raise RuntimeError("Runtime generation verifier changed during validation.")
    finally:
        os.close(descriptor)
    if hashlib.sha256(source).hexdigest() != sha256:
        raise RuntimeError("Runtime generation verifier digest changed.")
    return source


def _read_image_snapshot(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RuntimeError("Image runtime module is unsafe.")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        source = b"".join(chunks)
        final = os.fstat(descriptor)
        named = path.lstat()
        identity = (opened.st_dev, opened.st_ino)
        if (
            identity != (final.st_dev, final.st_ino)
            or identity != (named.st_dev, named.st_ino)
            or opened.st_size != final.st_size
            or opened.st_mtime_ns != final.st_mtime_ns
            or opened.st_ctime_ns != final.st_ctime_ns
            or named.st_nlink != 1
        ):
            raise RuntimeError("Image runtime module changed during validation.")
        return source
    finally:
        os.close(descriptor)


def _load_image_module(
    image_app_dir: Path, relative_path: str, module_name: str
) -> Any:
    module_path = image_app_dir.joinpath(*Path(relative_path).parts)
    source = _read_image_snapshot(module_path)
    module = types.ModuleType(module_name)
    module.__file__ = str(module_path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(
            compile(source, str(module_path), "exec"),
            module.__dict__,
            module.__dict__,
        )
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _read_bound_source(
    generation: Path, descriptor_sha256: str, relative_path: str
) -> bytes:
    descriptor_path = generation / "channelwatch-bundle.json"
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        identity = dict(descriptor)
        identity.pop("generation_id", None)
        canonical = json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("generation_id") != descriptor_sha256
            or hashlib.sha256(canonical).hexdigest() != descriptor_sha256
        ):
            raise RuntimeError("Runtime descriptor identity changed.")
        entries = [
            entry
            for entry in descriptor.get("files", [])
            if isinstance(entry, dict)
            and entry.get("path") == relative_path
        ]
        if len(entries) != 1:
            raise RuntimeError("Runtime bootstrap module is not bound.")
        entry = entries[0]
        source = _read_snapshot(
            generation.joinpath(*Path(relative_path).parts),
            size=int(entry["size"]),
            mode=int(str(entry["mode"]), 8),
            sha256=str(entry["sha256"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Runtime bootstrap module is unavailable.") from exc
    return source


def _load_bound_module(
    generation: Path,
    descriptor_sha256: str,
    relative_path: str,
    module_name: str,
) -> Any:
    source = _read_bound_source(generation, descriptor_sha256, relative_path)
    module_path = generation.joinpath(*Path(relative_path).parts)
    module = types.ModuleType(module_name)
    module.__file__ = str(module_path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(
            compile(source, str(module_path), "exec"),
            module.__dict__,
            module.__dict__,
        )
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _load_runtime_generation_module(
    generation: Path, descriptor_sha256: str
) -> Any:
    module_path = generation / "core" / "runtime_generation.py"
    module_name = "_channelwatch_runtime_generation_verifier"
    return _load_bound_module(
        generation,
        descriptor_sha256,
        str(module_path.relative_to(generation)),
        module_name,
    )


def _set_verified_runtime_claim(
    generation: Path, descriptor_sha256: str, claim: dict[str, str]
) -> None:
    module = _load_bound_module(
        generation,
        descriptor_sha256,
        "core/runtime_identity.py",
        "_channelwatch_verified_runtime_identity",
    )
    module.set_verified_runtime_claim(claim)


def verify_and_install_generation(
    generation: Path,
    descriptor_sha256: str,
    image_app_dir: Path | None = None,
) -> None:
    generation = generation.resolve()
    if image_app_dir is None:
        runtime_generation = _load_runtime_generation_module(
            generation, descriptor_sha256
        )
    else:
        runtime_generation = _load_image_module(
            image_app_dir,
            "core/runtime_generation.py",
            "_channelwatch_image_runtime_generation_verifier",
        )
    try:
        receipt = json.loads((generation / "prepared.json").read_text(encoding="utf-8"))
        runtime = runtime_generation.RuntimeGeneration(
            generation_id=receipt["generation_id"],
            app_dir=generation,
            python_executable=generation / receipt["python_executable"],
            dependency_digest=receipt["dependency_digest"],
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Prepared runtime receipt is invalid.") from exc
    runtime_generation.verify_runtime_generation(runtime)
    claim = {
        "path": str(generation),
        "runtime_generation_id": str(receipt["generation_id"]),
        "runtime_descriptor_sha256": descriptor_sha256,
        "dependency_inventory_sha256": str(receipt["dependency_digest"]),
    }
    if image_app_dir is None:
        _set_verified_runtime_claim(generation, descriptor_sha256, claim)
    else:
        runtime_identity = _load_image_module(
            image_app_dir,
            "core/runtime_identity.py",
            "_channelwatch_image_runtime_identity",
        )
        runtime_identity.set_verified_runtime_claim(claim)
    site_packages = _site_packages_path(generation)
    _install_import_paths(generation, site_packages)
    return None


def mark_validated_worker(
    mode: str, generation: Path, activation_id: str, descriptor_sha256: str
) -> None:
    sys._channelwatch_runtime_worker_validated = (  # type: ignore[attr-defined]
        mode,
        str(generation),
        activation_id,
        descriptor_sha256,
    )


def run_selected_application(
    mode: str,
    app_args: list[str],
    *,
    host: str | None,
    port: int | None,
    log_level: str | None,
) -> None:
    if mode == "core":
        sys.argv = ["python -m core.main", *app_args]
        runpy.run_module("core.main", run_name="__main__")
        return
    uvicorn = importlib.import_module("uvicorn")
    uvicorn.run(
        "ui.backend.main:app",
        host=host,
        port=port,
        log_level=log_level,
        proxy_headers=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a verified ChannelWatch generation."
    )
    parser.add_argument("--mode", choices=("core", "ui"), required=True)
    parser.add_argument("--image-app-dir", type=Path, required=True)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--activation-id", required=True)
    parser.add_argument("--descriptor-sha256", required=True)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--log-level")
    parser.add_argument("app_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    app_args = list(args.app_args)
    if app_args[:1] == ["--"]:
        app_args.pop(0)
    generation = args.generation.resolve()
    image_app_dir = args.image_app_dir.resolve(strict=True)
    expected_worker = image_app_dir / "core" / "runtime_worker.py"
    if Path(__file__).resolve() != expected_worker.resolve(strict=True):
        raise RuntimeError("Runtime worker is not image-owned.")
    validate_invocation_environment(
        mode=args.mode,
        generation=generation,
        activation_id=args.activation_id,
        descriptor_sha256=args.descriptor_sha256,
        image_app_dir=image_app_dir,
    )
    verify_and_install_generation(
        generation, args.descriptor_sha256, image_app_dir
    )
    mark_validated_worker(
        args.mode,
        generation,
        args.activation_id,
        args.descriptor_sha256,
    )
    host: str | None = None
    port: int | None = None
    log_level: str | None = None
    if args.mode == "ui":
        host = args.host or "0.0.0.0"
        port = args.port or 8501
        log_level = args.log_level or "warning"
        if app_args:
            ui_parser = argparse.ArgumentParser(add_help=False)
            ui_parser.add_argument("--host", default=host)
            ui_parser.add_argument("--port", type=int, default=port)
            ui_parser.add_argument("--log-level", default=log_level)
            ui_args = ui_parser.parse_args(app_args)
            host, port, log_level = ui_args.host, ui_args.port, ui_args.log_level
            app_args = []
    run_selected_application(
        args.mode,
        app_args,
        host=host,
        port=port,
        log_level=log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
