import { expect, test, type Page, type Route } from "@playwright/test"

import { canonicalDvrId } from "../lib/dvr-id"
import { installApiMocks, mockSettings } from "./support/mock-api"

const fulfillJson = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
})

type SavedSettings = {
  log_retention_days?: number
  clear_apprise_fields?: string[]
  dvr_servers: Array<{ id: string; host: string; port: number }>
}

async function mockSettingsRoute(page: Page, initial: Record<string, unknown> = mockSettings) {
  const saves: SavedSettings[] = []
  const state = { settings: initial, status: 200 }
  await page.route("**/api/settings", async (route) => {
    if (state.status !== 200) return fulfillJson(route, { detail: "stopped" }, state.status)
    if (route.request().method() === "POST") {
      saves.push(route.request().postDataJSON() as SavedSettings)
      return fulfillJson(route, { message: "Settings saved successfully", applied: "hot" })
    }
    return fulfillJson(route, state.settings)
  })
  return { saves, state }
}

const RESTART_RECOVERED = "channelwatch-restart-recovered"

const goToHash = (page: Page, hash: string) => page.evaluate((next) => { window.location.hash = next }, hash)

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("discarding a provider switch does not clear its saved credential on the next save", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page, { ...mockSettings, apprise_discord: "****" })
  await page.goto("/#settings:notifications")
  const discord = page.getByRole("switch", { name: "Discord" }).first()
  await discord.click()
  await expect(discord).not.toBeChecked()
  await page.getByRole("button", { name: "Discard" }).click()
  await expect(discord).toBeChecked()

  await goToHash(page, "#settings:general")
  await page.locator("#log_retention_days").fill("9")
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0].clear_apprise_fields).not.toContain("apprise_discord")
})

test("unsaved settings survive a trip to another tab", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page)
  await page.goto("/#settings:general")
  await page.locator("#log_retention_days").fill("9")
  await goToHash(page, "#overview")
  await expect(page.getByTestId("settings-save-bar")).toBeHidden()

  await goToHash(page, "#settings:general")
  await expect(page.locator("#log_retention_days")).toHaveValue("9")
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0].log_retention_days).toBe(9)
})

test("reloading warns while settings are unsaved", async ({ page }) => {
  await mockSettingsRoute(page)
  await page.goto("/#settings:general")
  await page.locator("#log_retention_days").fill("9")
  await expect(page.getByTestId("settings-save-bar")).toBeVisible()
  const prompted = page.waitForEvent("dialog").then(async (dialog) => {
    const type = dialog.type()
    await dialog.dismiss()
    return type
  })
  void page.reload().catch(() => undefined)
  expect(await prompted).toBe("beforeunload")
})

test("a restore replaces what the form holds, so saving cannot undo it", async ({ page }) => {
  const { saves, state } = await mockSettingsRoute(page)
  await page.route("**/api/v1/backup/restore", (route) => {
    state.settings = { ...mockSettings, log_retention_days: 99 }
    return fulfillJson(route, { message: "Restore completed." })
  })
  await page.goto("/#settings:backup")
  await page.locator('input[type="file"]').setInputFiles({ name: "backup.zip", mimeType: "application/zip", buffer: Buffer.from("zip") })
  const confirm = page.getByRole("alertdialog")
  await expect(confirm).toBeFocused()
  await confirm.getByRole("button", { name: /restore|replace/i }).click()
  await expect(page.getByRole("button", { name: /reload/i })).toBeVisible()

  await goToHash(page, "#settings:general")
  await expect(page.locator("#log_retention_days")).toHaveValue("99")
  await page.locator("#log_retention_days").fill("98")
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0].log_retention_days).toBe(98)
})

