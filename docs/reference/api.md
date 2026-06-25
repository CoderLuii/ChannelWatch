# FastAPI endpoint reference

This reference catalogs the HTTP routes registered by `app/ui/backend/main.py`, including the `/api/v1/auth` routes registered by the in-file auth router. There are no separate backend router modules in the v0.9 release.

Prefix note: `/api/` is the legacy v0.7 compatible surface. `/api/v1/` is the canonical v1 API namespace for new integrations.

OpenAPI JSON is available at `/openapi.json` when the UI backend is running. Routes marked `include_in_schema=False`, static mounts, and the conditional `/` fallback may not appear in that generated schema.

## Authentication and rate limits

| Mode | How to call | Notes |
| --- | --- | --- |
| No auth | Call directly | Used by health checks, setup status, login, setup, security status, token feeds, and the UI fallback. |
| API key | Send `X-API-Key: <key>` | Required for protected `/api/*` routes when effective auth mode is `api_key`. |
| RBAC session | Send `channelwatch_session` cookie | Used when effective auth mode is `rbac`. State changing session requests also need `X-CSRF-Token` when the security middleware applies. |
| RBAC role | `viewer`, `operator`, or `admin` | Routes with `require_role(...)` enforce the listed minimum role only when RBAC is enabled. API key fallback bypasses role checks when legacy fallback is active. |

Rate limits apply to most `/api/*` requests: 120 read requests and 30 write requests per client per 60 seconds. Exempt paths are `/api/ping`, `/api/health`, `/healthz/ready`, `/healthz/live`, `/healthz/startup`, and `/metrics`.

## Webhook endpoints

There are no inbound webhook HTTP endpoints in `main.py`. Outbound webhook destinations are configured through `AppSettings.webhooks` on `GET /api/settings` and `POST /api/settings`, and delivery history is exposed through `GET /api/v1/notification-log`.

## System

### `GET /api/ping`

| Field | Value |
| --- | --- |
| Function | `ping` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200 |
| Rate limit applies | no |

Example request:

```bash
curl -sS "$BASE_URL/api/ping"
```

Example response:

```json
{"status":"ok"}
```

### `GET /api/health`

| Field | Value |
| --- | --- |
| Function | `health` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 503 |
| Rate limit applies | no |

Example request:

```bash
curl -sS "$BASE_URL/api/health"
```

Example response:

```json
{"status":"ok","ready":true,"dvrs":[]}
```

### `GET /healthz/live`

| Field | Value |
| --- | --- |
| Function | `healthz_live` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200 |
| Rate limit applies | no |

Example request:

```bash
curl -sS "$BASE_URL/healthz/live"
```

Example response:

```json
{"status":"ok"}
```

### `GET /healthz/ready`

| Field | Value |
| --- | --- |
| Function | `healthz_ready` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 503 |
| Rate limit applies | no |

Example request:

```bash
curl -sS "$BASE_URL/healthz/ready"
```

Example response:

```json
{"status":"ready","ready":true,"dvrs":[{"id":"main","name":"Main DVR","monitoring_status":"healthy","freshness_status":"healthy","connected":true,"reason":"Freshness updates are current","last_freshness_at":"2026-04-26T00:00:00+00:00","freshness_age_seconds":12.0,"version":"2026.04.20.0213","version_compatible":true,"version_warning":null}],"stale_threshold_seconds":300,"tested_version_range":{"min":"2024.01.01","max":"2026.04.20"}}
```

Per-DVR `version`, `version_compatible`, and `version_warning` values are cached from existing DVR health/version checks. The readiness route still stays lightweight and does not make a fresh DVR HTTP call just to populate those fields.

### `GET /healthz/startup`

| Field | Value |
| --- | --- |
| Function | `healthz_startup` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 503 |
| Rate limit applies | no |

Example request:

```bash
curl -sS "$BASE_URL/healthz/startup"
```

Example response:

```json
{"status":"ready"}
```

### `GET /api/about`

