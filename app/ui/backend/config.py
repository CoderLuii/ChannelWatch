# CONFIGURATION
import json
import os
import secrets
from importlib import import_module
from pathlib import Path

from core.helpers.atomic_io import atomic_write_private_json, read_regular_file_bytes
from core.helpers.config import (
    MAX_SETTINGS_FILE_BYTES,
    ConfigLoadError,
    _build_recovery_message,
)
from pydantic import ValidationError

from .schemas import AppSettings


def _load_current_schema_version() -> int:
    try:
        migration = import_module("core.helpers.migration")
    except ImportError:
        return 7
    return int(getattr(migration, "CURRENT_SCHEMA_VERSION", 7))


CURRENT_SCHEMA_VERSION = _load_current_schema_version()

CONFIG_DIR = Path(os.getenv("CONFIG_PATH", "/config"))
CONFIG_FILE = CONFIG_DIR / "settings.json"


# UTILITIES
def get_model_defaults(model):
    defaults = {}
    for name, field in model.model_fields.items():
        default_value = field.get_default()
        if default_value is not None:
            defaults[name] = default_value
    return defaults


def _validation_error_summary(error: ValidationError) -> str:
    """Describe invalid fields without copying credential-bearing input values."""

    summaries: list[str] = []
    for issue in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:8]:
        location = ".".join(str(part) for part in issue.get("loc", ())) or "settings"
        error_type = str(issue.get("type") or "invalid_value")
        summaries.append(f"{location}: {error_type}")
    return "; ".join(summaries) or "invalid settings values"


# SETTINGS MANAGEMENT
def _webhook_identity(webhook: dict, index: int) -> str:
    identity = str(webhook.get("id", "") or "").strip()
    return identity or f"legacy-webhook-{index}"


def _merge_webhook_secrets(data: dict, existing: dict) -> dict:
    """Preserve masked webhook credentials only by a stable identity."""

    incoming_webhooks = data.get("webhooks")
    existing_webhooks = existing.get("webhooks")
    if not isinstance(incoming_webhooks, list) or not isinstance(
        existing_webhooks, list
    ):
        return data

    existing_by_id = {}
    for index, webhook in enumerate(existing_webhooks):
        if isinstance(webhook, dict):
            identity = _webhook_identity(webhook, index)
            if identity in existing_by_id:
                raise ValueError("Persisted webhook identities must be unique.")
            existing_by_id[identity] = webhook

    merged_webhooks = []
    incoming_ids: set[str] = set()
    for index, webhook in enumerate(incoming_webhooks):
        if not isinstance(webhook, dict):
            merged_webhooks.append(webhook)
            continue

        merged = dict(webhook)
        identity = str(merged.get("id", "") or "").strip()
        url_masked = "****" in str(merged.get("url", "") or "")
        secret_masked = merged.get("secret") in (None, "****")
        if not identity:
            if url_masked or secret_masked:
                raise ValueError(
                    "A masked webhook save is missing its stable identity; reload settings and retry."
                )
            identity = f"webhook_{secrets.token_urlsafe(18)}"
        if identity in incoming_ids:
            raise ValueError("Incoming webhook identities must be unique.")
        incoming_ids.add(identity)
        merged["id"] = identity

        match = existing_by_id.get(identity)
        if (url_masked or secret_masked) and match is None:
            raise ValueError(
                "A masked webhook no longer matches persisted settings; reload and retry."
            )
        if match is not None:
            if url_masked:
                merged["url"] = match.get("url", "")
            if secret_masked:
                merged["secret"] = match.get("secret", "")

        merged_webhooks.append(merged)

    data["webhooks"] = merged_webhooks
    return data


def _preserve_security_setup_marker(data: dict, existing: dict) -> dict:
    marker = data.get("security_setup_completed")
    if marker is None:
        if "security_setup_completed" in existing:
            existing_marker = existing.get("security_setup_completed")
            if existing_marker is None:
                data.pop("security_setup_completed", None)
            else:
                data["security_setup_completed"] = existing_marker
        else:
            data.pop("security_setup_completed", None)
    return data