test("an expired session asks to sign in above the page and keeps the edit", async ({ page }) => {
  const { saves, state } = await mockSettingsRoute(page)
  await page.route("**/api/v1/auth/login", (route) => {
    state.status = 200
    return fulfillJson(route, { username: "admin", role: "admin", csrf_token: "next" })
  })
  await page.goto("/#settings:general")
  await page.locator("#log_retention_days").fill("9")
  state.status = 401
  await page.getByRole("button", { name: "Save settings" }).click()

  const signIn = page.getByTestId("session-expired")
  await expect(signIn).toBeVisible()
  await signIn.locator("#login-username").fill("admin")
  await signIn.locator("#login-password").fill("password")
  await signIn.getByRole("button", { name: /sign in/i }).click()
  await expect(signIn).toBeHidden()

  await expect(page.locator("#log_retention_days")).toHaveValue("9")
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
})

test("a server that is down does not ask a signed-in person to sign in, and recovers by itself", async ({ page }) => {
  const { state } = await mockSettingsRoute(page)
  state.status = 503
  await page.goto("/#overview")
  await expect(page.getByTestId("app-load-failed")).toBeVisible()
  await expect(page.getByTestId("auth-login-shell")).toHaveCount(0)

  state.status = 200
  await expect(page.getByTestId("app-load-failed")).toBeHidden({ timeout: 10_000 })
  await expect(page.getByTestId("auth-login-shell")).toHaveCount(0)
})

test("two new DVRs get their own ids, and an empty port saves as the default", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page)
  await page.goto("/#settings:general")
  const existing = mockSettings.dvr_servers.length
  const add = page.getByRole("button", { name: "Add server" })
  await add.click()
  await add.click()
  await page.locator(`#dvr-${existing}-host`).fill("den.local")
  await page.locator(`#dvr-${existing + 1}-host`).fill("office.local")
  await page.locator(`#dvr-${existing + 1}-port`).fill("")
  await expect(page.locator(`#dvr-${existing + 1}-port`)).toHaveValue("")

  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  const added = saves[0].dvr_servers.slice(existing)
  expect(added.map((server) => server.id)).toEqual([canonicalDvrId("den.local", 8089), canonicalDvrId("office.local", 8089)])
  expect(added[1].port).toBe(8089)
})

test("a new DVR with no host is stopped before it reaches the server", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page)
  await page.goto("/#settings:general")
  await page.getByRole("button", { name: "Add server" }).click()
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect(page.getByText(/Enter a host for/).first()).toBeVisible()
  expect(saves).toHaveLength(0)
})

test("Enter in the update window saves the window, not the settings form", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page)
  const policySaves: unknown[] = []
  await page.route("**/api/v1/update/policy", async (route) => {
    if (route.request().method() === "POST" || route.request().method() === "PUT") {
      policySaves.push(route.request().postDataJSON())
    }
    return route.fallback()
  })
  await page.goto("/#settings:updates")
  await page.locator("#update-maintenance-minutes").fill("180")
  await page.locator("#update-maintenance-minutes").press("Enter")
  await expect.poll(() => policySaves.length).toBe(1)
  expect(saves).toHaveLength(0)
})

