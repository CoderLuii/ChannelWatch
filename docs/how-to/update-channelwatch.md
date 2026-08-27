# Update ChannelWatch

Install ChannelWatch v1.0.0 through Docker, Unraid, Compose, or Helm while preserving `/config`. v1.0.0 is the first intentional `.0` container-image milestone and establishes the runtime used by the v1.0.1 through v1.0.9 in-app update line.

ChannelWatch v1.0.1 is the first release on that in-app line. A v1.0.0 installation discovers and installs it through **Settings > Updates**. After activation, `Application version` shows v1.0.1 while `Container image version` may remain v1.0.0. That combination is fully current and does not require an image refresh.

v0.9.19 migrates valid historical `activity_history.json` rows into the durable SQLite activity store automatically. Recent Activity and the 24-Hour Timeline then use the same data. Do not edit or delete activity files manually, and do not re-enter DVR credentials for this update.

The Update Center remains the normal path for routine releases. From v1.0.0 forward, `X.Y.0` releases require the matching image, while `X.Y.1` through `X.Y.9` install in-app. After `X.Y.9`, the next release is `X.(Y+1).0`.

If you are still on an immutable published v0.9.9 or v0.9.10 image, **do not use its old in-app bridge for this upgrade**. Preserve the existing `/config` volume and pull/recreate the v1.0.0 image. The new image repairs stale legacy update markers without discarding settings or invalidating protected credentials. Future compatible releases then use the improved Update Center normally.

Operational v0.9.11–v0.9.17 installations can upgrade directly to v0.9.18 through Update Center. This includes the common v0.9.15, v0.9.16, and v0.9.17 installations. The stable v0.9.18 manifest keeps runtime ABI `channelwatch-runtime-v1` and settings schema `7`, so those compatible images can verify and activate the new bundle without intermediate releases.

An already-blocked v0.9.17 installation with a missing or incorrect external key cannot reach the old authenticated Update Center. Preserve `/config` and pull/recreate v1.0.0, or restore the correct old key for one migration restart. Fresh v1.0.0 and Project One-Click-style installations do not need an encryption variable.

If a future credential-protection problem blocks normal administrator navigation after v0.9.18 is installed, the setup/recovery shell can check and apply only the official signed stable recovery update. That narrow path requires same-origin anti-CSRF state and exact typed confirmation; it cannot accept a custom feed, upload, signing key, URL, or downgrade.

## Update policy

The default policy automatically installs verified compatible updates during the local 03:00–05:00 maintenance window. In **Settings > Updates**, an administrator can:

- switch to notify-only mode;
- apply an available update immediately;
- postpone automatic installation for 24 hours or 7 days;
- retry a failed verified update;
- inspect the application version, container image version, runtime source, ABI, and launcher protocol;
- roll back to the previous compatible app runtime.

Automatic policy never bypasses signature, digest, URL, archive, ABI, schema, backup, activation, or rollback checks. Releases that genuinely require a different operating system, interpreter, dependency set, entrypoint, Supervisor configuration, launcher protocol, or persistent schema remain container-image updates.

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

ChannelWatch versions that include the reconnect improvement wait for the target runtime and reload the page automatically. Refresh the page manually if older browser code does not reconnect after the restart. Direct v0.9.11–v0.9.17 upgrades to v0.9.18 use a compatible launcher and preserve `/config`; v0.9.9 and v0.9.10 instead require the one-time image pull/recreate described above.

## Roll back

If a newly activated app bundle fails during startup, the image-stable launcher records the failure and rolls back to the previous runtime or the image copy.

When the UI is reachable, **Settings > Updates** also shows a **Roll back** button when rollback metadata is available. Rollback changes the active runtime pointer and restarts ChannelWatch.

Rollback does not restore old settings or database state. If you need to restore data, use **Settings > Backup** or a manual `/config` backup.

## Container image update required

Some releases cannot be safely applied inside the current image. ChannelWatch will show **container image update required** when a release changes:

- Python dependencies;
- base image or OS packages;
- Supervisor or container startup behavior;
- runtime ABI;
- container entrypoint, Supervisor, privilege, or image-owned launcher behavior;
- persistent settings schema.

Starting with v1.0.0, every `X.Y.0` release is also an intentional image milestone even when its individual changes could fit in a bundle. This keeps one known container baseline for the following `X.Y.1` through `X.Y.9` in-app releases and makes the required update method clear from the version number.

Documentation, Compose, Helm, or Unraid presentation changes alone do not make an otherwise ABI-compatible app bundle image-required. v1.0.1 still publishes normal AMD64/ARM64 images for fresh installations, but existing v1.0.0 users do not need to pull them.

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

Normal app backups do not include downloaded app bundles. Keep your normal `/config` backup routine for disaster recovery.
