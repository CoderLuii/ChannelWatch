"""Bounded image-owned subprocess preparation for runtime generations."""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable

try:
    import fcntl
except ImportError:  # pragma: no cover - runtime generation support is POSIX-only
    fcntl = None


RUNTIME_PREPARATION_TIMEOUT_SECONDS = 600
MAX_DESCRIPTOR_BYTES = 4 * 1024 * 1024
GROUP_TERM_TIMEOUT_SECONDS = 3.0
GROUP_KILL_TIMEOUT_SECONDS = 2.0
RESULT_SCHEMA = 1
_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36


class RuntimePreparationError(RuntimeError):
    """A bounded runtime preparation child failed safely."""


class RuntimePreparationTimeout(RuntimePreparationError):
    """The aggregate runtime preparation deadline expired."""


@dataclass(frozen=True)
class PreparedRuntimeGeneration:
    generation_id: str
    app_dir: Path
    python_executable: Path
    dependency_digest: str


class _ForwardedSignal(BaseException):
    def __init__(self, signal_number: int):
        self.signal_number = signal_number


def _safe_image_file(
    path: Path,
    *,
    label: str,
    require_single_link: bool = True,
    require_immutable_mode: bool = True,
) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.stat()
    except OSError as exc:
        raise RuntimePreparationError(f"The image {label} is unavailable.") from exc
    if (
        not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode))
        or not stat.S_ISREG(target.st_mode)
        or (require_single_link and target.st_nlink != 1)
        or (require_immutable_mode and stat.S_IMODE(target.st_mode) & 0o022)
    ):
        raise RuntimePreparationError(f"The image {label} is not trusted.")
    return path


def _image_authority(
    image_app_dir: Path | None, image_python: Path | None
) -> tuple[Path, Path, Path, bool]:
    configured = os.environ.get("CHANNELWATCH_IMAGE_APP_DIR")
    development_fallback = False
    if image_app_dir is None:
        if configured:
            image_app_dir = Path(configured)
        elif (Path("/app") / "core/runtime_preparation.py").is_file():
            image_app_dir = Path("/app")
        else:
            image_app_dir = Path(__file__).resolve().parent.parent
            development_fallback = True
    try:
        image_app = image_app_dir.resolve(strict=True)
    except OSError as exc:
        raise RuntimePreparationError("The image application path is unavailable.") from exc
    helper = _safe_image_file(
        image_app / "core/runtime_preparation.py",
        label="preparation helper",
        require_immutable_mode=not development_fallback,
    )
    if helper.resolve(strict=True).parent.parent != image_app:
        raise RuntimePreparationError("The image preparation helper origin is invalid.")
    if image_python is None:
        if configured or image_app == Path("/app"):
            image_python = Path("/venv/bin/python")
        else:
            image_python = Path(sys.executable)
    interpreter = _safe_image_file(
        image_python, label="Python interpreter", require_single_link=False
    )
    return image_app, helper, interpreter, development_fallback


