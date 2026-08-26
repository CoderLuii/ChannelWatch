# Changelog

All notable changes to this project will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Keep this section for changes that have landed after the latest drafted release entry.

## [0.9.19] - 2026-08-26

### Changed

- Make SQLite the authoritative activity store for core event writes, dashboard queries, per-DVR history, feeds, backups, retention, clear-history operations, and DVR hard deletion.
- Keep `activity_history.json` as a bounded recovery journal for historical migrations, temporary SQLite failures, and rollback compatibility, with idempotent reconciliation by activity UUID.
- Keep the legacy v1 update feed pinned to v0.9.18 while delivering v0.9.19 to v0.9.18 and newer installations through the signed v2 Update Center catalog.
- Preserve a complete 48-hour review interval before automatic installation is allowed.

### Fixed

- Restore valid live-TV, VOD, movie, episode, recording, disk, and test activity that was detected and preserved in JSON but hidden whenever an empty SQLite activity table existed.
- Make Recent Activity, the 24-Hour Timeline, per-DVR history, and activity feeds read the same merged durable view without duplicate events during migration or recovery.
- Include valid pending recovery-journal activity in SQLite backup snapshots instead of silently omitting it.
- Remove activity from both durable representations when history is cleared or a DVR is permanently deleted.
- Preserve malformed recovery journals in private quarantine files and report storage degradation without replacing valid SQLite history.
- Refresh the pinned Skopeo publication helper after the old digest disappeared upstream.
- Make the early-SIGHUP Linux test use unbuffered child output instead of pipe timing.
- Install constrained runtime dependencies before every pinned-v1 bridge verification step.
- Make the single release workflow finish every non-publishing gate before it creates a public tag or publishes artifacts.

### Security

- Keep unauthenticated health responses minimal while adding only non-sensitive activity-storage status and counts to authenticated health diagnostics.
- Continue managing credential encryption automatically under persistent `/config`; no DVR credential re-entry or user-managed storage key is required for this update.

## [0.9.18] - 2026-08-25

### Important

- The immutable published v0.9.9 and v0.9.10 entrypoints cannot activate v0.9.18 safely through their old in-app bridge. If you are still on either image, preserve `/config`, pull/recreate the v0.9.18 image once, let v0.9.18 repair any stale legacy update marker without discarding the preserved configuration, and then use the improved Update Center for future releases.

### Added

- Add application-managed local encryption-key creation under persistent `/config`, with no secret variable, key prompt, or manual key-generation step for fresh installations.
- Add authenticated, rate-limited legacy recovery that can use the old wrapping value for an envelope created by v0.9.5–v0.9.17, or an original raw 32-byte key once, plus a confirmed last-resort reset that preserves non-secret settings and history.
- Add a narrowly scoped official signed recovery-update path so an installation blocked before administrator login can still check and apply a compatible fix using short-lived same-origin CSRF and exact typed confirmation, without accepting custom sources or downgrades.
- Add an automatic signed-update policy, defaulting to a 03:00–05:00 local maintenance window, with notify-only, bounded postpone, retry, failure quarantine, and rollback controls.
- Show the application version, container image version, runtime source, launcher protocol, and whether an eventual image refresh is recommended or required.

### Changed

- Make Update Center the normal upgrade path from operational v0.9.11–v0.9.17 installations, prominently including v0.9.15, v0.9.16, and v0.9.17; compatible app updates are signed, backed up, activated atomically, validated after restart, and rollback-capable.
- Keep the schema-1 stable feed permanently pinned to the v0.9.18 bridge for v0.9.11–v0.9.17 clients, while v0.9.18 and later independently select compatible releases from the new signed schema-2 catalog.
- Classify v0.9.9 and v0.9.10 honestly as immutable image-pull-only exceptions; their in-bundle safeguards remain defense-in-depth and are not counted as proof that those published images can activate the bundle.
- Remove the required `CHANNELWATCH_SECRET_STORAGE_KEY` from Compose, Helm, Unraid, and fresh-install documentation. Existing values remain accepted only as deprecated one-time legacy migration input.
- Require v0.9.9, v0.9.10, and an already-blocked v0.9.17 missing-key installation to pull/recreate the v0.9.18 image once while preserving `/config`; operational v0.9.11–v0.9.17 installations can install v0.9.18 directly in Update Center.
- Keep login reachable when older protected credentials need attention, pause only work that needs the locked credentials, and present generic authenticated recovery guidance without exposing key details.
- Keep dirty support-report drafts and private attachments in page memory while showing a restart countdown and one server-authorized 24-hour postponement before a scheduled automatic update.

