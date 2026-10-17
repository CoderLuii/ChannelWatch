import { createHash } from "node:crypto"
import { mkdir, stat, writeFile } from "node:fs/promises"
import { createServer } from "node:http"
import path from "node:path"

import { expect, test, type APIRequestContext, type APIResponse, type Page } from "@playwright/test"

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

  const consoleErrors: string[] = []
  const expectedConsoleErrors: ExpectedConsoleError[] = []
  const pageErrors: string[] = []
  let auditPhase: BrowserAuditPhase = "unauthenticated_bootstrap"
  let applyRequests = 0
  let rollbackRequests = 0
  let applyJobId: string | null = null
  let observedProgress = false
  page.on("console", (message) => {
    if (message.type() !== "error") return
    const text = message.text().slice(0, 500)
    const expected = expectedConsoleError(text, message.location().url, auditPhase, expectedOrigin)
    if (expected) {
      expectedConsoleErrors.push(expected)
      return
    }
    consoleErrors.push(text)
  })
  page.on("pageerror", (error) => pageErrors.push(error.message.slice(0, 500)))
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
  auditPhase = "authenticated"
  await page.goto("/#settings:updates")
  await expect(page).toHaveURL(/#settings:updates$/)
  await expect(page.getByTestId("auth-login-shell")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeVisible()
  const rollbackScreenshot = path.join(evidenceRoot, "runtime-update-rollback.png")
  await page.screenshot({ path: rollbackScreenshot, fullPage: true })

  expect(applyRequests).toBe(1)
  expect(rollbackRequests).toBe(1)
  expect(observedProgress).toBe(true)
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
