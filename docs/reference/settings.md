# settings.json reference

ChannelWatch stores persisted application settings in `/config/settings.json`. The UI backend validates this file with `app/ui/backend/schemas.py`; the core monitor reads the same file through `app/core/helpers/config.py`.

Reload behavior values:

| Value | Meaning |
|---|---|
| `restart-required` | Saved through the Settings UI and picked up after the app restarts the core or container. |
| `hot-reload` | Read by the UI backend or browser on the next request, save, or page reload. |
| `core-only` | Used by the core monitor or startup path, not directly edited by the visible Settings UI. |

Environment override notes:

| Environment variable | Setting path | Scope |
|---|---|---|
| `CONFIG_PATH` | Settings file directory | Core and UI backend read `/config` unless this is set. |
| `TZ` | `tz` | Written by the entrypoint and applied by core startup when present. |
| `CHANNELS_DVR_SERVERS` | `dvr_servers` | Bootstrap merge in the entrypoint, then core startup override. Format is `Name@host:port,Name@host:port`. |
| `CHANNELS_DVR_HOST`, `CHANNELS_DVR_PORT` | `dvr_servers` | Deprecated legacy single server bootstrap merge, then core startup override when `CHANNELS_DVR_SERVERS` is absent. |
| `CW_LOG_LEVEL` | `log_level` | Bootstrap only. Ignored by the entrypoint after `settings.json` exists. |
| `CW_APPRISE_DISCORD` | `apprise_discord` | Bootstrap only. |
| `CW_APPRISE_PUSHOVER` | `apprise_pushover` | Bootstrap only. |
| `CW_APPRISE_TELEGRAM` | `apprise_telegram` | Bootstrap only. |
| `CW_APPRISE_EMAIL` | `apprise_email` | Bootstrap only. |
| `CW_APPRISE_EMAIL_TO` | `apprise_email_to` | Bootstrap only. |
| `CW_APPRISE_SLACK` | `apprise_slack` | Bootstrap only. |
| `CW_APPRISE_GOTIFY` | `apprise_gotify` | Bootstrap only. |
| `CW_APPRISE_MATRIX` | `apprise_matrix` | Bootstrap only. |
| `CW_APPRISE_CUSTOM` | `apprise_custom` | Bootstrap only. |
| `CW_ALERT_CHANNEL_WATCHING` | `alert_channel_watching` | Bootstrap only. Boolean accepts `true`, `1`, or `yes` as true. |
| `CW_ALERT_VOD_WATCHING` | `alert_vod_watching` | Bootstrap only. Boolean accepts `true`, `1`, or `yes` as true. |
| `CW_ALERT_DISK_SPACE` | `alert_disk_space` | Bootstrap only. Boolean accepts `true`, `1`, or `yes` as true. |
| `CW_ALERT_RECORDING_EVENTS` | `alert_recording_events` | Bootstrap only. Boolean accepts `true`, `1`, or `yes` as true. |
| `CW_DS_THRESHOLD_PERCENT` | `ds_threshold_percent` | Bootstrap only, deprecated legacy threshold. |
| `CW_DS_THRESHOLD_GB` | `ds_threshold_gb` | Bootstrap only, deprecated legacy threshold. |
| `CW_DS_ALERT_COOLDOWN` | `ds_alert_cooldown` | Bootstrap only. |
| `CW_DISABLE_AUTH` | Runtime auth state | UI backend runtime override, not persisted in `settings.json`. |
| `CW_ADMIN_USER`, `CW_ADMIN_PASS` | Runtime setup credentials | UI backend setup helpers, not persisted in `settings.json`. |

Defaults match between `AppSettings` and `CoreSettings` unless noted below. The entrypoint's first-file template is older and omits fields that the core and UI schema later add during load, migration, or save.

## General tab

### DVR servers

- `dvr_servers` | JSON path: `dvr_servers` | Type: array of objects | Default: `[]` | Description: DVR server connection records. Common object keys are `id`, `name`, `host`, `port`, `enabled`, `api_key`, `overrides`, and optional deletion metadata. | Valid values: array; `port` normally uses `8089`; `overrides` is a settings-key object. | Env override: `CHANNELS_DVR_SERVERS`, or deprecated `CHANNELS_DVR_HOST` plus `CHANNELS_DVR_PORT`. | Reload behavior: `restart-required`.

### Timezone and logs