### Fixed

- Require the core process to confirm that notification queues remain held before an automatic update can begin, renew that hold throughout apply, and fail closed when the two-process handshake cannot be confirmed.
- Stop fresh third-party one-click and ordinary Docker installations from appearing partly healthy while monitoring is blocked by a missing deployment key.
- Let a mature, already-secured `/config` continue read-only monitoring when its storage is later remounted read-only, while disabling persistent logs, state changes, updates, and other writes until storage is repaired.
- Keep existing RBAC sessions and API-key access usable in read-only mode; creating or revoking sessions, changing credentials, and every other persistent mutation returns an actionable error until `/config` is writable. Monitoring and delivery continue, but secondary session, stream-count, activity, and delivery-history diagnostics may remain stale while writes are unavailable.
- Prevent Helm upgrades from deadlocking against the single-writer `/config` lock by using a `Recreate` Deployment, and restart the pod when the chart-managed ConfigMap or Secret changes.
- Preserve the logical encryption key and saved credentials when a recoverable v0.9.5–v0.9.17 legacy envelope is converted to the application-managed local format.
- Preserve legacy envelopes and settings byte-for-byte when their old input is missing or wrong, and offer authenticated recovery or an explicit protected-credential-only reset instead of overwriting them.
- Distinguish an in-app application upgrade from the container image underneath it, so a compatible v0.9.18 release is not unnecessarily blocked on an image pull.

### Security

- Continue encrypting DVR API keys and custom webhook URLs/secrets at rest while eliminating the fragile user-managed wrapping-key dependency from normal operation.
- Keep official signature, digest, ABI, schema, source allowlist, release revocation, backup, activation quorum, health, quarantine, and rollback checks mandatory for every automatic or manually retried update.
- Never place recovery values or raw key files in browser storage, settings, logs, API responses, evidence, release artifacts, or container metadata.
- Treat full `/config` access and complete backups as credential-bearing because they contain both encrypted settings and the application-managed local decryption key.
- Keep `settings.json`, managed keys, private backups, recovery snapshots, and maintenance transaction files owner-only, and fail closed on linked, special, oversized, or unstable key/settings paths.

## [0.9.17] - 2026-08-23

### Added

- Add a privacy-preserving runtime preflight endpoint and setup-required screen for deployments that omit, shorten, or mismatch the external secret-storage key.
- Add a DVR-specific destination policy that safely supports private-LAN, single-label, `.local`, public, IPv6 unique-local, and Tailscale MagicDNS hosts while pinning the validated DNS address to each outbound connection.
- Document that fresh installs from the third-party Project One-Click template remain setup-required until the operator runs `openssl rand -base64 48`, adds the result as `CHANNELWATCH_SECRET_STORAGE_KEY`, pulls and recreates the v0.9.17 container without deleting `/config`, and preserves that same key across upgrades, restores, and host migrations; the external project is not modified or contacted.

### Changed

- Keep the core process stable and responsive to shutdown while protected runtime setup is blocked, without starting monitors, notifications, migrations, or update readiness.
- Await notification diagnostics end to end with a bounded delivery deadline so a passing result means at least one configured destination accepted the test alert.
- Solve signed support-report proof challenges in a cancellable browser worker with a 30-second deadline and non-sensitive progress reporting.
- Determine restart recovery from UI liveness and startup probes, then report degraded monitoring separately from a failed restart.
- Mark v0.9.17 as container image required because secret-storage recovery must work before the core monitor can use an app-only update path.

### Fixed

- Replace fresh-install Supervisor crash loops with an actionable setup-required state while preserving fail-closed external key separation.
- Preserve existing plaintext encryption keys for compatibility, warn that migration is recommended, and atomically envelope the same logical key after a valid deployment key is supplied.
- Stop channel, VOD, and recording notification tests from reporting success for a coroutine that was not awaited or a queued-but-undelivered alert.
- Keep report text, screenshots, and debug bundles in memory after proof timeout or network failure until successful submission, explicit removal or discard, or page navigation.
- Stop a healthy restarted UI from being mislabeled as failed solely because a DVR remains offline or runtime setup still needs attention.
- Reject DNS results that include any loopback, link-local, metadata, multicast, unspecified, reserved, or otherwise forbidden destination even when another answer is safe.

