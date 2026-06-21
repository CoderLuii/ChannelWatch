# Multi-DVR configuration and lifecycle reference

ChannelWatch v0.9 monitors multiple Channels DVR servers from one container. This page is the detailed reference for the shipped multi-DVR model. For a shorter operator guide, see [`docs/how-to/multi-dvr.md`](../how-to/multi-dvr.md).

## Scope and source of truth

The persisted source of truth is `/config/settings.json`, shared by the core monitor and the FastAPI UI backend. The shipped settings field is `dvr_servers`. There is no persisted `channels_dvr_servers` key in v0.9.

`CHANNELS_DVR_SERVERS` is an environment bootstrap input. At startup, it is parsed into `dvr_servers` entries, then the saved file remains the runtime source of truth.

Source citations used for this page:

| Behavior | Source |
|---|---|
| Existing user guide and public wording | `docs/how-to/multi-dvr.md` |
| Canonical DVR ID algorithm | `app/core/helpers/dvr_id.py` |
| Core settings shape and environment merge | `app/core/helpers/config.py` |
| UI schema and `notification_routing` shape | `app/ui/backend/schemas.py` |
| UI settings save encryption path | `app/ui/backend/config.py` |
| mDNS scan behavior | `app/core/helpers/discovery.py` |
| Hot reload diffing | `app/core/helpers/hot_reload.py` |
| Core SIGHUP handling and monitor reload | `app/core/main.py` |
| Soft delete, restore, purge, hard delete | `app/core/helpers/soft_delete_manager.py` |
| Per-DVR state files and notification history | `app/core/engine/alert_manager.py` |
| Activity dedup keys and history fields | `app/core/helpers/activity_recorder.py` |
| Notification routing evaluation | `app/core/notifications/notification.py` |
| Webhook DVR identity payload | `app/core/notifications/webhook.py` |
| Routing matrix UI | `app/ui/components/settings/routing-settings-section.tsx` |
| v0.9 known limits | `docs/releases/CHANGELOG.md` |

## DVR identity model

Every DVR has an `id` used by the API, history, routing, state files, webhooks, and UI selection state.

Canonical format:

```text
dvr_<8 hex chars>
```

Derivation:

```text
"dvr_" + md5(normalized_host + ":" + str(port))[:8]
```

Normalization rules:

| Input class | Rule |
|---|---|
| IPv4 address | Keep the host text as provided. |
| Hostname | Keep the host text as provided, including case. |
| IPv6 address | Strip surrounding `[` and `]`, then lowercase before hashing. |
| Port | Convert to string and append after a colon. |

Examples and implications:

| Host value | Port | Identity behavior |
|---|---:|---|
| `192.168.1.10` | `8089` | Stable for that exact address and port. |
| `media-server` | `8089` | Different from an IP address that points to the same DVR. |
| `Media-Server` | `8089` | Different from `media-server`, because hostnames are case-preserving. |
| `[2001:db8::1]` | `8089` | Same as `2001:db8::1`. |
| `2001:DB8::1` | `8089` | Same as `2001:db8::1`. |

Changing `host` or `port` changes the derived ID unless the old `id` is preserved manually by migration or existing config merge logic. Use stable hostnames or static addresses when long-term identity matters.

## Configuration shape

The multi-DVR list lives at `dvr_servers` in `/config/settings.json`.

```json
{
  "dvr_servers": [
    {
      "id": "dvr_0123abcd",
      "name": "Living Room",
      "host": "192.168.1.10",
      "port": 8089,
      "api_key": "fernet:...",
      "enabled": true,
      "overrides": {},
      "deleted_at": null
    }
  ]
}
```

Per-DVR fields:

| Field | Type | Required | Behavior |
|---|---|---:|---|
| `id` | string | Yes | Canonical DVR identity. Active monitor setup requires a non-empty ID. |
| `name` | string | Recommended | Human-facing label shown in the UI and sent as `dvr_name`. v0.9 still uses `name` as the main display field. |
| `host` | string | Yes | DVR host, IP address, or IPv6 text. Combined with `port` for canonical ID generation. |
| `port` | integer | Yes | DVR HTTP port. Defaults to `8089` when omitted by helper paths. |
| `api_key` | string | Optional | Per-DVR Channels API key. Saved at rest as a `fernet:` value when present. |
| `enabled` | boolean | Optional | Defaults to enabled. Disabled DVRs are skipped by core monitor startup. |
| `overrides` | object | Optional | Per-DVR settings overrides copied onto that DVR monitor context. |
| `deleted_at` | string | Optional | Presence marks the DVR as archived and excludes it from active monitoring. |

Environment bootstrap:

