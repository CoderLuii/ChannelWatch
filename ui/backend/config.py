# CONFIGURATION
import json
import os
from pathlib import Path
from .schemas import AppSettings
from pydantic import ValidationError

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
def load_settings() -> AppSettings:
    """Loads settings from the config file, returning defaults if not found or invalid."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Warning: Could not create config directory {CONFIG_DIR}: {e}. Using default settings.")
        return AppSettings()

    model_defaults = get_model_defaults(AppSettings)
    settings_data = {}

    if CONFIG_FILE.is_file():
        try:
            loaded_data = json.loads(CONFIG_FILE.read_text())
            if isinstance(loaded_data, dict):
                settings_data = loaded_data
            else:
                print(f"Warning: Config file {CONFIG_FILE} does not contain a valid JSON object. Using defaults.")
                return AppSettings()
                
        except json.JSONDecodeError as e:
            print(f"Warning: Could not parse {CONFIG_FILE}: {e}. Using default settings.")
            return AppSettings()
        except Exception as e:
            print(f"Warning: Unexpected error reading {CONFIG_FILE}: {e}. Using default settings.")
            return AppSettings()

    cleaned_data = settings_data.copy()
    for key, value in settings_data.items():
        if value is None and key in model_defaults and model_defaults[key] is not None:
            print(f"Info: Ignoring null value for '{key}' from {CONFIG_FILE}, using schema default.")
            del cleaned_data[key]
            
    try:
        final_settings = AppSettings(**cleaned_data)
        return final_settings
    except ValidationError as e:
        print(f"Warning: Validation errors in config file {CONFIG_FILE}: {e}. Using default settings.")
        return AppSettings()
    except Exception as e:
         print(f"Warning: Unexpected error creating settings object: {e}. Using default settings.")
         return AppSettings()


def save_settings(settings: AppSettings):
    """Saves the provided settings object to the config file."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(settings.model_dump_json(indent=2))
        print(f"Info: Settings successfully saved to {CONFIG_FILE}")
    except OSError as e:
        print(f"Error: Could not create config directory {CONFIG_DIR} for saving: {e}")
        raise
    except Exception as e:
        print(f"Error: Failed to save settings to {CONFIG_FILE}: {e}")
        raise 