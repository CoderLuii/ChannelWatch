import { expect, test, type Route } from "@playwright/test"

import { installApiMocks, mockSettings, mockSystemInfo } from "./support/mock-api"

const fulfillJson = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
})

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("alert presets remain unsaved until Save Settings and disclosures are semantic", async ({ page }) => {
  let savedSettings: Record<string, unknown> | null = null
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "POST") {
      savedSettings = route.request().postDataJSON() as Record<string, unknown>
      return fulfillJson(route, { message: "Settings saved successfully" })
    }
    return fulfillJson(route, {
      ...mockSettings,
      notification_preferences_version: 0,
      alert_channel_watching: true,
      alert_vod_watching: false,
      rd_alert_started: false,
      rd_alert_completed: false,
    })
  })

  await page.goto("/#settings:alerts")
  await expect(page.getByText("New operational alerts are available")).toBeVisible()

  const healthDisclosure = page.getByRole("button", { name: /DVR Health/ })
  await expect(healthDisclosure).toHaveAttribute("aria-expanded", "false")
  await healthDisclosure.click()
  await expect(healthDisclosure).toHaveAttribute("aria-expanded", "true")
  await expect(page.getByText("ChannelWatch can report DVR outages only while ChannelWatch itself is running.")).toBeVisible()
  const healthSection = page.locator("#alert-section-health")
  await healthSection.getByRole("button", { name: "Main DVR" }).click()
  await expect(healthDisclosure).toContainText("Inherited")
  await healthSection.getByLabel("DVR Health").click()
  await expect(healthDisclosure).toContainText("Overridden")

  await page.getByLabel("Preset").click()
  await page.getByRole("option", { name: "Important Only" }).click()
  await page.getByRole("button", { name: "Apply preset" }).click()
  await expect(page.getByText(/alert types enabled; .* disabled\. Save Settings/)).toBeVisible()
  expect(savedSettings).toBeNull()

  await page.getByRole("button", { name: "Save Settings" }).click()
  await expect.poll(() => savedSettings).not.toBeNull()
  const persisted = savedSettings as unknown as Record<string, unknown>
  expect(persisted.notification_preferences_version).toBe(1)
  expect(persisted.dvr_alert_unreachable).toBe(true)
  expect(persisted.dvr_alert_recovered).toBe(true)
  expect(persisted.rd_alert_failed).toBe(true)
  expect(persisted.rd_alert_skipped).toBe(true)
  expect(persisted.rd_alert_missed).toBe(true)
  expect(persisted.rd_alert_interrupted).toBe(true)
  expect(persisted.alert_channel_watching).toBe(false)
})

test("Recent Activity applies an exact client without changing the aggregate timeline", async ({ page }) => {
  const clientQueries: string[] = []
  await page.route("**/api/recent-activity**", async (route) => {
    const url = new URL(route.request().url())
    const client = url.searchParams.get("client")
    if (client) clientQueries.push(client)
    return fulfillJson(route, client === "Living Room Apple TV"
      ? [{
          id: "activity-client-filtered",
          type: "watching_channel",
          title: "Filtered live TV activity",
          message: "A selected client is watching live TV",
          timestamp: "2026-04-21T11:45:00Z",
          icon: "tv",
          device_name: "Living Room Apple TV",
          dvr_id: "main-dvr",
          dvr_name: "Main DVR",
        }]
      : [])
  })

  await page.goto("/#overview")
  await page.getByRole("button", { name: "Filter activity by type" }).click()
  await expect(page.getByText("The timeline remains an aggregate across all clients.")).toBeVisible()
  await page.getByRole("option", { name: /Living Room Apple TV/ }).click()

  await expect.poll(() => clientQueries).toContain("Living Room Apple TV")
  await expect(page.getByText("Filtered live TV activity")).toBeVisible()
  await expect(page.getByText("24-Hour Timeline")).toBeVisible()
})

