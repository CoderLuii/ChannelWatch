import type { AppSettings, AboutInfo, TestResult, SystemInfo, RecordingInfo, ActivityItem, SecurityStatus, PerDvrSystemInfo, AuthMode, AuthSetupStatus, WhoAmIResponse, EffectiveAuthMode, NotificationDestinationSafetyPreview, TrustedNotificationDestinationSource, RuntimePreflightStatus, KeyRecoveryResult, KeyRecoveryStatus } from "@/lib/types"
import { parseApiError, type ErrorPayload } from "@/lib/error-catalog"
import { encodeReportChallengeProof, solveReportChallenge, type ReportChallenge } from "@/lib/report-proof"

const API_BASE = "/api"
let runtimeApiKey = ""

export const RUNTIME_PREFLIGHT_CHANGED_EVENT = "channelwatch-runtime-preflight-changed"

export class ApiError extends Error {
  readonly payload: ErrorPayload
  constructor(payload: ErrorPayload) {
    super(payload.message)
    this.name = "ApiError"
    this.payload = payload
  }
}

export class AuthRequiredError extends Error {
  readonly status: number
  constructor(message: string = "Authentication required", status: number = 401) {
    super(message)
    this.name = "AuthRequiredError"
    this.status = status
  }
}

export class SessionRequiredError extends Error {
  readonly status: number
  constructor(message: string = "Session authentication required", status: number = 401) {
    super(message)
    this.name = "SessionRequiredError"
    this.status = status
  }
}

function getCsrfToken(): string {
  return sessionStorage.getItem("cw_csrf_token") || ""
}

function getApiKey(): string {
  return runtimeApiKey
}

function setCsrfToken(value: string) {
  sessionStorage.setItem("cw_csrf_token", value)
}

export function clearCachedAuthState() {
  if (typeof window === "undefined") {
    return
  }

  if (typeof sessionStorage !== "undefined") {
    sessionStorage.removeItem("cw_csrf_token")
  }
  runtimeApiKey = ""
}

export function cacheApiKey(value: string) {
  if (typeof window === "undefined") {
    return
  }
  runtimeApiKey = value
}

function withConfiguredMode<T extends { configured_mode?: EffectiveAuthMode | null; current_mode?: EffectiveAuthMode | null; effective_mode?: EffectiveAuthMode | null }>(
  payload: T,
): T & { configured_mode: EffectiveAuthMode | null; effective_mode: EffectiveAuthMode | null } {
  return {
    ...payload,
    configured_mode: payload.configured_mode ?? payload.current_mode ?? payload.effective_mode ?? null,
    effective_mode: payload.effective_mode ?? payload.current_mode ?? payload.configured_mode ?? null,
  }
}

export function authHeaders(): Record<string, string> {
  const csrf = typeof window !== "undefined" ? getCsrfToken() : ""
  const apiKey = typeof window !== "undefined" ? getApiKey() : ""
  return {
    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    ...(apiKey ? { "X-API-Key": apiKey } : {}),
  }
}

export async function fetchSetupStatus(): Promise<AuthSetupStatus> {
  const response = await fetch(`${API_BASE}/v1/auth/setup-status`, {
    credentials: "same-origin",
  })

  if (!response.ok) {
    throw new Error(`Failed to fetch setup status: ${response.status}`)
  }

  const body = (await response.json()) as AuthSetupStatus
  return withConfiguredMode(body)
}

export async function fetchRuntimePreflight(): Promise<RuntimePreflightStatus> {
  const response = await fetch(`${API_BASE}/v1/runtime/preflight`, {
    credentials: "same-origin",
  })
  if (!response.ok) {
    throw new Error(`Failed to fetch runtime preflight: ${response.status}`)
  }
  return response.json()
}

export async function fetchWhoAmI(): Promise<WhoAmIResponse> {
  const response = await fetch(`${API_BASE}/v1/auth/whoami`, {
    credentials: "same-origin",
  })

  if (response.status === 401) {
    return { authenticated: false, rbac_enabled: true }
  }

  if (!response.ok) {
    throw new Error(`Failed to fetch auth status: ${response.status}`)
  }

  return response.json()
}