### Security

- Continue requiring a stable operator-managed secret-storage key of at least 32 trimmed characters; ChannelWatch does not generate, display, upload, persist, or recover that deployment secret.
- Keep the general outbound URL validator unchanged while applying the new hostname support only to Channels DVR connections.
- Keep unauthenticated health probes minimal and expose only fixed, non-secret blocker codes through the new bootstrap preflight endpoint.
- Require users to pull and recreate the v0.9.17 image while preserving `/config`; removing or changing an established wrapping key remains fail closed.

## [0.9.16] - 2026-08-22

### Added

- Add an always-running monitor reconciler that adopts new DVR configuration without a process restart and retries unavailable DVRs with interruptible exponential backoff.
- Add an explicit `CW_TRUSTED_PROXIES` allowlist for validated forwarded client and scheme handling.
- Add Helm support for a required chart-managed secret-storage key or an operator-managed existing Secret.
- Add browser/backend API contract checks and non-publishing push/pull-request CI jobs for Python, frontend, browser, docs/config, Helm, container, and security gates.
- Add a visible session logout control that uses the established CSRF-protected API contract.
- Add a release-specific OpenVEX record with machine-checked runtime vulnerability dispositions.

### Changed

- Reconcile changed DVR monitors concurrently within one bounded freshness window and expose setup/degraded state through minimal unauthenticated probes.
- Make notification rate limiting installation-wide and isolate circuit breakers by DVR, channel, provider, and destination.
- Coordinate Update Center activation with generation-specific core and UI readiness markers, a bounded deadline, and whole-container recovery.
- Mark v0.9.16 as container image required because launcher, entrypoint, lifecycle, and Helm behavior changed.
- Bound Channels DVR XMLTV downloads and parsing, preserve a healthy guide cache after malformed input, and extend the tested DVR range through build `2026.08.07.0346`.
- Refresh the pinned multi-architecture Chainguard Python inputs and pin the build-stage timezone package to the reviewed Wolfi version.

### Fixed

- Recover automatically when zero DVRs are configured or one or all configured DVRs are unreachable at startup.
- Stop and unregister failed replacement monitor tasks without multiplying hot-reload waits across DVRs.
- Restore the previous runtime selection when update restart or activation fails, reject stale/reused locks, and validate trusted redirects before reading response bodies.
- Require CSRF for logout, keep detailed DVR health authenticated, and prevent untrusted forwarding headers from changing cookies or rate-limit identity.
- Reject malformed explicit notification routing instead of broadening delivery and keep slow provider retries off the event loop.
- Require the Helm runtime secret-storage key and preserve production static export while adding a development-only same-origin API proxy.
- Keep unauthenticated readiness responses minimal while documenting authenticated per-DVR health diagnostics accurately.
- Start reliably on Docker Desktop virtiofs bind mounts by verifying config writes after privilege drop when ownership metadata is virtualized, while keeping native ownership mismatches fail closed.
- Keep the single-replica Helm Service reachable for first-run setup and diagnostics while preserving degraded monitoring readiness on the Pod.

### Security

- Fail closed on supplemental-group or effective-identity privilege-drop failures.
- Harden release publication against stale/divergent tags, mutable published releases, and concurrent publication races.
- Keep live DVR verification read-only and notification verification on disposable local sinks.
- Reject non-ASCII update hosts before trusted-host validation and publish reviewed Python runtime dispositions with release assets.
- Build and scan exact amd64 and arm64 release-candidate images before registry authentication or publication begins.
- Pin the release runtimes, copy one already-scanned multi-architecture OCI layout to only the exact version and `latest` tags with digest and platform-descriptor verification, and align the isolated Docker config and temporary home so provenance attestation reuses the authenticated registry session.
- Keep the required root-starting entrypoint exception narrow, documented, expiring, and enforced by the ordinary Trivy CI gate.
- Bundle ChannelWatch notices, complete applicable copyleft license texts, and an exact corresponding-source map with both the image and app-update archive; attach exact amd64/arm64 SPDX and CycloneDX SBOMs and checksum every non-checksum release asset before registry publication.

## [0.9.15] - 2026-08-14

### Changed

- Allow the release workflow to wait through normal site-deployment timing before it verifies the stable Update Center metadata.

### Fixed

- Make browsers running this version wait for a compatible update restart to finish, confirm the target bundle is active, and then reload the interface.
- Remove embedded credentials, private network addresses, secret-like values, unsafe links, and unintended mentions from the public problem-report preview.

