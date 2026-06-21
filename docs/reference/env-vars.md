# Environment variables reference

This reference lists environment variables read by the ChannelWatch container, core monitor, UI backend, and supervisor template.

Env vars are mostly startup inputs. Saved application settings live in `/config/settings.json`; see `docs/reference/settings.md` for the full settings schema.

## Scope values

| Scope | Meaning |
| --- | --- |
| startup-only | Read during container, core, UI, or supervisor startup. Restart the affected process or container after changes. |
| per-request | Read while handling a runtime action or request. |
| hot-reload | Read again when the core settings model reloads from disk or when a runtime helper runs. |

## Container runtime

- `PUID`
  - Default: `1000`
  - Purpose: Sets the UID used for `/config`, `/app`, and the final application process.
  - Scope: startup-only.
  - Related `settings.json` path: none.

- `PGID`
  - Default: `1000`
  - Purpose: Sets the GID used for `/config`, `/app`, and the final application process.
  - Scope: startup-only.
  - Related `settings.json` path: none.

- `TZ`
  - Default: `America/Los_Angeles` when no env var and no saved timezone exist.
  - Purpose: Sets the container timezone and overrides the saved ChannelWatch timezone when present.
  - Scope: startup-only for the container timezone; hot-reload for the core settings model when it reloads.
  - Related `settings.json` path: `tz`.

## ChannelWatch-specific

- `CW_LOG_LEVEL`
  - Default: no env override. New settings files default to `1`.
  - Purpose: Seeds the saved core log verbosity. Valid saved values are `1` for standard and `2` for verbose.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `log_level`.

- `CW_API_KEY`
  - Default: no env override. New settings files default to an empty API key.
  - Purpose: Seeds the saved app/ui/API access key during first-time settings bootstrap.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `api_key`.

- `CW_APPRISE_DISCORD`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the Discord Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_discord`.

- `CW_APPRISE_PUSHOVER`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the Pushover Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_pushover`.

- `CW_APPRISE_TELEGRAM`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the Telegram Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_telegram`.

- `CW_APPRISE_EMAIL`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the outbound email Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_email`.

- `CW_APPRISE_EMAIL_TO`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the email recipient used with `CW_APPRISE_EMAIL`.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_email_to`.

- `CW_APPRISE_SLACK`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the Slack Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_slack`.

- `CW_APPRISE_GOTIFY`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the Gotify Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_gotify`.

- `CW_APPRISE_MATRIX`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds the Matrix Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_matrix`.

- `CW_APPRISE_CUSTOM`
  - Default: no env override. New settings files default to an empty string.
  - Purpose: Seeds a custom Apprise URL value.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `apprise_custom`.

- `CW_ALERT_CHANNEL_WATCHING`
  - Default: no env override. New settings files default to `true`.
  - Purpose: Seeds whether channel watching alerts are enabled.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `alert_channel_watching`.

- `CW_ALERT_VOD_WATCHING`
  - Default: no env override. New settings files default to `true`.
  - Purpose: Seeds whether VOD watching alerts are enabled.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `alert_vod_watching`.

- `CW_ALERT_DISK_SPACE`
  - Default: no env override. New settings files default to `true`.
  - Purpose: Seeds whether disk space alerts are enabled.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `alert_disk_space`.

- `CW_ALERT_RECORDING_EVENTS`
  - Default: no env override. New settings files default to `true`.
  - Purpose: Seeds whether recording event alerts are enabled.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `alert_recording_events`.

- `CW_DS_THRESHOLD_PERCENT`
  - Default: no env override. New settings files default to `10`.
  - Purpose: Seeds the disk free percent alert threshold.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `ds_threshold_percent`.

- `CW_DS_THRESHOLD_GB`
  - Default: no env override. New settings files default to `50`.
  - Purpose: Seeds the disk free GB alert threshold.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `ds_threshold_gb`.

- `CW_DS_ALERT_COOLDOWN`
  - Default: no env override. New settings files default to `3600`.
  - Purpose: Seeds the disk alert cooldown in seconds.
  - Scope: startup-only; applied only when `settings.json` is created during the same container start.
  - Related `settings.json` path: `ds_alert_cooldown`.

- `CW_DISABLE_AUTH`
  - Default: empty string, treated as `false`.
  - Purpose: Temporarily bypasses UI API authentication when set to `true`. It does not change the saved auth mode.
  - Scope: startup-only for the UI backend process.
  - Related `settings.json` path: none.

- `CW_ADMIN_USER`
  - Default: empty string.
  - Purpose: Provides the username for one-time admin bootstrap when no auth users exist. Requires `CW_ADMIN_PASS`.
  - Scope: startup-only for the UI backend process.
  - Related `settings.json` path: none. Uses the auth database, not `settings.json`.

- `CW_ADMIN_PASS`
  - Default: empty string.
  - Purpose: Provides the password for one-time admin bootstrap when no auth users exist. Requires `CW_ADMIN_USER`.
  - Scope: startup-only for the UI backend process.
  - Related `settings.json` path: none. Uses the auth database, not `settings.json`.

