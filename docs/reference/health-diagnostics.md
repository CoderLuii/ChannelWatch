# Health checks and diagnostics reference

ChannelWatch exposes lightweight probe endpoints, operator diagnostics in the web UI, and a `channelwatch doctor` CLI for container troubleshooting. This page describes the behavior implemented in `app/ui/backend/main.py`, `app/core/cli/doctor.py`, `app/core/helpers/debug_bundle.py`, and `app/ui/components/diagnostics-panel.tsx`.

## Probe endpoints

There is no root `GET /healthz` route in this snapshot. The implemented health routes are `GET /healthz/live`, `GET /healthz/ready`, and `GET /healthz/startup`. They are exempt from authentication and rate limits.

### `GET /healthz/live`

`/healthz/live` is a process liveness probe for the UI backend.

Response body:

```json
{"status":"ok"}
```

Status codes:

* `200` when the FastAPI process can answer the request.

What it checks:

* It does not query the database.
* It does not query supervisor.
* It does not inspect the core process.
* It does not call any DVR.

### `GET /healthz/startup`

`/healthz/startup` reports whether backend startup has completed.

Ready response:

```json
{"status":"ready"}
```

Not ready response:

```json
{"status":"not_ready"}
```

Status codes:

* `200` when `_STARTUP_COMPLETE` is true.
* `503` when `_STARTUP_COMPLETE` is false.

What it checks:

* Only the backend startup flag.
* It does not query the database, supervisor, core process, or DVRs.

### `GET /healthz/ready`

`/healthz/ready` is the readiness probe. It reads enabled DVR records from settings, reads the persisted watchdog snapshot, then summarizes whether every enabled, non deleted DVR monitor is ready.

Ready response shape:

```json
{
  "status": "ready",
  "ready": true,
  "dvrs": [
    {
      "id": "main",
      "name": "Main DVR",
      "monitoring_status": "healthy",
      "freshness_status": "healthy",
      "connected": true,
      "reason": "Freshness updates are current",
      "last_freshness_at": "2026-04-26T00:00:00+00:00",
      "freshness_age_seconds": 12.0,
      "version": "2026.04.20.0213",
      "version_compatible": true,
      "version_warning": null
    }
  ],
  "stale_threshold_seconds": 300,
  "tested_version_range": {
    "min": "2024.01.01",
    "max": "2026.04.20"
  }
}
```

Degraded responses use the same shape with `"status":"degraded"` and `"ready":false`.

Status codes:

* `200` when at least one enabled DVR exists and all enabled DVR watchdog entries are ready.
* `503` when no enabled DVR is ready, when a DVR has no watchdog state yet, or when any enabled DVR is stale, dead, missing, or otherwise not ready.

What it checks:

* Enabled DVR records from the persisted settings model.
* The core watchdog snapshot from `/config/watchdog_status.json` through `load_watchdog_snapshot()`.
* Per DVR freshness and monitor state as last published by the core process.
* DVR connection state only as recorded in the watchdog snapshot. The readiness route does not make live DVR HTTP calls.
* Tested Channels DVR version range is included for context. Per-DVR `version`, `version_compatible`, and `version_warning` fields may be present when recent cached version data exists, but the route does not perform a live version check.

What it does not check directly:

* It does not run a database query.
* It does not query supervisor.
* It does not directly inspect the core process. Core health is inferred from the watchdog state it publishes.

`GET /api/health` is a legacy health route with a smaller response. It uses the same monitoring summary, returns `200` when ready, and returns `503` with `"status":"degraded"` when monitoring is not ready.

## Kubernetes style probes

Use the three implemented `/healthz/*` routes for Kubernetes style probes:

```yaml
livenessProbe:
  httpGet:
    path: /healthz/live
    port: http

readinessProbe:
  httpGet:
    path: /healthz/ready
    port: http

startupProbe:
  httpGet:
    path: /healthz/startup
    port: http
```

Behavior:

* Liveness should stay green as long as the UI backend process can serve HTTP. It is intentionally shallow so Kubernetes does not restart the container for transient DVR or core monitor issues.
* Readiness goes green only when the watchdog summary says all enabled DVR monitors are ready. A stale, missing, or dead monitor returns `503` and should remove the pod from service.
* Startup stays `503` until backend startup finishes, then returns `200`.

These routes are unauthenticated. They are also exempt from the standard API rate limiter.

## `channelwatch doctor` CLI

The CLI source is `app/core/cli/doctor.py`. The callable entry point is `run(argv: list[str] | None = None) -> None`, and the parser program name is `channelwatch doctor`.

Invoke it inside a running container:

```bash
docker exec <container_name_or_id> channelwatch doctor config-check
docker exec <container_name_or_id> channelwatch doctor diagnose
docker exec <container_name_or_id> channelwatch doctor debug bundle --output /config/channelwatch_debug_bundle.zip
```

Available commands:

* `config-check` dry runs settings validation through both the core loader and UI schema loader. It reports how many DVRs each loader accepted.
* `diagnose` validates configured, enabled, non deleted DVRs. It checks TCP connectivity, reads each DVR `/status` response, checks DVR version compatibility, and verifies `/api/v1/channels` authentication behavior with the configured DVR API key when present.
* `debug bundle` writes a sanitized debug bundle zip to the requested output path. The default output path is `channelwatch_debug_bundle.zip` in the current working directory.
* `rotate-encryption-key` rotates the local encryption key and re-encrypts stored DVR API keys.
* `reset-admin-password` resets an RBAC admin password when secure login is active.

