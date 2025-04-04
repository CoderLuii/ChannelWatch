# <a name="top"></a>ChannelWatch 📺🔔

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker Pulls](https://badgen.net/docker/pulls/coderluii/channelwatch?icon=docker)](https://hub.docker.com/r/coderluii/channelwatch)
[![PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://www.paypal.com/donate/?hosted_button_id=PM2UXGVSTHDNL)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow.svg?style=flat&logo=buy-me-a-coffee)](https://buymeacoffee.com/CoderLuii)
[![Twitter Follow](https://img.shields.io/twitter/follow/CoderLuii?style=social)](https://x.com/CoderLuii)


## 📑 Table of Contents
- 📈 [Version History](#-version-history)
- 🌟 [Overview](#-overview)
- 🏛 [Architecture](#-architecture)
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

- **v0.5 (April 3, 2024)** - Recording Events, Variable Standardization & Enhanced IP/Device Extraction
  - **New Recording Events Alert**:
    - 🔴 Complete DVR recording lifecycle tracking with status indicators 

              📅 Scheduled, 🔴 Recording, ✅ Completed, ⏹️ Cancelled
    - Detailed notifications with program information and images
    - Consistent stream counting across viewing and recording activities
  - **Variable Standardization**:
    - Organized prefix system for all settings (`ALERT_*`, `CW_*`, `RD_*`, `VOD_*`, `DS_*`)
    - Clean, consistent configuration options across all alert types
  - **IP/Device Improvements**:
    - Better device name and IP address detection across log patterns
    - IP address caching for more reliable notifications
    - Enhanced detection for both Channel and VOD alerts
  - **Alert Refinements**:
    - Smarter VOD session handling for content switching
    - Improved filtering for various event formats
    - Clearer labeling in notification messages
  - **Bug Fixes**:
    - Enhanced image handling for channel logos and program thumbnails
    - Improved stream tracking reliability
    - Optimized cache performance

- **v0.4 (March 30, 2024)** - Expanded alert types and enhanced capabilities
  - **New Alert Types**:
    - 🎬 **VOD-Watching Alert**: Track when recorded/VOD content is being watched
      - Rich metadata display with title, episode, progress, duration
      - Device identification and tracking
      - Support for both 6-file and 7-file patterns
      - Single notification per viewing session
      - Detailed logging of viewing activity
    - 💾 **Disk-Space Monitoring**: Get alerted when recording space runs low
      - Configurable thresholds (percentage and absolute GB)
      - Detailed space usage information
  - **Channel-Watching Improvements**:
    - 📺 Program Titles - See exactly what's playing on each channel directly in notifications
    - 🖼️ Program Images - Choose between channel logos or actual program images in alerts
    - 🎭 Enhanced Metadata - Richer program information with improved formatting
    - 🔢 Decimal Channel Support - Full support for subchannels (13.1, etc.) for broadcast/OTA channels
    - 📊 Total Streams Counter - See how many concurrent streams are active across your system
    - 📱 Stream Source Identification - Cleaner display of M3U, TVE, and Tuner sources
  - **System Improvements**:
    - ⚡ Performance Optimizations - Preloaded cache at startup for faster operation
    - ⚙️ Expanded Configuration - Control exactly what appears in your notifications
    - 🔄 Cache Management - Configurable TTLs and improved validation
  - **Bug Fixes**:
    - Fixed IP address extraction from various event formats
    - Improved timestamp and duration formatting
    - Enhanced session tracking reliability

- **v0.3 (March 23, 2024)** - Major architectural overhaul
  - Complete rewrite with new architecture for better reliability and expandability
  - Real-time event monitoring through direct connection to Channels DVR event stream
  - Multi-service notifications via Apprise integration (Discord, Slack, Telegram, Email)
  - Added channel logos in notifications for visual identification
  - Enhanced session tracking with automatic cleanup
  - Improved error handling and diagnostic capabilities

- **v0.2 (March 20, 2024)** - Security updates
  - Addressed vulnerabilities in base dependencies
  - Minor bug fixes and stability improvements

- **v0.1 (March 19, 2024)** - Initial release
  - Basic Channel-Watching alerts
  - Pushover notification support
  - Log file monitoring

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🌟 Overview

ChannelWatch is a lightweight Docker container that monitors Channels DVR events and sends real-time push notifications when TV viewing begins or recorded content is played. Perfect for:

- 📱 **Real-time viewing alerts** - Get instant notifications when channels are being watched
- 📺 **Content monitoring** - See exactly what's being played on your Channels DVR
- 🎬 **VOD tracking** - Know when recorded content or DVR libraries are accessed
- 🔴 **Recording lifecycle alerts** - Track when recordings are scheduled, start, complete, or are cancelled
- 🔔 **Multi-device awareness** - Track viewing across all your connected devices and clients
- 🏠 **Home automation integration** - Use alerts as triggers for smart home routines

The notifications provide detailed information with minimal setup and resource usage:
- Rich media details (channel/program info, VOD metadata)
- Device identification (name and IP address)
- Technical details (stream source, quality)
- Beautiful visuals (channel logos or program images)
- Customizable notification content

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🏛 Architecture

ChannelWatch follows a modular, service-oriented architecture:

- **Event System**: Monitors the Channels DVR event stream
- **Alert System**: Processes events to determine when to send notifications
- **Notification System**: Handles sending notifications through various providers
- **Configuration System**: Centralizes all configuration with environment variables
- **Extension Framework**: Makes it easy to add new alert types and notification providers

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 📋 Key Features

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
- ⚙️ **Highly configurable** settings to control exactly what appears in your notifications
- 🧹 **Automatic session tracking and management**
- 🚀 **Optimized performance** with:
  - Low resource usage (<1% CPU on most systems)
  - Small memory footprint (~30MB RAM)
  - Intelligent caching with configurable TTLs
  - Quick startup time

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## ⚡ Performance

ChannelWatch is designed to be lightweight and efficient:

- Minimal CPU usage (<1% on most systems)
- Low memory footprint (~30MB RAM)
- Small Docker image size (~100MB)
- Quick startup time (<5 seconds)

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
- `linux/arm/v7`: Older ARM devices (Raspberry Pi 3 and earlier)

The correct image will be automatically selected for your hardware when using `docker pull coderluii/channelwatch:latest`.

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

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
      
      # ========== ALERT MODULES ==========
      # Enable/disable specific alert types
      # Set to TRUE to enable, FALSE to disable
      ALERT_CHANNEL_WATCHING: TRUE   # Live TV watching alerts
      ALERT_VOD_WATCHING: TRUE       # DVR/recorded content alerts
      ALERT_DISK_SPACE: TRUE         # Monitor and alert on low disk space
      ALERT_RECORDING_EVENTS: TRUE   # Recording events (scheduled, started, cancelled, completed)
      
      # ========== STREAM COUNTING ==========
      # Affects BOTH channel watching AND recording alerts
      # Set TRUE to count all streams together, FALSE to disable count
      STREAM_COUNT: TRUE
      
      # ========== CHANNEL-WATCHING ALERT SETTINGS ==========
      # Control what appears in channel watching notifications
      # Set to TRUE to show, FALSE to hide
      CW_CHANNEL_NAME: TRUE       # Show channel name in notifications
      CW_CHANNEL_NUMBER: TRUE     # Show channel number in notifications
      CW_PROGRAM_NAME: TRUE       # Show program name in notifications
      CW_DEVICE_NAME: TRUE        # Show device name in notifications
      CW_DEVICE_IP: TRUE          # Show device IP address in notifications
      CW_STREAM_SOURCE: TRUE      # Show stream source in notifications
      CW_IMAGE_SOURCE: PROGRAM    # Set to "CHANNEL" or "PROGRAM" to determine which image to show
      
      # ========== RECORDING EVENTS ALERT SETTINGS ==========
      # Alert types - set to TRUE to enable, FALSE to disable
      RD_ALERT_SCHEDULED: TRUE    # Show alerts when recordings are scheduled
      RD_ALERT_STARTED: TRUE      # Show alerts when recordings start
      RD_ALERT_COMPLETED: TRUE    # Show alerts when recordings complete
      RD_ALERT_CANCELLED: TRUE    # Show alerts when recordings are cancelled
      
      # Content settings - set to TRUE to show, FALSE to hide in recording alerts
      RD_PROGRAM_NAME: TRUE       # Show program name
      RD_PROGRAM_DESC: TRUE       # Show program description
      RD_DURATION: TRUE           # Show duration
      RD_CHANNEL_NAME: TRUE       # Show channel name
      RD_CHANNEL_NUMBER: TRUE     # Show channel number
      RD_TYPE: TRUE               # Show if recording is scheduled or manual
      
      # ========== VOD WATCHING ALERT SETTINGS ==========
      # Control what appears in VOD/DVR content notifications
      VOD_TITLE: TRUE             # Show content title
      VOD_EPISODE_TITLE: TRUE     # Show episode title (for TV shows)
      VOD_SUMMARY: TRUE           # Show content summary
      VOD_DURATION: TRUE          # Show content duration
      VOD_PROGRESS: TRUE          # Show current playback progress
      VOD_IMAGE: TRUE             # Show content image
      VOD_RATING: TRUE            # Show content rating
      VOD_GENRES: TRUE            # Show content genres
      VOD_CAST: TRUE              # Show cast members
      VOD_DEVICE_NAME: TRUE       # Show device name
      VOD_DEVICE_IP: TRUE         # Show device IP
      
      # ========== CACHE SETTINGS (TTL in seconds) ==========
      CHANNEL_CACHE_TTL: 86400    # Refresh channel data every 24 hours
      PROGRAM_CACHE_TTL: 86400    # Refresh program data every 24 hours
      JOB_CACHE_TTL: 3600         # Refresh job data every hour
      VOD_CACHE_TTL: 86400        # Refresh VOD data every 24 hours
      
      # ========== DISK SPACE MONITORING ==========
      DS_THRESHOLD_PERCENT: 10  # Alert when free space falls below 10%
      DS_THRESHOLD_GB: 50       # Alert when free space falls below 50GB
      
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

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🔨 Configuration Options

### Environment Variables

#### Core Settings
| Variable | Description | Default |
|----------|-------------|---------|
| `CHANNELS_DVR_HOST` | IP address of your Channels DVR server | _Required_ |
| `CHANNELS_DVR_PORT` | Port number for your Channels DVR server | `8089` |
| `TZ` | Timezone for logs and timestamps (e.g., America/New_York) | `UTC` |

#### Alert Types
| Variable | Description | Default |
|----------|-------------|---------|
| `ALERT_CHANNEL_WATCHING` | Enable/disable channel watching alerts (TRUE/FALSE) | `TRUE` |
| `ALERT_VOD_WATCHING` | Enable/disable VOD/DVR content alerts (TRUE/FALSE) | `FALSE` |
| `ALERT_DISK_SPACE` | Enable/disable disk space monitoring alerts (TRUE/FALSE) | `FALSE` |
| `ALERT_RECORDING_EVENTS` | Enable/disable recording events alerts (TRUE/FALSE) | `FALSE` |

#### Logging Options
| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Log verbosity level (1=Standard, 2=Verbose) | `1` |
| `LOG_RETENTION_DAYS` | Number of days to keep log files before rotation | `7` |
| `CONFIG_PATH` | Path to configuration directory in container | `/config` |

#### Stream Counting
| Variable | Description | Default |
|----------|-------------|---------|
| `STREAM_COUNT` | Enable/disable stream count in notifications (TRUE/FALSE) | `TRUE` |

#### Channel-Watching Alert Settings
| Variable | Description | Default |
|----------|-------------|---------|
| `CW_CHANNEL_NAME` | Show channel name in notifications (TRUE/FALSE) | `TRUE` |
| `CW_CHANNEL_NUMBER` | Show channel number in notifications (TRUE/FALSE) | `TRUE` |
| `CW_PROGRAM_NAME` | Show program title in notifications (TRUE/FALSE) | `TRUE` |
| `CW_DEVICE_NAME` | Show device name in notifications (TRUE/FALSE) | `TRUE` |
| `CW_DEVICE_IP` | Show device IP address in notifications (TRUE/FALSE) | `TRUE` |
| `CW_STREAM_SOURCE` | Show stream source in notifications (TRUE/FALSE) | `TRUE` |
| `CW_IMAGE_SOURCE` | Which image to use (CHANNEL or PROGRAM) | `CHANNEL` |

#### Recording Events Alert Settings
| Variable | Description | Default |
|----------|-------------|---------|
| `RD_ALERT_SCHEDULED` | Enable/disable scheduled recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_ALERT_STARTED` | Enable/disable started recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_ALERT_COMPLETED` | Enable/disable completed recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_ALERT_CANCELLED` | Enable/disable cancelled recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_PROGRAM_NAME` | Show program name in recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_PROGRAM_DESC` | Show program description in recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_DURATION` | Show duration in recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_CHANNEL_NAME` | Show channel name in recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_CHANNEL_NUMBER` | Show channel number in recording alerts (TRUE/FALSE) | `TRUE` |
| `RD_TYPE` | Show recording type in recording alerts (TRUE/FALSE) | `TRUE` |

#### VOD-Watching Alert Settings
| Variable | Description | Default |
|----------|-------------|---------|
| `VOD_TITLE` | Show content title in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_EPISODE_TITLE` | Show episode title in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_SUMMARY` | Show content summary in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_DURATION` | Show content duration in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_PROGRESS` | Show playback progress in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_IMAGE` | Show content image in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_RATING` | Show content rating in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_GENRES` | Show content genres in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_CAST` | Show cast members in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_DEVICE_NAME` | Show device name in VOD notifications (TRUE/FALSE) | `TRUE` |
| `VOD_DEVICE_IP` | Show device IP in VOD notifications (TRUE/FALSE) | `TRUE` |

#### Cache Settings
| Variable | Description | Default |
|----------|-------------|---------|
| `CHANNEL_CACHE_TTL` | Channel data cache duration in seconds | `86400` |
| `PROGRAM_CACHE_TTL` | Program data cache duration in seconds | `86400` |
| `JOB_CACHE_TTL` | Job data cache duration in seconds | `3600` |
| `VOD_CACHE_TTL` | VOD metadata cache duration in seconds | `86400` |

#### Disk Space Settings
| Variable | Description | Default |
|----------|-------------|---------|
| `DS_THRESHOLD_PERCENT` | Percentage threshold for low disk space alerts | `10` |
| `DS_THRESHOLD_GB` | Absolute threshold in GB for low disk space alerts | `50` |

#### Notification Provider Settings
| Variable | Description | Default |
|----------|-------------|---------|
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

### No Notifications

1. Check container logs: `docker logs channelwatch`
2. Verify notification provider credentials are correct
3. Ensure Channels DVR server is accessible
4. Test connectivity: `docker exec -it channelwatch python -m channelwatch.main --test-connectivity`

### Testing Individual Alerts

You can test each alert type with these commands:

```bash
# Test live TV watching alerts
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_CHANNEL_WATCHING

# Test DVR/recorded content playback alerts
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_VOD_WATCHING

# Test disk space monitoring alerts
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_DISK_SPACE

# Test recording events alerts (scheduled, started, completed, cancelled)
docker exec -it channelwatch python -m channelwatch.main --test-alert ALERT_RECORDING_EVENTS
```

### Advanced Diagnostics

The application includes several diagnostic tools:

```bash
# Test connection to Channels DVR server
docker exec -it channelwatch python -m channelwatch.main --test-connectivity

# Test API endpoints for connectivity
docker exec -it channelwatch python -m channelwatch.main --test-api

# Monitor event stream for 60 seconds
docker exec -it channelwatch python -m channelwatch.main --monitor-events 60
```

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🔄 Upgrading

To upgrade to the latest version:

```bash
docker-compose pull
docker-compose up -d
```

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 📁 Project Structure

```
ChannelWatch/
├── .github/
│   ├── FUNDING.yml
│   └── ISSUE_TEMPLATE/          
├── alerts/
│   ├── __init__.py
│   ├── base.py
│   ├── channel_watching.py
│   ├── disk_space.py
│   ├── recording_events.py
│   ├── vod_watching.py
│   └── common/                 
│       ├── __init__.py
│       ├── alert_formatter.py
│       ├── cleanup_mixin.py
│       ├── session_manager.py
│       └── stream_tracker.py
├── core/
│   ├── __init__.py
│   ├── alert_manager.py
│   └── event_monitor.py
├── helpers/
│   ├── __init__.py
│   ├── channel_info.py
│   ├── initialize.py
│   ├── job_info.py            
│   ├── logging.py
│   ├── parsing.py
│   ├── program_info.py
│   ├── recording_info.py       
│   ├── tools.py
│   └── vod_info.py
├── notifications/
│   ├── __init__.py
│   ├── notification.py
│   └── providers/             
│       ├── __init__.py
│       ├── apprise.py
│       ├── base.py
│       └── pushover.py
├── test/
│   ├── __init__.py
│   ├── alerts/
│   │   ├── __init__.py
│   │   ├── test_channel_watching.py
│   │   ├── test_disk_space.py
│   │   └── test_vod_watching.py
│   │   └── test_recording_events.py
│   ├── connectivity/
│   │   ├── __init__.py
│   │   └── test_server.py
│   └── utils/
│       ├── __init__.py
│       └── test_utils.py
├── __init__.py
├── Dockerfile
├── docker-compose.yml
├── main.py
├── README.md
├── requirements.txt
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

<p align="right">
  <a href="#top">↑ back to top</a>
</p>

## 🗺 Future Roadmap

The development roadmap for ChannelWatch focuses on enhanced customization, monitoring, and user experience:

- **Advanced Notification System**
  - Custom notification templates with variable support
  - Priority-based alert routing (critical vs. informational)
  - Notification batching options to reduce alert fatigue

- **Intelligent Filtering**
  - Network-aware filtering (internal vs. external IP ranges)
  - Device-specific alert rules and exceptions
  - Time-based alert profiles (day/night, weekday/weekend)

- **Enhanced Monitoring**
  - System error detection and alerting
  - Channels DVR health monitoring
  - Tuner status and signal quality tracking

- **Management Interface**
  - Web-based configuration dashboard
  - Real-time monitoring and statistics
  - Mobile-responsive design

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
