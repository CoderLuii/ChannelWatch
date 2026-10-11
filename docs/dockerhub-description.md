# ChannelWatch

ChannelWatch is a self-hosted monitoring and notification dashboard for Channels DVR.

It watches DVR activity, recording events, VOD playback, disk space, and service health from a single container. The v0.9 release adds multi-DVR setup, first-run discovery, per-DVR status, notification routing, delivery history, backup and restore, health checks, metrics, an in-app Update Center, and a maintained Unraid template.

The Dashboard also suggests maintenance windows from known recording jobs, with minimum duration, day, and time filters.

## Images

- Docker Hub: `coderluii/channelwatch`
- GHCR: `ghcr.io/coderluii/channelwatch`

Recommended tags:

- `latest` for the newest stable image
- `1.5.0` for the current release

## Quick Start

```yaml
services:
  channelwatch:
    image: coderluii/channelwatch:latest
    container_name: channelwatch
    ports:
      - "8501:8501"
    volumes:
      - ./config:/config
    environment:
      TZ: America/Los_Angeles
      PUID: "1000"
      PGID: "1000"
    restart: unless-stopped
```

Open `http://localhost:8501` after the container starts.

## Updating

Use v1.5.0 for the current release, or `latest` for the newest stable image. Preserve `/config` and any external storage key configuration when recreating the container.

ChannelWatch v1.5.0 requires a container image update. Every published v1.4.x installation needs this one-time recreation to receive launcher protocol 4. Keep the same `/config` volume; saved DVRs, credentials, settings, history, and the application-managed encryption key remain there.

After v1.5.0 is running, **Settings > Updates** can install future compatible application code and official Python packages as one signed runtime generation. The application and packages roll back together if startup fails. Python interpreter, OS, glibc, Supervisor, and image-owned verifier updates still require a new container image.

Published v1.0.0 through v1.5.0 use `X.Y.0` as a container-image milestone. A future `.0` release can keep the proven v1.5.0 image floor only after its complete signed runtime generation passes the required AMD64 and ARM64 compatibility, restart, and rollback checks. Versions `X.Y.1` through `X.Y.9` install through **Settings > Updates**, and the next release after `X.Y.9` is `X.(Y+1).0`.

**Still on v0.9.9 or v0.9.10? Do not use the old in-app bridge for this upgrade.** Preserve `/config` and pull/recreate the v1.5.0 image. It repairs stale legacy update markers without discarding the preserved configuration; after this image refresh, use Update Center normally.

An already-blocked v0.9.17 installation with a missing or incorrect old deployment key cannot reach its old portal. Preserve `/config` and pull/recreate v1.5.0, or provide the correct old key for one migration restart.

After v0.9.18 or newer is installed, a setup or legacy-recovery state can use a narrowly scoped official signed recovery update before normal admin navigation is available. It requires same-origin anti-CSRF state and exact typed confirmation and does not accept custom feeds, URLs, uploads, keys, or downgrades.

Releases that change the interpreter or image-owned runtime still require a normal image update. ChannelWatch will show **container image update required** when that is the safe path.

## Configuration

ChannelWatch stores its settings, logs, database, backups, and managed encryption key under `/config`. There is no key to generate for a new installation. Protect the volume and app backups like credential storage.

For an existing legacy envelope created by v0.9.5–v0.9.17, keep the old `CHANNELWATCH_SECRET_STORAGE_KEY` or key-file input for the first restart on a migration-capable image (v0.9.18 or newer), including v1.5.0. ChannelWatch converts the same logical key to local managed storage. For v0.9.9 and v0.9.10, preserve `/config` and those key inputs when recreating directly with v1.5.0. If the old key is lost, the authenticated Security page can reset only unrecoverable DVR API keys and custom webhook URLs/secrets while preserving other settings and history.

DVR setup is easiest through the web UI. For bootstrap-only deployments, `CHANNELS_DVR_SERVERS` supports comma-separated `Name@host:port` entries.

## Links

- Project: https://github.com/CoderLuii/ChannelWatch
- Support: https://github.com/CoderLuii/ChannelWatch/discussions
- Documentation: https://channelwatch.coderluii.dev/

## License and release verification

ChannelWatch is MIT-licensed. The image carries the project license, notice,
third-party inventory, complete applicable copyleft license texts, and the
exact corresponding-source/rebuild map under `/licenses/channelwatch`. Exact
amd64 and arm64 SPDX/CycloneDX SBOMs and a checksum manifest covering every
other attached artifact are included with the corresponding GitHub Release.

- License: https://github.com/CoderLuii/ChannelWatch/blob/main/LICENSE
- Third-party licenses: https://github.com/CoderLuii/ChannelWatch/blob/main/docs/legal/THIRD_PARTY_LICENSES.md
- Corresponding source: https://github.com/CoderLuii/ChannelWatch/blob/main/docs/legal/CORRESPONDING_SOURCE.md
- Releases and SBOMs: https://github.com/CoderLuii/ChannelWatch/releases