| Field | Value |
| --- | --- |
| Function | `get_about_info` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `AboutInfo` |
| Status codes | 200, 401, 403, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/about"
```

Example response:

```json
{"app_name":"ChannelWatch","version":"0.9.10","developer":"CoderLuii","description":"Channels DVR monitoring tool for real-time notifications.","github_url":"https://github.com/CoderLuii/ChannelWatch","dockerhub_url":"https://hub.docker.com/r/coderluii/channelwatch"}
```

### `GET /metrics`

| Field | Value |
| --- | --- |
| Function | `metrics` |
| Auth requirement | api_key or RBAC session when auth is enabled |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `Prometheus text` |
| Status codes | 200, 401 |
| Rate limit applies | no |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/metrics"
```

Example response:

```json
{"content_type":"text/plain","example":"channelwatch_uptime_seconds 123"}
```

### `GET /api/v1/security/status`

| Field | Value |
| --- | --- |
| Function | `security_status` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `SecurityStatusResponse` |
| Status codes | 200, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS "$BASE_URL/api/v1/security/status"
```

Example response:

```json
{"persisted_mode":"rbac","configured_mode":"rbac","effective_mode":"rbac","setup_required":false,"runtime_auth_override_active":false,"api_key_fallback_active":false,"rbac_enabled":true,"session_auth_available":true,"session_setup_required":false,"security_mode":"RBAC_ONLY","auth_disabled":false,"api_key_configured":false,"encrypted_dvr_api_keys_at_rest":true,"encryption_key_path":"/config/encryption.key","feeds":{"implemented":true,"ics_enabled":true,"rss_enabled":true,"atom_enabled":true}}
```

## Settings

### `GET /api/settings`

| Field | Value |
| --- | --- |
| Function | `get_settings_endpoint` |
| Auth requirement | api_key or RBAC session when configured |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `AppSettings` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/settings"
```

Example response:

```json
{"dvr_servers":[{"id":"main","name":"Main DVR","host":"192.0.2.10","port":8089,"enabled":true}],"tz":"America/Los_Angeles","log_level":1,"api_key":"****"}
```

### `POST /api/settings`

| Field | Value |
| --- | --- |
| Function | `update_settings_endpoint` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | operator |
| Request body schema | `AppSettings` |
| Response body schema | `object` |
| Status codes | 200, 400, 401, 403, 422, 429 |
| Rate limit applies | yes |

`dvr_servers` host and port values are rejected with `400` when they fail the same DVR safety policy used by the connection-test endpoint.

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" "$BASE_URL/api/settings" -d '{"tz":"America/Los_Angeles","dvr_servers":[]}'
```

Example response:

```json
{"message":"Settings saved successfully"}
```

### `POST /api/regenerate-api-key`

| Field | Value |
| --- | --- |
| Function | `regenerate_api_key` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/regenerate-api-key"
```

Example response:

```json
{"api_key":"new-generated-token"}
```

## DVR management

### `GET /api/discover-servers`

| Field | Value |
| --- | --- |
| Function | `discover_servers` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/discover-servers"
```

Example response:

```json
{"servers":[{"host":"192.0.2.10","port":8089,"name":"Channels DVR","version":""}],"error":null}
```

### `POST /api/v1/discovery/scan`

| Field | Value |
| --- | --- |
| Function | `discovery_scan_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | operator |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/discovery/scan"
```

Example response:

```json
{"servers":[{"host":"192.0.2.10","port":8089,"display_name_suggestion":"Channels DVR"}],"manual_add_available":true}
```

### `POST /api/v1/dvrs/test-connection`

| Field | Value |
| --- | --- |
| Function | `test_dvr_connection` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | operator |
| Request body schema | `_DvrConnectionTestRequest` |
| Response body schema | `object` |
| Status codes | 200, 400, 401, 403, 422, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" "$BASE_URL/api/v1/dvrs/test-connection" -d '{"host":"192.168.1.100","port":8089}'
```

Example response:

```json
{"success":true,"name":"Channels DVR","version":"2025.01.01"}
```

### `GET /api/dvrs/archived`