- `tz` | JSON path: `tz` | Type: string | Default: `"America/Los_Angeles"` | Description: IANA timezone used for timestamps and scheduling display. | Valid values: IANA timezone names from the Settings UI list, plus values accepted by the host timezone database. | Env override: `TZ`. | Reload behavior: `restart-required`.
- `log_level` | JSON path: `log_level` | Type: integer | Default: `1` | Description: Logging verbosity. | Valid values: `1` standard, `2` verbose. | Env override: `CW_LOG_LEVEL` at bootstrap. | Reload behavior: `restart-required`.
- `log_retention_days` | JSON path: `log_retention_days` | Type: integer | Default: `7` | Description: Days to retain log files. | Valid values: greater than `0`. | Env override: none. | Reload behavior: `restart-required`.
- `history_retention_days` | JSON path: `history_retention_days` | Type: integer | Default: `90` | Description: Days to retain activity history records in the database. | Valid values: greater than `0`. | Env override: none. | Reload behavior: `restart-required`.

### Dashboard images

- `stream_card_image` | JSON path: `stream_card_image` | Type: string | Default: `"program"` | Description: Background image source for active stream cards. | Valid values: `program`, `channel`, `none`. | Env override: none. | Reload behavior: `hot-reload`.
- `recording_card_image` | JSON path: `recording_card_image` | Type: string | Default: `"program"` | Description: Background image source for upcoming recording cards. | Valid values: `program`, `none`. | Env override: none. | Reload behavior: `hot-reload`.

## Alerts tab

### Stream counter

- `stream_count` | JSON path: `stream_count` | Type: boolean | Default: `true` | Description: Include total active stream count in channel watching notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `monitor_stale_seconds` | JSON path: `monitor_stale_seconds` | Type: integer | Default: `300` | Description: Seconds before DVR monitoring is treated as stale. Not shown in the Settings UI. | Valid values: greater than or equal to `1`. | Env override: none. | Reload behavior: `core-only`.

### Channel watching

- `alert_channel_watching` | JSON path: `alert_channel_watching` | Type: boolean | Default: `true` | Description: Enables channel watching notifications. | Valid values: `true`, `false`. | Env override: `CW_ALERT_CHANNEL_WATCHING` at bootstrap. | Reload behavior: `restart-required`.
- `cw_image_source` | JSON path: `cw_image_source` | Type: string | Default: `"PROGRAM"` | Description: Image source for channel watching notifications. | Valid values: `PROGRAM`, `CHANNEL`; schema normalizes to uppercase. | Env override: none. | Reload behavior: `restart-required`.
- `cw_channel_name` | JSON path: `cw_channel_name` | Type: boolean | Default: `true` | Description: Include channel name in channel watching notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `cw_channel_number` | JSON path: `cw_channel_number` | Type: boolean | Default: `true` | Description: Include channel number in channel watching notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `cw_program_name` | JSON path: `cw_program_name` | Type: boolean | Default: `true` | Description: Include program title in channel watching notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `cw_device_name` | JSON path: `cw_device_name` | Type: boolean | Default: `true` | Description: Include client device name in channel watching notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `cw_device_ip` | JSON path: `cw_device_ip` | Type: boolean | Default: `true` | Description: Include client IP address in channel watching notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `cw_stream_source` | JSON path: `cw_stream_source` | Type: boolean | Default: `true` | Description: Include stream source in channel watching notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.

### VOD watching

- `alert_vod_watching` | JSON path: `alert_vod_watching` | Type: boolean | Default: `true` | Description: Enables VOD watching notifications. | Valid values: `true`, `false`. | Env override: `CW_ALERT_VOD_WATCHING` at bootstrap. | Reload behavior: `restart-required`.
- `vod_title` | JSON path: `vod_title` | Type: boolean | Default: `true` | Description: Include title in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_episode_title` | JSON path: `vod_episode_title` | Type: boolean | Default: `true` | Description: Include episode title in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_summary` | JSON path: `vod_summary` | Type: boolean | Default: `true` | Description: Include summary text in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_duration` | JSON path: `vod_duration` | Type: boolean | Default: `true` | Description: Include duration in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_progress` | JSON path: `vod_progress` | Type: boolean | Default: `true` | Description: Include playback progress in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_image` | JSON path: `vod_image` | Type: boolean | Default: `true` | Description: Include artwork in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_rating` | JSON path: `vod_rating` | Type: boolean | Default: `true` | Description: Include rating in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_genres` | JSON path: `vod_genres` | Type: boolean | Default: `true` | Description: Include genres in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_cast` | JSON path: `vod_cast` | Type: boolean | Default: `true` | Description: Include cast in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_device_name` | JSON path: `vod_device_name` | Type: boolean | Default: `true` | Description: Include client device name in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_device_ip` | JSON path: `vod_device_ip` | Type: boolean | Default: `true` | Description: Include client IP address in VOD notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.

