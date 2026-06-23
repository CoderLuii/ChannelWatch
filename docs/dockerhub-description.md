# ChannelWatch

ChannelWatch is a self-hosted monitoring and notification dashboard for Channels DVR.

It watches DVR activity, recording events, VOD playback, disk space, and service health from a single container. The v0.9 release adds multi-DVR setup, first-run discovery, per-DVR status, notification routing, delivery history, backup and restore, health checks, metrics, and a maintained Unraid template.

## Images

- Docker Hub: `coderluii/channelwatch`
- GHCR: `ghcr.io/coderluii/channelwatch`

Recommended tags:

- `latest` for the newest stable image
- `0.9.3` for the v0.9.3 release
- `0.9` for patch-line pinning

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

## Configuration

ChannelWatch stores its settings, logs, database, backups, and encryption key under `/config`.

DVR setup is easiest through the web UI. For bootstrap-only deployments, `CHANNELS_DVR_SERVERS` supports comma-separated `Name@host:port` entries.

## Links

- Project: https://github.com/CoderLuii/ChannelWatch
- Support: https://github.com/CoderLuii/ChannelWatch/discussions
- Documentation: https://github.com/CoderLuii/ChannelWatch/tree/main/docs
