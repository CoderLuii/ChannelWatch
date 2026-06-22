# Notification template variable reference

ChannelWatch notification templates use a custom placeholder engine implemented in `app/core/notifications/template_engine.py`. The engine is not Jinja2, Mustache, or Python execution. It replaces single brace placeholders such as `{channel_name}` with values from the alert context.

This reference shows variable names with double braces in tables so they stand out. In real templates, write them with single braces, for example `{channel_name}`.

## Supported syntax

| Syntax | Example | Result |
| --- | --- | --- |
| Placeholder | `{channel_name}` | Inserts the value of `channel_name`. |
| Prefix | `{Channel: <channel_number}` | Prints `Channel: 608` only when `channel_number` has a value. |
| Suffix | `{disk_percent> used}` | Prints `91.4% used` only when `disk_percent` has a value. |
| Prefix and suffix | `{Rating: <content_rating>}` | Prints `Rating: TV-MA` only when `content_rating` has a value. |
| Title case | `{title!c}` | Converts text to title case. |
| Uppercase | `{severity!u}` | Converts text to uppercase. |
| Lowercase | `{recording_status!l}` | Converts text to lowercase. |
| List index | `{genres:[0]}` | Prints the first item from a list or comma separated string. |
| List slice | `{cast:[:3]}` | Prints up to the first three values, joined with commas. |
| Conditional block | `<movie>Movie: {title}</movie>` | Keeps the block only when the matching condition is true. |

Supported conditional blocks are `<movie>`, `<episode>`, `<show>`, `<live>`, `<recorded>`, `<started>`, `<completed>`, `<failed>`, and `<cancelled>`.

Conditional block rules:

| Tag | Kept when |
| --- | --- |
| `<movie>` | `media_type` is `movie`. |
| `<episode>` | `media_type` is `episode`. |
| `<show>` | `media_type` is `episode` or `show`. |
| `<live>` | `is_live` is true, `true`, `True`, `yes`, or `Yes`. |
| `<recorded>` | `is_live` is not one of the live values. |
| `<started>` | `recording_status` is `started`. |
| `<completed>` | `recording_status` is `completed`. |
| `<failed>` | `recording_status` is `failed`. |
| `<cancelled>` | `recording_status` is `cancelled`. |

Unknown placeholders, invalid placeholder names, unsupported case modifiers, and unsupported format specifiers raise a template render error. The alert formatter catches those errors and falls back to the default notification text.

## Shared variables

These variables come from `AlertFormatter.build_context()` and are available to channel watching, VOD watching, recording events, and disk space templates.

| Variable | Type | Source | Example value | Description |
| --- | --- | --- | --- | --- |
| `{{server_name}}` | string | All alert types with a DVR object | `Living Room DVR` | DVR display name from the runtime DVR object. Use this for DVR identity in current templates. |
| `{{server_url}}` | string | All alert types with a DVR object | `http://192.168.1.10:8089` | DVR URL built from the DVR host and port. |
| `{{server_version}}` | string | All alert types with a DVR object | `2026.04.20.0213` | Channels DVR server version if the runtime DVR object has one. |
| `{{channelwatch_version}}` | string | All alert types | `0.9.2` | ChannelWatch application version. |
| `{{alert_type}}` | string | All alert types | `channel_watching` | Internal alert type key. |
| `{{alert_type_friendly}}` | string | All alert types | `Channel Watching` | Human readable alert type label. |
| `{{timestamp}}` | string | All alert types | `2026-04-26 14:30:05` | Local render timestamp. |
| `{{datestamp}}` | string | All alert types | `04/26/2026` | Local render date. |
| `{{current_year}}` | string | All alert types | `2026` | Four digit local year. |
| `{{current_month}}` | string | All alert types | `04` | Two digit local month. |
| `{{current_day}}` | string | All alert types | `26` | Two digit local day of month. |
| `{{current_time}}` | string | All alert types | `14:30:05` | Local render time. |
| `{{unixtime}}` | string | All alert types | `1777213805` | Unix timestamp at render time. |

`{{dvr_name}}` and `{{dvr_id}}` are not template variables in the current engine context. Notification routing may receive DVR identity separately, but templates cannot render those names unless the code adds them to the template context.

## Channel Watching

Populated by `app/core/alerts/common/alert_formatter.py` and `app/core/alerts/channel_watching.py` for `channel_watching` templates.

