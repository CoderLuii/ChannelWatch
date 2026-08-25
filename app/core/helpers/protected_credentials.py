"""Single registry for credential-bearing settings fields.

Every key-management, backup, restore, rotation, and health path must use this
module rather than maintaining its own list of protected settings fields.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from typing import Any

FERNET_PREFIX = "fernet:"


@dataclass(frozen=True)
class ProtectedFieldSpec:
    collection: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class ProtectedValue:
    collection: str
    index: int
    field: str
    value: str


PROTECTED_FIELD_REGISTRY: tuple[ProtectedFieldSpec, ...] = (
    ProtectedFieldSpec("dvr_servers", ("api_key",)),
    ProtectedFieldSpec("webhooks", ("url", "secret")),
)

_diagnostics_lock = Lock()
_diagnostic_failures: tuple[str, ...] = ()


def is_protected_ciphertext(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(FERNET_PREFIX)


def iter_protected_values(settings: dict[str, Any]) -> Iterator[ProtectedValue]:
    for spec in PROTECTED_FIELD_REGISTRY:
        collection = settings.get(spec.collection)
        if not isinstance(collection, list):
            continue
        for index, item in enumerate(collection):
            if not isinstance(item, dict):
                continue
            for field in spec.fields:
                value = item.get(field)
                if isinstance(value, str) and value:
                    yield ProtectedValue(spec.collection, index, field, value)


def encrypted_protected_values(settings: dict[str, Any]) -> tuple[ProtectedValue, ...]:
    return tuple(
        item
        for item in iter_protected_values(settings)
        if is_protected_ciphertext(item.value)
    )


def has_encrypted_protected_values(settings: dict[str, Any]) -> bool:
    return next(iter(encrypted_protected_values(settings)), None) is not None


def transform_protected_values(
    settings: dict[str, Any],
    transform: Callable[[ProtectedValue], str],
) -> dict[str, Any]:
    """Return a deep copy with every non-empty registered value transformed."""

    result = deepcopy(settings)
    for item in iter_protected_values(result):
        collection = result[item.collection]
        collection[item.index][item.field] = transform(item)
    return result


def clear_protected_values(settings: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Clear only registered credential fields, preserving all other state."""

    cleared = 0

    def _clear(item: ProtectedValue) -> str:
        nonlocal cleared
        cleared += 1
        return ""

    return transform_protected_values(settings, _clear), cleared


def clear_protected_values_and_disable(
    settings: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Clear registered credentials and disable only their owning entries.

    A DVR without its API key, or a webhook without one of its protected
    delivery fields, must not remain enabled after an explicit credential
    reset.  Entries which did not contain a protected value are left exactly
    as supplied so reset and backup-restore share one fail-closed policy.
    """

    affected_entries = {
        (item.collection, item.index) for item in iter_protected_values(settings)
    }
    cleared_settings, cleared_count = clear_protected_values(settings)
    for collection_name, index in affected_entries:
        collection = cleared_settings.get(collection_name)
        if not isinstance(collection, list) or index >= len(collection):
            continue
        entry = collection[index]
        if isinstance(entry, dict):
            entry["enabled"] = False
    return cleared_settings, cleared_count


def disable_failed_protected_credential_owners(
    settings: dict[str, Any],
    failure_paths: Iterable[str],
) -> dict[str, Any]:
    """Disable only entries that own an unreadable registered credential.

    Failure paths are diagnostic identifiers, not selectors supplied by a
    caller.  Construct the accepted paths from the central registry so malformed,
    out-of-range, or unregistered paths cannot affect unrelated settings.
    The source mapping is never mutated and protected field values are preserved
    exactly as supplied by the decryptor.
    """

    failures = {str(path) for path in failure_paths}
    result = deepcopy(settings)
    for spec in PROTECTED_FIELD_REGISTRY:
        collection = result.get(spec.collection)
        if not isinstance(collection, list):
            continue
        for index, entry in enumerate(collection):
            if not isinstance(entry, dict):
                continue
            if any(
                f"{spec.collection}[{index}].{field}" in failures
                for field in spec.fields
            ):
                entry["enabled"] = False
    return result


def preserve_failed_ciphertexts(
    incoming_settings: dict[str, Any],
    existing_settings: dict[str, Any],
    failure_paths: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """Prevent a diagnostic load->save from erasing unreadable ciphertext.

    The existing token is retained only at a registered failed path and only
    while the incoming value is empty, masked, or still ciphertext. A deliberate
    non-empty plaintext replacement wins and is encrypted by the normal save
    path.
    """

    failures = set(failure_paths)
    result = deepcopy(incoming_settings)
    for spec in PROTECTED_FIELD_REGISTRY:
        incoming_collection = result.get(spec.collection)
        existing_collection = existing_settings.get(spec.collection)
        if not isinstance(incoming_collection, list) or not isinstance(
            existing_collection, list
        ):
            continue
        for index, incoming_item in enumerate(incoming_collection):
            if index >= len(existing_collection):
                continue
            existing_item = existing_collection[index]
            if not isinstance(incoming_item, dict) or not isinstance(
                existing_item, dict
            ):
                continue
            for field in spec.fields:
                path = f"{spec.collection}[{index}].{field}"
                if path not in failures:
                    continue
                existing_value = existing_item.get(field)
                if not is_protected_ciphertext(existing_value):
                    continue
                incoming_value = incoming_item.get(field)
                explicit_plaintext = (
                    isinstance(incoming_value, str)
                    and bool(incoming_value.strip())
                    and incoming_value != "****"
                    and not is_protected_ciphertext(incoming_value)
                )
                if not explicit_plaintext:
                    incoming_item[field] = existing_value
    return result


def publish_protected_credential_failures(
    failure_paths: tuple[str, ...] | list[str],
) -> None:
    """Replace the in-process non-sensitive credential diagnostic snapshot."""

    normalized = tuple(dict.fromkeys(str(path) for path in failure_paths))
    with _diagnostics_lock:
        global _diagnostic_failures
        _diagnostic_failures = normalized


def get_protected_credential_failures() -> tuple[str, ...]:
    with _diagnostics_lock:
        return _diagnostic_failures
