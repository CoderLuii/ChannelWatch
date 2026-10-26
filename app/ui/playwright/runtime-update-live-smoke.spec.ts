import { createHash } from "node:crypto"
import { mkdir, stat, writeFile } from "node:fs/promises"
import { createServer } from "node:http"
import path from "node:path"

import { expect, test, type APIRequestContext, type APIResponse, type Page, type Request } from "@playwright/test"

const username = process.env.CHANNELWATCH_PLAYWRIGHT_ADMIN_USERNAME?.trim()
const password = process.env.CHANNELWATCH_PLAYWRIGHT_ADMIN_PASSWORD?.trim()
const targetVersion = process.env.CHANNELWATCH_PLAYWRIGHT_TARGET_VERSION?.trim()?.replace(/^v/, "")
const imageVersion = process.env.CHANNELWATCH_PLAYWRIGHT_IMAGE_VERSION?.trim()?.replace(/^v/, "")
const evidenceDirectory = process.env.CHANNELWATCH_PLAYWRIGHT_EVIDENCE_DIR?.trim()
const healthTimeoutMilliseconds = process.env.CHANNELWATCH_PLAYWRIGHT_HEALTH_TIMEOUT_MS?.trim()

type BrowserAuditPhase =
  | "unauthenticated_bootstrap"
  | "authenticated"
  | "apply_restart"
  | "rollback_restart"
  | "complete"

type ExpectedConsoleError =
  | { kind: "unauthenticated_bootstrap", endpoint: "/api/v1/auth/whoami" | "/api/settings" }
  | { kind: "restart_transport", phase: "apply_restart" | "rollback_restart", jobId: string }

const UNAUTHORIZED_RESOURCE_ERROR = "Failed to load resource: the server responded with a status of 401 (Unauthorized)"
const EXPECTED_RESTART_RESOURCE_ERRORS = new Set([
  "Failed to load resource: net::ERR_CONNECTION_RESET",
  "Failed to load resource: net::ERR_EMPTY_RESPONSE",
  "Failed to load resource: net::ERR_SOCKET_NOT_CONNECTED",
])

function expectedConsoleError(
  text: string,
  locationURL: string,
  phase: BrowserAuditPhase,
  expectedOrigin: string,
): ExpectedConsoleError | null {
  let url: URL
  try {
    url = new URL(locationURL)
  } catch {
    return null
  }
  if (url.origin !== expectedOrigin || url.search || url.hash) return null

  if (phase === "unauthenticated_bootstrap" && text === UNAUTHORIZED_RESOURCE_ERROR) {
    if (url.pathname === "/api/v1/auth/whoami" || url.pathname === "/api/settings") {
      return { kind: "unauthenticated_bootstrap", endpoint: url.pathname }
    }
    return null
  }

  if ((phase === "apply_restart" || phase === "rollback_restart")
    && EXPECTED_RESTART_RESOURCE_ERRORS.has(text)) {
    const match = /^\/api\/v1\/update\/jobs\/([0-9a-f]{32})$/.exec(url.pathname)
    if (match) return { kind: "restart_transport", phase, jobId: match[1] }
  }
  return null
}