### Security

- Strengthen the public/private boundary for problem reports before report text leaves ChannelWatch.

## [0.9.14] - 2026-08-13

### Fixed

- Make problem-report review work on local-network addresses where secure browser APIs are unavailable.
- Keep reporting status, retry, offline fallback, and validation messages accurate and actionable during network and provider failures.
- Improve update impact detection so version-only container metadata changes do not incorrectly require a new image.

### Security

- Strengthen support-package validation and preserve the public/private reporting boundary across retries and recovery.

## [0.9.13] - 2026-08-12

### Changed

- Enable live problem-report submission in the official container while preserving dry-run behavior for source and development environments.
- Make report preview, submission, delivery, retry, and fallback states explicit in the interface.
- Mark v0.9.13 as container image update required because the official image reporting configuration changes.

### Fixed

- Keep report drafts and attachments available after a submission failure.
- Display and download the support code so local browser clipboard restrictions cannot block manual reporting.
- Accept supported ChannelWatch LAN origins on custom mapped ports and prevent retries from creating duplicate public issues.

### Security

- Add durable submission coordination, layered request throttling, stricter attachment validation, and automated public/private data-boundary checks.

## [0.9.12] - 2026-08-11

### Changed

- Update the Python runtime, web UI libraries, build tools, container base images, and release actions to their latest stable compatible versions.
- Install Python dependencies from reviewed constraints and pin container base images by digest so release builds use the versions tested for this update.
- Mark v0.9.12 as container image update required because it changes Python and container dependencies.

### Security

- Refresh transitive frontend packages to patched releases and verify the shipped dependency trees have no known package advisories.

## [0.9.11] - 2026-07-29

### Changed

- Keep long-running Channels DVR event streams open during valid idle periods while retaining the connection timeout, health poll, retry backoff, and `Retry-After` handling.
- Read the stream-card image preference from the existing UI settings snapshot instead of rebuilding core settings during dashboard refreshes.
- Generate release notes and Update Center highlights from the v0.9.11 changelog, with explicit release metadata marking this as an image-required update.
- Run the Python test suite and `compileall` before the release workflow creates a draft release.

### Fixed

- Stop quiet DVR event streams from entering the 1, 2, 4, 8, 16, and 32-second reconnect sequence after five seconds without an event.
- Treat unexpected stream closure, failed health polls, and non-success responses as disconnected while keeping reconnect waits interruptible during shutdown.
- Stop routine dashboard stream requests from repeating the `TZ` override message.
- Return `ERR_UPDATE_JOB_NOT_FOUND` when an Update Center job ID does not match the persisted operation.
- Replace the generated supervisor config before each container start so Update Center restarts can safely return to the image runtime.
- Offer image-runtime rollback in the Update Center while an active app bundle is selected.

## [0.9.10] - 2026-06-25

### Changed

- Show v0.9.10 as container image update required because it repairs Docker entrypoint and runtime launcher behavior.
- Run the Docker entrypoint through `/venv/bin/python` so the stable image launcher can resolve Update Center runtime state with installed dependencies.
- Pass core launcher arguments such as `--stay-alive` through `runtime_launcher.py` while keeping unsupported UI launcher arguments rejected.
- Treat blank DVR names as optional by falling back to the DVR host or IP during settings load, settings save, health checks, `/api/v1/dvrs`, and core DVR connections.

### Fixed

- Preserve settings `_version` metadata when defaults are merged so schema migration backups do not repeat after settings reloads.
- Accept UTF-8-with-BOM `settings.json` files so Windows-edited configs do not block startup.
- Keep v0.9.10 release notes focused on the repair instead of repeating old known-limit sections.

## [0.9.9] - 2026-06-24

### Added

- Add the in-app Update Center under Settings so compatible app-only releases can be checked, verified, backed up, applied, restarted, and rolled back from the web UI.
- Publish signed app-bundle release assets and a signed update manifest for trusted Update Center checks.
- Add a stable image launcher that can activate a verified app bundle and roll back to the prior runtime if activation fails.

### Changed

- Keep future runtime-changing releases on the normal container image update path by marking them as image-required instead of forcing unsafe in-app updates.
- Extend the release workflow to build, sign, upload, and site-sync Update Center bundle metadata without adding extra workflow files.

### Security