| Field | Value |
| --- | --- |
| Function | `list_archived_dvrs` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/dvrs/archived"
```

Example response:

```json
{"archived":[{"id":"old","name":"Old DVR","deleted_at":"2026-04-01T00:00:00Z"}]}
```

### `POST /api/dvrs/{dvr_id}/soft-delete`

| Field | Value |
| --- | --- |
| Function | `soft_delete_dvr_endpoint` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 404, 409, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/dvrs/main/soft-delete"
```

Example response:

```json
{"message":"DVR 'main' soft-deleted"}
```

### `POST /api/dvrs/{dvr_id}/restore`

| Field | Value |
| --- | --- |
| Function | `restore_dvr_endpoint` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 404, 409, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/dvrs/main/restore"
```

Example response:

```json
{"message":"DVR 'main' restored"}
```

### `DELETE /api/dvrs/{dvr_id}`

| Field | Value |
| --- | --- |
| Function | `hard_delete_dvr_endpoint` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X DELETE -H "X-API-Key: $API_KEY" "$BASE_URL/api/dvrs/main"
```

Example response:

```json
{"message":"DVR 'main' permanently deleted"}
```

### `GET /api/v1/dvrs`

| Field | Value |
| --- | --- |
| Function | `list_dvrs_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `List[DvrListItem]` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/dvrs"
```

Example response:

```json
[{"id":"main","name":"Main DVR","host":"192.0.2.10","port":8089,"enabled":true}]
```

### `GET /api/v1/dvrs/{dvr_id}`

| Field | Value |
| --- | --- |
| Function | `get_dvr_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `DVRStatus` |
| Status codes | 200, 401, 403, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/dvrs/main"
```

Example response:

```json
{"id":"main","name":"Main DVR","host":"192.0.2.10","port":8089,"connected":true,"version":"2025.01.01","version_compatible":true,"disk_usage_percent":42,"library_shows":10,"library_movies":20,"library_episodes":30}
```

### `GET /api/v1/dvrs/{dvr_id}/streams`

| Field | Value |
| --- | --- |
| Function | `get_dvr_streams_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/dvrs/main/streams"
```

Example response:

```json
{"dvr_id":"main","dvr_name":"Main DVR","total":1,"watching":[{"device":"Living Room","channel":"NBC","image":""}],"recording":[],"subtitle":"Living Room watching NBC","image":""}
```

### `GET /api/v1/dvrs/{dvr_id}/system-info`

| Field | Value |
| --- | --- |
| Function | `get_dvr_system_info_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `PerDvrSystemInfo` |
| Status codes | 200, 401, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/dvrs/main/system-info"
```

Example response:

```json
{"dvr_id":"main","dvr_name":"Main DVR","host":"192.0.2.10","port":8089,"connected":true,"version":"2025.01.01","disk_usage_percent":42,"disk_usage_gb":420.0,"disk_total_gb":1000.0,"disk_free_gb":580.0,"disk_severity":"normal","library_shows":10,"library_movies":20,"library_episodes":30}
```

### `GET /api/v1/dvrs/{dvr_id}/health`

| Field | Value |
| --- | --- |
| Function | `get_dvr_health_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `DvrHealthResponse` |
| Status codes | 200, 401, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/dvrs/main/health"
```

Example response:

```json
{"dvr_id":"main","dvr_name":"Main DVR","host":"192.0.2.10","port":8089,"connected":true,"disk_status":"normal","last_checked":"2026-04-26T00:00:00+00:00","freshness_status":"fresh","monitoring_status":"ready","monitoring_ready":true}
```

### `GET /api/recordings/upcoming`

| Field | Value |
| --- | --- |
| Function | `get_upcoming_recordings` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `List[RecordingInfo]` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/recordings/upcoming?limit=5"
```

Example response:

```json
[{"id":"rec-1","title":"Example Show","start_time":1770000000,"end_time":1770003600,"channel":"NBC","scheduled_time":"Today at 08:00 PM","image":"","dvr_id":"main","dvr_name":"Main DVR"}]
```

### `GET /api/v1/dvrs/{dvr_id}/recordings/upcoming`

| Field | Value |
| --- | --- |
| Function | `get_dvr_upcoming_recordings_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `List[RecordingInfo]` |
| Status codes | 200, 401, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/dvrs/main/recordings/upcoming?limit=5"
```

Example response:

```json
[{"id":"rec-1","title":"Example Show","start_time":1770000000,"end_time":0,"channel":"NBC","scheduled_time":"Today at 08:00 PM","image":"","dvr_id":"main","dvr_name":"Main DVR"}]
```

### `GET /api/recordings/active`

| Field | Value |
| --- | --- |
| Function | `get_active_recordings_count` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `int` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/recordings/active"
```

