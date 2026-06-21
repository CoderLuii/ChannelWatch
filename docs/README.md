# ChannelWatch documentation

This docs tree is the home for ChannelWatch user, operator, reference, and architecture documentation. It follows the Diataxis model: tutorials teach by walking through a complete learning path, how-to guides solve focused tasks, reference pages give exact facts, and explanations clarify why the system works the way it does.

## Tutorials

- [getting-started.md](tutorials/getting-started.md) - Set up ChannelWatch and trigger a first Channel Watching alert.
- [first-alert.md](tutorials/first-alert.md) - Configure one provider and send a safe test notification.

## How-To

- [multi-dvr.md](how-to/multi-dvr.md) - Add a second or third Channels DVR server.
- [configure-webhooks.md](how-to/configure-webhooks.md) - Send signed ChannelWatch alerts to an external HTTP receiver.
- [troubleshoot-notifications.md](how-to/troubleshoot-notifications.md) - Diagnose missing, failing, or surprising notification delivery.
- [reverse-proxy.md](how-to/reverse-proxy.md) - Publish ChannelWatch through Nginx, Caddy, Traefik, or Cloudflare Tunnel.
- [backup-restore.md](how-to/backup-restore.md) - Back up and restore configuration, DVR state, and activity data.

## Reference

- [settings.md](reference/settings.md) - Schema details for persisted /config/settings.json values.
- [env-vars.md](reference/env-vars.md) - Startup variables read by the container, core, UI, and supervisor.
- [api.md](reference/api.md) - HTTP routes exposed by the FastAPI backend.
- [webhook.md](reference/webhook.md) - Outbound webhook payloads, headers, signing, and delivery behavior.
- [templates.md](reference/templates.md) - Supported notification template placeholders and rendering rules.
- [apprise-providers.md](reference/apprise-providers.md) - Configured Apprise destinations and provider-specific fields.
- [logs-metrics.md](reference/logs-metrics.md) - Logs, Prometheus metrics, history, and debug bundle surfaces.
- [health-diagnostics.md](reference/health-diagnostics.md) - Probe endpoints, UI diagnostics, and doctor CLI behavior.
- [multi-dvr.md](reference/multi-dvr.md) - Detailed multi-DVR configuration, identity, lifecycle, and limits.
- [disk-monitoring.md](reference/disk-monitoring.md) - Disk polling, thresholds, severity, metrics, and alert behavior.
- [plugins.md](reference/plugins.md) - Notification provider plugin locations, loader behavior, and safety rules.

## Explanation

- [architecture.md](explanation/architecture.md) - Why ChannelWatch separates monitoring and browser-facing responsibilities.
- [two-process-model.md](explanation/two-process-model.md) - Why one container runs separate core and FastAPI processes.
- [notification-pipeline.md](explanation/notification-pipeline.md) - How DVR activity becomes routed notifications.
- [config-lifecycle.md](explanation/config-lifecycle.md) - How settings move from browser saves to running monitor tasks.

## Diataxis quadrant guidance

- Read **Tutorials** when you want a guided first success from start to finish.
- Read **How-To** when you have a specific operational task to complete.
- Read **Reference** when you need exact fields, endpoints, variables, or behavior.
- Read **Explanation** when you want the reasons behind ChannelWatch design choices.
