"""Image-stable process launcher for active app bundles."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIG_DIR = Path(os.environ.get("CONFIG_PATH", "/config"))
RUNTIME_DIR = CONFIG_DIR / "channelwatch-runtime"
IMAGE_APP_DIR = Path(os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app")).resolve()
IMAGE_STATIC_UI_DIR = IMAGE_APP_DIR / "ui" / "backend" / "static_ui"
FALLBACK_ENV = "CHANNELWATCH_BUNDLE_FALLBACK"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[RuntimeLauncher] {message}", file=sys.stderr, flush=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
    fsync_directory(path.parent)


def selected_app_dir() -> Path:
    configured = os.environ.get("CHANNELWATCH_ACTIVE_APP_DIR", "").strip()
    return Path(configured).resolve() if configured else IMAGE_APP_DIR


def selected_static_ui_dir(app_dir: Path) -> Path:
    configured = os.environ.get("CHANNELWATCH_ACTIVE_STATIC_UI_DIR", "").strip()
    if configured:
        return Path(configured).resolve()
    if app_dir == IMAGE_APP_DIR:
        return IMAGE_STATIC_UI_DIR
    return app_dir / "ui" / "backend" / "static_ui"


def prepare_import_path(app_dir: Path) -> None:
    sys.path = [str(app_dir), *(item for item in sys.path if item != str(app_dir))]
    os.environ["PYTHONPATH"] = str(app_dir)
    os.environ["CHANNELWATCH_APP_DIR"] = str(app_dir)
    os.environ["CW_STATIC_UI_DIR"] = str(selected_static_ui_dir(app_dir))
    try:
        os.chdir(app_dir)
    except OSError:
        pass


def rollback_failed_activation(error: str) -> None:
    active_path = RUNTIME_DIR / "active.json"
    rollback_path = RUNTIME_DIR / "rollback.json"
    job_path = RUNTIME_DIR / "update-job.json"

    rollback = load_json(rollback_path, None)
    current = load_json(active_path, None)
    previous = rollback.get("previous_active") if isinstance(rollback, dict) else None
    rolled_back_to = "image"

    if isinstance(previous, dict) and previous.get("path"):
        atomic_write_json(active_path, previous)
        rolled_back_to = str(previous.get("version") or "previous bundle")
    else:
        try:
            active_path.unlink()
        except FileNotFoundError:
            pass

    atomic_write_json(
        job_path,
        {
            "job_id": f"activation-failed-{int(datetime.now(timezone.utc).timestamp())}",
            "operation": "apply",
            "status": "failed",
            "version": current.get("version") if isinstance(current, dict) else None,
            "message": "Update activation failed. ChannelWatch rolled back to the previous runtime.",
            "error": error[:2000],
            "rollback_applied": True,
            "rolled_back_from": current.get("version") if isinstance(current, dict) else None,
            "rolled_back_to": rolled_back_to,
            "failed_at": utc_now(),
            "updated_at": utc_now(),
        },
    )


def exec_image_fallback(mode: str, args: list[str]) -> None:
    os.environ[FALLBACK_ENV] = "1"
    os.environ["CHANNELWATCH_ACTIVE_APP_DIR"] = str(IMAGE_APP_DIR)
    os.environ["CHANNELWATCH_ACTIVE_STATIC_UI_DIR"] = str(IMAGE_STATIC_UI_DIR)
    os.execv(sys.executable, [sys.executable, __file__, mode, *args])


def run_core(args: argparse.Namespace) -> None:
    sys.argv = ["python -m core.main", *args.app_args]
    runpy.run_module("core.main", run_name="__main__")


def run_ui(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "ui.backend.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch ChannelWatch from image or active bundle.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    core = subparsers.add_parser("core")
    core.add_argument("app_args", nargs=argparse.REMAINDER)

    ui = subparsers.add_parser("ui")
    ui.add_argument("--host", default="0.0.0.0")
    ui.add_argument("--port", type=int, default=8501)
    ui.add_argument("--log-level", default="warning")

    args, unknown_args = parser.parse_known_args(argv)
    if args.mode == "core":
        args.app_args.extend(unknown_args)
    elif unknown_args:
        parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
    app_dir = selected_app_dir()
    prepare_import_path(app_dir)
    log(f"Launching {args.mode} from {app_dir}")

    try:
        if args.mode == "core":
            run_core(args)
        elif args.mode == "ui":
            run_ui(args)
        return 0
    except Exception:
        error = traceback.format_exc()
        if app_dir != IMAGE_APP_DIR and os.environ.get(FALLBACK_ENV) != "1":
            log("Active bundle failed during startup; rolling back and restarting from image.")
            rollback_failed_activation(error)
            fallback_args = (
                args.app_args
                if args.mode == "core"
                else ["--host", args.host, "--port", str(args.port), "--log-level", args.log_level]
            )
            exec_image_fallback(args.mode, fallback_args)
        print(error, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