| Variable | Type | Source | Example value | Description |
| --- | --- | --- | --- | --- |
| `{{channel_number}}` | string | Channel Watching | `608` | Channel number being watched. |
| `{{channel_name}}` | string | Channel Watching | `FX HD` | Channel name being watched. |
| `{{program_title}}` | string | Channel Watching | `The Bear` | Current guide program title when program lookup is enabled. |
| `{{client_name}}` | string | Channel Watching | `Apple TV Living Room` | Watching device name. |
| `{{device_name}}` | string | Channel Watching | `Apple TV Living Room` | Same device name exposed under a device focused key. |
| `{{client_ip}}` | string | Channel Watching | `192.168.1.45` | Watching device IP address when known. |
| `{{stream_source}}` | string | Channel Watching | `TVE` | Stream source reported for the session. |
| `{{resolution}}` | string | Channel Watching | `1080p` | Stream resolution from channel session data. |
| `{{stream_count}}` | string or number | Channel Watching | `2` | Total active streams when stream count is enabled. |
| `{{channel_logo}}` | string | Channel Watching | `http://dvr:8089/channel-logo/608.png` | Selected channel or program image URL. |
| `{{program_image}}` | string | Channel Watching | `http://dvr:8089/dvr/image/123.jpg` | Program artwork URL when guide data provides one. |
| `{{image_url}}` | string | Channel Watching | `http://dvr:8089/channel-logo/608.png` | Image URL sent with the notification. |
| `{{is_live}}` | string | Channel Watching | `Yes` | Marks the alert as live content for conditional tags. |
| `{{program_summary}}` | string | Channel Watching | `The team prepares for dinner service.` | Program description from guide data. |
| `{{content_rating}}` | string | Channel Watching | `TV-MA` | Program content rating. |
| `{{genres}}` | list | Channel Watching | `Drama, Comedy` | Program genres. Lists render as comma separated text. |
| `{{cast}}` | list | Channel Watching | `Jeremy Allen White, Ayo Edebiri` | Program cast. Lists render as comma separated text. |
| `{{categories}}` | list | Channel Watching | `Series` | Program categories from guide data. |
| `{{episode_title}}` | string | Channel Watching | `Doors` | Episode title when guide data includes one. |
| `{{season_number}}` | string or number | Channel Watching | `3` | Season number from guide data. |
| `{{season_number00}}` | string | Channel Watching | `03` | Zero padded season number. |
| `{{episode_number}}` | string or number | Channel Watching | `1` | Episode number from guide data. |
| `{{episode_number00}}` | string | Channel Watching | `01` | Zero padded episode number. |
| `{{program_duration}}` | string | Channel Watching | `1h 2m` | Program duration as formatted by upstream metadata. |
| `{{program_duration_secs}}` | string or number | Channel Watching | `3720` | Program duration in seconds. |

## Disk Space

Populated by `app/core/alerts/disk_space.py` for `disk_space` templates. The disk context is built only when custom disk templates are enabled.

| Variable | Type | Source | Example value | Description |
| --- | --- | --- | --- | --- |
| `{{disk_path}}` | string | Disk Space | `/shares/DVR` | Monitored DVR storage path. |
| `{{disk_label}}` | string | Disk Space | `Living Room DVR` | DVR name if available, otherwise `DVR Storage`. |
| `{{disk_total}}` | string | Disk Space | `2.0 TB` | Total disk capacity formatted for display. |
| `{{disk_total_bytes}}` | number | Disk Space | `2000398934016` | Total disk capacity in bytes. |
| `{{disk_used}}` | string | Disk Space | `1.8 TB` | Used disk space formatted for display. |
| `{{disk_used_bytes}}` | number | Disk Space | `1800000000000` | Used disk space in bytes. |
| `{{disk_free}}` | string | Disk Space | `186.3 GB` | Free disk space formatted for display. |
| `{{disk_free_bytes}}` | number | Disk Space | `200000000000` | Free disk space in bytes. |
| `{{disk_percent}}` | string | Disk Space | `91.4%` | Used space percentage formatted with a percent sign. |
| `{{disk_percent_num}}` | string | Disk Space | `91.4` | Used space percentage without the percent sign. |
| `{{threshold}}` | string | Disk Space | `10% or 50 GB free` | Threshold text for the active warning or critical level. |
| `{{threshold_num}}` | number | Disk Space | `10` | Active percentage threshold. |
| `{{recording_count}}` | string | Disk Space | `` | Reserved in context, currently populated as an empty string. |
| `{{oldest_recording}}` | string | Disk Space | `` | Reserved in context, currently populated as an empty string. |
| `{{oldest_recording_date}}` | string | Disk Space | `` | Reserved in context, currently populated as an empty string. |
| `{{severity}}` | string | Disk Space | `critical` | Disk severity, usually `warning` or `critical`. |

## VOD Watching

Populated by `app/core/alerts/vod_watching.py` for `vod_watching` templates.

