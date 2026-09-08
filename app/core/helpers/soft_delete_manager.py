import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from .logging import log, LOG_STANDARD, LOG_VERBOSE
from core.storage.activity_store import delete_dvr_activity, validate_activity_deletion
from .atomic_io import atomic_write_private_json, read_regular_file_bytes, fsync_directory

SOFT_DELETE_RETENTION_DAYS = 30


def soft_delete_dvr(dvr_servers: List[Dict[str, Any]], dvr_id: str) -> bool:
    """Set deleted_at on the matching DVR entry. Raises ValueError if already soft-deleted."""
    for server in dvr_servers:
        if isinstance(server, dict) and server.get("id") == dvr_id:
            if server.get("deleted_at"):
                raise ValueError(f"DVR {dvr_id!r} is already soft-deleted")
            server["deleted_at"] = datetime.now(timezone.utc).isoformat()
            log(f"Soft-deleted DVR {dvr_id}", level=LOG_STANDARD)
            return True
    return False


def restore_dvr(dvr_servers: List[Dict[str, Any]], dvr_id: str) -> bool:
    """Clear deleted_at on the matching DVR entry. Raises ValueError if not soft-deleted."""
    for server in dvr_servers:
        if isinstance(server, dict) and server.get("id") == dvr_id:
            if not server.get("deleted_at"):
                raise ValueError(f"DVR {dvr_id!r} is not soft-deleted, cannot restore")
            server.pop("deleted_at", None)
            log(f"Restored DVR {dvr_id}", level=LOG_STANDARD)
            return True
    return False


def _remove_dvr_state_files(config_dir: Path, dvr_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", dvr_id):
        raise ValueError("Invalid DVR identifier for deletion.")
    path = config_dir / f"session_state_{dvr_id}.json"
    path.unlink(missing_ok=True)
    fsync_directory(config_dir)


def _remove_dvr_history_rows(config_dir: Path, dvr_id: str) -> int:
    return delete_dvr_activity(dvr_id, config_dir=config_dir)


def prepare_dvr_deletions(config_dir: Path, dvr_ids: list[str]) -> None:
    """Write intent before settings commit; do not destroy product data here."""
    if not dvr_ids:
        return
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", item) for item in dvr_ids):
        raise ValueError("Invalid DVR identifier for deletion.")
    validate_activity_deletion(config_dir)
    atomic_write_private_json(
        config_dir / "pending-dvr-deletions.json", {"version": 1, "dvr_ids": dvr_ids}
    )


def complete_pending_dvr_deletions(config_dir: Path, servers: List[Dict[str, Any]]) -> None:
    """Replay intent against durable settings while the settings lock is held.

    A still-configured DVR means settings did not commit: cancel that intent.
    An absent DVR must finish its purge before this record can be removed.
    """
    path = config_dir / "pending-dvr-deletions.json"
    try:
        record = json.loads(read_regular_file_bytes(path, max_bytes=128 * 1024))
    except FileNotFoundError:
        return
    if (not isinstance(record, dict) or record.get("version") != 1
            or not isinstance(record.get("dvr_ids"), list)
            or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", item)
                   for item in record["dvr_ids"])):
        raise ValueError("Pending DVR deletion record needs recovery.")
    configured = {server.get("id") for server in servers if isinstance(server, dict)}
    for dvr_id in record["dvr_ids"]:
        if dvr_id not in configured:
            _remove_dvr_history_rows(config_dir, dvr_id)
            _remove_dvr_state_files(config_dir, dvr_id)
    path.unlink()
    fsync_directory(config_dir)


def hard_delete_dvr(
    config_dir: Path,
    dvr_servers: List[Dict[str, Any]],
    dvr_id: str,
    *,
    defer_data_purge: bool = False,
) -> bool:
    """Remove DVR from settings list, its state files, and history rows. Mutates dvr_servers in-place."""
    if not any(isinstance(server, dict) and server.get("id") == dvr_id for server in dvr_servers):
        return False
    if not defer_data_purge:
        _remove_dvr_history_rows(config_dir, dvr_id)
        _remove_dvr_state_files(config_dir, dvr_id)
    dvr_servers[:] = [
        server for server in dvr_servers
        if not (isinstance(server, dict) and server.get("id") == dvr_id)
    ]
    log(f"Hard-deleted DVR {dvr_id}", level=LOG_STANDARD)
    return True


def purge_expired_dvrs(
    config_dir: Path,
    dvr_servers: List[Dict[str, Any]],
    retention_days: int = SOFT_DELETE_RETENTION_DAYS,
    *,
    defer_data_purge: bool = False,
) -> List[str]:
    """Hard-delete soft-deleted DVRs older than retention_days. Mutates dvr_servers in-place."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)

    to_purge: List[str] = []
    for server in dvr_servers:
        if not isinstance(server, dict):
            continue
        deleted_at_raw = server.get("deleted_at")
        if not deleted_at_raw:
            continue
        dvr_id = server.get("id")
        if not dvr_id:
            continue
        try:
            deleted_at = datetime.fromisoformat(deleted_at_raw)
            if deleted_at.tzinfo is None:
                deleted_at = deleted_at.replace(tzinfo=timezone.utc)
            if deleted_at <= cutoff:
                to_purge.append(dvr_id)
        except ValueError:
            continue

    purged: List[str] = []
    for dvr_id in to_purge:
        if hard_delete_dvr(config_dir, dvr_servers, dvr_id, defer_data_purge=defer_data_purge):
            purged.append(dvr_id)
            log(
                f"Auto-purged DVR {dvr_id} (deleted >{retention_days}d ago)",
                level=LOG_STANDARD,
            )
    return purged
