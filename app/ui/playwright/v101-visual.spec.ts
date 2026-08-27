import { expect, test, type Page, type Route } from "@playwright/test"

import { installApiMocks, mockSettings, mockSystemInfo } from "./support/mock-api"

const fulfillJson = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
})

const screenshotOptions = {
  animations: "disabled" as const,
  fullPage: true,
  maxDiffPixelRatio: 0.01,
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => localStorage.setItem("theme", value), theme)
}

async function waitForCollapsedDesktopSidebar(page: Page) {
  // The Sidebar performs its mobile/desktop check after hydration. Waiting for
  // the desktop collapse control prevents a transient mobile drawer from
  // becoming the visual baseline when the full suite runs under load.
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-08-26T12:00:00Z"))
  await installApiMocks(page)
  await page.route("**/api/v1/update/status", (route) => fulfillJson(route, {
    current_version: "1.0.1",
    image_version: "1.0.0",
    runtime_abi: "channelwatch-runtime-v1",
    launcher_protocol: 3,
    runtime_source: "app_bundle",
    delivery_mode: "app_update",
    image_refresh_recommended: false,
    settings_schema_version: 7,
    active_bundle: "1.0.1",
    catalog_state: "current",
    catalog_checked_at: "2026-08-26T12:00:00Z",
    trusted_target: null,
    cached_release_stale: false,
    operation_state: "idle",
    operation_busy: false,
    latest: null,
    update_available: false,
    image_required: false,
    last_job: null,
    rollback_available: true,
    auth_disabled_warning: false,
  }))
})

test("Alerts desktop dark", async ({ page }) => {
  await useTheme(page, "dark")
  await page.route("**/api/settings", (route) => fulfillJson(route, {
    ...mockSettings,
    notification_preferences_version: 0,
    alert_channel_watching: false,
    alert_vod_watching: false,
    rd_alert_started: false,
    rd_alert_completed: false,
  }))
  await page.goto("/#settings:alerts")
  await waitForCollapsedDesktopSidebar(page)
  await expect(page.getByRole("heading", { name: "Alert policy" })).toBeVisible()
  await expect(page).toHaveScreenshot("alerts-desktop-dark.png", screenshotOptions)
})

test("Alerts mobile light", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await useTheme(page, "light")
  await page.route("**/api/settings", (route) => fulfillJson(route, {
    ...mockSettings,
    notification_preferences_version: 1,
    alert_channel_watching: false,
    alert_vod_watching: false,
    rd_alert_scheduled: false,
    rd_alert_started: false,
    rd_alert_completed: false,
    dvr_alert_unreachable: true,
    dvr_alert_recovered: true,
    rd_alert_failed: true,
    rd_alert_skipped: true,
    rd_alert_missed: true,
    rd_alert_interrupted: true,
  }))
  await page.goto("/#settings:alerts")
  await expect(page.getByRole("heading", { name: "Alert policy" })).toBeVisible()
  await expect(page).toHaveScreenshot("alerts-mobile-light.png", screenshotOptions)
})

test("Dashboard multi-DVR uptime dialog", async ({ page }) => {
  await useTheme(page, "dark")
  await page.route("**/api/system-info", (route) => fulfillJson(route, {
    ...mockSystemInfo,
    channelwatch_version: "1.0.1",
    channelwatch_core_started_at: "2026-08-26T10:00:00Z",
    channelwatch_core_uptime_seconds: 7200,
    channelwatch_ui_started_at: "2026-08-26T10:05:00Z",
    channelwatch_ui_uptime_seconds: 6900,
    dvr_status: [
      {
        ...mockSystemInfo.dvr_status[0],
        name: "Primary DVR with a long display name",
        started_at: "2026-08-20T10:00:00Z",
        uptime_seconds: 525600,
        uptime_available: true,
        enabled: true,
      },
      {
        ...mockSystemInfo.dvr_status[0],
        id: "secondary-dvr",
        name: "Secondary DVR",
        connected: false,
        version: null,
        started_at: null,
        uptime_seconds: null,
        uptime_available: false,
        enabled: true,
      },
      {
        ...mockSystemInfo.dvr_status[0],
        id: "disabled-dvr",
        name: "Disabled DVR",
        connected: false,
        enabled: false,
        version: null,
        started_at: null,
        uptime_seconds: null,
        uptime_available: false,
      },
    ],
  }))
  await page.goto("/#overview")
  await waitForCollapsedDesktopSidebar(page)
  await page.getByRole("button", { name: /DVR uptime/ }).click()
  await expect(page.getByRole("dialog", { name: "DVR uptime" })).toBeVisible()
  await expect(page).toHaveScreenshot("dashboard-dvr-uptime-dialog.png", screenshotOptions)
})

