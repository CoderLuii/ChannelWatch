# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Keep this section for changes that have landed after the latest drafted release entry.

## [0.9.2] - 2026-06-22

### Fixed

- Fix the dashboard Disk Space card so large free-space values display in TB instead of being shown with a hardcoded GB label.
- Use the same 1024-based disk size formatter for dashboard and diagnostics storage displays.

## [0.9.1] - 2026-06-21

### Fixed

- Align public documentation, operator guidance, release metadata, and deployment examples with the current v0.9 release line.
- Remove stale unused dependency entries from runtime and UI manifests.
- Make webhook receiver examples require an explicit shared secret instead of falling back to placeholder values.

## [0.9.0] - 2026-06-21

### Added

- Add multi-DVR support across configuration, monitoring orchestration, per-DVR status, per-DVR history, and per-DVR session isolation.
- Add stable v1 API routes under `/api/v1` for new integrations.
- Add compatibility feed aliases `/api/v1/calendar.ics`, `/api/v1/feed.rss`, and `/api/v1/feed.atom` alongside canonical `/api/v1/feeds/*` paths.
- Add health, readiness, and metrics endpoints for monitoring and container orchestration.
- Add cached per-DVR Channels DVR version metadata to `/healthz/ready` responses without adding fresh DVR calls to the readiness path.
- Add a first-run wizard with network discovery, manual DVR add, and connection testing for initial setup.
- Add backup and restore workflows in the admin UI and API.
- Add sanitized debug-bundle generation in the UI and doctor CLI.
- Add per-DVR notification routing, structured delivery logs, retry behavior, and circuit-breaker protection.
- Add a notification plugin loader with a documented provider ABC and example plugin.
- Add security status UI reporting for API-key-only, RBAC with fallback, RBAC-only, and auth-disabled states.
- Add community, legal, privacy, security, and release metadata docs for the v0.9 release.
- Add Helm chart assets for Kubernetes deployment with single-replica operation only, including optional `networking.k8s.io/v1` Ingress support.
- Add `artwork_fallback_exhausted` to recording responses so the UI can distinguish true artwork exhaustion from older payloads that simply lack the field.

### Changed

- Move settings to a versioned migration pipeline with automatic backup before migration.
- Migrate older single-DVR installs into `dvr_servers` with canonical DVR IDs derived from `host:port`.
- Update disk-space monitoring with warning and critical thresholds, startup grace, and quieter repeat behavior.
- Make notification delivery, routing, and security posture more visible to operators in the UI.
- Move blocking Apprise notification delivery behind async wrappers using `asyncio.to_thread` while keeping the synchronous provider APIs for compatibility.
- Document that `notification_routing` is the authoritative persisted field name for the routing matrix concept used in earlier plans.
- Update operational docs to describe the real v0.9 product surface instead of the earlier narrowed release track.

### Removed

- Defer the planned `CW_DVR_<N>_*` environment-variable pattern and `/config/dvrs.yaml` loader. Per-DVR setup through comma-separated `CHANNELS_DVR_SERVERS` `Name@host:port` entries remains supported.
- Defer full removal of the event monitor threading layer. The core has an asyncio entry point and per-DVR task groups, but v0.9 ships a tested mixed model.
- Defer full removal of legacy `CHANNELS_DVR_HOST` and `CHANNELS_DVR_PORT` settings until v1.1. They still work with loud startup warnings.

### Fixed

- Fix documentation gaps that previously left v0.9 features under-described or listed as future work.
- Fix backup and settings guidance so it explains schema backups, restore behavior, and legacy env-var warnings.
- Fix privacy and plugin docs so they describe shipped backup, debug-bundle, and plugin-loader behavior.

### Security

- Add an honest security posture document covering auth modes, CSRF protections, cookie handling, at-rest encryption limits, plugin trust boundaries, and private disclosure flow.
- Add a zero-telemetry privacy explanation with user-supplied DSN handling, debug-bundle masking rules, and GDPR / UK GDPR controller-responsibility notes for multi-user deployments.
- Keep crash reporting off by default. A user-supplied Sentry or GlitchTip DSN can be saved and masked, but no crash-reporting client is wired in v0.9.
- Make the release workflow fail when a semver tag has no matching `docs/releases/CHANGELOG.md` entry, preventing undocumented Docker image releases.

### Known Limits

- Per-DVR setup through comma-separated `CHANNELS_DVR_SERVERS` `Name@host:port` entries is supported in v0.9, but the planned `CW_DVR_<N>_*` environment-variable pattern and `/config/dvrs.yaml` loader are deferred. Existing `CHANNELS_DVR_SERVERS` and legacy compatibility paths remain the supported setup choices for this release.
- Authentication ships with API-key support and optional RBAC cookie sessions, but optional HTTP Basic Auth is not included yet. This keeps the v0.9 auth surface smaller while the patch series fills the remaining compatibility gap.
- The core now has an asyncio entry point and per-DVR task groups, but part of the event monitor still uses internal threads. Full removal of that threading layer is deferred so v0.9 can ship with the safer mixed model already tested.
- The core logs a soft warning around more than 10 DVRs. Larger setups may still work, but v0.9 does not display a UI scale warning or override control.
- Performance baseline tooling and the CI regression gate are not complete. A 10-DVR webhook stress path still needs follow-up before a latency gate is enabled.
- The main multi-DVR UI pieces have badges, including the wizard, switcher, and tabs. Secondary badge polish remains a patch-release follow-up.
- Mock DVR cluster tests are in place, but the real Channels DVR image is not yet part of CI. That lane is deferred because it needs license-backed setup work before it can run reliably for every build.
- The primary DVR display field remains `name`; a separate user-editable `display_name` field is deferred to avoid late UI and API churn.
- Legacy `CHANNELS_DVR_HOST` and `CHANNELS_DVR_PORT` settings still work with loud startup warnings. Full removal is deferred to v1.1 so existing deployments get a safer transition window.
- Debug bundles and zero-telemetry behavior are implemented, and a user-supplied Sentry or GlitchTip DSN can be saved and masked, but no crash-reporting client is wired in v0.9. The default remains no phone-home telemetry.
- The shipped Helm chart is single replica only because ChannelWatch uses shared writable state under `/config`. Optional Ingress is available, and local `helm lint` plus default/enabled template checks passed for the v0.9 release.

