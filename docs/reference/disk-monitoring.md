# Disk monitoring reference

ChannelWatch monitors Channels DVR storage through each configured DVR server. The core disk alert is implemented in `app/core/alerts/disk_space.py`; dashboard status and Prometheus output are built in `app/ui/backend/main.py`.

## Monitored disk

Disk monitoring is per DVR. For each configured DVR, ChannelWatch calls the Channels DVR HTTP API at `http://<host>:<port>/dvr` and reads the storage values returned by that server.

The core alert path expects a `disk` object with at least these fields:

| Field | Meaning |
| --- | --- |
| `disk.free` | Free storage bytes. |
| `disk.total` | Total storage bytes. |
| `disk.used` | Used storage bytes, used in notification text. |
| `path` | DVR storage path. ChannelWatch copies this to `disk.path`; if it is missing, the fallback display path is `/shares/DVR`. |

The UI backend also calls `/dvr` for each enabled DVR. It can parse either `ServerStorage.Available` plus `ServerStorage.Total`, or the `disk.free` plus `disk.total` shape. The aggregate `/api/system-info` disk fields sum the per-DVR values.

ChannelWatch does not inspect the ChannelWatch container host filesystem for DVR free space. It requires access to the Channels DVR API for storage data, so the reported disk is the storage path reported by the DVR server, not an arbitrary container mount or host path.

## Severity thresholds

The current server-side severity model has three states: `normal`, `warning`, and `critical`. Critical is checked first.

| Severity | Fires when free space is... | Default |
| --- | --- | --- |
| `normal` | Not below warning or critical thresholds. | N/A |
| `warning` | Less than `ds_warning_threshold_percent` percent free, or less than `ds_warning_threshold_gb` GiB free. | `10` percent or `50` GiB |
| `critical` | Less than `ds_critical_threshold_percent` percent free, or less than `ds_critical_threshold_gb` GiB free. | `5` percent or `25` GiB |

Threshold comparisons are strict. A DVR at exactly `10` percent free does not match the `10` percent warning threshold through the percent rule; it must be below the threshold. The GiB rule uses the same strict comparison.

Legacy fields still exist:

| Legacy field | Current use |
| --- | --- |
| `ds_threshold_percent` | Deprecated fallback for `ds_warning_threshold_percent` when the warning field is blank during normalization. Default `10`. |
| `ds_threshold_gb` | Deprecated fallback for `ds_warning_threshold_gb` when the warning field is blank during normalization. Default `50`. |

The field names `disk_severity_low`, `disk_severity_medium`, `disk_severity_high`, and `disk_severity_critical` are not current settings fields in `app/core/helpers/config.py` or `app/ui/backend/schemas.py`.

## Alert behavior

The disk alert is poll based, not event-stream based.

| Behavior | Value or rule |
| --- | --- |
| Poll interval | `120` seconds, with up to `0.5` seconds of jitter. |
| Startup grace | `ds_startup_grace_seconds`, default `10` seconds. Notifications are not eligible until both monitoring has passed the grace period and startup has been marked complete. |
| Master toggle | `alert_disk_space`, default `true`. If false, normal disk notifications do not send. |
| Cooldown | `ds_alert_cooldown`, default `3600` seconds. Applies to repeated alerts at the same severity. |
| Worsening threshold | Repeated alerts at the same severity require free space to worsen by at least `ds_worsening_delta_gb` GiB, default `1`, or `ds_worsening_delta_percent`, default `1.0`, and the cooldown must have elapsed. |

Escalation rules:

1. The first non-normal severity after notifications become eligible sends an alert.
2. Escalation from `warning` to `critical` sends immediately, even if the cooldown has not elapsed.
3. Repeated `warning` or repeated `critical` alerts send only after meaningful worsening and cooldown.
4. When storage returns to `normal`, ChannelWatch clears the last notification state so the next future low-space condition can alert again.

Notification titles are severity-specific. Warning alerts use `Low Disk Space Warning`; critical alerts use `Low Disk Space Critical`. Custom disk templates can render the `{severity}` placeholder.

## Settings fields

