import type { Page, Route } from "@playwright/test"

const createMockDebugBundleZip = () => {
  const prefix = "channelwatch_debug_20260622T000000Z"
  const entries = [
    [`${prefix}/manifest.json`, JSON.stringify({
      bundle_type: "debug",
      bundle_schema_version: 1,
      created_by: "channelwatch",
    })],
    [`${prefix}/settings_sanitized.json`, "{}"],
    [`${prefix}/logs/app.log`, ""],
    [`${prefix}/health_snapshot.json`, "{}"],
  ] as const

  let offset = 0
  const localParts: Buffer[] = []
  const centralParts: Buffer[] = []
  for (const [name, content] of entries) {
    const nameBytes = Buffer.from(name)
    const data = Buffer.from(content)
    const localHeader = Buffer.alloc(30)
    localHeader.writeUInt32LE(0x04034b50, 0)
    localHeader.writeUInt16LE(20, 4)
    localHeader.writeUInt16LE(0, 6)
    localHeader.writeUInt16LE(0, 8)
    localHeader.writeUInt32LE(0, 10)
    localHeader.writeUInt32LE(0, 14)
    localHeader.writeUInt32LE(data.length, 18)
    localHeader.writeUInt32LE(data.length, 22)
    localHeader.writeUInt16LE(nameBytes.length, 26)
    localHeader.writeUInt16LE(0, 28)
    localParts.push(localHeader, nameBytes, data)

    const centralHeader = Buffer.alloc(46)
    centralHeader.writeUInt32LE(0x02014b50, 0)
    centralHeader.writeUInt16LE(20, 4)
    centralHeader.writeUInt16LE(20, 6)
    centralHeader.writeUInt16LE(0, 8)
    centralHeader.writeUInt16LE(0, 10)
    centralHeader.writeUInt32LE(0, 12)
    centralHeader.writeUInt32LE(0, 16)
    centralHeader.writeUInt32LE(data.length, 20)
    centralHeader.writeUInt32LE(data.length, 24)
    centralHeader.writeUInt16LE(nameBytes.length, 28)
    centralHeader.writeUInt16LE(0, 30)
    centralHeader.writeUInt16LE(0, 32)
    centralHeader.writeUInt16LE(0, 34)
    centralHeader.writeUInt16LE(0, 36)
    centralHeader.writeUInt32LE(0, 38)
    centralHeader.writeUInt32LE(offset, 42)
    centralParts.push(centralHeader, nameBytes)
    offset += localHeader.length + nameBytes.length + data.length
  }

  const centralDirectory = Buffer.concat(centralParts)
  const eocd = Buffer.alloc(22)
  eocd.writeUInt32LE(0x06054b50, 0)
  eocd.writeUInt16LE(0, 4)
  eocd.writeUInt16LE(0, 6)
  eocd.writeUInt16LE(entries.length, 8)
  eocd.writeUInt16LE(entries.length, 10)
  eocd.writeUInt32LE(centralDirectory.length, 12)
  eocd.writeUInt32LE(offset, 16)
  eocd.writeUInt16LE(0, 20)
  return Buffer.concat([...localParts, centralDirectory, eocd])
}

const mockDebugBundleZip = createMockDebugBundleZip()