export async function logoutSession(): Promise<void> {
  const response = await fetch(`${API_BASE}/v1/auth/logout`, {
    method: "POST",
    credentials: "same-origin",
    headers: authHeaders(),
  })

  if (response.status !== 401 && !response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }
  clearCachedAuthState()
}

export async function loginWithPassword(username: string, password: string): Promise<{ username: string; role: string; csrf_token: string }> {
  const response = await fetch(`${API_BASE}/v1/auth/login`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })

  if (response.status === 401) {
    throw new SessionRequiredError("Invalid credentials", 401)
  }
  if (!response.ok) {
    throw new Error(`Failed to login: ${response.status}`)
  }

  const body = await response.json()
  if (body?.csrf_token) {
    runtimeApiKey = ""
    setCsrfToken(body.csrf_token)
  }
  return body
}

export async function completeInitialSetup(mode: AuthMode, username?: string, password?: string): Promise<{ message: string; username?: string; csrf_token?: string }> {
  const response = await fetch(`${API_BASE}/v1/auth/setup`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, username: username ?? "", password: password ?? "" }),
  })

  if (!response.ok) {
    throw new Error(`Failed to complete setup: ${response.status}`)
  }

  const body = await response.json()
  if (body?.csrf_token) {
    runtimeApiKey = ""
    setCsrfToken(body.csrf_token)
  }
  return body
}

export async function changeCredentials(currentPassword: string, username?: string, newPassword?: string): Promise<{ message: string; username: string }> {
  const response = await fetch(`${API_BASE}/v1/auth/change-credentials`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify({ current_password: currentPassword, username: username ?? "", new_password: newPassword ?? "" }),
  })
  if (response.status === 401) {
    throw new SessionRequiredError("Invalid credentials", 401)
  }
  if (!response.ok) {
    throw new Error(`Failed to update credentials: ${response.status}`)
  }
  return response.json()
}

export async function fetchSettings(): Promise<AppSettings> {
  const response = await fetch(`${API_BASE}/settings`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })

  if (!response.ok) {
    if (response.status === 401) {
      throw new AuthRequiredError("Authentication required to load settings", 401)
    }
    const errorText = await response.text()
    throw new Error(`Failed to fetch settings: ${errorText}`)
  }

  const data = await response.json()

  return data
}

