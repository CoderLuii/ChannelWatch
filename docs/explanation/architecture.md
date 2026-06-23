# Architecture

## System shape

ChannelWatch is shaped around one practical goal: keep Channels DVR monitoring easy to run on a home server while still separating the parts that have different jobs. The shipped application is one Docker container, but it is not one process. Supervisor starts a monitoring core and a FastAPI UI process side by side, then keeps both alive for as long as the container runs.

That split is intentional. The core process owns long-lived DVR monitoring work, including event stream subscriptions, alert state, metadata caches, disk polling, notification routing, and per-DVR monitor tasks. The UI process owns browser-facing work, including the static Next.js frontend, settings APIs, diagnostics, health endpoints, metrics, logs, backup and restore, and restart controls. Keeping those concerns in separate processes lets the web layer serve requests without sharing an event loop with the monitoring engine, while still keeping deployment simple for users who expect a single container and one `/config` volume.

```mermaid
flowchart TD
    Browser[Browser UI] --> FastAPI[FastAPI UI process]
    FastAPI --> Config[/config settings and SQLite]
    FastAPI --> Supervisor[Supervisor XML RPC]
    Supervisor --> Core[Core monitor process]
    Config --> Core
    DVR[Channels DVR events and APIs] --> Core
    Core --> Alerts[Alert managers and alert sources]
    Alerts --> Notifications[Notification pipeline]
    Notifications --> Providers[Apprise, Pushover, webhooks]
    Core --> Config
    FastAPI --> Observability[Health, metrics, logs, debug bundle]
```

The Docker image reflects that model. The build first exports the Next.js UI as static files, installs Python dependencies in a separate builder stage, and assembles a Python runtime image with `/app/core`, `/app/ui/backend`, the static UI, supervisor configuration, and `/config`. Runtime traffic enters through port `8501`, where uvicorn serves both `/api/*` routes and the static frontend. Supervisor control uses a local Unix socket inside the container, so process control is an internal container concern rather than a public service.

## Event and notification flow

The event path starts at Channels DVR. The core process creates one monitor for each enabled DVR returned by the persisted settings model. Each monitor connects to the DVR event stream and also calls DVR HTTP APIs when it needs metadata such as channels, programs, recordings, jobs, or disk status. Events pass into the alert manager, which fans them out to registered alert sources such as channel watching, VOD watching, recording events, and disk space. Alerts format messages and hand them to the notification manager, which applies delivery rules, rate limits, templates, and provider fanout.

The notification pipeline is deliberately provider-oriented. Pushover, Apprise-backed destinations, and signed webhooks sit behind provider abstractions so alert logic does not need to know how each destination authenticates, formats images, or signs payloads. This is the main extension point for new delivery channels. The other extension point is the alert source registry, where new event families can be added without changing the process model.

## Persistence and scaling

Persistence is centered on `/config`. The most visible file is `settings.json`, which stores DVR definitions, alert toggles, notification settings, routing choices, and security settings. Runtime state also lives there, including logs, activity history, watchdog snapshots, and per-DVR session files named with each `dvr_id`. SQLite is used for structured local state such as authentication data. This design keeps backup and migration understandable because the container can be replaced while `/config` remains the durable source of truth.

The trade-off is that `/config` is shared writable state. ChannelWatch is therefore a single-instance application, not a clustered service. One container should own one `/config` volume. Multi-DVR support scales inside that one instance by creating concurrent monitor tasks per enabled DVR, not by distributing DVRs across replicas. This matches the target deployment model for a self-hosted monitoring tool and avoids the coordination problems that would come with multiple writers managing the same sessions, settings, and notification history.

## Configuration and security boundaries

Configuration changes move through both processes. The UI reads and writes `settings.json`, masks sensitive fields on read, and can ask supervisor to restart the core when needed. The core bootstraps encryption at startup, reads settings, builds monitor contexts, and watches the config file for changes it can reload safely. Some changes can restart affected DVR monitors, while others still require a wider restart because they alter process-level behavior. That boundary keeps hot reload useful without pretending every setting is safe to mutate in place.

Security is scoped to the container and the browser/API boundary. An `encryption.key` is bootstrapped so stored secrets can be protected across restarts. Fresh installs use a setup-first flow: operators explicitly choose RBAC session login or a persisted no-auth mode before normal access. Legacy API-key installations remain compatible when an existing key is present, but API-key-only mode is a compatibility path rather than the default v1 setup outcome. RBAC session-changing requests require `X-CSRF-Token`; legacy API-key callers use the custom `X-API-Key` header, which keeps API-key-authenticated routes out of simple cross-site form submission paths. The UI also applies content security headers and masks sensitive settings values before returning configuration to the browser.

## Observability and trade-offs

Observability is part of the architecture rather than an afterthought. The UI process exposes `/api/health`, `/healthz/live`, `/healthz/ready`, `/healthz/startup`, `/metrics`, log download endpoints, notification logs, activity history, and an admin-only debug bundle. The core process writes structured operational state into `/config`, including watchdog snapshots that help the UI summarize enabled DVRs. This gives operators a single browser-facing place to inspect the system without exposing supervisor or the monitoring internals directly.

The architecture favors local clarity over distributed flexibility. A single container is easier to install, upgrade, and reason about. Two managed processes keep the monitoring loop and web surface from stepping on each other. Shared `/config` persistence makes state portable. The cost is that high availability, replica coordination, and distributed ownership are outside the current design. For ChannelWatch v0.9, that is a deliberate fit for the product: one self-hosted monitor watching one or more Channels DVR servers from one durable local volume.

## Related documentation

- [Two-process model](two-process-model.md)
- [Notification pipeline](notification-pipeline.md)
- [Configuration lifecycle](config-lifecycle.md)