Example response:

```json
2
```

### `GET /api/streams/active`

| Field | Value |
| --- | --- |
| Function | `get_active_streams_count` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `int` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/streams/active"
```

Example response:

```json
1
```

### `GET /api/streams/details`

| Field | Value |
| --- | --- |
| Function | `get_active_streams_details` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/streams/details"
```

Example response:

```json
{"total":1,"watching":[{"device":"Living Room","channel":"NBC","image":""}],"recording":[],"subtitle":"Living Room watching NBC","image":""}
```

## Activity and history

### `GET /api/recent-activity`

| Field | Value |
| --- | --- |
| Function | `get_recent_activity` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `List[AlertHistoryItem]` |
| Status codes | 200, 401, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/recent-activity?hours=24&limit=5"
```

Example response:

```json
[{"id":"evt-1","type":"watching_channel","title":"Channel started","message":"Living Room is watching NBC","timestamp":"2026-04-26T00:00:00+00:00","channel_name":"NBC","device_name":"Living Room","dvr_id":"main","dvr_name":"Main DVR"}]
```

### `GET /api/activity-history`

| Field | Value |
| --- | --- |
| Function | `get_activity_history` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `ActivityHistoryResponse` |
| Status codes | 200, 400, 401, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/activity-history?offset=0&limit=10&type=channel&sort=desc"
```

Example response:

```json
{"items":[{"id":"evt-1","type":"watching_channel","title":"Channel started","message":"Living Room is watching NBC","timestamp":"2026-04-26T00:00:00+00:00"}],"total":1,"offset":0,"limit":10}
```

### `GET /api/v1/dvrs/{dvr_id}/activity-history`

| Field | Value |
| --- | --- |
| Function | `get_dvr_activity_history_v1` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `ActivityHistoryResponse` |
| Status codes | 200, 400, 401, 404, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/dvrs/main/activity-history?offset=0&limit=10&sort=desc"
```

Example response:

```json
{"items":[{"id":"evt-1","type":"watching_channel","title":"Channel started","message":"Living Room is watching NBC","timestamp":"2026-04-26T00:00:00+00:00","dvr_id":"main"}],"total":1,"offset":0,"limit":10}
```

### `POST /api/clear-activity-history`

| Field | Value |
| --- | --- |
| Function | `clear_activity_history` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | operator |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/clear-activity-history"
```

Example response:

```json
{"message":"Activity history cleared successfully"}
```

### `GET /api/v1/history/export`

| Field | Value |
| --- | --- |
| Function | `export_history_csv` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `CSV stream` |
| Status codes | 200, 400, 401, 429, 503 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/history/export?format=csv"
```

Example response:

```json
{"content_type":"text/csv","example":"id,dvr_id,dvr_name,event_type,title,message,timestamp"}
```

### `GET /api/v1/notification-log`

| Field | Value |
| --- | --- |
| Function | `get_notification_log` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `NotificationLogResponse` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/notification-log?offset=0&limit=10&status=sent"
```

Example response:

```json
{"items":[{"id":1,"dvr_id":"main","activity_event_id":"evt-1","provider_type":"webhook","channel_id":"default","channel":"default","event_type":"watching_channel","status":"sent","retry_count":0,"payload_size":512,"error":null,"sent_at":"2026-04-26T00:00:00+00:00"}],"total":1,"offset":0,"limit":10}
```

## Diagnostics

### `GET /api/system-info`