test("rolling back asks first", async ({ page }) => {
  await mockSettingsRoute(page)
  let rollbacks = 0
  await page.route("**/api/v1/update/rollback", (route) => {
    rollbacks += 1
    return fulfillJson(route, { job_id: "job-1", status: "success", message: "Rolled back" })
  })
  await page.goto("/#settings:updates")
  await page.getByTestId("update-center-summary").getByRole("button", { name: /roll back/i }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toBeVisible()
  expect(rollbacks).toBe(0)
  await dialog.getByRole("button", { name: /roll back/i }).click()
  await expect.poll(() => rollbacks).toBe(1)
})

test("archived DVRs are not offered in the header", async ({ page }) => {
  await mockSettingsRoute(page, {
    ...mockSettings,
    dvr_servers: [
      ...mockSettings.dvr_servers,
      { id: "dvr_old", name: "Old Archived DVR", host: "old.local", port: 8089, enabled: true, deleted_at: "2026-10-30T00:00:00Z" },
    ],
  })
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto("/#overview")
  await page.getByRole("combobox", { name: /dvr/i }).first().click()
  await expect(page.getByRole("option").first()).toBeVisible()
  await expect(page.getByRole("option", { name: "Old Archived DVR" })).toHaveCount(0)
})

test("the skip link moves focus and does not leave the page", async ({ page }) => {
  await mockSettingsRoute(page)
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto("/#settings:general")
  await expect(page.locator("#log_retention_days")).toBeVisible()
  await page.locator("#log_retention_days").fill("9")
  await page.getByRole("link", { name: /skip to/i }).focus()
  await page.keyboard.press("Enter")
  await expect(page.locator("#main-content")).toBeFocused()
  await expect(page.locator("#log_retention_days")).toHaveValue("9")
  expect(new URL(page.url()).hash).toBe("#settings:general")
})

test("going back from an open report keeps the draft for when you return", async ({ page }) => {
  await mockSettingsRoute(page)
  await page.goto("/#overview")
  await goToHash(page, "#help-feedback")
  await page.getByTestId("help-report-problem").click()
  await page.locator("#report-summary").fill("The tiles look wrong")

  await page.goBack()
  await expect(page.locator("#report-summary")).toHaveCount(0)
  await page.goForward()
  await expect(page.locator("#report-summary")).toHaveValue("The tiles look wrong")
})

test("each screen names itself in the page title", async ({ page }) => {
  await mockSettingsRoute(page)
  // The release image checks require Home to keep the plain product name.
  await page.goto("/")
  await expect(page.locator('[data-view="overview"]')).toBeVisible()
  await expect(page).toHaveTitle("ChannelWatch")
  await page.goto("/#watch-history")
  await expect(page).toHaveTitle("Watch History - ChannelWatch")
  await goToHash(page, "#settings:general")
  await expect(page).toHaveTitle("Settings - ChannelWatch")
})

test("sign-in works on a phone even when a sheet was open, and the sheet's draft survives", async ({ page }) => {
  const { state } = await mockSettingsRoute(page)
  await page.route("**/api/v1/auth/login", (route) => {
    state.status = 200
    return fulfillJson(route, { username: "admin", role: "admin", csrf_token: "next" })
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/#help-feedback")
  await page.getByTestId("help-report-problem").click()
  await page.locator("#report-summary").fill("Still typing this")

  state.status = 401
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("channelwatch-session-expired")))
  const signIn = page.getByTestId("session-expired")
  await expect(signIn).toBeVisible()
  await signIn.locator("#login-username").click()
  await page.keyboard.type("admin")
  await expect(signIn.locator("#login-username")).toHaveValue("admin")
  await signIn.locator("#login-password").fill("password")
  await signIn.getByRole("button", { name: /sign in/i }).click()
  await expect(signIn).toBeHidden()
  await expect(page.locator("#report-summary")).toHaveValue("Still typing this")
})

test("a wrong password message stays while the page keeps polling", async ({ page }) => {
  const { state } = await mockSettingsRoute(page)
  await page.route("**/api/v1/auth/login", (route) => fulfillJson(route, { detail: "nope" }, 401))
  await page.goto("/#settings:general")
  await expect(page.locator("#log_retention_days")).toBeVisible()
  state.status = 401
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("channelwatch-session-expired")))
  const signIn = page.getByTestId("session-expired")
  await signIn.locator("#login-username").fill("admin")
  await signIn.locator("#login-password").fill("wrong")
  await signIn.getByRole("button", { name: /sign in/i }).click()
  const message = signIn.getByRole("alert")
  await expect(message).toBeVisible()
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("channelwatch-session-expired")))
  await page.waitForTimeout(300)
  await expect(message).toBeVisible()
})