const settings = {
  api_key: "test-api-key",
  dvr_servers: [
    {
      id: "main-dvr",
      name: "Main DVR",
      host: "192.168.1.50",
      port: 8089,
      enabled: true,
    },
  ],
  tz: "America/New_York",
  log_level: 20,
  log_retention_days: 14,
  alert_channel_watching: true,
  alert_vod_watching: true,
  alert_disk_space: true,
  alert_recording_events: true,
  stream_count: true,
  cw_channel_name: true,
  cw_channel_number: true,
  cw_program_name: true,
  cw_device_name: true,
  cw_device_ip: true,
  cw_stream_source: true,
  cw_image_source: "program",
  cw_alert_cooldown: 60,
  cw_template_title: "",
  cw_template_body: "",
  cw_template_use_default: true,
  global_rate_limit: 5,
  global_rate_window: 60,
  stream_card_image: "program",
  recording_card_image: "program",
  webhooks: [
    {
      url: "https://example.com/webhook",
      secret: "****",
      enabled: true,
    },
  ],
  rd_alert_scheduled: true,
  rd_alert_started: true,
  rd_alert_completed: true,
  rd_alert_cancelled: true,
  rd_program_name: true,
  rd_program_desc: true,
  rd_duration: true,
  rd_channel_name: true,
  rd_channel_number: true,
  rd_type: true,
  rd_template_title: "",
  rd_template_body: "",
  rd_template_use_default: true,
  vod_title: true,
  vod_episode_title: true,
  vod_summary: true,
  vod_duration: true,
  vod_progress: true,
  vod_image: true,
  vod_rating: true,
  vod_genres: true,
  vod_cast: true,
  vod_device_name: true,
  vod_device_ip: true,
  vod_alert_cooldown: 60,
  vod_significant_threshold: 75,
  vod_template_title: "",
  vod_template_body: "",
  vod_template_use_default: true,
  channel_cache_ttl: 60,
  program_cache_ttl: 60,
  job_cache_ttl: 60,
  vod_cache_ttl: 60,
  ds_threshold_percent: 20,
  ds_threshold_gb: 100,
  ds_warning_threshold_percent: 20,
  ds_warning_threshold_gb: 100,
  ds_critical_threshold_percent: 10,
  ds_critical_threshold_gb: 50,
  ds_alert_cooldown: 300,
  ds_startup_grace_seconds: 60,
  ds_worsening_delta_gb: 10,
  ds_worsening_delta_percent: 5,
  ds_test_route_override: "",
  ds_template_title: "",
  ds_template_body: "",
  ds_template_use_default: true,
  apprise_pushover: "****",
  apprise_discord: "",
  apprise_email: "alerts@example.com",
  apprise_email_to: "owner@example.com",
  apprise_telegram: "",
  apprise_slack: "",
  apprise_gotify: "",
  apprise_matrix: "",
  apprise_custom: "",
  error_reporting_dsn: "",
  notification_routing: {},
}

const systemInfo = {
  channelwatch_version: "0.9.3",
  channels_dvr_host: "192.168.1.50",
  channels_dvr_port: 8089,
  channels_dvr_server_version: "2024.12.1",
  timezone: "America/New_York",
  disk_usage_percent: 39,
  disk_usage_gb: 7260.16,
  disk_total_gb: 18462.72,
  disk_free_gb: 11202.56,
  disk_severity: "normal",
  log_retention_days: 14,
  start_time: "2026-04-21T10:00:00Z",
  container_start_time: "2026-04-21T10:00:00Z",
  uptime_data: { days: 2, hours: 5, minutes: 10, seconds: 0 },
  core_status: "Running",
  library_shows: 120,
  library_movies: 45,
  library_episodes: 1400,
  dvr_status: [
    {
      id: "main-dvr",
      name: "Main DVR",
      host: "192.168.1.50",
      port: 8089,
      connected: true,
      version: "2024.12.1",
      disk_usage_percent: 39,
      disk_total_gb: 18462.72,
      disk_free_gb: 11202.56,
      library_shows: 120,
      library_movies: 45,
      library_episodes: 1400,
      monitoring_status: "healthy",
      monitoring_ready: true,
      monitoring_reason: null,
      freshness_status: "fresh",
      last_freshness_at: "2026-04-21T12:00:00Z",
      last_event_at: "2026-04-21T12:00:00Z",
      freshness_age_seconds: 10,
      stale_threshold_seconds: 60,
    },
  ],
}

const streamDetails = {
  total: 2,
  watching: [
    { device: "Living Room Apple TV", channel: "HBO", image: "/images/channelwatch-logo.png" },
  ],
  recording: [
    { title: "Evening News", until: "8:00 PM" },
  ],
  subtitle: "1 live stream, 1 recording in progress",
  image: "/images/channelwatch-logo.png",
}

const upcomingRecordings = [
  {
    id: "rec-1",
    title: "Evening News",
    start_time: 1713702000,
    end_time: 1713703800,
    channel: "HBO",
    scheduled_time: "2026-04-21T20:00:00Z",
    image: "/images/channelwatch-logo.png",
    dvr_id: "main-dvr",
    dvr_name: "Main DVR",
  },
]