Exit behavior:

* Commands return exit code `0` on success.
* `config-check` exits `1` when settings cannot be loaded or validated.
* `diagnose` exits `1` when no DVRs are configured, settings cannot be loaded, connectivity fails, `/status` fails, version compatibility fails, or required DVR auth fails.
* `debug bundle` returns `0` when the bundle is written. Unhandled write or bundle creation exceptions propagate as process failures.
* Running `channelwatch doctor` without a subcommand prints help and exits `1`.

Sample output:

```text
Config check passed: core loader accepted 2 DVR(s) and UI schema accepted 2 DVR(s).
```

```text
Checking Living Room (192.0.2.10:8089)...
  OK version: 2026.01.01
  OK auth: Auth check passed using the configured DVR API key.
Diagnosis completed successfully for 1 DVR(s).
```

```text
Debug bundle written to /config/channelwatch_debug_bundle.zip
```

Current scope notes:

* `doctor diagnose` validates DVR reachability, DVR auth, and DVR version support. It does not query supervisor.
* `doctor config-check` validates config loading. It does not perform a separate filesystem permission audit beyond the reads required by the loaders.
* Supervisor health is visible through `GET /api/system-info` and the Diagnostics UI as `core_status`, not through `doctor diagnose`.

## Debug bundle

Debug bundles are available from the admin only API endpoint and from the CLI:

```bash
curl -sS -H "X-API-Key: $API_KEY" -o channelwatch_debug.zip "$BASE_URL/api/v1/debug/bundle"
docker exec <container_name_or_id> channelwatch doctor debug bundle --output /config/channelwatch_debug_bundle.zip
```

The implemented API path is `GET /api/v1/debug/bundle`. It returns `application/zip` with a filename like `channelwatch_debug_20260426T000000Z.zip`.

The bundle is generated under a timestamped top level folder. It contains:

* `manifest.json`, with bundle type, schema version, creation time, app version, CPU architecture, enabled DVR count, privacy note, and artifact list.
* `settings_sanitized.json`, a sanitized copy of `/config/settings.json`.
* `logs/app.log`, the last 500 lines of `/config/channelwatch.log` with log redaction applied.
* `health_snapshot.json`, currently containing only `dvr_count` for enabled, non deleted DVRs.

Sanitization guarantees from the implementation:

* Top level `api_key`, Apprise credential fields, and `error_reporting_dsn` are replaced with `****` when populated.
* DVR `host`, `port`, and `api_key` fields are replaced with `****`.
* Webhook `url` and `secret` fields are replaced with `****`.
* Empty sensitive fields stay empty.
* `encryption.key` is excluded entirely.
* `channelwatch.db` is excluded entirely.
* Raw session state files are excluded.
* Log lines replace IPv4 addresses with `[REDACTED_IP]` and full HTTP(S) URL tokens with `[REDACTED_URL]`.

Current bundle limits:

* The bundle does not include container environment variables, redacted or otherwise.
* It does not include a full system inventory. System details are limited to app version, CPU architecture, and enabled DVR count in the manifest and health snapshot.
* The API generates the zip in memory and returns it to the caller. The backend does not retain API generated bundles.
* The CLI writes the zip to the path supplied by `--output`; retention for CLI bundles is whatever the operator keeps or deletes on disk.

## Diagnostics page

The Diagnostics view is rendered by `app/ui/components/diagnostics-panel.tsx`. The app navigates to it with the `diagnostics` hash view, so users normally reach it from the sidebar or by opening `/#diagnostics` in the web UI.

The page shows:

* A debug bundle download button that calls `GET /api/v1/debug/bundle`.
* A diagnostics export button that downloads a local text report with system details, DVR status, health warnings, test results, and recent log lines.
* A live log terminal backed by `GET /api/logs`, with line count selection, text filtering, level filters, pause and resume, copy, and log download through `GET /api/logs/download`.
* System information from `GET /api/system-info`, including ChannelWatch version, timezone, core status from supervisor, uptime, log retention, active notification providers, aggregate storage, and per DVR connection and monitoring state.
* Connectivity and notification test actions using `POST /api/run_test/{test_name_url}`.
* Health warnings for offline DVRs, degraded monitoring, missing DVR configuration, no notification providers, and no enabled alert types.

## Source paths

* `app/ui/backend/main.py`, probe routes, `/api/health`, `/api/system-info`, and debug bundle API handler.
* `app/core/watchdog.py`, watchdog snapshot loading and readiness summary logic.
* `app/core/cli/doctor.py`, doctor CLI commands and exit behavior.
* `app/core/helpers/debug_bundle.py`, bundle contents and sanitization.
* `app/ui/components/diagnostics-panel.tsx`, Diagnostics UI.
* `app/ui/components/dashboard.tsx`, hash based diagnostics navigation.
* `app/ui/lib/api.ts`, frontend calls for system info, tests, and debug bundle download.

## See also

* `docs/reference/api.md`, endpoint catalog.
* `docs/reference/logs-metrics.md`, logs and metrics reference.
* `docs/how-to/troubleshoot-notifications.md`, notification troubleshooting.
