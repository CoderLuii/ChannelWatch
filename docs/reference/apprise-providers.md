# Apprise provider reference

ChannelWatch sends notifications through a small, configured subset of Apprise destinations. This page covers the providers that are wired into `app/core/notifications/providers/apprise.py`, plus ChannelWatch's separate outbound webhook path.

ChannelWatch does not expose the full Apprise catalog. Providers not listed here need to use `apprise_custom`, or they are out of scope for this release.

## Version

`deploy/requirements/runtime.txt` sets the Apprise dependency as:

```text
apprise>=1.11.0
```


## Supported destination keys

| Destination key | Setting | Scheme used by ChannelWatch | Notes |
|---|---|---|---|
| `pushover` | `apprise_pushover` | `pover://` | Dedicated Pushover field. |
| `discord` | `apprise_discord` | `discord://` | Dedicated Discord field, with direct Discord webhook URL conversion. |
| `email` | `apprise_email`, `apprise_email_to` | `mailto://` or `mailtos://` | Dedicated SMTP field and optional recipient field. |
| `telegram` | `apprise_telegram` | `tgram://` | Dedicated Telegram field. |
| `slack` | `apprise_slack` | `slack://` | Dedicated Slack field. |
| `gotify` | `apprise_gotify` | `gotify://` | Dedicated Gotify field. |
| `matrix` | `apprise_matrix` | `matrix://` | Dedicated Matrix field. |
| `custom` | `apprise_custom` | Any full Apprise URL | Use for Pushbullet, ntfy, JSON, Form, XML, and other one-off Apprise URLs. |
| `webhook` | `webhooks[]` | `http://` or `https://` | ChannelWatch native webhooks. This is not an Apprise provider. |

## Shared behavior

### Delivery path

1. The core loads `/config/settings.json` through `CoreSettings`.
2. `AppriseProvider` builds one URL per non-empty Apprise setting.
3. Discord URLs are sent through ChannelWatch's direct Discord embed sender when `httpx` is available. If that path is not available, ChannelWatch falls back to Apprise for Discord.
4. All other Apprise URLs are added to a fresh Apprise instance and sent together.
5. Native `webhooks[]` are delivered by `WebhookManager`, outside Apprise.

### Routing and severity mapping

Routing is controlled by `notification_routing` and uses these destination keys:

```text
pushover, discord, email, telegram, slack, gotify, matrix, custom, webhook
```

The routing shape is:

```json
{
  "notification_routing": {
    "dvr_main": {
      "disk": {
        "discord": true,
        "pushover": true,
        "email": false,
        "webhook": true
      }
    }
  }
}
```

Missing DVRs, missing event types, and missing destination keys default to enabled. Event routing keys used by alerts are `channel`, `vod`, `recording`, and `disk`.

ChannelWatch does not rewrite severity per provider. Each provider receives the title and body created by the alert template. Disk alerts use warning and critical titles in the message content, and native webhooks expose `disk.space.warning` or `disk.space.critical` as `eventType`. Apprise destinations are filtered by the `disk` route, not by a separate warning or critical route.

### Test instructions for all providers

- Save a placeholder-free provider value in `settings.json` or through the Settings UI.
- Restart the core process or container, because notification settings are restart-required.
- Use the Settings UI test action, or trigger a safe test alert such as a disk test route.
- Check `/config/channelwatch.log` for `Notification services ready`, provider add failures, delivery success, or delivery failure.
- For route-specific tests, set `notification_routing` so only the destination under test is enabled for the event type.
- Never paste real tokens into docs, issue comments, or test fixtures.

## Provider fields

### Pushover

| Field | Value |
|---|---|
| Name | Pushover |
| ChannelWatch setting | `apprise_pushover` |
| Apprise scheme | `pover://` |
| ChannelWatch URL construction | `pover://{apprise_pushover}` |

Required credentials:

- Pushover user key.
- Pushover application token.
- Optional device name or priority query parameters supported by Apprise.

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_pushover` | The credential portion after `pover://`, for example `{user_key}@{app_token}`. |

Example shape:

```text
apprise_pushover = "{user_key}@{app_token}"
```

