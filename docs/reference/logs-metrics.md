# Logs and metrics reference

This reference describes the observability surfaces exposed by ChannelWatch: runtime logs, Prometheus metrics, activity history, notification delivery history, and debug bundles.

## Log streams

ChannelWatch writes one application log file in the config volume:

| Stream | Location | Producer | Format | Notes |
| --- | --- | --- | --- | --- |
| Application log | `/config/channelwatch.log` | Core monitor and UI backend through `core.helpers.logging.setup_logging()` | Text by default, JSON when `LOG_FORMAT=json` | The UI exposes this file through `GET /api/logs` and `GET /api/logs/download`. |
| Console output | Container stdout | Core helper `log()` and Python logging handlers | Text by default, JSON when `LOG_FORMAT=json` for records emitted through logging | Useful for Docker logs and supervisor output. |

There is no `/config/logs/*.log` directory in the current implementation. The active file path is `/config/channelwatch.log`.

### Log levels

The helper module `app/core/helpers/logging.py` defines two application levels:

| Setting value | Constant | Meaning |
| --- | --- | --- |
| `1` | `LOG_STANDARD` | Standard operational messages. |
| `2` | `LOG_VERBOSE` | More detailed diagnostics. Messages logged at verbose level are skipped unless `log_level` is `2`. |

`app/core/main.py` reads `log_level` from settings at startup, defaults invalid values to `1`, then calls `set_log_level()`.

### Text and JSON formats

`LOG_FORMAT` controls formatting in `app/core/helpers/logging.py`:

| `LOG_FORMAT` value | File format | Console behavior |
| --- | --- | --- |
| unset or `text` | Lines like `[2026-04-26 12:00:00] [CORE] message` | `log()` also prints text lines to stdout. |
| `json` | One JSON object per line using `JsonFormatter` from `app/core/helpers/structured_log.py` | Logging records include `timestamp`, `level`, `module`, `message`, and optional `dvr_id`, `request_id`, or `user_id`. |

### Rotation and retention

`setup_logging(config_path, retention_days)` uses `TimedRotatingFileHandler` with:

| Policy | Value |
| --- | --- |
| Rotation interval | Midnight, every 1 day. |
| Retained backups | `log_retention_days` from settings, default `7`. |
| Active file | `/config/channelwatch.log`. |

The UI backend also calls `setup_logging(str(CONFIG_DIR), retention_days=settings.log_retention_days or 7)` during startup, where `CONFIG_DIR` defaults to `/config` and follows `CONFIG_PATH` when set, so both long running processes use the same logging helper and retention setting.

## Prometheus metrics

The metrics endpoint is implemented in `app/ui/backend/main.py` as `GET /metrics`. It returns Prometheus text exposition with media type `text/plain; version=0.0.4; charset=utf-8`, is exempt from the backend rate limiter, and requires the configured `X-API-Key` or an authenticated RBAC session whenever auth is enabled.

The current implementation does not instantiate `prometheus_client.Counter`, `Gauge`, or `Histogram`. Instead, it builds text exposition lines directly with `# TYPE ... gauge`. All exposed ChannelWatch metrics are gauges.

| Metric | Type | Labels | Measures |
| --- | --- | --- | --- |
| `channelwatch_uptime_seconds` | gauge | none | Seconds since the UI backend process started. |
| `channelwatch_active_streams` | gauge | none on aggregate sample, plus `dvr_id`, `dvr_name`, `host`, `port` on per DVR samples | Active stream count. The unlabeled sample is the total across configured DVRs. Per DVR samples are counted from each DVR `/dvr` activity map. |
| `channelwatch_core_running` | gauge | none | `1` when supervisor reports the core process as `Running`, otherwise `0`. |
| `channelwatch_configured_dvrs` | gauge | none | Count of enabled DVRs represented in the system info response. |
| `channelwatch_disk_free_bytes` | gauge | `scope="all"` on aggregate sample, plus `dvr_id`, `dvr_name`, `host`, `port` on per DVR samples | Free DVR storage bytes. Aggregate data is summed across configured DVRs. |
| `channelwatch_disk_total_bytes` | gauge | `scope="all"` on aggregate sample, plus `dvr_id`, `dvr_name`, `host`, `port` on per DVR samples | Total DVR storage bytes. Aggregate data is summed across configured DVRs. |
| `channelwatch_disk_used_bytes` | gauge | `scope="all"` on aggregate sample, plus `dvr_id`, `dvr_name`, `host`, `port` on per DVR samples | Used DVR storage bytes. Aggregate data is total minus free. |
| `channelwatch_dvr_connected` | gauge | `dvr_id`, `dvr_name`, `host`, `port` | `1` when the DVR `/status` request succeeds during system info collection, otherwise `0`. |
| `channelwatch_dvr_version_info` | gauge | `dvr_id`, `dvr_name`, `version`, `compatible` | DVR version metadata. The value is always `1`. The `compatible` label is `1`, `0`, or `unknown`. This metric is emitted only for DVRs with a version string. |


