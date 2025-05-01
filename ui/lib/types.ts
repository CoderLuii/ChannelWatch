export interface AppSettings {
  // Core Settings
  channels_dvr_host: string | null
  channels_dvr_port: number
  tz: string
  log_level: number
  log_retention_days: number

  // Alert Module Enable/Disable
  alert_channel_watching: boolean
  alert_vod_watching: boolean
  alert_disk_space: boolean
  alert_recording_events: boolean

  // Stream Counting
  stream_count: boolean

  // Channel-Watching Alert Settings
  cw_channel_name: boolean
  cw_channel_number: boolean
  cw_program_name: boolean
  cw_device_name: boolean
  cw_device_ip: boolean
  cw_stream_source: boolean
  cw_image_source: string

  // Recording Events Alert Settings
  rd_alert_scheduled: boolean
  rd_alert_started: boolean
  rd_alert_completed: boolean
  rd_alert_cancelled: boolean
  rd_program_name: boolean
  rd_program_desc: boolean
  rd_duration: boolean
  rd_channel_name: boolean
  rd_channel_number: boolean
  rd_type: boolean

  // VOD Watching Alert Settings
  vod_title: boolean
  vod_episode_title: boolean
  vod_summary: boolean
  vod_duration: boolean
  vod_progress: boolean
  vod_image: boolean
  vod_rating: boolean
  vod_genres: boolean
  vod_cast: boolean
  vod_device_name: boolean
  vod_device_ip: boolean
  vod_alert_cooldown: number
  vod_significant_threshold: number

  // Cache Settings
  channel_cache_ttl: number
  program_cache_ttl: number
  job_cache_ttl: number
  vod_cache_ttl: number

  // Disk Space Monitoring
  ds_threshold_percent: number
  ds_threshold_gb: number

  // Notification Providers
  pushover_user_key: string
  pushover_api_token: string
  apprise_discord: string
  apprise_email: string
  apprise_email_to: string
  apprise_telegram: string
  apprise_slack: string
  apprise_gotify: string
  apprise_matrix: string
  apprise_custom: string
}

export interface AboutInfo {
  app_name: string
  version: string
  developer: string
  description: string
  github_url: string
  dockerhub_url: string
}

export interface TestResult {
  test_name: string
  success: boolean
  message: string
}

export interface SystemInfo {
  channelwatch_version: string
  channels_dvr_host: string | null
  channels_dvr_port: number
  channels_dvr_server_version: string | null
  timezone: string
  disk_usage_percent: number | null
  disk_usage_gb: number | null
  disk_total_gb: number | null
  disk_free_gb: number | null
  log_retention_days: number | null
  start_time: string | null
  uptime_data: {
    days: number
    hours: number
    minutes: number
    seconds: number
  }
}


