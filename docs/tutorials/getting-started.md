# Getting started with ChannelWatch

This tutorial takes you from a clean machine to a running ChannelWatch container with one Channels DVR connected and a Channel Watching alert firing.

You will learn the normal setup model as you go: Docker Compose runs the container, then the ChannelWatch web UI configures the app.

## Prerequisites

Before you start, make sure you have:

1. Docker and Docker Compose installed on the machine that will run ChannelWatch.
2. A Channels DVR server already running on your LAN.
3. The LAN IP address or hostname of that DVR. Channels DVR usually listens on port `8089`.
4. At least one notification destination ready to configure in the ChannelWatch UI, such as Pushover, Discord, email, Telegram, Slack, Gotify, Matrix, or another Apprise URL.

This tutorial uses `/opt/channelwatch` as the example install folder. Replace it with another path if you prefer.

## Step 1: Pull the image

1. Create a working folder for the Compose file:

   ```bash
   mkdir -p /opt/channelwatch
   cd /opt/channelwatch
   ```

2. Pull the current stable ChannelWatch image:

   ```bash
   docker pull coderluii/channelwatch:latest
   ```

   Pulling first lets you catch network or Docker Hub access problems before you write any configuration. The same image name is used by the project Compose file and by the published Docker image.

## Step 2: Create the config directory

1. Create a persistent config directory:

   ```bash
   mkdir -p /opt/channelwatch/config
   ```

   ChannelWatch stores `settings.json`, logs, activity history, and per-DVR session state in `/config` inside the container. The host folder keeps that data safe when the container is replaced.

2. Do not create `settings.json` by hand for a normal fresh install.

   On first start, ChannelWatch creates the current settings file under `/config`, then the first-run wizard walks you through secure login or trusted-network no-auth mode and DVR setup. This avoids stale schema values and keeps the web UI as the source of truth for application settings.

3. Set the `TZ` environment variable in Compose if you want timestamps to use a different timezone before first start.

   For every available saved setting, see [settings reference](../reference/settings.md). For one-time container bootstrap variables, see [environment variables reference](../reference/env-vars.md).

## Step 3: Start ChannelWatch with Docker Compose

1. Create `/opt/channelwatch/docker-compose.yml`:

   ```yaml
   services:
     ChannelWatch:
       image: coderluii/channelwatch:latest
       container_name: channelwatch
       init: true
       network_mode: host
       volumes:
         - /opt/channelwatch/config:/config
       environment:
         TZ: "America/New_York"
         CHANNELWATCH_SECRET_STORAGE_KEY: "${CHANNELWATCH_SECRET_STORAGE_KEY:?set a unique value of at least 32 characters}"
       restart: unless-stopped
   ```

   Host networking matches the project Compose example. It also lets ChannelWatch reach a Channels DVR on your LAN without extra port mapping. The app listens on port `8501`, which the image exposes.
   Set `CHANNELWATCH_SECRET_STORAGE_KEY` in your shell or a local `.env` file before starting the container. Use a unique value of at least 32 characters and keep it with the rest of your deployment secrets.

2. Start the container:

   ```bash
   docker compose up -d
   ```

3. Watch the startup logs:

   ```bash
   docker logs -f channelwatch
   ```

4. In another terminal, check that the web service is responding:

   ```bash
   curl -fsS http://localhost:8501/healthz/live
   ```

   A successful response means the UI process is up. The Dockerfile uses the same `/healthz/live` endpoint for the container health check; `/api/health` remains available for the aggregate/degraded health response.

## Step 4: Open the UI and complete the first-run wizard

1. Open ChannelWatch in a browser:

   ```text
   http://your-server-ip:8501
   ```

2. Choose **Secure login** when the setup-first screen appears.

   Secure login is the recommended fresh-install path. Create the first admin username and password in the wizard.

3. Add your first Channels DVR server.

   Enter a friendly name, the DVR host or LAN IP address, and port `8089` unless your DVR uses a different port.

4. Configure at least one notification provider.

   Pick the provider you already prepared, then save the settings. Channel Watching alerts are enabled by the minimal `settings.json`, so the app is ready to notify after a DVR and provider are saved.

5. Confirm the dashboard shows the DVR as reachable.

   This gives you a quick check that ChannelWatch can talk to Channels DVR before you test an alert.

## Step 5: Verify a Channel Watching alert

1. On a device that uses your Channels DVR, start playing a live TV channel.

2. Keep the stream running for a short time.

   ChannelWatch watches the DVR event stream and sends one Channel Watching notification for the viewing session. The notification can include the channel, program, device, client IP, stream source, and current stream count, depending on your settings.

3. Check your notification destination.

   You should receive a Channel Watching alert for the stream you started.

4. Check the ChannelWatch logs if no alert arrives:

   ```bash
   docker logs --tail 100 channelwatch
   ```

   Look for DVR connection errors first, then provider delivery errors. If the UI is reachable but alerts are not firing, recheck the DVR host, notification provider settings, and whether the stream is live TV rather than recorded content.

## Step 6: What to do next

1. Add more DVRs after the first one works.

   Use the [multi-DVR how-to guide](../how-to/multi-dvr.md) when you are ready to connect additional servers.

2. Review the saved settings model.

   The [settings reference](../reference/settings.md) explains the app settings written to `/config/settings.json`.

3. Review container bootstrap options.

   The [environment variables reference](../reference/env-vars.md) explains runtime variables such as `TZ`, `PUID`, `PGID`, and one-time DVR bootstrap values.

4. Learn why the Docker setup is shaped this way.

   The [architecture explanation](../explanation/architecture.md) describes how the monitor, web UI, and `/config` volume fit together.
