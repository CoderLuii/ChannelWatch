"""Atomic filesystem helpers for durable config and migration writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return

    directory = Path(path)
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(
    path: Path, data: bytes, *, temp_path: Path | None = None
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = temp_path or destination.with_name(f"{destination.name}.tmp")

    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temp, destination)
    fsync_directory(destination.parent)
    return destination


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    temp_path: Path | None = None,
) -> Path:
    return atomic_write_bytes(path, text.encode(encoding), temp_path=temp_path)


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    temp_path: Path | None = None,
) -> Path:
    serialized = json.dumps(payload, indent=indent, sort_keys=sort_keys)
    return atomic_write_text(path, serialized, temp_path=temp_path)


def atomic_copy_file(
    source: Path, destination: Path, *, temp_path: Path | None = None
) -> Path:
    return atomic_write_bytes(
        destination, Path(source).read_bytes(), temp_path=temp_path
    )