function createBrowserConsoleAudit(page: Page, expectedOrigin: string, currentPhase: () => BrowserAuditPhase) {
  type Restart = { phase: "apply_restart" | "rollback_restart", jobId: string }
  type Problem = { text: string, url: string, phase: BrowserAuditPhase }
  type Tracked = {
    id: number, request: Request, phase: BrowserAuditPhase, restart: Restart | null, owned: boolean,
    failure?: { text: string, sequence: number }, status?: number, finished?: number, consoleMatched?: boolean,
  }
  type ConsoleRecord = Problem & { expected: ExpectedConsoleError | null, request?: Tracked }
  const requests = new Map<Request, Tracked>()
  const consoles: ConsoleRecord[] = []
  const pageErrors: Problem[] = []
  let sequence = 0
  let restart: Restart | null = null
  let activeRestart: Restart | null = null
  const isStatusGet = (request: Request) => {
    const url = new URL(request.url())
    return request.method() === "GET" && url.origin === expectedOrigin
      && url.pathname === "/api/v1/update/status" && !url.search && !url.hash
  }
  const matchFailures = () => {
    for (const record of consoles) {
      if (record.expected || record.request) continue
      const candidates = [...requests.values()].filter((tracked) => !tracked.consoleMatched
        && tracked.failure && tracked.request.url() === record.url
        && record.text === `Failed to load resource: ${tracked.failure.text}`)
      if (candidates.length !== 1) continue
      const tracked = candidates[0]
      const matchingConsole = consoles.filter((other) => !other.expected && !other.request
        && other.url === record.url && other.text === record.text)
      if (matchingConsole.length !== 1) continue
      tracked.consoleMatched = true
      record.request = tracked
    }
  }
  page.on("request", (request) => {
    const phase = currentPhase()
    requests.set(request, { id: ++sequence, request, phase, restart,
      owned: isStatusGet(request) && restart !== null && activeRestart === restart && restart.phase === phase })
  })
  page.on("requestfailed", (request) => {
    const tracked = requests.get(request)
    if (tracked) tracked.failure = { text: request.failure()?.errorText ?? "unknown transport failure", sequence: ++sequence }
    matchFailures()
  })
  page.on("response", (response) => {
    const tracked = requests.get(response.request())
    if (tracked) tracked.status = response.status()
  })
  page.on("requestfinished", (request) => {
    const tracked = requests.get(request)
    if (tracked) tracked.finished = ++sequence
  })
  page.on("console", (message) => {
    if (message.type() !== "error") return
    const text = message.text().slice(0, 500)
    const url = message.location().url
    const phase = currentPhase()
    consoles.push({ text, url, phase, expected: expectedConsoleError(text, url, phase, expectedOrigin) })
    matchFailures()
  })
  page.on("pageerror", (error) => pageErrors.push({ text: error.message.slice(0, 500), url: page.url(), phase: currentPhase() }))
  const acknowledge = (phase: Restart["phase"], jobId: string) => {
    if (currentPhase() !== phase || !/^[0-9a-f]{32}$/.test(jobId)) throw new Error("Restart acknowledgement is not bound to an owned job and phase.")
    restart = { phase, jobId }
    activeRestart = restart
  }
  return {
    requestId(request: Request) { return requests.get(request)?.id },
    acknowledge,
    endRestart() { activeRestart = null },
    acknowledgedRestart() { return activeRestart },
    observeApplyAcknowledgements(version: string, ownedJobId: () => string | null) {
      page.on("response", (response) => {
        const request = response.request()
        const url = new URL(response.url())
        if (request.method() !== "POST" || response.status() !== 202 || url.origin !== expectedOrigin
          || url.pathname !== "/api/v1/update/apply" || url.search || url.hash || currentPhase() !== "apply_restart") return
        void response.json().then((payload) => {
          if (currentPhase() !== "apply_restart" || !payload || typeof payload !== "object"
            || typeof payload.job_id !== "string" || !/^[0-9a-f]{32}$/.test(payload.job_id)
            || payload.job_id !== ownedJobId() || payload.job_id !== request.postDataJSON()?.job_id
            || payload.operation !== "apply" || payload.status !== "restarting"
            || payload.version !== version || payload.restart_required !== true) return
          acknowledge("apply_restart", payload.job_id)
        }).catch(() => { /* An absent or unreadable reply authorizes no exception. */ })
      })
    },
    report() {
      const expected: ExpectedConsoleError[] = []
      const unexpected: (Problem & { requestId?: number, requestPhase?: BrowserAuditPhase, reason?: string })[] = []
      const acceptedStatusRequests: { requestId: number, recoveryRequestId: number, phase: Restart["phase"], jobId: string, url: string }[] = []
      for (const record of consoles) {
        if (record.expected) { expected.push(record.expected); continue }
        const tracked = record.request
        const recovery = tracked?.failure && [...requests.values()].find((candidate) => candidate.id > tracked.failure!.sequence
          && candidate.restart === tracked.restart && candidate.request.url() === tracked.request.url()
          && isStatusGet(candidate.request) && candidate.status === 200 && candidate.finished && !candidate.failure)
        if (tracked?.owned && tracked.restart && tracked.failure?.text === "net::ERR_CONNECTION_RESET" && recovery) {
          expected.push({ kind: "restart_transport", phase: tracked.restart.phase, jobId: tracked.restart.jobId })
          acceptedStatusRequests.push({ requestId: tracked.id, recoveryRequestId: recovery.id, phase: tracked.restart.phase, jobId: tracked.restart.jobId, url: record.url })
        } else {
          unexpected.push({ text: record.text, url: record.url, phase: record.phase,
            requestId: tracked?.id, requestPhase: tracked?.phase,
            reason: !tracked ? "No unique matching requestfailed event." : !tracked.owned ? "Request did not start during an acknowledged owned restart." : !recovery ? "No later completed HTTP200 recovery for this restart." : "Transport failure is not an owned status reset." })
        }
      }
      return { expected, unexpected, pageErrors, acceptedStatusRequests }
    },
  }
}

function required(value: string | undefined, name: string): string {
  if (!value) throw new Error(`${name} is required for the live runtime update proof.`)
  return value
}