### Recording events

- `alert_recording_events` | JSON path: `alert_recording_events` | Type: boolean | Default: `true` | Description: Enables recording event notifications. | Valid values: `true`, `false`. | Env override: `CW_ALERT_RECORDING_EVENTS` at bootstrap. | Reload behavior: `restart-required`.
- `rd_alert_scheduled` | JSON path: `rd_alert_scheduled` | Type: boolean | Default: `true` | Description: Send notifications when recordings are scheduled. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_alert_started` | JSON path: `rd_alert_started` | Type: boolean | Default: `true` | Description: Send notifications when recordings start. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_alert_completed` | JSON path: `rd_alert_completed` | Type: boolean | Default: `true` | Description: Send notifications when recordings complete. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_alert_cancelled` | JSON path: `rd_alert_cancelled` | Type: boolean | Default: `true` | Description: Send notifications when recordings are cancelled. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_program_name` | JSON path: `rd_program_name` | Type: boolean | Default: `true` | Description: Include program name in recording event notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_program_desc` | JSON path: `rd_program_desc` | Type: boolean | Default: `true` | Description: Include program description in recording event notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_duration` | JSON path: `rd_duration` | Type: boolean | Default: `true` | Description: Include duration in recording event notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_channel_name` | JSON path: `rd_channel_name` | Type: boolean | Default: `true` | Description: Include channel name in recording event notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_channel_number` | JSON path: `rd_channel_number` | Type: boolean | Default: `true` | Description: Include channel number in recording event notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_type` | JSON path: `rd_type` | Type: boolean | Default: `true` | Description: Include event type in recording event notifications. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.

### Disk space

- `alert_disk_space` | JSON path: `alert_disk_space` | Type: boolean | Default: `true` | Description: Enables disk space notifications. | Valid values: `true`, `false`. | Env override: `CW_ALERT_DISK_SPACE` at bootstrap. | Reload behavior: `restart-required`.
- `ds_warning_threshold_percent` | JSON path: `ds_warning_threshold_percent` | Type: integer | Default: `10` | Description: Warning threshold for free disk percentage. Falls back from deprecated `ds_threshold_percent` during normalization when blank. | Valid values: `0` to `100`. | Env override: none. | Reload behavior: `restart-required`.
- `ds_warning_threshold_gb` | JSON path: `ds_warning_threshold_gb` | Type: integer | Default: `50` | Description: Warning threshold for free disk space in gigabytes. Falls back from deprecated `ds_threshold_gb` during normalization when blank. | Valid values: greater than or equal to `0`. | Env override: none. | Reload behavior: `restart-required`.

## Advanced tab

### Cache duration

- `channel_cache_ttl` | JSON path: `channel_cache_ttl` | Type: integer | Default: `86400` | Description: Channel list cache time to live, in seconds. | Valid values: `0` to `604800`. | Env override: none. | Reload behavior: `restart-required`.
- `program_cache_ttl` | JSON path: `program_cache_ttl` | Type: integer | Default: `86400` | Description: Program guide cache time to live, in seconds. | Valid values: `0` to `604800`. | Env override: none. | Reload behavior: `restart-required`.
- `job_cache_ttl` | JSON path: `job_cache_ttl` | Type: integer | Default: `3600` | Description: Recording jobs cache time to live, in seconds. | Valid values: `0` to `604800`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_cache_ttl` | JSON path: `vod_cache_ttl` | Type: integer | Default: `86400` | Description: VOD library cache time to live, in seconds. | Valid values: `0` to `604800`. | Env override: none. | Reload behavior: `restart-required`.

### Rate limiting

- `global_rate_limit` | JSON path: `global_rate_limit` | Type: integer | Default: `20` | Description: Maximum notifications sent during one global rate window. | Valid values: greater than or equal to `1`; UI presets are `5`, `10`, `20`, `50`, `100`. | Env override: none. | Reload behavior: `restart-required`.
- `global_rate_window` | JSON path: `global_rate_window` | Type: integer | Default: `300` | Description: Global notification rate window, in seconds. | Valid values: greater than or equal to `10`; UI presets are `60`, `300`, `900`, `1800`, `3600`. | Env override: none. | Reload behavior: `restart-required`.

### Timing

