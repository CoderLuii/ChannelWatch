export interface DVRServer {
  id: string
  name: string
  host: string
  port: number
  enabled: boolean
  deleted_at?: string | null
  api_key?: string | null
  overrides?: Record<string, any>
}

export interface WebhookEntry {
  id?: string
  url: string
  secret: string
  enabled: boolean
}

export type TrustedNotificationDestinationSource = "apprise_custom" | "webhook"

export interface TrustedNotificationDestination {
  source: TrustedNotificationDestinationSource
  scheme: "http" | "https"
  host: string
  port: number
  label?: string
}

export interface NotificationDestinationSafetyPreview {
  source: string
  url: string
  normalized: TrustedNotificationDestination | null
  status: string
  message: string
  trustable: boolean
  trusted: boolean
}

export interface AppSettings {
  // DVR Servers
  dvr_servers: DVRServer[]
  tz: string
  log_level: number
  log_retention_days: number
  history_retention_days: number

  // Alert Module Enable/Disable
  alert_channel_watching: boolean
  alert_vod_watching: boolean
  alert_disk_space: boolean
  alert_recording_events: boolean
  alert_dvr_health: boolean

  multi_dvr_v2_enabled: boolean

  // Stream Counting
  stream_count: boolean
  monitor_stale_seconds: number

  // Channel-Watching Alert Settings
  cw_channel_name: boolean
  cw_channel_number: boolean
  cw_program_name: boolean
  cw_device_name: boolean
  cw_device_ip: boolean
  cw_stream_source: boolean
  cw_image_source: string
  cw_alert_cooldown: number
  cw_template_title: string
  cw_template_body: string
  cw_template_use_default: boolean

  // Global Rate Limiter
  global_rate_limit: number
  global_rate_window: number

  // Display
  stream_card_image: string
  recording_card_image: string

  // API Authentication
  api_key: string
  security_setup_completed: boolean | null
  ics_feed_enabled: boolean
  ics_feed_token: string | null
  rss_feed_enabled: boolean
  rss_feed_token: string | null
  webhooks: WebhookEntry[]
  trusted_notification_destinations: TrustedNotificationDestination[]

  // Recording Events Alert Settings
  rd_alert_scheduled: boolean
  rd_alert_started: boolean
  rd_alert_completed: boolean
  rd_alert_cancelled: boolean
  rd_alert_failed: boolean
  rd_alert_skipped: boolean
  rd_alert_missed: boolean
  rd_alert_interrupted: boolean
  dvr_alert_unreachable: boolean
  dvr_alert_recovered: boolean
  dvr_health_alert_delay_seconds: number
  notification_preferences_version: number
  rd_program_name: boolean
  rd_program_desc: boolean
  rd_duration: boolean
  rd_channel_name: boolean
  rd_channel_number: boolean
  rd_type: boolean
  rd_template_title: string
  rd_template_body: string
  rd_template_use_default: boolean

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
  vod_template_title: string
  vod_template_body: string
  vod_template_use_default: boolean

  // Cache Settings
  channel_cache_ttl: number
  program_cache_ttl: number
  job_cache_ttl: number
  vod_cache_ttl: number

  // Disk Space Monitoring
  ds_threshold_percent: number
  ds_threshold_gb: number
  ds_warning_threshold_percent: number
  ds_warning_threshold_gb: number
  ds_critical_threshold_percent: number
  ds_critical_threshold_gb: number
  ds_alert_cooldown: number
  ds_startup_grace_seconds: number
  ds_worsening_delta_gb: number
  ds_worsening_delta_percent: number
  ds_test_route_override: string
  ds_template_title: string
  ds_template_body: string
  ds_template_use_default: boolean

  // Notification Providers
  apprise_pushover: string
  apprise_discord: string
  apprise_email: string
  apprise_email_to: string
  apprise_telegram: string
  apprise_slack: string
  apprise_gotify: string
  apprise_matrix: string
  apprise_custom: string

  error_reporting_dsn: string