- `CW_INSTANCE_URL`
  - Default: no env override. Webhook payloads fall back to `http://localhost:8501`.
  - Purpose: Sets the public instance URL included in outbound webhook payloads. Checked after `CHANNELWATCH_INSTANCE_URL` and before `APP_URL`.
  - Scope: per-request for webhook payload creation.
  - Related `settings.json` path: none.

- `CW_BOOTSTRAP_SETTINGS_CREATED`
  - Default: `0`.
  - Purpose: Internal flag exported by `docker-entrypoint.py` to tell its merge step whether `settings.json` was just created.
  - Scope: startup-only.
  - Related `settings.json` path: none.
  - Operator guidance: don't set this manually.

## Networking

- `CHANNELS_DVR_SERVERS`
  - Default: no env override.
  - Purpose: Seeds or overrides DVR server definitions from comma-separated `Name@host:port` entries. Takes precedence over `CHANNELS_DVR_HOST` in core config.
  - Scope: startup-only in `docker-entrypoint.py` when a new settings file is created; hot-reload in the core settings model.
  - Related `settings.json` path: `dvr_servers`.

- `CHANNELS_DVR_HOST`
  - Default: no env override.
  - Purpose: Legacy single-DVR host bootstrap. Use `CHANNELS_DVR_SERVERS` for new multi-DVR setups.
  - Scope: startup-only in `docker-entrypoint.py` when a new settings file is created; hot-reload in the core settings model and migration path.
  - Related `settings.json` path: `dvr_servers[].host`.

- `CHANNELS_DVR_PORT`
  - Default: `8089` when `CHANNELS_DVR_HOST` is set without a port.
  - Purpose: Legacy single-DVR port bootstrap.
  - Scope: startup-only in `docker-entrypoint.py` when a new settings file is created; hot-reload in the core settings model and migration path.
  - Related `settings.json` path: `dvr_servers[].port`.

- `CHANNELS_DVR_NAME`
  - Default: empty string, falling back to `CHANNELS_DVR_HOST`.
  - Purpose: Legacy migration helper for naming a single DVR server when old env-based installs are converted to `dvr_servers`.
  - Scope: hot-reload in the migration path when no saved DVR servers exist.
  - Related `settings.json` path: `dvr_servers[].name`.

- `CHANNELWATCH_INSTANCE_URL`
  - Default: no env override. Webhook payloads fall back to `http://localhost:8501`.
  - Purpose: Preferred public instance URL for outbound webhook payloads.
  - Scope: per-request for webhook payload creation.
  - Related `settings.json` path: none.

- `APP_URL`
  - Default: no env override. Webhook payloads fall back to `http://localhost:8501`.
  - Purpose: Compatibility public instance URL for outbound webhook payloads. Checked after `CHANNELWATCH_INSTANCE_URL` and `CW_INSTANCE_URL`.
  - Scope: per-request for webhook payload creation.
  - Related `settings.json` path: none.

## Logging

- `LOG_FORMAT`
  - Default: `text`.
  - Purpose: Sets core log output formatting. Use `json` to add JSON logs to stdout while the file handler uses the same formatter.
  - Scope: startup-only when core logging is configured.
  - Related `settings.json` path: none.

## Python runtime

- `CONFIG_PATH`
  - Default: `/config`.
  - Purpose: Changes the config directory read by Python code for `settings.json`, `channelwatch.log`, and `encryption.key`.
  - Scope: startup-only for imported config modules and core startup.
  - Related `settings.json` path: none.
  - Operator guidance: keep the default unless you also control the container mount and entrypoint behavior.

- `CHANNELWATCH_DB`
  - Default: `/config/channelwatch.db`.
  - Purpose: Points notification delivery tracking at a SQLite database path.
  - Scope: hot-reload the first time the delivery database engine is initialized in a process.
  - Related `settings.json` path: none.

- `CHANNELWATCH_PLUGIN_DIR`
  - Default: `/config/plugins/notifications`.
  - Purpose: Sets the directory scanned for notification provider plugins.
  - Scope: per-request when plugin loading is called without an explicit directory.
  - Related `settings.json` path: none.

## Supervisor

- `PYTHONPATH`
  - Default: `/app` in both supervisor-managed programs.
  - Purpose: Makes the packaged `core` and `ui` modules importable for the core monitor and UI backend.
  - Scope: startup-only for supervisor child processes.
  - Related `settings.json` path: none.
  - Operator guidance: set by `deploy/config/supervisor/supervisord.conf.template`; don't override in compose unless you are building a custom image.

## Recommended docker-compose environment block

```yaml
services:
  ChannelWatch:
    environment:
      TZ: "America/New_York"
      PUID: "1000"
      PGID: "1000"
      CHANNELS_DVR_SERVERS: "Main@192.168.1.100:8089"
      CW_LOG_LEVEL: "1"
      CW_DISABLE_AUTH: "false"
      LOG_FORMAT: "text"
```

## See also

- `docs/reference/settings.md`
- `deploy/compose/default.yml`