| Input | Shipped v0.9 behavior |
|---|---|
| `CHANNELS_DVR_SERVERS` | Parses comma-separated `Name@host:port` entries, preserves existing matching IDs and overrides, and keeps manual entries not listed in the env value. |
| `CHANNELS_DVR_HOST`, `CHANNELS_DVR_PORT` | Legacy single-DVR compatibility path. Still accepted with a warning, despite the original plan to remove it in v0.9. |
| `CW_DVR_<N>_*` and `/config/dvrs.yaml` | Deferred. They are listed as known limits in the v0.9 changelog and are not implemented in the shipped config loader. |

## Per-DVR state isolation

ChannelWatch isolates DVR state by `dvr_id` at several layers.

| State | Isolation rule |
|---|---|
| Alert session files | Each `AlertManager` writes `/config/session_state_<dvr_id>.json`. |
| Notification cooldown memory | `AlertManager` keeps `_notification_history` as an instance field, so each DVR monitor has its own in-memory duplicate suppression state. |
| Activity dedup keys | Live and VOD activity keys start with `dvr_id`. Recording event keys include `dvr_id`. Disk alert keys include `dvr_id` when available, otherwise DVR name, path, or `global`. |
| Activity history rows | Recorded activity includes `dvr_id` and `dvr_name` fields. |
| Webhooks | Payloads include top-level `dvr_id` and `dvr_name`. |
| Notification delivery retry and circuit state | Delivery code keys circuit breaker state by `dvr_id` and channel. |

`AlertManager` rejects construction without an explicit DVR identity. This prevents accidental fallback into a shared `default` state bucket.

## Discovery

Discovery is a convenience path, not the source of truth. Saved configuration still controls what ChannelWatch monitors.

Implementation details:

| Item | Shipped behavior |
|---|---|
| Protocol | mDNS through `zeroconf`. |
| Service type | `_channels_dvr._tcp.local.` |
| Default timeout | `5.0` seconds. |
| Result fields | `host`, `port`, and `display_name_suggestion`. |
| Display suggestion | mDNS `name`, then `friendlyName`, then service name. |
| Duplicate filtering | Filters exact `(host, port)` pairs already in saved config. |
| Manual add fallback | Always available in scan responses. |

If `zeroconf` cannot be imported, scanning returns an empty list. If no servers are found while running inside a container, the response message says auto-discovery requires host network mode and tells the user to start the container with `--network host` or add the DVR manually. This is the bridge-mode graceful fallback shipped in v0.9.

Discovery does not rewrite existing DVR entries. If a DVR changes IP address and discovery finds the new address, adding it creates a new identity unless you preserve the original `id` yourself.

## Hot reload lifecycle

The UI backend saves settings, then tries to signal the core process with `SIGHUP` through supervisor XML-RPC. The core installs an early `SIGHUP` ignore guard at module import, then replaces it with an asyncio signal handler once the runtime is ready. This prevents a fresh no-DVR container from exiting if a reload signal arrives before the event loop installs its real handler.

Core reload steps:

1. `SIGHUP` sets a reload event.
2. The config watcher compares the previous and current `/config/settings.json` content by hash.
3. `compute_reload_diff()` classifies DVR additions, removals, per-DVR changes, global runtime changes, and restart-required keys.
4. Removed DVR IDs are stopped.
5. Changed DVR IDs are stopped, rebuilt, restarted, and freshness-checked.
6. Added DVR IDs are initialized and started without restarting the container.
7. Non-restart-required global changes restart active DVR monitors so they pick up the new shared settings.
8. Restart-required keys are logged as not applied hot.

Restart-required keys in the helper are `uvicorn_host`, `uvicorn_port`, `db_url`, `rbac_enabled`, and `multi_dvr_v2_enabled`.

Important edge behavior: if no DVR is configured when the core first starts, `app/core/main.py` waits and logs that configuration was detected but a restart is needed. The hot reload path applies to the dynamic monitor runtime after monitors have been initialized.

## Per-DVR notification routing

Notification routing is stored as an object under `notification_routing`:

```json
{
  "notification_routing": {
    "dvr_0123abcd": {
      "channel": {
        "discord": true,
        "webhook": false
      },
      "disk": {
        "webhook": true
      }
    }
  }
}
```

Shape:

```text
dvr_id -> event_type -> destination -> boolean
```

Event keys used by the routing UI are `channel`, `vod`, `recording`, and `disk`.

Destinations:

| Destination group | Keys |
|---|---|
| Apprise | `pushover`, `discord`, `email`, `telegram`, `slack`, `gotify`, `matrix`, `custom` |
| Webhook | `webhook` |

