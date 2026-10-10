# Maintenance windows

The Dashboard's **Maintenance windows** card finds gaps between known Channels DVR recording jobs. It is an advisory view for choosing a quieter time to restart a service, update a host, or do other planned work.

Each enabled DVR is checked separately. Use the Dashboard DVR selector to focus on one server, or select **All DVRs** to compare them. The card lets you set:

- the minimum useful gap, from 30 minutes to 4 hours
- how many days to inspect
- any day, weekdays, or weekends
- the local start and end hours to include, including an overnight range

Times use the timezone configured in ChannelWatch. Overlapping and back-to-back recordings are treated as one busy block. A recording already in progress also stays busy until its scheduled end.

## Safety limits

ChannelWatch only shows time it can bound with known recording jobs. If the returned schedule ends before the requested range, the card labels the result **Known schedule only** and does not mark the unobserved tail as free.

An unreachable DVR, an empty schedule response, or a malformed recording entry produces no suggested window for that server. These states appear as **DVR unavailable** or **Schedule unknown**.

The result is based on recording jobs only. It does not predict live TV use, recorded-content playback, automatic guide work, other DVR activity, or whether another service needs the host. Check the DVR before starting disruptive maintenance.

## API

`GET /api/v1/maintenance-windows` accepts these query parameters:

| Parameter | Default | Range | Purpose |
| --- | ---: | ---: | --- |
| `minimum_minutes` | `60` | `1`–`1440` | Smallest returned gap. |
| `days` | `7` | `1`–`31` | Requested look-ahead range. |
| `start_hour` | `0` | `0`–`23` | First local hour included each day. |
| `end_hour` | `24` | `0`–`24` | Last local hour included each day. A value earlier than `start_hour` creates an overnight range. |
| `weekdays` | every day | comma-separated `0`–`6` | Limits results by local day, where Monday is `0` and Sunday is `6`. |
| `dvr_id` | all enabled DVRs | configured DVR ID | Limits the result to one DVR. |

Every DVR result includes a status, the last verified schedule boundary, any safe windows before that boundary, and a plain-language reason when no window is available.
