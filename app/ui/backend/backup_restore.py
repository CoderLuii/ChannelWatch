from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Any, cast


def _load_current_schema_version() -> int:
    try:
        migration = import_module("core.helpers.migration")
    except ImportError:
        return 7
    return int(getattr(migration, "CURRENT_SCHEMA_VERSION", 7))


CURRENT_SCHEMA_VERSION: int = _load_current_schema_version()

_SENSITIVE_SUBFOLDER = "sensitive_keys"
MAX_RESTORE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_RESTORE_MEMBER_BYTES = 32 * 1024 * 1024
MAX_RESTORE_MANIFEST_BYTES = 1024 * 1024
MAX_RESTORE_TOTAL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_RESTORE_MEMBER_COUNT = 256

_SECURITY_WARNING_TEXT = (
    "SECURITY WARNING\n"
    "================\n\n"
    "This folder contains the encryption key used to protect per-DVR API keys\n"
    "stored in settings.json.\n\n"
    "DO NOT share this backup archive with untrusted parties.\n\n"
    "The encryption key alone does not expose secrets — it only becomes sensitive\n"
    "when paired with settings.json. Together they allow decryption of API keys.\n\n"
    "If restoring to a different machine, ensure the destination volume is secured\n"
    "at least as well as the source.\n"
)


class RestoreValidationError(Exception):
    pass


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_settings_schema_version(config_dir: Path) -> int:
    settings_file = config_dir / "settings.json"
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                v = data.get("_version")
                if isinstance(v, int):
                    return v
        except Exception:
            pass
    return CURRENT_SCHEMA_VERSION


def create_backup_zip(config_dir: Path) -> bytes:
    ts = _utc_timestamp()
    prefix = f"channelwatch_backup_{ts}"
    settings_schema_version = _read_settings_schema_version(config_dir)

    buf = io.BytesIO()
    files_included: list[str] = []

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:

        def _add(src: Path, arcname: str) -> None:
            if src.exists():
                zf.write(str(src), f"{prefix}/{arcname}")
                files_included.append(arcname)

        _add(config_dir / "settings.json", "settings.json")
        _add(config_dir / "channelwatch.db", "channelwatch.db")

        for state_file in sorted(config_dir.glob("session_state_*.json")):
            _add(state_file, state_file.name)

        enc_key = config_dir / "encryption.key"
        if enc_key.exists():
            zf.write(str(enc_key), f"{prefix}/{_SENSITIVE_SUBFOLDER}/encryption.key")
            files_included.append(f"{_SENSITIVE_SUBFOLDER}/encryption.key")
            zf.writestr(
                f"{prefix}/{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt",
                _SECURITY_WARNING_TEXT,
            )
            files_included.append(f"{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt")

        manifest = {
            "backup_schema_version": 1,
            "settings_schema_version": settings_schema_version,
            "created_at": ts,
            "created_by": "channelwatch-ui",
            "files": files_included,
        }
        zf.writestr(f"{prefix}/backup_manifest.json", json.dumps(manifest, indent=2))

    return buf.getvalue()


def _validate_zip_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise RestoreValidationError(
            f"Backup archive contains unsafe member path: {name!r}."
        )
    return path


def _is_allowed_restore_member(rel: str) -> bool:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts:
        return False

    if rel in {"settings.json", "channelwatch.db", "backup_manifest.json"}:
        return True
    if rel.startswith("session_state_") and path.name == rel and rel.endswith(".json"):
        return True
    if rel == f"{_SENSITIVE_SUBFOLDER}/encryption.key":
        return True
    if rel == f"{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt":
        return True
    return False


def _restore_filename_for_member(rel: str) -> str | None:
    if not rel or rel.endswith("/") or rel == "backup_manifest.json":
        return None
    if rel == f"{_SENSITIVE_SUBFOLDER}/SECURITY_WARNING.txt":
        return None
    if not _is_allowed_restore_member(rel):
        raise RestoreValidationError(
            f"Backup archive contains unsupported restore member path: {rel!r}."
        )

    if rel.startswith(f"{_SENSITIVE_SUBFOLDER}/"):
        filename = rel[len(f"{_SENSITIVE_SUBFOLDER}/") :]
    else:
        filename = rel

    dest_path = PurePosixPath(filename)
    if dest_path.is_absolute() or ".." in dest_path.parts or len(dest_path.parts) != 1:
        raise RestoreValidationError(
            f"Backup archive contains unsafe restore destination: {rel!r}."
        )
    return filename


def _safe_restore_destination(config_dir: Path, filename: str) -> Path:
    config_root = config_dir.resolve()
    dest = (config_root / filename).resolve()
    if dest.parent != config_root:
        raise RestoreValidationError(
            f"Backup archive restore destination escapes config directory: {filename!r}."
        )
    return dest