- Verify update manifests and bundles with Ed25519 signatures, SHA256 hashes, trusted release hosts, strict bundle path checks, and a runtime ABI/schema compatibility gate.

## [0.9.8] - 2026-06-23

### Added

- Add trusted local notification destinations for private LAN webhook receivers and HTTP-style custom Apprise URLs.

### Changed

- Keep local, loopback, link-local, reserved, and metadata destinations blocked by default while allowing operators to approve an exact private LAN scheme, host, and port from Settings.

### Security

- Preserve SSRF protections for image fetching and all non-notification outbound URLs. Metadata, link-local, loopback, reserved, malformed, and unresolved destinations cannot be trusted.

## [0.9.7] - 2026-06-23

### Changed

- Keep live-watch and DVR playback notifications to a single outer Apprise delivery attempt so provider rate limits do not stall the event stream.

### Fixed

- Record Channel Watching activity and session cooldown before provider delivery so Recent Activity stays current even when a notification provider fails or rate-limits.

## [0.9.6] - 2026-06-23

### Changed

- Keep the in-app `Report a Problem` submit path free of hosted bot verification while preserving the protected public upload portal.
- Show the normal first monitor pass as `Monitoring starting` instead of presenting startup freshness as a degraded state.

### Fixed

- Allow local ChannelWatch installs to submit support-code reports to the hosted intake service without showing an in-app verification widget.
- Return a structured restart error when supervisor control is unavailable instead of leaving the restart overlay waiting indefinitely.

### Security

- Keep bot verification scoped to the public report upload portal, where anonymous traffic can reach it, while continuing to validate support codes for report intake.

## [0.9.5] - 2026-06-23

### Changed

- Keep legacy browser API keys in memory only instead of storing them in browser session storage.

### Fixed

- Escape report-intake diagnostics more defensively so pasted status text cannot break the public issue preview table.

### Security

- Store newly written local secret files with an encrypted envelope using `CHANNELWATCH_SECRET_STORAGE_KEY`, while preserving existing installs through migration-safe reads.

## [0.9.4] - 2026-06-23

### Changed

- Use a local supervisor Unix socket for internal process control instead of generated supervisor HTTP credentials.
- Keep Docker image publishing focused on `latest` and the exact release version tag only.

### Fixed

- Allow the configured report-intake endpoint in the UI content security policy so live report submission can reach the hosted intake service.
- Route live report submissions that require hosted verification into the secure upload path instead of showing a generic browser fetch failure.
- Keep startup and DVR target validation responses generic while logging rejected targets internally with redaction.

### Security

- Encrypt webhook URLs and shared secrets at rest using the existing ChannelWatch Fernet key.
- Stop printing generated `reset-admin-password` values; omitting `--password` now uses a hidden prompt.

## [0.9.3] - 2026-06-23

### Added

- Add a `Report a Problem` flow in Diagnostics with sanitized report previews, optional GetChannels/GitHub/email contact fields, screenshot uploads, and ChannelWatch debug-bundle ZIP validation.
- Add a manual upload path with support codes and offline report packages for installs that cannot submit directly from the container.
- Add the hosted report intake handoff to the ChannelWatch site for creating public issues while sending private screenshots, debug bundles, and private email details only to CoderLuii.

### Changed

- Point manual uploads to the hosted ChannelWatch report portal instead of requiring GitHub knowledge from reporters.
- Keep report-intake API failures on the structured error catalog so UI errors remain consistent with the rest of the app.

### Security

- Keep private emails, screenshot filenames, screenshots, debug bundles, raw logs, tokens, and private config values out of the public GitHub issue body.
- Require support-code validation for hosted report uploads and keep production verification on the hosted intake path.

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

[Unreleased]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.19...HEAD
[0.9.19]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.18...v0.9.19
[0.9.18]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.17...v0.9.18
[0.9.17]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.16...v0.9.17
[0.9.16]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.15...v0.9.16
[0.9.15]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.14...v0.9.15
[0.9.14]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.13...v0.9.14
[0.9.13]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.12...v0.9.13
[0.9.12]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.11...v0.9.12
[0.9.11]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.10...v0.9.11
[0.9.10]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.9...v0.9.10
[0.9.9]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.8...v0.9.9
[0.9.8]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/CoderLuii/ChannelWatch/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.2
[0.9.1]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.1
[0.9.0]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.9.0
[0.8.0]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.8
[0.7.0]: https://github.com/CoderLuii/ChannelWatch/releases/tag/v0.7