- `cw_alert_cooldown` | JSON path: `cw_alert_cooldown` | Type: integer | Default: `300` | Description: Minimum seconds between repeated channel watching alerts for the same condition. | Valid values: greater than or equal to `0`; UI presets start at `60`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_alert_cooldown` | JSON path: `vod_alert_cooldown` | Type: integer | Default: `300` | Description: Minimum seconds between repeated VOD alerts for the same condition. | Valid values: greater than or equal to `0`; UI presets start at `60`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_significant_threshold` | JSON path: `vod_significant_threshold` | Type: integer | Default: `300` | Description: Seconds watched before a VOD view is considered significant. | Valid values: greater than or equal to `0`; UI presets are `30`, `60`, `300`, `600`, `900`. | Env override: none. | Reload behavior: `restart-required`.

### Disk controls

- `ds_threshold_percent` | JSON path: `ds_threshold_percent` | Type: integer | Default: `10` | Description: Deprecated legacy disk free percentage threshold retained for migration and fallback. Use `ds_warning_threshold_percent`. | Valid values: `0` to `100`. | Env override: `CW_DS_THRESHOLD_PERCENT` at bootstrap. | Reload behavior: `core-only`.
- `ds_threshold_gb` | JSON path: `ds_threshold_gb` | Type: integer | Default: `50` | Description: Deprecated legacy disk free gigabyte threshold retained for migration and fallback. Use `ds_warning_threshold_gb`. | Valid values: greater than or equal to `0`. | Env override: `CW_DS_THRESHOLD_GB` at bootstrap. | Reload behavior: `core-only`.
- `ds_critical_threshold_percent` | JSON path: `ds_critical_threshold_percent` | Type: integer | Default: `5` | Description: Critical threshold for free disk percentage. | Valid values: `0` to `100`. | Env override: none. | Reload behavior: `restart-required`.
- `ds_critical_threshold_gb` | JSON path: `ds_critical_threshold_gb` | Type: integer | Default: `25` | Description: Critical threshold for free disk space in gigabytes. | Valid values: greater than or equal to `0`. | Env override: none. | Reload behavior: `restart-required`.
- `ds_startup_grace_seconds` | JSON path: `ds_startup_grace_seconds` | Type: integer | Default: `10` | Description: Seconds to wait after startup before disk alerts can fire. | Valid values: greater than or equal to `0`. | Env override: none. | Reload behavior: `restart-required`.
- `ds_worsening_delta_gb` | JSON path: `ds_worsening_delta_gb` | Type: integer | Default: `1` | Description: Send another disk alert when free space worsens by at least this many gigabytes. | Valid values: greater than or equal to `0`. | Env override: none. | Reload behavior: `restart-required`.
- `ds_worsening_delta_percent` | JSON path: `ds_worsening_delta_percent` | Type: number | Default: `1.0` | Description: Send another disk alert when free percentage worsens by at least this amount. | Valid values: greater than or equal to `0`. | Env override: none. | Reload behavior: `restart-required`.
- `ds_alert_cooldown` | JSON path: `ds_alert_cooldown` | Type: integer | Default: `3600` | Description: Minimum seconds between repeated disk space notifications. | Valid values: greater than or equal to `0`. | Env override: `CW_DS_ALERT_COOLDOWN` at bootstrap. | Reload behavior: `restart-required`.
- `ds_test_route_override` | JSON path: `ds_test_route_override` | Type: string or null | Default: `""` | Description: Optional route override used for disk alert test delivery. Blank values normalize to an empty string. | Valid values: any string or null. | Env override: none. | Reload behavior: `restart-required`.

### Error reporting

- `error_reporting_dsn` | JSON path: `error_reporting_dsn` | Type: string or null | Default: `""` | Description: Optional error reporting DSN. | Valid values: any string or null. | Env override: none. | Reload behavior: `restart-required`.

### Feature and security internals

