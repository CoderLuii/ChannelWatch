# How to manage alerts and operational notifications

ChannelWatch always records supported activity and operational outcomes. Alert switches control notification delivery, not monitoring or history.

Open **Settings > Alerts** to choose a starting policy or adjust individual alert types. Changes remain unsaved until you select **Save Settings**.

## Choose an alert policy

| Policy | What it delivers |
| --- | --- |
| Monitor Only | No notifications. Monitoring and activity history continue. |
| Important Only | DVR unreachable and recovered, disk warning and critical, and recording failed, skipped, missed, interrupted, or cancelled. |
| Balanced | Important Only plus recording started and completed. |
| Everything | Every supported alert, including scheduled recordings and viewing activity. |
| Custom | The current switches do not exactly match another policy. |

Fresh v1.0.1 installations start with **Important Only**. An upgrade does not silently enable any new alert. ChannelWatch preserves existing global switches, per-DVR overrides, routing, providers, templates, and rate limits. Newly introduced operational delivery switches start off until you deliberately change them.

Selecting a policy does not change the form. Choose **Apply preset** to update the unsaved switches, review the summary, and then choose **Save Settings**. Applying a global policy leaves existing per-DVR overrides in place. Applying one from a DVR-specific tab changes only that DVR.

## Understand recording outcomes

ChannelWatch uses the following terminal outcomes:

- **Failed** means Channels DVR reported a failure flag, dead job, or error.
- **Skipped** means Channels DVR explicitly skipped the recording.
- **Cancelled** means the recording was cancelled before it started.
- **Interrupted** means a previously started recording ended without confirmed success.
- **Completed** means the recording finished without a failure indicator.
- **Did not start** means ChannelWatch previously observed the schedule, the start time and grace period passed, the DVR remained reachable, and two later checks found no start or other terminal outcome.

Failure takes precedence over every other terminal label. A single job cannot produce contradictory terminal outcomes. ChannelWatch defers missed or interrupted decisions while the DVR is unreachable and resumes reconciliation after recovery.

## Configure DVR health alerts

Enable **DVR unreachable** and **DVR recovered** under DVR Health. The default fresh-install delay is 120 seconds of continuous confirmed unavailability. A DVR that is already offline when ChannelWatch starts receives a five-minute startup grace period.

ChannelWatch records one outage and one corresponding recovery. It sends a recovery notification only when that outage generated an unreachable notification, so an intentionally disabled route does not create an unexplained recovery message.

ChannelWatch can report a DVR outage only while ChannelWatch itself is running. It cannot send while its own container, host, power, or network is completely unavailable. Use an independent monitoring system against `/healthz/live`, `/healthz/startup`, and `/healthz/ready` when you need ChannelWatch-down detection.

## Route Health separately

The Routing tab includes a **Health** event column. You can send DVR health alerts to different destinations than Recording, Disk, Live TV, or VOD alerts. Routing changes do not change which events ChannelWatch records.

## Verify delivery

Use the notification diagnostics for a direct, awaited test. A passing diagnostic means at least one configured destination accepted the test. The Notification Log shows sent, failed, skipped, retry, and circuit-breaker outcomes.

See also:

- [Troubleshoot notifications](troubleshoot-notifications.md)
- [Notification pipeline](../explanation/notification-pipeline.md)
- [Settings reference](../reference/settings.md)
