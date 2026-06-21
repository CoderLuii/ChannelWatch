# CONFIGURATION
import json
import os
from pathlib import Path
from importlib import import_module
from .schemas import AppSettings
from pydantic import ValidationError
from core.helpers.atomic_io import atomic_write_json
from core.helpers.config import ConfigLoadError, _build_recovery_message


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


# SETTINGS MANAGEMENT
def _merge_webhook_secrets(data: dict, existing: dict) -> dict:
    incoming_webhooks = data.get("webhooks")
    existing_webhooks = existing.get("webhooks")
    if not isinstance(incoming_webhooks, list) or not isinstance(
        existing_webhooks, list
    ):
        return data

    existing_by_url = {}
    for index, webhook in enumerate(existing_webhooks):
        if isinstance(webhook, dict):
            existing_by_url[str(webhook.get("url", "") or "").strip()] = (
                index,
                webhook,
            )

    merged_webhooks = []
    for index, webhook in enumerate(incoming_webhooks):
        if not isinstance(webhook, dict):
            merged_webhooks.append(webhook)
            continue

        merged = dict(webhook)
        secret = merged.get("secret")
        if secret in ("", None, "****"):
            match = None
            webhook_url = str(merged.get("url", "") or "").strip()
            if webhook_url and webhook_url in existing_by_url:
                match = existing_by_url[webhook_url][1]
            elif index < len(existing_webhooks) and isinstance(
                existing_webhooks[index], dict
            ):
                match = existing_webhooks[index]

            if match is not None:
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

    model_defaults = get_model_defaults(AppSettings)
    settings_data = {}

    if CONFIG_FILE.is_file():
        try:
            loaded_data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
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
        from core.helpers.encryption import decrypt_dvr_api_keys, ENCRYPTION_KEY_FILE

        settings_data["dvr_servers"] = decrypt_dvr_api_keys(
            settings_data.get("dvr_servers") or [],
            CONFIG_DIR / ENCRYPTION_KEY_FILE.name,
        )
    except Exception:
        pass

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
            _build_recovery_message(CONFIG_FILE, f"schema validation failed ({e})")
        ) from e
    except Exception as e:
        raise ConfigLoadError(
            _build_recovery_message(CONFIG_FILE, f"settings construction failed ({e})")
        ) from e


def save_settings(settings: AppSettings):
    """Saves the provided settings object to the config file. Preserves _version."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = json.loads(settings.model_dump_json(indent=2))
        existing = {}
        if CONFIG_FILE.is_file():
            try:
                loaded_existing = json.loads(CONFIG_FILE.read_text())
                if isinstance(loaded_existing, dict):
                    existing = loaded_existing
            except (json.JSONDecodeError, OSError):
                existing = {}

        data = _merge_webhook_secrets(data, existing)
        data = _preserve_security_setup_marker(data, existing)

        from core.helpers.encryption import encrypt_dvr_api_keys, ENCRYPTION_KEY_FILE

        data["dvr_servers"] = encrypt_dvr_api_keys(
            data.get("dvr_servers") or [],
            CONFIG_DIR / ENCRYPTION_KEY_FILE.name,
        )

        # Ensure _version is always present (frontend doesn't manage it)
        if "_version" not in data:
            if CONFIG_FILE.is_file():
                try:
                    data["_version"] = existing.get("_version", CURRENT_SCHEMA_VERSION)
                except (json.JSONDecodeError, OSError):
                    data["_version"] = CURRENT_SCHEMA_VERSION
            else:
                data["_version"] = CURRENT_SCHEMA_VERSION
        atomic_write_json(CONFIG_FILE, data)
        print(f"Info: Settings successfully saved to {CONFIG_FILE}")
    except OSError as e:
        print(f"Error: Could not create config directory {CONFIG_DIR} for saving: {e}")
        raise
    except Exception as e:
        print(f"Error: Failed to save settings to {CONFIG_FILE}: {e}")
        raise
