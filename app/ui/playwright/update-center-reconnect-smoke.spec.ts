import { expect, test, type Page, type Route } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

const fulfillJson = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
})

const TEST_JOB_ID = "0123456789abcdef0123456789abcdef"

const pendingJob = {
  job_id: TEST_JOB_ID,
  operation: "apply",
  status: "restarting",
  version: "1.4.2",
  message: "Restarting ChannelWatch.",
  restart_required: true,
}

const successfulJob = {
  ...pendingJob,
  status: "success",
  message: "ChannelWatch v1.4.2 is ready.",
  restart_required: false,
}

const availableStatus = {
  current_version: "1.4.1",
  image_version: "1.4.0",
  runtime_abi: "channelwatch-runtime-v1",
  launcher_protocol: 3,
  runtime_source: "app_bundle",
  delivery_mode: "app_update",
  image_refresh_recommended: false,
  settings_schema_version: 7,
  active_bundle: { version: "1.4.1", path: "/config/releases/v1.4.1" },
  catalog_state: "update_available",
  catalog_checked_at: "2026-10-10T20:00:00Z",
  trusted_target: {
    version: "1.4.2",
    version_tag: "v1.4.2",
    image_required: false,
    delivery_mode: "app_update",
    runtime_abi: "channelwatch-runtime-v1",
    settings_schema_version: 7,
    highlights: [],
  },
  cached_release_stale: false,
  operation_state: "idle",
  operation_busy: false,
  latest: null,
  update_available: true,
  image_required: false,
  last_job: null,
  rollback_available: true,
  auth_disabled_warning: false,
}

const readyStatus = {
  ...availableStatus,
  current_version: "1.4.2",
  active_bundle: { version: "1.4.2", path: "/config/releases/v1.4.2" },
  catalog_state: "current",
  trusted_target: null,
  update_available: false,
  operation_state: "idle",
  operation_busy: false,
  last_job: successfulJob,
}

async function trackDocumentLoads(page: Page) {
  await page.addInitScript(() => {
    const loads = Number(window.sessionStorage.getItem("update-center-test-loads") ?? "0")
    window.sessionStorage.setItem("update-center-test-loads", String(loads + 1))
  })
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
  await trackDocumentLoads(page)
  await page.addInitScript((jobId) => {
    const bytes = jobId.match(/.{2}/g)?.map(value => Number.parseInt(value, 16)) ?? []
    Object.defineProperty(Crypto.prototype, "getRandomValues", {
      configurable: true,
      value: (target: Uint8Array) => {
        target.set(bytes.slice(0, target.length))
        return target
      },
    })
    Object.defineProperty(Crypto.prototype, "randomUUID", {
      configurable: true,
      value: undefined,
    })
  }, TEST_JOB_ID)
})

test("one Apply action survives a restart disconnect and refreshes once after the durable job is ready", async ({ page }, testInfo) => {
  let applyRequests = 0
  let jobRequests = 0
  let updateStarted = false
  let releaseApply!: () => void
  const applyGate = new Promise<void>((resolve) => {
    releaseApply = resolve
  })

  await page.route("**/api/v1/update/status", (route) => fulfillJson(route, updateStarted ? readyStatus : availableStatus))
  await page.route("**/api/v1/update/apply", async (route) => {
    applyRequests += 1
    updateStarted = true
    await applyGate
    return fulfillJson(route, pendingJob)
  })
  await page.route(`**/api/v1/update/jobs/${TEST_JOB_ID}`, async (route) => {
    jobRequests += 1
    if (jobRequests === 1) return route.abort("connectionreset")
    if (jobRequests === 2) return fulfillJson(route, pendingJob)
    return fulfillJson(route, successfulJob)
  })

  await page.goto("/#settings:updates")
  await page.setViewportSize({ width: 375, height: 812 })
  const applyButton = page.getByRole("button", { name: "Apply update" })
  await expect(applyButton).toBeEnabled()

  await applyButton.evaluate((button) => {
    const element = button as HTMLElement
    element.click()
    element.click()
  })

  await expect(page.getByRole("heading", { name: "Installing" })).toBeVisible()
  await expect(page.getByText("The dashboard will refresh when ready.")).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath("update-waiting-mobile.png"),
    fullPage: true,
  })
  releaseApply()
  await expect(page).toHaveURL(/#overview$/, { timeout: 15_000 })
  await expect.poll(() => applyRequests).toBe(1)
  await expect.poll(() => jobRequests).toBeGreaterThanOrEqual(3)
  await expect.poll(() => page.evaluate(() => window.sessionStorage.getItem("update-center-test-loads"))).toBe("2")
})