| Variable | Type | Source | Example value | Description |
| --- | --- | --- | --- | --- |
| `{{media_type}}` | string | VOD Watching | `movie` | Lowercase media type from DVR metadata. Drives `<movie>`, `<episode>`, and `<show>` tags. |
| `{{title}}` | string | VOD Watching | `Inception` | Formatted content title. For episodes, current code also uses this as `show_name`. |
| `{{show_name}}` | string | VOD Watching | `Breaking Bad` | Show name field. Current code populates it from the formatted title. |
| `{{episode_title}}` | string | VOD Watching | `Pilot` | Episode title when available. |
| `{{season_number}}` | string or number | VOD Watching | `1` | Season number from DVR metadata. |
| `{{season_number00}}` | string | VOD Watching | `01` | Zero padded season number. |
| `{{episode_number}}` | string or number | VOD Watching | `1` | Episode number from DVR metadata. |
| `{{episode_number00}}` | string | VOD Watching | `01` | Zero padded episode number. |
| `{{client_name}}` | string | VOD Watching | `iPad Kitchen` | Watching device name. |
| `{{device_name}}` | string | VOD Watching | `iPad Kitchen` | Same device name exposed under a device focused key. |
| `{{client_ip}}` | string | VOD Watching | `192.168.1.51` | Watching device IP address. |
| `{{summary}}` | string | VOD Watching | `A thief enters people's dreams.` | Formatted short summary. |
| `{{full_summary}}` | string | VOD Watching | `A longer movie summary...` | Raw summary from DVR metadata. |
| `{{image_url}}` | string | VOD Watching | `http://dvr:8089/dvr/image/movie.jpg` | Artwork URL sent with the notification. |
| `{{thumbnail_url}}` | string | VOD Watching | `http://dvr:8089/dvr/image/thumb.jpg` | Thumbnail URL when metadata provides one. |
| `{{duration}}` | string | VOD Watching | `2h 28m` | Formatted duration. |
| `{{duration_secs}}` | string or number | VOD Watching | `8880` | Duration in seconds from DVR metadata. |
| `{{playback_time}}` | string | VOD Watching | `45m 12s` | Current playback position. |
| `{{progress_percent}}` | string or number | VOD Watching | `32` | Playback progress percent from DVR metadata. |
| `{{content_rating}}` | string | VOD Watching | `PG-13` | Content rating. |
| `{{release_year}}` | string or number | VOD Watching | `2010` | Release year. |
| `{{release_date}}` | string | VOD Watching | `07/16/2010` | Release date. |
| `{{genres}}` | list | VOD Watching | `Action, Sci-Fi, Thriller` | Genres. Lists render as comma separated text. |
| `{{cast}}` | list | VOD Watching | `Leonardo DiCaprio, Elliot Page` | Cast. Lists render as comma separated text. |
| `{{directors}}` | list | VOD Watching | `Christopher Nolan` | Directors. Lists render as comma separated text. |
| `{{tags}}` | list | VOD Watching | `HD, 5.1, CC` | Quality or media tags. |
| `{{categories}}` | list | VOD Watching | `Movie` | Metadata categories. |
| `{{watched}}` | boolean | VOD Watching | `Yes` | Whether the item is marked watched. Booleans render as Yes or No. |
| `{{favorited}}` | boolean | VOD Watching | `No` | Whether the item is marked favorite. Booleans render as Yes or No. |
| `{{library_path}}` | string | VOD Watching | `/shares/DVR/Movies/Inception.mpg` | Media file path from metadata. |
| `{{channel}}` | string | VOD Watching | `502` | Source channel for recorded media when available. |
| `{{media_title}}` | string | VOD Watching | `Inception` | First line of the default VOD message. |
| `{{progress_line}}` | string | VOD Watching | `Progress: 45m 12s / 2h 28m` | Default progress line when duration and progress are known. |
| `{{summary_block}}` | string | VOD Watching | `A thief enters people's dreams.` | Summary wrapped for default message layout. |
| `{{info_sections}}` | string | VOD Watching | `Rating: PG-13` | Joined metadata lines from the default VOD message. |

## Recording Events

Populated by `app/core/alerts/recording_events.py` for `recording_events` templates.