test("Watch History sends the exact client filter and resets it with Clear filters", async ({ page }) => {
  const activityQueries: URL[] = []
  const exportQueries: URL[] = []
  await page.route("**/api/activity-history**", async (route) => {
    const url = new URL(route.request().url())
    activityQueries.push(url)
    return fulfillJson(route, {
      items: [],
      total: 0,
      offset: Number(url.searchParams.get("offset") ?? 0),
      limit: Number(url.searchParams.get("limit") ?? 25),
    })
  })
  await page.route("**/api/v1/history/export**", async (route) => {
    exportQueries.push(new URL(route.request().url()))
    return route.fulfill({
      status: 200,
      contentType: "text/csv",
      headers: { "Content-Disposition": 'attachment; filename="channelwatch-history-all.csv"' },
      body: "id,title\r\nactivity-1,Filtered activity\r\n",
    })
  })

  await page.goto("/#watch-history")
  const clientFilter = page.getByRole("combobox", { name: "Filter activity by exact client" })
  await clientFilter.click()
  await page.getByRole("option", { name: /Living Room Apple TV/ }).click()
  await expect.poll(() => activityQueries.some((url) => url.searchParams.get("client") === "Living Room Apple TV")).toBe(true)

  await page.getByRole("textbox", { name: "Search activity history" }).fill("HBO")
  await page.getByRole("combobox", { name: "Filter activity type" }).click()
  await page.getByRole("option", { name: "Live TV" }).click()
  await page.getByRole("combobox", { name: "Sort activity history" }).click()
  await page.getByRole("option", { name: "Oldest first" }).click()

  const downloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export CSV" }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe("channelwatch-history.csv")
  await expect.poll(() => exportQueries.length).toBe(1)
  expect(exportQueries[0].searchParams.get("client")).toBe("Living Room Apple TV")
  expect(exportQueries[0].searchParams.get("type")).toBe("channel")
  expect(exportQueries[0].searchParams.get("search")).toBe("HBO")
  expect(exportQueries[0].searchParams.get("sort")).toBe("asc")

  await expect.poll(() => new URL(page.url()).searchParams.get("wh_client")).toBe("Living Room Apple TV")
  await page.reload()
  await expect(page.getByRole("textbox", { name: "Search activity history" })).toHaveValue("HBO")
  await expect(page.getByRole("combobox", { name: "Filter activity by exact client" })).toContainText("Living Room Apple TV")
  await expect(page.getByRole("combobox", { name: "Filter activity type" })).toContainText("Live TV")
  await expect(page.getByRole("combobox", { name: "Sort activity history" })).toContainText("Oldest first")

  await page.getByRole("button", { name: "Clear filters" }).click()
  await expect(clientFilter).toContainText("All clients")
  await expect.poll(() => activityQueries.at(-1)?.searchParams.has("client") ?? true).toBe(false)
})

test("ChannelWatch uptime keeps DVR details in a standard dialog", async ({ page }) => {
  await page.route("**/api/settings", (route) => fulfillJson(route, {
    ...mockSettings,
    alert_dvr_health: true,
    dvr_alert_unreachable: true,
    dvr_alert_recovered: true,
  }))
  await page.route("**/api/system-info", (route) => fulfillJson(route, {
    ...mockSystemInfo,
    channelwatch_version: "1.0.1",
    channelwatch_core_started_at: "2026-04-21T10:00:00Z",
    channelwatch_core_uptime_seconds: 7200,
    channelwatch_ui_started_at: "2026-04-21T10:05:00Z",
    channelwatch_ui_uptime_seconds: 6900,
    dvr_status: [
      {
        ...mockSystemInfo.dvr_status[0],
        name: "Main DVR with a deliberately long but readable name",
        started_at: "2026-04-20T10:00:00Z",
        uptime_seconds: 93600,
        uptime_available: true,
        enabled: true,
      },
      {
        ...mockSystemInfo.dvr_status[0],
        id: "offline-dvr",
        name: "Backup DVR",
        connected: false,
        version: null,
        started_at: null,
        uptime_seconds: null,
        uptime_available: false,
        enabled: true,
      },
    ],
  }))

  await page.goto("/#overview")
  const disclosure = page.getByRole("button", { name: /DVR uptime/ })
  await expect(disclosure).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText("ChannelWatch Uptime")).toBeVisible()
  await expect(page.getByText("DVR Health", { exact: true })).toBeVisible()
  await expect(disclosure).toContainText("1 of 2 connected")
  await disclosure.click()

  const dialog = page.getByRole("dialog", { name: "DVR uptime" })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText("Main DVR with a deliberately long but readable name")).toBeVisible()
  await expect(dialog.getByText("Backup DVR")).toBeVisible()
  await expect(dialog.getByText("Disconnected")).toBeVisible()
  await dialog.getByRole("button", { name: "Close" }).first().click()
  await expect(disclosure).toBeFocused()
})