test("a restored update job resumes its bounded wait without submitting Apply again", async ({ page }) => {
  let applyRequests = 0
  let jobRequests = 0
  let updateReady = false

  await page.route("**/api/v1/update/status", (route) => fulfillJson(route, updateReady ? readyStatus : {
    ...availableStatus,
    operation_state: "restarting",
    operation_busy: true,
    last_job: pendingJob,
  }))
  await page.route("**/api/v1/update/apply", (route) => {
    applyRequests += 1
    return fulfillJson(route, pendingJob)
  })
  await page.route(`**/api/v1/update/jobs/${TEST_JOB_ID}`, (route) => {
    jobRequests += 1
    if (jobRequests === 1) return route.abort("connectionreset")
    if (jobRequests === 2) return fulfillJson(route, pendingJob)
    updateReady = true
    return fulfillJson(route, successfulJob)
  })

  await page.goto("/#settings:updates")

  await expect(page.getByText("Restarting", { exact: true })).toBeVisible()
  await expect(page.getByText("The dashboard will refresh when ready.")).toBeVisible()
  await expect(page).toHaveURL(/#overview$/, { timeout: 15_000 })
  expect(applyRequests).toBe(0)
  expect(jobRequests).toBeGreaterThanOrEqual(3)
  await expect.poll(() => page.evaluate(() => window.sessionStorage.getItem("update-center-test-loads"))).toBe("2")
})

test("a durable terminal failure shows the server message and stops reconnecting", async ({ page }) => {
  const failedJob = {
    ...pendingJob,
    status: "failed",
    message: "ChannelWatch rolled back because the updated UI did not become ready.",
    restart_required: false,
  }
  let applyRequests = 0
  let jobRequests = 0

  await page.route("**/api/v1/update/status", (route) => fulfillJson(route, availableStatus))
  await page.route("**/api/v1/update/apply", (route) => {
    applyRequests += 1
    return fulfillJson(route, pendingJob)
  })
  await page.route(`**/api/v1/update/jobs/${TEST_JOB_ID}`, (route) => {
    jobRequests += 1
    return fulfillJson(route, failedJob)
  })

  await page.goto("/#settings:updates")
  expect(await page.evaluate(() => typeof crypto.randomUUID)).toBe("undefined")
  await page.getByRole("button", { name: "Apply update" }).click()

  const errorAlert = page.getByRole("alert").filter({ hasText: "Update Center error" })
  await expect(errorAlert.getByText(failedJob.message)).toBeVisible({ timeout: 10_000 })
  await expect(page).toHaveURL(/#settings:updates$/)
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeEnabled()
  expect(applyRequests).toBe(1)
  expect(jobRequests).toBe(1)
  expect(await page.evaluate(() => window.sessionStorage.getItem("update-center-test-loads"))).toBe("1")
})

test("a restored terminal job unlocks controls even when the initial status snapshot was busy", async ({ page }) => {
  const failedJob = {
    ...pendingJob,
    status: "failed",
    message: "The previous version was restored after startup checks failed.",
    restart_required: false,
  }
  let applyRequests = 0

  await page.route("**/api/v1/update/status", (route) => fulfillJson(route, {
    ...availableStatus,
    operation_state: "restarting",
    operation_busy: true,
    last_job: pendingJob,
  }))
  await page.route("**/api/v1/update/apply", (route) => {
    applyRequests += 1
    return fulfillJson(route, pendingJob)
  })
  await page.route(`**/api/v1/update/jobs/${TEST_JOB_ID}`, (route) => fulfillJson(route, failedJob))

  await page.goto("/#settings:updates")

  const errorAlert = page.getByRole("alert").filter({ hasText: "Update Center error" })
  await expect(errorAlert.getByText(failedJob.message)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeEnabled()
  await expect(page.getByRole("button", { name: "Retry now" })).toBeEnabled()
  expect(applyRequests).toBe(0)
})

for (const lostReply of ["connection reset", "HTTP 502"] as const) {
test(`a lost Apply response (${lostReply}) follows only the client-assigned durable job and never resubmits`, async ({ page }) => {
  let applyRequests = 0
  let jobRequests = 0
  let submittedJobId: string | null = null
  let updateReady = false
  let startupChecks = 0
  let statusOutages = 0
  let settingsOutages = 0

  await page.route("**/api/v1/update/status", (route) => {
    if (updateReady && statusOutages === 0) {
      statusOutages += 1
      return route.fulfill({ status: 502, body: "" })
    }
    return fulfillJson(route, updateReady ? readyStatus : availableStatus)
  })
  await page.route("**/api/v1/update/apply", async (route) => {
    applyRequests += 1
    const body = route.request().postDataJSON() as { job_id?: string }
    submittedJobId = body.job_id ?? null
    if (lostReply === "connection reset") return route.abort("connectionreset")
    return route.fulfill({ status: 502, body: "" })
  })
  await page.route(`**/api/v1/update/jobs/${TEST_JOB_ID}`, (route) => {
    jobRequests += 1
    if (jobRequests === 1) {
      return fulfillJson(route, {
        detail: {
          code: "ERR_UPDATE_JOB_NOT_FOUND",
          message: "The update job has not been published yet.",
        },
      }, 404)
    }
    if (jobRequests === 2) return fulfillJson(route, pendingJob)
    updateReady = true
    return fulfillJson(route, successfulJob)
  })
  await page.route("**/healthz/startup", (route) => {
    startupChecks += 1
    return route.fulfill({ status: startupChecks === 1 ? 503 : 200, body: "" })
  })
  await page.route("**/api/settings", (route) => {
    if (updateReady && settingsOutages === 0) {
      settingsOutages += 1
      return route.fulfill({ status: 503, body: "" })
    }
    return route.fallback()
  })

  await page.goto("/#settings:updates")
  await page.getByRole("button", { name: "Apply update" }).click()

  await expect.poll(() => submittedJobId).toBe(TEST_JOB_ID)
  await expect(page.getByText("Restarting", { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/#overview$/, { timeout: 15_000 })
  expect(applyRequests).toBe(1)
  expect(jobRequests).toBeGreaterThanOrEqual(3)
  expect(startupChecks).toBeGreaterThanOrEqual(3)
  expect(statusOutages).toBe(1)
  expect(settingsOutages).toBe(1)
  await expect.poll(() => page.evaluate(() => window.sessionStorage.getItem("update-center-test-loads"))).toBe("2")
})
}
