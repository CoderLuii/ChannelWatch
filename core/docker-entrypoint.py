"""Container startup for ChannelWatch."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONFIG_DIR = Path("/config")
SETTINGS_FILE = CONFIG_DIR / "settings.json"
APP_DEFAULT_TZ = "America/Los_Angeles"

DEFAULT_SETTINGS = {
    "channels_dvr_host": None,
    "channels_dvr_port": 8089,
    "tz": APP_DEFAULT_TZ,
    "log_level": 1,
    "log_retention_days": 7,
    "alert_channel_watching": True,
    "alert_vod_watching": True,
    "alert_disk_space": True,
    "alert_recording_events": True,
    "stream_count": True,
    "cw_channel_name": True,
    "cw_channel_number": True,
    "cw_program_name": True,
    "cw_device_name": True,
    "cw_device_ip": True,
    "cw_stream_source": True,
    "cw_image_source": "PROGRAM",
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
    "pushover_user_key": "",
    "pushover_api_token": "",
    "apprise_discord": "",
    "apprise_email": "",
    "apprise_email_to": "",
    "apprise_telegram": "",
    "apprise_slack": "",
    "apprise_gotify": "",
    "apprise_matrix": "",
    "apprise_custom": "",
}


def warning(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


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


def write_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")


def chmod_tree(path: Path, file_mode: int, directory_mode: int) -> None:
    if not path.exists():
        return

    for current_root, directories, files in os.walk(path):
        root_path = Path(current_root)
        try:
            root_path.chmod(directory_mode)
        except OSError as exc:
            warning(f"Failed to chmod {root_path}: {exc}")

        for directory in directories:
            try:
                (root_path / directory).chmod(directory_mode)
            except OSError as exc:
                warning(f"Failed to chmod {root_path / directory}: {exc}")

        for filename in files:
            try:
                (root_path / filename).chmod(file_mode)
            except OSError as exc:
                warning(f"Failed to chmod {root_path / filename}: {exc}")


def chown_tree(path: Path, uid: int, gid: int) -> None:
    if not path.exists():
        return

    for current_root, directories, files in os.walk(path):
        root_path = Path(current_root)
        for target in [root_path, *(root_path / item for item in directories), *(root_path / item for item in files)]:
            try:
                os.chown(target, uid, gid)
            except OSError as exc:
                warning(f"Failed to chown {target}: {exc}")


def ensure_settings() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o755)

    if SETTINGS_FILE.exists():
        return

    print("Settings file not found. Creating default settings.json")
    write_settings(DEFAULT_SETTINGS)
    SETTINGS_FILE.chmod(0o644)
    print(f"Created default settings file at {SETTINGS_FILE}")


def load_settings() -> tuple[dict, bool]:
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded, True
        warning(f"{SETTINGS_FILE} is not a JSON object; using default timezone.")
    except Exception as exc:
        warning(f"Failed to read timezone from {SETTINGS_FILE}: {exc}")

    return {}, False


def configure_timezone() -> str:
    docker_tz = (os.environ.get("TZ") or "").strip()
    data, can_write = load_settings()
    configured_tz = str(data.get("tz") or "").strip()
    selected_tz = configured_tz or APP_DEFAULT_TZ
    should_write = False

    if docker_tz:
        if is_valid_timezone(docker_tz):
            selected_tz = docker_tz
            if data.get("tz") != selected_tz:
                data["tz"] = selected_tz
                should_write = True
        else:
            warning(f"Invalid TZ environment variable '{docker_tz}', using configured timezone.")
    elif not configured_tz:
        data["tz"] = selected_tz
        should_write = True

    if not is_valid_timezone(selected_tz):
        warning(f"Invalid configured timezone '{selected_tz}', using UTC.")
        selected_tz = "UTC"
        data["tz"] = selected_tz
        should_write = True

    if should_write and can_write:
        write_settings(data)

    os.environ["TZ"] = selected_tz
    if docker_tz and docker_tz == selected_tz:
        os.environ["CHANNELWATCH_TZ_OVERRIDE"] = docker_tz

    print(f"Setting timezone to: {selected_tz}")
    return selected_tz


def drop_privileges(uid: int, gid: int) -> None:
    if os.geteuid() != 0:
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
    for fd in (1, 2):
        try:
            os.chmod(f"/proc/self/fd/{fd}", 0o666)
        except OSError as exc:
            warning(f"Failed to chmod fd {fd}: {exc}")


def main() -> None:
    uid = parse_id("PUID", 1000)
    gid = parse_id("PGID", 1000)

    ensure_settings()
    chown_tree(CONFIG_DIR, uid, gid)
    chown_tree(Path("/app"), uid, gid)
    chmod_tree(CONFIG_DIR, 0o644, 0o755)
    configure_timezone()

    try:
        os.chown(SETTINGS_FILE, uid, gid)
        SETTINGS_FILE.chmod(0o644)
    except OSError as exc:
        warning(f"Failed to update {SETTINGS_FILE} permissions: {exc}")

    prepare_standard_streams()

    if len(sys.argv) < 2:
        warning("No command provided for ChannelWatch startup.")
        sys.exit(1)

    drop_privileges(uid, gid)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