const activityItems = [
  {
    id: "activity-1",
    type: "watching_channel",
    title: "Watching HBO",
    message: "Living Room Apple TV is watching HBO",
    timestamp: "2026-04-21T11:45:00Z",
    icon: "tv",
    channel_name: "HBO",
    channel_number: "501",
    device_name: "Living Room Apple TV",
    device_ip: "192.168.1.75",
    program_title: "Morning Show",
    image_url: "/images/channelwatch-logo.png",
    stream_source: "channels-dvr",
    extra: { stream_count: 2 },
    dvr_id: "main-dvr",
    dvr_name: "Main DVR",
  },
  {
    id: "activity-2",
    type: "recording_event",
    title: "Evening News",
    message: "Scheduled: Evening News",
    timestamp: "2026-04-21T10:30:00Z",
    icon: "recording",
    channel_name: "CBS",
    program_title: "Evening News",
    extra: { recording_type: "scheduled", duration: "30m" },
    dvr_id: "main-dvr",
    dvr_name: "Main DVR",
  },
]

const notificationLog = {
  items: [
    {
      id: 1,
      dvr_id: "main-dvr",
      activity_event_id: "activity-1",
      provider_type: "apprise",
      channel_id: "email",
      channel: "apprise",
      event_type: "watching_channel",
      status: "sent",
      retry_count: 0,
      payload_size: 512,
      error: null,
      sent_at: "2026-04-21T11:46:00Z",
    },
  ],
  total: 1,
  offset: 0,
  limit: 50,
}

const diagnosticsLogs = {
  lines: [
    "2026-04-21 11:45:00 INFO ChannelWatch started successfully",
    "2026-04-21 11:46:00 INFO Notification delivered via apprise",
  ],
}

const securityStatus = {
  persisted_mode: "rbac",
  configured_mode: "rbac",
  effective_mode: "rbac",
  setup_required: false,
  runtime_auth_override_active: false,
  security_mode: "RBAC_ONLY",
  auth_disabled: false,
  rbac_enabled: true,
  api_key_configured: false,
  api_key_fallback_active: false,
  session_auth_available: true,
  session_setup_required: false,
  encrypted_dvr_api_keys_at_rest: true,
  encryption_key_path: "/config/channelwatch.key",
  feeds: {
    implemented: true,
    ics_enabled: false,
    rss_enabled: false,
    atom_enabled: false,
  },
}

const setupStatus = {
  persisted_mode: "rbac",
  configured_mode: "rbac",
  effective_mode: "rbac",
  setup_required: false,
  runtime_auth_override_active: false,
  api_key_fallback_active: false,
  needs_setup: false,
  rbac_enabled: true,
  session_auth_available: true,
  session_setup_required: false,
  current_mode: "rbac",
  available_modes: ["rbac", "none"],
}

const whoAmI = {
  authenticated: true,
  rbac_enabled: true,
  username: "cwadmin",
  role: "admin",
}

const reportConfig = {
  mode: "dry-run",
  endpoint: "/api/v1/support/report-dry-run",
  portal_url: "https://channelwatch.coderluii.dev/report",
  max_bytes: 262144,
  turnstile_site_key: null,
  attachments_enabled: true,
  max_attachment_bytes: 8388608,
  max_total_attachment_bytes: 20971520,
  max_screenshot_count: 5,
  allowed_attachment_types: [
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
  ],
}

function json(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  })
}

