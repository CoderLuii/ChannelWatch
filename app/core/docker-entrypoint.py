"""Container startup for ChannelWatch."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import stat as stat_module
import sys
import uuid
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
IMAGE_APP_DIR = Path(os.environ.get("CHANNELWATCH_IMAGE_APP_DIR", "/app"))
RUNTIME_ABI = "channelwatch-runtime-v1"
CHANNELWATCH_RUNTIME_DIR = CONFIG_DIR / "channelwatch-runtime"
RUNTIME_PROCESS_UMASK = 0o027
VIRTUALIZED_OWNERSHIP_FILESYSTEMS = {"virtiofs"}
RESTART_REQUIRED_FILE = "restart-required.json"
RESTART_REQUIRED_PATH = CHANNELWATCH_RUNTIME_DIR / RESTART_REQUIRED_FILE
RESTART_JOURNAL_LOCK_FILE = "restart-required.lock"
RESTART_JOURNAL_LOCK_PATH = CHANNELWATCH_RUNTIME_DIR / RESTART_JOURNAL_LOCK_FILE
ACTIVATION_OUTCOME_LOCK_FILE = "activation-outcome.lock"
RESTART_JOURNAL_SCHEMA = 2
RESTART_JOURNAL_KEYS = {
    "schema",
    "reason",
    "operation",
    "phase",
    "job_id",
    "source_active",
    "replace_activation_state",
    "created_at",
    "control",
}
RESTART_CONTROL_FILES = (
    "active.json",
    "rollback.json",
    "activation-pending.json",
    "activation-core-ready.json",
    "activation-ui-ready.json",
    "update-job.json",
)

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


def filesystem_type_for_path(
    path: Path, *, mountinfo_text: str | None = None
) -> str | None:
    """Return the filesystem type for the deepest Linux mount containing path."""

    if mountinfo_text is None:
        try:
            mountinfo_text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError:
            return None

    def decode_mount_path(value: str) -> str:
        for encoded, decoded in (
            ("\\040", " "),
            ("\\011", "\t"),
            ("\\012", "\n"),
            ("\\134", "\\"),
        ):
            value = value.replace(encoded, decoded)
        return value

    absolute_path = os.path.abspath(path)
    best_match: tuple[int, str] | None = None
    for line in mountinfo_text.splitlines():
        try:
            left, right = line.split(" - ", 1)
            mount_point = decode_mount_path(left.split()[4])
            filesystem_type = right.split()[0]
        except (IndexError, ValueError):
            continue
        normalized_mount = os.path.normpath(mount_point)
        if absolute_path != normalized_mount and not absolute_path.startswith(
            normalized_mount.rstrip("/") + "/"
        ):
            continue
        candidate = (len(normalized_mount), filesystem_type)
        if best_match is None or candidate[0] > best_match[0]:
            best_match = candidate
    return best_match[1] if best_match is not None else None


def ownership_metadata_is_virtualized(path: Path) -> bool:
    """Identify filesystems that report the caller as owner on shared mounts."""

    return filesystem_type_for_path(path) in VIRTUALIZED_OWNERSHIP_FILESYSTEMS


def chown_path(path: Path, uid: int, gid: int) -> bool:
    try:
        metadata = path.lstat()
    except OSError as exc:
        warning(f"Failed to inspect {path} before ownership repair: {exc}")
        return False
    if stat_module.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Refusing to follow symbolic link during ownership repair: {path}")
    if stat_module.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
        raise RuntimeError(
            f"Refusing hard-linked regular file during ownership repair: {path}"
        )
    if not hasattr(os, "chown") or not running_as_root():
        return True
    try:
        os.chown(path, uid, gid, follow_symlinks=False)
    except OSError as exc:
        warning(f"Failed to chown {path}: {exc}")
        return False
    try:
        metadata = path.lstat()
    except OSError as exc:
        warning(f"Failed to verify ownership of {path}: {exc}")
        return False
    if metadata.st_uid != uid or metadata.st_gid != gid:
        if ownership_metadata_is_virtualized(path):
            warning(
                f"Ownership metadata for {path} is virtualized by "
                f"{filesystem_type_for_path(path)}; deferring enforcement to "
                "the target-identity write-access check."
            )
            return True
        warning(
            f"Failed to verify ownership of {path}: expected {uid}:{gid}, "
            f"got {metadata.st_uid}:{metadata.st_gid}"
        )
        return False
    return True


def _require_path_kind(path: Path, *, kind: str, purpose: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"Could not inspect {purpose} path {path}: {exc}") from exc
    expected = {
        "directory": stat_module.S_ISDIR,
        "regular": stat_module.S_ISREG,
        "socket": stat_module.S_ISSOCK,
    }[kind]
    if stat_module.S_ISLNK(metadata.st_mode) or not expected(metadata.st_mode):
        raise RuntimeError(f"Unsafe {purpose} path is not a real {kind}: {path}")
    if kind == "regular" and metadata.st_nlink != 1:
        raise RuntimeError(f"Unsafe hard-linked {purpose} path: {path}")
    return metadata


def _chmod_path_no_follow(path: Path, mode: int, *, purpose: str) -> None:
    metadata = path.lstat()
    if stat_module.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Refusing to chmod symbolic link for {purpose}: {path}")
    if stat_module.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
        raise RuntimeError(f"Refusing to chmod hard-linked file for {purpose}: {path}")
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(f"Failed to chmod {purpose} path {path}: {exc}") from exc
    actual = path.lstat()
    if stat_module.S_IMODE(actual.st_mode) != mode:
        raise RuntimeError(
            f"Failed to verify mode {mode:o} on {purpose} path {path}"
        )


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


def validate_runtime_identity(uid: int, gid: int) -> None:
    """Reject an identity that would leave any root authority behind."""

    if uid == 0 or gid == 0:
        raise RuntimeError(
            "PUID and PGID must both be greater than zero; "
            f"refusing root runtime identity {uid}:{gid}."
        )


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


def _open_real_directory(path: Path, *, purpose: str) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        directory_fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"Unsafe or inaccessible {purpose} directory {path}: {exc}") from exc
    metadata = os.fstat(directory_fd)
    if not stat_module.S_ISDIR(metadata.st_mode):
        os.close(directory_fd)
        raise RuntimeError(f"Unsafe non-directory {purpose} path: {path}")
    return directory_fd


def _ensure_real_directory(path: Path, *, mode: int, purpose: str) -> None:
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        pass
    except OSError as exc:
        raise RuntimeError(f"Could not create {purpose} directory {path}: {exc}") from exc
    directory_fd = _open_real_directory(path, purpose=purpose)
    os.close(directory_fd)


@contextmanager
def restart_transition_lock():
    """Serialize journal writers/replayers on one stable, never-unlinked inode."""

    _ensure_real_directory(
        CHANNELWATCH_RUNTIME_DIR,
        mode=0o750,
        purpose="runtime transition",
    )
    parent_fd = _open_real_directory(
        CHANNELWATCH_RUNTIME_DIR,
        purpose="runtime transition",
    )
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            lock_fd = os.open(
                RESTART_JOURNAL_LOCK_FILE,
                flags,
                0o600,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Could not open runtime transition lock safely: {exc}"
            ) from exc
        metadata = os.fstat(lock_fd)
        if not stat_module.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            os.close(lock_fd)
            raise RuntimeError(
                "Runtime transition lock is not a single-link regular file."
            )
        lock_acquired = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            lock_acquired = True
            yield
        finally:
            if lock_acquired:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    finally:
        os.close(parent_fd)


def atomic_write_json(path: Path, payload: object, *, indent: int | None = 2) -> None:
    content = json.dumps(payload, indent=indent) + "\n"
    atomic_write_text(path, content)


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace a regular file without following pre-planted links."""

    parent_fd = _open_real_directory(path.parent, purpose="atomic-write parent")
    temp_name: str | None = None
    try:
        try:
            existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        except OSError as exc:
            raise RuntimeError(f"Could not inspect atomic-write target {path}: {exc}") from exc
        if existing is not None and not stat_module.S_ISREG(existing.st_mode):
            raise RuntimeError(
                f"Refusing to replace non-regular atomic-write target: {path}"
            )
        if existing is not None and existing.st_nlink != 1:
            raise RuntimeError(
                f"Refusing to replace hard-linked atomic-write target: {path}"
            )

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_fd: int | None = None
        for _attempt in range(16):
            candidate = f".{path.name}.tmp-{uuid.uuid4().hex}"
            try:
                file_fd = os.open(candidate, flags, 0o600, dir_fd=parent_fd)
            except FileExistsError:
                continue
            except OSError as exc:
                raise RuntimeError(f"Could not create secure temporary file for {path}: {exc}") from exc
            temp_name = candidate
            break
        if file_fd is None or temp_name is None:
            raise RuntimeError(f"Could not allocate a unique temporary file for {path}")

        opened_metadata: os.stat_result
        with os.fdopen(file_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            opened_metadata = os.fstat(handle.fileno())
            if opened_metadata.st_nlink != 1:
                raise RuntimeError(
                    f"Secure temporary file for {path} acquired a hard link"
                )

        try:
            named_metadata = os.stat(
                temp_name, dir_fd=parent_fd, follow_symlinks=False
            )
        except OSError as exc:
            raise RuntimeError(f"Secure temporary file for {path} changed before replace: {exc}") from exc
        if (
            not stat_module.S_ISREG(named_metadata.st_mode)
            or named_metadata.st_nlink != 1
            or (named_metadata.st_dev, named_metadata.st_ino)
            != (opened_metadata.st_dev, opened_metadata.st_ino)
        ):
            raise RuntimeError(f"Secure temporary file for {path} changed before replace")

        os.replace(
            temp_name,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        # Dirfd-safe publication is the hardened equivalent of
        # os.replace(temp_path, path) without resolving attacker-owned links.
        temp_name = None
        published = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat_module.S_ISREG(published.st_mode)
            or published.st_nlink != 1
            or (published.st_dev, published.st_ino)
            != (opened_metadata.st_dev, opened_metadata.st_ino)
        ):
            raise RuntimeError(f"Atomic-write target {path} was not published safely")
        os.fsync(parent_fd)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def load_restart_required_journal() -> dict | None:
    """Load and strictly validate the image-stable runtime transition journal."""

    parent_fd = _open_real_directory(
        RESTART_REQUIRED_PATH.parent,
        purpose="runtime transition",
    )
    journal_fd: int | None = None
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        try:
            listed = os.stat(
                RESTART_REQUIRED_PATH.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeError(
                f"Could not inspect runtime transition journal: {exc}"
            ) from exc
        if not stat_module.S_ISREG(listed.st_mode):
            raise RuntimeError(
                "Runtime transition journal exists but is not a regular file."
            )
        if listed.st_nlink != 1:
            raise RuntimeError("Runtime transition journal is hard-linked.")
        try:
            journal_fd = os.open(
                RESTART_REQUIRED_PATH.name,
                flags,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeError(
                f"Could not open runtime transition journal safely: {exc}"
            ) from exc
        before = os.fstat(journal_fd)
        if not stat_module.S_ISREG(before.st_mode):
            raise RuntimeError(
                "Runtime transition journal exists but is not a regular file."
            )
        if before.st_nlink != 1:
            raise RuntimeError("Runtime transition journal is hard-linked.")
        try:
            with os.fdopen(journal_fd, "r", encoding="utf-8") as handle:
                journal_fd = None
                content = handle.read()
                after = os.fstat(handle.fileno())
            journal = json.loads(content)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Runtime transition journal is unreadable or malformed: {exc}"
            ) from exc
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_nlink,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_nlink,
        ):
            raise RuntimeError(
                "Runtime transition journal changed while it was being read."
            )
        named = os.stat(
            RESTART_REQUIRED_PATH.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat_module.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise RuntimeError(
                "Runtime transition journal changed while it was being read."
            )
    finally:
        if journal_fd is not None:
            os.close(journal_fd)
        os.close(parent_fd)
    if not isinstance(journal, dict) or set(journal) != RESTART_JOURNAL_KEYS:
        raise RuntimeError("Runtime transition journal has an invalid structure.")
    if journal.get("schema") != RESTART_JOURNAL_SCHEMA:
        raise RuntimeError("Runtime transition journal has an unsupported schema.")

    operation = journal.get("operation")
    reason = journal.get("reason")
    if operation not in {"apply", "manual_rollback", "activation_rollback"}:
        raise RuntimeError("Runtime transition journal has an invalid operation.")
    expected_reason = (
        "activation_rollback"
        if operation == "activation_rollback"
        else "runtime_transition"
    )
    if reason != expected_reason or journal.get("phase") not in {"commit", "abort"}:
        raise RuntimeError("Runtime transition journal has an invalid phase or reason.")
    if journal.get("job_id") is not None and not isinstance(
        journal.get("job_id"), str
    ):
        raise RuntimeError("Runtime transition journal has an invalid job ID.")
    if journal.get("source_active") is not None and not isinstance(
        journal.get("source_active"), dict
    ):
        raise RuntimeError("Runtime transition journal has an invalid source selection.")
    if journal.get("replace_activation_state") is not True:
        raise RuntimeError(
            "Runtime transition journal must replace activation state atomically."
        )
    if not isinstance(journal.get("created_at"), str) or not journal["created_at"]:
        raise RuntimeError("Runtime transition journal has an invalid timestamp.")

    control = journal.get("control")
    if not isinstance(control, dict) or set(control) != set(RESTART_CONTROL_FILES):
        raise RuntimeError("Runtime transition journal has an invalid control set.")
    if any(value is not None and not isinstance(value, dict) for value in control.values()):
        raise RuntimeError("Runtime transition journal has an invalid control value.")
    return journal


def _unlink_control_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _cleanup_restart_journal_candidates() -> None:
    """Remove only regular internal candidates abandoned by a dead writer."""

    removed = False
    for candidate in CHANNELWATCH_RUNTIME_DIR.glob(
        f".{RESTART_REQUIRED_FILE}.candidate-*"
    ):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if not stat_module.S_ISREG(metadata.st_mode):
            continue
        try:
            candidate.unlink()
            removed = True
        except FileNotFoundError:
            pass
    if removed:
        fsync_directory(CHANNELWATCH_RUNTIME_DIR)


def cleanup_restart_journal_candidates_before_validation() -> None:
    """Remove narrowly named writer leftovers before the hard-link gate.

    Initial journal publication temporarily creates a same-inode candidate and
    canonical name. A power loss between link publication and candidate cleanup
    can therefore leave both names durable. Unlinking only the internal
    candidate name is safe: it never reads, chmods, chowns, or follows the inode,
    and it restores the canonical journal to the required single-link state.
    """

    try:
        runtime_fd = _open_real_directory(
            CHANNELWATCH_RUNTIME_DIR,
            purpose="runtime transition preflight",
        )
    except RuntimeError:
        try:
            CHANNELWATCH_RUNTIME_DIR.lstat()
        except FileNotFoundError:
            return
        raise

    removed = False
    prefix = f".{RESTART_REQUIRED_FILE}.candidate-"
    try:
        with os.scandir(runtime_fd) as iterator:
            names = sorted(
                entry.name for entry in iterator if entry.name.startswith(prefix)
            )
        for name in names:
            try:
                metadata = os.stat(name, dir_fd=runtime_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat_module.S_ISREG(metadata.st_mode):
                continue
            try:
                os.unlink(name, dir_fd=runtime_fd)
                removed = True
            except FileNotFoundError:
                pass
        if removed:
            os.fsync(runtime_fd)
    finally:
        os.close(runtime_fd)


def replay_restart_required_journal() -> dict | None:
    """Idempotently finish a write-ahead runtime transition before selection."""

    try:
        RESTART_REQUIRED_PATH.lstat()
    except FileNotFoundError:
        # A journal created immediately after this observation remains present;
        # the image-stable launchers will see it and block child launch. Orphaned
        # candidates are also removed by the next journal writer.
        return None
    except OSError as exc:
        raise RuntimeError(
            f"Could not inspect runtime transition journal: {exc}"
        ) from exc
    with restart_transition_lock():
        _cleanup_restart_journal_candidates()
        journal = load_restart_required_journal()
        if journal is None:
            return None
        if journal["replace_activation_state"]:
            for path in CHANNELWATCH_RUNTIME_DIR.glob("activation-*.json"):
                _unlink_control_file(path)

        control = journal["control"]
        # The active selection is authoritative only after all validation,
        # activation, rollback, and job records for the transition are durable.
        for name in (
            "activation-pending.json",
            "activation-core-ready.json",
            "activation-ui-ready.json",
            "rollback.json",
            "update-job.json",
            "active.json",
        ):
            path = CHANNELWATCH_RUNTIME_DIR / name
            value = control[name]
            if value is None:
                _unlink_control_file(path)
            else:
                atomic_write_json(path, value)
        fsync_directory(CHANNELWATCH_RUNTIME_DIR)
        return journal


def load_settings() -> tuple[dict, bool]:
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, dict):
            return loaded, True
        warning(f"{SETTINGS_FILE} is not a JSON object; using defaults.")
    except Exception as exc:
        warning(f"Failed to read {SETTINGS_FILE}: {exc}")

    return dict(DEFAULT_SETTINGS), False


def ensure_settings(uid: int, gid: int) -> bool:
    _ensure_real_directory(CONFIG_DIR, mode=0o755, purpose="configuration")
    try:
        settings_metadata = SETTINGS_FILE.lstat()
    except FileNotFoundError:
        settings_metadata = None
    except OSError as exc:
        raise RuntimeError(f"Could not inspect settings file {SETTINGS_FILE}: {exc}") from exc
    if settings_metadata is not None:
        if (
            not stat_module.S_ISREG(settings_metadata.st_mode)
            or settings_metadata.st_nlink != 1
        ):
            raise RuntimeError(
                f"Refusing unsafe non-regular or hard-linked settings file: {SETTINGS_FILE}"
            )
        return False

    info("Settings file not found. Creating default settings.json")
    atomic_write_json(SETTINGS_FILE, DEFAULT_SETTINGS, indent=4)
    if not chown_path(SETTINGS_FILE, uid, gid):
        raise RuntimeError(f"Failed to set ownership on {SETTINGS_FILE}")
    _chmod_path_no_follow(SETTINGS_FILE, 0o640, purpose="settings")
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
    # Keep bootstrap-generated IDs compatible with the persisted DVR ID contract.
    digest = hashlib.md5(
        f"{normalized}:{port}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()
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


def _walk_config_tree_no_follow(
    path: Path, visitor, *, writable_regular_files: bool = False
) -> None:
    """Visit only real directories and regular files beneath one opened root."""

    root_fd = _open_real_directory(path, purpose="configuration")

    def visit_directory(directory_fd: int, display_path: Path) -> None:
        directory_metadata = os.fstat(directory_fd)
        visitor(
            directory_fd,
            directory_metadata,
            display_path,
            True,
            display_path.name,
        )
        try:
            with os.scandir(directory_fd) as iterator:
                entries = sorted(
                    (
                        entry.name,
                        entry.stat(follow_symlinks=False),
                    )
                    for entry in iterator
                )
        except OSError as exc:
            raise RuntimeError(f"Could not inspect configuration directory {display_path}: {exc}") from exc

        for name, listed_metadata in entries:
            child_path = display_path / name
            if stat_module.S_ISLNK(listed_metadata.st_mode):
                raise RuntimeError(
                    f"Refusing symbolic link inside configuration directory: {child_path}"
                )
            is_directory = stat_module.S_ISDIR(listed_metadata.st_mode)
            is_regular = stat_module.S_ISREG(listed_metadata.st_mode)
            if not is_directory and not is_regular:
                raise RuntimeError(
                    f"Refusing non-regular object inside configuration directory: {child_path}"
                )
            if is_regular and listed_metadata.st_nlink != 1:
                raise RuntimeError(
                    f"Refusing hard-linked file inside configuration directory: {child_path}"
                )

            flags = os.O_RDWR if writable_regular_files and is_regular else os.O_RDONLY
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            if is_directory:
                flags |= getattr(os, "O_DIRECTORY", 0)
            try:
                child_fd = os.open(name, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise RuntimeError(
                    f"Configuration path changed or became unsafe: {child_path}: {exc}"
                ) from exc
            try:
                opened_metadata = os.fstat(child_fd)
                if (
                    (opened_metadata.st_dev, opened_metadata.st_ino)
                    != (listed_metadata.st_dev, listed_metadata.st_ino)
                    or is_directory != stat_module.S_ISDIR(opened_metadata.st_mode)
                    or is_regular != stat_module.S_ISREG(opened_metadata.st_mode)
                    or (is_regular and opened_metadata.st_nlink != 1)
                ):
                    raise RuntimeError(
                        f"Configuration path changed while it was inspected: {child_path}"
                    )
                if is_directory:
                    visit_directory(child_fd, child_path)
                else:
                    visitor(
                        child_fd,
                        opened_metadata,
                        child_path,
                        False,
                        name,
                    )
            finally:
                os.close(child_fd)

    try:
        visit_directory(root_fd, path)
    finally:
        os.close(root_fd)


def validate_config_tree(path: Path) -> None:
    _walk_config_tree_no_follow(path, lambda *_args: None)


def chmod_config_tree(path: Path) -> None:
    started_as_root = running_as_root()
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else None

    def apply_mode(
        file_fd: int,
        metadata: os.stat_result,
        display_path: Path,
        is_directory: bool,
        name: str,
    ) -> None:
        # A pre-dropped Helm process cannot chmod root-owned PVC entries. The
        # volume driver's fsGroup policy supplies access to those entries; files
        # owned by this process are still normalized and verified.
        if not started_as_root and metadata.st_uid != effective_uid:
            return
        mode = (
            0o750
            if is_directory
            else 0o600
            if name
            in {
                "encryption.key",
                RESTART_JOURNAL_LOCK_FILE,
                ACTIVATION_OUTCOME_LOCK_FILE,
            }
            else 0o640
        )
        try:
            os.fchmod(file_fd, mode)
        except OSError as exc:
            raise RuntimeError(f"Failed to chmod configuration path {display_path}: {exc}") from exc
        actual = os.fstat(file_fd)
        if stat_module.S_IMODE(actual.st_mode) != mode:
            raise RuntimeError(
                f"Failed to verify mode {mode:o} on configuration path {display_path}"
            )

    _walk_config_tree_no_follow(path, apply_mode)


def chown_tree(path: Path, uid: int, gid: int) -> None:
    started_as_root = running_as_root()
    virtualized_mismatches: list[Path] = []

    def apply_owner(
        file_fd: int,
        _metadata: os.stat_result,
        display_path: Path,
        _is_directory: bool,
        _name: str,
    ) -> None:
        if not started_as_root:
            return
        try:
            os.fchown(file_fd, uid, gid)
        except OSError as exc:
            raise RuntimeError(f"Failed to chown configuration path {display_path}: {exc}") from exc
        actual = os.fstat(file_fd)
        if actual.st_uid != uid or actual.st_gid != gid:
            if ownership_metadata_is_virtualized(display_path):
                virtualized_mismatches.append(display_path)
                return
            raise RuntimeError(
                f"Failed to verify ownership of configuration path {display_path}: "
                f"expected {uid}:{gid}, got {actual.st_uid}:{actual.st_gid}"
            )

    _walk_config_tree_no_follow(path, apply_owner)
    if virtualized_mismatches:
        warning(
            "Ownership metadata remained virtualized for "
            f"{len(virtualized_mismatches)} configuration path(s); deferring "
            "enforcement to the target-identity write-access check."
        )


def verify_config_tree_writable(path: Path) -> None:
    """Prove the dropped runtime identity can update every config path safely."""

    def verify_access(
        directory_or_file_fd: int,
        _metadata: os.stat_result,
        display_path: Path,
        is_directory: bool,
        _name: str,
    ) -> None:
        if not is_directory:
            # The no-follow walker opened this regular file with O_RDWR.
            return

        probe_name: str | None = None
        probe_fd: int | None = None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            for _attempt in range(16):
                candidate = f".channelwatch-access-{uuid.uuid4().hex}"
                try:
                    probe_fd = os.open(
                        candidate,
                        flags,
                        0o600,
                        dir_fd=directory_or_file_fd,
                    )
                except FileExistsError:
                    continue
                except OSError as exc:
                    raise RuntimeError(
                        f"Runtime identity cannot create files in {display_path}: {exc}"
                    ) from exc
                probe_name = candidate
                break
            if probe_fd is None or probe_name is None:
                raise RuntimeError(
                    f"Runtime identity could not allocate an access probe in {display_path}"
                )
            os.write(probe_fd, b"channelwatch-access-check\n")
            os.fsync(probe_fd)
        except OSError as exc:
            raise RuntimeError(
                f"Runtime identity cannot write files in {display_path}: {exc}"
            ) from exc
        finally:
            if probe_fd is not None:
                os.close(probe_fd)
            if probe_name is not None:
                try:
                    os.unlink(probe_name, dir_fd=directory_or_file_fd)
                    os.fsync(directory_or_file_fd)
                except OSError as exc:
                    raise RuntimeError(
                        f"Runtime identity cannot remove files from {display_path}: {exc}"
                    ) from exc

    _walk_config_tree_no_follow(
        path,
        verify_access,
        writable_regular_files=True,
    )


def read_image_version() -> str:
    init_file = IMAGE_APP_DIR / "core" / "__init__.py"
    try:
        content = init_file.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', content, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def select_app_runtime_dir() -> Path:
    try:
        sys.path.insert(0, str(IMAGE_APP_DIR))
        from core.update_center import resolve_active_app_dir

        selection = resolve_active_app_dir(
            config_dir=CONFIG_DIR,
            image_app_dir=IMAGE_APP_DIR,
            image_version=read_image_version(),
            runtime_abi=RUNTIME_ABI,
            settings_schema_version=CURRENT_SCHEMA_VERSION,
        )
        info(
            "[Entrypoint] Selected ChannelWatch app runtime: "
            f"{selection.source} ({selection.reason}) at {selection.app_dir}"
        )
        return selection.app_dir
    except Exception as exc:
        warning(f"Failed to resolve active app bundle; using image app: {exc}")
        return IMAGE_APP_DIR


def clear_completed_restart_handoff(expected_journal: dict) -> bool:
    """Acknowledge a rollback handoff only after this entrypoint rerenders.

    The image-stable runtime launchers refuse to execute an app bundle while
    this sentinel exists.  Only a newly started container entrypoint may clear
    it, after it has selected the restored runtime and rendered the Supervisor
    configuration that pins both children to that selection.
    """

    with restart_transition_lock():
        try:
            current_journal = load_restart_required_journal()
        except RuntimeError as exc:
            warning(f"Refusing to clear a changed runtime transition journal: {exc}")
            return False
        if current_journal != expected_journal:
            warning(
                "Refusing to clear a runtime transition journal that changed "
                "while Supervisor configuration was rendered."
            )
            return False

        directory_fd = _open_real_directory(
            RESTART_REQUIRED_PATH.parent,
            purpose="runtime transition",
        )
        try:
            try:
                os.unlink(RESTART_REQUIRED_PATH.name, dir_fd=directory_fd)
            except FileNotFoundError:
                return False
            except OSError as exc:
                warning(
                    "Failed to clear the completed update restart handoff at "
                    f"{RESTART_REQUIRED_PATH}: {exc}"
                )
                return False
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                warning(f"Failed to sync the cleared update restart handoff: {exc}")
                return False
            return True
        finally:
            os.close(directory_fd)


def _prepare_supervisor_runtime_dir() -> None:
    if SUPERVISOR_SOCKET.parent != SUPERVISOR_RUNTIME_DIR:
        raise RuntimeError("Supervisor socket must remain inside its runtime directory.")
    _ensure_real_directory(
        SUPERVISOR_RUNTIME_DIR,
        mode=0o700,
        purpose="Supervisor runtime",
    )
    _require_path_kind(
        SUPERVISOR_RUNTIME_DIR,
        kind="directory",
        purpose="Supervisor runtime",
    )
    try:
        socket_metadata = SUPERVISOR_SOCKET.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(f"Could not inspect Supervisor socket path: {exc}") from exc
    if stat_module.S_ISLNK(socket_metadata.st_mode):
        raise RuntimeError(
            f"Refusing unsafe symbolic link at Supervisor socket path: {SUPERVISOR_SOCKET}"
        )
    if not stat_module.S_ISSOCK(socket_metadata.st_mode):
        raise RuntimeError(
            f"Refusing unsafe non-socket Supervisor path: {SUPERVISOR_SOCKET}"
        )
    SUPERVISOR_SOCKET.unlink()
    fsync_directory(SUPERVISOR_RUNTIME_DIR)


def _set_required_runtime_permissions(
    path: Path,
    *,
    kind: str,
    uid: int,
    gid: int,
    mode: int,
) -> None:
    _require_path_kind(path, kind=kind, purpose="Supervisor")
    started_as_root = running_as_root()
    if not chown_path(path, uid, gid):
        raise RuntimeError(f"Failed to set required ownership on Supervisor path {path}")
    _chmod_path_no_follow(path, mode, purpose="Supervisor")
    metadata = _require_path_kind(path, kind=kind, purpose="Supervisor")
    expected_uid = uid if started_as_root else os.geteuid()
    expected_gid = gid if started_as_root else os.getegid()
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise RuntimeError(
            f"Supervisor path {path} has unsafe ownership: expected "
            f"{expected_uid}:{expected_gid}, got {metadata.st_uid}:{metadata.st_gid}"
        )


def render_supervisor_config(app_uid: int, app_gid: int) -> None:
    restart_journal = replay_restart_required_journal()
    if restart_journal is not None:
        # Replay writes occur after main's initial ownership pass. Normalize the
        # newly published control records before the journal can be cleared.
        chown_tree(CHANNELWATCH_RUNTIME_DIR, app_uid, app_gid)
        chmod_config_tree(CHANNELWATCH_RUNTIME_DIR)
    if not SUPERVISOR_TEMPLATE.is_file():
        warning(f"supervisord.conf.template not found at {SUPERVISOR_TEMPLATE}")
        return

    _prepare_supervisor_runtime_dir()

    selected_app_dir = select_app_runtime_dir()
    static_ui_dir = selected_app_dir / "ui" / "backend" / "static_ui"
    template = SUPERVISOR_TEMPLATE.read_text(encoding="utf-8")
    rendered = (
        template.replace("__SUPERVISOR_SOCKET__", str(SUPERVISOR_SOCKET))
        .replace("__APP_DIR__", str(selected_app_dir))
        .replace("__STATIC_UI_DIR__", str(static_ui_dir))
    )
    atomic_write_text(SUPERVISOR_CONF, rendered)

    for path, kind, mode in (
        (SUPERVISOR_RUNTIME_DIR, "directory", 0o700),
        (SUPERVISOR_CONF, "regular", 0o640),
    ):
        _set_required_runtime_permissions(
            path,
            kind=kind,
            uid=app_uid,
            gid=app_gid,
            mode=mode,
        )

    if restart_journal is not None and not clear_completed_restart_handoff(
        restart_journal
    ):
        warning("Runtime transition journal remains active; child launch will stay blocked.")
    info("Generated supervisord config with local Unix socket control")


def drop_privileges(uid: int, gid: int) -> None:
    validate_runtime_identity(uid, gid)

    started_as_root = running_as_root()
    if started_as_root:
        try:
            os.setgroups([])
        except OSError as exc:
            raise RuntimeError(f"Failed to clear supplemental groups: {exc}") from exc

        try:
            os.setgid(gid)
            os.setuid(uid)
        except OSError as exc:
            raise RuntimeError(
                f"Failed to drop privileges to {uid}:{gid}: {exc}"
            ) from exc

    # Container runtimes may start the entrypoint with ``--user`` or another
    # platform-level identity override.  That path cannot call setuid/setgid,
    # but it must satisfy the same fail-closed identity contract as the normal
    # root entrypoint path before ChannelWatch is executed.
    effective_uid = os.geteuid()
    effective_gid = os.getegid()
    supplemental_groups = os.getgroups()
    supplemental_groups_valid = (
        not supplemental_groups
        if started_as_root
        else all(group == gid and group != 0 for group in supplemental_groups)
    )
    if (
        effective_uid != uid
        or effective_gid != gid
        or not supplemental_groups_valid
    ):
        allowed_groups = (
            "no supplemental groups" if started_as_root else f"only {gid}"
        )
        raise RuntimeError(
            "Privilege drop left an unexpected effective identity: "
            f"expected {uid}:{gid} with {allowed_groups}, got "
            f"{effective_uid}:{effective_gid} groups={supplemental_groups}"
        )


def prepare_standard_streams() -> None:
    if not running_as_root():
        return

    for fd in (1, 2):
        try:
            # These are already-open container log descriptors, not filesystem
            # data. The mode adjustment keeps stdout/stderr writable after the
            # configurable UID/GID drop.
            os.chmod(f"/proc/self/fd/{fd}", 0o666)  # nosec B103
        except OSError as exc:
            warning(f"Failed to chmod fd {fd}: {exc}")


def set_runtime_umask() -> None:
    """Set and verify the permission mask inherited by Supervisor children."""

    os.umask(RUNTIME_PROCESS_UMASK)
    observed = os.umask(RUNTIME_PROCESS_UMASK)
    if observed != RUNTIME_PROCESS_UMASK:
        raise RuntimeError(
            "Failed to verify runtime umask: "
            f"expected {RUNTIME_PROCESS_UMASK:03o}, got {observed:03o}."
        )


def main() -> None:
    uid = parse_id("PUID", 1000)
    gid = parse_id("PGID", 1000)
    validate_runtime_identity(uid, gid)

    _ensure_real_directory(CONFIG_DIR, mode=0o755, purpose="configuration")
    cleanup_restart_journal_candidates_before_validation()
    # Reject links, FIFOs, devices, sockets, and traversal races before any
    # settings/bootstrap/runtime file is read or replaced as root.
    validate_config_tree(CONFIG_DIR)
    settings_created = ensure_settings(uid, gid)
    merge_bootstrap_env(settings_created)
    if not running_as_root():
        info("[Entrypoint] Running without root privileges; ownership repair is skipped.")
    chown_tree(CONFIG_DIR, uid, gid)
    chmod_config_tree(CONFIG_DIR)
    render_supervisor_config(uid, gid)
    prepare_standard_streams()

    if len(sys.argv) < 2:
        warning("No command provided for ChannelWatch startup.")
        sys.exit(1)

    drop_privileges(uid, gid)
    verify_config_tree_writable(CONFIG_DIR)
    set_runtime_umask()
    # Container argv is operator-controlled, is executed without a shell, and
    # is reached only after the verified privilege drop.
    os.execvp(sys.argv[1], sys.argv[1:])  # nosemgrep


if __name__ == "__main__":
    main()
