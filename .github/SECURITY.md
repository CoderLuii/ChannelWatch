# Security Policy

## Supported versions

Security fixes are provided for the current 1.x release line.

| Version line | Supported |
|---|---|
| 1.x | Yes |
| 0.x and earlier pre-release builds | No |

If you are still on a 0.x build, upgrade before reporting a security issue unless the issue itself blocks upgrade.

## Reporting a vulnerability

Do not open public GitHub issues for security problems.

Use GitHub's private advisory flow instead:

- [Open a private security advisory](https://github.com/CoderLuii/ChannelWatch/security/advisories/new)

### Please include

- affected version or image tag
- deployment shape, for example Docker Compose, reverse proxy, or local-only setup
- exact steps to reproduce
- expected impact
- any logs, screenshots, or proof of concept that help explain the issue

### Response targets

| Severity | Acknowledge | Target remediation window |
|---|---|---|
| Critical | 48 hours | 7 days |
| High | 48 hours | 14 days |
| Medium or Low | 48 hours | 30 days |

These are targets, not hard guarantees. ChannelWatch is still a single-maintainer project.

## Honest security model

ChannelWatch is a self-hosted app designed for a trusted home or small-office network. It has meaningful protections, but it is not pretending to be a zero-trust multi-tenant platform.

The safest mental model is:

- one operator-controlled instance
- one writable `/config` volume
- private network first
- reverse proxy and TLS if you expose it more broadly

## Authentication modes

ChannelWatch now supports a setup-first auth model for fresh installs, plus a legacy compatibility path for older installs. The endpoint reference summarizes API key, RBAC session, role, CSRF, and rate-limit behavior in [`docs/reference/api.md`](../docs/reference/api.md).

Fresh installs should start with secure login. No-auth is an advanced persisted choice, and legacy API-key mode is compatibility-only.

### `API_KEY_ONLY`

This is a **legacy API-key compatibility posture**, not the recommended path for new installs.

- one shared API key is stored in `settings.json`
- the frontend sends it as `X-API-Key`
- older installs may still rely on it temporarily

Fresh installs should prefer setup-based secure login instead.

### `RBAC_WITH_API_KEY_FALLBACK`

RBAC is enabled, but the shared API key still works.

This is better than API-key-only for browser sign-in, but it is not the same as role-only enforcement because the shared key still bypasses role checks.

### `RBAC_ONLY`

RBAC is enabled and the shared API-key fallback is not active.

This is the strongest built-in posture available in the current codebase.

### `NO_AUTH`

This is an explicit advanced mode selected in the setup/UI flow.

- no credentials are required to access the app
- the saved preference is persisted as `auth_mode="none"` in `settings.json`
- use it only on a trusted private network
- the Security page provides a way back to secure login by creating the first admin user
- disabling persisted no-auth returns the install to secure-login setup, not to shared browser legacy API-key mode

### `CW_DISABLE_AUTH=true`

This disables the normal API-key and RBAC checks.

Use it only on an isolated, fully trusted network. It is intentionally called out in the UI because it opens the app far beyond the recommended posture.

This is a temporary break-glass runtime override, not the recommended way to configure normal long-term auth behavior.

- it changes only the effective runtime auth state for the running process
- it does not rewrite the saved `auth_mode` in `settings.json`
- removing the env override restores the previously saved auth mode on restart
- it does not re-enable the legacy shared browser API-key UX unless that was already part of the saved configuration

Compose and env can start the app and seed an empty config, but the UI and persisted config remain the long-term source of truth for auth setup and normal mode changes.

## Session cookies and CSRF protection

When RBAC is enabled, ChannelWatch issues a `channelwatch_session` cookie.

Current cookie behavior:

- `HttpOnly`
- `Secure` when ChannelWatch sees HTTPS directly or via `X-Forwarded-Proto: https`
- `SameSite=Strict`

For TLS deployments behind a reverse proxy, make sure the proxy forwards `X-Forwarded-Proto: https`; plain HTTP requests intentionally do not receive the `Secure` cookie attribute.

Mutating requests made under a logged-in session must also present the matching `X-CSRF-Token`.

For legacy API-key-authenticated requests, ChannelWatch relies on the custom-header pattern plus a locked-down CORS posture to make browser-driven CSRF much harder.

When `CW_DISABLE_AUTH=true`, the backend falls back to an Origin-versus-Host check on state-changing requests.

### Honest limitation

If you leave API-key fallback active, the shared key still grants broad access even when RBAC is turned on. That is why `RBAC_WITH_API_KEY_FALLBACK` should be treated as a transition mode, not an end state, for exposed deployments.

## Browser security headers

The backend sends security headers on responses, including:

- `Content-Security-Policy`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` with camera, microphone, and geolocation disabled


## Encryption at rest

ChannelWatch currently encrypts per-DVR API keys stored in `dvr_servers[*].api_key` using `/config/encryption.key`.

This helps reduce accidental plaintext exposure on disk, especially when an operator opens `settings.json` or handles a backup.

It does not solve full host compromise or filesystem exposure.

Important limits:

- the encrypted settings and the encryption key live on the same volume
- someone who can freely read `/config` can usually recover both
- other operational secrets in `settings.json` still require filesystem protection

So treat this as defense in depth, not as a substitute for host security.

## Sensitive field masking

When the UI reads settings from `GET /api/settings`, sensitive values are masked with `****` instead of being echoed back in full.

That helps avoid accidental exposure in the browser, but it is separate from on-disk protection.

## Backup and restore risk surface

The full backup archive is intentionally restorable, not sanitized.

It can include:

- `settings.json`
- `channelwatch.db`
- per-DVR session state
- `encryption.key` in a sensitive subfolder

That is why the backup flow is admin-only, and why backup archives should be treated as sensitive material.

If you need to share data for troubleshooting, prefer the debug bundle described in [`docs/project/PRIVACY.md`](../docs/project/PRIVACY.md).

## SSRF protections


Outbound webhook receivers should still be treated as trusted destinations. The webhook reference documents HMAC signing, retry behavior, receiver verification examples, and receiver URL guidance in [`docs/reference/webhook.md`](../docs/reference/webhook.md).

## Plugins

Notification plugins are local Python code loaded into the ChannelWatch process. The plugin loader does narrow the formal contract, but plugins still run inside your own container.

Only install plugins you trust.

## Network exposure guidance

ChannelWatch is safest on a private network.

If you expose it beyond that, you should:

- put it behind a reverse proxy
- terminate TLS there
- avoid `CW_DISABLE_AUTH=true`
- prefer `RBAC_ONLY` over shared-key fallback
- restrict who can reach the UI at all

## Recovery

The preferred recovery path is the doctor CLI, not manual inspection of files inside the container for shared browser credentials.

Current recovery command:

```bash
docker exec -it channelwatch channelwatch doctor reset-admin-password --username <admin>
```

This recovery command is RBAC-only.

- it resets the named admin password
- it invalidates all existing sessions for that user so re-authentication is required
- if you omit `--password`, any generated temporary password is printed only to the operator's stdout
- the generated password is not written to ChannelWatch app logs or exposed through the UI

If the install is still in true first-run setup, or if `auth_mode="none"` was explicitly saved, the command exits non-zero and directs the operator to complete secure-login setup from the web UI Security page instead.

The recovery command does not rewrite the persisted auth mode and does not toggle `CW_DISABLE_AUTH`.

## Public feeds

ChannelWatch now supports optional public feeds for calendar and recent activity.

- the ICS calendar feed and the RSS/Atom recent-activity feeds are disabled by default
- access is protected by dedicated feed tokens, not the shared UI API key or session cookie
- treat the full feed URL as a bearer secret because most feed clients send the token in the query string
- the RSS and Atom endpoints share one explicit activity-feed token configuration

## Deployment and Helm posture

The repository now ships a Helm chart, but it is intentionally single replica only.

That is not a temporary documentation omission. The chart itself enforces `replicaCount=1` because ChannelWatch uses shared writable state under `/config` and is not designed for concurrent writers.

The final v0.9 polish pass verified the chart with `helm lint`, confirmed the default render creates no Ingress, and confirmed `--set ingress.enabled=true` renders one `networking.k8s.io/v1` Ingress. Those checks do not change the single-replica posture.

## Shipped v0.9 security hardening


- generated supervisor credentials at container start instead of public hardcoded credentials
- masked sensitive settings returned by `GET /api/settings`
- RBAC setup, role checks, session cookies, CSRF checks, and API-key compatibility controls documented in [`docs/reference/api.md`](../docs/reference/api.md)
- browser security headers and a restrictive CSP
- notification image SSRF filtering
- rate limits on API routes, with health and metrics exemptions documented in [`docs/reference/api.md`](../docs/reference/api.md)

## Supply chain state

Current release posture:

- images are built and published through GitHub Actions
- provenance attestation is part of the release story
- dependency updates are tracked in the repository

Not yet claimed as complete here:

- cosign image signing
- SBOM publication as a finished shipped requirement

Those remain follow-up hardening work, not a v0.9 claim.

## Disclosure policy

Once a fix is ready, the normal path is:

1. publish a patched release
2. publish a security advisory with details
3. note the fix in `docs/releases/CHANGELOG.md`

Please do not disclose the issue publicly before a fix is available, or before 90 days have passed from the initial private report, whichever comes first.

## Credit

Responsible reporters can be credited in the related advisory if they want that credit.
