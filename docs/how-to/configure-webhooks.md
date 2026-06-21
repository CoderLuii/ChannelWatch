# Configure outbound webhooks

Use this guide to send ChannelWatch alerts to an external HTTP receiver, verify the HMAC signature, and confirm that your receiver logs a test delivery.

For the full payload schema, headers, retry behavior, and HMAC signing rules, see the [webhook reference](../reference/webhook.md). For message formatting options before delivery, see the [notification templates reference](../reference/templates.md). If webhook delivery fails after setup, use [troubleshoot notifications](troubleshoot-notifications.md).

## When to use webhooks

Use outbound webhooks when you want ChannelWatch alerts to trigger another system, such as an automation service, incident workflow, dashboard, or custom script.

Webhooks are a good fit when the receiver can expose an HTTP endpoint, store a shared secret, and verify `X-ChannelWatch-Signature` before acting on a payload.

## Prerequisites

Before you start, have:

1. A ChannelWatch instance with access to the Settings page.
2. A receiver URL that ChannelWatch can reach from its container or host network.
3. A shared HMAC secret for the receiver. The examples read it from `CHANNELWATCH_WEBHOOK_SECRET`.
4. Python with Flask, or Node.js with Express, if you want to run one of the sample receivers below.

Do not paste a real secret into docs, screenshots, tickets, or logs.

## Step 1: Configure webhook URL in Settings

1. Open ChannelWatch.
2. Go to **Settings**.
3. Find the webhook notification settings.
4. Add a webhook receiver URL, for example `http://receiver.example.com/channelwatch`.
5. Enable the webhook destination.
6. Save the settings.

For local testing from a container, make sure the URL is reachable from the ChannelWatch container, not only from your browser. For example, a receiver running on your workstation may need a Docker host name or LAN IP instead of `localhost`.

## Step 2: Set HMAC secret

1. In the same webhook settings row, set the secret to the same value your receiver will use.
2. Use the same value in ChannelWatch and in the receiver's `CHANNELWATCH_WEBHOOK_SECRET` environment variable.
3. Save the settings again.

ChannelWatch skips webhook delivery when the secret is empty or still set to the masked value `****`. If you see a saved secret displayed as `****`, that is the UI mask, not the value you should copy into a receiver.

## Step 3: Run a sample receiver

Run one of these receivers on a host that ChannelWatch can reach. Both examples verify `X-ChannelWatch-Signature` against the raw request body before reading the JSON payload. Set `CHANNELWATCH_WEBHOOK_SECRET` to the same secret saved in ChannelWatch before starting the receiver.

### Python Flask receiver

Save this as `receiver.py`:

```text
import hashlib
import hmac
import os

from flask import Flask, abort, request

WEBHOOK_SECRET = os.environ["CHANNELWATCH_WEBHOOK_SECRET"]


def verify_channelwatch_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


app = Flask(__name__)


@app.post("/channelwatch")
def channelwatch_webhook():
    body = request.get_data()
    signature = request.headers.get("X-ChannelWatch-Signature", "")
    if not verify_channelwatch_signature(WEBHOOK_SECRET, body, signature):
        print("signature failed")
        abort(401)

    payload = request.get_json(force=True)
    print(
        "signature ok "
        f"event={payload.get('eventType')} "
        f"delivery={payload.get('deliveryId')}"
    )
    return {"ok": True}
```

Install Flask if needed, then run:

```text
python3 -m pip install flask
CHANNELWATCH_WEBHOOK_SECRET=use-a-private-random-value flask --app receiver run --host 0.0.0.0 --port 9000
```

Use this ChannelWatch webhook URL if the receiver is reachable on the same host and port:

```text
http://<receiver-host>:9000/channelwatch
```

### Node.js Express receiver

Save this as `receiver.js`:

```javascript
const crypto = require("node:crypto");
const express = require("express");

const app = express();
const secret = process.env.CHANNELWATCH_WEBHOOK_SECRET;

if (!secret) {
  throw new Error("Set CHANNELWATCH_WEBHOOK_SECRET before starting the receiver.");
}

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
    console.log("signature failed");
    return res.sendStatus(401);
  }

  const payload = JSON.parse(req.body.toString("utf8"));
  console.log(
    `signature ok event=${payload.eventType} delivery=${payload.deliveryId}`,
  );
  return res.json({ ok: true });
});

app.listen(9000, () => {
  console.log("ChannelWatch receiver listening on port 9000");
});
```

Install Express if needed, then run:

```text
npm install express
CHANNELWATCH_WEBHOOK_SECRET=use-a-private-random-value node receiver.js
```

Use this ChannelWatch webhook URL if the receiver is reachable on the same host and port:

```text
http://<receiver-host>:9000/channelwatch
```

## Step 4: Trigger a test alert

After the receiver is running and the webhook settings are saved, trigger a ChannelWatch test notification from Settings.

The webhook reference lists the current event type mapping. A test notification is sent as `test` when the notification title contains `[test]`.

## Step 5: Verify signature in receiver logs

Watch the receiver terminal after triggering the test alert. A successful delivery should log a line like this:

```text
signature ok event=test delivery=<delivery-id>
```

If the receiver logs `signature failed`, check that ChannelWatch and the receiver use the same exact secret. Also confirm the receiver verifies the raw request body bytes. Parsing and reserializing JSON before the HMAC check changes the signed bytes and will fail validation.

## Troubleshoot

| Symptom | What to check |
|---|---|
| No request reaches the receiver | Confirm the webhook destination is enabled, the URL is reachable from the ChannelWatch container, and the receiver is listening on the expected host and port. |
| ChannelWatch skips delivery | Confirm the webhook secret is not empty and is not the masked value `****`. |
| Receiver returns 401 | Confirm the receiver and ChannelWatch use the same secret, and verify the HMAC against the raw request body. |
| Receiver sees duplicate deliveries | Webhook retries can resend the same payload. Use `deliveryId` to ignore duplicates. |
| Remote receiver fails over HTTPS | Check the receiver certificate and hostname. ChannelWatch verifies TLS certificates by default. |
| Message text is not what you expect | Review [notification templates](../reference/templates.md) to adjust the message before webhook delivery. |

For payload fields, header names, HMAC details, retries, and security notes, use the [webhook reference](../reference/webhook.md). For notification wide delivery problems, continue with [troubleshoot notifications](troubleshoot-notifications.md).