export async function installApiMocks(page: Page) {
  await page.addInitScript(() => {
    window.confirm = () => true
  })

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url())
    const { pathname } = url

    if (pathname === "/api/settings") return json(route, settings)
    if (pathname === "/api/system-info") return json(route, systemInfo)
    if (pathname === "/api/streams/details") return json(route, streamDetails)
    if (pathname === "/api/recordings/upcoming") return json(route, upcomingRecordings)
    if (pathname === "/api/recent-activity") return json(route, activityItems)
    if (pathname === "/api/activity-history") {
      return json(route, {
        items: activityItems,
        total: activityItems.length,
        offset: Number(url.searchParams.get("offset") ?? 0),
        limit: Number(url.searchParams.get("limit") ?? 25),
      })
    }
    if (pathname === "/api/v1/notification-log") return json(route, notificationLog)
    if (pathname === "/api/v1/security/status") return json(route, securityStatus)
    if (pathname === "/api/v1/auth/setup-status") return json(route, setupStatus)
    if (pathname === "/api/v1/auth/whoami") return json(route, whoAmI)
    if (pathname === "/api/v1/support/report-config") return json(route, reportConfig)
    if (pathname === "/api/v1/support/offline-package") {
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        headers: {
          "Content-Disposition": 'attachment; filename="channelwatch_support_report_test.zip"',
        },
        body: "offline-package",
      })
    }
    if (pathname === "/api/v1/support/report-dry-run") {
      const contentType = route.request().headers()["content-type"] || ""
      const payload = contentType.includes("multipart/form-data")
        ? {
            summary: "Active Streams shows a stream but no activity appears",
            expected: "A channel watching activity event should appear.",
            email: "viewer@example.com",
          }
        : (route.request().postDataJSON() as { summary?: string; expected?: string; email?: string })
      const attachments = contentType.includes("multipart/form-data")
        ? [
            {
              filename: "channelwatch-logo.png",
              content_type: "image/png",
              size_bytes: 12288,
              kind: "screenshot",
              sha256: "a".repeat(64),
            },
            {
              filename: "channelwatch_debug_test.zip",
              content_type: "application/zip",
              size_bytes: 2048,
              kind: "debug_bundle",
              sha256: "b".repeat(64),
            },
          ]
        : []
      const title = `[In-App] ${payload.summary || "Untitled report"}`
      const body = [
        "# ChannelWatch Support Report",
        `## Summary\n\n${payload.summary || "Untitled report"}`,
        `## Expected behavior\n\n${payload.expected || "Not provided."}`,
        "## Reporter\n\n- GetChannels community: [@Matthew_Crommert](https://community.getchannels.com/u/Matthew_Crommert)",
        "## Diagnostics\n\n| Field | Value |\n| --- | --- |\n| ChannelWatch version | 0.9.3 |\n| DVRs configured | 1 |\n| DVRs connected | 1 |\n| Core status | Running |\n| Monitoring | healthy: 1 |\n| Notification providers | Pushover |\n| Enabled feature toggles | Channel watching, Disk space, Recording events |",
      ].join("\n\n")
      return json(route, {
        mode: "dry-run",
        status: "dry-run-complete",
        issue_title: title,
        issue_body: body,
        email_subject: `ChannelWatch Report - ${payload.summary || "Untitled report"}`,
        email_body: `Private email: ${payload.email || "Not provided"}`,
        email_html: "<h1>New ChannelWatch report</h1>",
        email_in_public_issue: Boolean(payload.email && body.includes(payload.email)),
        attachments,
        attachment_total_bytes: attachments.reduce((sum, attachment) => sum + attachment.size_bytes, 0),
        attachments_sent: false,
      })
    }
    if (pathname === "/api/logs") return json(route, diagnosticsLogs)
    if (pathname === "/api/logs/download") {
      return route.fulfill({ status: 200, contentType: "text/plain", body: diagnosticsLogs.lines.join("\n") })
    }
    if (pathname === "/api/v1/debug/bundle") {
      return route.fulfill({ status: 200, contentType: "application/zip", body: mockDebugBundleZip })
    }
    if (pathname === "/api/health") return json(route, { ok: true })
    if (pathname.startsWith("/api/v1/dvrs/") && pathname.endsWith("/system-info")) {
      return json(route, {
        ...systemInfo.dvr_status[0],
        dvr_id: "main-dvr",
        dvr_name: "Main DVR",
        version_compatible: true,
        version_warning: null,
        disk_usage_gb: 7260.16,
      })
    }
    if (pathname.startsWith("/api/v1/dvrs/") && pathname.endsWith("/streams")) {
      return json(route, { ...streamDetails, dvr_id: "main-dvr", dvr_name: "Main DVR" })
    }
    if (pathname.startsWith("/api/v1/dvrs/") && pathname.endsWith("/recordings/upcoming")) {
      return json(route, upcomingRecordings)
    }

    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    })
  })
}
