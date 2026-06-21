import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from core.dvr_client import check_version_compatibility
from core.helpers import config as core_config
from core.helpers.atomic_io import atomic_write_bytes, atomic_write_json
from core.helpers.config import ConfigLoadError, CoreSettings
from core.helpers.dvr_connection import build_dvr_base_url
from core.helpers.encryption import (
    ENCRYPTION_KEY_FILE,
    encrypt_value,
    is_fernet_encrypted,
)
from core.helpers.initialize import check_server_connectivity
from ui.backend import config as ui_config


def _config_dir() -> Path:
    return Path(os.getenv("CONFIG_PATH", "/config"))


def _settings_file() -> Path:
    return core_config.CONFIG_FILE


def _key_file() -> Path:
    return core_config.CONFIG_DIR / ENCRYPTION_KEY_FILE.name


def _base_url(server: dict[str, Any]) -> str:
    return build_dvr_base_url(server.get("host", ""), server.get("port", 8089))


def _server_label(server: dict[str, Any]) -> str:
    name = server.get("name") or server.get("host") or server.get("id") or "Unknown DVR"
    host = server.get("host", "")
    port = server.get("port", 8089)
    return f"{name} ({host}:{port})"


def _read_settings_json() -> dict[str, Any]:
    settings_file = _settings_file()
    if not settings_file.is_file():
        return {}
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigLoadError(
            core_config._build_recovery_message(
                settings_file,
                f"expected a JSON object but found {type(data).__name__}",
            )
        )
    return data


def _load_core_settings() -> CoreSettings:
    CoreSettings._instance = None
    return CoreSettings()


def _hint_for_connectivity(server: dict[str, Any]) -> str:
    host = str(server.get("host", ""))
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return (
            "Hint: inside Docker, localhost points at the container. "
            "Use the DVR's LAN IP or host.docker.internal instead."
        )
    return (
        "Hint: verify the DVR host/port in settings, confirm the DVR is online, "
        "and make sure firewall rules allow access from the ChannelWatch container."
    )


def _diagnose_auth(server: dict[str, Any]) -> tuple[bool | None, str]:
    headers = {}
    api_key = server.get("api_key") or ""
    if api_key:
        headers["X-API-Key"] = str(api_key)

    response = httpx.get(
        f"{_base_url(server)}/api/v1/channels", headers=headers, timeout=5
    )
    if response.status_code == 200:
        if api_key:
            return True, "Auth check passed using the configured DVR API key."
        return True, "API endpoint is reachable without a DVR API key."

    if response.status_code in (401, 403):
        if api_key:
            return (
                False,
                "Auth failed. Hint: re-enter the DVR API key in Settings and then rerun `channelwatch doctor diagnose`.",
            )
        return (
            False,
            "Auth is required. Hint: add this DVR's API key in Settings and rerun `channelwatch doctor diagnose`.",
        )

    return (
        None,
        f"Auth check returned HTTP {response.status_code}. Hint: verify the DVR API is healthy and reachable from ChannelWatch.",
    )


def _cmd_debug_bundle(args: argparse.Namespace) -> int:
    from core.helpers.debug_bundle import create_debug_bundle

    config_dir = _config_dir()
    out_path = Path(args.output)
    out_path.write_bytes(create_debug_bundle(config_dir))
    print(f"Debug bundle written to {out_path}")
    return 0


def _cmd_config_check(args: argparse.Namespace) -> int:
    del args
    try:
        core_settings = _load_core_settings()
        ui_settings = ui_config.load_settings()
    except ConfigLoadError as exc:
        print(f"Config check failed: {exc}")
        print(
            "Hint: restore /config/settings.json from /config/backups or repair the file, then rerun this command."
        )
        return 1

    print(
        "Config check passed: "
        f"core loader accepted {len(core_settings.get_dvr_connections())} DVR(s) and "
        f"UI schema accepted {len(ui_settings.dvr_servers)} DVR(s)."
    )
    return 0


def _cmd_diagnose(args: argparse.Namespace) -> int:
    del args
    try:
        settings = _load_core_settings()
    except ConfigLoadError as exc:
        print(f"Diagnose aborted: {exc}")
        print(
            "Hint: run `channelwatch doctor config-check` after repairing /config/settings.json."
        )
        return 1

    servers = [
        server
        for server in (settings.dvr_servers or [])
        if isinstance(server, dict) and not server.get("deleted_at")
    ]
    if not servers:
        print("No DVR servers are configured.")
        print(
            "Hint: add at least one DVR in Settings before running `channelwatch doctor diagnose`."
        )
        return 1

    failures = 0
    for server in servers:
        label = _server_label(server)
        print(f"Checking {label}...")

        if not check_server_connectivity(
            str(server.get("host", "")), int(server.get("port", 8089))
        ):
            failures += 1
            print(f"  FAIL connectivity: {_hint_for_connectivity(server)}")
            continue

        try:
            status_response = httpx.get(f"{_base_url(server)}/status", timeout=5)
            status_response.raise_for_status()
            status_payload = status_response.json()
            version = str(status_payload.get("version", "Unknown"))
        except Exception as exc:
            failures += 1
            print(
                f"  FAIL status: could not read /status after connectivity passed ({exc})."
            )
            print("  Hint: verify the DVR web API responds with JSON at /status.")
            continue

        compat = check_version_compatibility(version)
        if compat["warning"]:
            failures += 1
            print(f"  FAIL version: {compat['warning']}")
            print(
                "  Hint: upgrade or downgrade the DVR to a tested release before relying on ChannelWatch automation."
            )
        else:
            print(f"  OK version: {version}")

        try:
            auth_ok, auth_message = _diagnose_auth(server)
        except Exception as exc:
            failures += 1
            print(f"  FAIL auth: unable to verify DVR API access ({exc}).")
            print(
                "  Hint: confirm the DVR HTTP API is reachable and the configured API key is current."
            )
            continue

        if auth_ok is False:
            failures += 1
            print(f"  FAIL auth: {auth_message}")
        elif auth_ok is None:
            print(f"  WARN auth: {auth_message}")
        else:
            print(f"  OK auth: {auth_message}")

    if failures:
        print(f"Diagnosis completed with {failures} issue(s).")
        return 1

    print(f"Diagnosis completed successfully for {len(servers)} DVR(s).")
    return 0


