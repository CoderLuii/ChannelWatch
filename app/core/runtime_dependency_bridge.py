"""Start an isolated dependency worker before application imports."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import signal
import subprocess
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


RUNTIME_WORKER_ENV = "CHANNELWATCH_RUNTIME_WORKER"
RUNTIME_WORKER_MODE_ENV = "CHANNELWATCH_RUNTIME_WORKER_MODE"
RUNTIME_WORKER_GENERATION_ENV = "CHANNELWATCH_RUNTIME_WORKER_GENERATION"
RUNTIME_WORKER_ACTIVATION_ENV = "CHANNELWATCH_RUNTIME_WORKER_ACTIVATION_ID"
RUNTIME_WORKER_DESCRIPTOR_ENV = "CHANNELWATCH_RUNTIME_WORKER_DESCRIPTOR_SHA256"
RUNTIME_CAPABILITY = "isolated_dependencies_v1"
BUNDLE_FORMAT = "channelwatch-runtime-generation-v1"
MAX_CONTROL_FILE_BYTES = 4 * 1024 * 1024
GROUP_TERM_TIMEOUT_SECONDS = 3.0
GROUP_KILL_TIMEOUT_SECONDS = 2.0
_WORKER_SNAPSHOT_BOOTSTRAP = """\
import signal
import sys

signals = (signal.SIGTERM, signal.SIGINT)
if hasattr(signal, "pthread_sigmask"):
    signal.pthread_sigmask(signal.SIG_UNBLOCK, signals)