test("feature requests are lightweight and submit no diagnostics", async ({ page }) => {
  let submitted: Record<string, unknown> | null = null
  await page.route("**/api/v1/support/report-dry-run", async (route) => {
    const requestBody = route.request().postData() ?? ""
    const multipartPayload = requestBody.match(/name="payload"\r\n\r\n(\{[\s\S]*?\})\r\n--/)
    submitted = multipartPayload
      ? JSON.parse(multipartPayload[1]) as Record<string, unknown>
      : route.request().postDataJSON() as Record<string, unknown>
    return fulfillJson(route, {
      mode: "dry-run",
      status: "dry-run-complete",
      issue_title: "[Feature] Exact client filter",
      issue_body: "Feature request",
      email_subject: "Feature request",
      email_body: "",
      email_in_public_issue: false,
      attachments: [],
      attachment_total_bytes: 0,
      attachments_sent: false,
    })
  })

  await page.goto("/#help-feedback")
  await expect(page.getByRole("heading", { name: "Help & Feedback" })).toBeVisible()
  await page.getByTestId("help-request-feature").click()
  const dialog = page.getByRole("dialog", { name: "Request a feature or change" })
  await dialog.getByLabel("Short title").fill("Exact client filter")
  await dialog.getByLabel("What should change?").fill("Add an exact client choice to activity views.")
  await dialog.getByLabel("Why would it help?").fill("It would make it easier to find one playback device.")
  await dialog.locator('input[type="file"]').setInputFiles("public/images/channelwatch-logo.png")
  await expect(dialog.getByText("channelwatch-logo.png")).toBeVisible()
  await dialog.getByRole("button", { name: "Submit request" }).click()

  await expect(dialog.getByText("Request submitted")).toBeVisible()
  expect(submitted).toMatchObject({
    kind: "feature",
    area: "dashboard",
    summary: "Exact client filter",
  })
  expect(submitted).not.toHaveProperty("diagnostics")
  expect(submitted).not.toHaveProperty("debug_bundle")

  await dialog.getByTestId("feature-request-success").getByRole("button", { name: "Close" }).click()
  await page.getByTestId("help-request-feature").click()
  const reopened = page.getByRole("dialog", { name: "Request a feature or change" })
  await expect(reopened.getByLabel("Short title")).toHaveValue("")
  await expect(reopened.getByText("channelwatch-logo.png")).toHaveCount(0)
})

test("failed feature submission retains its in-memory draft and screenshot until confirmed discard", async ({ page }) => {
  await page.route("**/api/v1/support/report-dry-run", (route) => fulfillJson(route, {
    detail: { code: "REPORT_TEMPORARILY_UNAVAILABLE", message: "Feedback service is temporarily unavailable." },
  }, 503))

  await page.goto("/#help-feedback")
  await page.getByTestId("help-request-feature").click()
  let dialog = page.getByRole("dialog", { name: "Request a feature or change" })
  await dialog.getByLabel("Short title").fill("Keep this request")
  await dialog.getByLabel("What should change?").fill("Keep the draft after a failed submission.")
  await dialog.getByLabel("Why would it help?").fill("The user should not have to type it twice.")
  await dialog.locator('input[type="file"]').setInputFiles("public/images/channelwatch-logo.png")
  await expect(dialog.getByText("channelwatch-logo.png")).toBeVisible()
  await dialog.getByRole("button", { name: "Submit request" }).click()
  await expect(dialog.getByText("Request not submitted")).toBeVisible()
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click()
  await expect(dialog).toHaveCount(0)

  await page.getByTestId("help-request-feature").click()
  dialog = page.getByRole("dialog", { name: "Request a feature or change" })
  await expect(dialog.getByLabel("Short title")).toHaveValue("Keep this request")
  await expect(dialog.getByText("channelwatch-logo.png")).toBeVisible()
  await dialog.getByRole("button", { name: "Discard draft" }).click()
  await expect(dialog.getByText("Discard this draft and its screenshot? This cannot be undone.")).toBeVisible()
  await dialog
    .getByText("Discard this draft and its screenshot? This cannot be undone.")
    .locator("..")
    .getByRole("button", { name: "Discard draft" })
    .click()
  await expect(dialog).toHaveCount(0)
})

