import { createHash } from "node:crypto"
import { mkdir, stat, writeFile } from "node:fs/promises"
import path from "node:path"

import { expect, test } from "@playwright/test"

const username = process.env.CHANNELWATCH_PLAYWRIGHT_ADMIN_USERNAME?.trim()
const password = process.env.CHANNELWATCH_PLAYWRIGHT_ADMIN_PASSWORD?.trim()
const targetVersion = process.env.CHANNELWATCH_PLAYWRIGHT_TARGET_VERSION?.trim()?.replace(/^v/, "")
const imageVersion = process.env.CHANNELWATCH_PLAYWRIGHT_IMAGE_VERSION?.trim()?.replace(/^v/, "")
const evidenceDirectory = process.env.CHANNELWATCH_PLAYWRIGHT_EVIDENCE_DIR?.trim()

function required(value: string | undefined, name: string): string {
  if (!value) throw new Error(`${name} is required for the live runtime update proof.`)
  return value
}

async function sha256(file: string) {
  const data = await import("node:fs/promises").then(({ readFile }) => readFile(file))
  return createHash("sha256").update(data).digest("hex")
}

test("authenticated live Update Center applies and rolls back the exact candidate once", async ({ page }) => {
  const adminUsername = required(username, "CHANNELWATCH_PLAYWRIGHT_ADMIN_USERNAME")
  const adminPassword = required(password, "CHANNELWATCH_PLAYWRIGHT_ADMIN_PASSWORD")
  const expectedVersion = required(targetVersion, "CHANNELWATCH_PLAYWRIGHT_TARGET_VERSION")
  const expectedImageVersion = required(imageVersion, "CHANNELWATCH_PLAYWRIGHT_IMAGE_VERSION")
  const evidenceRoot = required(evidenceDirectory, "CHANNELWATCH_PLAYWRIGHT_EVIDENCE_DIR")
  await mkdir(evidenceRoot, { recursive: true })

  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  let applyRequests = 0
  let rollbackRequests = 0
  let observedProgress = false
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text().slice(0, 500))
  })
  page.on("pageerror", (error) => pageErrors.push(error.message.slice(0, 500)))
  page.on("request", (request) => {
    if (request.method() !== "POST") return
    if (request.url().endsWith("/api/v1/update/apply")) applyRequests += 1
    if (request.url().endsWith("/api/v1/update/rollback")) rollbackRequests += 1
  })

  await page.goto("/#settings:updates")
  await expect(page.getByTestId("auth-login-shell")).toBeVisible()
  await page.getByLabel("Username").fill(adminUsername)
  await page.getByLabel("Password").fill(adminPassword)
  await page.getByRole("button", { name: "Sign in" }).click()
  await expect(page.getByTestId("auth-login-shell")).toHaveCount(0)

  await page.goto("/#settings:updates")
  const checkResponsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/update/check") && response.request().method() === "POST",
  )
  await page.getByRole("button", { name: "Check for updates" }).click()
  const check = await (await checkResponsePromise).json()
  expect(check.update_available).toBe(true)
  expect(check.image_required).toBe(false)
  expect(check.trusted_target?.version).toBe(expectedVersion)
  await expect(page.getByText(`v${expectedVersion}`, { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Apply update" })).toBeEnabled()

  await page.getByRole("button", { name: "Apply update" }).click()
  const progress = page.getByText(/Downloading|Restarting/, { exact: true })
  await expect(progress.first()).toBeVisible({ timeout: 30_000 })
  observedProgress = true
  await expect.poll(async () => {
    const response = await page.request.get("/api/v1/update/status")
    if (!response.ok()) return "unavailable"
    return (await response.json()).current_version
  }, { timeout: 10 * 60_000 }).toBe(expectedVersion)

  await page.goto("/#settings:updates")
  await expect(page.getByRole("button", { name: "Roll back" })).toBeEnabled({ timeout: 60_000 })
  const selectedScreenshot = path.join(evidenceRoot, "runtime-update-selected.png")
  await page.screenshot({ path: selectedScreenshot, fullPage: true })

  await page.getByRole("button", { name: "Roll back" }).click()
  const rollbackStatus = await expect.poll(async () => {
    const response = await page.request.get("/api/v1/update/status")
    if (!response.ok()) return null
    const status = await response.json()
    return status.current_version !== expectedVersion && status.active_bundle === null && status.operation_busy === false
      ? status
      : null
  }, { timeout: 10 * 60_000 }).not.toBeNull()
  void rollbackStatus

  const finalResponse = await page.request.get("/api/v1/update/status")
  expect(finalResponse.ok()).toBe(true)
  const finalStatus = await finalResponse.json()
  expect(finalStatus.current_version).toBe(expectedImageVersion)
  const startupResponse = await page.request.get("/healthz/startup")
  const readyResponse = await page.request.get("/healthz/ready")
  expect(startupResponse.ok()).toBe(true)
  expect(readyResponse.ok()).toBe(true)
  const finalHealth = {
    startup: await startupResponse.json(),
    ready: await readyResponse.json(),
  }
  expect(finalHealth).toEqual({
    startup: { status: "ready" },
    ready: { status: "ready", ready: true },
  })
  await page.goto("/#settings:updates")
  const rollbackScreenshot = path.join(evidenceRoot, "runtime-update-rollback.png")
  await page.screenshot({ path: rollbackScreenshot, fullPage: true })

  expect(applyRequests).toBe(1)
  expect(rollbackRequests).toBe(1)
  expect(observedProgress).toBe(true)
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
    console_errors: consoleErrors,
    page_errors: pageErrors,
  }
  const encoded = `${JSON.stringify(evidence, null, 2)}\n`
  expect(Buffer.byteLength(encoded)).toBeLessThan(64 * 1024)
  await writeFile(path.join(evidenceRoot, "runtime-update-live.json"), encoded, "utf8")
})
