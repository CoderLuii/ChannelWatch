# ChannelWatch 📺🔔

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Pulls](https://badgen.net/docker/pulls/coderluii/channelwatch?icon=docker)](https://hub.docker.com/r/coderluii/channelwatch)
[![PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://www.paypal.com/donate/?hosted_button_id=PM2UXGVSTHDNL)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg?style=flat&logo=buy-me-a-coffee)](https://buymeacoffee.com/CoderLuii)
[![Twitter Follow](https://img.shields.io/twitter/follow/CoderLuii?style=social)](https://x.com/CoderLuii)


## 📑 Table of Contents
- [Version History](#-version-history)
- [Overview](#-overview)
- [Architecture](#-architecture)
- [Key Features](#-key-features)
- [Performance](#-performance)
- [Support This Project](#-support-this-project)
- [Prerequisites](#-prerequisites)
- [Quick Setup](#-quick-setup)
- [Configuration Options](#️-configuration-options)
- [Troubleshooting](#-troubleshooting)
- [Upgrading](#-upgrading)
- [Project Structure](#️-project-structure)
- [License](#-license)
- [Acknowledgments](#-acknowledgments)
- [Contributing](#-contributing)
- [Future Roadmap](#-future-roadmap)
- [Get Help](#-get-help)

## 📊 Version History

- v0.3.0 - Major rewrite with improved architecture, real-time event monitoring, multi-provider notifications, session tracking, and enhanced stability
- v0.2.0 - Security updates, addressing vulnerabilities in base dependencies
- v0.1.0 - Initial release

## 🌟 Overview

ChannelWatch is a lightweight Docker container that monitors Channels DVR events and sends real-time push notifications when TV viewing begins. Track household TV usage with minimal setup and resource requirements.

## 📊 Architecture

ChannelWatch follows a modular, service-oriented architecture:

- **Event System**: Monitors the Channels DVR event stream
- **Alert System**: Processes events to determine when to send notifications
- **Notification System**: Handles sending notifications through various providers
- **Configuration System**: Centralizes all configuration with environment variables
- **Extension Framework**: Makes it easy to add new alert types and notification providers

## 📋 Key Features

- 🔍 Real-time monitoring of Channels DVR event stream
- 📲 Push notifications via Pushover or Apprise (Discord, Slack, Email, Telegram, etc.)
- 📺 Detailed viewing information (channel name, number, resolution)
- 🖼️ Channel logo support in notifications
- ⚙️ Configurable alert settings
- 🧹 Automatic session cleanup and management
- 💻 Low resource usage
- 🐳 Simple Docker deployment

## ⚡ Performance

ChannelWatch is designed to be lightweight and efficient:

- Minimal CPU usage (<1% on most systems)
- Low memory footprint (~30MB RAM)
- Small Docker image size (~100MB)
- Quick startup time (<5 seconds)

## 💡 Support This Project

If you find ChannelWatch helpful, consider supporting its development:

- [GitHub Sponsors](https://github.com/sponsors/CoderLuii)
- [PayPal](https://www.paypal.com/donate/?hosted_button_id=PM2UXGVSTHDNL)
- [Buy Me a Coffee](https://buymeacoffee.com/CoderLuii)

Follow me on X:
- [Twitter/X](https://x.com/CoderLuii)


## 🔧 Prerequisites

- Docker and Docker Compose
- Channels DVR server with accessible API
- Pushover and/or Apprise account/configuration (Requires at least one provider configured for notifications)

## 🚀 Quick Setup

### 1. Docker Compose Configuration

Create a `docker-compose.yml` file:

```yaml
version: '3.0'
services:
  ChannelWatch:
    image: coderluii/channelwatch:latest
    container_name: channelwatch
    network_mode: host
    volumes:
      # Path to store configuration and logs
      - /your/local/path:/config
    environment:
      # ========== CORE SETTINGS ==========
      # Required: IP address of your Channels DVR server
      CHANNELS_DVR_HOST: x.x.x.x
      
      # Optional: Port for your Channels DVR server (default: 8089)
      # Only change if you've modified the default Channels DVR port
      CHANNELS_DVR_PORT: 8089
      
      # Optional: Timezone for logs and timestamps
      # Find your TZ value at: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
      # Example: America/New_York, Europe/London, Asia/Tokyo
      TZ: Your/Timezone
      
      # ========== LOGGING CONFIGURATION ==========
      # Optional: Log verbosity level (1=Standard, 2=Verbose)
      LOG_LEVEL: 1
      
      # Optional: Number of days to keep log files
      LOG_RETENTION_DAYS: 7
      
      # ========== ALERT CONFIGURATION ==========
      # Enable/disable specific alert types
      # Set to TRUE to enable, FALSE to disable (or remove the line)
      Alerts_Channel-Watching: TRUE
      
      # ========== NOTIFICATION SETTINGS ==========
      # Optional: Enable/disable channel logos in notifications
      CHANNEL_IMAGES: TRUE
      
      # ========== NOTIFICATION PROVIDERS ==========
      # Configure at least one provider below to receive alerts
      # Feel free to leave empty or completely remove any services you don't use
      
      # ----- Pushover Configuration -----
      # Get credentials at https://pushover.net
      PUSHOVER_USER_KEY: ""       # Your Pushover user key
      PUSHOVER_API_TOKEN: ""      # Your Pushover application token
      
      # ----- Apprise Configuration -----
      # Configure any services you want to use
      
      # Discord Webhooks - Format: webhook_id/webhook_token
      # Create webhook in Discord Server Settings → Integrations
      APPRISE_DISCORD: ""
      
      # Email - Format: user:password@gmail.com
      # For Gmail, use App Password from Google Account settings
      APPRISE_EMAIL: ""
      APPRISE_EMAIL_TO: ""        # Recipient email (optional)
      
      # Telegram - Format: bottoken/ChatID
      # Create bot with @BotFather and get your Chat ID with @userinfobot
      APPRISE_TELEGRAM: ""
      
      # Slack - Format: tokenA/tokenB/tokenC
      # Create app at https://api.slack.com/apps
      APPRISE_SLACK: ""
      
      # Gotify - Format: gotify.example.com/token
      # Self-hosted push notification service
      APPRISE_GOTIFY: ""
      
      # Matrix - Format: matrixuser:pass@domain/#room
      # Open-source chat platform
      APPRISE_MATRIX: ""
      
      # MQTT - Format: mqtt://user:pass@hostname
      # For smart home integrations
      APPRISE_MQTT: ""
      
      # Custom Apprise URL - For other services
      # See: https://github.com/caronc/apprise/wiki
      APPRISE_CUSTOM: ""
      
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
| `CHANNELS_DVR_HOST` | IP address of your Channels DVR server | _Required_ |
| `CHANNELS_DVR_PORT` | Port number for your Channels DVR server | `8089` |
| `TZ` | Timezone for logs and timestamps (e.g., America/New_York) | `UTC` |
| `LOG_LEVEL` | Log verbosity level (1=Standard, 2=Verbose) | `1` |
| `LOG_RETENTION_DAYS` | Number of days to keep log files before rotation | `7` |
| `CONFIG_PATH` | Path to configuration directory in container | `/config` |
| `Alerts_Channel-Watching` | Enable/disable channel watching alerts (TRUE/FALSE) | `TRUE` |
| `CHANNEL_IMAGES` | Include channel logos in notifications (TRUE/FALSE) | `TRUE` |
| `ALERT_COOLDOWN` | Minimum seconds between duplicate alerts for same device/channel | `3600` |
| `RECONNECT_DELAY` | Seconds to wait before reconnecting after a connection failure | `5` |
| `PUSHOVER_USER_KEY` | Your Pushover user key from pushover.net | _Optional_ |
| `PUSHOVER_API_TOKEN` | Your Pushover application token | _Optional_ |
| `APPRISE_DISCORD` | Discord webhook in format: webhook_id/webhook_token | _Optional_ |
| `APPRISE_EMAIL` | Email configuration in format: user:password@server | _Optional_ |
| `APPRISE_EMAIL_TO` | Email recipient address | _Optional_ |
| `APPRISE_TELEGRAM` | Telegram configuration in format: bottoken/ChatID | _Optional_ |
| `APPRISE_SLACK` | Slack configuration in format: tokenA/tokenB/tokenC | _Optional_ |
| `APPRISE_GOTIFY` | Gotify configuration in format: gotify.example.com/token | _Optional_ |
| `APPRISE_MATRIX` | Matrix configuration in format: user:pass@domain/#room | _Optional_ |
| `APPRISE_MQTT` | MQTT configuration in format: mqtt://user:pass@hostname | _Optional_ |
| `APPRISE_CUSTOM` | Custom Apprise URL for other services | _Optional_ |

### Volume Mounts

| Container Path | Purpose |
|----------------|---------|
| `/config` | Configuration and log storage |

## 📱 Notification Example

Sample notification when TV viewing starts:

```
📺 ABC
Channel: 7
Device: Living Room
IP: 192.168.1.101
Source: HDHR
```

## 🔍 Troubleshooting

### No Notifications

1. Check container logs: `docker logs channelwatch`
2. Verify Pushover credentials are correct
3. Ensure Channels DVR server is accessible
4. Test connectivity: `docker exec channelwatch python -m channelwatch.main --test-connectivity`

### Advanced Diagnostics

The application includes several diagnostic tools:

```bash
# Test connection to Channels DVR server
docker exec channelwatch python -m channelwatch.main --test-connectivity

# Monitor event stream for 60 seconds
docker exec channelwatch python -m channelwatch.main --monitor-events 60
```

## 🔄 Upgrading

To upgrade to the latest version:

```bash
docker-compose pull
docker-compose up -d
```

## 🛠️ Project Structure

```
ChannelWatch/
├── alerts/
│   ├── __init__.py
│   ├── base.py
│   ├── channel_watching.py
│   ├── common/
│   │   ├── __init__.py
│   │   ├── alert_formatter.py
│   │   ├── cleanup_mixin.py
│   │   └── session_manager.py
├── core/
│   ├── __init__.py
│   ├── alert_manager.py
│   ├── event_monitor.py
├── helpers/
│   ├── __init__.py
│   ├── channel_info.py
│   ├── logging.py
│   ├── parsing.py
│   └── tools.py
├── notifications/
│   ├── __init__.py
│   ├── notification.py
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── apprise.py
│   │   ├── base.py
│   │   └── pushover.py
├── __init__.py
├── main.py
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 📜 License

MIT License - see the LICENSE file for details.

## 🙌 Acknowledgments

- [Channels DVR](https://getchannels.com/) for exceptional DVR software
- [Pushover](https://pushover.net/) for reliable notifications

## 🔮 Future Roadmap

Features planned for future releases:

- More alert types (recordings, errors, tuner status)
- Web UI for configuration and monitoring
- Multiple server monitoring
- Authentication for enhanced security
- Custom notification templates

## 💬 Get Help

Contributions, issues, and feature requests are welcome!

- [GitHub Discussions](https://github.com/CoderLuii/ChannelWatch/discussions)
- [GitHub Issues](https://github.com/CoderLuii/ChannelWatch/issues)
- [Docker Hub](https://hub.docker.com/r/coderluii/channelwatch)