Evaluation rules:

| Case | Result |
|---|---|
| No routing object | All destinations are enabled. |
| Missing `dvr_id` | All destinations are enabled. |
| Missing `event_type` | All destinations are enabled. |
| No entry for DVR | All destinations are enabled. |
| No entry for event type | All destinations are enabled. |
| Destination key missing inside an event route | That destination defaults to enabled. |
| Destination key set to `false` | That destination is skipped for that DVR and event type. |

The matrix UI filters rows to active, non-archived DVRs and only shows destinations that are configured. A reset action deletes the DVR key from `notification_routing`, returning that DVR to default all-enabled routing.

## Soft delete, restoration, and hard delete

Soft delete is archival. It sets `deleted_at` on the DVR entry and leaves the entry in `settings.json`.

| Operation | Endpoint | Behavior |
|---|---|---|
| List archived DVRs | `GET /api/dvrs/archived` | Returns entries with `deleted_at`. |
| Soft delete | `POST /api/dvrs/{dvr_id}/soft-delete` | Sets `deleted_at`, saves settings, and signals hot reload. |
| Restore | `POST /api/dvrs/{dvr_id}/restore` | Removes `deleted_at`, saves settings, and signals hot reload. |
| Hard delete | `DELETE /api/dvrs/{dvr_id}` | Removes the entry, deletes `/config/session_state_<dvr_id>.json`, removes matching legacy history rows, saves settings, and signals hot reload. |

Retention:

| Value | Meaning |
|---|---|
| `SOFT_DELETE_RETENTION_DAYS = 30` | Helper default for purging archived DVRs after 30 days. |
| Purge action | `purge_expired_dvrs()` hard-deletes soft-deleted entries older than the retention window when called by maintenance code. |

Archived DVRs are not returned by `get_dvr_connections()` and are ignored by hot reload active DVR maps. Restoration only works while the archived entry still exists.

## Encrypted per-DVR API keys

Per-DVR API keys are encrypted at rest when settings are saved by the UI backend.

| Item | Behavior |
|---|---|
| Key path | `/config/encryption.key`, or `CONFIG_PATH/encryption.key` when `CONFIG_PATH` changes. |
| Key contents | Raw 32-byte random key. |
| Permissions | Must be `0600` or stricter. The bootstrap code refuses to use a key with group or world permissions. |
| Cipher | Fernet AEAD from `cryptography.fernet`. |
| Stored prefix | Encrypted values are stored as `fernet:<token>`. |
| Encryption timing | `save_settings()` calls `encrypt_dvr_api_keys()` before writing `settings.json`. |
| Decryption timing | Core and UI settings loaders decrypt `fernet:` API keys for runtime use. |
| Failure behavior on decrypt | Leaves encrypted values unchanged rather than corrupting config. |

The key is generated at core startup before settings are loaded. Backup and restore code treats `encryption.key` as sensitive backup material because encrypted per-DVR API keys need it for recovery.

## Limits and known v0.9 gaps

The v0.9 changelog is authoritative for known limits.

| Area | Shipped v0.9 status |
|---|---|
| DVR count warning | The core logs a soft warning above 10 enabled DVRs. Tests document that 50 DVR settings can load, round-trip, and emit the warning without blocking operation. Larger setups may work, but no UI warning or override control ships in v0.9. |
| Config loaders | `CHANNELS_DVR_SERVERS` comma-separated `Name@host:port` entries and legacy single-DVR env vars ship. `CW_DVR_<N>_*` and `/config/dvrs.yaml` are deferred. |
| Concurrency | The core has asyncio task groups, but parts of event monitoring still run through threads. |
| Real DVR CI | Mock cluster tests exist. A real Channels DVR image lane is deferred. |
| Display field split | The plan called for separate `display_name`; shipped v0.9 primarily uses `name`. |
| Legacy env vars | Legacy `CHANNELS_DVR_HOST` and `CHANNELS_DVR_PORT` still work with warnings. Full removal is deferred. |

## Relationship to `docs/how-to/multi-dvr.md`

`docs/how-to/multi-dvr.md` is the user-facing guide. It explains practical setup, discovery, routing, removal, restore, and general limits.

This reference is intentionally more exhaustive. It adds exact settings shapes, shipped field names, implementation-level routing defaults, state isolation details, hot reload internals, encryption behavior, and known v0.9 drift from the original plan.

The two documents should not contradict each other. If they differ, treat this page as the more detailed reference and verify against the cited source files.

## See also

* `docs/how-to/multi-dvr.md`, multi-DVR how-to guide
* `docs/releases/CHANGELOG.md`, v0.9.0 release notes and known limits
