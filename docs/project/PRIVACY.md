# Privacy Policy

ChannelWatch is a self-hosted Channels DVR monitoring app. The privacy model is simple: your data stays under your control unless you explicitly point ChannelWatch at a service you want it to talk to.

## Zero telemetry

ChannelWatch has no built-in telemetry pipeline.

That means:

- no analytics SDK
- no usage beacons
- no automatic calls to a ChannelWatch-operated service
- no default crash reporting endpoint

By default, ChannelWatch only talks to:

- your Channels DVR server or servers
- notification destinations you configure
- optional endpoints you explicitly provide, such as a webhook URL or your own Sentry or GlitchTip DSN

If you do not configure an outbound integration, ChannelWatch does not invent one.

## Local data stored under `/config`

ChannelWatch stores its state on your own volume.

Common files include:

| File | What it stores |
|---|---|
| `settings.json` | Main configuration, including DVR list and notification settings |
| `channelwatch.db` | Runtime database used by current releases |
| `channelwatch.log` | Application logs |
| `activity_history.json` | Legacy or migrated activity history data in older installs |
| `session_state_<dvr_id>.json` | Per-DVR session tracking state |
| `encryption.key` | Key used to protect per-DVR API keys at rest |
| `backups/` | Migration backups and pre-restore snapshots |

This data is not copied to a ChannelWatch service because there is no ChannelWatch cloud service involved.

## Debug bundle

ChannelWatch can generate a sanitized debug bundle ZIP for troubleshooting.

You can create it from:

- Settings -> Backup
- the diagnostics UI
- the CLI: `channelwatch doctor debug bundle`

The debug bundle is designed to be shareable with maintainers because it masks or excludes sensitive data.

### What the debug bundle contains

The bundle contains four files:

| File | Purpose |
|---|---|
| `manifest.json` | Bundle metadata and privacy note |
| `settings_sanitized.json` | Settings with sensitive fields masked |
| `log_tail.txt` | Tail of the log file with IPs and URL host portions redacted |
| `health_snapshot.json` | Local health snapshot generated without network probes |

### What the debug bundle excludes

These items are not included:

- `encryption.key`
- `channelwatch.db`
- raw session-state files
- unmasked notification credentials

### What gets masked in `settings_sanitized.json`

Sensitive fields are replaced with `****`, including:

- the shared UI API key
- Apprise credentials
- DVR host, port, and per-DVR API key values
- webhook destination URLs and signing secrets
- `error_reporting_dsn`

No debug bundle is sent automatically. You must choose to generate and download it.

## In-app support reports

The Diagnostics page includes **Report a Problem** for support cases that need more structure than a normal issue form.

The report preview is designed to be public-safe. It can include:

- problem summary
- expected behavior
- public contact handles
- ChannelWatch version, DVR counts, core status, monitoring status, enabled alert toggles, and configured notification provider names

Private support details are kept out of the public issue text. Screenshots, debug bundle ZIPs, private email addresses, attachment filenames, raw logs, API keys, webhook secrets, notification tokens, DVR API keys, and private config values are not published in the issue body.

Direct in-app submit is operator controlled. If direct submit is not available, ChannelWatch can create a support code or offline package for the hosted upload portal. The hosted portal re-validates the support code and attachments before creating a public issue or sending private troubleshooting material.

## Backup archive versus debug bundle

These are different tools and they should not be confused.

### Debug bundle

- meant for troubleshooting
- sanitized for sharing
- excludes encryption key and database

### Full backup archive

- meant for restore and migration workflows
- not sanitized for sharing
- can include `settings.json`, `channelwatch.db`, per-DVR session state, and `encryption.key`

If you share a full backup archive, you may be sharing sensitive data. If you need to send information to a maintainer, prefer the debug bundle unless you have a specific reason not to.

## Optional error reporting, user-supplied DSN only

ChannelWatch includes an optional Sentry-compatible DSN field named `error_reporting_dsn`.

Important facts:

- it is empty by default
- ChannelWatch does not ship with a bundled DSN
- if you enter a DSN, it is saved in your local settings for future support
- no crash-reporting client is wired in v0.9, so no crash reports are sent today
- if future support is added, events would go only to the Sentry, GlitchTip, or compatible service you configure

### DSN handling in support workflows

If you later generate a debug bundle, `error_reporting_dsn` is masked before the bundle is written.

## Notification destinations you configure

If you configure Pushover, Discord, Slack, Telegram, email, Gotify, Matrix, custom Apprise URLs, or outbound webhooks, ChannelWatch sends notification content to those services because you asked it to.

That content can include:

- DVR names
- channel names
- program titles and summaries
- device names and IP addresses
- disk alert information

Each destination has its own privacy policy and retention behavior. ChannelWatch does not control those third-party services.

## Multi-user deployment notice

ChannelWatch is built first for self-hosted operators. If you deploy it where multiple people can access the UI or the notification outputs, you are responsible for that deployment's privacy obligations.

That can include:

- restricting access to the UI
- deciding how long logs and history should be kept
- informing affected users that viewing activity may be visible

RBAC and network controls help, but they do not replace your own compliance responsibilities.

## Data controller responsibilities (GDPR / UK GDPR)

ChannelWatch does not collect, transmit, or process any personal data on behalf of its maintainer. There is no telemetry, no analytics, and no operator-side data ingestion. See "Zero telemetry" above.

When you deploy ChannelWatch in a multi-user configuration, such as RBAC enabled with multiple user accounts or shared notification destinations, **you become the data controller** for any personal data you process, including:

- usernames, password hashes, and session metadata in `/config/channelwatch.db`
- notification destinations, such as webhook URLs, Apprise targets, and email addresses
- DVR API keys and connection metadata
- any activity history retained on disk

Under the EU GDPR, the UK GDPR, and equivalent regimes, this means you are responsible for:

- providing notice to users about what data ChannelWatch stores
- honoring data-subject access, rectification, and erasure requests
- ensuring an appropriate lawful basis for processing
- notifying authorities of personal-data breaches within statutory deadlines
- restricting access to `/config/` consistent with your jurisdiction's requirements

If you process personal data on behalf of third parties, for example by hosting ChannelWatch for a household other than your own, additional data-processing-agreement obligations may apply. ChannelWatch's maintainer is not a processor for your deployment.

Single-user or single-household deployments typically fall outside scope of GDPR commercial obligations, but local privacy law may still apply.

## Artwork and metadata sources

Channel logos and program artwork shown in notifications are fetched through your Channels DVR environment. ChannelWatch does not contact Gracenote, TVDb, or similar metadata vendors on its own.

## Contact

For privacy questions, use the GitHub repository discussions or issues for non-sensitive topics. If your question is actually a security problem, use the private disclosure path described in `.github/SECURITY.md`.

## See also

- [`docs/reference/health-diagnostics.md`](../reference/health-diagnostics.md) for debug bundle sanitization details.

*Last updated: April 2026*
