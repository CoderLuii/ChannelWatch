"""Settings migration framework for ChannelWatch upgrades.

Handles defaults merging, schema versioning, auto-backup, and versioned
migrations to ensure seamless upgrades for existing users.
"""

import json
import os
import secrets
import shutil
import stat
from dataclasses import fields, MISSING
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from .atomic_io import (
    _atomic_write_secret_bytes,
    atomic_copy_file,
    atomic_write_json,
    atomic_write_private_json,
    fsync_directory,
    read_regular_file_bytes,
)
from .logging import log, LOG_STANDARD, LOG_VERBOSE
from ..notifications.template_engine import TEMPLATE_SETTINGS_DEFAULTS
from .dvr_id import canonical_dvr_id, dvr_display_name

CURRENT_SCHEMA_VERSION = 7
JOURNAL_FILE_NAME = "migration.journal"
MAX_MIGRATION_SETTINGS_BYTES = 8 * 1024 * 1024

DISK_ALERT_DEFAULTS = {
    "ds_threshold_percent": 10,
    "ds_threshold_gb": 50,
    "ds_warning_threshold_percent": 10,
    "ds_warning_threshold_gb": 50,
    "ds_critical_threshold_percent": 5,
    "ds_critical_threshold_gb": 25,
    "ds_startup_grace_seconds": 10,
    "ds_worsening_delta_gb": 1,
    "ds_worsening_delta_percent": 1.0,
    "ds_test_route_override": "",
}

# --- Defaults and version detection ---


def get_dataclass_defaults(dataclass_cls) -> Dict[str, Any]:
    """Extract default values from a dataclass class definition.

    Skips private fields (prefixed with _) since those are internal state,
    not user-facing settings.
    """
    defaults = {}
    for f in fields(dataclass_cls):
        if f.name.startswith("_"):
            continue
        if f.default is not MISSING:
            defaults[f.name] = f.default
        elif f.default_factory is not MISSING:
            defaults[f.name] = f.default_factory()
    return defaults


def detect_version(settings: Dict[str, Any]) -> int:
    """Detect the schema version of a settings dict.

    Returns the _version value if present, or 0 for pre-v0.8 settings
    that lack a version field.
    """
    return settings.get("_version", 0)


