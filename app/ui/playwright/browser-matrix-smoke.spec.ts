import { expect, test, type Page, type Route } from "@playwright/test"

import {
  installApiMocks,
  mockSecurityStatus,
  mockSettings,
  mockSetupStatus,
  mockWhoAmI,
} from "./support/mock-api"

const fulfillJson = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

async function installAuthState(
  page: Page,
  initial: "setup" | "none",
) {
  let mode: "setup" | "rbac" | "none" = initial

  await page.route("**/api/v1/auth/setup-status", (route) => fulfillJson(route, {
    ...mockSetupStatus,
    persisted_mode: mode === "setup" ? "" : mode,
    configured_mode: mode === "setup" ? null : mode,
    effective_mode: mode === "setup" ? null : mode,
    current_mode: mode === "setup" ? null : mode,
    setup_required: mode === "setup",
    needs_setup: mode === "setup",
    rbac_enabled: mode === "rbac",
    session_auth_available: mode === "rbac",
  }))
  await page.route("**/api/v1/security/status", (route) => fulfillJson(route, {
    ...mockSecurityStatus,
    persisted_mode: mode === "setup" ? "" : mode,
    configured_mode: mode === "setup" ? null : mode,
    effective_mode: mode === "setup" ? null : mode,
    setup_required: mode === "setup",
    session_setup_required: mode === "setup",
    security_mode: mode === "none" ? "NO_AUTH" : "RBAC_ONLY",
    auth_disabled: mode === "none",
    rbac_enabled: mode === "rbac",
    session_auth_available: mode === "rbac",
  }))
  await page.route("**/api/v1/auth/setup", async (route) => {
    const request = route.request().postDataJSON() as { mode: "rbac" | "none" }
    mode = request.mode
    return fulfillJson(route, request.mode === "rbac"
      ? { message: "Admin user created", username: "matrix-admin", csrf_token: "matrix-csrf" }
      : { message: "Authentication disabled by setup choice" })
  })
  await page.route("**/api/settings", (route) => fulfillJson(route, {
    ...mockSettings,
    auth_mode: mode === "setup" ? "" : mode,
    rbac_enabled: mode === "rbac",
  }))
}

test("first-run RBAC setup reaches the authenticated dashboard and caches CSRF", async ({ page }) => {
  await installAuthState(page, "setup")
  await page.goto("/#overview")

  const shell = page.getByTestId("auth-bootstrap-shell")
  await expect(shell).toBeVisible()
  await shell.getByLabel("Admin username").fill("matrix-admin")
  await shell.getByLabel("Admin password").fill("ChannelWatch!234")
  await shell.getByRole("button", { name: "Finish setup" }).click()

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("cw_csrf_token"))).toBe("matrix-csrf")
})

test("persisted no-auth mode shows both the shell and security warning", async ({ page }) => {
  await installAuthState(page, "none")
  await page.goto("/#settings:security")

  await expect(page.getByTestId("auth-noauth-shell")).toBeVisible()
  await expect(page.getByTestId("security-noauth-warning")).toBeVisible()
  await expect(page.getByTestId("security-create-login-btn")).toBeVisible()
  await expect(page.getByTestId("security-runtime-override-banner")).toHaveCount(0)
})

test("credential change submits CSRF and surfaces the confirmed username", async ({ page }) => {
  let credentialRequest: { headers: Record<string, string>; body: Record<string, string> } | undefined
  await page.addInitScript(() => sessionStorage.setItem("cw_csrf_token", "credential-csrf"))
  await page.route("**/api/v1/auth/change-credentials", async (route) => {
    credentialRequest = {
      headers: route.request().headers(),
      body: route.request().postDataJSON() as Record<string, string>,
    }
    return fulfillJson(route, { message: "Credentials updated", username: "renamed-admin" })
  })
  await page.route("**/api/v1/auth/whoami", (route) => fulfillJson(route, {
    ...mockWhoAmI,
    username: credentialRequest ? "renamed-admin" : mockWhoAmI.username,
  }))

  await page.goto("/#settings:security")
  await page.getByLabel("Username").fill("renamed-admin")
  await page.getByLabel("Current password").fill("Current!234")
  await page.getByLabel("New password").fill("NewPassword!567")
  await page.getByRole("button", { name: "Save credentials" }).click()

  await expect(page.getByText("Credentials updated")).toBeVisible()
  expect(credentialRequest?.headers["x-csrf-token"]).toBe("credential-csrf")
  expect(credentialRequest?.body).toEqual({
    current_password: "Current!234",
    username: "renamed-admin",
    new_password: "NewPassword!567",
  })
})

