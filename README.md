# <img src="https://github.com/CoderLuii/ChannelWatch/blob/main/ui/public/favicon.png?raw=true" alt="ChannelWatch" width="39" valign="bottom"> <a name="top"></a>ChannelWatch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Pulls](https://badgen.net/docker/pulls/coderluii/channelwatch?icon=docker)](https://hub.docker.com/r/coderluii/channelwatch)
[![PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://www.paypal.com/donate/?hosted_button_id=PM2UXGVSTHDNL)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg?style=flat&logo=buy-me-a-coffee)](https://buymeacoffee.com/CoderLuii)
[![Twitter Follow](https://img.shields.io/twitter/follow/CoderLuii?style=social)](https://x.com/CoderLuii)


## 📑 Table of Contents
- 📈 [Version History](#-version-history)
- 🌟 [Overview](#-overview)
- 🏛  [ Architecture](#-architecture)
- 📋 [Key Features](#-key-features)
- ⚡ [Performance](#-performance)
- 💡 [Support This Project](#-support-this-project)
- 🔧 [Prerequisites](#-prerequisites)
- 💻 [Platform Support](#-platform-support)
- 🚀 [Quick Setup](#-quick-setup)
- 🔨 [Configuration Options](#-configuration-options)
- 📱 [Notification Examples](#-notification-examples)
- 🔍 [Troubleshooting](#-troubleshooting)
- 🔄 [Upgrading](#-upgrading)
- 📁 [Project Structure](#-project-structure)
- 📜 [License](#-license)
- 🙏 [Acknowledgments](#-acknowledgments)
- 🗺 [Future Roadmap](#-future-roadmap)
- 💬 [Get Help](#-get-help)

## 📈 Version History

- **v0.8.2** - Adds a maintained Unraid install template
- **v0.8.1** - Adds Docker `TZ` support and publishes Docker Hub and GHCR images from release tags
- **v0.8** - Library updates for the UI, backend, and Docker build, plus a PostCSS update for the web build
- **v0.7** - Notification enhancements with improved email and Discord integration, UI documentation improvements, and bug fixes for image selection
- **v0.6** - Complete project restructuring with modern web UI, simplified configuration (no environment variables), responsive dashboard, and enhanced error recovery
- **v0.5** - Added Recording-Events alerts for monitoring the entire recording lifecycle (scheduled, started, completed, cancelled, stopped), enhanced stream count integration, improved time formatting
- **v0.4** - Expanded alert types with VOD-Watching and Disk-Space monitoring, enhanced Channel-Watching with program details and images, improved session tracking
- **v0.3** - Complete architecture overhaul with real-time event monitoring, multi-provider notifications, session tracking, and enhanced stability
- **v0.2** - Security updates addressing Python dependency vulnerabilities and adding Docker supply chain attestations
- **v0.1** - Initial release with core monitoring and notification features


<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🌟 Overview

ChannelWatch is a comprehensive monitoring solution with a modern web interface that tracks Channels DVR activity and sends real-time notifications. The system features:

- 🌐 **Modern Web Dashboard** - Responsive UI with system status monitoring and configuration
- 📱 **Real-time viewing alerts** - Get instant notifications when channels are being watched
- 📺 **Content monitoring** - See exactly what's being played on your Channels DVR
- 🎬 **VOD tracking** - Know when recorded content or DVR libraries are accessed
- 🔴 **Recording lifecycle alerts** - Track when recordings are scheduled, start, complete, or are cancelled
- 💾 **System monitoring** - Track disk space usage and receive alerts when space runs low
- 🔔 **Multi-device awareness** - Track viewing across all your connected devices and clients
- 🏠 **Home automation integration** - Use alerts as triggers for smart home routines

The system provides comprehensive information with a simple setup process:
- Web-based configuration with no environment variables needed
- Rich media notifications with detailed metadata
- Device identification and stream tracking
- Technical details with beautiful visuals
- Fully customizable notification content

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🏛 Architecture

ChannelWatch follows a modern, component-based architecture:

- **Core Backend**: Monitors the Channels DVR event stream and processes alerts
- **Web UI**: Provides a responsive dashboard for configuration and monitoring
- **Alert System**: Processes events to determine when to send notifications
- **Notification System**: Handles sending notifications through various providers
- **Configuration System**: Web-based settings management with persistent storage
- **Extension Framework**: Makes it easy to add new alert types and notification providers

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 📋 Key Features

- 🌐 **Modern Web Interface** with:
  - Responsive dashboard for desktop and mobile
  - Real-time system status monitoring
  - Visual disk space and stream tracking
  - Upcoming recordings display
  - Quick access diagnostic tools
- ⚙️ **Web-based Configuration** with:
  - Intuitive settings management
  - No environment variables required
  - Persistent configuration storage
  - Real-time validation and feedback
- 🔍 **Real-time monitoring** of Channels DVR event stream
- 📲 **Multi-provider notifications** via:
  - Pushover for simple push notifications
  - Apprise for Discord, Slack, Email, Telegram, and more
- 📺 **Live TV alerts** with:
  - Channel name and number (including decimal subchannels like 13.1)
  - Program title and description
  - Device name and IP address
  - Stream source and quality information
  - Total stream count across your system
- 🎬 **VOD/Recording Playback alerts** with:
  - Title, episode, and duration information
  - Playback progress tracking
  - Cast, rating, and genres
  - Smart device detection
  - Single notification per viewing session (prevents alert fatigue)
  - Support for both standard and newer file patterns
- 🔴 **Recording Event alerts** with:
  - Lifecycle tracking (scheduled, started, completed, cancelled)
  - Detailed program information (title, description, duration, channel)
  - Status indicators (📅, 🔴, ✅, ⏹️)
- 🖼️ **Rich visual alerts** with:
  - Configurable image source (channel logo or program image)
  - High-quality thumbnails for instant recognition
- 💾 **System monitoring** with:
  - Disk space alerts when recording space runs low
  - Configurable thresholds (percentage and absolute GB)
  - Visual dashboard representation of system status
- 🧹 **Automatic session tracking and management**

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## ⚡ Performance

ChannelWatch continues to be lightweight and efficient despite the addition of a full web UI:

- Minimal CPU usage (<2% on most systems)
- Modest memory footprint (~50MB RAM)
- Compact Docker image size (~150MB)
- Quick startup time (<5 seconds)
- Responsive web interface even on low-powered devices
- Efficient background processing with minimal resource contention

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 💡 Support This Project

If you find ChannelWatch helpful, consider supporting its development:

- [GitHub Sponsors](https://github.com/sponsors/CoderLuii)
- [PayPal](https://www.paypal.com/donate/?hosted_button_id=PM2UXGVSTHDNL)
- [Buy Me a Coffee](https://buymeacoffee.com/CoderLuii)

Follow me on X:
- [Twitter/X](https://x.com/CoderLuii)


<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🔧 Prerequisites

- Docker and Docker Compose
- Channels DVR server with accessible API
- Pushover and/or Apprise account/configuration (Requires at least one provider configured for notifications)

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 💻 Platform Support

ChannelWatch is available as a multi-platform Docker image, supporting:

- `linux/amd64`: Standard 64-bit x86 servers and PCs
- `linux/arm64`: Modern ARM devices (Raspberry Pi 4, Apple M1/M2 Macs)

The correct image will be automatically selected for your hardware when using `docker pull coderluii/channelwatch:latest`.

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🚀 Quick Setup

### 1. Docker Compose Configuration

Create a `docker-compose.yml` file:

```yaml
services:
  ChannelWatch:
    image: coderluii/channelwatch:latest
    container_name: channelwatch
    network_mode: host
    environment:
      TZ: America/Los_Angeles
    volumes:
      # Path to store configuration, logs, and settings
      - /your/local/path:/config
    restart: unless-stopped
```

> **Note:** 
> - All configuration is now done through the web UI at `http://your-server-ip:8501`
> - `TZ` is optional. Change it to your local timezone, for example `America/New_York`.
> - For bridge networking, replace `network_mode: host` with:
>   ```yaml
>   network_mode: bridge
>   ports:
>     - "8501:8501"  # Or replace 8501 on the left with your desired port
>   ```

### Unraid Template

A maintained Unraid user template is available at
[`deploy/unraid/channelwatch.xml`](deploy/unraid/channelwatch.xml).

Copy the template to `/boot/config/plugins/dockerMan/templates-user/` on your
Unraid flash device, then go to Docker > Add Container and select
`ChannelWatch`. Change `TZ` to your local timezone before starting the
container. ChannelWatch is still configured through the web UI at port `8501`.

### 2. Start the Container

```bash
docker-compose up -d
```

### 3. Monitor Logs

```bash
docker logs -f channelwatch
```

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🔨 Configuration Options

Configuration is managed through the web UI at `http://your-server-ip:8501`

### Core Settings

| Setting | Description |
|---------|-------------|
| Channels DVR Host | Server IP or hostname |
| Channels DVR Port | Server port (default: 8089) |
| Timezone | Local timezone for timestamps |
| Log Level | Standard or Verbose logging |
| Log Retention | Auto-cleanup period in days |

You can also set the container timezone with Docker's standard `TZ` environment
variable. When `TZ` is set, ChannelWatch uses it for the app timezone and keeps
the web UI setting in sync.

### Alert Types

| Alert | Description |
|-------|-------------|
| Channel Watching | Live TV viewing notifications |
| VOD Watching | Recorded content playback alerts |
| Recording Events | Track recording lifecycle |
| Stream Counting | Show total active streams |

### Channel Watching Options

| Option | Description |
|--------|-------------|
| Image Source | Channel Logo or Program Image |
| Show Channel Name | Display channel name |
| Show Channel Number | Display channel number |
| Show Program Name | Display program title |
| Show Device Name | Display device name |
| Show Device IP | Display device IP address |
| Show Stream Source | Display stream source |

### VOD Watching Options

| Option | Description |
|--------|-------------|
| Show Title | Display content title |
| Show Episode Title | Display episode title for TV shows |
| Show Summary | Display content summary/description |
| Show Content Image | Display thumbnail image |
| Show Duration | Display content duration |
| Show Progress | Display playback progress |
| Show Rating | Display content rating |
| Show Genres | Display content genres |
| Show Cast | Display cast members |
| Show Device Name | Display device name |
| Show Device IP | Display device IP address |

### Recording Events Options

| Option | Description |
|--------|-------------|
| Scheduled Events | Show alerts for scheduled recordings |
| Started Events | Show alerts when recordings start |
| Completed Events | Show alerts when recordings complete |
| Cancelled Events | Show alerts when recordings are cancelled |
| Show Program Name | Display program name |
| Show Description | Display program description |
| Show Duration | Display recording duration |
| Show Channel Name | Display channel name |
| Show Channel Number | Display channel number |
| Show Recording Type | Display if recording is scheduled or manual |

### Notification Providers

| Provider | Description | Config Needed |
|----------|-------------|---------------|
| Pushover | Mobile/Desktop notifications | User Key, API Token |
| Discord | Chat channel notifications | Webhook URL |
| Telegram | Chat messaging | Bot Token/Chat ID |
| Email | Standard email | SMTP Settings |
| Slack | Chat messaging | Webhook URL (token format) |
| Gotify | Self-hosted notifications | Server URL & Token |
| Matrix | Decentralized chat | Room/User credentials |
| Custom | Any Apprise-supported service | URL |

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 📱 Notification Examples

### Channel Watching Alert
```
📺 ABC
Channel: 7
Program: Good Morning America
Device: Living Room
IP: 192.168.1.101
Source: HDHR
```

### VOD Watching Alert
```
🎬 Crank: High Voltage (2009)
Duration: 58m 46s / 1h 42m 11s
Device Name: Living Room
Device IP: 192.168.1.100

Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.

Rating: R · Genres: Action, Thriller
Cast: Jason Statham, Amy Smart, Dwight Yoakam
```

### Disk Space Alert
```
⚠️ Low Disk Space Warning
Free Space: 200.59 GB / 1.82 TB (10.8%)
Used Space: 1.62 TB
DVR Path: /shares/DVR
```

### Recording Events Examples

```
📺 ACTION NETWORK
Channel: 137
Status: 📅 Scheduled
Program: Batman (1989)
-----------------------
Scheduled: Today at 8:54 AM EDT
Duration:  2 hours 16 minutes

Caped Crusader (Michael Keaton) saves Gotham City from the Joker (Jack Nicholson).
```

```
📺 MOVIE CHANNEL
Channel: 129
Status: 🔴 Recording (Manual)
Program: Crank: High Voltage (2009)
-----------------------
Recording: 8:49 AM EDT
Program:   8:48 AM EDT
Duration:  1 hour 42 minutes
Total Streams: 1

Chev Chelios (Jason Statham) seeks revenge after someone steals his nearly indestructible heart.
```

```
📺 MOVIE CHANNEL 
Channel: 129
Status: ✅ Completed
Program: Pet Sematary (1989)
-----------------------
Duration: 1 hour 54 minutes
Total Streams: 1

A doctor (Dale Midkiff) and his family move to a town near an ancient Indian burial ground.
```

```
📺 SCI-FI CHANNEL
Channel: 152
Status: ⏹️ Stopped
Program: Pandorum (2009)
-----------------------
Duration: 20 minutes
Total Streams: 1

Astronauts awake to a terrifying reality aboard a seemingly abandoned spaceship.
```

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🔍 Troubleshooting

### Diagnostics & Testing

#### Web UI (Recommended)
Access diagnostics tools at `http://your-server-ip:8501` and navigate to the "Diagnostics" tab:

- **System Status** - Overview of service health and connectivity
- **Connection Tests** - Verify connectivity to Channels DVR
- **API Tests** - Check endpoint functionality
- **Alert Tests** - Send test notifications for each alert type

#### Command Line (Advanced)
For automation or headless troubleshooting:

```bash
# Test connectivity
docker exec -it channelwatch python -m channelwatch.main --test-connectivity

# Test API endpoints
docker exec -it channelwatch python -m channelwatch.main --test-api

# Test individual alert types
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_CHANNEL_WATCHING
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_VOD_WATCHING
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_DISK_SPACE
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_RECORDING_EVENTS

# Monitor event stream for 60 seconds
docker exec -it channelwatch python -m channelwatch.main --monitor-events 60
```

### Common Issues

#### No Notifications
1. Check logs: `docker logs channelwatch`
2. Verify notification provider credentials are correct
3. Ensure Channels DVR server is accessible (test connection)
4. Check if notification service is operational (test with diagnostic panel)
5. Confirm alerts are properly configured and enabled

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🔄 Upgrading

To upgrade to the latest version:

```bash
docker-compose pull
docker-compose up -d
```

## 📁 Project Structure

```
ChannelWatch/
├── core/                       # Core backend logic (Python)
│   ├── docker-entrypoint.sh    #   Container startup script
│   ├── alerts/                 #   Alert handling modules (one per alert type)
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── channel_watching.py
│   │   ├── disk_space.py
│   │   ├── recording_events.py
│   │   ├── vod_watching.py
│   │   └── common/             #   Shared alert utilities (formatting, sessions)
│   │       ├── __init__.py
│   │       ├── alert_formatter.py
│   │       ├── cleanup_mixin.py
│   │       ├── session_manager.py
│   │       └── stream_tracker.py
│   ├── engine/                 #   Event processing engine & orchestration
│   │   ├── __init__.py
│   │   ├── alert_manager.py
│   │   └── event_monitor.py
│   ├── helpers/                #   Backend utilities & data providers (config, logging, API interactions)
│   │   ├── __init__.py
│   │   ├── activity_recorder.py
│   │   ├── channel_info.py
│   │   ├── config.py
│   │   ├── initialize.py
│   │   ├── job_info.py
│   │   ├── logging.py
│   │   ├── parsing.py
│   │   ├── program_info.py
│   │   ├── recording_info.py
│   │   ├── tools.py
│   │   ├── type_utils.py
│   │   └── vod_info.py
│   ├── notifications/          #   Notification sending system
│   │   ├── __init__.py
│   │   ├── notification.py
│   │   └── providers/         #   Specific notification services (Pushover, Apprise)
│   │       ├── __init__.py
│   │       ├── apprise.py
│   │       ├── base.py
│   │       └── pushover.py
│   ├── test/                   #   Backend test framework & simulations
│   │   ├── __init__.py
│   │   ├── alerts/             #   Alert simulation tests
│   │   │   ├── __init__.py
│   │   │   ├── test_channel_watching.py
│   │   │   ├── test_disk_space.py
│   │   │   ├── test_recording_events.py
│   │   │   └── test_vod_watching.py
│   │   ├── connectivity/       #   Server connection tests
│   │   │   ├── __init__.py
│   │   │   └── test_server.py
│   │   └── utils/              #   Test helper utilities
│   │       ├── __init__.py
│   │       └── test_utils.py
│   ├── main.py                 #   Backend entry point (CLI/testing)
│   └── __init__.py             #   Backend package marker & version info
├── ui/                         # Web Interface (Next.js/React frontend, FastAPI backend)
│   ├── app/                    #   Next.js frontend application root
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   └── globals.css
│   ├── backend/                #   FastAPI backend serving the UI API
│   │   ├── main.py
│   │   ├── config.py
│   │   └── schemas.py
│   ├── components/             #   React UI components
│   │   ├── base/               #     shadcn/ui base components
│   │   │   ├── alert.tsx
│   │   │   ├── badge.tsx
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── checkbox.tsx
│   │   │   ├── command.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── dropdown-menu.tsx
│   │   │   ├── form.tsx
│   │   │   ├── input.tsx
│   │   │   ├── label.tsx
│   │   │   ├── popover.tsx
│   │   │   ├── progress.tsx
│   │   │   ├── select.tsx
│   │   │   ├── separator.tsx
│   │   │   ├── switch.tsx
│   │   │   ├── tabs.tsx
│   │   │   ├── toast.tsx
│   │   │   ├── toaster.tsx
│   │   │   └── tooltip.tsx
│   │   ├── dashboard.tsx
│   │   ├── header.tsx
│   │   ├── settings-form.tsx
│   │   ├── sidebar.tsx
│   │   ├── diagnostics-panel.tsx
│   │   ├── about-section.tsx
│   │   ├── mode-toggle.tsx
│   │   ├── status-overview.tsx
│   │   └── theme-provider.tsx
│   ├── hooks/                  #   Custom React hooks
│   │   └── use-toast.ts
│   ├── lib/                    #   Frontend helper libraries & utilities
│   │   ├── api.ts
│   │   ├── types.ts
│   │   └── utils.ts
│   ├── public/                 #   Static assets (images, favicon)
│   │   ├── images/             #     Image assets
│   │   │   ├── channelwatch-logo.png
│   │   │   ├── background-bio.webp
│   │   │   └── coder-luii.png
│   │   ├── favicon.png
│   │   └── og-image.png
│   ├── types/                  #   Custom TypeScript definitions
│   │   └── global.d.ts
│   ├── components.json
│   ├── next-env.d.ts
│   ├── next.config.mjs
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── postcss.config.mjs
│   ├── tailwind.config.ts
│   └── tsconfig.json
├── Dockerfile                  # Docker container build instructions
├── docker-compose.yml          # Example Docker deployment file
├── supervisord.conf            # Process manager (supervisor) configuration
├── requirements.txt            # Python dependency list (backend)
└── README.md
```

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 📜 License

MIT License - see the LICENSE file for details.

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🙏 Acknowledgments

- [Channels DVR](https://getchannels.com/) for exceptional DVR software
- [Pushover](https://pushover.net/) and [Apprise](https://github.com/caronc/apprise) for powering the notification capabilities
- [Shadcn/ui](https://ui.shadcn.com/) for the beautiful and responsive UI components

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🗺 Future Roadmap

ChannelWatch will continue to evolve with these planned enhancements:

### 🔔 Advanced Notification System
- Custom notification templates with variable support
- Error alerting for system and connection issues

### 🛠️ Enhanced Diagnostics
- Live container log feed in diagnostic page

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 💬 Get Help

Contributions, issues, and feature requests are welcome!

- [GitHub Discussions](https://github.com/CoderLuii/ChannelWatch/discussions)
- [GitHub Issues](https://github.com/CoderLuii/ChannelWatch/issues)
- [Docker Hub](https://hub.docker.com/r/coderluii/channelwatch)

<p align="right">
  <a href="#top">↑ back to top</a>
</p>
