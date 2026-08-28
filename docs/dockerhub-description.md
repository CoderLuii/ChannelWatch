# ChannelWatch

ChannelWatch is a self-hosted monitoring and notification dashboard for Channels DVR.

It watches DVR activity, recording events, VOD playback, disk space, and service health from a single container. The v0.9 release adds multi-DVR setup, first-run discovery, per-DVR status, notification routing, delivery history, backup and restore, health checks, metrics, an in-app Update Center, and a maintained Unraid template.

## Images

- Docker Hub: `coderluii/channelwatch`
- GHCR: `ghcr.io/coderluii/channelwatch`

Recommended tags:

- `latest` for the newest stable image
- `0.9` for the current compatible v0.9 release
- `1.0.0` for the v1.0 image milestone
- `1.0.8` for a fresh install containing the current v1.0.8 app; existing operational v1.0.0 through v1.0.7 installations update in-app

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

Use v1.0.0, `1.0`, or `latest` for the current v1.0 image milestone. Preserve `/config` when recreating the container; ChannelWatch continues managing credential protection automatically.

Existing operational v1.0.0 through v1.0.7 installations install v1.0.8 through **Settings > Updates**. A compatible v1.0 container running the signed v1.0.8 bundle is fully current and does not need to be recreated. After activation, v1.0.8 opens Dashboard Overview and follows monitoring startup automatically until it becomes healthy or reaches a real degraded state. It also reads immutable image metadata instead of a stale container environment value when displaying image version and launcher compatibility. If an older Update Center is already trapped by an abandoned scheduler lock, preserve `/config` and recreate with `coderluii/channelwatch:1.0.8` once; no settings or credentials need to be re-entered.

From v1.0.0 forward, every `X.Y.0` version is a container-image milestone. Versions `X.Y.1` through `X.Y.9` install through **Settings > Updates**, and the next release after `X.Y.9` is `X.(Y+1).0`.

**Still on v0.9.9 or v0.9.10? Do not use the old in-app bridge for this upgrade.** Preserve `/config` and pull/recreate the v1.0.0 image. It repairs stale legacy update markers without discarding the preserved configuration; after this image refresh, use Update Center normally.

An already-blocked v0.9.17 installation with a missing or incorrect old deployment key cannot reach its old portal. Preserve `/config` and pull/recreate v1.0.0, or provide the correct old key for one migration restart.

After v0.9.18 or newer is installed, a setup or legacy-recovery state can use a narrowly scoped official signed recovery update before normal admin navigation is available. It requires same-origin anti-CSRF state and exact typed confirmation and does not accept custom feeds, URLs, uploads, keys, or downgrades.

Releases that change the container runtime still require a normal image update. ChannelWatch will show **container image update required** when that is the safe path.

## Configuration

ChannelWatch stores its settings, logs, database, backups, and managed encryption key under `/config`. There is no key to generate for a new installation. Protect the volume and app backups like credential storage.

For an existing legacy envelope created by v0.9.5–v0.9.17, keep the old `CHANNELWATCH_SECRET_STORAGE_KEY` or key-file input for the first v0.9.18 restart. ChannelWatch converts the same logical key to local managed storage. v0.9.9 and v0.9.10 still need the documented one-time v0.9.18 image pull before that migration can run. If the old key is lost, the authenticated Security page can reset only unrecoverable DVR API keys and custom webhook URLs/secrets while preserving other settings and history.

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