def defaults_merge(
    saved_settings: Dict[str, Any], defaults: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge saved settings onto full defaults.

    Starts with the complete defaults dict, then overlays saved settings on top.
    This ensures missing keys get default values while existing user values
    are preserved.
    """
    merged = dict(defaults)
    for key, value in saved_settings.items():
        if key in defaults:
            merged[key] = value
    if "_version" in saved_settings:
        merged["_version"] = saved_settings["_version"]
    return merged


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _normalize_number(value: Any, default: Any, cast_type):
    if _is_blank(value):
        return default
    try:
        return cast_type(value)
    except (TypeError, ValueError):
        return default


def normalize_disk_alert_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(settings)

    legacy_percent = _normalize_number(
        normalized.get("ds_threshold_percent"),
        DISK_ALERT_DEFAULTS["ds_threshold_percent"],
        int,
    )
    legacy_gb = _normalize_number(
        normalized.get("ds_threshold_gb"),
        DISK_ALERT_DEFAULTS["ds_threshold_gb"],
        int,
    )

    normalized["ds_threshold_percent"] = legacy_percent
    normalized["ds_threshold_gb"] = legacy_gb
    normalized["ds_warning_threshold_percent"] = _normalize_number(
        normalized.get("ds_warning_threshold_percent"),
        legacy_percent,
        int,
    )
    normalized["ds_warning_threshold_gb"] = _normalize_number(
        normalized.get("ds_warning_threshold_gb"),
        legacy_gb,
        int,
    )
    normalized["ds_critical_threshold_percent"] = _normalize_number(
        normalized.get("ds_critical_threshold_percent"),
        DISK_ALERT_DEFAULTS["ds_critical_threshold_percent"],
        int,
    )
    normalized["ds_critical_threshold_gb"] = _normalize_number(
        normalized.get("ds_critical_threshold_gb"),
        DISK_ALERT_DEFAULTS["ds_critical_threshold_gb"],
        int,
    )
    normalized["ds_startup_grace_seconds"] = _normalize_number(
        normalized.get("ds_startup_grace_seconds"),
        DISK_ALERT_DEFAULTS["ds_startup_grace_seconds"],
        int,
    )
    normalized["ds_worsening_delta_gb"] = _normalize_number(
        normalized.get("ds_worsening_delta_gb"),
        DISK_ALERT_DEFAULTS["ds_worsening_delta_gb"],
        int,
    )
    normalized["ds_worsening_delta_percent"] = _normalize_number(
        normalized.get("ds_worsening_delta_percent"),
        DISK_ALERT_DEFAULTS["ds_worsening_delta_percent"],
        float,
    )

    test_route_override = normalized.get("ds_test_route_override")
    if _is_blank(test_route_override):
        normalized["ds_test_route_override"] = DISK_ALERT_DEFAULTS[
            "ds_test_route_override"
        ]
    elif not isinstance(test_route_override, str):
        normalized["ds_test_route_override"] = str(test_route_override)

    return normalized


def normalize_dvr_server_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(settings)
    servers = normalized.get("dvr_servers")
    if not isinstance(servers, list):
        return normalized

    normalized_servers: List[Any] = []
    for server in servers:
        if not isinstance(server, dict):
            normalized_servers.append(server)
            continue
        updated = dict(server)
        host = str(updated.get("host", "") or "").strip()
        if host:
            updated["host"] = host
            updated["name"] = dvr_display_name(updated.get("name"), host)
        normalized_servers.append(updated)
    normalized["dvr_servers"] = normalized_servers
    return normalized


# --- Auto-backup ---


def _create_auto_backup(
    config_dir: Path, from_version: int, to_version: int
) -> Path | None:
    """Create a timestamped backup of settings.json before migration.

    Stores backups in config_dir/backups/ with rotation (max 10 kept).
    Returns the exact private backup path when creation succeeds.
    """
    settings_file = config_dir / "settings.json"
    if not settings_file.is_file():
        return None

    backup_dir = config_dir / "backups"
    try:
        try:
            backup_metadata = backup_dir.lstat()
        except FileNotFoundError:
            backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
            fsync_directory(backup_dir.parent)
            backup_metadata = backup_dir.lstat()
        if stat.S_ISLNK(backup_metadata.st_mode) or not stat.S_ISDIR(
            backup_metadata.st_mode
        ):
            raise PermissionError("The migration backup directory is unsafe.")
        if os.name != "nt":
            backup_dir.chmod(0o700)
            if stat.S_IMODE(backup_dir.stat().st_mode) != 0o700:
                raise PermissionError(
                    "The migration backup directory is not owner-only."
                )
    except OSError as e:
        log(f"Could not secure migration backup directory: {e}", level=LOG_STANDARD)
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_name = (
        f"settings.v{from_version}.{timestamp}.{secrets.token_hex(6)}.json"
    )
    backup_path = backup_dir / backup_name

    try:
        settings_bytes = read_regular_file_bytes(
            settings_file,
            max_bytes=MAX_MIGRATION_SETTINGS_BYTES,
        )
        _atomic_write_secret_bytes(backup_path, settings_bytes)
        log(f"Backup saved: {backup_path.name}", level=LOG_STANDARD)
    except (OSError, ValueError) as e:
        log(f"Failed to create backup: {e}", level=LOG_STANDARD)
        return None

    # Rotate: keep last 10 backups
    _rotate_backups(backup_dir, max_backups=10, preserve=backup_path)
    return backup_path


def auto_backup(config_dir: Path, from_version: int, to_version: int) -> bool:
    """Compatibility wrapper returning whether a private backup was created."""

    return _create_auto_backup(config_dir, from_version, to_version) is not None


def _rotate_backups(
    backup_dir: Path,
    max_backups: int = 10,
    *,
    preserve: Path | None = None,
):
    """Remove oldest backups if count exceeds max_backups."""
    try:
        preserved = Path(preserve) if preserve is not None else None
        backups = sorted(
            [
                f
                for f in backup_dir.iterdir()
                if f.name.startswith("settings")
                and stat.S_ISREG(f.lstat().st_mode)
                and f.lstat().st_nlink == 1
            ],
            key=lambda f: f.lstat().st_mtime,
        )
        while len(backups) > max_backups:
            oldest_index = next(
                (
                    index
                    for index, candidate in enumerate(backups)
                    if preserved is None or candidate != preserved
                ),
                None,
            )
            if oldest_index is None:
                break
            oldest = backups.pop(oldest_index)
            oldest.unlink()
            log(f"Rotated old backup: {oldest.name}", level=LOG_VERBOSE)
    except OSError as e:
        log(f"Backup rotation error: {e}", level=LOG_VERBOSE)


def _journal_path(config_dir: Path) -> Path:
    return config_dir / JOURNAL_FILE_NAME


def _write_journal(config_dir: Path, payload: Dict[str, Any]) -> Path:
    payload = dict(payload)
    payload["updated_at"] = datetime.now().isoformat()
    return atomic_write_json(_journal_path(config_dir), payload)


def _journal_step(
    config_dir: Path,
    *,
    step: str,
    status: str,
    from_version: int,
    to_version: int,
    backup_path: str | None,
) -> None:
    _write_journal(
        config_dir,
        {
            "step": step,
            "status": status,
            "from_version": from_version,
            "to_version": to_version,
            "backup_path": backup_path,
        },
    )


def _read_journal(config_dir: Path) -> Dict[str, Any] | None:
    journal_file = _journal_path(config_dir)
    if not journal_file.is_file():
        return None
    try:
        payload = json.loads(journal_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"Migration journal at {journal_file} is unreadable ({exc}). Repair or remove it before restarting."
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Migration journal at {journal_file} must contain a JSON object."
        )
    return payload


def _recover_interrupted_migration(
    config_dir: Path, settings: Dict[str, Any]
) -> Dict[str, Any]:
    journal = _read_journal(config_dir)
    if not journal or journal.get("status") != "started":
        return settings

    settings_file = config_dir / "settings.json"
    backup_path_raw = journal.get("backup_path") or ""
    step = journal.get("step", "unknown")
    backup_path = Path(backup_path_raw) if backup_path_raw else None

    if backup_path and backup_path.is_file():
        atomic_copy_file(backup_path, settings_file)
        restored = json.loads(backup_path.read_text(encoding="utf-8"))
        if not isinstance(restored, dict):
            raise RuntimeError(
                f"Backup {backup_path} is invalid while recovering interrupted migration step {step}."
            )
        log(
            f"Recovered interrupted migration at step '{step}' by restoring {backup_path.name}",
            level=LOG_STANDARD,
        )
        return restored

    log(
        f"Found interrupted migration at step '{step}' without a backup; resuming from current settings.",
        level=LOG_STANDARD,
    )
    return settings


# --- Versioned migrations ---


def migrate_v0_to_v1(settings: Dict[str, Any]) -> Dict[str, Any]:
    """v0.8: Add schema version and new v0.8 fields.

    v0 = pre-v0.8 settings (no _version field).
    v1 = v0.8 schema with cooldown, rate limiter, and api_key fields.
    """
    settings.setdefault("cw_alert_cooldown", 300)
    settings.setdefault("global_rate_limit", 20)
    settings.setdefault("global_rate_window", 300)
    settings.setdefault("api_key", "")
    settings["_version"] = 1
    return settings


def migrate_v1_to_v2(settings: Dict[str, Any]) -> Dict[str, Any]:
    """v0.8: Migrate Pushover from two-field to single Apprise URL.

    v1 = separate pushover_user_key and pushover_api_token fields.
    v2 = single apprise_pushover field using pover:// format.
    """
    pushover_key = settings.get("pushover_user_key", "")
    pushover_token = settings.get("pushover_api_token", "")
    apprise_pushover = settings.get("apprise_pushover", "")

    if pushover_key and pushover_token and not apprise_pushover:
        settings["apprise_pushover"] = f"{pushover_key}@{pushover_token}"

    settings.pop("pushover_user_key", None)
    settings.pop("pushover_api_token", None)
    settings["_version"] = 2
    return settings


def migrate_v2_to_v3(settings: Dict[str, Any]) -> Dict[str, Any]:
    """v0.8: Migrate from single DVR host/port to dvr_servers array.

    v2 = single channels_dvr_host and channels_dvr_port fields.
    v3 = dvr_servers array supporting multiple DVR servers.
    """
    host = settings.pop("channels_dvr_host", None)
    port = settings.pop("channels_dvr_port", 8089)

    if host and not settings.get("dvr_servers"):
        settings["dvr_servers"] = [
            {
                "id": canonical_dvr_id(host, port),
                "name": host,
                "host": host,
                "port": port,
                "enabled": True,
            }
        ]

    settings.setdefault("dvr_servers", [])
    settings["_version"] = 3
    return settings


def migrate_v3_to_v4(settings: Dict[str, Any]) -> Dict[str, Any]:
    settings = normalize_disk_alert_settings(settings)
    settings["_version"] = 4
    return settings


def migrate_v4_to_v5(settings: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(settings.get("webhooks"), list):
        settings["webhooks"] = []
    settings["_version"] = 5
    return settings


def migrate_v5_to_v6(settings: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in TEMPLATE_SETTINGS_DEFAULTS.items():
        settings.setdefault(key, value)
    settings["_version"] = 6
    return settings


def migrate_v6_to_v7(settings: Dict[str, Any]) -> Dict[str, Any]:

    settings.setdefault("multi_dvr_v2_enabled", True)

    existing = list(settings.get("dvr_servers") or [])
    if existing:
        canonicalized: List[Any] = []
        for server in existing:
            if not isinstance(server, dict):
                canonicalized.append(server)
                continue
            host = server.get("host", "")
            port = server.get("port", 8089)
            if host:
                new_id = canonical_dvr_id(host, port)
                updated = dict(server)
                updated["id"] = new_id
                canonicalized.append(updated)
                display = server.get("display_name") or server.get("name") or host
                log(
                    f"Migrated DVR '{display}' at {host}:{port} \u2192 dvr_id={new_id}",
                    level=LOG_STANDARD,
                )
            else:
                canonicalized.append(server)
        settings["dvr_servers"] = canonicalized

    host = os.getenv("CHANNELS_DVR_HOST")
    if host and not settings.get("dvr_servers"):
        port_str = os.getenv("CHANNELS_DVR_PORT", "8089")
        try:
            port = int(port_str)
        except ValueError:
            port = 8089
        dvr_id = canonical_dvr_id(host, port)
        dvr_name_env = os.getenv("CHANNELS_DVR_NAME", "")
        dvr_name = dvr_name_env or host
        new_server: Dict[str, Any] = {
            "id": dvr_id,
            "name": dvr_name,
            "host": host,
            "port": port,
            "enabled": True,
        }
        if dvr_name_env:
            new_server["display_name"] = dvr_name_env
        settings["dvr_servers"] = [new_server]
        log(
            f"Migrated DVR '{dvr_name}' at {host}:{port} \u2192 dvr_id={dvr_id}",
            level=LOG_STANDARD,
        )
        print(
            f"[Core Migration] WARNING: CHANNELS_DVR_HOST env var is deprecated and will be "
            f"supported only for compatibility. Server '{host}:{port}' has been migrated to dvr_servers[0]. "
            f"Use CHANNELS_DVR_SERVERS, the UI settings page, or persisted dvr_servers in settings.json.",
            flush=True,
        )

    settings["_version"] = 7
    return settings


def archive_legacy_session_state(config_dir: Path) -> bool:
    """Move session_state_default.json to /config/backups/ if it exists.

    Returns True on success, False when the file is absent or on I/O error.
    """
    state_file = config_dir / "session_state_default.json"
    if not state_file.is_file():
        return False
    backup_dir = config_dir / "backups"
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"session_state_default.{timestamp}.json"
        shutil.move(str(state_file), str(backup_path))
        log(f"Archived legacy session state: {backup_path.name}", level=LOG_STANDARD)
        return True
    except OSError as e:
        log(f"Failed to archive session_state_default.json: {e}", level=LOG_STANDARD)
        return False


def _adopt_session_state(config_dir: Path, old_id: str, new_id: str) -> None:
    if not old_id or old_id == new_id:
        return
    old_file = config_dir / f"session_state_{old_id}.json"
    new_file = config_dir / f"session_state_{new_id}.json"
    if old_file.is_file() and not new_file.is_file():
        try:
            shutil.copy2(str(old_file), str(new_file))
            log(
                f"Copied session state {old_file.name} \u2192 {new_file.name}",
                level=LOG_STANDARD,
            )
        except OSError as e:
            log(
                f"Failed to copy session state {old_id} \u2192 {new_id}: {e}",
                level=LOG_STANDARD,
            )


def _seed_session_state_from_default(config_dir: Path, dvr_id: str) -> None:
    target_file = config_dir / f"session_state_{dvr_id}.json"
    default_file = config_dir / "session_state_default.json"
    if not target_file.is_file() and default_file.is_file():
        try:
            shutil.copy2(str(default_file), str(target_file))
            log(
                f"Seeded session state for {dvr_id} from legacy default",
                level=LOG_STANDARD,
            )
        except OSError as e:
            log(f"Failed to seed session state for {dvr_id}: {e}", level=LOG_STANDARD)


def _handle_v7_session_state(
    config_dir: Path, old_dvrs: List[Any], new_dvrs: List[Any]
) -> None:
    old_by_hp: Dict[Tuple[str, int], str] = {}
    for d in old_dvrs:
        if isinstance(d, dict):
            h = d.get("host", "")
            p = int(d.get("port", 8089))
            oid = d.get("id", "")
            if h and oid:
                old_by_hp[(h, p)] = oid

    for dvr in new_dvrs:
        if not isinstance(dvr, dict):
            continue
        h = dvr.get("host", "")
        p = int(dvr.get("port", 8089))
        new_id = dvr.get("id", "")
        if not new_id:
            continue
        old_id = old_by_hp.get((h, p), "")
        _adopt_session_state(config_dir, old_id, new_id)
        _seed_session_state_from_default(config_dir, new_id)

    archive_legacy_session_state(config_dir)


MIGRATIONS: List[Tuple[int, int, Callable[[Dict[str, Any]], Dict[str, Any]]]] = [
    (0, 1, migrate_v0_to_v1),
    (1, 2, migrate_v1_to_v2),
    (2, 3, migrate_v2_to_v3),
    (3, 4, migrate_v3_to_v4),
    (4, 5, migrate_v4_to_v5),
    (5, 6, migrate_v5_to_v6),
    (6, 7, migrate_v6_to_v7),
]


def run_migrations(
    settings: Dict[str, Any], from_version: int, to_version: int
) -> Dict[str, Any]:
    """Run all necessary migrations to bring settings from from_version to to_version."""
    version = from_version
    for mv_from, mv_to, migrate_fn in MIGRATIONS:
        if version == mv_from and mv_to <= to_version:
            log(f"Running migration v{mv_from} -> v{mv_to}", level=LOG_STANDARD)
            settings = migrate_fn(settings)
            version = mv_to
    return settings


def migrate_settings(config_dir: Path, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Full migration pipeline: detect version, backup, migrate, merge defaults.

    Returns the migrated settings dict. Does NOT write to disk (caller handles that).
    """
    settings = dict(_recover_interrupted_migration(config_dir, settings))
    version = detect_version(settings)
    settings_file = config_dir / "settings.json"

    if version > CURRENT_SCHEMA_VERSION:
        log(
            f"Settings schema version ({version}) is newer than this version of "
            f"ChannelWatch supports ({CURRENT_SCHEMA_VERSION}). Some features may not work. "
            f"Consider upgrading ChannelWatch.",
            level=LOG_STANDARD,
        )
        return normalize_dvr_server_settings(settings)

    if version < CURRENT_SCHEMA_VERSION:
        if not settings and not settings_file.is_file():
            return normalize_dvr_server_settings(
                run_migrations(settings, version, CURRENT_SCHEMA_VERSION)
            )

        old_dvr_servers = list(settings.get("dvr_servers") or [])
        needs_v7_session_archival = version < 7 <= CURRENT_SCHEMA_VERSION
        backup_path: str | None = None
        # A durable pre-migration backup is mandatory whenever there is a live
        # settings file to protect.  Some callers (and first-run imports) pass
        # an in-memory legacy mapping before settings.json exists; there is no
        # on-disk source to back up in that case, and this function does not
        # persist the returned mapping on their behalf.
        if settings_file.is_file():
            _journal_step(
                config_dir,
                step="backup",
                status="started",
                from_version=version,
                to_version=CURRENT_SCHEMA_VERSION,
                backup_path=None,
            )
            created_backup = _create_auto_backup(
                config_dir,
                version,
                CURRENT_SCHEMA_VERSION,
            )
            if created_backup is None:
                _journal_step(
                    config_dir,
                    step="backup",
                    status="failed",
                    from_version=version,
                    to_version=CURRENT_SCHEMA_VERSION,
                    backup_path=None,
                )
                raise RuntimeError(
                    "Settings migration requires a private, durable pre-migration "
                    "backup. Repair /config/backups permissions and restart."
                )
            backup_path = str(created_backup)
            _journal_step(
                config_dir,
                step="backup",
                status="completed",
                from_version=version,
                to_version=CURRENT_SCHEMA_VERSION,
                backup_path=backup_path,
            )
        _journal_step(
            config_dir,
            step="schema_migrations",
            status="started",
            from_version=version,
            to_version=CURRENT_SCHEMA_VERSION,
            backup_path=backup_path,
        )
        settings = run_migrations(settings, version, CURRENT_SCHEMA_VERSION)
        _journal_step(
            config_dir,
            step="schema_migrations",
            status="completed",
            from_version=version,
            to_version=CURRENT_SCHEMA_VERSION,
            backup_path=backup_path,
        )
        if needs_v7_session_archival:
            _journal_step(
                config_dir,
                step="session_state",
                status="started",
                from_version=version,
                to_version=CURRENT_SCHEMA_VERSION,
                backup_path=backup_path,
            )
            _handle_v7_session_state(
                config_dir, old_dvr_servers, settings.get("dvr_servers") or []
            )
            _journal_step(
                config_dir,
                step="session_state",
                status="completed",
                from_version=version,
                to_version=CURRENT_SCHEMA_VERSION,
                backup_path=backup_path,
            )
        from .encryption import (
            ENCRYPTION_KEY_FILE,
            encrypt_dvr_api_keys,
            encrypt_webhook_credentials,
        )

        key_file = config_dir / ENCRYPTION_KEY_FILE.name
        _journal_step(
            config_dir,
            step="encrypt_dvr_api_keys",
            status="started",
            from_version=version,
            to_version=CURRENT_SCHEMA_VERSION,
            backup_path=backup_path,
        )
        settings["dvr_servers"] = encrypt_dvr_api_keys(
            settings.get("dvr_servers") or [], key_file
        )
        settings["webhooks"] = encrypt_webhook_credentials(
            settings.get("webhooks") or [], key_file
        )
        _journal_step(
            config_dir,
            step="encrypt_dvr_api_keys",
            status="completed",
            from_version=version,
            to_version=CURRENT_SCHEMA_VERSION,
            backup_path=backup_path,
        )
        if settings_file.is_file():
            _journal_step(
                config_dir,
                step="persist_settings",
                status="started",
                from_version=version,
                to_version=CURRENT_SCHEMA_VERSION,
                backup_path=backup_path,
            )
            atomic_write_private_json(settings_file, settings)
            _journal_step(
                config_dir,
                step="persist_settings",
                status="completed",
                from_version=version,
                to_version=CURRENT_SCHEMA_VERSION,
                backup_path=backup_path,
            )

    return normalize_dvr_server_settings(settings)