Disk fields are stored in `/config/settings.json`. See `docs/reference/settings.md` for the full settings reference, validation ranges, environment override notes, and reload behavior.

| JSON path | Default | Purpose |
| --- | --- | --- |
| `alert_disk_space` | `true` | Enables disk space notifications. |
| `ds_warning_threshold_percent` | `10` | Warning threshold for free disk percentage. |
| `ds_warning_threshold_gb` | `50` | Warning threshold for free disk space in GiB. |
| `ds_critical_threshold_percent` | `5` | Critical threshold for free disk percentage. |
| `ds_critical_threshold_gb` | `25` | Critical threshold for free disk space in GiB. |
| `ds_alert_cooldown` | `3600` | Minimum seconds between repeated disk alerts at the same severity. |
| `ds_startup_grace_seconds` | `10` | Startup delay before disk alerts are eligible. |
| `ds_worsening_delta_gb` | `1` | GiB drop that counts as meaningful worsening. |
| `ds_worsening_delta_percent` | `1.0` | Free percentage drop that counts as meaningful worsening. |
| `ds_test_route_override` | `""` | Optional route override used by disk alert tests. |
| `ds_template_title` | `"⚠️ Low Disk Space Warning"` | Custom disk notification title template. |
| `ds_template_body` | Default disk body template | Custom disk notification body template. |
| `ds_template_use_default` | `true` | Use the built in disk message instead of custom disk templates. |
| `notification_routing.<dvr_id>.disk.<destination>` | Missing keys enabled | Per-DVR disk notification routing. |

## Prometheus metrics

`/metrics` exposes disk gauges in bytes. The same metric names are used for aggregate and per-DVR samples.

| Metric | Labels | Meaning |
| --- | --- | --- |
| `channelwatch_disk_free_bytes` | `scope="all"` | Free DVR storage bytes aggregated across configured DVRs. |
| `channelwatch_disk_total_bytes` | `scope="all"` | Total DVR storage bytes aggregated across configured DVRs. |
| `channelwatch_disk_used_bytes` | `scope="all"` | Used DVR storage bytes aggregated across configured DVRs. |
| `channelwatch_disk_free_bytes` | `dvr_id`, `dvr_name`, `host`, `port` | Free storage bytes for one DVR. |
| `channelwatch_disk_total_bytes` | `dvr_id`, `dvr_name`, `host`, `port` | Total storage bytes for one DVR. |
| `channelwatch_disk_used_bytes` | `dvr_id`, `dvr_name`, `host`, `port` | Used storage bytes for one DVR. |

The metrics endpoint emits disk samples only when the corresponding disk value is available from `/api/system-info`. Per-DVR used bytes are computed as total minus free.

## Provider routing

Disk alerts use the routing event key `disk`. `BaseAlert.send_alert()` injects the alert's DVR id and event type before handing the notification to `NotificationManager`.

Routing is read from `notification_routing` with this shape:

```json
{
  "dvr_abc12345": {
    "disk": {
      "pushover": true,
      "discord": true,
      "email": true,
      "telegram": true,
      "slack": true,
      "gotify": true,
      "matrix": true,
      "custom": true,
      "webhook": true
    }
  }
}
```

Missing routing config, missing DVR id, missing event type, missing DVR entry, missing `disk` entry, and missing destination keys all default to enabled. Setting a destination to `false` suppresses that provider for disk alerts from that DVR only.

## Limitations

* Disk monitoring depends on the Channels DVR `/dvr` response. If the DVR API is unreachable, returns no storage object, or reports zero total bytes, ChannelWatch skips that check.
* The core alert path reads the DVR-reported storage path. It does not monitor arbitrary folders, individual recording directories, or the ChannelWatch host filesystem.
* The UI backend aggregates disk totals across all configured DVRs for `/api/system-info`. Per-DVR Prometheus samples are available, but the aggregate severity in `/api/system-info` is computed from aggregate free and total values.
* The warning and critical severities are server-side. The current code does not expose `low`, `medium`, or `high` disk severity settings.

## See also

* `docs/reference/settings.md`
* `docs/reference/logs-metrics.md`
