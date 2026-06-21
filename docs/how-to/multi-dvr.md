# Add another DVR to ChannelWatch

Use this guide when ChannelWatch is already running with one Channels DVR and you want to add a second or third DVR to the same install.

For exact field meanings, see [`docs/reference/multi-dvr.md`](../reference/multi-dvr.md) and [`docs/reference/settings.md`](../reference/settings.md). Known limits are tracked in [`CHANGELOG.md`](../releases/CHANGELOG.md).

## When you'd want this

Add another DVR when you run more than one Channels DVR server and want one ChannelWatch dashboard to show all of them.

Common cases:

* one DVR per home or site
* one DVR for production and one for testing
* separate DVRs for different tuner groups, libraries, or storage pools

ChannelWatch monitors each active DVR separately, then lets you view either the aggregate dashboard or one DVR at a time.

## Prerequisites

Before you start, confirm that:

* ChannelWatch is already installed and reachable in your browser.
* Your first DVR is working in ChannelWatch.
* The new DVR is reachable from the ChannelWatch container by host or IP address.
* You know the new DVR's port, usually `8089`.
* You have the DVR API key if that DVR requires one.

If you rely on discovery, host networking gives mDNS the best chance to find Channels DVR servers. In bridge networking, discovery may return no results, and manual entry still works.

## Add via UI

Use the UI path when you want ChannelWatch to save and validate the settings for you.

1. Open ChannelWatch in your browser.
2. Go to **Settings**.
3. In **General**, find **DVR servers**.
4. Choose one of these paths:
   * Click **Discover** to scan for DVRs on the local network, then click **Add** next to the DVR you want.
   * Click **Add Server** to add a blank DVR row manually.
5. For a manual row, enter a clear name, the DVR host or IP address, and the port.
6. Save the settings.
7. Wait for ChannelWatch to reload monitoring. If the new DVR does not appear after a short wait, restart the ChannelWatch container.

The first-run wizard has a connection test for manual DVR entry. The Settings page uses the same saved `dvr_servers` list, but the current Settings form does not show that test button. If you need to test before saving, open `http://host:port/status` for the DVR from a network location that matches your container's reachability.

## Add via settings.json

Use the file path when you manage `/config/settings.json` directly or prefer config review before saving.

1. Stop ChannelWatch, or make a backup before editing.
2. Open `/config/settings.json`.
3. Find the `dvr_servers` array.
4. Add one object for each extra DVR.
5. Save the file as valid JSON.
6. Start or restart ChannelWatch.

Example with three DVRs:

```json
{
  "dvr_servers": [
    {
      "id": "dvr_17c621dc",
      "name": "Main DVR",
      "host": "192.168.1.10",
      "port": 8089,
      "enabled": true,
      "api_key": "",
      "overrides": {},
      "deleted_at": null
    },
    {
      "id": "dvr_72d08952",
      "name": "Garage DVR",
      "host": "192.168.1.11",
      "port": 8089,
      "enabled": true,
      "api_key": "",
      "overrides": {},
      "deleted_at": null
    },
    {
      "id": "dvr_24286ceb",
      "name": "Test DVR",
      "host": "192.168.1.12",
      "port": 8089,
      "enabled": true,
      "api_key": "",
      "overrides": {},
      "deleted_at": null
    }
  ],
  "notification_routing": {
    "dvr_17c621dc": {
      "channel": {
        "discord": true,
        "webhook": true
      },
      "disk": {
        "discord": true,
        "webhook": true
      }
    },
    "dvr_72d08952": {
      "channel": {
        "discord": false,
        "webhook": true
      },
      "recording": {
        "discord": true,
        "webhook": false
      }
    }
  }
}
```

Use the same `id` style that ChannelWatch generates, `dvr_` plus 8 hex characters. If you change a DVR's `host` or `port`, ChannelWatch treats that as a different identity unless you intentionally keep the old `id`. See the identity rules in [`docs/reference/multi-dvr.md`](../reference/multi-dvr.md) before editing IDs by hand.

Do not use `/config/dvrs.yaml` for this release. The changelog lists that loader as deferred, so `/config/settings.json` is the supported file-config path.

## Configure per-DVR notification routing

After adding the DVR, decide which notifications should go where.

In the UI:

1. Go to **Settings**.
2. Confirm your destinations in **Notifications**. Routing only shows configured destinations.
3. Open the **Routing** tab.
4. For each DVR row, turn destinations on or off for these event types:
   * channel
   * VOD
   * recording
   * disk
5. Save the settings.

In JSON, routing lives under `notification_routing` and uses this shape:

```text
dvr_id -> event_type -> destination -> true or false
```

Missing route entries default to enabled. Set a destination to `false` only when you want to block that destination for that DVR and event type.

## Verify both DVRs are connected

Use these checks after saving:

1. Open the dashboard.
2. Use the DVR picker to switch from the aggregate view to each DVR.
3. Confirm each DVR shows status, disk, streams, or activity data.
4. Trigger or wait for an event on the added DVR.
5. Check logs if a DVR stays empty or stale. Look for connection errors with that DVR's host and port.

If the UI does not update, restart the ChannelWatch container. If the DVR still does not connect, confirm the container can reach the DVR host and port.

## Removing a DVR

Use removal when a DVR is retired or you no longer want ChannelWatch to monitor it.

ChannelWatch supports soft delete and restore semantics:

* Soft delete archives the DVR by setting `deleted_at` in `settings.json`.
* Archived DVRs stay in the config, stop being monitored, and can be restored while the archived entry still exists.
* Restore clears `deleted_at` and returns the DVR to active monitoring.
* Permanent delete removes the DVR entry and DVR-scoped state that ChannelWatch can safely clean up.

Prefer soft delete if you might bring the DVR back. Use permanent delete only when you are sure the old entry and its local state are no longer needed.

## Limits

ChannelWatch v0.9 supports multiple DVRs in one application instance, but it is not a clustered multi-replica app.

Known limits from the changelog:

* The core logs a soft warning when more than 10 enabled DVRs are configured.
* Larger setups are not blocked, but v0.9 does not include a UI override control for the warning.
* `/config/dvrs.yaml` and `CW_DVR_<N>_*` are deferred. Use the UI, `CHANNELS_DVR_SERVERS`, or `/config/settings.json` instead.
* The Helm chart is single replica only because ChannelWatch uses shared writable state under `/config`.
