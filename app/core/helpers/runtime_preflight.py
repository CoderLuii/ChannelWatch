"""Fail-closed, non-sensitive runtime secret-storage preflight."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import InvalidToken

from .atomic_io import (
    SecretStorageKeyUnavailableError,
    _atomic_read_secret_bytes,
    _is_secret_envelope,
    secret_storage_key_status,
)
from .encryption import ENCRYPTION_KEY_FILE, _validate_key_permissions


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
) -> RuntimePreflight:
    """Describe whether protected local key material can be used safely."""

    path = Path(key_file)
    external = secret_storage_key_status()

    if not path.exists():
        if external.available:
            return RuntimePreflight(status="ready", setup_required=False)
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=(external.code or "secret_storage_key_missing",),
        )

    try:
        _validate_key_permissions(path)
        stored = path.read_bytes()
    except (OSError, PermissionError):
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=("secret_storage_key_file_unreadable",),
        )

    if not _is_secret_envelope(stored):
        # Every ChannelWatch legacy plaintext key was created with
        # os.urandom(32). Treat any other shape as corrupt local key material;
        # otherwise preflight would claim readiness and Fernet construction
        # would fail later during a credential write.
        if len(stored) != 32:
            return RuntimePreflight(
                status="setup_required",
                setup_required=True,
                blockers=("secret_storage_key_mismatch",),
            )
        if external.available:
            return RuntimePreflight(status="ready", setup_required=False)
        return RuntimePreflight(
            status="migration_recommended",
            setup_required=False,
            warnings=("legacy_plaintext_key_migration_recommended",),
        )

    if not external.available:
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=(external.code or "secret_storage_key_missing",),
        )

    try:
        _atomic_read_secret_bytes(path)
    except (InvalidToken, ValueError):
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=("secret_storage_key_mismatch",),
        )
    except SecretStorageKeyUnavailableError as exc:
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=(exc.code,),
        )
    except OSError:
        return RuntimePreflight(
            status="setup_required",
            setup_required=True,
            blockers=("secret_storage_key_file_unreadable",),
        )

    return RuntimePreflight(status="ready", setup_required=False)
