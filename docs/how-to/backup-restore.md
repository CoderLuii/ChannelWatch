# Back up and restore ChannelWatch

<!-- cspell:ignore healthz -->

Use this guide when you need a restorable copy of ChannelWatch configuration, DVR state, and activity data. Make a manual `/config` backup for disaster recovery. Use the Settings backup button when you want a portable ChannelWatch backup zip created by the app.

The app backup contains sensitive material. Store it somewhere private, restrict access to it, and do not upload it to public issue trackers, shared paste sites, or chat rooms.

## When to back up

Back up before any change that would be painful to undo:

1. Before changing the running container image or making a large settings change.
2. On a regular schedule, such as before weekly maintenance or after known good settings changes.
3. Before major settings edits, including DVR server changes, notification routing changes, authentication changes, provider credential changes, and manual edits to `/config/settings.json`.

## Manual file backup

A manual archive of `/config` is the safest rollback backup because it can include every persisted file, including files the app backup does not include.

For a bind mount, archive the host directory that is mounted as `/config`:

```bash
tar czf channelwatch-config-$(date +%Y%m%d_%H%M%S).tar.gz -C /path/to/channelwatch/config .
```

For a Docker named volume, archive the volume from a temporary container:

```bash
docker run --rm \
  -v channelwatch_config:/config:ro \
  -v "$PWD":/backup \
  alpine \
  tar czf /backup/channelwatch-config-$(date +%Y%m%d_%H%M%S).tar.gz -C /config .
```

Keep these files:

| File | Why it matters |
| --- | --- |
| `settings.json` | Main ChannelWatch settings, DVR definitions, notification settings, and encrypted per DVR API keys. |
| `encryption.key` | Key used to decrypt `fernet:` DVR API keys stored in `settings.json`. |
| `channelwatch.db` | Current SQLite store for activity history, notification delivery rows, and related app state. |
| `session_state_*.json` | Per DVR alert session state. |
| `activity_history.json` | Legacy activity history file, useful when restoring or diagnosing older installs. |
| `backups/` | Automatic settings backups and pre restore snapshots, useful for local rollback. |

You can skip runtime noise when you are making a portable backup:

| File | Why you may skip it |
| --- | --- |
| `channelwatch.log` and rotated logs | Useful for diagnosis, but not required to restore settings or DVR state. |
| Temporary files | Not needed after the container restarts. |
| Old backup archives | Keep one known good archive elsewhere instead of nesting many old archives. |

Back up `/config/encryption.key` separately as well as inside full archives. ChannelWatch keeps this file with permission mode `0600`, root owned by default. Treat it as a secret. If you set `CHANNELWATCH_SECRET_STORAGE_KEY` or `CHANNELWATCH_SECRET_STORAGE_KEY_FILE`, preserve that wrapping secret too. The key material plus `settings.json` can decrypt encrypted per DVR keys. See [`docs/reference/multi-dvr.md`](../reference/multi-dvr.md) for how encrypted DVR keys depend on this file.

## App Backup and Restore

ChannelWatch also ships a backup and restore panel in the web UI.

1. Open ChannelWatch.
2. Go to Settings.
3. Open the Backup tab.
4. Click the backup download button to save a `channelwatch_backup_<timestamp>.zip` file.

The same operation is available through `GET /api/v1/backup/download`. Restore uses `POST /api/v1/backup/restore` with a multipart `file` upload. See [`docs/reference/api.md`](../reference/api.md#backup-and-restore) for request examples, auth requirements, and status codes.

The UI restore button accepts a `.zip` file produced by ChannelWatch. Before writing restored files, ChannelWatch creates a pre restore snapshot under `/config/backups/`, then signals the core process to hot reload.

## What's included and excluded

The app backup zip is created by the FastAPI backend and includes only the files below when they exist:

| Zip entry | Included | Notes |
| --- | --- | --- |
| `settings.json` | Yes | Required for restore validation. |
| `channelwatch.db` | Yes | Contains current activity history and notification delivery data. |
| `session_state_*.json` | Yes | Preserves per DVR alert session state. |
| `sensitive_keys/encryption.key` | Yes, when present | Restored to `/config/encryption.key` with permissions set to `0600`. |
| `sensitive_keys/SECURITY_WARNING.txt` | Yes, when `encryption.key` is included | Documents why the archive is sensitive. |
| `backup_manifest.json` | Yes | Lists the files in the archive and the settings schema version. |

The app backup does not include logs by default. It also does not include legacy `activity_history.json`, existing files under `/config/backups/`, debug bundles, or arbitrary extra files in `/config`. Use a manual `/config` archive when you need those files.

Security implication: the app backup can include both `settings.json` and `sensitive_keys/encryption.key`. Together, those files can decrypt encrypted per DVR API keys. Store the zip like you would store credentials.

## Restore procedure

### Restore with the UI

1. Save a copy of the current `/config` directory before restoring. This gives you a way back if you picked the wrong archive.
2. Open Settings, then Backup.
3. In the restore section, choose a ChannelWatch backup `.zip` file.
4. Wait for the success message: `Restore completed. Core process hot-reloaded.`
5. Reload the page if the UI offers the reload button.

The restore endpoint validates the zip, checks the manifest, refuses backups from a newer settings schema, writes a pre restore snapshot to `/config/backups/`, and restores files into `/config`.

### Restore files manually

Use manual restore when the UI is unavailable or when you need to restore a full `/config` archive.

1. Stop ChannelWatch:

   ```bash
   docker compose down
   ```

2. Move the current config directory aside instead of deleting it:

   ```bash
   mv /path/to/channelwatch/config /path/to/channelwatch/config.failed.$(date +%Y%m%d_%H%M%S)
   mkdir -p /path/to/channelwatch/config
   ```

3. Extract the archive back into the config directory:

   ```bash
   tar xzf channelwatch-config-backup.tar.gz -C /path/to/channelwatch/config
   ```

4. Set safe permissions on the encryption key if it exists:

   ```bash
   chmod 600 /path/to/channelwatch/config/encryption.key
   ```

5. Start ChannelWatch again:

   ```bash
   docker compose up -d
   ```

If you restore the app zip manually, extract the files under the top level `channelwatch_backup_<timestamp>/` folder. Move `sensitive_keys/encryption.key` back to `/config/encryption.key`, not `/config/sensitive_keys/encryption.key`, and set it to `0600`.

## Verifying restore worked

After any restore:

1. Open the UI and confirm Settings loads without setup prompts you did not expect.
2. Check `/healthz/ready` and confirm each enabled DVR appears.
3. Confirm DVR entries, names, ports, and enabled states match the backup.
4. If DVR API keys were encrypted, confirm DVR connectivity works. If the wrong `encryption.key` was restored, encrypted per DVR keys cannot decrypt.
5. Open recent activity or watch history and confirm the expected events appear.
6. Send a test notification for each provider you rely on.
7. Review container logs for restore, migration, permission, or decryption errors.

Keep the backup until you have verified settings, DVR connectivity, activity history, and notification delivery.

## See also

* [`docs/reference/api.md`](../reference/api.md#backup-and-restore) for backup and restore endpoints.
* [`docs/reference/multi-dvr.md`](../reference/multi-dvr.md) for encrypted per DVR API key behavior.
