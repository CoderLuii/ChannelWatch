# ChannelWatch 📺🔔

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Pulls](https://badgen.net/docker/pulls/coderluii/channelwatch?icon=docker)](https://hub.docker.com/r/coderluii/channelwatch)

## 📊 Version History

- v0.2 - Security updates, addressing vulnerabilities in base dependencies
- v0.1 - Initial release

## 🌟 Overview

ChannelWatch is a lightweight Docker container that monitors Channels DVR log files and sends real-time Pushover notifications when TV viewing begins. Track household TV usage with minimal setup and resource requirements.

## 📋 Key Features

- 🔍 Real-time monitoring of Channels DVR log file
- 📲 Push notifications via Pushover
- 📺 Detailed viewing information (channel name, number, resolution)
- ⚙️ Configurable check intervals
- 💻 Low resource usage
- 🐳 Simple Docker deployment

## 🔧 Prerequisites

- Docker and Docker Compose
- Channels DVR with accessible log file
- Pushover account
  - User Key
  - API Token

## 🚀 Quick Setup

### 1. Docker Compose Configuration

Create a `docker-compose.yml` file:

```yaml
version: '3.0'
services:
  ChannelWatch:
    image: coderluii/channelwatch:latest
    container_name: channelwatch
    network_mode: bridge
    volumes:
      - /path/to/your/config/directory:/config
      - /path/to/channels-dvr.log:/channels-dvr.log:ro
    environment:
      LOG_CHECK_INTERVAL: 10
      Alerts_Channel-Watching: TRUE
      PUSHOVER_USER_KEY: your_user_key_here
      PUSHOVER_API_TOKEN: your_api_token_here
    restart: unless-stopped
```

### 2. Start the Container

```bash
docker-compose up -d
```

### 3. Monitor Logs

```bash
docker logs -f channelwatch
```

## ⚙️ Configuration Options

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_CHECK_INTERVAL` | Seconds between log file checks | `10` |
| `Alerts_Channel-Watching` | Enable channel watching notifications | `TRUE` |
| `PUSHOVER_USER_KEY` | Pushover user key | Required |
| `PUSHOVER_API_TOKEN` | Pushover API token | Required |

### Volume Mounts

| Container Path | Purpose |
|----------------|---------|
| `/config` | Configuration storage |
| `/channels-dvr.log` | Channels DVR log file (read-only) |

## 📱 Notification Example

Sample notification when TV viewing starts:

```
Watching: ABC (ch7)
Resolution: 1920x1080
Source: HDHR
```

## 🔍 Troubleshooting

### No Notifications

1. Check container logs: `docker logs channelwatch`
2. Verify Pushover credentials
3. Ensure Channels DVR log file is mounted and readable

### Container Instability

1. Check log file permissions
2. Verify network access to Pushover API

## 🛠️ Project Structure

```
ChannelWatch/
├── channelwatch/
│   ├── __init__.py
│   ├── main.py
│   ├── core/
│   │   ├── alert_manager.py
│   │   ├── log_monitor.py
│   │   └── notification.py
│   ├── alerts/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── channel_watching.py
│   │   └── patterns.py
│   └── helpers/
│       ├── __init__.py
│       ├── logging.py
│       └── parsing.py
├── Dockerfile
└── docker-compose.yml
```

## 📜 License

MIT License - see the LICENSE file for details.

## 🙌 Acknowledgments

- [Channels DVR](https://getchannels.com/) for exceptional DVR software
- [Pushover](https://pushover.net/) for reliable notifications

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/CoderLuii/ChannelWatch/issues).

## 💡 Support This Project

If you find ChannelWatch helpful, consider supporting its development:

- [GitHub Sponsors](https://github.com/sponsors/CoderLuii)
- [Buy Me a Coffee](https://buymeacoffee.com/CoderLuii)