  rbac_enabled?: boolean
  auth_mode?: AuthMode | ""
  notification_routing: Record<string, Record<string, Record<string, boolean> | null> | null>
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

export interface DVRStatusInfo {
  id: string
  name: string
  host: string
  port: number
  connected: boolean
  enabled?: boolean
  version: string | null
  started_at?: string | null
  uptime_seconds?: number | null
  uptime_available?: boolean
  version_compatible: boolean | null
  version_warning: string | null
  disk_usage_percent: number | null
  disk_total_gb: number | null
  disk_free_gb: number | null
  active_streams: number
  library_shows: number
  library_movies: number
  library_episodes: number
  monitoring_status?: string
  monitoring_ready?: boolean
  monitoring_reason?: string | null
  freshness_status?: string
  last_freshness_at?: string | null
  last_event_at?: string | null
  freshness_age_seconds?: number | null
  stale_threshold_seconds?: number | null
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
  disk_severity: 'normal' | 'warning' | 'critical'
  log_retention_days: number | null
  start_time: string | null
  container_start_time: string | null
  channelwatch_core_started_at: string | null
  channelwatch_core_uptime_seconds: number | null
  channelwatch_ui_started_at: string | null
  channelwatch_ui_uptime_seconds: number | null
  uptime_data: {
    days: number
    hours: number
    minutes: number
    seconds: number
  }
  core_status: string
  library_shows: number
  library_movies: number
  library_episodes: number
  dvr_status: DVRStatusInfo[]
}

export interface RecordingInfo {
  id: string
  title: string
  start_time: number
  end_time: number
  channel: string
  scheduled_time: string
  image: string
  artwork_fallback_exhausted?: boolean
  dvr_id?: string
  dvr_name?: string
}

export interface ActivityItem {
  id: string
  type: string
  title: string
  message: string
  timestamp: string
  icon: string
  channel_name?: string
  channel_number?: string
  device_name?: string
  device_ip?: string
  program_title?: string
  image_url?: string
  stream_source?: string
  extra?: Record<string, any>
  dvr_id?: string
  dvr_name?: string
  is_test?: boolean
}

export interface PerDvrSystemInfo {
  dvr_id: string
  dvr_name: string
  host: string
  port: number
  connected: boolean
  version: string | null
  version_compatible: boolean | null
  version_warning: string | null
  disk_usage_percent: number | null
  disk_usage_gb: number | null
  disk_total_gb: number | null
  disk_free_gb: number | null
  disk_severity: 'normal' | 'warning' | 'critical'
  library_shows: number
  library_movies: number
  library_episodes: number
}

export type AuthMode = "api_key" | "rbac" | "none"

export type EffectiveAuthMode = AuthMode | "setup"

export type SecurityMode = "NO_AUTH" | "API_KEY_ONLY" | "RBAC_WITH_API_KEY_FALLBACK" | "RBAC_ONLY"

export interface AuthStateContract {
  persisted_mode: AuthMode | null
  configured_mode: EffectiveAuthMode | null
  effective_mode: EffectiveAuthMode | null
  setup_required: boolean
  runtime_auth_override_active: boolean
  api_key_fallback_active: boolean
  rbac_enabled: boolean
  session_auth_available: boolean
  session_setup_required: boolean
}

export interface WhoAmIResponse {
  authenticated: boolean
  rbac_enabled: boolean
  username?: string
  role?: string
}

export interface AuthSetupStatus extends AuthStateContract {
  current_mode: EffectiveAuthMode | null
  available_modes: AuthMode[]
  needs_setup: boolean
}

export type RuntimePreflightBlocker =
  | "secret_storage_key_missing"
  | "secret_storage_key_too_short"
  | "secret_storage_key_mismatch"
  | "secret_storage_key_file_unreadable"
  | "managed_key_unavailable"
  | "protected_credentials_locked"
  | "legacy_key_conflict"
  | "encryption_key_corrupt"

export type RuntimePreflightWarning =
  | "legacy_plaintext_key_migration_recommended"
  | "legacy_envelope_migration_available"
  | "protected_credentials_recovery_required"

export interface RuntimePreflightStatus {
  status: "ready" | "setup_required" | "migration_recommended" | "recovery_required" | "storage_unavailable"
  setup_required: boolean
  blockers: RuntimePreflightBlocker[]
  warnings: RuntimePreflightWarning[]
}

export type KeyRecoveryState =
  | "managed_local"
  | "legacy_recovery_required"
  | "protected_credentials_need_attention"
  | "ready"
  | "storage_unavailable"

export interface KeyRecoveryStatus {
  state: KeyRecoveryState
  recovery_required: boolean
  can_migrate: boolean
  can_reset: boolean
  blocker_code?: string | null
  affected_dvr_credentials: number
  affected_notification_credentials: number
  legacy_input_detected: boolean
  message?: string | null
}

export interface KeyRecoveryResult extends KeyRecoveryStatus {
  message: string
  backup_created: boolean
  restart_required: boolean
  cleared_dvr_credentials?: number
  cleared_notification_credentials?: number
}

export interface SecurityFeedsStatus {
  implemented: boolean
  ics_enabled: boolean
  rss_enabled: boolean
  atom_enabled: boolean
}

export interface SecurityStatus extends AuthStateContract {
  security_mode: SecurityMode
  auth_disabled: boolean
  api_key_configured: boolean
  encrypted_dvr_api_keys_at_rest: boolean
  encryption_key_path: string
  feeds: SecurityFeedsStatus
}
