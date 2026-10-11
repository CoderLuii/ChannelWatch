# Update ChannelWatch

ChannelWatch v1.5.0 requires its matching container image. Pull `coderluii/channelwatch:1.5.0` (or `ghcr.io/coderluii/channelwatch:1.5.0`) and recreate the container with the same `/config` volume and any external storage key configuration.

Every published v1.4.x image needs this one-time update to receive launcher protocol 4. Update directly to v1.5.0 through Docker, Unraid, Compose, or Helm. Preserve the entire existing `/config` volume. Do not re-enter saved DVRs or credentials merely to update.

The updater waits for stable target-runtime and Settings checks, opens Dashboard Overview, and hard-refreshes the activated frontend once. **Retry now** uses the same checks when a restart briefly disconnects the dashboard. Application and immutable container image versions are reported separately.

## Upgrade and recovery notes

### Recover an abandoned pre-v1.0.2 scheduler lock

Some v1.0.0 and v1.0.1 installations can repeatedly show `Another update operation is already running` even though no update is active. Refreshing or restarting only the ChannelWatch Core and UI processes may recreate the abandoned marker. Do not delete application data or repeatedly click Apply.

Preserve the existing `/config` mount, pull `coderluii/channelwatch:1.5.0`, and recreate the container once. The current image contains the v1.0.2 scheduler-lock repair, which replaces the old existence-based marker with an operating-system-held advisory lock and safely removes the abandoned legacy marker during startup. Saved DVRs, credentials, settings, history, and the application-managed encryption key remain under `/config`.

v0.9.19 migrates valid historical `activity_history.json` rows into the durable SQLite activity store automatically. Recent Activity and the 24-Hour Timeline then use the same data. Do not edit or delete activity files manually, and do not re-enter DVR credentials for this update.

The Update Center remains the normal path for routine releases. Published v1.0.0 through v1.5.0 use `X.Y.0` as an image milestone, while `X.Y.1` through `X.Y.9` install in-app. A future `.0` release can retain the proven v1.5.0 image floor only after its full signed runtime generation passes the required compatibility, restart, and rollback checks on AMD64 and ARM64. After `X.Y.9`, the next release is `X.(Y+1).0`.

If the catalog includes a newer image milestone and a compatible app update for your current image, Update Center shows both choices separately. You can install the highest compatible signed app update now, then refresh the container later. The image recommendation does not hide that forward app update, and the app update does not clear the image recommendation.

If you are still on an immutable published v0.9.9 or v0.9.10 image, **do not use its old in-app bridge for this upgrade**. Preserve the existing `/config` volume and pull/recreate the v1.5.0 image. The new image repairs stale legacy update markers without discarding settings or invalidating protected credentials. Future compatible releases then use the improved Update Center normally.

Operational v0.9.11–v0.9.17 installations can upgrade directly to v0.9.18 through Update Center. This includes the common v0.9.15, v0.9.16, and v0.9.17 installations. The stable v0.9.18 manifest keeps runtime ABI `channelwatch-runtime-v1` and settings schema `7`, so those compatible images can verify and activate the new bundle without intermediate releases.

An already-blocked v0.9.17 installation with a missing or incorrect external key cannot reach the old authenticated Update Center. Preserve `/config` and pull/recreate v1.5.0, or restore the correct old key for one migration restart. Fresh installations using the current image do not need an encryption variable.

If a future credential-protection problem blocks normal administrator navigation after v0.9.18 is installed, the setup/recovery shell can check and apply only the official signed stable recovery update. That narrow path requires same-origin anti-CSRF state and exact typed confirmation; it cannot accept a custom feed, upload, signing key, URL, or downgrade.

## Update policy

The default policy automatically installs verified compatible updates during the local 03:00–05:00 maintenance window. In **Settings > Updates**, an administrator can:

- switch to notify-only mode;
- apply an available update immediately;
- postpone automatic installation for 24 hours or 7 days;
- retry a failed verified update;
- inspect the application version, container image version, runtime source, ABI, and launcher protocol;
- roll back to the previous compatible app runtime.

Automatic policy never bypasses signature, digest, URL, archive, ABI, schema, backup, activation, or rollback checks. Releases that genuinely require a different operating system, interpreter, incompatible package set, entrypoint, Supervisor configuration, launcher protocol, image-owned verifier, or persistent schema remain container-image updates.

## Check for updates

1. Open ChannelWatch.
2. Go to **Settings > Updates**.
3. Click **Check for updates**.

ChannelWatch fetches public release metadata from the official ChannelWatch docs site. It does not send telemetry, install identifiers, DVR details, settings, or usage data.

## Apply a compatible update

When an app-only update is available, the Update Center:

1. verifies the signed update manifest;
2. downloads the signed app bundle from the trusted release URL;
3. verifies the bundle SHA256 and Ed25519 signature;
4. rejects unsafe archive paths and unsupported files;
5. creates a pre-update backup under `/config/backups/`;
6. installs the bundle under `/config/channelwatch-runtime/releases/vX.Y.Z`;
7. atomically points `/config/channelwatch-runtime/active.json` at the new bundle;
8. restarts ChannelWatch through Supervisor or the container restart fallback;
9. records the activation result in the update job state.