Routing and severity behavior:

- Controlled by the `pushover` key in `notification_routing`.
- Receives the same ChannelWatch title and body as other Apprise providers.
- Disk warning and critical messages are distinguished by the generated disk alert title and body.

Test:

- Enable only `pushover` for one route, then send a test notification.
- Confirm the mobile device receives one ChannelWatch message.
- If nothing arrives, verify the user key, app token, device filters, and Pushover priority parameters.

### Discord

| Field | Value |
|---|---|
| Name | Discord |
| ChannelWatch setting | `apprise_discord` |
| Apprise scheme | `discord://` |
| ChannelWatch URL construction | `discord://{apprise_discord}` unless a native Discord webhook URL is supplied |

Required credentials:

- Discord webhook ID.
- Discord webhook token.

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_discord` | Either `{webhook_id}/{webhook_token}` or the full `https://discord.com/api/webhooks/{webhook_id}/{webhook_token}` URL. |

ChannelWatch accepts full Discord webhook URLs and converts them to `discord://{webhook_id}/{webhook_token}` internally.

Routing and severity behavior:

- Controlled by the `discord` key in `notification_routing`.
- Uses ChannelWatch's direct Discord embed sender first. The embed title is the notification title, the embed description is the notification body, and `image_url` becomes an embed image if it passes the SSRF guard.
- Falls back to Apprise text delivery when the direct sender is unavailable.

Test:

- Send a test notification and confirm a Discord embed appears in the target channel.
- If the direct webhook URL was pasted, check logs for parsing errors.
- For image tests, use a public HTTPS image URL. Private and localhost image URLs are dropped.

### Email and SMTP

| Field | Value |
|---|---|
| Name | Email or SMTP |
| ChannelWatch settings | `apprise_email`, `apprise_email_to` |
| Apprise scheme | `mailto://` or `mailtos://` |
| ChannelWatch URL construction | `mailto://{apprise_email}` or `mailtos://_?{parameter_string}` |

Required credentials and fields:

- SMTP username or service account.
- SMTP password or app password.
- SMTP host or provider domain.
- Recipient address, usually through `apprise_email_to`.
- Optional sender value. ChannelWatch adds `from=ChannelWatch` when no sender is present.

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_email` | Either a standard Apprise mail credential string, or a parameter string such as `user={user}&pass={password}&smtp={smtp_host}&port=587`. |
| `apprise_email_to` | Recipient email address or addresses supported by Apprise. ChannelWatch appends this as `to=`. |

Example shapes:

```text
apprise_email = "{user}:{app_password}@gmail.com"
apprise_email_to = "alerts@example.com"
```

```text
apprise_email = "user={user}&pass={password}&smtp=smtp.example.com&port=587"
apprise_email_to = "alerts@example.com"
```

Routing and severity behavior:

- Controlled by the `email` key in `notification_routing`.
- Receives HTML-style line breaks from ChannelWatch for non-Discord Apprise delivery.
- Disk severity appears in the subject and body created by ChannelWatch.

Test:

- Send a test notification to a mailbox you control.
- Check spam filtering and SMTP provider security rules if delivery is delayed.
- For Gmail or Yahoo, use an app password where required by the provider.

### Telegram

| Field | Value |
|---|---|
| Name | Telegram |
| ChannelWatch setting | `apprise_telegram` |
| Apprise scheme | `tgram://` |
| ChannelWatch URL construction | `tgram://{apprise_telegram}` |

Required credentials:

- Bot token from BotFather.
- Chat ID for the target user, group, or channel.

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_telegram` | `{bot_token}/{chat_id}` plus any Apprise-supported query parameters. |

Routing and severity behavior:

- Controlled by the `telegram` key in `notification_routing`.
- Receives the same title and body as other non-Discord Apprise providers.
- Disk warning and critical status is part of the message text.

Test:

- Start a chat with the bot before testing.
- Send a test notification and confirm the chat receives the message.
- If the message is missing, recheck the chat ID and bot permissions.

### Slack

| Field | Value |
|---|---|
| Name | Slack |
| ChannelWatch setting | `apprise_slack` |
| Apprise scheme | `slack://` |
| ChannelWatch URL construction | `slack://{apprise_slack}` |