## Activity history

Activity history is the user visible timeline used by Recent Activity and Watch History.

| Storage | Path | Used when | Retention |
| --- | --- | --- | --- |
| SQLite database | `/config/channelwatch.db`, table `activity_event` | Preferred read path when the database exists and storage modules import successfully. | `history_retention_days` from settings, default `90`, enforced by the nightly storage maintenance thread. |
| JSON fallback | `/config/activity_history.json` | Legacy and fallback read path when the database is unavailable. Core helper writes still target this file directly in `activity_recorder.py`. | The direct writer keeps the newest 500 records. It does not apply day based pruning. |

The database model is `ActivityEvent` in `app/core/storage/models.py`. The legacy JSON writer lives in `app/core/helpers/activity_recorder.py`.

Recorded activity includes:

| Event type | Source |
| --- | --- |
| `watching_channel` | Channel watching activity. |
| `watching_vod` | VOD playback activity. |
| `recording_event` | Scheduled, started, cancelled, completed, stopped, or updated recording activity. |
| `disk_alert` | Disk space warning, critical, and test activity. |

The API reads activity through:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/recent-activity` | Legacy recent activity list. |
| `GET /api/activity-history` | Legacy paginated activity history with type, search, and sort filters. |
| `GET /api/v1/dvrs/{dvr_id}/activity-history` | Per DVR activity history. |
| `GET /api/v1/history/export?format=csv` | CSV export from the activity database. |
| `POST /api/clear-activity-history` | Clears JSON history and database activity rows when the database is available. |

## Notification log

Notification delivery history is separate from activity history. It records delivery attempts and outcomes for Apprise destinations and outbound webhooks.

| Storage | Path | Table | Retention |
| --- | --- | --- | --- |
| SQLite database | `/config/channelwatch.db` | `notification_delivery` | Same `history_retention_days` setting as activity history, default `90`, pruned by nightly storage maintenance. |

The storage model is `NotificationDelivery` in `app/core/storage/models.py`. Inserts and queries are implemented in `app/core/storage/delivery_queries.py`. Notification delivery code writes fields such as `dvr_id`, `activity_event_id`, `provider_type`, `channel`, `event_type`, `status`, `retry_count`, `payload_size`, `error_message`, and `delivered_at`.

Query API:

```text
GET /api/v1/notification-log
```

Supported query parameters are `dvr_id`, `channel`, `status`, `since`, `until`, `offset`, and `limit`. If the activity database is not available, the endpoint returns an empty result set rather than reading from `activity_history.json`.

## Debug bundle

A sanitized debug bundle can be generated in either of these ways:

```bash
channelwatch doctor debug bundle --output channelwatch_debug_bundle.zip
```

```text
GET /api/v1/debug/bundle
```

The API route requires the `admin` role when RBAC is enabled. It returns a zip file named like `channelwatch_debug_YYYYMMDDTHHMMSSZ.zip`.

The bundle generator is `app/core/helpers/debug_bundle.py`. It writes a timestamped directory inside the zip with these artifacts:

| Artifact | Contents |
| --- | --- |
| `manifest.json` | Bundle schema, creation time, app version, architecture, DVR count, privacy note, and artifact list. |
| `settings_sanitized.json` | Sanitized settings from `/config/settings.json`. |
| `logs/app.log` | Last 500 lines from `/config/channelwatch.log` after line level redaction. |
| `health_snapshot.json` | Offline health summary with enabled DVR count. |

Sanitization rules:

| Data | Handling |
| --- | --- |
| API keys, Apprise credentials, email fields, custom provider strings, and error reporting DSN | Replaced with `****` when present. |
| DVR `host`, `port`, and `api_key` | Replaced with `****`. |
| Webhook `url` and `secret` | Replaced with `****`. |
| Log IPv4 addresses | Replaced with `[REDACTED_IP]`. |
| Full HTTP(S) URL tokens in logs | Replaced with `[REDACTED_URL]`. |
| `/config/encryption.key`, `/config/channelwatch.db`, raw session state files | Excluded. |

For privacy boundaries, third-party notification destinations, debug-bundle masking, and GDPR / UK GDPR controller responsibilities in multi-user deployments, see [`../project/PRIVACY.md`](../project/PRIVACY.md).

## See also

- `docs/reference/health-diagnostics.md`
- `docs/reference/api.md`
- `docs/project/PRIVACY.md`