## [0.8.0] - 2026-06-19

### Added

- Add API key authentication for backend endpoints, with automatic key generation on first startup and a regeneration endpoint.
- Add live Diagnostics logs with search, level filters, pause and resume, copy, download, and configurable line counts.
- Add global notification rate limiting with configurable count and time window, defaulting to 20 notifications per 5 minutes.
- Add session state persistence so active sessions and cooldown timers survive container restarts.
- Add a settings migration framework that upgrades v0.7 configs to the v0.8 schema with backups.
- Add SSRF protection and safe URL redaction for outbound image fetches and notification logs.
- Add rich stream details from the DVR API, including devices, channels, recordings, images, and dashboard subtitles.
- Add Docker health check support through `/api/health` and a Dockerfile `HEALTHCHECK`.
- Add Diagnostics actions for Run All Tests, granular recording event tests, health checks, diagnostic export, and activity history clearing.
- Add hash-based routing with browser history support and deep links into settings tabs.
- Add a restart overlay with health polling, elapsed timer, retry, reload, and API key refresh after recovery.
- Add activity and recording detail dialogs with structured fields and artwork.
- Add `DVRConnection` and per-DVR activity fields as groundwork for multi-DVR support.

### Changed

- Split the dashboard status overview into focused cards, timeline, status, activity, and recording components.
- Record activity independently from notification delivery so dashboard history remains complete when alerts are disabled.
- Add master toggles for alert types while preserving internal activity logging.
- Make the channel watching alert cooldown configurable instead of hardcoded.
- Query active streams directly from the DVR API instead of reading `/config/stream_count.txt`.
- Show upcoming recording images and timezone-aware dates.
- Expand settings with collapsible alert sections, rate limiter controls, timing controls, display image preferences, and cache TTL bounds.
- Use UTC-aware timestamps across backend and activity history paths for consistent container behavior.
- Make activity history reads and writes thread-safe.
- Let the app start without a configured notification provider so users can run dashboard-only mode or configure notifications later.
- Support `TZ` through an environment variable as well as the config file.
- Improve accessibility with sidebar `aria-label` values and pinch-zoom support.
- Switch the production image from Alpine to Debian slim with a dedicated Python dependency builder stage.
- Generate supervisord credentials at container start instead of baking shared defaults into the image.
- Update Compose examples with `init: true`, optional environment settings, and bridge-mode guidance.

### Removed

- Remove the dedicated Pushover provider from the default provider registry. Pushover delivery now goes through Apprise.
- Remove `sseclient-py` from Python requirements.
- Remove unused frontend packages `date-fns` and `sonner`.
- Remove the deprecated Docker Compose `version` key.
- Stop using `/config/stream_count.txt` as the source of active stream counts.

### Fixed

- Fix event statistics double-counting that inflated total event counts.
- Fix a disk-space alert `AttributeError` when cooldown log time was read before initialization.
- Fix session data loss when notification delivery failed.
- Fix recording event activity gaps when sub-alert types were disabled.
- Fix settings saves that silently dropped the schema version field.
- Fix light mode readability in the About section.
- Fix Docker bridge-mode troubleshooting by warning when localhost-style DVR hosts fail from a container.

### Security

- Require `X-API-Key` for protected API routes while keeping ping, health, and settings reads available for bootstrapping.
- Mask sensitive settings fields such as API keys and webhook tokens in settings responses.
- Redact notification credentials before writing URLs to logs.
- Validate outbound image URLs to block private IPs, loopback addresses, cloud metadata hosts, and non-HTTP schemes.
- Add security headers to responses and lock down CORS defaults.

## [0.7.0] - 2025-05-01

### Added

- Establish the v0.7 release line for the Dockerized ChannelWatch monitor, UI, and notification workflow.
- Provide the single-DVR configuration model that later v0.8 migrations upgrade into a versioned settings schema.
- Include core alert coverage for channel watching, VOD watching, disk space, and recording events.
- Include the original dashboard, settings, diagnostics, and about pages used as the baseline for the v0.8 UI refresh.

### Changed

- Document the pre-v0.8 deployment path around Docker Compose, `/config/settings.json`, Channels DVR host and port settings, and notification provider setup.

### Security

- Carry forward the project security policy and dependency security updates that existed before the v0.8 hardening work.

[Unreleased]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.2...HEAD
[0.9.2]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.2
[0.9.1]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.1
[0.9.0]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.0
[0.8.0]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.8
[0.7.0]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.7