test("backup download and restore exercise the browser-facing success states", async ({ page }) => {
  await page.route("**/api/v1/backup/download", (route) => route.fulfill({
    status: 200,
    contentType: "application/zip",
    body: "backup-zip",
  }))
  await page.route("**/api/v1/backup/restore", (route) => fulfillJson(route, {
    message: "Restore completed. Core process hot-reloaded.",
  }))
  await page.goto("/#settings:backup")

  const download = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download Backup" }).click()
  await expect((await download).suggestedFilename()).toMatch(/^channelwatch_backup_.*\.zip$/)

  const chooser = page.waitForEvent("filechooser")
  await page.getByRole("button", { name: "Choose backup file…" }).click()
  await (await chooser).setFiles({ name: "channelwatch-backup.zip", mimeType: "application/zip", buffer: Buffer.from("PK") })
  await expect(page.getByText("Restore completed. Core process hot-reloaded.")).toBeVisible()
  await expect(page.getByRole("button", { name: "Restored" })).toBeVisible()
})

test("settings save posts the changed value before requesting core restart", async ({ page }) => {
  const calls: string[] = []
  let submitted: Record<string, unknown> | undefined
  await page.route("**/api/settings", (route) => {
    if (route.request().method() === "POST") {
      calls.push("settings")
      submitted = route.request().postDataJSON() as Record<string, unknown>
      return fulfillJson(route, { message: "Settings saved successfully" })
    }
    return fulfillJson(route, { ...mockSettings, log_retention_days: submitted?.log_retention_days ?? 14 })
  })
  await page.route("**/api/restart_core", (route) => {
    calls.push("restart")
    return fulfillJson(route, { message: "Restart command sent" })
  })
  await page.goto("/#settings:general")

  await page.getByLabel("Log Retention (Days)").fill("21")
  await page.getByRole("button", { name: "Save Settings" }).click()
  await expect(page.getByText("Settings saved. Restarting core process...")).toBeVisible()
  expect(submitted?.log_retention_days).toBe(21)
  expect(calls).toEqual(["settings", "restart"])
})

test("restart overlay appears and recovers after the health endpoint returns", async ({ page }) => {
  let healthChecks = 0
  await page.route("**/api/restart_container", (route) => fulfillJson(route, { message: "Restart initiated" }, 202))
  await page.route("**/api/health", (route) => {
    healthChecks += 1
    return healthChecks === 1
      ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ status: "starting" }) })
      : fulfillJson(route, { status: "ok" })
  })
  await page.goto("/#overview")
  await page.getByRole("button", { name: "Restart", exact: true }).click()

  await expect(page.getByRole("heading", { name: "Restarting ChannelWatch..." })).toBeVisible()
  await expect(page.getByRole("heading", { name: "ChannelWatch is back online" })).toBeVisible({ timeout: 10_000 })
  expect(healthChecks).toBeGreaterThanOrEqual(2)
})

test("restart API errors recover to the dashboard with actionable feedback", async ({ page }) => {
  await page.route("**/api/restart_container", (route) => fulfillJson(route, {
    detail: {
      code: "SUPERVISOR_NOT_AVAILABLE",
      message: "Supervisor is unavailable.",
      remediation: "Restart the container from the host.",
    },
  }, 503))
  await page.goto("/#overview")
  await page.getByRole("button", { name: "Restart", exact: true }).click()

  await expect(page.getByText("Restart Failed", { exact: true })).toBeVisible()
  await expect(page.getByText(
    "Supervisor is unavailable. Restart the container from the host.",
    { exact: true },
  )).toBeVisible()
  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Restarting ChannelWatch..." })).toHaveCount(0)
})
