"""Container startup for ChannelWatch."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONFIG_DIR = Path(os.environ.get("CONFIG_PATH", "/config"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"
APP_DEFAULT_TZ = "America/Los_Angeles"
CURRENT_SCHEMA_VERSION = 7
SUPERVISOR_TEMPLATE = Path("/etc/supervisor/conf.d/supervisord.conf.template")
SUPERVISOR_CONF = Path("/tmp/supervisord.conf")
SUPERVISOR_RUNTIME_DIR = Path(
    os.environ.get("CHANNELWATCH_RUNTIME_DIR", "/tmp/channelwatch")
)
SUPERVISOR_SOCKET = SUPERVISOR_RUNTIME_DIR / "supervisor.sock"

DEFAULT_SETTINGS = {
    "dvr_servers": [],
    "tz": APP_DEFAULT_TZ,
    "log_level": 1,
    "log_retention_days": 7,
    "history_retention_days": 90,
    "multi_dvr_v2_enabled": True,
    "auth_mode": "",
    "rbac_enabled": False,
    "security_setup_completed": None,
    "alert_channel_watching": True,
    "alert_vod_watching": True,
    "alert_disk_space": True,
    "alert_recording_events": True,
    "stream_count": True,
    "monitor_stale_seconds": 300,
    "cw_channel_name": True,
    "cw_channel_number": True,
    "cw_program_name": True,
    "cw_device_name": True,
    "cw_device_ip": True,
    "cw_stream_source": True,
    "cw_image_source": "PROGRAM",
    "cw_alert_cooldown": 300,
    "global_rate_limit": 20,
    "global_rate_window": 300,
    "stream_card_image": "program",
    "recording_card_image": "program",
    "api_key": "",
    "ics_feed_enabled": False,
    "ics_feed_token": "",
    "rss_feed_enabled": False,
    "rss_feed_token": "",
    "webhooks": [],
    "trusted_notification_destinations": [],
    "rd_alert_scheduled": True,
    "rd_alert_started": True,
    "rd_alert_completed": True,
    "rd_alert_cancelled": True,
    "rd_program_name": True,
    "rd_program_desc": True,
    "rd_duration": True,
    "rd_channel_name": True,
    "rd_channel_number": True,
    "rd_type": True,
    "vod_title": True,
    "vod_episode_title": True,
    "vod_summary": True,
    "vod_duration": True,
    "vod_progress": True,
    "vod_image": True,
    "vod_rating": True,
    "vod_genres": True,
    "vod_cast": True,
    "vod_device_name": True,
    "vod_device_ip": True,
    "vod_alert_cooldown": 300,
    "vod_significant_threshold": 300,
    "channel_cache_ttl": 86400,
    "program_cache_ttl": 86400,
    "job_cache_ttl": 3600,
    "vod_cache_ttl": 86400,
    "ds_threshold_percent": 10,
    "ds_threshold_gb": 50,
    "ds_warning_threshold_percent": 10,
    "ds_warning_threshold_gb": 50,
    "ds_critical_threshold_percent": 5,
    "ds_critical_threshold_gb": 25,
    "ds_alert_cooldown": 3600,
    "ds_startup_grace_seconds": 10,
    "ds_worsening_delta_gb": 1,
    "ds_worsening_delta_percent": 1.0,
    "ds_test_route_override": "",
    "apprise_pushover": "",
    "apprise_discord": "",
    "apprise_email": "",
    "apprise_email_to": "",
    "apprise_telegram": "",
    "apprise_slack": "",
    "apprise_gotify": "",
    "apprise_matrix": "",
    "apprise_custom": "",
    "error_reporting_dsn": "",
    "notification_routing": {},
    "_version": CURRENT_SCHEMA_VERSION,
}

BOOTSTRAP_ENV_MAP = {
    "CW_API_KEY": ("api_key", str),
    "CW_LOG_LEVEL": ("log_level", int),
    "CW_APPRISE_DISCORD": ("apprise_discord", str),
    "CW_APPRISE_PUSHOVER": ("apprise_pushover", str),
    "CW_APPRISE_TELEGRAM": ("apprise_telegram", str),
    "CW_APPRISE_EMAIL": ("apprise_email", str),
    "CW_APPRISE_EMAIL_TO": ("apprise_email_to", str),
    "CW_APPRISE_SLACK": ("apprise_slack", str),
    "CW_APPRISE_GOTIFY": ("apprise_gotify", str),
    "CW_APPRISE_MATRIX": ("apprise_matrix", str),
    "CW_APPRISE_CUSTOM": ("apprise_custom", str),
    "CW_ALERT_CHANNEL_WATCHING": ("alert_channel_watching", bool),
    "CW_ALERT_VOD_WATCHING": ("alert_vod_watching", bool),
    "CW_ALERT_DISK_SPACE": ("alert_disk_space", bool),
    "CW_ALERT_RECORDING_EVENTS": ("alert_recording_events", bool),
    "CW_DS_THRESHOLD_PERCENT": ("ds_threshold_percent", int),
    "CW_DS_THRESHOLD_GB": ("ds_threshold_gb", int),
    "CW_DS_ALERT_COOLDOWN": ("ds_alert_cooldown", int),
}


def warning(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr, flush=True)


def info(message: str) -> None:
    print(message, flush=True)


def running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def chown_path(path: Path, uid: int, gid: int) -> None:
    if not hasattr(os, "chown") or not running_as_root():
        return
    try:
        os.chown(path, uid, gid)
    except OSError as exc:
        warning(f"Failed to chown {path}: {exc}")


def parse_id(name: str, default: int) -> int:
    value = os.environ.get(name, str(default))
    try:
        parsed = int(value)
    except ValueError:
        warning(f"Invalid {name} value '{value}', using {default}.")
        return default

    if parsed < 0:
        warning(f"Invalid {name} value '{value}', using {default}.")
        return default

    return parsed


def is_valid_timezone(value: str | None) -> bool:
    if not value:
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return

    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, payload: object, *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=indent)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
    fsync_directory(path.parent)


def load_settings() -> tuple[dict, bool]:
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded, True
        warning(f"{SETTINGS_FILE} is not a JSON object; using defaults.")
    except Exception as exc:
        warning(f"Failed to read {SETTINGS_FILE}: {exc}")

    return dict(DEFAULT_SETTINGS), False


def ensure_settings(uid: int, gid: int) -> bool:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o755)
    except OSError as exc:
        warning(f"Failed to chmod {CONFIG_DIR}: {exc}")

    if SETTINGS_FILE.exists():
        return False

    info("Settings file not found. Creating default settings.json")
    atomic_write_json(SETTINGS_FILE, DEFAULT_SETTINGS, indent=4)
    chown_path(SETTINGS_FILE, uid, gid)
    SETTINGS_FILE.chmod(0o640)
    info(f"Created default settings file at {SETTINGS_FILE}")
    return True


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes", "on"}


def cast_bootstrap_value(env_key: str, value: str, cast_type: type) -> object | None:
    if cast_type is bool:
        return parse_bool(value)
    if cast_type is int:
        try:
            return int(value)
        except ValueError:
            warning(f"Ignoring invalid integer for {env_key}.")
            return None
    return value


def canonical_dvr_id(host: str, port: int) -> str:
    stripped = host.strip("[]")
    normalized = stripped.lower() if ":" in stripped else stripped
    digest = hashlib.md5(f"{normalized}:{port}".encode("utf-8")).hexdigest()
    return "dvr_" + digest[:8]


def parse_dvr_entry(entry: str) -> dict | None:
    entry = entry.strip()
    if not entry:
        return None

    if "@" in entry:
        name, _, hostport = entry.rpartition("@")
    else:
        name, hostport = "", entry

    if hostport.startswith("[") and "]:" in hostport:
        host, _, port_text = hostport[1:].partition("]:")
    elif ":" in hostport:
        host, port_text = hostport.rsplit(":", 1)
    else:
        host, port_text = hostport, "8089"

    host = host.strip()
    if not host:
        return None

    try:
        port = int(port_text)
    except ValueError:
        warning(f"Ignoring invalid DVR port in CHANNELS_DVR_SERVERS entry '{entry}'.")
        port = 8089

    return {
        "id": canonical_dvr_id(host, port),
        "name": name.strip() or host,
        "host": host,
        "port": port,
        "enabled": True,
    }


def existing_servers_by_host_port(settings: dict) -> dict[tuple[str, int], dict]:
    servers = {}
    for server in settings.get("dvr_servers") or []:
        if not isinstance(server, dict):
            continue
        try:
            key = (str(server.get("host", "")), int(server.get("port", 8089)))
        except (TypeError, ValueError):
            continue
        servers[key] = server
    return servers


def merge_dvr_env(settings: dict) -> list[str]:
    changed_keys = []
    existing_by_hp = existing_servers_by_host_port(settings)
    servers_env = os.environ.get("CHANNELS_DVR_SERVERS", "")

    if servers_env:
        parsed = []
        for entry in servers_env.split(","):
            server = parse_dvr_entry(entry)
            if server is None:
                continue
            existing = existing_by_hp.get((server["host"], server["port"]))
            if existing:
                server["id"] = existing.get("id") or server["id"]
                server["overrides"] = existing.get("overrides", {})
                if existing.get("api_key"):
                    server["api_key"] = existing.get("api_key")
            parsed.append(server)

        if parsed:
            env_keys = {(server["host"], server["port"]) for server in parsed}
            for server in settings.get("dvr_servers") or []:
                if not isinstance(server, dict):
                    continue
                try:
                    key = (str(server.get("host", "")), int(server.get("port", 8089)))
                except (TypeError, ValueError):
                    continue
                if key not in env_keys:
                    parsed.append(server)
            settings["dvr_servers"] = parsed
            changed_keys.append("dvr_servers")
        return changed_keys

    host = os.environ.get("CHANNELS_DVR_HOST")
    if not host:
        return changed_keys

    try:
        port = int(os.environ.get("CHANNELS_DVR_PORT", "8089"))
    except ValueError:
        warning("Invalid CHANNELS_DVR_PORT value; using 8089.")
        port = 8089

    existing = existing_by_hp.get((host, port))
    dvr_name = os.environ.get("CHANNELS_DVR_NAME") or host
    server = {
        "id": existing.get("id") if existing else canonical_dvr_id(host, port),
        "name": dvr_name,
        "host": host,
        "port": port,
        "enabled": True,
        "overrides": existing.get("overrides", {}) if existing else {},
    }
    if os.environ.get("CHANNELS_DVR_NAME"):
        server["display_name"] = dvr_name
    if existing and existing.get("api_key"):
        server["api_key"] = existing.get("api_key")

    other_servers = [
        item
        for item in settings.get("dvr_servers") or []
        if isinstance(item, dict)
        and (str(item.get("host", "")), int(item.get("port", 8089))) != (host, port)
    ]
    settings["dvr_servers"] = [server, *other_servers]
    warning(
        "CHANNELS_DVR_HOST / CHANNELS_DVR_PORT are deprecated. "
        "Use CHANNELS_DVR_SERVERS, the UI settings page, or saved DVR settings."
    )
    changed_keys.append("dvr_servers")
    return changed_keys


def merge_bootstrap_env(settings_created: bool) -> None:
    settings, can_write = load_settings()
    changed_keys: list[str] = []

    if settings_created:
        for env_key, (settings_key, cast_type) in BOOTSTRAP_ENV_MAP.items():
            value = os.environ.get(env_key)
            if value is None:
                continue
            casted = cast_bootstrap_value(env_key, value, cast_type)
            if casted is None:
                continue
            settings[settings_key] = casted
            changed_keys.append(settings_key)

        changed_keys.extend(merge_dvr_env(settings))
    else:
        ignored = [
            key
            for key in [
                *BOOTSTRAP_ENV_MAP.keys(),
                "CHANNELS_DVR_SERVERS",
                "CHANNELS_DVR_HOST",
                "CHANNELS_DVR_PORT",
                "CHANNELS_DVR_NAME",
            ]
            if os.environ.get(key) is not None
        ]
        if ignored:
            info(
                "[Entrypoint] Bootstrap env ignored because settings already exist: "
                + ", ".join(sorted(ignored))
            )

    selected_tz = configure_timezone_value(settings, changed_keys)
    if changed_keys and can_write:
        settings["_version"] = max(int(settings.get("_version") or 0), CURRENT_SCHEMA_VERSION)
        atomic_write_json(SETTINGS_FILE, settings, indent=2)
        info(
            f"[Entrypoint] Merged {len(changed_keys)} env var(s) into settings: "
            + ", ".join(dict.fromkeys(changed_keys))
        )
        atomic_write_json(CONFIG_DIR / "env_overrides.json", list(dict.fromkeys(changed_keys)), indent=None)

    os.environ["TZ"] = selected_tz
    info(f"Setting timezone to: {selected_tz}")


def configure_timezone_value(settings: dict, changed_keys: list[str]) -> str:
    docker_tz = (os.environ.get("TZ") or "").strip()
    configured_tz = str(settings.get("tz") or "").strip()
    selected_tz = configured_tz or APP_DEFAULT_TZ

    if docker_tz:
        if is_valid_timezone(docker_tz):
            selected_tz = docker_tz
            if settings.get("tz") != selected_tz:
                settings["tz"] = selected_tz
                changed_keys.append("tz")
            os.environ["CHANNELWATCH_TZ_OVERRIDE"] = selected_tz
        else:
            warning(f"Invalid TZ environment variable '{docker_tz}', using configured timezone.")
    elif not configured_tz:
        selected_tz = APP_DEFAULT_TZ
        settings["tz"] = selected_tz
        changed_keys.append("tz")

    if not is_valid_timezone(selected_tz):
        warning(f"Invalid configured timezone '{selected_tz}', using UTC.")
        selected_tz = "UTC"
        settings["tz"] = selected_tz
        changed_keys.append("tz")

    return selected_tz


def chmod_config_tree(path: Path) -> None:
    if not path.exists():
        return

    for current_root, directories, files in os.walk(path):
        root_path = Path(current_root)
        try:
            root_path.chmod(0o750)
        except OSError as exc:
            warning(f"Failed to chmod {root_path}: {exc}")

        for directory in directories:
            try:
                (root_path / directory).chmod(0o750)
            except OSError as exc:
                warning(f"Failed to chmod {root_path / directory}: {exc}")

        for filename in files:
            file_path = root_path / filename
            mode = 0o600 if filename == "encryption.key" else 0o640
            try:
                file_path.chmod(mode)
            except OSError as exc:
                warning(f"Failed to chmod {file_path}: {exc}")


def chown_tree(path: Path, uid: int, gid: int) -> None:
    if not path.exists():
        return

    for current_root, directories, files in os.walk(path):
        root_path = Path(current_root)
        for target in [root_path, *(root_path / item for item in directories), *(root_path / item for item in files)]:
            chown_path(target, uid, gid)


def render_supervisor_config(app_uid: int, app_gid: int) -> None:
    if not SUPERVISOR_TEMPLATE.is_file():
        warning(f"supervisord.conf.template not found at {SUPERVISOR_TEMPLATE}")
        return

    SUPERVISOR_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        SUPERVISOR_SOCKET.unlink()
    except FileNotFoundError:
        pass

    template = SUPERVISOR_TEMPLATE.read_text(encoding="utf-8")
    rendered = template.replace("__SUPERVISOR_SOCKET__", str(SUPERVISOR_SOCKET))
    SUPERVISOR_CONF.write_text(rendered, encoding="utf-8")

    for path, mode in (
        (SUPERVISOR_RUNTIME_DIR, 0o700),
        (SUPERVISOR_CONF, 0o640),
    ):
        chown_path(path, app_uid, app_gid)
        try:
            path.chmod(mode)
        except OSError as exc:
            warning(f"Failed to chmod {path}: {exc}")

    info("Generated supervisord config with local Unix socket control")


def drop_privileges(uid: int, gid: int) -> None:
    if not running_as_root():
        return

    try:
        os.setgroups([])
    except OSError as exc:
        warning(f"Failed to clear supplemental groups: {exc}")

    try:
        os.setgid(gid)
        os.setuid(uid)
    except OSError as exc:
        warning(f"Failed to drop privileges to {uid}:{gid}: {exc}")


def prepare_standard_streams() -> None:
    if not running_as_root():
        return

    for fd in (1, 2):
        try:
            os.chmod(f"/proc/self/fd/{fd}", 0o666)
        except OSError as exc:
            warning(f"Failed to chmod fd {fd}: {exc}")


def main() -> None:
    uid = parse_id("PUID", 1000)
    gid = parse_id("PGID", 1000)

    settings_created = ensure_settings(uid, gid)
    merge_bootstrap_env(settings_created)
    if not running_as_root():
        info("[Entrypoint] Running without root privileges; ownership repair is skipped.")
    chown_tree(CONFIG_DIR, uid, gid)
    chown_tree(Path("/app"), uid, gid)
    chmod_config_tree(CONFIG_DIR)
    render_supervisor_config(uid, gid)
    prepare_standard_streams()

    if len(sys.argv) < 2:
        warning("No command provided for ChannelWatch startup.")
        sys.exit(1)

    drop_privileges(uid, gid)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
