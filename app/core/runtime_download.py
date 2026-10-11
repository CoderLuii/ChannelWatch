"""Bound runtime downloads while keeping archive bytes off the Python heap."""

from __future__ import annotations

import os
import time
from typing import BinaryIO
import urllib.error
import urllib.request


def fetch_runtime_bundle(
    url: str, destination: BinaryIO, *, max_bytes: int,
    deadline_seconds: float = 300.0, timeout: float = 20.0,
) -> None:
    from core.update_center import (
        UpdateManifestError, _TrustedUpdateRedirectHandler, validate_trusted_url,
    )

    validate_trusted_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "ChannelWatch-UpdateCenter"}
    )
    opener = urllib.request.build_opener(_TrustedUpdateRedirectHandler())
    deadline = time.monotonic() + deadline_seconds
    total = 0
    try:
        with opener.open(request, timeout=timeout) as response:  # nosec B310
            validate_trusted_url(response.geturl())
            while True:
                if time.monotonic() >= deadline:
                    raise UpdateManifestError("Runtime download exceeded its deadline.")
                chunk = response.read(65_536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UpdateManifestError("Downloaded update data exceeds size limit.")
                pending = memoryview(chunk)
                while pending:
                    written = destination.write(pending)
                    if written is None or written <= 0:
                        raise OSError("Runtime download could not be written completely.")
                    pending = pending[written:]
        destination.flush()
        os.fsync(destination.fileno())
        destination.seek(0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateManifestError(
            "Runtime download failed. Check container internet and available storage, "
            "then try again."
        ) from exc
