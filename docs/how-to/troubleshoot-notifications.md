# Troubleshoot notifications

Use this guide when ChannelWatch is running but notifications are missing, failing, or surprising. Start with the symptom that matches what you see, then run the checks in order. The first checks use the delivery log and settings because they are fast and usually point to the failed layer.

Useful evidence sources:

- `GET /api/v1/notification-log`, the canonical delivery log. See [`docs/reference/api.md`](../reference/api.md).
- The Diagnostics page and `GET /api/v1/debug/bundle`, which collect sanitized settings and recent logs. See [`docs/reference/health-diagnostics.md`](../reference/health-diagnostics.md).
- Container logs, usually `/config/channelwatch.log` through the Diagnostics page or `GET /api/logs`.
- Provider setup details in [`docs/reference/apprise-providers.md`](../reference/apprise-providers.md) and webhook signing details in [`docs/reference/webhook.md`](../reference/webhook.md).

## Quick decision tree

1. Nothing reaches any provider? Go to [Provider not delivering at all](#provider-not-delivering-at-all).
2. A Settings test works, but normal alerts are silent? Go to [Test notification works but real alerts don't](#test-notification-works-but-real-alerts-dont).
3. The delivery log shows webhook HTTP errors? Go to [Webhook returns 4xx/5xx in delivery log](#webhook-returns-4xx5xx-in-delivery-log).
4. You receive more than one copy of the same alert? Go to [Notifications duplicated](#notifications-duplicated).
5. Notifications worked, then went quiet? Go to [Notifications stopped after working](#notifications-stopped-after-working).
6. Only one configured DVR produces alerts? Go to [Multi-DVR: only one DVR alerts firing](#multi-dvr-only-one-dvr-alerts-firing).
7. You need evidence for support or a bug report? Go to [Where do I look for evidence](#where-do-i-look-for-evidence).

## Provider not delivering at all

Symptom: no notification arrives through Discord, Pushover, email, Telegram, Slack, Gotify, Matrix, custom Apprise, or native webhooks.

Checks:

1. Open the delivery log with `GET /api/v1/notification-log?offset=0&limit=20`.
   - If there are no rows, ChannelWatch may not be generating notification events. Continue with routing and alert source checks in the next section.
   - If rows exist, compare `provider_type`, `channel`, `status`, `retry_count`, and `error` with the provider you expected.
2. Check that the provider value is complete and placeholder-free.
   - Apprise destinations use the configured fields listed in [`docs/reference/apprise-providers.md`](../reference/apprise-providers.md), such as `apprise_discord`, `apprise_email`, or `apprise_custom`.
   - Native webhooks need `webhooks[].url`, `webhooks[].secret`, and `webhooks[].enabled`.
3. Restart the core process or container after changing notification settings. The provider reference notes that notification settings are restart-required.
4. Check container logs for provider initialization and send failures. In the web UI, use Diagnostics logs. From the API, use `GET /api/logs?lines=100`.
5. For Discord embeds or Apprise notifications with images, check whether the image URL was dropped by the SSRF guard.
   - Private, loopback, link local, reserved, metadata, non-HTTP, and hostname-less image URLs are blocked.
   - When this happens, ChannelWatch should still send the text notification without the image. If text arrives but the image is missing, use a public HTTPS image URL.

## Test notification works but real alerts don't

Symptom: the Settings test action reaches the provider, but disk, channel, VOD, or recording alerts do not arrive.

Checks:

1. Open `GET /api/v1/notification-log?offset=0&limit=20` and compare a successful test row with a missing real alert.
   - Look at `event_type`, `dvr_id`, `provider_type`, `channel`, and `status`.
   - A test proves the provider credential can work. It does not prove every route or alert source is enabled.
2. Check `notification_routing` for the DVR and event type.
   - Route keys are `channel`, `vod`, `recording`, and `disk`.
   - Destination keys are `pushover`, `discord`, `email`, `telegram`, `slack`, `gotify`, `matrix`, `custom`, and `webhook`.
3. Check that the alert source itself is enabled in settings.
   - If disk alerts are quiet, confirm disk alert thresholds and disk alert enablement.
   - If watching or VOD alerts are quiet, confirm the relevant activity alert settings.
   - If recording alerts are quiet, confirm recording notifications are enabled.
4. Check `/healthz/ready` or the Diagnostics page. If a DVR monitor is stale, dead, missing, or not ready, real alert generation for that DVR can stop even though a manual provider test still sends.
5. Use container logs to confirm whether the core saw the source event. If no source event is logged, troubleshoot DVR connectivity before provider delivery.

## Webhook returns 4xx/5xx in delivery log

Symptom: `GET /api/v1/notification-log` shows webhook rows with failed status, non-2xx errors, or repeated retries.

Checks:

1. Read the newest webhook rows in `GET /api/v1/notification-log?offset=0&limit=20`.
   - Confirm `provider_type` is `webhook`.
   - Check `status`, `retry_count`, `error`, `event_type`, and `dvr_id`.
2. If the receiver returns `401`, `403`, or another auth error, check receiver authentication first.
   - Confirm the receiver expects ChannelWatch's outbound webhook format, not an inbound Channels DVR webhook.
   - Confirm any bearer token, allowlist, reverse proxy auth, or path-specific auth on the receiver.
3. Check HMAC configuration.
   - ChannelWatch signs the compact JSON body with HMAC-SHA256.
   - The header is `X-ChannelWatch-Signature` and the value format is `sha256=<hex digest>`.
   - The receiver must verify the raw request body bytes before JSON parsing changes the body.
   - See [`docs/reference/webhook.md`](../reference/webhook.md) for Python and Node.js verification examples.
4. Confirm `webhooks[].secret` is set to the real shared secret.
   - ChannelWatch skips native webhook delivery when the secret is empty or still masked as `****`.
5. For `5xx` responses, inspect the receiver logs. ChannelWatch retries each enabled endpoint up to 3 attempts, then records failure.

## Notifications duplicated

Symptom: the same alert appears more than once in the destination.

Checks:

1. Open `GET /api/v1/notification-log?offset=0&limit=50` and compare duplicate rows.
   - If `deliveryId` differs in the receiver but `activity_event_id` or `event_type` is the same in ChannelWatch, the alert may have been generated twice.
   - If the same webhook `deliveryId` appears more than once at the receiver, it may be a retry after a timeout or non-2xx response.
2. Check route overlap.
   - A provider can be enabled for more than one route. Confirm only the intended destination keys are enabled under the relevant `notification_routing` route.
3. Check the dedup window behavior by comparing timestamps in the delivery log and recent activity.
   - Duplicates outside the dedup window may be new events.
   - Duplicates inside the window suggest route overlap, retry behavior, or repeated source events.
4. In multi DVR setups, compare `dvr_id`.
   - The same title from two DVRs may be valid if both DVRs report the same type of event.
   - Per DVR scoping means `notification_routing` can intentionally send different routes for different DVR ids.
5. For native webhook receivers, make the receiver idempotent with `deliveryId`. Retries can send the same payload more than once if the receiver processes the request but returns an error or times out.

## Notifications stopped after working

Symptom: notifications used to arrive, then stopped without an obvious settings change.

Checks:

1. Check `GET /api/v1/notification-log?offset=0&limit=20`.
   - A row with `status` such as `circuit_open` points to the circuit breaker.
   - Repeated failed webhook rows point to receiver, network, or auth failures.
2. Wait for webhook circuit breaker recovery if the log shows `circuit_open`.
   - The shared webhook circuit breaker opens after 5 failed webhook notification fanout calls for the same `dvr_id` and `webhook` channel.
   - It stays open for 300 seconds, then resets on the next check.
3. Check backoff and retry behavior.
   - Native webhooks retry each endpoint up to 3 times with 1 second and 2 second waits.
   - A provider-side outage can create a burst of failures before ChannelWatch quiets down.
4. Check provider rate limits and account state.
   - Email, Slack, Telegram, Discord, and hosted webhook receivers may reject or throttle messages even when ChannelWatch settings are unchanged.
5. Check container logs and the Diagnostics page for provider errors, DNS failures, TLS failures, or core restart messages.
6. Download a debug bundle if the problem persists. It includes sanitized settings and the last 500 redacted log lines.

## Multi-DVR: only one DVR alerts firing

Symptom: one DVR sends alerts, but another enabled DVR stays silent.

Checks:

1. Check `/healthz/ready`.
   - The readiness response includes one entry per enabled DVR monitor with `id`, `name`, `monitoring_status`, `freshness_status`, `connected`, `reason`, and freshness timestamps.
   - If the silent DVR is stale, missing, dead, or disconnected in this response, fix DVR connectivity before provider routing.
2. Check the Diagnostics page for per DVR connection and monitoring state. It uses the same health and system information surfaces operators normally need.
3. Open `GET /api/v1/notification-log?offset=0&limit=50` and filter mentally by `dvr_id`.
   - If rows exist only for one `dvr_id`, the silent DVR may not be producing source events.
   - If rows exist for the silent DVR with failed status, troubleshoot the provider or route for that DVR.
4. Check the per DVR routing matrix in settings.
   - `notification_routing` is scoped by DVR id, such as `dvr_main` in the provider reference sample.
   - Confirm the silent DVR has the expected route keys and destination keys enabled.
5. Confirm the DVR itself is enabled and not deleted or archived. `/healthz/ready` only summarizes enabled, non deleted DVR monitors.

## Where do I look for evidence

Use these tools before changing settings again:

1. Delivery log: `GET /api/v1/notification-log?offset=0&limit=20`.
   - Best for delivery status, provider type, route channel, DVR id, retry count, payload size, error text, and activity event id.
2. Diagnostics logs: `GET /api/logs?lines=100`, `GET /api/logs/download`, or the Diagnostics page log viewer.
   - Best for provider initialization, Apprise add failures, webhook retry details, SSRF image drops, and core restart context.
3. Debug bundle: `GET /api/v1/debug/bundle` or `channelwatch doctor debug bundle --output /config/channelwatch_debug_bundle.zip`.
   - Best for sharing sanitized settings and recent redacted logs with support.
   - Requires admin access through the API endpoint.
4. Readiness: `/healthz/ready`.
   - Best for proving whether each enabled DVR monitor is ready, stale, missing, or disconnected.
5. Receiver logs.
   - Best for webhook `4xx` and `5xx` failures, HMAC mismatches, auth failures, and provider-side rate limits.

Do not start by editing internal databases or private state files. The delivery log, readiness endpoint, Diagnostics page, debug bundle, and container logs usually show the failed layer without risking persisted state.

## See also

- [`docs/reference/api.md`](../reference/api.md), including `GET /api/v1/notification-log`.
- [`docs/reference/health-diagnostics.md`](../reference/health-diagnostics.md), including debug bundles and Diagnostics UI behavior.
- [`docs/reference/webhook.md`](../reference/webhook.md), including HMAC signing, retry behavior, and webhook failure modes.
- [`docs/reference/apprise-providers.md`](../reference/apprise-providers.md), including supported destination keys, routing, and SSRF image handling.