def load_settings() -> AppSettings:
    """Loads settings from the config file, returning defaults only when absent."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(
            f"Warning: Could not create config directory {CONFIG_DIR}: {e}. Using default settings."
        )
        return AppSettings()

    from core.helpers.maintenance_transaction import (
        configuration_maintenance_lock,
    )

    # Key rotation, restore, and credential reset replace encryption.key and
    # settings.json as one recoverable transaction.  Keep the shared lock for
    # the complete read-and-decrypt sequence so this process never observes a
    # transient pair assembled from two different transaction generations.
    with configuration_maintenance_lock(CONFIG_DIR):
        return _load_settings_locked()


def _load_settings_locked() -> AppSettings:
    """Read and decrypt settings while the caller holds the maintenance lock."""

    model_defaults = get_model_defaults(AppSettings)
    settings_data = {}

    if CONFIG_FILE.is_file():
        try:
            loaded_data = json.loads(
                read_regular_file_bytes(
                    CONFIG_FILE, max_bytes=MAX_SETTINGS_FILE_BYTES
                ).decode("utf-8-sig")
            )
            if isinstance(loaded_data, dict):
                settings_data = loaded_data
            else:
                raise ConfigLoadError(
                    _build_recovery_message(
                        CONFIG_FILE,
                        f"expected a JSON object but found {type(loaded_data).__name__}",
                    )
                )

        except json.JSONDecodeError as e:
            raise ConfigLoadError(
                _build_recovery_message(CONFIG_FILE, f"invalid JSON ({e})")
            ) from e
        except Exception as e:
            if isinstance(e, ConfigLoadError):
                raise
            raise ConfigLoadError(
                _build_recovery_message(CONFIG_FILE, f"read error ({e})")
            ) from e

    try:
        from core.helpers.encryption import (
            ENCRYPTION_KEY_FILE,
            decrypt_registered_credentials_with_diagnostics,
        )
        from core.helpers.protected_credentials import (
            publish_protected_credential_failures,
        )

        key_file = CONFIG_DIR / ENCRYPTION_KEY_FILE.name
        protected = decrypt_registered_credentials_with_diagnostics(
            settings_data,
            key_file,
        )
        settings_data = protected.settings
        publish_protected_credential_failures(protected.failures)
    except Exception:
        # Loading the non-secret settings shell must remain possible in a
        # storage-recovery state.  The decryptor itself guarantees that an
        # unreadable ``fernet:`` value is never returned to a consumer.
        from core.helpers.protected_credentials import (
            disable_failed_protected_credential_owners,
            encrypted_protected_values,
            publish_protected_credential_failures,
        )

        failed_values = encrypted_protected_values(settings_data)
        failure_paths = tuple(
            f"{item.collection}[{item.index}].{item.field}"
            for item in failed_values
        )
        publish_protected_credential_failures(failure_paths)
        for item in failed_values:
            collection = settings_data.get(item.collection)
            if isinstance(collection, list) and item.index < len(collection):
                entry = collection[item.index]
                if isinstance(entry, dict):
                    entry[item.field] = ""
        settings_data = disable_failed_protected_credential_owners(
            settings_data,
            failure_paths,
        )

    cleaned_data = settings_data.copy()
    for key, value in settings_data.items():
        if value is None and key in model_defaults and model_defaults[key] is not None:
            print(
                f"Info: Ignoring null value for '{key}' from {CONFIG_FILE}, using schema default."
            )
            del cleaned_data[key]

    try:
        final_settings = AppSettings(**cleaned_data)
        return final_settings
    except ValidationError as e:
        raise ConfigLoadError(
            _build_recovery_message(
                CONFIG_FILE,
                f"schema validation failed ({_validation_error_summary(e)})",
            )
        ) from None
    except Exception:
        raise ConfigLoadError(
            _build_recovery_message(CONFIG_FILE, "settings construction failed")
        ) from None


def save_settings(settings: AppSettings, *, lock_already_held: bool = False):
    """Saves the provided settings object to the config file. Preserves _version."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        from core.helpers.encryption import (
            ENCRYPTION_KEY_FILE,
            bootstrap_encryption_key,
            encrypt_dvr_api_keys,
            encrypt_webhook_credentials,
        )
        from core.helpers.maintenance_transaction import (
            configuration_maintenance_lock,
        )
        from core.helpers.protected_credentials import (
            get_protected_credential_failures,
            preserve_failed_ciphertexts,
        )

        key_file = CONFIG_DIR / ENCRYPTION_KEY_FILE.name
        if not key_file.exists() and not key_file.is_symlink():
            if lock_already_held:
                raise RuntimeError(
                    "Managed key initialization must finish before a locked settings save."
                )
            # First creation owns the same interprocess lock and, when legacy
            # plaintext credentials exist, journals key+settings together.
            # Complete that initialization before entering the ordinary save
            # lock to avoid recursively acquiring the file lock.
            bootstrap_encryption_key(key_file, settings_file=CONFIG_FILE)

        def _save_locked() -> None:
            data = json.loads(settings.model_dump_json(indent=2))
            existing = {}
            if CONFIG_FILE.is_file():
                try:
                    loaded_existing = json.loads(
                        read_regular_file_bytes(
                            CONFIG_FILE, max_bytes=MAX_SETTINGS_FILE_BYTES
                        ).decode("utf-8-sig")
                    )
                    if isinstance(loaded_existing, dict):
                        existing = loaded_existing
                except (json.JSONDecodeError, OSError, ValueError):
                    existing = {}

            data = _merge_webhook_secrets(data, existing)
            data = _preserve_security_setup_marker(data, existing)
            data = preserve_failed_ciphertexts(
                data,
                existing,
                get_protected_credential_failures(),
            )

            data["dvr_servers"] = encrypt_dvr_api_keys(
                data.get("dvr_servers") or [],
                key_file,
            )
            data["webhooks"] = encrypt_webhook_credentials(
                data.get("webhooks") or [],
                key_file,
            )

            # Ensure _version is always present (frontend doesn't manage it)
            if "_version" not in data:
                data["_version"] = existing.get("_version", CURRENT_SCHEMA_VERSION)
            atomic_write_private_json(CONFIG_FILE, data)
        if lock_already_held:
            _save_locked()
        else:
            with configuration_maintenance_lock(CONFIG_DIR):
                _save_locked()
        print(f"Info: Settings successfully saved to {CONFIG_FILE}")
    except OSError as e:
        print(f"Error: Could not create config directory {CONFIG_DIR} for saving: {e}")
        raise
    except Exception as e:
        print(f"Error: Failed to save settings to {CONFIG_FILE}: {e}")
        raise
