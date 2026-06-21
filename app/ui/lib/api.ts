import type { AppSettings, AboutInfo, TestResult, SystemInfo, RecordingInfo, ActivityItem, SecurityStatus, PerDvrSystemInfo, AuthMode, AuthSetupStatus, WhoAmIResponse, EffectiveAuthMode } from "@/lib/types"
import { parseApiError, type ErrorPayload } from "@/lib/error-catalog"

const API_BASE = "/api"

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
  if (typeof sessionStorage === "undefined") {
    return ""
  }
  return sessionStorage.getItem("cw_api_key") || ""
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
    sessionStorage.removeItem("cw_api_key")
  }
  if (typeof localStorage !== "undefined") {
    localStorage.removeItem("cw_api_key")
  }
}

export function cacheApiKey(value: string) {
  if (typeof window === "undefined") {
    return
  }
  if (typeof sessionStorage === "undefined") {
    return
  }
  sessionStorage.setItem("cw_api_key", value)
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
    if (typeof window !== "undefined") {
      if (typeof sessionStorage !== "undefined") {
        sessionStorage.removeItem("cw_api_key")
      }
      if (typeof localStorage !== "undefined") {
        localStorage.removeItem("cw_api_key")
      }
    }
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
    throw new Error(`HTTP error ${response.status}`)
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
  onTick?: (elapsedMs: number) => void
  onRecovered?: () => void
  onTimeout?: () => void
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

    fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) })
      .then((res) => {
        if (cancelled) return
        if (res.ok) {
          options.onRecovered?.()
        } else {
          timerId = setTimeout(poll, interval)
        }
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