def _parse_manifest(zf: zipfile.ZipFile) -> tuple[dict[str, Any], str]:
    manifest_paths = [n for n in zf.namelist() if n.endswith("/backup_manifest.json")]
    if not manifest_paths:
        raise RestoreValidationError(
            "backup_manifest.json not found — this does not appear to be a ChannelWatch backup."
        )
    try:
        manifest = json.loads(zf.read(manifest_paths[0]))
    except json.JSONDecodeError as exc:
        raise RestoreValidationError(
            f"backup_manifest.json contains invalid JSON: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise RestoreValidationError("backup_manifest.json must contain a JSON object.")
    prefix = manifest_paths[0].rsplit("/backup_manifest.json", 1)[0]
    return cast(dict[str, Any], manifest), prefix


def _validate_restore_zip_info(zf: zipfile.ZipFile) -> None:
    member_count = 0
    total_uncompressed = 0
    for info in zf.infolist():
        _ = _validate_zip_member_path(info.filename)
        if not info.is_dir():
            member_count += 1
            total_uncompressed += info.file_size
            if member_count > MAX_RESTORE_MEMBER_COUNT:
                raise RestoreValidationError(
                    "Backup archive exceeds the restore member count limit."
                )
            if total_uncompressed > MAX_RESTORE_TOTAL_UNCOMPRESSED_BYTES:
                raise RestoreValidationError(
                    "Backup archive exceeds the restore total uncompressed size limit."
                )
        if info.file_size > MAX_RESTORE_MEMBER_BYTES:
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} exceeds the restore member size limit."
            )
        if info.compress_size > MAX_RESTORE_ARCHIVE_BYTES:
            raise RestoreValidationError(
                f"Backup archive member {info.filename!r} exceeds the compressed size limit."
            )
        if info.filename.endswith("/backup_manifest.json") and (
            info.file_size > MAX_RESTORE_MANIFEST_BYTES
        ):
            raise RestoreValidationError(
                "backup_manifest.json exceeds the restore manifest size limit."
            )


def validate_restore_zip(zip_bytes: bytes) -> dict[str, Any]:
    if len(zip_bytes) > MAX_RESTORE_ARCHIVE_BYTES:
        raise RestoreValidationError(
            "Backup archive exceeds the restore upload size limit."
        )

    try:
        buf = io.BytesIO(zip_bytes)
        zf = zipfile.ZipFile(buf, "r")
    except zipfile.BadZipFile as exc:
        raise RestoreValidationError(f"Not a valid zip file: {exc}") from exc

    with zf:
        _validate_restore_zip_info(zf)

        bad_member = zf.testzip()
        if bad_member is not None:
            raise RestoreValidationError(
                f"Archive integrity check failed: member '{bad_member}' has a bad CRC."
            )

        manifest, prefix = _parse_manifest(zf)

        backup_sv = manifest.get("settings_schema_version")
        if not isinstance(backup_sv, int):
            raise RestoreValidationError(
                "backup_manifest.json is missing a valid 'settings_schema_version' integer field."
            )

        if backup_sv > CURRENT_SCHEMA_VERSION:
            raise RestoreValidationError(
                f"Backup schema version ({backup_sv}) is ahead of this installation "
                f"({CURRENT_SCHEMA_VERSION}). Upgrade ChannelWatch before restoring."
            )

        names = set(zf.namelist())
        prefix_with_slash = f"{prefix}/"
        for name in names:
            if not name.startswith(prefix_with_slash):
                continue
            rel = name[len(prefix_with_slash) :]
            if rel and not rel.endswith("/") and not _is_allowed_restore_member(rel):
                raise RestoreValidationError(
                    f"Backup archive contains unsupported restore member path: {rel!r}."
                )

        if f"{prefix}/settings.json" not in names:
            raise RestoreValidationError(
                "Backup is missing settings.json — archive may be incomplete."
            )

    return manifest


def restore_from_zip(zip_bytes: bytes, config_dir: Path) -> dict[str, Any]:
    atomic_io = import_module("core.helpers.atomic_io")
    atomic_write_bytes = cast(
        Callable[[Path, bytes], None], getattr(atomic_io, "atomic_write_bytes")
    )
    atomic_write_secret_bytes = cast(
        Callable[[Path, bytes], None],
        getattr(atomic_io, "_atomic_write_secret_bytes"),
    )
    decrypt_secret_bytes = cast(
        Callable[[bytes], bytes], getattr(atomic_io, "_decrypt_secret_bytes")
    )

    manifest = validate_restore_zip(zip_bytes)

    backups_dir = config_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    snapshot_bytes = create_backup_zip(config_dir)
    snapshot_path = backups_dir / f"pre-restore.{_utc_timestamp()}.zip"
    atomic_write_bytes(snapshot_path, snapshot_bytes)

    buf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(buf, "r") as zf:
        manifest_paths = [
            n for n in zf.namelist() if n.endswith("/backup_manifest.json")
        ]
        prefix = manifest_paths[0].rsplit("/backup_manifest.json", 1)[0] + "/"

        for name in zf.namelist():
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix) :]
            filename = _restore_filename_for_member(rel)
            if not filename:
                continue

            dest = _safe_restore_destination(config_dir, filename)
            dest.parent.mkdir(parents=True, exist_ok=True)
            member_bytes = zf.read(name)
            if dest.name == "encryption.key":
                atomic_write_secret_bytes(dest, decrypt_secret_bytes(member_bytes))
            else:
                atomic_write_bytes(dest, member_bytes)

    return manifest