| Field | Value |
| --- | --- |
| Function | `get_system_info` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | viewer |
| Request body schema | `none` |
| Response body schema | `SystemInfo` |
| Status codes | 200, 401, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/system-info"
```

Example response:

```json
{"channelwatch_version":"0.9.10","channels_dvr_host":"192.0.2.10","channels_dvr_port":8089,"timezone":"America/Los_Angeles","disk_usage_percent":42,"disk_severity":"normal","core_status":"Running","library_shows":10,"library_movies":20,"library_episodes":30,"dvr_status":[]}
```

### `GET /api/v1/debug/bundle`

| Field | Value |
| --- | --- |
| Function | `download_debug_bundle` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `application/zip` |
| Status codes | 200, 401, 403, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" -o channelwatch_debug.zip "$BASE_URL/api/v1/debug/bundle"
```

Example response:

```json
{"content_type":"application/zip","file":"channelwatch_debug.zip"}
```

### `GET /api/logs`

| Field | Value |
| --- | --- |
| Function | `get_logs` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | operator |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/logs?lines=50"
```

Example response:

```json
{"lines":["2026-04-26 00:00:00 INFO ChannelWatch started"]}
```

### `GET /api/logs/download`

| Field | Value |
| --- | --- |
| Function | `download_logs` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | operator |
| Request body schema | `none` |
| Response body schema | `text/plain file` |
| Status codes | 200, 401, 403, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" -o channelwatch.log "$BASE_URL/api/logs/download"
```

Example response:

```json
{"content_type":"text/plain","file":"channelwatch.log"}
```

### `POST /api/run_test/{test_name_url}`

| Field | Value |
| --- | --- |
| Function | `trigger_test_endpoint` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | operator |
| Request body schema | `none` |
| Response body schema | `TestResult` |
| Status codes | 200, 401, 403, 429, 501 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/run_test/Test_Connectivity"
```

Example response:

```json
{"test_name":"Test Connectivity","success":true,"message":"Test 'Test Connectivity' succeeded"}
```

### `POST /api/restart_core`

| Field | Value |
| --- | --- |
| Function | `restart_core_process` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 202, 401, 403, 429, 500, 503 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/restart_core"
```

Example response:

```json
{"message":"Restart command sent to process 'core'."}
```

### `POST /api/restart_container`

| Field | Value |
| --- | --- |
| Function | `restart_container` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 202, 401, 403, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" "$BASE_URL/api/restart_container"
```

Example response:

```json
{"message":"Restart initiated. The application will be unavailable for a few moments."}
```

## Backup and restore

### `GET /api/v1/backup/download`

| Field | Value |
| --- | --- |
| Function | `download_backup` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `application/zip` |
| Status codes | 200, 401, 403, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" -o channelwatch_backup.zip "$BASE_URL/api/v1/backup/download"
```

Example response:

```json
{"content_type":"application/zip","file":"channelwatch_backup.zip"}
```

### `POST /api/v1/backup/restore`

| Field | Value |
| --- | --- |
| Function | `restore_backup` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `multipart/form-data file UploadFile` |
| Response body schema | `object` |
| Status codes | 200, 400, 401, 403, 409 (`ERR_RESTORE_SCHEMA_AHEAD` for newer-schema backups), 422, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "X-API-Key: $API_KEY" -F "file=@channelwatch_backup.zip" "$BASE_URL/api/v1/backup/restore"
```

Example response:

```json
{"message":"Restore completed. Core process hot-reloaded.","manifest":{"version":1,"created_at":"2026-04-26T00:00:00Z"}}
```

## Update Center

Update Center routes are admin-only when auth is enabled. If `CW_DISABLE_AUTH=true`, they follow the rest of the app's auth-disabled behavior: anyone who can reach the UI can run admin actions. Do not expose auth-disabled installs to untrusted networks.

### `GET /api/v1/update/status`

| Field | Value |
| --- | --- |
| Function | `update_status` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `UpdateStatus` |
| Status codes | 200, 401, 403, 429, 500 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/update/status"
```

Example response:

```json
{"current_version":"0.9.10","runtime_abi":"channelwatch-runtime-v1","settings_schema_version":7,"active_bundle":null,"latest":null,"update_available":false,"image_required":false,"last_job":null,"rollback_available":false,"auth_disabled_warning":false}
```

### `POST /api/v1/update/check`

