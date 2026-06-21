# Webhook reference

ChannelWatch can send outbound webhook notifications to external HTTP receivers. These webhooks are for ChannelWatch events going out to automation tools, dashboards, and custom services. They are not inbound Channels DVR webhooks.

## Overview

ChannelWatch sends a webhook when the notification pipeline sends an alert and webhook delivery is enabled for that alert route. Each configured webhook receives the same JSON envelope, signed with its shared secret.

The current sender derives `eventType` from the notification title, message, and optional image URL. The implemented outbound event types are:

| Event type | When it is sent |
|---|---|
| `test` | A notification title contains `[test]`. |
| `channel.watching.start` | A notification title contains `watching tv`. |
| `vod.playback.start` | A notification title contains `watching dvr content`. |
| `disk.space.critical` | A notification title contains `low disk space critical`. |
| `disk.space.warning` | A notification title contains `low disk space warning`. |
| `recording.scheduled` | A recording event notification message contains `scheduled`. |
| `recording.started` | A recording event notification message contains `started`. |
| `recording.cancelled` | A recording event notification message contains `cancelled`. |
| `recording.completed` | A recording event notification message contains `completed` or `stopped`. |
| `recording.updated` | A recording event notification does not match a more specific recording state. |
| `alert.notification` | A notification includes `image_url` and no earlier rule matched. |
| `notification` | Fallback for all other notifications. |

The design memo discusses future event specific payloads such as live stream details and recording metadata. The current production payload is a notification envelope with `title`, `message`, and optional `imageUrl` inside `data`.

## Payload schema

ChannelWatch serializes the payload as compact JSON before signing and sending it:

```text
json.dumps(payload, separators=(",", ":")).encode("utf-8")
```

Example payload:

```json
{
  "eventType": "disk.space.warning",
  "timestamp": "2026-04-26T18:30:00Z",
  "instanceName": "ChannelWatch",
  "instanceUrl": "http://localhost:8501",
  "version": "0.9.0",
  "deliveryId": "8d79f4f7-5c46-4f31-b812-dc17f5c6cf1b",
  "dvr_id": "dvr_1234",
  "dvr_name": "Living Room DVR",
  "data": {
    "title": "Low Disk Space Warning",
    "message": "Living Room DVR is below the configured free space threshold.",
    "imageUrl": null
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `eventType` | string | Yes | Dot notation event name selected by ChannelWatch. See the event type table above. |
| `timestamp` | string | Yes | UTC ISO 8601 timestamp generated when the payload is built. It ends with `Z`. |
| `instanceName` | string | Yes | Application name from the core package. Current value is `ChannelWatch`. |
| `instanceUrl` | string | Yes | Base URL resolved from `CHANNELWATCH_INSTANCE_URL`, `CW_INSTANCE_URL`, then `APP_URL`. Defaults to `http://localhost:8501`. |
| `version` | string | Yes | ChannelWatch version from the core package. |
| `deliveryId` | string | Yes | UUID v4 for this delivery payload. Use it for idempotency in receivers. |
| `dvr_id` | string | Yes | DVR identifier passed through the notification call. Empty string when unavailable. This is the multi DVR v1 identity field. |
| `dvr_name` | string | Yes | DVR display name passed through the notification call. Empty string when unavailable. |
| `data` | object | Yes | Notification details. Current fields are listed below. |
| `data.title` | string | Yes | Notification title passed to the webhook manager. |
| `data.message` | string | Yes | Notification message passed to the webhook manager. |
| `data.imageUrl` | string or null | Yes | Optional image URL from `image_url`. It is `null` when no image URL is supplied. |

Current payload field count: 9 top-level fields, 12 fields when the three nested `data` fields are counted.

### Fields not present in the current payload

The current code does not include a separate `severity` field. Severity is only implied by values such as `disk.space.warning`, `disk.space.critical`, and the notification text.

The current code does not include the raw Channels DVR event payload. Receivers should treat `data.message` as formatted notification text, not as a source event object.

## HMAC signing

Every outbound webhook must have a non-empty shared secret. ChannelWatch skips delivery when the secret is empty or still set to the masked value `****`.

Signature details:

| Item | Value |
|---|---|
| Algorithm | HMAC-SHA256 |
| Header | `X-ChannelWatch-Signature` |
| Format | `sha256=<hex digest>` |
| Signed bytes | The exact compact JSON request body bytes |
| Secret encoding | UTF-8 |

ChannelWatch also sends these headers:

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `X-ChannelWatch-Signature` | `sha256=<hex digest>` |
| `X-ChannelWatch-Delivery` | The payload `deliveryId`. |
| `X-ChannelWatch-Event` | The payload `eventType`. |

### Verify a signature in Python

```python
import hashlib
import hmac


def verify_channelwatch_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")
```

Use the raw request body bytes from your HTTP framework. Do not parse and reserialize the JSON before checking the signature.

### Verify a signature in Node.js

```javascript
import crypto from "node:crypto";

export function verifyChannelWatchSignature(secret, bodyBuffer, signature) {
  const expected =
    "sha256=" +
    crypto.createHmac("sha256", Buffer.from(secret, "utf8"))
      .update(bodyBuffer)
      .digest("hex");

  const provided = Buffer.from(signature || "", "utf8");
  const expectedBuffer = Buffer.from(expected, "utf8");

  return provided.length === expectedBuffer.length &&
    crypto.timingSafeEqual(provided, expectedBuffer);
}
```

## Retry and backoff behavior

Webhook delivery has two layers.

| Layer | Behavior |
|---|---|
| Per webhook endpoint | `WebhookManager` tries each enabled endpoint up to 3 times. Attempt 1 is immediate. Attempts 2 and 3 wait 1 second and 2 seconds. |
| Notification wrapper | `NotificationManager` wraps the webhook fanout in `deliver_with_retry` with `with_retry=False`. This means the wrapper records success or failure and checks the circuit breaker, but does not add another retry loop. |