def _descriptor_bytes(descriptor: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            descriptor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimePreparationError("The runtime descriptor is invalid.") from exc
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        raise RuntimePreparationError("The runtime descriptor exceeds the safe limit.")
    return payload


def _load_image_verifier(
    image_app: Path,
    *,
    require_immutable_mode: bool,
) -> Any:
    module_path = _safe_image_file(
        image_app / "core/runtime_generation.py",
        label="runtime verifier",
        require_immutable_mode=require_immutable_mode,
    )
    module_name = "_channelwatch_image_runtime_preparation_verifier"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimePreparationError("The image runtime verifier is unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        if Path(str(module.__file__)).resolve() != module_path.resolve(strict=True):
            raise RuntimePreparationError("The image runtime verifier origin is invalid.")
        return module
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise RuntimePreparationError(str(exc) or "Runtime descriptor validation failed.") from exc


def _forget_image_verifier(module: Any) -> None:
    sys.modules.pop(str(module.__name__), None)


def _verify_descriptor_with_image_authority(
    image_app: Path,
    descriptor: dict[str, Any],
    *,
    require_immutable_mode: bool,
) -> None:
    module = _load_image_verifier(
        image_app, require_immutable_mode=require_immutable_mode
    )
    try:
        platform_value = module.detect_runtime_platform()
        module.validate_runtime_descriptor(descriptor, platform_value)
    except Exception as exc:
        raise RuntimePreparationError(str(exc) or "Runtime descriptor validation failed.") from exc
    finally:
        _forget_image_verifier(module)


def _copy_to_temporary(source: bytes | BinaryIO, temporary: BinaryIO) -> None:
    if isinstance(source, bytes):
        temporary.write(source)
    else:
        source.seek(0)
        while chunk := source.read(1024 * 1024):
            temporary.write(chunk)
        source.seek(0)
    temporary.flush()
    temporary.seek(0)


def _readonly_archive_fd(
    source: bytes | BinaryIO, stack: ExitStack
) -> int:
    candidate: BinaryIO
    if isinstance(source, bytes):
        candidate = stack.enter_context(
            tempfile.TemporaryFile(mode="w+b")
        )
        _copy_to_temporary(source, candidate)
    else:
        try:
            source.flush()
            source.seek(0)
            source.fileno()
            candidate = source
        except (AttributeError, OSError):
            candidate = stack.enter_context(
                tempfile.TemporaryFile(mode="w+b")
            )
            _copy_to_temporary(source, candidate)
    try:
        descriptor = os.open(
            f"/proc/self/fd/{candidate.fileno()}",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise RuntimePreparationError("A read-only runtime archive cannot be opened.") from exc
    stack.callback(os.close, descriptor)
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        raise RuntimePreparationError("The runtime archive is not a regular file.")
    if fcntl is not None and (fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE) != os.O_RDONLY:
        raise RuntimePreparationError("The runtime archive descriptor is writable.")
    return descriptor


def _child_environment(image_app: Path, interpreter: Path) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PYTHON", "LD_", "GLIBC_"))
    }
    environment.update(
        {
            "CHANNELWATCH_IMAGE_APP_DIR": str(image_app),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    library_candidates = [
        interpreter.resolve(strict=True).parent.parent / "lib",
        Path(str(sysconfig.get_config_var("LIBDIR") or "")),
    ]
    library_name = f"libpython{sys.version_info.major}.{sys.version_info.minor}.so*"
    for library_dir in library_candidates:
        if library_dir.is_dir() and any(library_dir.glob(library_name)):
            environment["LD_LIBRARY_PATH"] = str(library_dir)
            break
    return environment


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_group(process_group: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _group_exists(process_group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def _signal_group(process_group: int, signal_number: int) -> None:
    try:
        os.killpg(process_group, signal_number)
    except ProcessLookupError:
        pass


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    if _group_exists(process.pid):
        _signal_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=GROUP_TERM_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    if _wait_group(process.pid, GROUP_TERM_TIMEOUT_SECONDS):
        return
    _signal_group(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=GROUP_KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise RuntimePreparationError("The runtime preparation child could not be reaped.") from exc
    if not _wait_group(process.pid, GROUP_KILL_TIMEOUT_SECONDS):
        raise RuntimePreparationError("The runtime preparation process group remained alive.")


def _process_group_members(process_group: int) -> set[int]:
    members: set[int] = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return members
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_fields = (entry / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            if int(stat_fields[2]) == process_group:
                members.add(int(entry.name))
        except (IndexError, OSError, ValueError):
            continue
    return members


def _child_emergency_exit(exit_code: int) -> None:
    own_pid = os.getpid()
    process_group = os.getpgrp()
    for signal_number, timeout in (
        (signal.SIGTERM, GROUP_TERM_TIMEOUT_SECONDS),
        (signal.SIGKILL, GROUP_KILL_TIMEOUT_SECONDS),
    ):
        deadline = time.monotonic() + timeout
        while True:
            members = _process_group_members(process_group) - {own_pid}
            if not members:
                break
            for pid in members:
                try:
                    os.kill(pid, signal_number)
                except ProcessLookupError:
                    pass
            while True:
                try:
                    waited, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if waited == 0:
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
    os._exit(exit_code)


def _arm_child_lifecycle(parent_pid: int, timeout_seconds: float) -> None:
    if parent_pid <= 1 or timeout_seconds <= 0:
        raise RuntimePreparationError("The runtime preparation lifecycle is invalid.")
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(
            signal.SIG_UNBLOCK, (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)
        )
    handling_signal = False

    def terminate(signal_number: int, _frame: object) -> None:
        nonlocal handling_signal
        if handling_signal:
            os._exit(128 + signal_number)
        handling_signal = True
        _child_emergency_exit(124 if signal_number == signal.SIGALRM else 128 + signal_number)

    for signal_number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(signal_number, terminate)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise RuntimePreparationError("The runtime preparation subreaper could not start.")
    if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        raise RuntimePreparationError("The runtime preparation parent guard could not start.")
    if os.getppid() != parent_pid:
        _child_emergency_exit(143)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)


def _result_from_output(
    output: bytes, descriptor: dict[str, Any], releases_dir: Path
) -> PreparedRuntimeGeneration:
    try:
        result = json.loads(output.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePreparationError(
            "The runtime preparation child returned invalid output."
        ) from exc
    if not isinstance(result, dict):
        raise RuntimePreparationError("The runtime preparation child returned invalid output.")
    if result.get("schema") != RESULT_SCHEMA or result.get("status") != "ok":
        message = str(result.get("error") or "Runtime preparation failed.")
        raise RuntimePreparationError(message)
    required = {
        "schema",
        "status",
        "generation_id",
        "app_dir",
        "python_executable",
        "dependency_digest",
    }
    if set(result) != required:
        raise RuntimePreparationError("The runtime preparation result shape is invalid.")
    generation_id = str(result["generation_id"])
    expected_id = str(descriptor.get("generation_id") or "")
    expected_digest = str(descriptor.get("dependency_inventory_sha256") or "")
    expected_app = releases_dir / expected_id
    app_dir = Path(str(result["app_dir"]))
    python_executable = Path(str(result["python_executable"]))
    if (
        generation_id != expected_id
        or not app_dir.is_absolute()
        or app_dir != expected_app
        or str(result["dependency_digest"]) != expected_digest
        or python_executable != app_dir / "environment/bin/python"
    ):
        raise RuntimePreparationError("The runtime preparation result identity is invalid.")
    return PreparedRuntimeGeneration(
        generation_id=generation_id,
        app_dir=app_dir.resolve(strict=True),
        python_executable=python_executable,
        dependency_digest=expected_digest,
    )


def prepare_runtime_archive_bounded(
    source: bytes | BinaryIO,
    releases_dir: Path,
    descriptor: dict[str, Any],
    *,
    timeout_seconds: float = RUNTIME_PREPARATION_TIMEOUT_SECONDS,
    image_app_dir: Path | None = None,
    image_python: Path | None = None,
    on_started: Callable[[], None] | None = None,
) -> PreparedRuntimeGeneration:
    """Prepare one generation in a cancellable image-owned subprocess."""

    if os.name == "nt" or fcntl is None:
        raise RuntimePreparationError("Bounded runtime preparation requires POSIX fd passing.")
    if timeout_seconds <= 0:
        raise ValueError("Runtime preparation timeout must be positive.")
    releases_dir = Path(releases_dir)
    if not releases_dir.is_absolute():
        raise RuntimePreparationError("The runtime releases directory must be absolute.")
    image_app, helper, interpreter, development_fallback = _image_authority(
        image_app_dir, image_python
    )
    _verify_descriptor_with_image_authority(
        image_app,
        descriptor,
        require_immutable_mode=not development_fallback,
    )
    signed_descriptor = _descriptor_bytes(descriptor)
    process: subprocess.Popen[bytes] | None = None
    previous_handlers: dict[int, Any] = {}
    previous_mask: set[signal.Signals] | None = None
    install_signal_guard = threading.current_thread() is threading.main_thread()

    with ExitStack() as stack:
        archive_fd = _readonly_archive_fd(source, stack)
        argv = (
            str(interpreter),
            "-I",
            "-B",
            str(helper),
            "--child",
            "--archive-fd",
            str(archive_fd),
            "--releases-dir",
            str(releases_dir),
            "--parent-pid",
            str(os.getpid()),
            "--timeout-seconds",
            str(timeout_seconds),
        )

        def forward(signal_number: int, _frame: object) -> None:
            if process is not None:
                _signal_group(process.pid, signal_number)
            raise _ForwardedSignal(signal_number)

        try:
            if install_signal_guard and hasattr(signal, "pthread_sigmask"):
                previous_mask = signal.pthread_sigmask(
                    signal.SIG_BLOCK, (signal.SIGTERM, signal.SIGINT)
                )
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_child_environment(image_app, interpreter),
                pass_fds=(archive_fd,),
                start_new_session=True,
            )
            if install_signal_guard:
                for signal_number in (signal.SIGTERM, signal.SIGINT):
                    previous_handlers[signal_number] = signal.signal(
                        signal_number, forward
                    )
            if previous_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                previous_mask = None
            if on_started is not None:
                on_started()
            try:
                output, _stderr = process.communicate(
                    input=signed_descriptor, timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired as exc:
                _terminate_group(process)
                raise RuntimePreparationTimeout(
                    f"Runtime preparation exceeded the {int(timeout_seconds)}-second limit."
                ) from exc
            _terminate_group(process)
        except _ForwardedSignal as exc:
            if process is not None:
                _terminate_group(process)
            raise SystemExit(0) from exc
        except BaseException:
            if process is not None:
                _terminate_group(process)
            raise
        finally:
            for signal_number, handler in previous_handlers.items():
                signal.signal(signal_number, handler)
            if previous_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    if process.returncode != 0:
        if process.returncode == 124:
            raise RuntimePreparationTimeout(
                f"Runtime preparation exceeded the {int(timeout_seconds)}-second limit."
            )
        try:
            failure = json.loads(output.decode("utf-8"))
            message = str(failure.get("error") or "Runtime preparation failed.")
        except (UnicodeError, json.JSONDecodeError, AttributeError):
            message = "Runtime preparation failed in the image helper."
        raise RuntimePreparationError(message)
    result = _result_from_output(output, descriptor, releases_dir)
    verifier = _load_image_verifier(
        image_app, require_immutable_mode=not development_fallback
    )
    try:
        verifier.verify_runtime_generation(
            verifier.RuntimeGeneration(
                generation_id=result.generation_id,
                app_dir=result.app_dir,
                python_executable=result.python_executable,
                dependency_digest=result.dependency_digest,
            )
        )
    except Exception as exc:
        raise RuntimePreparationError(
            str(exc) or "The prepared runtime generation failed parent verification."
        ) from exc
    finally:
        _forget_image_verifier(verifier)
    return result


def _child_main(
    archive_fd: int,
    releases_dir: Path,
    parent_pid: int,
    timeout_seconds: float,
) -> int:
    _arm_child_lifecycle(parent_pid, timeout_seconds)
    if fcntl is None or (fcntl.fcntl(archive_fd, fcntl.F_GETFL) & os.O_ACCMODE) != os.O_RDONLY:
        raise RuntimePreparationError("The inherited runtime archive is not read-only.")
    payload = sys.stdin.buffer.read(MAX_DESCRIPTOR_BYTES + 1)
    if len(payload) > MAX_DESCRIPTOR_BYTES:
        raise RuntimePreparationError("The runtime descriptor exceeds the safe limit.")
    try:
        descriptor = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePreparationError("The runtime descriptor is invalid.") from exc
    if not isinstance(descriptor, dict):
        raise RuntimePreparationError("The runtime descriptor is invalid.")
    image_app = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(image_app))
    os.environ["CHANNELWATCH_IMAGE_APP_DIR"] = str(image_app)
    with open(os.devnull, "w", encoding="utf-8") as discarded:
        with redirect_stdout(discarded), redirect_stderr(discarded):
            from core.runtime_archive import install_runtime_archive

            with os.fdopen(os.dup(archive_fd), "rb") as archive:
                generation = install_runtime_archive(archive, releases_dir, descriptor)
    result = {
        "schema": RESULT_SCHEMA,
        "status": "ok",
        "generation_id": generation.generation_id,
        "app_dir": str(generation.app_dir),
        "python_executable": str(generation.python_executable),
        "dependency_digest": generation.dependency_digest,
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true", required=True)
    parser.add_argument("--archive-fd", type=int, required=True)
    parser.add_argument("--releases-dir", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        return _child_main(
            args.archive_fd,
            args.releases_dir,
            args.parent_pid,
            args.timeout_seconds,
        )
    except BaseException as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "status": "error",
            "error": str(exc) or "Runtime preparation failed.",
        }
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
