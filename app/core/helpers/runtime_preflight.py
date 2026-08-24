"""Fail-closed, non-sensitive runtime secret-storage preflight."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .encryption import ENCRYPTION_KEY_FILE
from .key_manager import (
    ManagedKeyUnavailableError,
    ensure_managed_key,
)


@dataclass(frozen=True)
class RuntimePreflight:
    status: str
    setup_required: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def public_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "setup_required": self.setup_required,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def inspect_runtime_preflight(
    key_file: Path = ENCRYPTION_KEY_FILE,
    *,
    settings_file: Path | None = None,
) -> RuntimePreflight:
    """Prepare and describe application-managed local key material safely."""

    try:
        ensure_managed_key(Path(key_file), settings_file=settings_file)
    except ManagedKeyUnavailableError as exc:
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=(exc.code,),
        )
    except (OSError, PermissionError):
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=("secret_storage_key_file_unreadable",),
        )
    return RuntimePreflight(status="ready", setup_required=False)