test("setup typed after an outage is not interrupted by retries", async ({ page }) => {
  const { state } = await mockSettingsRoute(page)
  let setupReady = false
  await page.route("**/api/v1/auth/setup-status", (route) => {
    if (!setupReady) return fulfillJson(route, { detail: "starting" }, 503)
    return route.fallback()
  })
  state.status = 503
  await page.goto("/#overview")
  await expect(page.getByTestId("app-load-failed")).toBeVisible()
  await page.route("**/api/v1/auth/setup-status", (route) => fulfillJson(route, { setup_required: true, persisted_mode: "rbac", configured_mode: "rbac", effective_mode: "rbac", current_mode: "rbac" }))
  setupReady = true
  const username = page.locator("#setup-username")
  await expect(username).toBeVisible({ timeout: 10_000 })
  await username.click()
  await page.keyboard.type("admin")
  await page.waitForTimeout(6_000)
  await expect(username).toBeFocused()
  await expect(username).toHaveValue("admin")
})

test("switching a saved provider off and on again does not block saving", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page, { ...mockSettings, apprise_discord: "****" })
  await page.goto("/#settings:notifications")
  const discord = page.getByRole("switch", { name: "Discord" }).first()
  await discord.click()
  await discord.click()
  await expect(discord).toBeChecked()
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0].clear_apprise_fields).not.toContain("apprise_discord")
})

test("a new provider with no details stops the save and says so", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page)
  await page.goto("/#settings:notifications")
  await page.getByRole("switch", { name: "Discord" }).first().click()
  await goToHash(page, "#settings:general")
  await page.locator("#log_retention_days").fill("9")
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect(page.getByText("Add the Discord details or turn it off.").first()).toBeVisible()
  expect(saves).toHaveLength(0)
})

test("the same DVR address twice stops the save", async ({ page }) => {
  const { saves } = await mockSettingsRoute(page)
  await page.goto("/#settings:general")
  const existing = mockSettings.dvr_servers.length
  await page.getByRole("button", { name: "Add server" }).click()
  await page.locator(`#dvr-${existing}-host`).fill(mockSettings.dvr_servers[0].host)
  await page.locator(`#dvr-${existing}-port`).fill(String(mockSettings.dvr_servers[0].port))
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect(page.getByText(/is listed twice/).first()).toBeVisible()
  expect(saves).toHaveLength(0)
})

test("adding back an archived DVR's address gets its own id", async ({ page }) => {
  const archivedId = canonicalDvrId("old.local", 8089)
  const { saves } = await mockSettingsRoute(page, {
    ...mockSettings,
    dvr_servers: [
      ...mockSettings.dvr_servers,
      { id: archivedId, name: "Old", host: "old.local", port: 8089, enabled: true, deleted_at: "2026-10-30T00:00:00Z" },
    ],
  })
  await page.goto("/#settings:general")
  const rows = mockSettings.dvr_servers.length + 1
  await page.getByRole("button", { name: "Add server" }).click()
  await page.locator(`#dvr-${rows}-host`).fill("old.local")
  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  const ids = saves[0].dvr_servers.map((server) => server.id)
  expect(new Set(ids).size).toBe(ids.length)
  expect(ids[rows]).not.toBe(archivedId)
})

test("a restart recovering does not throw away unsaved settings", async ({ page }) => {
  await mockSettingsRoute(page)
  await page.goto("/#settings:general")
  await page.locator("#log_retention_days").fill("9")
  await page.evaluate((name) => window.dispatchEvent(new CustomEvent(name)), RESTART_RECOVERED)
  await page.waitForTimeout(500)
  await expect(page.locator("#log_retention_days")).toHaveValue("9")
  await expect(page.getByTestId("settings-save-bar")).toBeVisible()
})

test("an error message survives changing tabs", async ({ page }) => {
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "POST") return fulfillJson(route, { detail: "Nope" }, 400)
    return fulfillJson(route, mockSettings)
  })
  await page.goto("/#settings:general")
  await page.locator("#log_retention_days").fill("9")
  await page.getByRole("button", { name: "Save settings" }).click()
  const toast = page.locator(".toast-viewport").getByText("Error saving settings")
  await expect(toast).toBeVisible()
  await goToHash(page, "#overview")
  await page.waitForTimeout(400)
  await expect(toast).toBeVisible()
})
