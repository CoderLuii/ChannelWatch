"""Hot reload for runtime settings.

Watches /config/settings.json for changes and signals the async runtime to
cancel and re-init only the DVR task(s) whose config changed.

Restart-required settings (listen port, RBAC feature flag, DB URL) are
detected and logged but never applied hot — they require a container restart.

compute_reload_diff() is a pure function (no I/O) — straightforward to unit-test.
"""

from __future__ import annotations

import json
from typing import Any

# These settings require a container restart — hot reload detects changes and
# emits a warning but does NOT stop or restart any DVR task.
RESTART_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "uvicorn_host",  # listen address for the UI web server
        "uvicorn_port",  # listen port for the UI web server
        "db_url",  # database connection string (future)
        "rbac_enabled",  # RBAC feature flag — requires re-init of auth layer
        "multi_dvr_v2_enabled",  # feature-gate — full restart required to take effect
    }
)

_DVR_RESTART_FIELDS: tuple[str, ...] = (
    "host",
    "port",
    "name",
    "enabled",
    "overrides",
)

POLL_INTERVAL: float = 2.0


def _active_dvr_map(raw_settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for server in raw_settings.get("dvr_servers", []):
        if not isinstance(server, dict):
            continue
        if server.get("deleted_at"):
            continue
        dvr_id = server.get("id")
        if dvr_id:
            result[str(dvr_id)] = server
    return result


def _dvr_fingerprint(server: dict[str, Any]) -> str:
    snapshot = {f: server.get(f) for f in _DVR_RESTART_FIELDS}
    return json.dumps(snapshot, sort_keys=True, default=str)


def compute_reload_diff(
    old: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    """Compare two raw settings dicts and return a structured reload diff.

    Returns a dict with keys:
        changed_dvr_ids  – list[str]: existing DVR IDs whose config changed
        added_dvr_ids    – list[str]: IDs present in new but absent in old
        removed_dvr_ids  – list[str]: IDs present in old but absent in new
        restart_required – list[str]: keys that changed but need container restart
        global_changes   – dict:     non-DVR, non-restart-required keys that changed
        any_action       – bool:     True if anything actionable changed
    """
    old_dvrs = _active_dvr_map(old)
    new_dvrs = _active_dvr_map(new)

    old_ids = set(old_dvrs)
    new_ids = set(new_dvrs)

    added: list[str] = sorted(new_ids - old_ids)
    removed: list[str] = sorted(old_ids - new_ids)
    changed: list[str] = sorted(
        dvr_id
        for dvr_id in old_ids & new_ids
        if _dvr_fingerprint(old_dvrs[dvr_id]) != _dvr_fingerprint(new_dvrs[dvr_id])
    )

    restart_required: list[str] = sorted(
        k for k in RESTART_REQUIRED_KEYS if old.get(k) != new.get(k)
    )

    _skip: set[str] = {"dvr_servers", "_version"} | RESTART_REQUIRED_KEYS
    global_changes: dict[str, Any] = {
        k: {"from": old.get(k), "to": new.get(k)}
        for k in sorted(set(old) | set(new))
        if k not in _skip and old.get(k) != new.get(k)
    }

    any_action = bool(changed or added or removed or restart_required or global_changes)

    return {
        "changed_dvr_ids": changed,
        "added_dvr_ids": added,
        "removed_dvr_ids": removed,
        "restart_required": restart_required,
        "global_changes": global_changes,
        "any_action": any_action,
    }


def format_diff_summary(diff: dict[str, Any]) -> str:
    parts: list[str] = []
    if diff["added_dvr_ids"]:
        parts.append(f"DVRs added={diff['added_dvr_ids']}")
    if diff["removed_dvr_ids"]:
        parts.append(f"DVRs removed={diff['removed_dvr_ids']}")
    if diff["changed_dvr_ids"]:
        parts.append(f"DVRs changed={diff['changed_dvr_ids']}")
    if diff["restart_required"]:
        parts.append(f"restart-required (not applied)={diff['restart_required']}")
    if diff["global_changes"]:
        parts.append(f"global updated={list(diff['global_changes'].keys())}")
    return "; ".join(parts) if parts else "no changes detected"


def compute_reload_targets(
    diff: dict[str, Any],
    *,
    active_dvr_ids: list[str] | tuple[str, ...] | set[str],
) -> list[str]:
    """Return existing active DVR ids that should be restarted for this diff.

    Per-DVR edits only restart the affected DVR ids. Non-restart-required global
    changes apply to every currently active DVR because those monitors inherit the
    shared runtime settings object.
    """
    active_ids = sorted(str(dvr_id) for dvr_id in active_dvr_ids)
    if diff["global_changes"]:
        removed = set(diff["removed_dvr_ids"])
        return [dvr_id for dvr_id in active_ids if dvr_id not in removed]
    return sorted(diff["changed_dvr_ids"])