function requiredPositiveInteger(value: string | undefined, name: string): number {
  const raw = required(value, name)
  if (!/^[1-9][0-9]*$/.test(raw)) {
    throw new Error(`${name} must be a positive integer.`)
  }
  const parsed = Number(raw)
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${name} must be a safe positive integer.`)
  }
  return parsed
}

async function sha256(file: string) {
  const data = await import("node:fs/promises").then(({ readFile }) => readFile(file))
  return createHash("sha256").update(data).digest("hex")
}

async function waitForUpdateCenterOutcome(page: Page, action: string) {
  const timeout = 10 * 60_000
  const errorAlert = page.getByRole("alert").filter({ hasText: "Update Center error" })
  await Promise.race([
    page.waitForURL((url) => url.hash === "#overview", { timeout }),
    errorAlert.waitFor({ state: "visible", timeout }).then(async () => {
      throw new Error(`${action} failed: ${(await errorAlert.innerText()).slice(0, 1_000)}`)
    }),
  ])
}

async function currentTopDocumentLoads(page: Page): Promise<number | null> {
  try {
    return Number(await page.evaluate(
      () => window.sessionStorage.getItem("channelwatch:runtime-live-document-loads") ?? "0",
    ))
  } catch (error) {
    if (error instanceof Error && /Execution context was destroyed|Cannot find context with specified id/i.test(error.message)) {
      return null
    }
    throw error
  }
}

const PENDING_ROLLBACK_STATUSES = new Set(["restarting", "validating"])
const FAILED_ROLLBACK_STATUSES = new Set(["failed", "rejected", "rollback", "rolled_back"])

function isExpectedRestartTransportError(error: unknown): boolean {
  return error instanceof Error
    && /\bECONNRESET\b|\bECONNREFUSED\b|socket hang up|ERR_CONNECTION_RESET|ERR_CONNECTION_REFUSED|apiRequestContext\.get: Timeout \d+ms exceeded/i.test(error.message)
}

async function restartJson(responsePromise: Promise<APIResponse>): Promise<Record<string, unknown> | null> {
  let response: APIResponse
  try {
    response = await responsePromise
  } catch (error) {
    if (isExpectedRestartTransportError(error)) return null
    throw error
  }
  if ([502, 503, 504].includes(response.status())) return null
  expect(response.status()).toBe(200)
  return await response.json() as Record<string, unknown>
}

type JsonHttpResponse = {
  status(): number
  json(): Promise<unknown>
}

type QueuedRollbackJob = {
  job_id: string
  operation: "rollback"
  status: "restarting"
  version: string
  rolled_back_from: string
  restart_required: true
}

async function queuedRollbackJob(
  response: JsonHttpResponse,
  expectedVersion: string,
): Promise<QueuedRollbackJob> {
  if (response.status() !== 202) {
    throw new Error(`Roll back request expected HTTP 202, received ${response.status()}.`)
  }

  const payload = await response.json()
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("Roll back request returned a malformed job payload.")
  }
  const job = payload as Record<string, unknown>
  if (typeof job.job_id !== "string" || !/^[0-9a-f]{32}$/.test(job.job_id)) {
    throw new Error("Roll back request returned an invalid job ID.")
  }
  if (job.operation !== "rollback") {
    throw new Error("Roll back request returned a job for another operation.")
  }
  if (job.status !== "restarting") {
    throw new Error("Roll back request did not enter the restarting state.")
  }
  if (job.version !== expectedVersion || job.rolled_back_from !== expectedVersion) {
    throw new Error("Roll back request returned a job for another version.")
  }
  if (job.restart_required !== true) {
    throw new Error("Roll back request did not require the expected restart.")
  }
  return job as QueuedRollbackJob
}

type ExactHealth = {
  startup: { status: "ready" }
  ready: { status: "ready", ready: true }
}

function isExactRecord(value: unknown, expected: Record<string, unknown>): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  const keys = Object.keys(record)
  return keys.length === Object.keys(expected).length
    && Object.entries(expected).every(([key, expectedValue]) => record[key] === expectedValue)
}

async function healthEndpointState(
  request: APIRequestContext,
  url: string,
  expectedReady: Record<string, unknown>,
  expectedPending: Record<string, unknown>,
  requestTimeout: number,
): Promise<"ready" | "pending"> {
  let response: APIResponse
  try {
    response = await request.get(url, { timeout: requestTimeout })
  } catch (error) {
    if (isExpectedRestartTransportError(error)) return "pending"
    throw error
  }

  if ([502, 504].includes(response.status())) return "pending"
  const payload = await response.json() as unknown
  if (response.status() === 503) {
    if (isExactRecord(payload, expectedPending)) return "pending"
    throw new Error(`${url} returned an unexpected HTTP 503 payload.`)
  }
  if (response.status() !== 200) {
    throw new Error(`${url} expected HTTP 200, received ${response.status()}.`)
  }
  if (!isExactRecord(payload, expectedReady)) {
    throw new Error(`${url} returned an unexpected ready payload.`)
  }
  return "ready"
}

async function waitForExactHealth(
  request: APIRequestContext,
  options: { baseURL?: string, timeout?: number, interval?: number } = {},
): Promise<ExactHealth> {
  const baseURL = options.baseURL ?? ""
  const timeout = options.timeout ?? 60_000
  const interval = options.interval ?? 1_000
  const deadline = Date.now() + timeout

  while (Date.now() < deadline) {
    const startupRequestTimeout = Math.min(10_000, Math.max(1, deadline - Date.now()))
    const startup = await healthEndpointState(
      request,
      `${baseURL}/healthz/startup`,
      { status: "ready" },
      { status: "not_ready" },
      startupRequestTimeout,
    )
    if (Date.now() >= deadline) break
    const readyRequestTimeout = Math.min(10_000, Math.max(1, deadline - Date.now()))
    const ready = await healthEndpointState(
      request,
      `${baseURL}/healthz/ready`,
      { status: "ready", ready: true },
      { status: "degraded", ready: false },
      readyRequestTimeout,
    )
    if (startup === "ready" && ready === "ready") {
      return {
        startup: { status: "ready" },
        ready: { status: "ready", ready: true },
      }
    }
    const wait = Math.min(interval, Math.max(0, deadline - Date.now()))
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait))
  }
  throw new Error(`ChannelWatch did not reach exact startup and monitoring readiness within ${timeout}ms.`)
}

test("runtime polling enforces restart, rollback, and exact readiness responses", async ({ request }) => {
  expect(requiredPositiveInteger("180000", "health timeout")).toBe(180_000)
  for (const invalid of [undefined, "", "0", "-1", "1.5", "NaN", "9007199254740992"]) {
    expect(() => requiredPositiveInteger(invalid, "health timeout")).toThrow()
  }

  const origin = "http://127.0.0.1:8123"
  expect(expectedConsoleError(
    UNAUTHORIZED_RESOURCE_ERROR,
    `${origin}/api/v1/auth/whoami`,
    "unauthenticated_bootstrap",
    origin,
  )).toEqual({ kind: "unauthenticated_bootstrap", endpoint: "/api/v1/auth/whoami" })
  expect(expectedConsoleError(
    UNAUTHORIZED_RESOURCE_ERROR,
    `${origin}/api/settings`,
    "unauthenticated_bootstrap",
    origin,
  )).toEqual({ kind: "unauthenticated_bootstrap", endpoint: "/api/settings" })
  expect(expectedConsoleError(
    "Failed to load resource: net::ERR_CONNECTION_RESET",
    `${origin}/api/v1/update/jobs/${"a".repeat(32)}`,
    "apply_restart",
    origin,
  )).toEqual({ kind: "restart_transport", phase: "apply_restart", jobId: "a".repeat(32) })
  expect(expectedConsoleError(
    "Failed to load resource: net::ERR_SOCKET_NOT_CONNECTED",
    `${origin}/api/v1/update/jobs/${"b".repeat(32)}`,
    "rollback_restart",
    origin,
  )).toEqual({ kind: "restart_transport", phase: "rollback_restart", jobId: "b".repeat(32) })
  const capturedRollbackJobId = "6d7041b9beaa49ccb1ffd1eed2052dfe"
  expect(expectedConsoleError(
    "Failed to load resource: net::ERR_EMPTY_RESPONSE",
    `${origin}/api/v1/update/jobs/${capturedRollbackJobId}`,
    "rollback_restart",
    origin,
  )).toEqual({ kind: "restart_transport", phase: "rollback_restart", jobId: capturedRollbackJobId })
  for (const [text, url, phase, expectedOrigin] of [
    [UNAUTHORIZED_RESOURCE_ERROR, `${origin}/api/settings`, "authenticated", origin],
    [UNAUTHORIZED_RESOURCE_ERROR, `${origin}/api/v1/update/status`, "unauthenticated_bootstrap", origin],
    [UNAUTHORIZED_RESOURCE_ERROR, `http://127.0.0.1:8124/api/settings`, "unauthenticated_bootstrap", origin],
    ["Failed to load resource: net::ERR_CONNECTION_REFUSED", `${origin}/api/v1/update/jobs/${"a".repeat(32)}`, "apply_restart", origin],
    ["Failed to load resource: net::ERR_CONNECTION_RESET", `${origin}/api/v1/update/status`, "apply_restart", origin],
    ["Failed to load resource: net::ERR_CONNECTION_RESET", `${origin}/api/v1/update/jobs/${"a".repeat(32)}?extra=true`, "apply_restart", origin],
    ["Failed to load resource: net::ERR_CONNECTION_RESET", `http://127.0.0.1:8124/api/v1/update/jobs/${"a".repeat(32)}`, "apply_restart", origin],
    ["Failed to load resource: net::ERR_EMPTY_RESPONSE", `${origin}/api/v1/update/jobs/${capturedRollbackJobId}`, "authenticated", origin],
    ["Failed to load resource: net::ERR_EMPTY_RESPONSE", `${origin}/api/v1/update/status`, "rollback_restart", origin],
    ["Failed to load resource: net::ERR_EMPTY_RESPONSE", `${origin}/api/v1/update/jobs/${capturedRollbackJobId}?extra=true`, "rollback_restart", origin],
    ["Failed to load resource: net::ERR_EMPTY_RESPONSE", `http://127.0.0.1:8124/api/v1/update/jobs/${capturedRollbackJobId}`, "rollback_restart", origin],
    ["Failed to load resource: net::ERR_EMPTY_RESPONSE", `${origin}/api/v1/update/jobs/${"c".repeat(31)}`, "rollback_restart", origin],
    ["Failed to load resource: net::ERR_EMPTY_RESPONSE", `${origin}/api/v1/update/jobs/${"D".repeat(32)}`, "rollback_restart", origin],
  ] as const) {
    expect(expectedConsoleError(text, url, phase, expectedOrigin)).toBeNull()
  }

  let mode: "transition" | "persistent" | "extra" | "malformed" | "unauthorized" = "transition"
  let startupAttempts = 0
  let readyAttempts = 0
  const server = createServer((incoming, response) => {
    response.setHeader("content-type", "application/json")
    if (incoming.url === "/malformed") {
      response.writeHead(200)
      response.end("ECONNRESET")
      return
    }
    if (incoming.url === "/healthz/startup") {
      startupAttempts += 1
      const pending = mode === "transition" && startupAttempts === 1
      response.writeHead(pending ? 503 : 200)
      response.end(JSON.stringify(pending ? { status: "not_ready" } : { status: "ready" }))
      return
    }
    if (incoming.url === "/healthz/ready") {
      readyAttempts += 1
      if (mode === "unauthorized") {
        response.writeHead(401)
        response.end(JSON.stringify({ detail: "unauthorized" }))
        return
      }
      if (mode === "malformed") {
        response.writeHead(503)
        response.end("not-json")
        return
      }
      if (mode === "extra") {
        response.writeHead(503)
        response.end(JSON.stringify({ status: "degraded", ready: false, unexpected: true }))
        return
      }
      if (mode === "persistent") {
        response.writeHead(503)
        response.end(JSON.stringify({ status: "degraded", ready: false }))
        return
      }
      if (readyAttempts === 1) {
        response.writeHead(502)
        response.end()
        return
      }
      if (readyAttempts === 2) {
        response.writeHead(504)
        response.end()
        return
      }
      const pending = readyAttempts === 3
      response.writeHead(pending ? 503 : 200)
      response.end(JSON.stringify(pending
        ? { status: "degraded", ready: false }
        : { status: "ready", ready: true }))
      return
    }
    response.writeHead(404)
    response.end(JSON.stringify({ detail: "not found" }))
  })
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", resolve)
  })
  try {
    const address = server.address()
    if (!address || typeof address === "string") throw new Error("Ephemeral test server did not expose a TCP address.")
    const baseURL = `http://127.0.0.1:${address.port}`
    await expect(restartJson(request.get(`${baseURL}/malformed`))).rejects.toThrow()

    await expect(waitForExactHealth(request, { baseURL, timeout: 1_000, interval: 1 })).resolves.toEqual({
      startup: { status: "ready" },
      ready: { status: "ready", ready: true },
    })
    expect(startupAttempts).toBe(4)
    expect(readyAttempts).toBe(4)

    mode = "persistent"
    await expect(waitForExactHealth(request, { baseURL, timeout: 25, interval: 1 })).rejects.toThrow(
      /did not reach exact startup and monitoring readiness/,
    )
    mode = "extra"
    await expect(waitForExactHealth(request, { baseURL, timeout: 1_000, interval: 1 })).rejects.toThrow(
      /unexpected HTTP 503 payload/,
    )
    mode = "malformed"
    await expect(waitForExactHealth(request, { baseURL, timeout: 1_000, interval: 1 })).rejects.toThrow(SyntaxError)
    mode = "unauthorized"
    await expect(waitForExactHealth(request, { baseURL, timeout: 1_000, interval: 1 })).rejects.toThrow(
      /expected HTTP 200, received 401/,
    )
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  }

  const accepted = {
    job_id: "a".repeat(32),
    operation: "rollback",
    status: "restarting",
    version: "1.6.0",
    rolled_back_from: "1.6.0",
    restart_required: true,
  }
  const response = (status: number, payload: unknown): JsonHttpResponse => ({
    status: () => status,
    json: async () => payload,
  })

  await expect(queuedRollbackJob(response(202, accepted), "1.6.0")).resolves.toEqual(accepted)
  await expect(queuedRollbackJob(response(200, accepted), "1.6.0")).rejects.toThrow(/expected HTTP 202/)
  await expect(queuedRollbackJob(
    response(202, { ...accepted, job_id: "foreign-job" }),
    "1.6.0",
  )).rejects.toThrow(/invalid job ID/)
  await expect(queuedRollbackJob(
    response(202, { ...accepted, operation: "apply" }),
    "1.6.0",
  )).rejects.toThrow(/another operation/)
  await expect(queuedRollbackJob(
    response(202, { ...accepted, status: "validating" }),
    "1.6.0",
  )).rejects.toThrow(/restarting state/)
  await expect(queuedRollbackJob(
    response(202, { ...accepted, version: "1.5.3", rolled_back_from: "1.5.3" }),
    "1.6.0",
  )).rejects.toThrow(/another version/)
  await expect(queuedRollbackJob(
    response(202, { ...accepted, restart_required: false }),
    "1.6.0",
  )).rejects.toThrow(/expected restart/)
  await expect(queuedRollbackJob(
    { status: () => 202, json: async () => { throw new SyntaxError("Unexpected token") } },
    "1.6.0",
  )).rejects.toThrow(SyntaxError)
})