def _cmd_rotate_encryption_key(args: argparse.Namespace) -> int:
    del args
    try:
        settings = _load_core_settings()
        persisted = _read_settings_json()
    except ConfigLoadError as exc:
        print(f"Encryption key rotation failed: {exc}")
        print("Hint: repair /config/settings.json first, then rerun this command.")
        return 1

    key_file = _key_file()
    key_file.parent.mkdir(parents=True, exist_ok=True)

    new_key = os.urandom(32)
    updated_servers = []
    rotated_count = 0
    for server in settings.dvr_servers or []:
        if not isinstance(server, dict):
            updated_servers.append(server)
            continue

        server_copy = dict(server)
        api_key = server_copy.get("api_key") or ""
        if api_key:
            server_copy["api_key"] = encrypt_value(str(api_key), new_key)
            rotated_count += 1
        elif is_fernet_encrypted(str(server_copy.get("api_key", ""))):
            server_copy["api_key"] = encrypt_value(str(server_copy["api_key"]), new_key)
            rotated_count += 1
        updated_servers.append(server_copy)

    persisted["dvr_servers"] = updated_servers

    backup_file = key_file.with_suffix(f"{key_file.suffix}.bak")
    if key_file.exists():
        atomic_write_bytes(backup_file, key_file.read_bytes())

    try:
        atomic_write_bytes(key_file, new_key)
        key_file.chmod(0o600)
        atomic_write_json(_settings_file(), persisted)
    except Exception:
        if backup_file.exists():
            atomic_write_bytes(key_file, backup_file.read_bytes())
            key_file.chmod(0o600)
        raise

    print(
        f"Encryption key rotated successfully. Re-encrypted {rotated_count} DVR API key(s)."
    )
    if backup_file.exists():
        print(f"Recovery backup kept at {backup_file}")
    return 0


def _cmd_reset_admin_password(args: argparse.Namespace) -> int:
    try:
        settings = _load_core_settings()
    except ConfigLoadError as exc:
        print(f"Password reset failed: {exc}")
        return 1

    settings_for_auth: Any = settings
    from ui.backend.main import _effective_auth_mode, _setup_required

    if _setup_required(settings_for_auth):
        print(
            "Password reset unavailable because secure login setup has not been completed. "
            "Open the ChannelWatch web UI and finish secure-login setup from the Security page."
        )
        return 1

    if _effective_auth_mode(settings_for_auth) != "rbac":
        print(
            "Password reset unavailable because this install is not using RBAC login. "
            "Open the ChannelWatch web UI and finish secure-login setup from the Security page."
        )
        return 1

    try:
        from ui.backend.main import _ensure_auth_tables as _eat

        engine = _eat()
    except Exception:
        engine = None
    if engine is None:
        print("Password reset failed: auth database unavailable")
        return 1

    from core.storage.auth import reset_password

    password = args.password or os.urandom(12).hex()
    if not reset_password(engine, args.username, password):
        print(f"No user found with username {args.username!r}.")
        return 1

    print(f"Password reset successful for {args.username}.")
    print(f"Temporary password: {password}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="channelwatch doctor",
        description="ChannelWatch diagnostics and health checks.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    diagnose_parser = sub.add_parser(
        "diagnose", help="Validate DVR connectivity, auth, and version support"
    )
    diagnose_parser.set_defaults(handler=_cmd_diagnose)

    config_parser = sub.add_parser(
        "config-check", help="Dry-run settings validation against real loaders"
    )
    config_parser.set_defaults(handler=_cmd_config_check)

    rotate_parser = sub.add_parser(
        "rotate-encryption-key", help="Generate a new key and re-encrypt DVR API keys"
    )
    rotate_parser.set_defaults(handler=_cmd_rotate_encryption_key)

    reset_parser = sub.add_parser(
        "reset-admin-password", help="Reset an admin password for RBAC login recovery"
    )
    reset_parser.add_argument(
        "--username", required=True, metavar="USERNAME", help="Admin username to reset"
    )
    reset_parser.add_argument(
        "--password",
        default="",
        metavar="PASSWORD",
        help="Optional new password; omit to generate one",
    )
    reset_parser.set_defaults(handler=_cmd_reset_admin_password)

    debug_parser = sub.add_parser("debug", help="Debug utilities")
    debug_sub = debug_parser.add_subparsers(dest="debug_command", metavar="SUBCOMMAND")

    bundle_parser = debug_sub.add_parser(
        "bundle",
        help="Generate a sanitized debug bundle zip.",
    )
    bundle_parser.add_argument(
        "--output",
        default="channelwatch_debug_bundle.zip",
        metavar="PATH",
        help="Output path (default: channelwatch_debug_bundle.zip)",
    )
    bundle_parser.set_defaults(handler=_cmd_debug_bundle)

    return parser


def run(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)

    if handler is None:
        if args.command == "debug":
            parser.parse_args(["debug", "--help"])
            return
        parser.print_help()
        sys.exit(1)

    exit_code = int(handler(args) or 0)
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    run()