test("feature request configuration can recover without losing the open form", async ({ page }) => {
  let configRequests = 0
  await page.route("**/api/v1/support/report-config", (route) => {
    configRequests += 1
    if (configRequests === 1) {
      return fulfillJson(route, {
        detail: { code: "REPORT_CONFIG_UNAVAILABLE", message: "Feedback settings could not be loaded." },
      }, 503)
    }
    return fulfillJson(route, {
      reporting_enabled: true,
      attachments_enabled: true,
      max_attachment_bytes: 8 * 1024 * 1024,
      max_attachments: 1,
      challenge_required: false,
      turnstile_required: false,
      intake_url: "http://127.0.0.1:19001/report",
    })
  })

  await page.goto("/#help-feedback")
  await page.getByTestId("help-request-feature").click()
  const dialog = page.getByRole("dialog", { name: "Request a feature or change" })
  await dialog.getByLabel("Short title").fill("Keep this while retrying")
  await expect(dialog.getByText("Feedback service unavailable")).toBeVisible()
  await expect(dialog.getByRole("button", { name: "Submit request" })).toBeDisabled()
  await dialog.getByRole("button", { name: "Try again" }).click()

  await expect.poll(() => configRequests).toBe(2)
  await expect(dialog.getByText("Feedback service unavailable")).toHaveCount(0)
  await expect(dialog.getByLabel("Short title")).toHaveValue("Keep this while retrying")
  await expect(dialog.getByRole("button", { name: "Submit request" })).toBeEnabled()
})

test("Update Center ignores an older cached release and offers v1.0.1 as an app update", async ({ page }) => {
  const availableStatus = {
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
    catalog_checked_at: "2026-08-26T12:05:00Z",
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
  }
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
    catalog_checked_at: "2026-08-26T12:00:00Z",
    trusted_target: null,
    cached_release_stale: true,
    operation_state: "idle",
    operation_busy: false,
    latest: {
      version: "0.9.19",
      version_tag: "v0.9.19",
      highlights: ["These old highlights must not be rendered."],
    },
    update_available: false,
    image_required: false,
    last_job: null,
    rollback_available: false,
    auth_disabled_warning: false,
  }))

  await page.goto("/#settings:updates")
  await expect(page.getByText("Current release v1.0.0")).toBeVisible()
  await expect(page.getByTestId("update-stale-cache-notice")).toBeVisible()
  await expect(page.getByText("v0.9.19", { exact: true })).toHaveCount(0)
  await expect(page.getByText("These old highlights must not be rendered.")).toHaveCount(0)

  await page.route("**/api/v1/update/check", (route) => fulfillJson(route, availableStatus))
  await page.getByRole("button", { name: "Check for updates" }).click()
  await expect(page.getByText("v1.0.1", { exact: true })).toBeVisible()
  await expect(page.getByText("A newer container image is required.")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Apply update" })).toBeEnabled()
})

test("Update Center follows an operation started in another tab", async ({ page }) => {
  let statusRequests = 0
  const baseStatus = {
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
    catalog_checked_at: "2026-08-26T12:05:00Z",
    trusted_target: {
      version: "1.0.1",
      version_tag: "v1.0.1",
      image_required: false,
      delivery_mode: "app_update",
      runtime_abi: "channelwatch-runtime-v1",
      settings_schema_version: 7,
      highlights: [],
    },
    cached_release_stale: false,
    latest: null,
    update_available: true,
    image_required: false,
    last_job: null,
    rollback_available: false,
    auth_disabled_warning: false,
  }
  await page.route("**/api/v1/update/status", (route) => {
    statusRequests += 1
    return fulfillJson(route, {
      ...baseStatus,
      operation_state: statusRequests === 1 ? "applying" : "idle",
      operation_busy: statusRequests === 1,
    })
  })

  await page.goto("/#settings:updates")
  await expect(page.getByRole("heading", { name: "Update operation in progress: applying." })).toBeVisible()
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "Apply update" })).toBeDisabled()

  await expect.poll(() => statusRequests, { timeout: 6_000 }).toBeGreaterThanOrEqual(2)
  await expect(page.getByRole("button", { name: "Check for updates" })).toBeEnabled()
  await expect(page.getByRole("button", { name: "Apply update" })).toBeEnabled()
})
