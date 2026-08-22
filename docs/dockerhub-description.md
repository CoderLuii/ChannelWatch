# ChannelWatch

ChannelWatch is a self-hosted monitoring and notification dashboard for Channels DVR.

It watches DVR activity, recording events, VOD playback, disk space, and service health from a single container. The v0.9 release adds multi-DVR setup, first-run discovery, per-DVR status, notification routing, delivery history, backup and restore, health checks, metrics, an in-app Update Center, and a maintained Unraid template.

## Images

- Docker Hub: `coderluii/channelwatch`
- GHCR: `ghcr.io/coderluii/channelwatch`

Recommended tags:

- `latest` for the newest stable image
- `0.9.16` for the v0.9.16 release

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
      CHANNELWATCH_SECRET_STORAGE_KEY: "${CHANNELWATCH_SECRET_STORAGE_KEY:?set a unique value of at least 32 characters}"
      PUID: "1000"
      PGID: "1000"
    restart: unless-stopped
```

Open `http://localhost:8501` after the container starts.

## Updating

Use v0.9.16 or `latest` for the current v0.9 release. It keeps monitoring alive through empty and offline startup states, coordinates Update Center activation across core and UI, tightens proxy/auth/health privacy boundaries, and repairs the Helm secret contract.

After installing a version with Update Center support through Docker, Unraid, Compose, or Helm, compatible app-only releases can be checked, verified, backed up, applied, and rolled back from **Settings > Updates**.

v0.9.16 requires a normal container image update because it changes the image-stable launcher, entrypoint privilege handling, lifecycle coordination, and deployment contract. The Update Center intentionally reports this release as **container image update required**.

Releases that change the container runtime still require a normal image update. ChannelWatch will show **container image update required** when that is the safe path.

## Configuration

ChannelWatch stores its settings, logs, database, backups, and encryption key under `/config`. Set `CHANNELWATCH_SECRET_STORAGE_KEY` to a unique value of at least 32 characters so new local secret files are written with envelope encryption.

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