| Variable | Type | Source | Example value | Description |
| --- | --- | --- | --- | --- |
| `{{recording_status}}` | string | Recording Events | `started` | Machine readable recording status. Drives status conditional tags. |
| `{{recording_status_friendly}}` | string | Recording Events | `Recording Started` | Human readable recording status. |
| `{{job_id}}` | string | Recording Events | `job-12345` | Channels DVR recording job identifier when available. |
| `{{title}}` | string | Recording Events | `Jeopardy!` | Recording title. |
| `{{show_name}}` | string | Recording Events | `Jeopardy!` | Show title, falling back to the recording title. |
| `{{episode_title}}` | string | Recording Events | `Show #9021` | Episode title when available. |
| `{{season_number}}` | string or number | Recording Events | `41` | Season number when available. |
| `{{season_number00}}` | string | Recording Events | `41` | Zero padded season number. |
| `{{episode_number}}` | string or number | Recording Events | `5` | Episode number when available. |
| `{{episode_number00}}` | string | Recording Events | `05` | Zero padded episode number. |
| `{{channel_number}}` | string | Recording Events | `7.1` | Recording channel number. |
| `{{channel_name}}` | string | Recording Events | `ABC` | Recording channel name. |
| `{{start_time}}` | string | Recording Events | `07:00 PM` | Scheduled or actual recording start time. |
| `{{end_time}}` | string | Recording Events | `07:30 PM` | Scheduled or actual recording end time. |
| `{{duration}}` | string | Recording Events | `30m` | Recording duration. |
| `{{summary}}` | string | Recording Events | `Contestants compete.` | Program summary. |
| `{{image_url}}` | string | Recording Events | `http://dvr:8089/dvr/image/rec.jpg` | Recording image URL after fallback resolution. |
| `{{content_rating}}` | string | Recording Events | `TV-G` | Content rating. |
| `{{genres}}` | list | Recording Events | `Game Show` | Genres. Lists render as comma separated text. |
| `{{cast}}` | list | Recording Events | `Ken Jennings` | Cast. Lists render as comma separated text. |
| `{{error_message}}` | string | Recording Events | `Tuner unavailable` | Error text for failed recordings. |
| `{{file_path}}` | string | Recording Events | `/shares/DVR/TV/Jeopardy.mpg` | Recording file path when available. |
| `{{is_pass}}` | boolean | Recording Events | `Yes` | Whether the recording belongs to a pass. |
| `{{pass_name}}` | string | Recording Events | `Jeopardy!` | Recording pass name. |
| `{{completed}}` | boolean | Recording Events | `Yes` | Whether the recording completed. |
| `{{corrupted}}` | boolean | Recording Events | `No` | Whether Channels DVR marks the recording corrupted. |
| `{{processed}}` | boolean | Recording Events | `Yes` | Whether post processing is complete. |
| `{{media_type}}` | string | Recording Events | `episode` | `episode` when an episode title exists, otherwise `show`. |
| `{{status}}` | string | Recording Events | `Recording Started` | Alias for `recording_status_friendly`, used by the default template. |
| `{{details}}` | string | Recording Events | `Jeopardy!` | Alias for the recording title, used by the default template. |
| `{{summary_block}}` | string | Recording Events | `Contestants compete.` | Summary value used by the default template. |
| `{{default_message}}` | string | Recording Events | `Status: Recording Started` | Fully formatted default message for the event. |

Recording API and UI payloads also include an `artwork_fallback_exhausted` boolean. It is not a template placeholder; it is a response/UI flag set only after ChannelWatch tries every recording artwork tier (cc4c lookup, DVR artwork, then channel logo) without finding an image. Consumers that receive this field should prefer it over inferring fallback exhaustion from a missing `image_url` alone.

## Stream Events and Server Health

The current template engine is used by channel watching, VOD watching, recording events, and disk space alerts. There are no separate template contexts for generic stream events or server health alerts in `app/core/notifications/template_engine.py` or the alert formatter wiring. If those alert types are added later, their variables should be documented here from their context builder before users rely on them.

## Examples

### Minimal channel template

Title:

```text
{client_name} is watching {channel_name}
```

Body:

```text
{program_title}
{Channel: <channel_number}
{Device: <client_name}
```

### Markdown formatted disk template

Title:

```text
Disk space {severity!u}: {disk_label}
```

Body:

```markdown
**{disk_label}** is low on storage.

* Used: `{disk_used}` of `{disk_total}` (`{disk_percent}`)
* Free: `{disk_free}`
* Path: `{disk_path}`
* Threshold: `{threshold}`
```

### Conditional VOD template

```text
<movie>Movie: {title} ({release_year})</movie><episode>Episode: {show_name} S{season_number00}E{episode_number00} - {episode_title}</episode>
{Progress: <progress_percent>%}
{Starring: <cast:[:3]}
```

### Conditional recording failure template

```text
{recording_status_friendly}: {title}
<failed>Error: {error_message}</failed><completed>Saved to {file_path}</completed>
{Channel: <channel_name>}
```

## See also

* `docs/reference/settings.md` for template fields in settings, when that reference page exists
* `app/core/notifications/template_engine.py`
