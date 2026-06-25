# Update ChannelWatch

Install ChannelWatch v0.9.10 or newer normally through Docker, Unraid, Compose, or Helm. After that, use **Settings > Updates** for compatible app-only releases.

The Update Center is meant to make routine updates feel like a normal app update while keeping container-runtime changes explicit and safe.

If you pulled v0.9.9, update the container image to v0.9.10 first. That repair release touches Docker entrypoint and runtime behavior, so ChannelWatch marks it as **container image update required** instead of applying it as an in-app bundle.

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

Refresh the page after the restart if your browser does not reconnect automatically.

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
- Helm or deployment assumptions;
- persistent settings schema.

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
