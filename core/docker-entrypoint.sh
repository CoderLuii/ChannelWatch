#!/bin/sh
# CONFIGURATION
CONFIG_DIR="/config"
SETTINGS_FILE="${CONFIG_DIR}/settings.json"
DEFAULT_TZ="America/Los_Angeles"

# USER SETUP
APP_UID=${PUID:-1000}
APP_GID=${PGID:-1000}

# PERMISSIONS
mkdir -p "$CONFIG_DIR"
chown -R "$APP_UID:$APP_GID" "$CONFIG_DIR" || echo "Warning: Failed to chown $CONFIG_DIR"
chown -R "$APP_UID:$APP_GID" /app || echo "Warning: Failed to chown /app"
chmod 755 "$CONFIG_DIR"

# DEFAULT SETTINGS
if [ ! -f "$SETTINGS_FILE" ]; then
    echo "Settings file not found. Creating default settings.json"
    su-exec "$APP_UID:$APP_GID" sh -c "cat > $SETTINGS_FILE << EOF
{
    \"channels_dvr_host\": null,
    \"channels_dvr_port\": 8089,
    \"tz\": \"$DEFAULT_TZ\",
    \"log_level\": 1,
    \"log_retention_days\": 7,
    \"alert_channel_watching\": true,
    \"alert_vod_watching\": true,
    \"alert_disk_space\": true,
    \"alert_recording_events\": true,
    \"stream_count\": true,
    \"cw_channel_name\": true,
    \"cw_channel_number\": true,
    \"cw_program_name\": true,
    \"cw_device_name\": true,
    \"cw_device_ip\": true,
    \"cw_stream_source\": true,
    \"cw_image_source\": \"PROGRAM\",
    \"rd_alert_scheduled\": true,
    \"rd_alert_started\": true,
    \"rd_alert_completed\": true,
    \"rd_alert_cancelled\": true,
    \"rd_program_name\": true,
    \"rd_program_desc\": true,
    \"rd_duration\": true,
    \"rd_channel_name\": true,
    \"rd_channel_number\": true,
    \"rd_type\": true,
    \"vod_title\": true,
    \"vod_episode_title\": true,
    \"vod_summary\": true,
    \"vod_duration\": true,
    \"vod_progress\": true,
    \"vod_image\": true,
    \"vod_rating\": true,
    \"vod_genres\": true,
    \"vod_cast\": true,
    \"vod_device_name\": true,
    \"vod_device_ip\": true,
    \"vod_alert_cooldown\": 300,
    \"vod_significant_threshold\": 300,
    \"channel_cache_ttl\": 86400,
    \"program_cache_ttl\": 86400,
    \"job_cache_ttl\": 3600,
    \"vod_cache_ttl\": 86400,
    \"ds_threshold_percent\": 10,
    \"ds_threshold_gb\": 50,
    \"pushover_user_key\": \"\",
    \"pushover_api_token\": \"\",
    \"apprise_discord\": \"\",
    \"apprise_email\": \"\",
    \"apprise_email_to\": \"\",
    \"apprise_telegram\": \"\",
    \"apprise_slack\": \"\",
    \"apprise_gotify\": \"\",
    \"apprise_matrix\": \"\",
    \"apprise_mqtt\": \"\",
    \"apprise_custom\": \"\"
}
EOF"
    echo "Created default settings file at $SETTINGS_FILE"
    chmod 644 "$SETTINGS_FILE"
fi

# FILE PERMISSIONS
find "$CONFIG_DIR" -type f -exec chmod 644 {} \; 2>/dev/null || true
find "$CONFIG_DIR" -type d -exec chmod 755 {} \; 2>/dev/null || true

# TIMEZONE
TZ=$(grep -o '"tz": *"[^"]*"' "$SETTINGS_FILE" | sed 's/"tz": *"\([^"]*\)"/\1/')

if [ -z "$TZ" ]; then
    echo "No timezone found in settings.json, using default: $DEFAULT_TZ"
    TZ="$DEFAULT_TZ"
fi

echo "Setting timezone to: $TZ"
export TZ

# I/O PERMISSIONS
chmod 666 /proc/self/fd/1 /proc/self/fd/2 || echo "Warning: Failed to chmod stdout/stderr"

# LAUNCH
exec su-exec "$APP_UID:$APP_GID" "$@" 