test("Recent Activity client filter", async ({ page }) => {
  await useTheme(page, "dark")
  await page.goto("/#overview")
  await waitForCollapsedDesktopSidebar(page)
  await page.getByRole("button", { name: "Filter activity by type" }).click()
  await expect(page.getByText("The timeline remains an aggregate across all clients.")).toBeVisible()
  await expect(page).toHaveScreenshot("recent-activity-client-filter.png", screenshotOptions)
})

test("Watch History client filter", async ({ page }) => {
  await useTheme(page, "light")
  await page.goto("/#watch-history")
  await waitForCollapsedDesktopSidebar(page)
  await page.getByRole("combobox", { name: "Filter activity by exact client" }).click()
  await expect(page.getByRole("option", { name: /Living Room Apple TV/ })).toBeVisible()
  await expect(page).toHaveScreenshot("watch-history-client-filter.png", screenshotOptions)
})

test("Help and Feedback page", async ({ page }) => {
  await useTheme(page, "dark")
  await page.goto("/#help-feedback")
  await waitForCollapsedDesktopSidebar(page)
  await expect(page.getByRole("heading", { name: "Help & Feedback" })).toBeVisible()
  await expect(page).toHaveScreenshot("help-feedback-page.png", screenshotOptions)
})

test("Feature request dialog", async ({ page }) => {
  await useTheme(page, "dark")
  await page.goto("/#help-feedback")
  await waitForCollapsedDesktopSidebar(page)
  await page.getByTestId("help-request-feature").click()
  const dialog = page.getByRole("dialog", { name: "Request a feature or change" })
  await dialog.getByLabel("Short title").fill("Filter activity by client")
  await dialog.getByLabel("What should change?").fill("Add an exact client selector to activity views.")
  await dialog.getByLabel("Why would it help?").fill("It would make one playback device easier to review.")
  await expect(page).toHaveScreenshot("feature-request-dialog.png", screenshotOptions)
})

test("Update Center stale-cache recovery", async ({ page }) => {
  await useTheme(page, "dark")
  await page.route("**/api/v1/update/status", (route) => fulfillJson(route, {
    current_version: "1.0.0",
    image_version: "1.0.0",
    runtime_abi: "channelwatch-runtime-v1",
    launcher_protocol: 3,
    runtime_source: "image",
    delivery_mode: "app_update",
    image_refresh_recommended: false,
    settings_schema_version: 7,
    active_bundle: null,
    catalog_state: "stale_cache",
    catalog_checked_at: "2026-08-26T11:55:00Z",
    trusted_target: null,
    cached_release_stale: true,
    operation_state: "idle",
    operation_busy: false,
    latest: { version: "0.9.19", version_tag: "v0.9.19", highlights: ["Hidden stale notes"] },
    update_available: false,
    image_required: false,
    last_job: null,
    rollback_available: false,
    auth_disabled_warning: false,
  }))
  await page.goto("/#settings:updates")
  await waitForCollapsedDesktopSidebar(page)
  await expect(page.getByTestId("update-stale-cache-notice")).toBeVisible()
  await expect(page).toHaveScreenshot("update-center-stale-cache.png", screenshotOptions)
})

test("Update Center v1.0.1 available", async ({ page }) => {
  await useTheme(page, "light")
  await page.route("**/api/v1/update/status", (route) => fulfillJson(route, {
    current_version: "1.0.0",
    image_version: "1.0.0",
    runtime_abi: "channelwatch-runtime-v1",
    launcher_protocol: 3,
    runtime_source: "image",
    delivery_mode: "app_update",
    image_refresh_recommended: false,
    settings_schema_version: 7,
    active_bundle: null,
    catalog_state: "update_available",
    catalog_checked_at: "2026-08-26T12:00:00Z",
    trusted_target: {
      version: "1.0.1",
      version_tag: "v1.0.1",
      image_required: false,
      delivery_mode: "app_update",
      runtime_abi: "channelwatch-runtime-v1",
      settings_schema_version: 7,
      highlights: ["Useful operational alerts and easier feedback."],
    },
    cached_release_stale: false,
    operation_state: "idle",
    operation_busy: false,
    latest: null,
    update_available: true,
    image_required: false,
    last_job: null,
    rollback_available: false,
    auth_disabled_warning: false,
  }))
  await page.goto("/#settings:updates")
  await waitForCollapsedDesktopSidebar(page)
  await expect(page.getByText("v1.0.1", { exact: true })).toBeVisible()
  await expect(page).toHaveScreenshot("update-center-v101-available.png", screenshotOptions)
})