async function waitForRollbackOutcome(
  page: Page,
  expected: { jobId: string, restoredVersion: string, rolledBackFrom: string },
) {
  const timeout = 10 * 60_000
  const deadline = Date.now() + timeout
  const errorAlert = page.getByRole("alert").filter({ hasText: "Update Center error" })
  let lastRestartOutage = "none"

  while (Date.now() < deadline) {
    if (await errorAlert.isVisible()) {
      throw new Error(`Roll back failed: ${(await errorAlert.innerText()).slice(0, 1_000)}`)
    }

    const [job, status] = await Promise.all([
      restartJson(page.request.get(`/api/v1/update/jobs/${expected.jobId}`, { timeout: 10_000 })),
      restartJson(page.request.get("/api/v1/update/status", { timeout: 10_000 })),
    ])
    if (!job || !status) {
      lastRestartOutage = !job && !status ? "job and status unavailable" : !job ? "job unavailable" : "status unavailable"
      await page.waitForTimeout(1_000)
      continue
    }

    expect(job.job_id).toBe(expected.jobId)
    expect(job.operation).toBe("rollback")
    expect(job.version).toBe(expected.rolledBackFrom)
    expect(job.rolled_back_from).toBe(expected.rolledBackFrom)
    const jobStatus = String(job.status ?? "")
    if (FAILED_ROLLBACK_STATUSES.has(jobStatus)) {
      throw new Error(`Roll back job failed: ${String(job.message ?? jobStatus)}`)
    }
    if (PENDING_ROLLBACK_STATUSES.has(jobStatus)) {
      await page.waitForTimeout(1_000)
      continue
    }
    expect(jobStatus).toBe("success")
    expect(job.restored_version).toBe(expected.restoredVersion)
    expect(job.rollback_applied).toBe(true)
    expect(job.restart_required).toBe(false)

    expect(status.current_version).toBe(expected.restoredVersion)
    expect(status.runtime_source).toBe("image")
    expect(status.active_bundle).toBeNull()
    expect(status.operation_busy).toBe(false)
    expect(status.last_job).toMatchObject({
      job_id: expected.jobId,
      operation: "rollback",
      status: "success",
      version: expected.rolledBackFrom,
      rolled_back_from: expected.rolledBackFrom,
      restored_version: expected.restoredVersion,
      rollback_applied: true,
    })
    return { job, status }
  }

  throw new Error(`Roll back did not reach its exact restored runtime within ${timeout}ms; last outage: ${lastRestartOutage}`)
}

