import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from .logging import log, LOG_STANDARD, LOG_VERBOSE

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
    for path in [config_dir / f"session_state_{dvr_id}.json"]:
        if path.is_file():
            try:
                path.unlink()
                log(f"Removed state file: {path.name}", level=LOG_STANDARD)
            except OSError as e:
                log(f"Could not remove {path.name}: {e}", level=LOG_VERBOSE)


def _remove_dvr_history_rows(config_dir: Path, dvr_id: str) -> int:
    history_file = config_dir / "activity_history.json"
    if not history_file.is_file():
        return 0
    try:
        raw = json.loads(history_file.read_text())
        if not isinstance(raw, list):
            return 0
        before = len(raw)
        kept = [
            r for r in raw if not (isinstance(r, dict) and r.get("dvr_id") == dvr_id)
        ]
        removed = before - len(kept)
        if removed > 0:
            history_file.write_text(json.dumps(kept, indent=2))
            log(f"Removed {removed} history rows for DVR {dvr_id}", level=LOG_STANDARD)
        return removed
    except (json.JSONDecodeError, OSError) as e:
        log(f"Could not purge history rows for DVR {dvr_id}: {e}", level=LOG_VERBOSE)
        return 0


def hard_delete_dvr(
    config_dir: Path,
    dvr_servers: List[Dict[str, Any]],
    dvr_id: str,
) -> bool:
    """Remove DVR from settings list, its state files, and history rows. Mutates dvr_servers in-place."""
    original_len = len(dvr_servers)
    dvr_servers[:] = [
        s for s in dvr_servers if not (isinstance(s, dict) and s.get("id") == dvr_id)
    ]
    if len(dvr_servers) == original_len:
        return False
    _remove_dvr_state_files(config_dir, dvr_id)
    _remove_dvr_history_rows(config_dir, dvr_id)
    log(f"Hard-deleted DVR {dvr_id}", level=LOG_STANDARD)
    return True


def purge_expired_dvrs(
    config_dir: Path,
    dvr_servers: List[Dict[str, Any]],
    retention_days: int = SOFT_DELETE_RETENTION_DAYS,
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
        if hard_delete_dvr(config_dir, dvr_servers, dvr_id):
            purged.append(dvr_id)
            log(
                f"Auto-purged DVR {dvr_id} (deleted >{retention_days}d ago)",
                level=LOG_STANDARD,
            )
    return purged
