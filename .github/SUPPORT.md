# Getting Help with ChannelWatch

ChannelWatch is a community project maintained by one person. This page explains where to get help and how to report problems so issues get resolved as quickly as possible.

---

## Step 1: Check the FAQ

Most setup questions are answered here.

**ChannelWatch won't start / container exits immediately**
Start with `docker logs channelwatch`. The most common causes are a port conflict on `8501`, a `/config` volume that isn't writable by the container user, or a compose/config problem that prevents startup. If you changed your compose file recently, also run `docker compose config` to catch YAML errors before restarting.

**Notifications aren't arriving**
Work through [`docs/how-to/troubleshoot-notifications.md`](../docs/how-to/troubleshoot-notifications.md): check `docker logs channelwatch`, verify DVR connectivity from the Diagnostics tab, confirm your provider credentials under Settings, then make sure the alert type you expect is enabled. If you need a direct provider check for an Apprise URL, you can still test it with `docker exec channelwatch python3 -c "import apprise; a = apprise.Apprise(); a.add('YOUR_URL'); a.notify(title='test', body='test')"`.

**The dashboard shows no activity**
Open the web UI and run the DVR connection test from the Diagnostics tab first. If you need a headless check, confirm ChannelWatch can reach your Channels DVR server with `docker exec channelwatch curl -s http://YOUR_DVR_IP:8089/status`. If that fails, check your network config and the DVR address saved in the web UI. If you still use `CHANNELS_DVR_HOST` or `CHANNELS_DVR_PORT`, treat them as deprecated v0.9 compatibility settings only. They are not the preferred path in current releases, and new changes should be made in the DVR Servers form in the web UI.

**Multi-DVR: one server shows as offline**
Each DVR needs a unique, stable host:port combination. Check that the DVR's IP hasn't changed (use a static IP or hostname). The DVR id is derived from host:port, so changing either creates a new entry.

**I locked myself out / need to reset the admin password**
Fresh installs are setup-first, with secure login recommended. No-auth is an advanced reversible option, legacy API-key mode is compatibility-only, and `CW_DISABLE_AUTH` is a temporary break-glass runtime override.

Use the in-container recovery command for RBAC-enabled installs instead of looking through files inside the container for shared browser credentials:

```bash
docker exec -it channelwatch channelwatch doctor reset-admin-password --username <admin>
```

That command resets the named admin password, invalidates all existing sessions for that user, and prints any generated temporary password only to the operator's stdout.

If the install is still in first-run secure-login setup, or if it was explicitly saved in no-auth mode, the command exits with guidance to finish secure-login setup from the web UI Security page instead. It does not switch auth modes for you.

If you disable persisted no-auth later, the install returns to secure-login setup. It does not fall back to shared browser legacy API-key mode.

Compose and env can start the container and seed an empty config, but the UI remains the long-term source of truth for auth and normal app settings.

**Where are the logs?**
- Container stdout: `docker logs channelwatch`
- Persistent log: `/config/channelwatch.log` inside the container (or your mapped volume)
- Debug bundle: `docker exec channelwatch channelwatch doctor debug bundle` (if available in your version)

---

## Step 2: Search Discussions

[GitHub Discussions](https://github.com/CoderLuii/ChannelWatch/discussions) is the right place for:

- Setup and configuration questions
- "How do I..." questions
- Sharing your setup or tips with others
- Feedback and ideas that aren't ready to be filed as feature requests

Search before posting. Your question has probably been asked before.

---

## Step 3: Open an Issue (bugs only)

The issue tracker is for **confirmed bugs and feature requests**. It's not a support forum.

Before opening a bug report:
1. Confirm you're on the latest version (`docker pull coderluii/channelwatch:latest`)
2. Check that the issue isn't already reported
3. Collect your logs and compose snippet

Use the matching issue template so the report includes the right fields:

- [Open a bug report](https://github.com/CoderLuii/ChannelWatch/issues/new?template=bug_report.yml)
- [Request a feature](https://github.com/CoderLuii/ChannelWatch/issues/new?template=feature_request.yml)
- [Report a documentation problem](https://github.com/CoderLuii/ChannelWatch/issues/new?template=documentation-improvement.md)
- [Ask a question](https://github.com/CoderLuii/ChannelWatch/issues/new?template=question.yml) only if Discussions is not a fit

**Do not open issues for questions.** Questions opened as issues will be redirected to Discussions and closed.

---

## Security vulnerabilities

Do not report security vulnerabilities in public issues. Use the [private security advisory](https://github.com/CoderLuii/ChannelWatch/security/advisories/new) instead.

See [SECURITY.md](SECURITY.md) for the full disclosure policy and response SLA.

---

## Response expectations

ChannelWatch is a solo project. Response times vary, but the general targets are:

- Security advisories: 48-hour acknowledgment
- Bug reports with full reproduction info: best effort, usually within a few days
- Feature requests: reviewed periodically; no timeline guarantees
- Questions in Discussions: community-driven; no SLA

The fastest way to get a bug fixed is to include a complete reproduction case in your report.