ChannelWatch versions that include the reconnect improvement wait for the target runtime, verify Settings twice, open Dashboard Overview, and hard-refresh the page automatically. Rejected, failed, or timed-out applies remain on the Update Center so their remediation stays visible. Refresh manually only if browser code older than this improvement cannot reconnect. Direct v0.9.11–v0.9.17 upgrades to v0.9.18 use a compatible launcher and preserve `/config`; v0.9.9 and v0.9.10 instead require the one-time image pull/recreate described above.

### Wait through a restart

The reconnect code was written for v1.4.2. Publications of v1.4.2 through v1.4.4 failed before any release assets were published, so it first ships in v1.4.5. Update Center follows the saved update job for up to three minutes while Core and UI restart and validate the installation. A brief lost connection during this wait does not mean the update failed. Leave the page open; it refreshes once the matching job succeeds and the new runtime and Settings checks pass.

If you reopen Settings during an update, Update Center resumes watching that job without submitting another installation. Apply and Retry stay disabled while it waits. A failed or rolled-back installation shows the recorded failure instead of refreshing as though it succeeded.

If the wait expires, check Update Center again before retrying. A browser timeout does not cancel an installation already running on the server. Keep your existing `/config` volume and storage key configuration; a compatible app update does not require recreating the container or re-entering DVR settings.

This improvement runs in the browser code supplied by v1.4.5. When installing v1.4.5 from an older version, the page initially uses that older version's reconnect behavior until the new frontend loads.

## Roll back

If a newly activated app bundle fails during startup, the image-stable launcher records the failure and rolls back to the previous runtime or the image copy.

When the UI is reachable, **Settings > Updates** also shows a **Roll back** button when rollback metadata is available. Rollback changes the active runtime pointer and restarts ChannelWatch.

Rollback does not restore old settings or database state. If you need to restore data, use **Settings > Backup** or a manual `/config` backup.

## Install a dependency generation

Dependency installation requires an image with launcher protocol 4. Published
v1.4.x images need a one-time image update to get that launcher.
Keep the same `/config` volume and external storage key configuration. The
container update does not require re-entering DVRs or saved credentials.

After that bootstrap, install a compatible signed release from
**Settings > Updates**. ChannelWatch downloads and verifies the release,
prepares its Python packages without changing the active environment, then
restarts and checks the new core and UI. Keep the page open to watch progress,
or return to Updates to inspect the same job. Do not submit Apply again while
preparation is running. The dashboard refreshes after confirmed success.

The application, frontend and packages roll back together if activation fails.
Rollback does not restore settings or database contents. Keep normal backups.
Generation storage must allow execution; a `noexec` `/config` mount cannot
prepare this runtime. A storage or verification error leaves the previous
selection in place and shows the reason in the update job.

If the container stops during preparation, ChannelWatch keeps the selected
release. After that runtime starts again, the interrupted job shows a failure
and can be retried from Updates. A running installation keeps its existing job.

Python interpreter updates and OS, glibc, Supervisor or image-verifier fixes
still use the container update path. The in-app updater does not manage the
host Docker socket or recreate containers on your behalf.

## Other container image requirements

Some releases cannot be safely applied inside the current image. ChannelWatch will show **container image update required** when a release changes:

- Python dependencies unsupported by the installed launcher or platform;
- Python interpreter;
- base image or OS packages;
- Supervisor or container startup behavior;
- runtime ABI;
- container entrypoint, Supervisor, privilege, or image-owned launcher behavior;
- persistent settings schema.

Published v1.0.0 through v1.5.0 use every `X.Y.0` release as an intentional image milestone. This keeps one known container baseline for the following `X.Y.1` through `X.Y.9` in-app releases and makes the required update method clear from the version number. A future `.0` release can retain the proven v1.5.0 floor only with a complete signed runtime generation and matching native AMD64 and ARM64 evidence.

Documentation, Compose, Helm, or Unraid presentation changes alone do not make an otherwise compatible runtime generation image-required. v1.5.0 changes the image-owned launcher and verifier and therefore requires its matching image on AMD64 or ARM64.

When this appears, update the container using your normal Docker, Unraid, Compose, or Helm process. The in-app updater intentionally does not replace the Docker image.

## Auth-disabled warning

If ChannelWatch is running with `CW_DISABLE_AUTH=true`, anyone who can reach the UI can use admin actions, including updates and rollback. Keep auth-disabled installs on a trusted private network only.

## Files used by Update Center

Update Center runtime files live under `/config/channelwatch-runtime/`:

| Path | Purpose |
| --- | --- |
| `active.json` | Active app bundle pointer. |
| `latest.json` | Last trusted update manifest. |
| `rollback.json` | Previous runtime pointer and pre-update backup path. |
| `update-job.json` | Last update/check/rollback operation state. |
| `update.lock` | Single-flight update lock. Stale locks are discarded automatically. |
| `releases/vX.Y.Z/` | Extracted verified app bundle. |
| `releases/<generation-sha256>/` | Protocol-4 application and dependency generation. |
| `trust/<generation-sha256>.json` | Retained signed catalog used by the image launcher. |

Normal app backups do not include downloaded app bundles. Keep your normal `/config` backup routine for disaster recovery.