- `multi_dvr_v2_enabled` | JSON path: `multi_dvr_v2_enabled` | Type: boolean | Default: `true` | Description: Feature flag for the multi DVR settings model. Not shown as a user toggle. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `core-only`.
- `rbac_enabled` | JSON path: `rbac_enabled` | Type: boolean | Default: `false` | Description: Legacy compatibility flag set from `auth_mode` when the Security tab selects secure login. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload` for UI auth state, `restart-required` for core consumers. |

## Notifications tab

### Webhooks

- `webhooks` | JSON path: `webhooks` | Type: array of `WebhookSettings` objects | Default: `[]` | Description: Outbound webhook destinations. Secrets are preserved on save when the incoming secret is empty or masked. | Valid values: array of objects with `url`, `secret`, and `enabled`. | Env override: none. | Reload behavior: `restart-required`.
- `url` | JSON path: `webhooks[].url` | Type: string | Default: `""` | Description: Destination URL for webhook delivery. | Valid values: any string accepted by the webhook sender. | Env override: none. | Reload behavior: `restart-required`.
- `secret` | JSON path: `webhooks[].secret` | Type: string | Default: `""` | Description: Shared secret used for HMAC SHA256 signing. | Valid values: any string. `null` normalizes to `""`. | Env override: none. | Reload behavior: `restart-required`.
- `enabled` | JSON path: `webhooks[].enabled` | Type: boolean | Default: `false` in schema; new UI entries start as `true`. | Description: Whether this webhook destination is active. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.

### Apprise providers

- `apprise_pushover` | JSON path: `apprise_pushover` | Type: string or null | Default: `""` | Description: Pushover Apprise credential string. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_PUSHOVER` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_discord` | JSON path: `apprise_discord` | Type: string or null | Default: `""` | Description: Discord Apprise credential string. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_DISCORD` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_email` | JSON path: `apprise_email` | Type: string or null | Default: `""` | Description: Email sender or SMTP Apprise credential string. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_EMAIL` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_email_to` | JSON path: `apprise_email_to` | Type: string or null | Default: `""` | Description: Email recipient value paired with `apprise_email`. Sensitive on API reads. | Valid values: recipient string, empty string, or null. | Env override: `CW_APPRISE_EMAIL_TO` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_telegram` | JSON path: `apprise_telegram` | Type: string or null | Default: `""` | Description: Telegram Apprise credential string. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_TELEGRAM` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_slack` | JSON path: `apprise_slack` | Type: string or null | Default: `""` | Description: Slack Apprise credential string. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_SLACK` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_gotify` | JSON path: `apprise_gotify` | Type: string or null | Default: `""` | Description: Gotify Apprise credential string. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_GOTIFY` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_matrix` | JSON path: `apprise_matrix` | Type: string or null | Default: `""` | Description: Matrix Apprise credential string. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_MATRIX` at bootstrap. | Reload behavior: `restart-required`.
- `apprise_custom` | JSON path: `apprise_custom` | Type: string or null | Default: `""` | Description: Custom Apprise URL or URLs. Sensitive on API reads. | Valid values: provider credential string, empty string, or null. | Env override: `CW_APPRISE_CUSTOM` at bootstrap. | Reload behavior: `restart-required`.

### Notification templates

- `cw_template_title` | JSON path: `cw_template_title` | Type: string | Default: `"Channels DVR - Watching TV"` | Description: Channel watching notification title template. | Valid values: template string using supported channel watching placeholders. | Env override: none. | Reload behavior: `restart-required`.
- `cw_template_body` | JSON path: `cw_template_body` | Type: string | Default: channel watching body template from `TEMPLATE_SETTINGS_DEFAULTS`. | Description: Channel watching notification body template. | Valid values: template string using supported channel watching placeholders. | Env override: none. | Reload behavior: `restart-required`.
- `cw_template_use_default` | JSON path: `cw_template_use_default` | Type: boolean | Default: `true` | Description: Use the built in channel watching template instead of custom title and body values. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `vod_template_title` | JSON path: `vod_template_title` | Type: string | Default: `"🎬 Channels DVR - Watching DVR Content"` | Description: VOD notification title template. | Valid values: template string using supported VOD placeholders and conditional tags. | Env override: none. | Reload behavior: `restart-required`.
- `vod_template_body` | JSON path: `vod_template_body` | Type: string | Default: VOD body template from `TEMPLATE_SETTINGS_DEFAULTS`. | Description: VOD notification body template. | Valid values: template string using supported VOD placeholders and conditional tags. | Env override: none. | Reload behavior: `restart-required`.
- `vod_template_use_default` | JSON path: `vod_template_use_default` | Type: boolean | Default: `true` | Description: Use the built in VOD template instead of custom title and body values. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `rd_template_title` | JSON path: `rd_template_title` | Type: string | Default: `"Channels DVR - Recording Event"` | Description: Recording event notification title template. | Valid values: template string using supported recording placeholders and conditional tags. | Env override: none. | Reload behavior: `restart-required`.
- `rd_template_body` | JSON path: `rd_template_body` | Type: string | Default: recording body template from `TEMPLATE_SETTINGS_DEFAULTS`. | Description: Recording event notification body template. | Valid values: template string using supported recording placeholders and conditional tags. | Env override: none. | Reload behavior: `restart-required`.
- `rd_template_use_default` | JSON path: `rd_template_use_default` | Type: boolean | Default: `true` | Description: Use the built in recording event template instead of custom title and body values. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.
- `ds_template_title` | JSON path: `ds_template_title` | Type: string | Default: `"⚠️ Low Disk Space Warning"` | Description: Disk space notification title template. | Valid values: template string using supported disk placeholders. | Env override: none. | Reload behavior: `restart-required`.
- `ds_template_body` | JSON path: `ds_template_body` | Type: string | Default: disk body template from `TEMPLATE_SETTINGS_DEFAULTS`. | Description: Disk space notification body template. | Valid values: template string using supported disk placeholders. | Env override: none. | Reload behavior: `restart-required`.
- `ds_template_use_default` | JSON path: `ds_template_use_default` | Type: boolean | Default: `true` | Description: Use the built in disk space template instead of custom title and body values. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `restart-required`.

## Routing tab

### Notification routing

- `notification_routing` | JSON path: `notification_routing` | Type: object | Default: `{}` | Description: Per DVR, per event type routing for Apprise and webhook destinations. Missing keys default to enabled. | Valid values: object shaped as `dvr_id -> event_type -> destination -> boolean`; event keys in the UI are `channel`, `vod`, `recording`, and `disk`. | Env override: none. | Reload behavior: `restart-required`.

> **Field naming note**: The persisted settings field is `notification_routing`. The UI may describe the same feature as notification routing or a routing matrix, but the settings file always uses `notification_routing`.

## Security tab

### Authentication settings

- `api_key` | JSON path: `api_key` | Type: string or null | Default: `""` | Description: Legacy API key used by API key auth and frontend bootstrap. It is intentionally not exposed as a direct Settings UI input. | Valid values: string, empty string, or null. | Env override: none. | Reload behavior: `hot-reload` for UI requests, `restart-required` for core consumers.
- `auth_mode` | JSON path: `auth_mode` | Type: string | Default: `""` | Description: Persisted auth mode. Empty string keeps legacy auto detection. | Valid values: `api_key`, `rbac`, `none`, or empty string. The visible Settings UI offers `rbac` and `none`. | Env override: `CW_DISABLE_AUTH` can override runtime behavior without changing this field. | Reload behavior: `hot-reload` for UI auth state.
- `security_setup_completed` | JSON path: `security_setup_completed` | Type: boolean or null | Default: `null` in schema; the entrypoint first-file template writes `false`. | Description: Legacy marker for whether initial security setup completed. If absent and still null, core persistence keeps it absent. | Valid values: `true`, `false`, or null. | Env override: none. | Reload behavior: `hot-reload` for UI setup state.
- `ics_feed_enabled` | JSON path: `ics_feed_enabled` | Type: boolean | Default: `false` | Description: Enables tokenized ICS calendar feed access. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `ics_feed_token` | JSON path: `ics_feed_token` | Type: string or null | Default: `""` | Description: Token for ICS calendar feed access. | Valid values: string, empty string, or null. | Env override: none. | Reload behavior: `hot-reload`.
- `rss_feed_enabled` | JSON path: `rss_feed_enabled` | Type: boolean | Default: `false` | Description: Enables tokenized RSS and Atom activity feeds. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `rss_feed_token` | JSON path: `rss_feed_token` | Type: string or null | Default: `""` | Description: Token for RSS and Atom feed access. | Valid values: string, empty string, or null. | Env override: none. | Reload behavior: `hot-reload`.

## Backup tab

The Backup tab does not add fields to `settings.json`. It exposes backup and restore operations around the persisted file and migration backups.

## Core and entrypoint default mismatches

| Setting | Schema default | Core default | Entrypoint first-file default | Note |
|---|---:|---:|---:|---|
| `_version` | Not a Pydantic field | Migration managed | `7` | Core migrations own the authoritative schema epoch; UI saves preserve the existing `_version` when present and should not be treated as the source of truth for core migration state. |
| `history_retention_days` | `90` | `90` | omitted | Added by model defaults or migration after load. |
| `multi_dvr_v2_enabled` | `true` | `true` | omitted | Added by model defaults or migration after load. |
| `rbac_enabled` | `false` | omitted | omitted | UI schema only, derived from auth mode in the Security tab. |
| `auth_mode` | `""` | `""` | omitted | Added by model defaults or migration after load. |
| `security_setup_completed` | `null` | `null` | `false` | Entrypoint writes `false` only when creating the initial file. |
| `monitor_stale_seconds` | `300` | `300` | omitted | Added by model defaults. |
| Template fields | Template defaults | Template defaults | omitted | Added by model defaults or migration. |
| Feed fields | disabled and empty token | disabled and empty token | omitted | Added by UI schema defaults. |
| `webhooks` | `[]` | `[]` | omitted | Added by model defaults or migration. |
| Warning and critical disk fields | schema values | same values | omitted | Legacy `ds_threshold_*` fields are present in the template. |
| `error_reporting_dsn` | `""` | `""` | omitted | Added by model defaults. |
| `notification_routing` | `{}` | `{}` | omitted | Added by model defaults. |

## Minimal example

This is the smallest JSON object accepted by `AppSettings`; omitted fields take schema defaults.

```json
{}
```

## Fully populated example

```json
{
  "dvr_servers": [
    {
      "id": "dvr_abc12345",
      "name": "Main DVR",
      "host": "192.168.1.100",
      "port": 8089,
      "enabled": true,
      "api_key": "",
      "overrides": {
        "alert_channel_watching": true,
        "cw_image_source": "PROGRAM"
      }
    }
  ],
  "tz": "America/Los_Angeles",
  "log_level": 1,
  "log_retention_days": 7,
  "history_retention_days": 90,
  "alert_channel_watching": true,
  "alert_vod_watching": true,
  "alert_disk_space": true,
  "alert_recording_events": true,
  "multi_dvr_v2_enabled": true,
  "rbac_enabled": false,
  "auth_mode": "",
  "security_setup_completed": null,
  "stream_count": true,
  "monitor_stale_seconds": 300,
  "cw_channel_name": true,
  "cw_channel_number": true,
  "cw_program_name": true,
  "cw_device_name": true,
  "cw_device_ip": true,
  "cw_stream_source": true,
  "cw_image_source": "PROGRAM",
  "cw_alert_cooldown": 300,
  "cw_template_title": "Channels DVR - Watching TV",
  "cw_template_body": "{📺 <channel_name}\n{Channel: <channel_number}\n{Program: <program_title}\n{Resolution: <resolution}\n{Device: <client_name}\n{Device IP: <client_ip}\n{Source: <stream_source}\n{Total Streams: <stream_count}",
  "cw_template_use_default": true,
  "global_rate_limit": 20,
  "global_rate_window": 300,
  "stream_card_image": "program",
  "recording_card_image": "program",
  "api_key": "",
  "ics_feed_enabled": false,
  "ics_feed_token": "",
  "rss_feed_enabled": false,
  "rss_feed_token": "",
  "webhooks": [
    {
      "url": "https://receiver.example.invalid/channelwatch",
      "secret": "shared-secret",
      "enabled": true
    }
  ],
  "rd_alert_scheduled": true,
  "rd_alert_started": true,
  "rd_alert_completed": true,
  "rd_alert_cancelled": true,
  "rd_program_name": true,
  "rd_program_desc": true,
  "rd_duration": true,
  "rd_channel_name": true,
  "rd_channel_number": true,
  "rd_type": true,
  "rd_template_title": "Channels DVR - Recording Event",
  "rd_template_body": "{📺 <channel_name}\n{Channel: <channel_number}\n{status}\n{details}\n{summary_block}\n{time_table}",
  "rd_template_use_default": true,
  "vod_title": true,
  "vod_episode_title": true,
  "vod_summary": true,
  "vod_duration": true,
  "vod_progress": true,
  "vod_image": true,
  "vod_rating": true,
  "vod_genres": true,
  "vod_cast": true,
  "vod_device_name": true,
  "vod_device_ip": true,
  "vod_alert_cooldown": 300,
  "vod_significant_threshold": 300,
  "vod_template_title": "🎬 Channels DVR - Watching DVR Content",
  "vod_template_body": "{media_title}\n{progress_line}\n{Device Name: <client_name}\n{Device IP: <client_ip}\n{summary_block}\n{info_sections}",
  "vod_template_use_default": true,
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
  "ds_template_title": "⚠️ Low Disk Space Warning",
  "ds_template_body": "Free Space: {disk_free} / {disk_total} ({disk_percent})\nUsed Space: {disk_used}\nDVR Path: {disk_path}",
  "ds_template_use_default": true,
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
  "notification_routing": {
    "dvr_abc12345": {
      "channel": {
        "pushover": true,
        "webhook": true
      },
      "vod": {
        "pushover": true,
        "webhook": true
      },
      "recording": {
        "pushover": true,
        "webhook": true
      },
      "disk": {
        "pushover": true,
        "webhook": true
      }
    }
  }
}
```

## Non persisted schema fields

These fields are declared in `app/ui/backend/schemas.py` response models. They are not stored in `settings.json`.

### AuthStateContract

- `persisted_mode` | JSON path: API response only | Type: `api_key`, `rbac`, `none`, or null | Default: `null` | Description: Auth mode persisted in settings. | Valid values: `api_key`, `rbac`, `none`, null. | Env override: runtime auth env can change effective state. | Reload behavior: `hot-reload`.
- `configured_mode` | JSON path: API response only | Type: `api_key`, `rbac`, `none`, `setup`, or null | Default: `null` | Description: Auth mode after config and setup checks. | Valid values: `api_key`, `rbac`, `none`, `setup`, null. | Env override: runtime auth env can change effective state. | Reload behavior: `hot-reload`.
- `effective_mode` | JSON path: API response only | Type: `api_key`, `rbac`, `none`, `setup`, or null | Default: `null` | Description: Auth mode actually enforced by the UI backend. | Valid values: `api_key`, `rbac`, `none`, `setup`, null. | Env override: `CW_DISABLE_AUTH`. | Reload behavior: `hot-reload`.
- `setup_required` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether initial security setup is required. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `runtime_auth_override_active` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether a runtime auth override is active. | Valid values: `true`, `false`. | Env override: `CW_DISABLE_AUTH`. | Reload behavior: `hot-reload`.
- `api_key_fallback_active` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether API key fallback is active with RBAC. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `session_auth_available` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether session auth can be used. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `session_setup_required` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether session credential setup is required. | Valid values: `true`, `false`. | Env override: `CW_ADMIN_USER`, `CW_ADMIN_PASS` may affect setup path. | Reload behavior: `hot-reload`.

### SecurityFeedsStatus

- `implemented` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether feed security status reporting is implemented. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `ics_enabled` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether the ICS feed is enabled. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `rss_enabled` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether the RSS feed is enabled. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `atom_enabled` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether the Atom feed is enabled. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.

### SecurityStatusResponse

- `security_mode` | JSON path: API response only | Type: string | Default: required by response model | Description: UI security mode summary. | Valid values: `NO_AUTH`, `API_KEY_ONLY`, `RBAC_WITH_API_KEY_FALLBACK`, `RBAC_ONLY`. | Env override: `CW_DISABLE_AUTH` can affect runtime mode. | Reload behavior: `hot-reload`.
- `auth_disabled` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether auth is disabled. | Valid values: `true`, `false`. | Env override: `CW_DISABLE_AUTH`. | Reload behavior: `hot-reload`.
- `api_key_configured` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether an API key is configured. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `encrypted_dvr_api_keys_at_rest` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether DVR API keys are encrypted at rest. | Valid values: `true`, `false`. | Env override: none. | Reload behavior: `hot-reload`.
- `encryption_key_path` | JSON path: API response only | Type: string | Default: required by response model | Description: Path to the DVR API key encryption key. | Valid values: string path. | Env override: `CONFIG_PATH` changes the config directory. | Reload behavior: `hot-reload`.
- `feeds` | JSON path: API response only | Type: `SecurityFeedsStatus` object | Default: required by response model | Description: Feed security status object. | Valid values: object containing feed status fields. | Env override: none. | Reload behavior: `hot-reload`.

### SetupStatusResponse

- `needs_setup` | JSON path: API response only | Type: boolean | Default: required by response model | Description: Whether setup is needed. | Valid values: `true`, `false`. | Env override: `CW_ADMIN_USER`, `CW_ADMIN_PASS` may affect setup path. | Reload behavior: `hot-reload`.
- `current_mode` | JSON path: API response only | Type: `api_key`, `rbac`, `none`, `setup`, or null | Default: `null` | Description: Current effective setup mode. | Valid values: `api_key`, `rbac`, `none`, `setup`, null. | Env override: `CW_DISABLE_AUTH` can affect runtime mode. | Reload behavior: `hot-reload`.
- `available_modes` | JSON path: API response only | Type: array | Default: required by response model | Description: Auth modes offered by setup. | Valid values: array containing `api_key`, `rbac`, or `none`. | Env override: none. | Reload behavior: `hot-reload`.

## See also

- `docs/reference/env-vars.md` for environment variable behavior.
- `docs/reference/api.md` for API response models and endpoints.