A per endpoint retry happens after any of these results:

| Condition | Retried? |
|---|---|
| HTTP 2xx | No. Delivery is successful. |
| HTTP non-2xx | Yes, until 3 attempts are used. |
| `httpx.TimeoutException` | Yes, until 3 attempts are used. |
| `httpx.RequestError` | Yes, until 3 attempts are used. |

Current code uses fixed exponential delays of 1 second and 2 seconds. It does not add jitter. The design memo proposed jitter, but it is not implemented in `app/core/notifications/webhook.py`.

Each HTTP attempt uses a 5 second timeout.

## Failure modes

| Failure mode | Current behavior |
|---|---|
| Missing secret | The endpoint is skipped and delivery returns failure for that endpoint. |
| Masked secret, `****` | The endpoint is skipped. This prevents accidentally saving the UI mask as the real secret. |
| Non-2xx response | Logged and retried by the endpoint retry loop. After 3 failed attempts, the endpoint returns failure. |
| Timeout or request error | Logged and retried by the endpoint retry loop. After 3 failed attempts, the endpoint returns failure. |
| Circuit breaker open | The notification wrapper skips webhook fanout and records `circuit_open`. |
| Delivery log unavailable | Delivery still proceeds. Persistence is skipped if the delivery database cannot be opened. |
| Dead letter queue | Not implemented. Failed webhook payloads are not queued for later replay. |

The shared circuit breaker opens after 5 failed webhook notification fanout calls for the same `dvr_id` and `webhook` channel. It stays open for 300 seconds, then resets on the next check.

Delivery records are written through the shared delivery log when `/config/channelwatch.db`, or `CHANNELWATCH_DB`, is available. Records include the DVR id, event type, channel, provider type, status, retry count, payload size, error message, and optional activity event id.

## Security considerations

### Keep the secret private

Use a unique, high entropy secret for each receiver. Do not put real secrets in examples, screenshots, tickets, or logs. ChannelWatch masks webhook secrets as `****` when settings are read back through the UI API.

### Rotate secrets safely

To rotate a secret, accept both the old and new secret on the receiver for a short overlap window. Update ChannelWatch to send with the new secret, confirm successful deliveries, then remove the old secret from the receiver.

### Verify signatures before parsing trusted data

Check `X-ChannelWatch-Signature` against the raw request body before acting on the payload. Use constant time comparison, such as `hmac.compare_digest` in Python or `crypto.timingSafeEqual` in Node.js.

### Treat receiver URLs as external targets

Webhook delivery calls the shared URL safety helper before posting. It skips destinations that use non HTTP schemes, localhost, metadata hostnames, or private, reserved, loopback, or link local IP literals, and logs the skipped URL in redacted form.

### Use TLS for remote receivers

Use `https://` for receivers outside a trusted local network. ChannelWatch uses `httpx`, which verifies TLS certificates by default. Do not disable certificate verification in a reverse proxy or receiver setup unless you have another authenticated transport layer.

### Make receivers idempotent

Use `deliveryId` to ignore duplicate deliveries. Retries can send the same payload more than once when a receiver processes the request but returns an error or times out before ChannelWatch sees the response.

## Example receiver

### Flask receiver

This Flask app validates the signature before reading the JSON payload. It is written as an app factory so importing the file does not bind a port.

```text
import hashlib
import hmac

WEBHOOK_SECRET = "replace-with-your-webhook-secret"


def verify_channelwatch_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def create_app():
    from flask import Flask, abort, request

    app = Flask(__name__)

    @app.post("/channelwatch")
    def channelwatch_webhook():
        body = request.get_data()
        signature = request.headers.get("X-ChannelWatch-Signature", "")
        if not verify_channelwatch_signature(WEBHOOK_SECRET, body, signature):
            abort(401)

        payload = request.get_json(force=True)
        event_type = payload.get("eventType")
        delivery_id = payload.get("deliveryId")
        print(f"received {event_type} delivery {delivery_id}")
        return {"ok": True}

    return app
```

Run it with:

```text
flask --app receiver:create_app run --host 0.0.0.0 --port 9000
```

### Express receiver

Express must keep the raw body bytes for signature validation. This example uses `express.raw` for the webhook route.

```javascript
import crypto from "node:crypto";
import express from "express";

const app = express();
const secret = process.env.CHANNELWATCH_WEBHOOK_SECRET || "replace-with-your-webhook-secret";

function verifyChannelWatchSignature(body, signature) {
  const expected = "sha256=" + crypto
    .createHmac("sha256", Buffer.from(secret, "utf8"))
    .update(body)
    .digest("hex");

  const provided = Buffer.from(signature || "", "utf8");
  const expectedBuffer = Buffer.from(expected, "utf8");

  return provided.length === expectedBuffer.length &&
    crypto.timingSafeEqual(provided, expectedBuffer);
}

app.post("/channelwatch", express.raw({ type: "application/json" }), (req, res) => {
  const signature = req.header("X-ChannelWatch-Signature") || "";
  if (!verifyChannelWatchSignature(req.body, signature)) {
    return res.sendStatus(401);
  }

  const payload = JSON.parse(req.body.toString("utf8"));
  console.log(`received ${payload.eventType} delivery ${payload.deliveryId}`);
  return res.json({ ok: true });
});

app.listen(9000, () => {
  console.log("ChannelWatch receiver listening on port 9000");
});
```

## See also

| Document | Purpose |
|---|---|
| [`docs/how-to/configure-webhooks.md`](../how-to/configure-webhooks.md) | Setup guide for webhook destinations. |
| [`docs/reference/templates.md`](templates.md) | Notification template reference. |