for (const scenario of ["rollback", "apply", "unobserved_apply_ack", "steady", "unacknowledged", "started_before_ack", "stale_ack", "wrong_origin", "query", "method", "static", "unrecovered", "javascript", "pageerror"] as const) {
  test(`status reset audit binds request identity and recovery (${scenario})`, async ({ page }) => {
    const jobId = "e".repeat(32)
    const operation = scenario === "apply" || scenario === "unobserved_apply_ack" ? "apply" : "rollback"
    const restartPhase = operation === "apply" ? "apply_restart" : "rollback_restart"
    const restoredVersion = operation === "apply" ? "1.6.2" : "1.5.3"
    const runtimeSource = operation === "apply" ? "app_bundle" : "image"
    let phase: BrowserAuditPhase = "authenticated"
    let mode: "hold" | "reset" | "ready" = "hold"
    let signalHeld: () => void = () => {}
    const held = new Promise<void>((resolve) => { signalHeld = resolve })
    let resetHeld: () => void = () => { throw new Error("No status request is held.") }
    const server = createServer((incoming, response) => {
      const pathname = incoming.url?.split("?")[0]
      if (pathname === `/api/v1/update/${operation}` && incoming.method === "POST") {
        response.writeHead(202, { "content-type": "application/json" })
        response.end(JSON.stringify({ job_id: jobId, operation, status: "restarting", version: "1.6.2", rolled_back_from: "1.6.2", restart_required: true }))
      } else if ((pathname === "/api/v1/update/status" || pathname === "/asset.js") && mode !== "ready") {
        resetHeld = () => incoming.socket.resetAndDestroy()
        signalHeld()
        if (mode === "reset") resetHeld()
      } else if (pathname === "/api/v1/update/status") {
        response.writeHead(200, { "content-type": "application/json" })
        response.end(JSON.stringify({ current_version: restoredVersion, runtime_source: runtimeSource, operation_busy: false, last_job: { job_id: jobId, operation, status: "success", rolled_back_from: "1.6.2" } }))
      } else if (incoming.url === "/healthz/startup" || incoming.url === "/healthz/ready") {
        response.writeHead(200, { "content-type": "application/json" })
        response.end(JSON.stringify(incoming.url === "/healthz/startup" ? { status: "ready" } : { status: "ready", ready: true }))
      } else {
        response.writeHead(200, { "content-type": "text/html" })
        response.end("<!doctype html><title>Local restart audit fixture</title>")
      }
    })
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject)
      server.listen(0, "127.0.0.1", resolve)
    })
    try {
      const address = server.address()
      if (!address || typeof address === "string") throw new Error("Local fixture has no TCP address.")
      const origin = `http://127.0.0.1:${address.port}`
      const audit = createBrowserConsoleAudit(page, scenario === "wrong_origin" ? "http://127.0.0.1:1" : origin, () => phase)
      audit.observeApplyAcknowledgements("1.6.2", () => jobId)
      await page.goto(origin)
      phase = restartPhase
      const response = scenario === "apply"
        ? await page.evaluate(async ({ url, id }) => {
          const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ job_id: id }) })
          return { status: response.status, payload: await response.json() }
        }, { url: `${origin}/api/v1/update/apply`, id: jobId })
        : await page.request.post(`${origin}/api/v1/update/${operation}`)
      const accepted = "payload" in response ? response.payload
        : operation === "rollback" ? await queuedRollbackJob(response, "1.6.2") : await response.json()
      expect("payload" in response ? response.status : response.status()).toBe(202)
      expect(accepted).toMatchObject({ job_id: jobId, operation, status: "restarting", restart_required: true })
      expect(accepted.job_id).toBe(jobId)
      if (scenario === "apply") await expect.poll(() => audit.acknowledgedRestart()?.jobId).toBe(jobId)
      else if (scenario !== "unacknowledged" && scenario !== "started_before_ack" && scenario !== "unobserved_apply_ack") audit.acknowledge(restartPhase, accepted.job_id)
      if (scenario === "steady" || scenario === "started_before_ack") phase = "authenticated"
      if (scenario === "stale_ack") { phase = "authenticated"; audit.endRestart(); phase = restartPhase }
      const failedURL = `${origin}${scenario === "static" ? "/asset.js" : "/api/v1/update/status"}${scenario === "query" ? "?unexpected=1" : ""}`
      const failed = page.waitForEvent("requestfailed", (request) => request.url() === failedURL)
      const consoleFailure = page.waitForEvent("console", (message) => message.type() === "error" && message.location().url === failedURL)
      await page.evaluate(({ url, method }) => { void fetch(url, { method }).catch(() => null) }, { url: failedURL, method: scenario === "method" ? "POST" : "GET" })
      await held
      if (scenario === "started_before_ack") { phase = restartPhase; audit.acknowledge(restartPhase, accepted.job_id) }
      mode = "reset"
      resetHeld()
      const failedRequest = await failed
      expect(failedRequest.failure()?.errorText).toBe("net::ERR_CONNECTION_RESET")
      await consoleFailure
      mode = "ready"
      const recovered = scenario === "unrecovered"
        ? await (await page.request.get(`${origin}/api/v1/update/status`)).json()
        : await page.evaluate(async (url) => (await fetch(url)).json(), `${origin}/api/v1/update/status`)
      expect(recovered).toMatchObject({ current_version: restoredVersion, runtime_source: runtimeSource, operation_busy: false, last_job: { job_id: jobId, operation, status: "success", rolled_back_from: "1.6.2" } })
      await expect(waitForExactHealth(page.request, { baseURL: origin, timeout: 1_000, interval: 1 })).resolves.toEqual({ startup: { status: "ready" }, ready: { status: "ready", ready: true } })
      if (scenario === "javascript") await page.evaluate(() => console.error("Deliberate local JavaScript console error"))
      if (scenario === "pageerror") {
        const error = page.waitForEvent("pageerror")
        await page.evaluate(() => { setTimeout(() => { throw new Error("Deliberate local page error") }, 0) })
        await error
      }
      if (scenario === "rollback" || scenario === "apply") {
        await expect.poll(() => audit.report().acceptedStatusRequests.length).toBe(1)
        expect(audit.report().acceptedStatusRequests[0]).toMatchObject({ phase: restartPhase, jobId, url: `${origin}/api/v1/update/status`, requestId: audit.requestId(failedRequest) })
        expect(audit.report().unexpected).toEqual([])
        expect(audit.report().pageErrors).toEqual([])
      } else if (scenario === "pageerror") {
        expect(audit.report().pageErrors).toHaveLength(1)
      } else {
        expect(audit.report().unexpected.length).toBeGreaterThan(0)
        expect(audit.report().unexpected[0]).toHaveProperty("url")
        expect(audit.report().unexpected[0]).toHaveProperty("phase")
      }
    const fixtureEvidence = test.info().outputPath("status-reset-audit.json")
    await mkdir(path.dirname(fixtureEvidence), { recursive: true })
    await writeFile(fixtureEvidence, `${JSON.stringify({ fixture_scenario: scenario, expected_classification: scenario === "rollback" || scenario === "apply" ? "owned_restart_reset" : "fatal_error", audit: audit.report() }, null, 2)}\n`, "utf8")
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
    }
  })
}