| Field | Value |
| --- | --- |
| Function | `update_check` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `UpdateStatus` |
| Status codes | 200, 401, 403, 409 (`ERR_UPDATE_LOCKED`), 429, 500 |
| Rate limit applies | yes |

This route fetches the trusted public ChannelWatch update manifest and stores the last trusted manifest under `/config/channelwatch-runtime/latest.json`.

### `POST /api/v1/update/apply`

| Field | Value |
| --- | --- |
| Function | `update_apply` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `{ "version": "0.9.10" }` |
| Response body schema | `UpdateJob` |
| Status codes | 202, 401, 403, 409 (`ERR_UPDATE_LOCKED` or `ERR_UPDATE_IMAGE_REQUIRED`), 429, 500 |
| Rate limit applies | yes |

`apply` verifies the signed manifest, signed bundle, SHA256 digest, runtime ABI, settings schema, and bundle allowlist before writing an active bundle pointer. Compatible updates create a pre-update backup and then restart ChannelWatch.

### `GET /api/v1/update/jobs/{job_id}`

| Field | Value |
| --- | --- |
| Function | `update_job` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `UpdateJob` |
| Status codes | 200, 401, 403, 404, 429 |
| Rate limit applies | yes |

The route returns the persisted last job only when `{job_id}` matches the stored operation.

### `POST /api/v1/update/rollback`

| Field | Value |
| --- | --- |
| Function | `update_rollback` |
| Auth requirement | api_key or RBAC session |
| RBAC role required | admin |
| Request body schema | `none` |
| Response body schema | `UpdateJob` |
| Status codes | 202, 401, 403, 409, 429, 500 |
| Rate limit applies | yes |

Rollback restores the previous active bundle pointer, or removes `active.json` to fall back to the image app, then restarts ChannelWatch.

## Auth

### `POST /api/v1/auth/login`

| Field | Value |
| --- | --- |
| Function | `auth_login` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `_LoginRequest` |
| Response body schema | `object` |
| Status codes | 200, 401, 429, 501, 503 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "Content-Type: application/json" -c cookies.txt "$BASE_URL/api/v1/auth/login" -d '{"username":"admin","password":"change-me"}'
```

Example response:

```json
{"username":"admin","role":"admin","csrf_token":"csrf-token"}
```

### `POST /api/v1/auth/logout`

| Field | Value |
| --- | --- |
| Function | `auth_logout` |
| Auth requirement | none, optional RBAC session cookie |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -b cookies.txt "$BASE_URL/api/v1/auth/logout"
```

Example response:

```json
{"message":"Logged out"}
```

### `GET /api/v1/auth/whoami`

| Field | Value |
| --- | --- |
| Function | `auth_whoami` |
| Auth requirement | RBAC session cookie |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `_WhoAmIResponse` |
| Status codes | 200, 401, 429, 503 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -b cookies.txt "$BASE_URL/api/v1/auth/whoami"
```

Example response:

```json
{"authenticated":true,"rbac_enabled":true,"username":"admin","role":"admin"}
```

### `POST /api/v1/auth/change-credentials`

| Field | Value |
| --- | --- |
| Function | `auth_change_credentials` |
| Auth requirement | RBAC session cookie plus CSRF header |
| RBAC role required | none |
| Request body schema | `_ChangeCredentialsRequest` |
| Response body schema | `object` |
| Status codes | 200, 401, 403, 409, 422, 429, 501, 503 |
| Rate limit applies | yes |

Missing or invalid `X-CSRF-Token` values return `403`.

Example request:

```bash
curl -sS -X POST -b cookies.txt -H "X-CSRF-Token: $CSRF_TOKEN" -H "Content-Type: application/json" "$BASE_URL/api/v1/auth/change-credentials" -d '{"current_password":"change-me","username":"admin","new_password":"new-pass"}'
```

Example response:

```json
{"message":"Credentials updated","username":"admin"}
```

### `GET /api/v1/auth/setup-status`

| Field | Value |
| --- | --- |
| Function | `auth_setup_status` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `SetupStatusResponse` |
| Status codes | 200, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS "$BASE_URL/api/v1/auth/setup-status"
```

Example response:

```json
{"persisted_mode":null,"configured_mode":"setup","effective_mode":"setup","setup_required":true,"runtime_auth_override_active":false,"api_key_fallback_active":false,"rbac_enabled":false,"session_auth_available":false,"session_setup_required":true,"needs_setup":true,"current_mode":"setup","available_modes":["rbac","none"]}
```

### `POST /api/v1/auth/setup`

| Field | Value |
| --- | --- |
| Function | `auth_setup` |
| Auth requirement | none while setup is required |
| RBAC role required | none |
| Request body schema | `_SetupRequest` |
| Response body schema | `object` |
| Status codes | 201, 409, 422, 429, 501, 503 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS -X POST -H "Content-Type: application/json" -c cookies.txt "$BASE_URL/api/v1/auth/setup" -d '{"mode":"rbac","username":"admin","password":"change-me"}'
```

Example response:

```json
{"message":"Admin user created","username":"admin","csrf_token":"csrf-token"}
```

## Feeds

The canonical feed paths are under `/api/v1/feeds/`. Bare-path aliases (`/api/v1/calendar.ics`, `/api/v1/feed.rss`, `/api/v1/feed.atom`) are provided for older integrations and feed subscribers that pre-date the feed namespacing; they reuse the same handlers, query parameters, token checks, and rate limits as the canonical paths. The feed renderers are implemented in-tree with standard-library XML/date helpers and manual ICS/RSS/Atom formatting, so `icalendar` and `feedgen` are intentionally not required runtime dependencies.

### `GET /api/v1/feeds/calendar.ics`

| Field | Value |
| --- | --- |
| Function | `get_calendar_feed` |
| Alias | `GET /api/v1/calendar.ics` |
| Auth requirement | token query parameter |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `text/calendar` |
| Status codes | 200, 401, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS "$BASE_URL/api/v1/feeds/calendar.ics?token=$ICS_TOKEN"
```

Example response:

```json
{"content_type":"text/calendar","example":"BEGIN:VCALENDAR"}
```

### `GET /api/v1/feeds/activity.rss`

| Field | Value |
| --- | --- |
| Function | `get_activity_rss_feed` |
| Alias | `GET /api/v1/feed.rss` |
| Auth requirement | token query parameter |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `application/rss+xml` |
| Status codes | 200, 401, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS "$BASE_URL/api/v1/feeds/activity.rss?token=$RSS_TOKEN"
```

Example response:

```json
{"content_type":"application/rss+xml","example":"<rss version=\"2.0\">"}
```

### `GET /api/v1/feeds/activity.atom`

| Field | Value |
| --- | --- |
| Function | `get_activity_atom_feed` |
| Alias | `GET /api/v1/feed.atom` |
| Auth requirement | token query parameter |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `application/atom+xml` |
| Status codes | 200, 401, 404, 429 |
| Rate limit applies | yes |

Example request:

```bash
curl -sS "$BASE_URL/api/v1/feeds/activity.atom?token=$RSS_TOKEN"
```

Example response:

```json
{"content_type":"application/atom+xml","example":"<feed xmlns=\"http://www.w3.org/2005/Atom\">"}
```

## UI fallback

### `GET /`

| Field | Value |
| --- | --- |
| Function | `fallback_root` |
| Auth requirement | none |
| RBAC role required | none |
| Request body schema | `none` |
| Response body schema | `object` |
| Status codes | 200 |
| Rate limit applies | no |

Example request:

```bash
curl -sS "$BASE_URL/"
```

Example response:

```json
{"message":"Frontend UI not found."}
```

## Route coverage summary

This document includes 57 API endpoint entries: 51 canonical app endpoint headings from the current v0.9 route set, including health and metrics probe endpoints that are hidden from OpenAPI, plus 6 included `/api/v1/auth` router headings. The conditional `/` UI fallback is listed separately above and is intentionally not counted as an API endpoint entry. The 3 feed alias routes are documented as alias rows under their canonical feed endpoints. There are no WebSocket endpoints in `main.py`.

## See also

* `docs/reference/settings.md`
* `docs/reference/webhook.md`
* `docs/reference/health-diagnostics.md`