Required credentials:

- Incoming webhook tokens or Bot OAuth token, depending on the Apprise Slack format used.
- Optional channel target.

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_slack` | Slack credential path after `slack://`, for example `{tokenA}/{tokenB}/{tokenC}` for webhook mode. |

Routing and severity behavior:

- Controlled by the `slack` key in `notification_routing`.
- Receives non-Discord Apprise delivery with the ChannelWatch title and body.
- Disk warning and critical status is part of the message text.

Test:

- Send a test notification to a low-risk channel first.
- Confirm the app or webhook has permission to post to that channel.
- If using webhook mode, confirm the three token path segments are in the right order.

### Gotify

| Field | Value |
|---|---|
| Name | Gotify |
| ChannelWatch setting | `apprise_gotify` |
| Apprise scheme | `gotify://` |
| ChannelWatch URL construction | `gotify://{apprise_gotify}` |

Required credentials:

- Gotify host.
- Gotify application token.
- Optional port, path, HTTPS scheme, or priority query parameters supported by Apprise.

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_gotify` | `{host}/{token}` or another Apprise Gotify credential path. |

Routing and severity behavior:

- Controlled by the `gotify` key in `notification_routing`.
- Receives the same title and body as other non-Discord Apprise providers.
- Disk warning and critical status is part of the message text.

Test:

- Send a test notification and confirm it appears in the Gotify app and web UI.
- For self-hosted Gotify, use a URL reachable from inside the ChannelWatch container.
- Avoid private image URLs for artwork. The image URL is dropped before delivery if it points to private space.

### Matrix

| Field | Value |
|---|---|
| Name | Matrix |
| ChannelWatch setting | `apprise_matrix` |
| Apprise scheme | `matrix://` |
| ChannelWatch URL construction | `matrix://{apprise_matrix}` |

Required credentials:

- Matrix homeserver.
- Login credentials or token accepted by Apprise.
- Room alias or room ID.

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_matrix` | Matrix credential path after `matrix://`, for example `{user}:{password}@{host}/#{room}`. |

Routing and severity behavior:

- Controlled by the `matrix` key in `notification_routing`.
- Receives non-Discord Apprise delivery with the ChannelWatch title and body.
- Disk warning and critical status is part of the message text.

Test:

- Send a test notification to a test room.
- Confirm the Matrix user has joined the room or has permission to post.
- URL-encode special characters in passwords, room aliases, and room IDs.

### Custom Apprise URL, including Pushbullet and generic HTTP posts

| Field | Value |
|---|---|
| Name | Custom Apprise URL |
| ChannelWatch setting | `apprise_custom` |
| Apprise scheme | Any full Apprise URL with `://` |
| ChannelWatch URL construction | Uses the value as-is when it contains `://` |

Use this for Apprise providers that ChannelWatch does not expose as named settings. Common examples include:

| Provider | Apprise scheme | Required fields |
|---|---|---|
| Pushbullet | `pbul://` | Pushbullet access token, plus optional device, email, or channel target. |
| ntfy | `ntfy://` or `ntfys://` | Topic, and optional host, username, password, or token. |
| Generic JSON post | `json://` or `jsons://` | Host, path, and optional template or query parameters supported by Apprise. |
| Generic form post | `form://` or `forms://` | Host, path, and optional form parameters supported by Apprise. |
| Generic XML post | `xml://` or `xmls://` | Host, path, and optional template parameters supported by Apprise. |

Configuration:

| Setting field | Enter |
|---|---|
| `apprise_custom` | One complete Apprise URL, for example `pbul://{access_token}` or `jsons://example.com/channelwatch`. |

Routing and severity behavior:

- Controlled by the `custom` key in `notification_routing`, regardless of the actual Apprise scheme.
- Receives the same title and body as other non-Discord Apprise providers.
- Disk warning and critical status is part of the message text.

Test:

- Start with one custom URL only.
- Send a test notification and check both ChannelWatch logs and the target service logs.
- If testing HTTP-style custom URLs, use a public or container-reachable endpoint and avoid credentials in the URL path when possible.

### Native ChannelWatch webhooks

| Field | Value |
|---|---|
| Name | ChannelWatch outbound webhook |
| ChannelWatch setting | `webhooks[]` |
| Scheme | `http://` or `https://` |
| ChannelWatch URL construction | Uses `webhooks[].url` as the request URL |

Required fields:

- `url`, the receiver endpoint.
- `secret`, shared secret used for HMAC SHA256 signing.
- `enabled`, set to `true`.

Configuration:

| Setting field | Enter |
|---|---|
| `webhooks[].url` | Destination URL. |
| `webhooks[].secret` | Shared signing secret. ChannelWatch skips delivery when missing or masked. |
| `webhooks[].enabled` | `true` to enable delivery. |

Routing and severity behavior:

- Controlled by the `webhook` key in `notification_routing`.
- Native webhooks receive a JSON payload with `eventType`, `timestamp`, `instanceName`, `version`, `deliveryId`, DVR fields, and the notification data.
- Disk warnings map to `disk.space.warning`. Disk critical alerts map to `disk.space.critical`.
- Recording messages map to `recording.scheduled`, `recording.started`, `recording.cancelled`, `recording.completed`, or `recording.updated` when the title and body match the detector.

Test:

- Use a receiver that can show headers and body.
- Confirm these headers are present: `X-ChannelWatch-Signature`, `X-ChannelWatch-Delivery`, and `X-ChannelWatch-Event`.
- Verify the HMAC SHA256 signature with the shared secret.

## SSRF guard for notification images

ChannelWatch validates notification `image_url` values before Apprise or Discord delivery. The guard is `is_safe_url()` in `app/core/helpers/url_validator.py`, called from `AppriseProvider.send_notification()`.

Blocked image URL targets include:

- Private and reserved IP ranges, including `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, and `0.0.0.0`.
- Localhost hostnames such as `localhost` and `localhost.localdomain`.
- Cloud metadata hostnames listed in the guard.
- Non-HTTP schemes such as `file://`, `ftp://`, `gopher://`, `data:`, and `javascript:`.
- URLs without a hostname.

When an image URL is blocked, ChannelWatch logs an SSRF warning with the URL redacted, drops the image, and still sends the notification without the image.

There is no `settings.json` allowlist for localhost in this code path. For temporary localhost tests, monkeypatch `is_safe_url()` in the test, or make a short local code change to the guard and revert it before release. For manual testing without code changes, expose the test image or receiver through a public HTTPS URL or another address that the guard treats as public.

## Sample settings.json

This sample uses fake placeholders only. Replace each placeholder with real values in your private `/config/settings.json`.

```json
{
  "apprise_pushover": "{pushover_user_key}@{pushover_app_token}",
  "apprise_discord": "{discord_webhook_id}/{discord_webhook_token}",
  "apprise_email": "user={smtp_user}&pass={smtp_password}&smtp=smtp.example.com&port=587",
  "apprise_email_to": "alerts@example.com",
  "apprise_telegram": "{telegram_bot_token}/{telegram_chat_id}",
  "apprise_slack": "{slack_token_a}/{slack_token_b}/{slack_token_c}",
  "apprise_gotify": "gotify.example.com/{gotify_app_token}",
  "apprise_matrix": "{matrix_user}:{matrix_password}@matrix.example.com/#channelwatch",
  "apprise_custom": "pbul://{pushbullet_access_token}",
  "webhooks": [
    {
      "url": "https://hooks.example.com/channelwatch",
      "secret": "replace-with-a-long-random-secret",
      "enabled": true
    }
  ],
  "notification_routing": {
    "dvr_main": {
      "channel": {
        "discord": true,
        "pushover": false,
        "email": false,
        "telegram": false,
        "slack": false,
        "gotify": false,
        "matrix": false,
        "custom": false,
        "webhook": true
      },
      "disk": {
        "discord": true,
        "pushover": true,
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
}
```

## See also

- `docs/reference/settings.md`
- https://github.com/caronc/apprise