test("authenticated live Update Center applies and rolls back the exact candidate once", async ({ page }) => {
  const adminUsername = required(username, "CHANNELWATCH_PLAYWRIGHT_ADMIN_USERNAME")
  const adminPassword = required(password, "CHANNELWATCH_PLAYWRIGHT_ADMIN_PASSWORD")
  const expectedVersion = required(targetVersion, "CHANNELWATCH_PLAYWRIGHT_TARGET_VERSION")
  const expectedImageVersion = required(imageVersion, "CHANNELWATCH_PLAYWRIGHT_IMAGE_VERSION")
  const evidenceRoot = required(evidenceDirectory, "CHANNELWATCH_PLAYWRIGHT_EVIDENCE_DIR")
  const healthTimeout = requiredPositiveInteger(
    healthTimeoutMilliseconds,
    "CHANNELWATCH_PLAYWRIGHT_HEALTH_TIMEOUT_MS",
  )
  const expectedOrigin = new URL(required(
    process.env.CHANNELWATCH_PLAYWRIGHT_BASE_URL?.trim(),
    "CHANNELWATCH_PLAYWRIGHT_BASE_URL",
  )).origin
  await mkdir(evidenceRoot, { recursive: true })
  await page.addInitScript(() => {
    if (window.top !== window) return
    const key = "channelwatch:runtime-live-document-loads"
    const loads = Number(window.sessionStorage.getItem(key) ?? "0")
    window.sessionStorage.setItem(key, String(loads + 1))
  })

  let auditPhase: BrowserAuditPhase = "unauthenticated_bootstrap"
  const consoleAudit = createBrowserConsoleAudit(page, expectedOrigin, () => auditPhase)
  let applyRequests = 0
  let rollbackRequests = 0
  let applyJobId: string | null = null
  let observedProgress = false
  consoleAudit.observeApplyAcknowledgements(expectedVersion, () => applyJobId)
  page.on("request", (request) => {
    if (request.method() !== "POST") return
    if (request.url().endsWith("/api/v1/update/apply")) {
      applyRequests += 1
      const body = request.postDataJSON() as { job_id?: unknown }
      applyJobId = typeof body.job_id === "string" ? body.job_id : null
    }
    if (request.url().endsWith("/api/v1/update/rollback")) rollbackRequests += 1
  })

  await page.goto("/#settings:updates")
  await expect(page.getByTestId("auth-login-shell")).toBeVisible()
  await page.getByLabel("Username").fill(adminUsername)
  await page.getByLabel("Password").fill(adminPassword)
  await page.getByRole("button", { name: "Sign in" }).click()
  await expect(page.getByTestId("auth-login-shell")).toHaveCount(0)
  auditPhase = "authenticated"

  await page.goto("/#settings:updates")
  const checkResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/update/check") && response.request().method() === "POST",
  )
  await page.getByRole("button", { name: "Check for updates" }).click()
  const check = await (await checkResponsePromise).json()
  expect(check.update_available).toBe(true)
  expect(check.image_required).toBe(false)
  expect(check.current_version).toBe(expectedImageVersion)
  expect(check.runtime_source).toBe("image")
  expect(check.trusted_target?.version).toBe(expectedVersion)
  await expect(page.getByText(`v${expectedVersion}`, { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Apply update" })).toBeEnabled()
  const loadsBeforeApply = await currentTopDocumentLoads(page)
  expect(loadsBeforeApply).not.toBeNull()

  auditPhase = "apply_restart"
  await page.getByRole("button", { name: "Apply update" }).click()
  const progress = page.getByText(/Downloading|Restarting/, { exact: true })
  await expect(progress.first()).toBeVisible({ timeout: 30_000 })
  observedProgress = true
  await waitForUpdateCenterOutcome(page, "Apply update")
  await expect.poll(
    () => currentTopDocumentLoads(page),
    { timeout: 60_000, message: "The updated top-level document did not finish its hard reload." },
  ).toBeGreaterThan(loadsBeforeApply!)

  expect(applyJobId).toMatch(/^[0-9a-f]{32}$/)
  const selectedResponse = await page.request.get("/api/v1/update/status")
  expect(selectedResponse.status()).toBe(200)
  const selectedStatus = await selectedResponse.json()
  expect(selectedStatus.current_version).toBe(expectedVersion)
  expect(selectedStatus.runtime_source).toBe("app_bundle")
  expect(selectedStatus.active_bundle?.version).toBe(expectedVersion)
  expect(selectedStatus.operation_busy).toBe(false)
  expect(selectedStatus.last_job).toMatchObject({
    job_id: applyJobId,
    operation: "apply",
    status: "success",
    version: expectedVersion,
  })
  consoleAudit.endRestart()
  auditPhase = "authenticated"

  await page.goto("/#settings:updates")
  await expect(page).toHaveURL(/#settings:updates$/)
  await expect(page.getByTestId("auth-login-shell")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Roll back" })).toBeEnabled({ timeout: 60_000 })
  const selectedScreenshot = path.join(evidenceRoot, "runtime-update-selected.png")
  await page.screenshot({ path: selectedScreenshot, fullPage: true })
  const loadsBeforeRollback = await currentTopDocumentLoads(page)
  expect(loadsBeforeRollback).not.toBeNull()

  const rollbackResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/update/rollback") && response.request().method() === "POST",
  )
  auditPhase = "rollback_restart"
  await page.getByRole("button", { name: "Roll back" }).click()
  const rollbackJob = await queuedRollbackJob(await rollbackResponsePromise, expectedVersion)
  consoleAudit.acknowledge("rollback_restart", rollbackJob.job_id)

  const [{ status: finalStatus }] = await Promise.all([
    waitForRollbackOutcome(page, {
      jobId: rollbackJob.job_id,
      restoredVersion: expectedImageVersion,
      rolledBackFrom: expectedVersion,
    }),
    waitForUpdateCenterOutcome(page, "Roll back"),
  ])
  await expect.poll(
    () => currentTopDocumentLoads(page),
    { timeout: 60_000 },
  ).toBeGreaterThan(loadsBeforeRollback!)
  const finalHealth = await waitForExactHealth(page.request, { timeout: healthTimeout })
  consoleAudit.endRestart()
  auditPhase = "authenticated"
  await page.goto("/#settings:updates")
  await expect(page).toHaveURL(/#settings:updates$/)
  await expect(page.getByTestId("auth-login-shell")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeEnabled({ timeout: 60_000 })
  await expect(page.getByRole("alert").filter({ hasText: "Update Center error" })).toHaveCount(0)
  const rollbackScreenshot = path.join(evidenceRoot, "runtime-update-rollback.png")
  await page.screenshot({ path: rollbackScreenshot, fullPage: true })

  expect(applyRequests).toBe(1)
  expect(rollbackRequests).toBe(1)
  expect(observedProgress).toBe(true)
  const { unexpected: consoleErrors, expected: expectedConsoleErrors, pageErrors } = consoleAudit.report()
  for (const expected of expectedConsoleErrors) {
    if (expected.kind !== "restart_transport") continue
    expect(expected.jobId).toBe(expected.phase === "apply_restart" ? applyJobId : rollbackJob.job_id)
  }
  auditPhase = "complete"
  expect(consoleErrors).toEqual([])
  expect(pageErrors).toEqual([])

  const evidence = {
    status: "passed",
    apply_requests: applyRequests,
    rollback_requests: rollbackRequests,
    target_version: expectedVersion,
    final_version: finalStatus.current_version,
    final_active_bundle: finalStatus.active_bundle,
    page_url: page.url(),
    page_title: await page.title(),
    observed_progress: observedProgress,
    screenshots: {
      selected: { file: path.basename(selectedScreenshot), sha256: await sha256(selectedScreenshot), size: (await stat(selectedScreenshot)).size },
      rollback: { file: path.basename(rollbackScreenshot), sha256: await sha256(rollbackScreenshot), size: (await stat(rollbackScreenshot)).size },
    },
    final_health: finalHealth,
    expected_console_events: {
      unauthenticated_bootstrap: expectedConsoleErrors.filter((error) => error.kind === "unauthenticated_bootstrap").length,
      apply_restart_transport: expectedConsoleErrors.filter((error) => error.kind === "restart_transport" && error.phase === "apply_restart").length,
      rollback_restart_transport: expectedConsoleErrors.filter((error) => error.kind === "restart_transport" && error.phase === "rollback_restart").length,
    },
    console_errors: consoleErrors,
    page_errors: pageErrors,
  }
  const encoded = `${JSON.stringify(evidence, null, 2)}\n`
  expect(Buffer.byteLength(encoded)).toBeLessThan(64 * 1024)
  await writeFile(path.join(evidenceRoot, "runtime-update-live.json"), encoded, "utf8")
})