source = sys.stdin.buffer.read()
sys.stdin.close()
filename = sys.argv.pop(1)
sys.argv[0] = filename
namespace = {
    "__name__": "__main__",
    "__file__": filename,
    "__package__": None,
}
exec(compile(source, filename, "exec"), namespace, namespace)
"""


class RuntimeDependencyWorkerError(RuntimeError):
    """An isolated worker failure that the image launcher must roll back."""


@dataclass(frozen=True)
class WorkerInvocation:
    mode: str
    generation: Path
    activation_id: str
    descriptor_sha256: str
    argv: tuple[str, ...]


def _runtime_dir() -> Path:
    return Path(os.environ.get("CONFIG_PATH", "/config")) / "channelwatch-runtime"


def _remove_bootstrap_bytecode() -> None:
    source = Path(__file__).with_name("__init__.py")
    try:
        generation = source.parent.parent.resolve(strict=True)
        releases = (_runtime_dir() / "releases").resolve(strict=True)
        generation.relative_to(releases)
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.relative_to(source.parent / "__pycache__")
        metadata = cache.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeDependencyWorkerError("Runtime bootstrap cache is unsafe.")
        cache.unlink()
        try:
            cache.parent.rmdir()
        except OSError:
            pass
    except (FileNotFoundError, ValueError):
        return
    except OSError as exc:
        raise RuntimeDependencyWorkerError(
            "Runtime bootstrap cache could not be removed."
        ) from exc


def _open_verified_file(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeDependencyWorkerError(
            f"Runtime file is unavailable or unsafe: {path.name}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_nlink != 1
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeDependencyWorkerError(
                f"Runtime file is unavailable or unsafe: {path.name}"
            )
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _verify_open_file_unchanged(
    path: Path, descriptor: int, opened: os.stat_result
) -> None:
    final = os.fstat(descriptor)
    try:
        named = path.lstat()
    except OSError as exc:
        raise RuntimeDependencyWorkerError(
            f"Runtime file changed while it was being verified: {path.name}"
        ) from exc
    identity = (opened.st_dev, opened.st_ino)
    if (
        identity != (final.st_dev, final.st_ino)
        or identity != (named.st_dev, named.st_ino)
        or opened.st_size != final.st_size
        or opened.st_mtime_ns != final.st_mtime_ns
        or opened.st_ctime_ns != final.st_ctime_ns
        or final.st_nlink != 1
        or named.st_nlink != 1
    ):
        raise RuntimeDependencyWorkerError(
            f"Runtime file changed while it was being verified: {path.name}"
        )


def _read_verified_file(path: Path, *, maximum_size: int) -> bytes:
    descriptor, opened = _open_verified_file(path)
    try:
        if opened.st_size > maximum_size:
            raise RuntimeDependencyWorkerError(
                f"Runtime control file is too large: {path.name}"
            )
        chunks: list[bytes] = []
        remaining = maximum_size + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum_size:
            raise RuntimeDependencyWorkerError(
                f"Runtime control file is too large: {path.name}"
            )
        _verify_open_file_unchanged(path, descriptor, opened)
        return payload
    finally:
        os.close(descriptor)


def _verified_file_sha256(path: Path, *, size: int, mode: int) -> str:
    descriptor, opened = _open_verified_file(path)
    try:
        if opened.st_size != size or stat.S_IMODE(opened.st_mode) != mode:
            raise RuntimeDependencyWorkerError("Runtime generation file metadata changed.")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        _verify_open_file_unchanged(path, descriptor, opened)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _read_bound_source(
    generation: Path, descriptor: dict[str, object], relative_path: str
) -> bytes:
    files = descriptor.get("files")
    matches = [
        entry
        for entry in files if isinstance(entry, dict) and entry.get("path") == relative_path
    ] if isinstance(files, list) else []
    if len(matches) != 1:
        raise RuntimeDependencyWorkerError("Runtime bootstrap source is not bound.")
    entry = matches[0]
    try:
        expected_size = int(entry["size"])
        expected_mode = int(str(entry["mode"]), 8)
        expected_sha256 = str(entry["sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeDependencyWorkerError("Runtime bootstrap source is invalid.") from exc
    path = generation.joinpath(*Path(relative_path).parts)
    file_descriptor, opened = _open_verified_file(path)
    try:
        if opened.st_size != expected_size or stat.S_IMODE(opened.st_mode) != expected_mode:
            raise RuntimeDependencyWorkerError("Runtime bootstrap source metadata changed.")
        chunks: list[bytes] = []
        while chunk := os.read(file_descriptor, 1024 * 1024):
            chunks.append(chunk)
        source = b"".join(chunks)
        _verify_open_file_unchanged(path, file_descriptor, opened)
    finally:
        os.close(file_descriptor)
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise RuntimeDependencyWorkerError("Runtime bootstrap source digest changed.")
    return source


def _read_json(path: Path) -> object:
    try:
        payload = _read_verified_file(path, maximum_size=MAX_CONTROL_FILE_BYTES)
    except RuntimeDependencyWorkerError:
        raise
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeDependencyWorkerError(
            f"Runtime control file is invalid: {path.name}"
        ) from exc


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_generation_path(value: str, runtime_dir: Path) -> Path:
    try:
        generation = Path(value).resolve(strict=True)
        generation.relative_to((runtime_dir / "releases").resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise RuntimeDependencyWorkerError("Runtime generation path is not trusted.") from exc
    return generation


def _verified_descriptor(generation: Path) -> tuple[dict[str, object], str] | None:
    descriptor = _read_json(generation / "channelwatch-bundle.json")
    if not isinstance(descriptor, dict) or descriptor.get("bundle_format") != BUNDLE_FORMAT:
        return None
    identity = dict(descriptor)
    identity.pop("generation_id", None)
    descriptor_sha256 = _canonical_sha256(identity)
    if descriptor.get("generation_id") != descriptor_sha256:
        raise RuntimeDependencyWorkerError("Runtime descriptor identity is invalid.")
    files = descriptor.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeDependencyWorkerError("Runtime descriptor file inventory is invalid.")
    file_paths = {
        entry.get("path") for entry in files if isinstance(entry, dict)
    }
    if not {
        "core/runtime_dependency_bridge.py",
        "core/runtime_generation.py",
        "core/runtime_worker.py",
    }.issubset(file_paths):
        raise RuntimeDependencyWorkerError("Runtime bootstrap files are not bound.")
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "mode", "size"}:
            raise RuntimeDependencyWorkerError("Runtime descriptor file entry is invalid.")
        relative = str(entry["path"])
        parts = Path(relative).parts
        if (
            not relative
            or Path(relative).is_absolute()
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise RuntimeDependencyWorkerError("Runtime descriptor file path is invalid.")
        path = generation.joinpath(*parts)
        try:
            expected_mode = int(str(entry["mode"]), 8)
        except ValueError as exc:
            raise RuntimeDependencyWorkerError(
                "Runtime generation file mode is invalid."
            ) from exc
        try:
            expected_size = int(entry["size"])
        except (TypeError, ValueError) as exc:
            raise RuntimeDependencyWorkerError(
                "Runtime generation file size is invalid."
            ) from exc
        if _verified_file_sha256(
            path, size=expected_size, mode=expected_mode
        ) != entry["sha256"]:
            raise RuntimeDependencyWorkerError("Runtime generation file digest changed.")
    return descriptor, descriptor_sha256


def _generation_identity() -> tuple[Path, str, str, Path] | None:
    runtime_dir = _runtime_dir()
    configured = os.environ.get("CHANNELWATCH_APP_DIR", "").strip()
    if not configured:
        raise RuntimeDependencyWorkerError("Selected runtime path is missing.")
    generation = _safe_generation_path(configured, runtime_dir)
    active = _read_json(runtime_dir / "active.json")
    verified = _verified_descriptor(generation)
    if verified is None:
        return None
    descriptor, descriptor_sha256 = verified
    receipt = _read_json(generation / "prepared.json")
    if not isinstance(active, dict) or not isinstance(receipt, dict):
        raise RuntimeDependencyWorkerError("Runtime generation metadata is invalid.")
    try:
        active_path = _safe_generation_path(str(active["path"]), runtime_dir)
    except (KeyError, TypeError) as exc:
        raise RuntimeDependencyWorkerError("Active runtime identity is invalid.") from exc
    activation_id = str(active.get("activation_id") or "")
    generation_id = str(descriptor.get("generation_id") or "")
    dependency_digest = str(descriptor.get("dependency_inventory_sha256") or "")
    receipt_sha256 = str(
        receipt.get("runtime_descriptor_sha256")
        or receipt.get("descriptor_sha256")
        or ""
    ).lower()
    active_sha256 = str(active.get("runtime_descriptor_sha256") or "").lower()
    if (
        active_path != generation
        or not activation_id
        or generation.name != generation_id
        or descriptor.get("required_launcher_capability") != RUNTIME_CAPABILITY
        or str(active.get("runtime_generation_id") or "") != generation_id
        or str(receipt.get("generation_id") or "") != generation_id
        or str(active.get("dependency_inventory_sha256") or "") != dependency_digest
        or str(receipt.get("dependency_digest") or "") != dependency_digest
        or receipt_sha256 != descriptor_sha256
        or active_sha256 != descriptor_sha256
    ):
        raise RuntimeDependencyWorkerError("Runtime generation identity is inconsistent.")
    relative_python = str(
        receipt.get("python_executable") or "environment/bin/python"
    )
    if relative_python != "environment/bin/python":
        raise RuntimeDependencyWorkerError("Runtime interpreter path is invalid.")
    interpreter = generation / relative_python
    if (
        not interpreter.is_file()
        or (
            os.name != "nt"
            and interpreter.resolve() != Path(sys.executable).resolve()
        )
        or (os.name != "nt" and not os.access(interpreter, os.X_OK))
    ):
        raise RuntimeDependencyWorkerError("Runtime interpreter is not executable.")
    worker = generation / "core" / "runtime_worker.py"
    try:
        worker.relative_to(generation)
    except ValueError as exc:
        raise RuntimeDependencyWorkerError("Runtime worker escapes generation.") from exc
    if not worker.is_file():
        raise RuntimeDependencyWorkerError("Runtime worker is missing.")
    return generation, activation_id, descriptor_sha256, interpreter


def _parent_mode_and_args() -> tuple[str, tuple[str, ...]] | None:
    if sys.argv and sys.argv[0] == "python -m core.main":
        return "core", tuple(sys.argv[1:])
    if len(sys.argv) >= 2 and Path(sys.argv[0]).name == "runtime_launcher.py":
        if sys.argv[1] == "ui":
            return "ui", tuple(sys.argv[2:])
    return None


def _worker_arguments() -> dict[str, str] | None:
    if not sys.argv or Path(sys.argv[0]).name != "runtime_worker.py":
        return None
    values: dict[str, str] = {}
    index = 1
    while index < len(sys.argv):
        item = sys.argv[index]
        if item == "--":
            break
        if item not in {
            "--mode",
            "--generation",
            "--activation-id",
            "--descriptor-sha256",
        } or index + 1 >= len(sys.argv):
            return None
        values[item[2:].replace("-", "_")] = sys.argv[index + 1]
        index += 2
    return values


def is_validated_worker_process() -> bool:
    if os.environ.get(RUNTIME_WORKER_ENV) != "1":
        return False
    try:
        identity = _generation_identity()
    except RuntimeDependencyWorkerError:
        return False
    if identity is None:
        return False
    generation, activation_id, descriptor_sha256, interpreter = identity
    expected = {
        "mode": os.environ.get(RUNTIME_WORKER_MODE_ENV, ""),
        "generation": os.environ.get(RUNTIME_WORKER_GENERATION_ENV, ""),
        "activation_id": os.environ.get(RUNTIME_WORKER_ACTIVATION_ENV, ""),
        "descriptor_sha256": os.environ.get(RUNTIME_WORKER_DESCRIPTOR_ENV, ""),
    }
    validated_context = getattr(sys, "_channelwatch_runtime_worker_validated", None)
    if validated_context == (
        expected["mode"],
        expected["generation"],
        expected["activation_id"],
        expected["descriptor_sha256"],
    ):
        arguments = expected
    else:
        arguments = _worker_arguments()
        if arguments is None:
            return False
    return bool(
        expected["mode"] in {"core", "ui"}
        and arguments == expected
        and Path(expected["generation"]).resolve() == generation
        and expected["activation_id"] == activation_id
        and expected["descriptor_sha256"] == descriptor_sha256
        and Path(sys.executable).resolve() == interpreter.resolve()
    )


def validated_worker_invocation() -> WorkerInvocation | None:
    if is_validated_worker_process():
        return None
    parent = _parent_mode_and_args()
    if parent is None or os.environ.get("CHANNELWATCH_LAUNCHER_PROTOCOL") != "3":
        return None
    identity = _generation_identity()
    if identity is None:
        return None
    generation, activation_id, descriptor_sha256, interpreter = identity
    if os.environ.get("CHANNELWATCH_ACTIVATION_ID", "") != activation_id:
        raise RuntimeDependencyWorkerError("Launcher activation identity is inconsistent.")
    mode, app_args = parent
    worker = generation / "core" / "runtime_worker.py"
    argv = (
        str(interpreter),
        "-I",
        "-B",
        "-S",
        "-c",
        _WORKER_SNAPSHOT_BOOTSTRAP,
        str(worker),
        "--mode",
        mode,
        "--generation",
        str(generation),
        "--activation-id",
        activation_id,
        "--descriptor-sha256",
        descriptor_sha256,
        "--",
        *app_args,
    )
    return WorkerInvocation(mode, generation, activation_id, descriptor_sha256, argv)


def _verify_generation_for_launch(
    generation: Path, descriptor_sha256: str
) -> bytes:
    verified = _verified_descriptor(generation)
    if verified is None or verified[1] != descriptor_sha256:
        raise RuntimeDependencyWorkerError("Runtime descriptor identity changed.")
    descriptor, _identity = verified
    verifier_source = _read_bound_source(
        generation, descriptor, "core/runtime_generation.py"
    )
    worker_source = _read_bound_source(generation, descriptor, "core/runtime_worker.py")
    module_path = generation / "core" / "runtime_generation.py"
    module_name = "_channelwatch_parent_runtime_generation_verifier"
    module = types.ModuleType(module_name)
    module.__file__ = str(module_path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(
            compile(verifier_source, str(module_path), "exec"),
            module.__dict__,
            module.__dict__,
        )
        receipt = _read_json(generation / "prepared.json")
        if not isinstance(receipt, dict):
            raise RuntimeDependencyWorkerError("Prepared runtime receipt is invalid.")
        runtime = module.RuntimeGeneration(
            generation_id=receipt["generation_id"],
            app_dir=generation,
            python_executable=generation / receipt["python_executable"],
            dependency_digest=receipt["dependency_digest"],
        )
        module.verify_runtime_generation(runtime)
        return worker_source
    except (KeyError, TypeError) as exc:
        raise RuntimeDependencyWorkerError("Prepared runtime receipt is invalid.") from exc
    finally:
        sys.modules.pop(module_name, None)


def _trusted_image_app_dir(image_app_dir: Path) -> Path:
    expected = Path(__file__).resolve().parent.parent
    try:
        trusted = image_app_dir.resolve(strict=True)
    except OSError as exc:
        raise RuntimeDependencyWorkerError("Image application path is unavailable.") from exc
    if trusted != expected:
        raise RuntimeDependencyWorkerError("Image application path is not trusted.")
    return trusted


def _verify_with_image_authority(
    image_app_dir: Path, generation: Path, descriptor_sha256: str
) -> None:
    trusted_image = _trusted_image_app_dir(image_app_dir)
    module_path = trusted_image / "core" / "runtime_generation.py"
    module_name = "_channelwatch_image_runtime_generation_verifier"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeDependencyWorkerError("Image runtime verifier is unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        if Path(str(module.__file__)).resolve() != module_path.resolve():
            raise RuntimeDependencyWorkerError("Image runtime verifier origin is invalid.")
        receipt = _read_json(generation / "prepared.json")
        if not isinstance(receipt, dict):
            raise RuntimeDependencyWorkerError("Prepared runtime receipt is invalid.")
        if str(receipt.get("descriptor_sha256") or "") != descriptor_sha256:
            raise RuntimeDependencyWorkerError("Prepared runtime identity changed.")
        runtime = module.RuntimeGeneration(
            generation_id=receipt["generation_id"],
            app_dir=generation,
            python_executable=generation / receipt["python_executable"],
            dependency_digest=receipt["dependency_digest"],
        )
        module.verify_runtime_generation(runtime)
    except (KeyError, TypeError) as exc:
        raise RuntimeDependencyWorkerError("Prepared runtime receipt is invalid.") from exc
    finally:
        sys.modules.pop(module_name, None)


def _validate_explicit_generation(
    *,
    runtime_dir: Path,
    generation: Path,
    activation_id: str,
    descriptor_sha256: str,
) -> Path:
    try:
        runtime_dir = runtime_dir.resolve(strict=True)
        generation = generation.resolve(strict=True)
        generation.relative_to((runtime_dir / "releases").resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise RuntimeDependencyWorkerError("Runtime generation path is not trusted.") from exc
    verified = _verified_descriptor(generation)
    if verified is None or verified[1] != descriptor_sha256:
        raise RuntimeDependencyWorkerError("Runtime descriptor identity changed.")
    descriptor, _identity = verified
    active = _read_json(runtime_dir / "active.json")
    receipt = _read_json(generation / "prepared.json")
    if not isinstance(active, dict) or not isinstance(receipt, dict):
        raise RuntimeDependencyWorkerError("Runtime generation metadata is invalid.")
    dependency_digest = str(descriptor.get("dependency_inventory_sha256") or "")
    if (
        Path(str(active.get("path") or "")).resolve() != generation
        or str(active.get("activation_id") or "") != activation_id
        or str(active.get("runtime_generation_id") or "") != generation.name
        or str(active.get("runtime_descriptor_sha256") or "") != descriptor_sha256
        or str(active.get("dependency_inventory_sha256") or "") != dependency_digest
        or str(receipt.get("generation_id") or "") != generation.name
        or str(receipt.get("descriptor_sha256") or "") != descriptor_sha256
        or str(receipt.get("dependency_digest") or "") != dependency_digest
    ):
        raise RuntimeDependencyWorkerError("Runtime generation identity is inconsistent.")
    return generation


def _process_group_exists(process_group: int) -> bool:
    if os.name == "nt":
        return False
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(process_group: int, signal_number: int) -> None:
    if os.name == "nt":
        return
    try:
        os.killpg(process_group, signal_number)
    except ProcessLookupError:
        pass


def _wait_for_group_exit(process_group: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _process_group_exists(process_group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def _reap_process_group(process: subprocess.Popen[bytes]) -> None:
    if not _process_group_exists(process.pid):
        return
    _signal_group(process.pid, signal.SIGTERM)
    if _wait_for_group_exit(process.pid, GROUP_TERM_TIMEOUT_SECONDS):
        return
    _signal_group(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    if not _wait_for_group_exit(process.pid, GROUP_KILL_TIMEOUT_SECONDS):
        raise RuntimeDependencyWorkerError("Runtime worker process group did not exit.")


def _worker_environment(
    invocation: WorkerInvocation, image_app_dir: Path | None = None
) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PYTHON", "LD_", "GLIBC_"))
    }
    environment.update(
        {
            RUNTIME_WORKER_ENV: "1",
            RUNTIME_WORKER_MODE_ENV: invocation.mode,
            RUNTIME_WORKER_GENERATION_ENV: str(invocation.generation),
            RUNTIME_WORKER_ACTIVATION_ENV: invocation.activation_id,
            "CHANNELWATCH_ACTIVATION_ID": invocation.activation_id,
            RUNTIME_WORKER_DESCRIPTOR_ENV: invocation.descriptor_sha256,
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CHANNELWATCH_APP_DIR": str(invocation.generation),
        }
    )
    if image_app_dir is not None:
        environment["CHANNELWATCH_IMAGE_APP_DIR"] = str(image_app_dir)
    return environment


def supervise_worker(
    invocation: WorkerInvocation, *, image_app_dir: Path | None = None
) -> None:
    worker_source: bytes | None
    if image_app_dir is None:
        worker_source = _verify_generation_for_launch(
            invocation.generation, invocation.descriptor_sha256
        )
    else:
        image_app_dir = _trusted_image_app_dir(image_app_dir)
        _verify_with_image_authority(
            image_app_dir, invocation.generation, invocation.descriptor_sha256
        )
        worker_source = None
    environment = _worker_environment(invocation, image_app_dir)
    forwarded_signal: list[int] = []
    previous_handlers: dict[int, Callable[..., object] | int | None] = {}
    forwarded_signals = (signal.SIGTERM, signal.SIGINT)
    previous_mask: set[signal.Signals] | None = None
    mask_restored = False
    process: subprocess.Popen[bytes] | None = None
    group_reaped = False

    def forward(signal_number: int, _frame: object) -> None:
        forwarded_signal.append(signal_number)
        if process is not None:
            _signal_group(process.pid, signal_number)

    try:
        if hasattr(signal, "pthread_sigmask"):
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK, forwarded_signals
            )
        process = subprocess.Popen(
            invocation.argv,
            env=environment,
            cwd=invocation.generation,
            start_new_session=True,
            stdin=subprocess.PIPE if worker_source is not None else subprocess.DEVNULL,
        )
        for signal_number in forwarded_signals:
            previous_handlers[signal_number] = signal.signal(signal_number, forward)
        if previous_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            mask_restored = True
        if worker_source is not None:
            if process.stdin is None:
                raise RuntimeDependencyWorkerError(
                    "Runtime worker input pipe is unavailable."
                )
            try:
                process.stdin.write(worker_source)
                process.stdin.close()
            except BrokenPipeError:
                if not forwarded_signal:
                    raise
        while True:
            try:
                returncode = process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if not forwarded_signal:
                    continue
                _signal_group(process.pid, signal.SIGTERM)
                try:
                    returncode = process.wait(timeout=GROUP_TERM_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    _signal_group(
                        process.pid, getattr(signal, "SIGKILL", signal.SIGTERM)
                    )
                    try:
                        returncode = process.wait(timeout=GROUP_KILL_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeDependencyWorkerError(
                            "Runtime worker did not stop after a forwarded signal."
                        ) from exc
                break
        _reap_process_group(process)
        group_reaped = True
    finally:
        try:
            if process is not None and not group_reaped:
                _reap_process_group(process)
        finally:
            try:
                for signal_number, handler in previous_handlers.items():
                    if handler is not None:
                        signal.signal(signal_number, handler)
            finally:
                if previous_mask is not None and not mask_restored:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    if forwarded_signal:
        raise SystemExit(0)
    raise RuntimeDependencyWorkerError(
        f"Runtime dependency worker exited unexpectedly with status {returncode}."
    )


def supervise_image_worker(
    *,
    image_app_dir: Path,
    runtime_dir: Path,
    generation: Path,
    activation_id: str,
    descriptor_sha256: str,
    mode: str,
    app_args: tuple[str, ...],
    host: str | None,
    port: int | None,
    log_level: str | None,
) -> None:
    if mode not in {"core", "ui"}:
        raise RuntimeDependencyWorkerError("Runtime worker mode is invalid.")
    trusted_image = _trusted_image_app_dir(image_app_dir)
    generation = _validate_explicit_generation(
        runtime_dir=runtime_dir,
        generation=generation,
        activation_id=activation_id,
        descriptor_sha256=descriptor_sha256,
    )
    worker = trusted_image / "core" / "runtime_worker.py"
    if worker.resolve(strict=True) != Path(__file__).with_name("runtime_worker.py").resolve(
        strict=True
    ):
        raise RuntimeDependencyWorkerError("Image runtime worker origin is invalid.")
    argv = [
        str(generation / "environment" / "bin" / "python"),
        "-I",
        "-B",
        "-S",
        str(worker),
        "--image-app-dir",
        str(trusted_image),
        "--mode",
        mode,
        "--generation",
        str(generation),
        "--activation-id",
        activation_id,
        "--descriptor-sha256",
        descriptor_sha256,
    ]
    if mode == "ui":
        if host is None or port is None or log_level is None:
            raise RuntimeDependencyWorkerError("Runtime UI worker options are incomplete.")
        argv.extend(
            ["--host", host, "--port", str(port), "--log-level", log_level]
        )
    argv.extend(("--", *app_args))
    invocation = WorkerInvocation(
        mode,
        generation,
        activation_id,
        descriptor_sha256,
        tuple(argv),
    )
    supervise_worker(invocation, image_app_dir=trusted_image)


def bootstrap_dependency_worker() -> bool:
    _remove_bootstrap_bytecode()
    invocation = validated_worker_invocation()
    if invocation is None:
        return False
    supervise_worker(invocation)
    return True