export async function saveSettings(settings: AppSettings): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/settings`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify(settings),
  })

  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }

  return response.json()
}

export async function previewNotificationDestinationSafety(
  source: TrustedNotificationDestinationSource,
  url: string,
): Promise<NotificationDestinationSafetyPreview> {
  const response = await fetch(`${API_BASE}/v1/notifications/destination-safety/preview`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify({ source, url }),
  })

  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }

  return response.json()
}

export async function fetchAboutInfo(): Promise<AboutInfo> {
  const response = await fetch(`${API_BASE}/about`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchSystemInfo(options: { dvr_id?: string } = {}): Promise<SystemInfo> {
  const params = new URLSearchParams()
  if (options.dvr_id) params.set("dvr_id", options.dvr_id)
  const queryString = params.toString()
  const response = await fetch(`${API_BASE}/system-info${queryString ? `?${queryString}` : ""}`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function runTest(testName: string): Promise<TestResult> {
  // Replace spaces with underscores for the URL path
  const urlTestName = encodeURIComponent(testName.replace(/ /g, "_"))

  const response = await fetch(`${API_BASE}/run_test/${urlTestName}`, {
    method: "POST",
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function signalRestart(): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/restart_core`, {
    method: "POST",
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function signalContainerRestart(): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/restart_container`, {
    method: "POST",
    headers: authHeaders(),
  })

  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }

  return response.json()
}

export interface UpdateManifestPayload {
  version: string
  version_tag: string
  image_required: boolean
  runtime_abi: string
  settings_schema_version: number
  release_url?: string | null
  bundle_url?: string | null
  highlights?: string[]
  published_at?: string | null
  delivery_mode?: UpdateDeliveryMode
  image_refresh_recommended?: boolean
}

export type UpdateDeliveryMode = "app_update" | "app_update_with_image_refresh" | "image_required"
export type UpdatePolicyMode = "automatic" | "notify_only"

export interface UpdatePolicy {
  mode: UpdatePolicyMode
  maintenance_window_start: string
  maintenance_window_minutes: number
  postponed_until?: string | null
  next_attempt_at?: string | null
  last_attempt_at?: string | null
  retry_count?: number
  last_error?: string | null
  scheduled_restart_at?: string | null
  postpone_available?: boolean
}

export interface UpdateJob {
  job_id: string
  operation: "check" | "apply" | "rollback" | string
  status: string
  version?: string | null
  message?: string | null
  updated_at?: string | null
  backup_path?: string | null
  restart_required?: boolean
  validated_at?: string | null
  rolled_back_from?: string | null
}

export interface UpdateStatus {
  current_version: string
  image_version?: string | null
  runtime_abi: string
  launcher_protocol?: number | null
  runtime_source?: "image" | "app_bundle" | string
  delivery_mode?: UpdateDeliveryMode
  image_refresh_recommended?: boolean
  settings_schema_version: number
  active_bundle?: Record<string, unknown> | null
  latest?: UpdateManifestPayload | null
  update_available: boolean
  image_required: boolean
  operation_busy?: boolean
  last_job?: UpdateJob | null
  rollback_available: boolean
  auth_disabled_warning: boolean
}

export interface RecoveryUpdateStatus {
  status: "active" | "inactive"
  reason_code: "official_recovery_active" | "official_recovery_inactive"
  current_version: string
  active_bundle?: { version?: string | null } | null
  latest?: UpdateManifestPayload | null
  update_available: boolean
  image_required: boolean
  recovery_waiting_for_newer_release?: boolean
  recovery_active: boolean
  bootstrap_csrf?: string | null
  confirmation_required: boolean
}

function recoveryUpdateHeaders(bootstrapCsrf?: string | null): Record<string, string> {
  return {
    "Content-Type": "application/json",
    ...authHeaders(),
    ...(bootstrapCsrf ? { "X-CSRF-Token": bootstrapCsrf } : {}),
  }
}

export async function fetchRecoveryUpdateStatus(): Promise<RecoveryUpdateStatus> {
  const response = await fetch(`${API_BASE}/v1/update/recovery/status`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function checkRecoveryUpdate(bootstrapCsrf?: string | null): Promise<RecoveryUpdateStatus> {
  const response = await fetch(`${API_BASE}/v1/update/recovery/check`, {
    method: "POST",
    headers: recoveryUpdateHeaders(bootstrapCsrf),
    credentials: "same-origin",
    body: JSON.stringify({}),
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function applyRecoveryUpdate(
  version: string,
  bootstrapCsrf?: string | null,
): Promise<UpdateJob> {
  const response = await fetch(`${API_BASE}/v1/update/recovery/apply`, {
    method: "POST",
    headers: recoveryUpdateHeaders(bootstrapCsrf),
    credentials: "same-origin",
    body: JSON.stringify({
      version,
      confirmation: "INSTALL OFFICIAL UPDATE",
    }),
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function fetchUpdatePolicy(): Promise<UpdatePolicy> {
  const response = await fetch(`${API_BASE}/v1/update/policy`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function saveUpdatePolicy(policy: Pick<UpdatePolicy, "mode" | "maintenance_window_start" | "maintenance_window_minutes">): Promise<UpdatePolicy> {
  const response = await fetch(`${API_BASE}/v1/update/policy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    credentials: "same-origin",
    body: JSON.stringify(policy),
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function postponeUpdate(hours: 24 | 168, reason?: "dirty_report_draft"): Promise<UpdatePolicy> {
  const response = await fetch(`${API_BASE}/v1/update/postpone`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    credentials: "same-origin",
    body: JSON.stringify(reason ? { hours, reason } : { hours }),
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function retryUpdate(): Promise<UpdateJob> {
  const response = await fetch(`${API_BASE}/v1/update/retry`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function fetchKeyRecoveryStatus(): Promise<KeyRecoveryStatus> {
  const response = await fetch(`${API_BASE}/v1/runtime/key-recovery/status`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function migrateLegacyKey(options: { legacyWrappingKey?: string; rawKeyFile?: File }): Promise<KeyRecoveryResult> {
  const body = new FormData()
  if (options.legacyWrappingKey) body.set("legacy_storage_key", options.legacyWrappingKey)
  if (options.rawKeyFile) body.set("raw_key_file", options.rawKeyFile, options.rawKeyFile.name)
  const response = await fetch(`${API_BASE}/v1/runtime/key-recovery/migrate`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
    body,
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function resetProtectedCredentials(confirmation: "RESET PROTECTED CREDENTIALS"): Promise<KeyRecoveryResult> {
  const response = await fetch(`${API_BASE}/v1/runtime/key-recovery/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    credentials: "same-origin",
    body: JSON.stringify({ confirmation }),
  })
  if (!response.ok) {
    throw new ApiError(await parseApiError(response))
  }
  return response.json()
}

export async function fetchUpdateStatus(): Promise<UpdateStatus> {
  const response = await fetch(`${API_BASE}/v1/update/status`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }
  return response.json()
}

export async function checkForUpdate(): Promise<UpdateStatus> {
  const response = await fetch(`${API_BASE}/v1/update/check`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }
  return response.json()
}

export async function applyUpdate(version?: string): Promise<UpdateJob> {
  const response = await fetch(`${API_BASE}/v1/update/apply`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    credentials: "same-origin",
    body: JSON.stringify({ version: version ?? null }),
  })
  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }
  return response.json()
}

export async function fetchUpdateJob(jobId: string): Promise<UpdateJob> {
  const response = await fetch(`${API_BASE}/v1/update/jobs/${encodeURIComponent(jobId)}`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }
  return response.json()
}

export async function rollbackUpdate(): Promise<UpdateJob> {
  const response = await fetch(`${API_BASE}/v1/update/rollback`, {
    method: "POST",
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }
  return response.json()
}

export interface DiscoveredServer {
  host: string
  port: number
  name: string
  version: string
}

export interface DvrConnectionTestResult {
  success: boolean
  name?: string
  version?: string
  error?: string
}

export async function testDvrConnection(
  host: string,
  port: number,
  apiKey?: string,
): Promise<DvrConnectionTestResult> {
  const response = await fetch(`${API_BASE}/v1/dvrs/test-connection`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ host, port, api_key: apiKey || null }),
  })
  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }
  return response.json()
}

export async function discoverServers(): Promise<{ servers: DiscoveredServer[]; error?: string }> {
  const response = await fetch(`${API_BASE}/discover-servers`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchUpcomingRecordings(limit: number = 250): Promise<RecordingInfo[]> {
  const response = await fetch(`${API_BASE}/recordings/upcoming?limit=${limit}`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchActiveRecordingsCount(): Promise<number> {
  const response = await fetch(`${API_BASE}/recordings/active`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchActiveStreamsCount(): Promise<number> {
  const response = await fetch(`${API_BASE}/streams/active`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export interface StreamDetails {
  total: number
  watching: Array<{ device: string; channel: string; image: string }>
  recording: Array<{ title: string; until: string }>
  subtitle: string
  image: string
}

export async function fetchStreamDetails(): Promise<StreamDetails> {
  const response = await fetch(`${API_BASE}/streams/details`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchRecentActivity(hours: number = 24, limit: number = 10): Promise<ActivityItem[]> {
  const response = await fetch(`${API_BASE}/recent-activity?hours=${hours}&limit=${limit}`, {
    headers: authHeaders(),
  });

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`);
  }

  return response.json();
}

export interface ActivityHistoryResponse {
  items: ActivityItem[]
  total: number
  offset: number
  limit: number
}

export interface FetchActivityHistoryOptions {
  offset?: number
  limit?: number
  type?: string
  search?: string
  sort?: "asc" | "desc"
  dvr_id?: string
}

export async function fetchActivityHistory(options: FetchActivityHistoryOptions = {}): Promise<ActivityHistoryResponse> {
  const params = new URLSearchParams()

  if (options.offset != null) params.set("offset", String(options.offset))
  if (options.limit != null) params.set("limit", String(options.limit))
  if (options.type && options.type !== "all") params.set("type", options.type)
  if (options.search) params.set("search", options.search)
  if (options.sort) params.set("sort", options.sort)
  if (options.dvr_id) params.set("dvr_id", options.dvr_id)

  const queryString = params.toString()
  const response = await fetch(`${API_BASE}/activity-history${queryString ? `?${queryString}` : ""}`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function clearActivityHistory(): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/clear-activity-history`, {
    method: "POST",
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function regenerateApiKey(): Promise<{ api_key: string }> {
  const response = await fetch(`${API_BASE}/regenerate-api-key`, {
    method: "POST",
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchSecurityStatus(): Promise<SecurityStatus> {
  const response = await fetch(`${API_BASE}/v1/security/status`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  const body = (await response.json()) as SecurityStatus
  return withConfiguredMode(body)
}

export interface PollForRecoveryOptions {
  interval?: number
  initialDelay?: number
  timeout?: number
  minimumRecoveryMs?: number
  onTick?: (elapsedMs: number) => void
  onRecovered?: () => void
  onTimeout?: () => void
}

export const RESTART_RECOVERED_EVENT = "channelwatch-restart-recovered"

export async function fetchMonitoringReadiness(): Promise<boolean | null> {
  const publicResponse = await fetch(`/healthz/ready`, {
    credentials: "same-origin",
  })
  if (publicResponse.status !== 200 && publicResponse.status !== 503) return null
  const publicPayload = await publicResponse.json() as { ready?: boolean }
  if (publicResponse.status === 200 && publicPayload.ready === true) return true

  // Public readiness is authoritative for the recovered process. Detailed
  // health enriches the result when authentication survived the restart, but
  // its absence must not turn a known degraded state into success.
  const response = await fetch(`${API_BASE}/health`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })
  if (response.status !== 200 && response.status !== 503) return false
  const payload = await response.json() as { ready?: boolean }
  return payload.ready === true
}

export async function fetchDvrSystemInfo(dvrId: string): Promise<PerDvrSystemInfo> {
  const response = await fetch(`${API_BASE}/v1/dvrs/${encodeURIComponent(dvrId)}/system-info`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export interface DvrStreamDetails extends StreamDetails {
  dvr_id: string
  dvr_name: string
}

export async function fetchDvrStreams(dvrId: string): Promise<DvrStreamDetails> {
  const response = await fetch(`${API_BASE}/v1/dvrs/${encodeURIComponent(dvrId)}/streams`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchDvrUpcomingRecordings(dvrId: string, limit: number = 250): Promise<RecordingInfo[]> {
  const response = await fetch(`${API_BASE}/v1/dvrs/${encodeURIComponent(dvrId)}/recordings/upcoming?limit=${limit}`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchDvrActivityHistory(
  dvrId: string,
  options: Omit<FetchActivityHistoryOptions, "dvr_id"> = {},
): Promise<ActivityHistoryResponse> {
  const params = new URLSearchParams()
  if (options.offset != null) params.set("offset", String(options.offset))
  if (options.limit != null) params.set("limit", String(options.limit))
  if (options.type && options.type !== "all") params.set("type", options.type)
  if (options.search) params.set("search", options.search)
  if (options.sort) params.set("sort", options.sort)

  const queryString = params.toString()
  const response = await fetch(
    `${API_BASE}/v1/dvrs/${encodeURIComponent(dvrId)}/activity-history${queryString ? `?${queryString}` : ""}`,
    { headers: authHeaders() },
  )

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function downloadBackup(): Promise<Blob> {
  const response = await fetch(`${API_BASE}/v1/backup/download`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }

  return response.blob()
}

export async function downloadDebugBundle(): Promise<Blob> {
  const response = await fetch(`${API_BASE}/v1/debug/bundle`, {
    headers: authHeaders(),
  })

  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }

  return response.blob()
}

export type ReportMode = "dry-run" | "email-test" | "live"

export interface ReportFeatureToggles {
  channel_watching: boolean
  vod_watching: boolean
  disk_space: boolean
  recording_events: boolean
  stream_counter: boolean
}

export interface ReportDiagnostics {
  channelwatch_version?: string | null
  dvr_count: number
  connected_dvr_count: number
  core_status?: string | null
  monitoring_statuses: string[]
  notification_providers: string[]
  feature_toggles: ReportFeatureToggles
}

export interface ReportProblemPayload {
  summary: string
  expected?: string | null
  getchannels_username?: string | null
  github_username?: string | null
  email?: string | null
  diagnostics: ReportDiagnostics
  turnstile_token?: string | null
}

export interface ReportConfig {
  mode: ReportMode
  endpoint: string
  portal_url?: string | null
  max_bytes: number
  turnstile_site_key?: string | null
  attachments_enabled: boolean
  max_attachment_bytes: number
  max_total_attachment_bytes: number
  max_screenshot_count: number
  allowed_attachment_types: string[]
}

export interface ReportAttachmentSummary {
  filename: string
  content_type: string
  size_bytes: number
  kind: "screenshot" | "debug_bundle"
  sha256: string
}

export interface ReportPreviewResponse {
  mode: ReportMode
  status:
    | "dry-run-complete"
    | "email-test-ready"
    | "live-ready"
    | "received"
    | "issue_created"
    | "private_delivery_pending"
    | "provider_confirmation_pending"
    | "completed"
    | "completed_with_private_delivery_failure"
    | "retryable_failure"
    | "rejected"
    | "rate_limited"
  issue_title: string
  issue_body: string
  email_subject: string
  email_body: string
  email_html?: string
  email_in_public_issue: boolean
  attachments: ReportAttachmentSummary[]
  attachment_total_bytes: number
  attachments_sent: boolean
  report_id?: string | null
  issue_url?: string | null
  private_delivery_status?: "not_requested" | "pending" | "delivered" | "failed" | null
  correlation_id?: string | null
}

export interface ReportDraft {
  reportId: string
  supportCode: string
}

export interface ReportSubmissionAttachments {
  screenshots?: File[]
  debugBundle?: File | null
}

function createReportId(): string {
  const webCrypto = globalThis.crypto
  if (!webCrypto?.getRandomValues) {
    throw new Error("This browser cannot generate secure random values. Use a current browser or download the report details manually.")
  }
  if (typeof webCrypto.randomUUID === "function") return webCrypto.randomUUID()
  const bytes = webCrypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function payloadForSupportCode(payload: ReportProblemPayload): ReportProblemPayload {
  return {
    ...payload,
    turnstile_token: null,
  }
}

function base64UrlEncode(value: string): string {
  const bytes = new TextEncoder().encode(value)
  let binary = ""
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.slice(offset, offset + chunkSize))
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/u, "")
}

export function createReportSupportCode(
  payload: ReportProblemPayload,
  options: { reportId?: string; createdAt?: string } = {},
): string {
  const reportId = options.reportId ?? createReportId()
  const envelope = {
    schema: 2,
    report_id: reportId,
    created_at: options.createdAt ?? new Date().toISOString(),
    report: payloadForSupportCode(payload),
    client: {
      channelwatch_version: payload.diagnostics.channelwatch_version ?? "unknown",
      submission_source: "in-app",
    },
  }
  return `CW-REPORT-v2-${base64UrlEncode(JSON.stringify(envelope))}`
}

export function createReportDraft(payload: ReportProblemPayload): ReportDraft {
  const reportId = createReportId()
  return {
    reportId,
    supportCode: createReportSupportCode(payload, { reportId }),
  }
}

function isSameOriginEndpoint(endpoint: string): boolean {
  return endpoint.startsWith("/")
}

function buildReportBody(
  payload: ReportProblemPayload,
  attachments: ReportSubmissionAttachments = {},
  options: { includeSupportCode?: boolean; supportCode?: string } = {},
) {
  const screenshotFiles = attachments.screenshots ?? []
  const hasAttachments = screenshotFiles.length > 0 || Boolean(attachments.debugBundle)
  const supportCode = options.includeSupportCode
    ? options.supportCode ?? createReportSupportCode(payload)
    : null
  const body = hasAttachments
    ? new FormData()
    : JSON.stringify(supportCode ? { support_code: supportCode } : payload)
  if (body instanceof FormData) {
    if (supportCode) {
      body.append("support_code", supportCode)
    } else {
      body.append("payload", JSON.stringify(payload))
    }
    for (const file of screenshotFiles) {
      body.append("screenshots", file, file.name)
    }
    if (attachments.debugBundle) {
      body.append("debug_bundle", attachments.debugBundle, attachments.debugBundle.name)
    }
  }
  return { body, hasAttachments }
}

export async function fetchReportConfig(): Promise<ReportConfig> {
  const response = await fetch(`${API_BASE}/v1/support/report-config`, {
    headers: authHeaders(),
    credentials: "same-origin",
  })

  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }

  return response.json()
}

export async function submitReport(
  endpoint: string,
  payload: ReportProblemPayload,
  attachments: ReportSubmissionAttachments = {},
  options: {
    supportCode?: string
    signal?: AbortSignal
    onChallengeProgress?: (attempts: number) => void
  } = {},
): Promise<ReportPreviewResponse> {
  const sameOrigin = isSameOriginEndpoint(endpoint)
  let proofHeader: string | null = null
  if (!sameOrigin) {
    const challengeUrl = new URL("/api/reports/challenge", endpoint).toString()
    const challengeResponse = await fetch(challengeUrl, {
      method: "POST",
      credentials: "omit",
      headers: { "X-ChannelWatch-In-App-Report": "1" },
      signal: options.signal,
    })
    if (!challengeResponse.ok) {
      const errorPayload = await parseApiError(challengeResponse)
      throw new ApiError(errorPayload)
    }
    const challenge = await challengeResponse.json() as ReportChallenge
    const proof = await solveReportChallenge(challenge, {
      signal: options.signal,
      onProgress: options.onChallengeProgress,
    })
    proofHeader = encodeReportChallengeProof(proof)
  }
  const { body, hasAttachments } = buildReportBody(payload, attachments, {
    includeSupportCode: !sameOrigin,
    supportCode: options.supportCode,
  })
  const response = await fetch(endpoint, {
    method: "POST",
    credentials: sameOrigin ? "same-origin" : "omit",
    headers: {
      ...(!hasAttachments ? { "Content-Type": "application/json" } : {}),
      ...(sameOrigin ? authHeaders() : {}),
      ...(!sameOrigin ? { "X-ChannelWatch-In-App-Report": "1" } : {}),
      ...(proofHeader ? { "X-ChannelWatch-Report-Challenge": proofHeader } : {}),
    },
    body,
    signal: options.signal,
  })

  if (!response.ok) {
    const errorPayload = await parseApiError(response)
    throw new ApiError(errorPayload)
  }

  return response.json()
}

export async function retryPrivateReport(
  endpoint: string,
  payload: ReportProblemPayload,
  attachments: ReportSubmissionAttachments,
  options: { supportCode: string; signal?: AbortSignal; onChallengeProgress?: (attempts: number) => void },
): Promise<ReportPreviewResponse> {
  const retryEndpoint = new URL("/api/reports/retry-private", endpoint).toString()
  return submitReport(retryEndpoint, payload, attachments, options)
}

export async function checkReportStatus(
  endpoint: string,
  supportCode: string,
  signal?: AbortSignal,
): Promise<ReportPreviewResponse> {
  const statusEndpoint = new URL("/api/reports/status", endpoint).toString()
  const response = await fetch(statusEndpoint, {
    method: "POST",
    credentials: "omit",
    headers: {
      "Content-Type": "application/json",
      "X-ChannelWatch-In-App-Report": "1",
    },
    body: JSON.stringify({ support_code: supportCode }),
    signal,
  })
  if (!response.ok) throw new ApiError(await parseApiError(response))
  return response.json()
}

export async function downloadOfflineReportPackage(
  payload: ReportProblemPayload,
  attachments: ReportSubmissionAttachments = {},
  supportCode: string,
): Promise<Blob> {
  const { body, hasAttachments } = buildReportBody(payload, attachments, {
    includeSupportCode: true,
    supportCode,
  })
  const response = await fetch(`${API_BASE}/v1/support/offline-package`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      ...(!hasAttachments ? { "Content-Type": "application/json" } : {}),
      ...authHeaders(),
    },
    body,
  })

  if (!response.ok) {
    const errorPayload = await parseApiError(response)
    throw new ApiError(errorPayload)
  }

  return response.blob()
}

export interface RestoreResult {
  message: string
  manifest: Record<string, unknown>
}

export async function restoreFromBackup(file: File): Promise<RestoreResult> {
  const body = new FormData()
  body.append("file", file)

  const response = await fetch(`${API_BASE}/v1/backup/restore`, {
    method: "POST",
    headers: authHeaders(),
    body,
  })

  if (!response.ok) {
    const payload = await parseApiError(response)
    throw new ApiError(payload)
  }

  return response.json()
}

export function pollForRecovery(options: PollForRecoveryOptions = {}): () => void {
  const interval = options.interval ?? 2000
  const initialDelay = options.initialDelay ?? 3000
  const timeout = options.timeout ?? 60000
  const minimumRecoveryMs = options.minimumRecoveryMs ?? 5000
  const startTime = Date.now()
  let timerId: ReturnType<typeof setTimeout> | null = null
  let cancelled = false

  const poll = () => {
    if (cancelled) return
    const elapsed = Date.now() - startTime
    options.onTick?.(elapsed)

    if (elapsed > timeout) {
      options.onTimeout?.()
      return
    }

    fetch(`/healthz/live`, { signal: AbortSignal.timeout(2000) })
      .then(async (res) => {
        if (cancelled) return
        if (!res.ok) {
          timerId = setTimeout(poll, interval)
          return
        }
        const startup = await fetch(`/healthz/startup`, { signal: AbortSignal.timeout(2000) })
        if (cancelled) return
        if (!startup.ok || elapsed < minimumRecoveryMs) {
          timerId = setTimeout(poll, interval)
          return
        }
        cancelled = true
        options.onRecovered?.()
      })
      .catch(() => {
        if (cancelled) return
        timerId = setTimeout(poll, interval)
      })
  }

  timerId = setTimeout(poll, initialDelay)

  return () => {
    cancelled = true
    if (timerId) clearTimeout(timerId)
  }
}

export interface NotificationDeliveryItem {
  id: number
  dvr_id: string
  activity_event_id: string | null
  provider_type: string
  channel_id: string
  channel: string
  event_type: string
  status: string
  retry_count: number
  payload_size: number
  error: string | null
  sent_at: string
}

export interface NotificationLogResponse {
  items: NotificationDeliveryItem[]
  total: number
  offset: number
  limit: number
}

export interface FetchNotificationLogOptions {
  dvr_id?: string
  channel?: string
  status?: string
  since?: string
  until?: string
  offset?: number
  limit?: number
}

export async function fetchNotificationLog(
  options: FetchNotificationLogOptions = {},
): Promise<NotificationLogResponse> {
  const params = new URLSearchParams()
  if (options.dvr_id) params.set("dvr_id", options.dvr_id)
  if (options.channel) params.set("channel", options.channel)
  if (options.status) params.set("status", options.status)
  if (options.since) params.set("since", options.since)
  if (options.until) params.set("until", options.until)
  if (options.offset != null) params.set("offset", String(options.offset))
  if (options.limit != null) params.set("limit", String(options.limit))
  const qs = params.toString()
  const response = await fetch(`${API_BASE}/v1/notification-log${qs ? `?${qs}` : ""}`, {
    headers: authHeaders(),
  })
  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }
  return response.json()
}
