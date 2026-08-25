"""Bootstrap and load the ChannelWatch encryption key.

Public API:
- bootstrap_encryption_key()         logical 32-byte encryption key
- encrypt_value() / decrypt_value()  Fernet AEAD, "fernet:" prefix
- encrypt_dvr_api_keys()             batch encrypt api_key in dvr_servers list
- decrypt_dvr_api_keys()             batch decrypt api_key in dvr_servers list
- encrypt_webhook_credentials()      batch encrypt webhook URL and secret fields
- decrypt_webhook_credentials()      batch decrypt webhook URL and secret fields
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken

from .key_manager import (
    ManagedKeyUnavailableError,
    ensure_managed_key,
)
from .protected_credentials import (
    FERNET_PREFIX,
    ProtectedValue,
    disable_failed_protected_credential_owners,
    encrypted_protected_values,
    is_protected_ciphertext,
    publish_protected_credential_failures,
    transform_protected_values,
)

ENCRYPTION_KEY_FILE = Path(os.getenv("CONFIG_PATH", "/config")) / "encryption.key"
_ALLOWED_MODE = 0o600
WEBHOOK_CREDENTIAL_FIELDS = ("url", "secret")


class EncryptionKeyUnavailableError(RuntimeError):
    """Raised when DVR API keys cannot be encrypted safely."""


def _validate_key_permissions(path: Path) -> None:
    if os.name == "nt":
        return

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(
            f"Refusing to use {path}: permissions must be 0600 or stricter, got {oct(mode)}"
        )


def bootstrap_encryption_key(
    key_file: Path = ENCRYPTION_KEY_FILE,
    *,
    settings_file: Path | None = None,
) -> bytes:
    """Load or create the application-managed logical encryption key."""

    return ensure_managed_key(key_file, settings_file=settings_file).key


def _make_fernet(raw_key: bytes):
    from base64 import urlsafe_b64encode

    from cryptography.fernet import Fernet

    return Fernet(urlsafe_b64encode(raw_key))


def is_fernet_encrypted(value: str) -> bool:
    return is_protected_ciphertext(value)


def encrypt_value(plaintext: str, raw_key: bytes) -> str:
    """Encrypt a UTF-8 string. Returns 'fernet:<token>'."""
    token = _make_fernet(raw_key).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{FERNET_PREFIX}{token}"


def decrypt_value(ciphertext: str, raw_key: bytes) -> str:
    """Decrypt a 'fernet:<token>' string. Returns UTF-8 plaintext.

    Raises ValueError if the prefix is absent. Raises
    cryptography.fernet.InvalidToken if decryption fails.
    """
    if not is_fernet_encrypted(ciphertext):
        raise ValueError("Value is not Fernet-encrypted.")
    token = ciphertext[len(FERNET_PREFIX) :]
    return _make_fernet(raw_key).decrypt(token.encode("ascii")).decode("utf-8")


def encrypt_dvr_api_keys(
    dvr_servers: list, key_file: Path = ENCRYPTION_KEY_FILE
) -> list:
    """Return a new list where any plaintext api_key in each server dict is encrypted.

    No-ops for entries that are already 'fernet:...' or have no api_key.
    Creates the key file via bootstrap if absent.
    Raises EncryptionKeyUnavailableError if encryption cannot be performed safely.
    """
    plain = [
        s
        for s in dvr_servers
        if isinstance(s, dict)
        and s.get("api_key")
        and not is_fernet_encrypted(s["api_key"])
    ]
    if not plain:
        return list(dvr_servers)

    try:
        raw_key = bootstrap_encryption_key(key_file)
    except (OSError, PermissionError, ManagedKeyUnavailableError) as exc:
        raise EncryptionKeyUnavailableError(
            f"Unable to access encryption key at {key_file}"
        ) from exc

    result = []
    for server in dvr_servers:
        if (
            isinstance(server, dict)
            and server.get("api_key")
            and not is_fernet_encrypted(server["api_key"])
        ):
            server = dict(server)
            server["api_key"] = encrypt_value(server["api_key"], raw_key)
        result.append(server)
    return result


def decrypt_dvr_api_keys(
    dvr_servers: list,
    key_file: Path = ENCRYPTION_KEY_FILE,
    *,
    failure_paths: list[str] | None = None,
) -> list:
    """Return a new list where any 'fernet:...' api_key is decrypted.

    Never returns an unreadable ``fernet:`` token. Affected fields are cleared
    in the returned copy and optionally reported by non-sensitive JSON paths.
    """
    encrypted = [
        s
        for s in dvr_servers
        if isinstance(s, dict) and is_fernet_encrypted(s.get("api_key", ""))
    ]
    if not encrypted:
        return list(dvr_servers)

    try:
        raw_key = bootstrap_encryption_key(key_file)
    except (OSError, PermissionError, ManagedKeyUnavailableError):
        result = []
        for index, server in enumerate(dvr_servers):
            if isinstance(server, dict) and is_fernet_encrypted(
                server.get("api_key", "")
            ):
                server = dict(server)
                server["api_key"] = ""
                if failure_paths is not None:
                    failure_paths.append(f"dvr_servers[{index}].api_key")
            result.append(server)
        return result

    result = []
    for index, server in enumerate(dvr_servers):
        if isinstance(server, dict) and is_fernet_encrypted(server.get("api_key", "")):
            server = dict(server)
            try:
                server["api_key"] = decrypt_value(server["api_key"], raw_key)
            except (InvalidToken, TypeError, UnicodeError, ValueError):
                server["api_key"] = ""
                if failure_paths is not None:
                    failure_paths.append(f"dvr_servers[{index}].api_key")
        result.append(server)
    return result


def _encrypt_mapping_fields(
    items: list,
    field_names: tuple[str, ...],
    key_file: Path,
) -> list:
    plain = [
        item
        for item in items
        if isinstance(item, dict)
        and any(
            item.get(field) and not is_fernet_encrypted(str(item.get(field, "")))
            for field in field_names
        )
    ]
    if not plain:
        return list(items)

    try:
        raw_key = bootstrap_encryption_key(key_file)
    except (OSError, PermissionError, ManagedKeyUnavailableError) as exc:
        raise EncryptionKeyUnavailableError(
            f"Unable to access encryption key at {key_file}"
        ) from exc

    result = []
    for item in items:
        if isinstance(item, dict):
            item = dict(item)
            for field in field_names:
                value = item.get(field)
                if value and not is_fernet_encrypted(str(value)):
                    item[field] = encrypt_value(str(value), raw_key)
        result.append(item)
    return result


def _decrypt_mapping_fields(
    items: list,
    field_names: tuple[str, ...],
    key_file: Path,
    *,
    collection_name: str,
    failure_paths: list[str] | None = None,
) -> list:
    encrypted = [
        item
        for item in items
        if isinstance(item, dict)
        and any(is_fernet_encrypted(str(item.get(field, ""))) for field in field_names)
    ]
    if not encrypted:
        return list(items)

    try:
        raw_key = bootstrap_encryption_key(key_file)
    except (OSError, PermissionError, ManagedKeyUnavailableError):
        result = []
        for index, item in enumerate(items):
            if isinstance(item, dict):
                item = dict(item)
                for field in field_names:
                    if is_fernet_encrypted(str(item.get(field, "") or "")):
                        item[field] = ""
                        if failure_paths is not None:
                            failure_paths.append(f"{collection_name}[{index}].{field}")
            result.append(item)
        return result

    result = []
    for index, item in enumerate(items):
        if isinstance(item, dict):
            item = dict(item)
            for field in field_names:
                value = item.get(field)
                if is_fernet_encrypted(str(value or "")):
                    try:
                        item[field] = decrypt_value(str(value), raw_key)
                    except (InvalidToken, TypeError, UnicodeError, ValueError):
                        item[field] = ""
                        if failure_paths is not None:
                            failure_paths.append(f"{collection_name}[{index}].{field}")
        result.append(item)
    return result


def encrypt_webhook_credentials(
    webhooks: list, key_file: Path = ENCRYPTION_KEY_FILE
) -> list:
    """Return a new webhook list with URL and shared-secret fields encrypted."""
    return _encrypt_mapping_fields(webhooks, WEBHOOK_CREDENTIAL_FIELDS, key_file)


def decrypt_webhook_credentials(
    webhooks: list,
    key_file: Path = ENCRYPTION_KEY_FILE,
    *,
    failure_paths: list[str] | None = None,
) -> list:
    """Return a new webhook list with encrypted URL and shared-secret fields decrypted."""
    return _decrypt_mapping_fields(
        webhooks,
        WEBHOOK_CREDENTIAL_FIELDS,
        key_file,
        collection_name="webhooks",
        failure_paths=failure_paths,
    )


@dataclass(frozen=True)
class CredentialValidationReport:
    encrypted_count: int
    decrypted_count: int
    failures: tuple[str, ...]

    @property
    def all_valid(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class ProtectedCredentialLoadResult:
    settings: dict[str, Any]
    failures: tuple[str, ...]


def decrypt_registered_credentials_with_diagnostics(
    settings: dict[str, Any],
    key_file: Path = ENCRYPTION_KEY_FILE,
) -> ProtectedCredentialLoadResult:
    """Decrypt registered fields while isolating unreadable credentials."""

    result = dict(settings)
    failures: list[str] = []
    result["dvr_servers"] = decrypt_dvr_api_keys(
        settings.get("dvr_servers") or [],
        key_file,
        failure_paths=failures,
    )
    result["webhooks"] = decrypt_webhook_credentials(
        settings.get("webhooks") or [],
        key_file,
        failure_paths=failures,
    )
    result = disable_failed_protected_credential_owners(result, failures)
    publish_protected_credential_failures(failures)
    return ProtectedCredentialLoadResult(
        settings=result,
        failures=tuple(failures),
    )


def validate_protected_credentials(
    settings: dict[str, Any], raw_key: bytes
) -> CredentialValidationReport:
    """Validate registered ciphertext without exposing values or key material."""

    encrypted = encrypted_protected_values(settings)
    decrypted_count = 0
    failures: list[str] = []
    for item in encrypted:
        try:
            decrypt_value(item.value, raw_key)
        except (InvalidToken, TypeError, UnicodeError, ValueError):
            failures.append(f"{item.collection}[{item.index}].{item.field}")
        else:
            decrypted_count += 1
    return CredentialValidationReport(
        encrypted_count=len(encrypted),
        decrypted_count=decrypted_count,
        failures=tuple(failures),
    )


def encrypt_registered_plaintext_credentials(
    settings: dict[str, Any], raw_key: bytes
) -> dict[str, Any]:
    """Encrypt every non-empty registered plaintext value with ``raw_key``."""

    def _encrypt(item: ProtectedValue) -> str:
        if is_fernet_encrypted(item.value):
            return item.value
        return encrypt_value(item.value, raw_key)

    return transform_protected_values(settings, _encrypt)


def reencrypt_registered_credentials(
    settings: dict[str, Any], old_key: bytes, new_key: bytes
) -> tuple[dict[str, Any], int]:
    """Re-encrypt every registered credential, failing before partial output."""

    rotated_count = 0

    def _rotate(item: ProtectedValue) -> str:
        nonlocal rotated_count
        plaintext = (
            decrypt_value(item.value, old_key)
            if is_fernet_encrypted(item.value)
            else item.value
        )
        rotated_count += 1
        return encrypt_value(plaintext, new_key)

    return transform_protected_values(settings, _rotate), rotated_count
