import { expect, test, type Locator, type Page, type Route } from "@playwright/test"
import { createHash } from "node:crypto"

import { installApiMocks } from "./support/mock-api"

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("legacy key recovery keeps the authenticated dashboard reachable", async ({ page }) => {
  await page.route("**/api/v1/runtime/preflight", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "setup_required",
      setup_required: true,
      blockers: ["protected_credentials_locked"],
      warnings: [],
    }),
  }))

  await page.goto("/")

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  const warning = page.getByTestId("runtime-recovery-warning")
  await expect(warning).toBeVisible()
  await expect(warning).toContainText("Protected credentials need administrator attention")
  await expect(page.getByText("openssl rand -base64 48", { exact: true })).toHaveCount(0)
  await warning.getByRole("button", { name: "Open Security recovery" }).click()
  await expect(page).toHaveURL(/#settings:security$/)
})

test("legacy recovery submits the old value once and clears the browser field", async ({ page }) => {
  const legacyValue = "legacy-review-only-0123456789abcdef"
  await page.route("**/api/v1/runtime/key-recovery/status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      state: "legacy_recovery_required",
      recovery_required: true,
      can_migrate: true,
      can_reset: true,
      blocker_code: "protected_credentials_locked",
      affected_dvr_credentials: 1,
      affected_notification_credentials: 0,
      legacy_input_detected: false,
      message: null,
    }),
  }))
  await page.route("**/api/v1/runtime/key-recovery/migrate", async (route) => {
    expect(await route.request().headerValue("content-type")).toContain("multipart/form-data")
    expect(route.request().postData()).toContain('name="legacy_storage_key"')
    expect(route.request().postData()).toContain(legacyValue)
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "managed_local",
        recovery_required: false,
        can_migrate: false,
        can_reset: false,
        blocker_code: null,
        affected_dvr_credentials: 1,
        affected_notification_credentials: 0,
        legacy_input_detected: false,
        message: "Credential protection recovered. Monitoring is resuming.",
        backup_created: false,
        restart_required: false,
      }),
    })
  })

  await page.goto("/#settings:security")
  const recovery = page.getByTestId("key-recovery-card")
  await expect(recovery).toBeVisible()
  const wrappingInput = page.getByLabel("Old v0.9.5-v0.9.17 wrapping value")
  await wrappingInput.fill(legacyValue)
  await recovery.getByRole("button", { name: "Migrate protected key" }).click()

  await expect(page.getByTestId("key-recovery-complete")).toContainText("Credential protection recovered")
  await expect(wrappingInput).toHaveCount(0)
  expect(await page.evaluate(() => ({
    legacy: localStorage.getItem("legacy_storage_key"),
    recovery: localStorage.getItem("key_recovery"),
  }))).toEqual({ legacy: null, recovery: null })
})

test("setup shell can apply only a confirmed official signed recovery update", async ({ page }) => {
  const bootstrapCsrf = "bootstrap-browser-review-token"
  const latest = {
    version: "0.9.18",
    version_tag: "v0.9.18",
    image_required: false,
    runtime_abi: "channelwatch-runtime-v1",
    settings_schema_version: 7,
  }
  const recoveryStatus = {
    current_version: "0.9.17",
    image_version: "0.9.17",
    runtime_abi: "channelwatch-runtime-v1",
    launcher_protocol: 1,
    runtime_source: "image",
    delivery_mode: "app_update",
    image_refresh_recommended: false,
    settings_schema_version: 7,
    active_bundle: null,
    latest,
    update_available: true,
    image_required: false,
    last_job: null,
    rollback_available: false,
    auth_disabled_warning: false,
    recovery_active: true,
    bootstrap_csrf: bootstrapCsrf,
    confirmation_required: true,
  }
  await page.route("**/api/settings", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: { code: "ERR_AUTH_UNAUTHENTICATED", message: "Sign in required." } }),
  }))
  await page.route("**/api/v1/runtime/preflight", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "setup_required", setup_required: true, blockers: ["protected_credentials_locked"], warnings: [] }),
  }))
  await page.route("**/api/v1/auth/setup-status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ setup_required: true, configured_mode: "rbac", effective_mode: "rbac", available_modes: ["rbac", "none"] }),
  }))
  await page.route("**/api/v1/security/status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      auth_disabled: false,
      configured_mode: "rbac",
      current_mode: "rbac",
      effective_mode: "rbac",
      persisted_mode: "rbac",
      runtime_auth_override_active: false,
      api_key_required: false,
      api_key_configured: false,
      api_key_fallback_active: false,
      session_auth_active: false,
      setup_required: true,
      session_setup_required: true,
      security_mode: "RBAC_ONLY",
      encryption_key_path: "/config/encryption.key",
    }),
  }))
  await page.route("**/api/v1/update/recovery/status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(recoveryStatus),
  }))
  await page.route("**/api/v1/update/recovery/check", async (route) => {
    expect(await route.request().headerValue("x-csrf-token")).toBe(bootstrapCsrf)
    expect(route.request().postDataJSON()).toEqual({})
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...recoveryStatus, bootstrap_csrf: null }),
    })
  })
  await page.route("**/api/v1/update/recovery/apply", async (route) => {
    expect(await route.request().headerValue("x-csrf-token")).toBe(bootstrapCsrf)
    expect(route.request().postDataJSON()).toEqual({
      version: "0.9.18",
      confirmation: "INSTALL OFFICIAL UPDATE",
    })
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "recovery-browser-job",
        operation: "apply",
        status: "success",
        version: "0.9.18",
        message: "Official recovery update installed.",
        restart_required: false,
      }),
    })
  })

  await page.goto("/")
  await expect(page.getByText("Set up ChannelWatch", { exact: true })).toBeVisible()
  const actions = page.getByTestId("official-recovery-update-actions")
  await expect(actions).toBeVisible()
  await actions.getByRole("button", { name: "Check official channel" }).click()
  const confirmation = page.getByLabel(/INSTALL OFFICIAL UPDATE/)
  await confirmation.fill("INSTALL OFFICIAL UPDATE")
  await actions.getByRole("button", { name: "Apply signed v0.9.18 update" }).click()
  await expect(actions).toContainText("Official recovery update installed.")
  await expect(confirmation).toHaveValue("")
  expect(await page.evaluate(() => sessionStorage.getItem("recovery_bootstrap_csrf"))).toBeNull()
})

test("Update Center defaults to automatic signed updates in the 03:00–05:00 window", async ({ page }) => {
  await page.goto("/#settings:updates")

  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible()
  await expect(page.getByText("Application version")).toBeVisible()
  await expect(page.getByText("Container image version")).toBeVisible()
  await expect(page.getByText("Launcher protocol 1")).toBeVisible()
  await expect(page.getByRole("button", { name: "Automatic during maintenance window" })).toHaveAttribute("aria-pressed", "true")
  await expect(page.getByText("Maintenance window: 03:00–05:00 local time")).toBeVisible()
  await expect(page.getByText("A compatible base-image refresh is recommended later.")).toBeVisible()
})

test("Update Center validates and saves an editable local maintenance window", async ({ page }) => {
  let savedPolicy: Record<string, unknown> | null = null
  await page.route("**/api/v1/update/policy", async (route) => {
    const defaultPolicy = {
      mode: "automatic",
      maintenance_window_start: "03:00",
      maintenance_window_minutes: 120,
      scheduled_restart_at: null,
      postpone_available: true,
    }
    if (route.request().method() === "PUT") {
      savedPolicy = route.request().postDataJSON()
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(savedPolicy) })
      return
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(defaultPolicy) })
  })

  await page.goto("/#settings:updates")
  await page.getByLabel("Maintenance window start (local time)").fill("04:30")
  await page.getByLabel("Window duration in minutes").fill("90")
  const policySaved = page.waitForResponse((response) => (
    response.url().endsWith("/api/v1/update/policy")
    && response.request().method() === "PUT"
  ))
  await page.getByRole("button", { name: "Save maintenance window" }).click()
  await policySaved
  await expect.poll(() => savedPolicy).toEqual({
    mode: "automatic",
    maintenance_window_start: "04:30",
    maintenance_window_minutes: 90,
  })
  await expect(page.getByText("Maintenance window: 04:30–06:00 local time")).toBeVisible()

  await page.getByLabel("Window duration in minutes").fill("10")
  await expect(page.getByRole("alert").filter({ hasText: "Choose a valid local start time" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Save maintenance window" })).toBeDisabled()
})

test("terminal retry and rollback failures restore Update Center controls", async ({ page }) => {
  const failedStatus = {
    current_version: "0.9.18",
    image_version: "0.9.18",
    runtime_abi: "channelwatch-runtime-v1",
    launcher_protocol: 3,
    runtime_source: "image",
    delivery_mode: "app_update",
    image_refresh_recommended: false,
    settings_schema_version: 7,
    active_bundle: null,
    latest: null,
    update_available: false,
    image_required: false,
    rollback_available: true,
    auth_disabled_warning: false,
    last_job: {
      job_id: "failed-before-retry",
      operation: "apply",
      status: "failed",
      version: "0.9.18",
      message: "Previous update attempt failed.",
      restart_required: true,
    },
  }
  await page.route("**/api/v1/update/status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(failedStatus),
  }))
  await page.route("**/api/v1/update/retry", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      job_id: "failed-retry",
      operation: "apply",
      status: "failed",
      version: "0.9.18",
      message: "Retry restart was rejected.",
      restart_required: true,
    }),
  }))
  await page.route("**/api/v1/update/rollback", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      job_id: "failed-rollback",
      operation: "rollback",
      status: "failed",
      version: "0.9.18",
      message: "Rollback restart was rejected.",
      restart_required: true,
      rollback_applied: false,
    }),
  }))

  await page.goto("/#settings:updates")
  const retry = page.getByRole("button", { name: "Retry now" })
  const rollback = page.getByRole("button", { name: "Roll back" })

  await retry.click()
  await expect(page.getByText("Retry restart was rejected.")).toBeVisible()
  await expect(retry).toBeEnabled()
  await expect(rollback).toBeEnabled()

  await rollback.click()
  await expect(page.getByText("Rollback restart was rejected.")).toBeVisible()
  await expect(retry).toBeEnabled()
  await expect(rollback).toBeEnabled()
})

test("closing and reopening a report retains in-memory text and attachments until discard", async ({ page }) => {
  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Retain this report draft")
  await page.getByLabel("Screenshots").setInputFiles("public/images/channelwatch-logo.png")
  await expect(page.getByText("channelwatch-logo.png")).toBeVisible()

  await page.getByRole("button", { name: "Cancel", exact: true }).click()
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await expect(page.getByLabel("Problem summary")).toHaveValue("Retain this report draft")
  await expect(page.getByText("channelwatch-logo.png")).toBeVisible()

  await page.getByRole("button", { name: "Discard draft", exact: true }).click()
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await expect(page.getByLabel("Problem summary")).toHaveValue("")
  await expect(page.getByText("channelwatch-logo.png")).toHaveCount(0)
})

test("dirty report draft shows a scheduled restart countdown and one postponement", async ({ page }) => {
  const scheduledRestart = new Date(Date.now() + 5 * 60 * 1000).toISOString()
  await page.route("**/api/v1/update/policy", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      mode: "automatic",
      maintenance_window_start: "03:00",
      maintenance_window_minutes: 120,
      scheduled_restart_at: scheduledRestart,
      postpone_available: true,
    }),
  }))
  await page.route("**/api/v1/update/postpone", async (route) => {
    expect(route.request().postDataJSON()).toEqual({ hours: 24, reason: "dirty_report_draft" })
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "automatic",
        maintenance_window_start: "03:00",
        maintenance_window_minutes: 120,
        scheduled_restart_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
        postpone_available: false,
      }),
    })
  })

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Keep this private draft during the update")

  const warning = page.getByTestId("report-scheduled-restart-warning")
  await expect(warning).toBeVisible()
  await expect(warning).toContainText(/ChannelWatch is scheduled to restart in \d+m \d{2}s/)
  await warning.getByRole("button", { name: "Postpone once for 24 hours" }).click()
  await expect(warning).toContainText("The one-time draft postponement has already been used.")
  await expect(page.getByLabel("Problem summary")).toHaveValue("Keep this private draft during the update")
})

test("report review works without randomUUID or network access", async ({ page, context }) => {
  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByRole("button", { name: "Review report" }).click()
  await expect(page.getByLabel("Problem summary")).toBeFocused()

  await page.getByLabel("Problem summary").fill("LAN report review")
  await page.evaluate(() => {
    Object.defineProperty(globalThis.crypto, "randomUUID", { value: undefined, configurable: true })
  })
  await context.setOffline(true)
  await page.getByRole("button", { name: "Review report" }).click()
  await expect(page.getByTestId("report-problem-review")).toBeVisible()
})

const pasteClipboardImage = async (page: Page, selector: string, filename: string) => {
  await page.evaluate(
    ({ selector, filename }) => {
      const file = new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], filename, {
        type: "image/png",
      })
      const event = new Event("paste", { bubbles: true, cancelable: true })
      Object.defineProperty(event, "clipboardData", {
        value: { items: [{ kind: "file", getAsFile: () => file }] },
      })
      document.querySelector(selector)?.dispatchEvent(event)
    },
    { selector, filename },
  )
}

const expectNoDialogHorizontalShift = async (reportDialog: Locator) => {
  await expect
    .poll(() =>
      reportDialog.evaluate((element) => {
        const bounds = element.getBoundingClientRect()
        const visibleChildrenInside = Array.from(element.querySelectorAll<HTMLElement>("*")).every(
          (child) => {
            if (child.offsetParent === null) return true
            const style = window.getComputedStyle(child)
            if (style.position === "absolute" || style.position === "fixed") return true
            const rect = child.getBoundingClientRect()
            return rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1
          },
        )
        const scrollBody = element.querySelector<HTMLElement>('[data-testid="report-problem-scroll-body"]')
        return visibleChildrenInside && (!scrollBody || scrollBody.scrollLeft === 0)
      }),
    )
    .toBe(true)
}

test("release-day smoke: configured bootstrap, core navigation, and diagnostics admin flow", async ({ page }) => {
  await page.goto("/#overview")

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  await expect(page.getByText("1 live stream, 1 recording in progress")).toBeVisible()
  await expect(page.getByText("10.94 TB Free")).toBeVisible()
  await expect(page.getByText("10.94 TB GB Free")).toHaveCount(0)

  await page.getByRole("button", { name: "Watch History", exact: true }).click()
  await expect(page).toHaveURL(/#watch-history$/)
  await expect(page.getByText("Watch History", { exact: true })).toBeVisible()
  await expect(page.getByRole("textbox", { name: "Search activity history" })).toBeVisible()

  await page.getByRole("button", { name: "Notification Log", exact: true }).click()
  await expect(page).toHaveURL(/#notification-log$/)
  await expect(page.getByText("Notification Delivery Log")).toBeVisible()
  await expect(page.getByRole("combobox", { name: "Filter notification log by channel" })).toBeVisible()

  await page.getByRole("button", { name: "Settings", exact: true }).click()
  await expect(page).toHaveURL(/#settings$/)
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible()
  await expect(page.getByRole("tab", { name: "Security" })).toBeVisible()

  await page.getByRole("button", { name: "Diagnostics", exact: true }).click()
  await expect(page).toHaveURL(/#diagnostics$/)
  await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible()
  await expect(page.getByText("10.94 TB").first()).toBeVisible()
  await expect(page.getByText("ChannelWatch started successfully")).toBeVisible()

  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  const reportDialog = page.getByRole("dialog", { name: "Report a Problem" })
  await expect(reportDialog).toBeVisible()
  await expect(page.getByText("No contact method provided")).toBeVisible()
  await page.getByLabel("Problem summary").fill("Active Streams shows a stream but no activity appears")
  await page.getByLabel("What did you expect? (Optional)").fill("A channel watching activity event should appear.")
  await page.getByLabel("GetChannels username (Optional)").fill("@Matthew_Crommert")
  await page.getByLabel("Email (Optional)").fill("viewer@example.com")
  await page.getByLabel("Screenshots").setInputFiles("public/images/channelwatch-logo.png")
  await expect(page.getByText("channelwatch-logo.png")).toBeVisible()
  await pasteClipboardImage(page, "#report-summary", "summary-paste.png")
  await expect(page.getByText("summary-paste.png")).toHaveCount(0)
  await page.getByTestId("report-screenshot-dropzone").focus()
  await pasteClipboardImage(page, '[data-testid="report-screenshot-dropzone"]', "clipboard-image.png")
  await expect(page.getByText("clipboard-image.png")).toBeVisible()
  await expectNoDialogHorizontalShift(reportDialog)
  await page.getByLabel("Debug bundle ZIP").setInputFiles({
    name: "TC-Helicon_GoXLR_Driver.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("PK\u0003\u0004not-a-channelwatch-debug-bundle"),
  })
  await expect(page.getByText("Attach a ChannelWatch-generated debug bundle ZIP.")).toBeVisible()
  await expect(page.getByText("TC-Helicon_GoXLR_Driver.zip")).toHaveCount(0)
  await page.getByRole("button", { name: "Create fresh debug bundle" }).click()
  await expect(page.getByText(/channelwatch_debug_.*\.zip/)).toBeVisible()
  await page.getByRole("button", { name: "Review report" }).click()
  await expect(page.getByTestId("report-problem-review")).toBeVisible()
  await expect(page.getByRole("heading", { name: "Report preview" })).toBeVisible()
  await expect(page.getByText("Private attachments")).toBeVisible()
  await expect(page.getByText("Email and attached files are shared only with CoderLuii for follow-up and troubleshooting.")).toBeVisible()
  await expect(page.getByText("viewer@example.com")).toHaveCount(0)
  await page.getByRole("button", { name: "Preview report" }).click()
  await expect(page.getByTestId("report-problem-success")).toBeVisible()
  await expect(page.getByText("Dry run complete")).toBeVisible()
  await expect(page.getByText("The report and attachments were validated locally. Nothing was sent.")).toBeVisible()
  await expect(page.getByText("channelwatch_debug_test.zip")).toBeVisible()
  await page.getByRole("button", { name: "Done" }).click()

  const downloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download sanitized debug bundle" }).click()
  const download = await downloadPromise

  expect(download.suggestedFilename()).toMatch(/^channelwatch_debug_.*\.zip$/)
  await expect.poll(() => download.failure()).toBeNull()
})

test("report problem shows dry-run API failures", async ({ page }) => {
  await page.route("**/api/v1/support/report-dry-run", async (route) => {
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Report renderer unavailable" }),
    })
  })

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Dry-run failure test")
  await page.getByRole("button", { name: "Review report" }).click()
  await page.getByRole("button", { name: "Preview report" }).click()

  await expect(page.getByText("Could not submit report.")).toBeVisible()
  await expect(page.getByText("Report renderer unavailable")).toBeVisible()
  await expect(page.getByRole("heading", { name: "Manual upload fallback" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Copy support code" })).toBeVisible()
})

test("report problem prepares anonymous proof without a visible challenge", async ({ page }) => {
  await page.route("**/api/v1/support/report-config", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "live",
        endpoint: "https://channelwatch.coderluii.dev/api/reports",
        portal_url: "https://channelwatch.coderluii.dev/report",
        max_bytes: 262144,
        turnstile_site_key: "1x00000000000000000000AA",
        attachments_enabled: true,
        max_attachment_bytes: 8388608,
        max_total_attachment_bytes: 20971520,
        max_screenshot_count: 5,
        allowed_attachment_types: [
          "image/png",
          "image/jpeg",
          "image/webp",
          "application/zip",
          "application/x-zip-compressed",
          "application/octet-stream",
        ],
      }),
    })
  })

  const submittedBodies: Array<{ support_code?: string; turnstile_token?: string }> = []
  const submittedHeaders: Array<Record<string, string>> = []
  await page.route("https://channelwatch.coderluii.dev/api/reports/challenge", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      nonce: "browser-test", expires_at: Date.now() + 60_000, route_class: "in_app",
      difficulty: 16, key_id: "current", signature: "signed",
    }) })
  })
  await page.route("https://channelwatch.coderluii.dev/api/reports", async (route) => {
    submittedHeaders.push(route.request().headers())
    submittedBodies.push(route.request().postDataJSON() as { support_code?: string; turnstile_token?: string })
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "live",
        status: "live-ready",
        issue_title: "[In-App] Direct secure upload test",
        issue_body: "report body",
        email_subject: "ChannelWatch Issue #123",
        email_body: "private body",
        email_in_public_issue: false,
        attachments: [],
        attachment_total_bytes: 0,
        attachments_sent: true,
      }),
    })
  })

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Direct secure upload test")
  await page.getByRole("button", { name: "Review report" }).click()
  await expect(page.getByText("Secure upload check")).toHaveCount(0)
  await expect(page.getByTestId("manual-upload-panel")).toHaveCount(0)

  await page.getByRole("button", { name: "Submit report" }).click()

  await expect(page.getByTestId("report-problem-success")).toBeVisible()
  await expect(page.getByText("Report submitted")).toBeVisible()
  expect(submittedHeaders[0]?.["x-channelwatch-in-app-report"]).toBe("1")
  expect(submittedHeaders[0]?.["x-channelwatch-report-challenge"]).toBeTruthy()
  const proof = JSON.parse(Buffer.from(
    submittedHeaders[0]["x-channelwatch-report-challenge"],
    "base64url",
  ).toString("utf8")) as { nonce: string; difficulty: number; solution: string }
  const proofDigest = createHash("sha256").update(`${proof.nonce}.${proof.solution}`).digest()
  expect(proof.difficulty).toBe(16)
  expect(proofDigest[0]).toBe(0)
  expect(proofDigest[1]).toBe(0)
  expect(submittedBodies[0]?.support_code).toMatch(/^CW-REPORT-v2-/)
  expect(submittedBodies[0]?.turnstile_token).toBeUndefined()
})

test("report problem retries failed private delivery without creating a new draft", async ({ page }) => {
  await page.route("**/api/v1/support/report-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "live",
        endpoint: "https://channelwatch.coderluii.dev/api/reports",
        portal_url: "https://channelwatch.coderluii.dev/report",
        max_bytes: 262144,
        attachments_enabled: true,
        max_attachment_bytes: 8388608,
        max_total_attachment_bytes: 20971520,
        max_screenshot_count: 5,
        allowed_attachment_types: ["image/png", "image/jpeg", "image/webp", "application/zip"],
      }),
    })
  })

  const supportCodes: string[] = []
  let attempt = 0
  await page.route("https://channelwatch.coderluii.dev/api/reports/challenge", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      nonce: `retry-${attempt}`, expires_at: Date.now() + 60_000, route_class: "in_app",
      difficulty: 0, key_id: "current", signature: "signed",
    }) })
  })
  const handlePrivateDelivery = async (route: Route) => {
    attempt += 1
    const body = route.request().postDataJSON() as { support_code: string }
    supportCodes.push(body.support_code)
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "live",
        status: attempt === 1 ? "completed_with_private_delivery_failure" : "completed",
        report_id: "report-stable-123",
        issue_url: "https://github.com/CoderLuii/ChannelWatch/issues/123",
        private_delivery_status: attempt === 1 ? "failed" : "delivered",
        issue_title: "[In-App] Private delivery retry",
        issue_body: "report body",
        email_subject: "ChannelWatch Issue #123",
        email_body: "private body",
        email_in_public_issue: false,
        attachments: [],
        attachment_total_bytes: 0,
        attachments_sent: attempt > 1,
      }),
    })
  }
  await page.route("https://channelwatch.coderluii.dev/api/reports", handlePrivateDelivery)
  await page.route("https://channelwatch.coderluii.dev/api/reports/retry-private", handlePrivateDelivery)

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Private delivery retry")
  await page.getByRole("button", { name: "Review report" }).click()
  await page.getByRole("button", { name: "Submit report" }).click()

  await expect(page.getByTestId("report-private-delivery-warning")).toBeVisible()
  await expect(page.getByText("Private attachments not delivered")).toBeVisible()
  await expect(page.getByRole("link", { name: "Open GitHub issue" })).toHaveAttribute(
    "href",
    "https://github.com/CoderLuii/ChannelWatch/issues/123",
  )
  await page.getByRole("button", { name: "Retry private delivery" }).click()

  await expect(page.getByTestId("report-private-delivery-warning")).toHaveCount(0)
  await expect(page.getByText("Report submitted")).toBeVisible()
  await expect(page.getByText("Private delivery: delivered")).toBeVisible()
  expect(supportCodes).toHaveLength(2)
  expect(supportCodes[0]).toMatch(/^CW-REPORT-v2-/)
  expect(supportCodes[1]).toBe(supportCodes[0])
})

test("provider confirmation pending is truthful and keeps the stable reference", async ({ page }) => {
  await page.route("**/api/v1/support/report-config", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      mode: "live", endpoint: "https://channelwatch.coderluii.dev/api/reports",
      portal_url: "https://channelwatch.coderluii.dev/report", max_bytes: 262144,
      attachments_enabled: true, max_attachment_bytes: 8388608,
      max_total_attachment_bytes: 20971520, max_screenshot_count: 5, allowed_attachment_types: [],
    }),
  }))
  await page.route("https://channelwatch.coderluii.dev/api/reports/challenge", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ nonce: "pending", expires_at: Date.now() + 60000, route_class: "in_app", difficulty: 0, key_id: "current", signature: "signed" }),
  }))
  await page.route("https://channelwatch.coderluii.dev/api/reports", (route) => route.fulfill({
    status: 202, contentType: "application/json", body: JSON.stringify({ status: "provider_confirmation_pending", mode: "live", report_id: "stable-report-reference", correlation_id: "safe-correlation" }),
  }))
  let statusChecks = 0
  await page.route("https://channelwatch.coderluii.dev/api/reports/status", (route) => {
    statusChecks += 1
    const submitted = route.request().postDataJSON() as { support_code: string }
    expect(submitted.support_code).toMatch(/^CW-REPORT-v2-/)
    expect(route.request().url()).not.toContain(submitted.support_code)
    if (statusChecks === 1) return route.fulfill({
      status: 202, contentType: "application/json", body: JSON.stringify({ status: "provider_confirmation_pending", mode: "live", report_id: "stable-report-reference" }),
    })
    if (statusChecks === 2) return route.fulfill({
      status: 503, contentType: "application/json", body: JSON.stringify({ detail: { code: "REPORT_STATUS_UNAVAILABLE", message: "Status is temporarily unavailable." } }),
    })
    return route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({
        status: "completed", mode: "live", report_id: "stable-report-reference",
        issue_url: "https://github.com/CoderLuii/ChannelWatch/issues/456",
        issue_title: "[In-App] Pending provider confirmation", issue_body: "report body",
        email_subject: "ChannelWatch issue", email_body: "private body",
        email_in_public_issue: false, attachments: [], attachment_total_bytes: 0,
        attachments_sent: true, private_delivery_status: "delivered",
      }),
    })
  })
  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Pending provider confirmation")
  await page.getByRole("button", { name: "Review report" }).click()
  await page.getByRole("button", { name: "Submit report" }).click()
  await expect(page.getByTestId("report-provider-confirmation-pending")).toBeVisible()
  await expect(page.getByText("Report confirmation pending")).toBeVisible()
  await expect(page.getByText("Report reference: stable-report-reference")).toBeVisible()
  await expect(page.getByText("The report and attachments were validated locally. Nothing was sent.")).toHaveCount(0)
  await expect(page.getByText("Report submitted")).toHaveCount(0)
  await page.getByRole("button", { name: "Check report status" }).click()
  await expect(page.getByTestId("report-provider-confirmation-pending")).toBeVisible()
  await page.getByRole("button", { name: "Check report status" }).click()
  await expect(page.getByText("Status is temporarily unavailable.")).toBeVisible()
  await page.getByRole("button", { name: "Check report status" }).click()
  await expect(page.getByText("Report submitted")).toBeVisible()
  await expect(page.getByRole("link", { name: "Open GitHub issue" })).toHaveAttribute("href", "https://github.com/CoderLuii/ChannelWatch/issues/456")
})

test("slow secure preparation can be cancelled and keeps manual fallbacks", async ({ page }) => {
  await page.route("**/api/v1/support/report-config", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      mode: "live", endpoint: "https://channelwatch.coderluii.dev/api/reports",
      portal_url: "https://channelwatch.coderluii.dev/report", max_bytes: 262144,
      attachments_enabled: true, max_attachment_bytes: 8388608,
      max_total_attachment_bytes: 20971520, max_screenshot_count: 5,
      allowed_attachment_types: ["image/png", "image/jpeg", "image/webp", "application/zip"],
    }),
  }))
  await page.route("https://channelwatch.coderluii.dev/api/reports/challenge", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      nonce: "intentionally-slow", expires_at: Date.now() + 60_000, route_class: "in_app",
      difficulty: 24, key_id: "current", signature: "signed",
    }),
  }))
  let reportRequests = 0
  await page.route("https://channelwatch.coderluii.dev/api/reports", (route) => {
    reportRequests += 1
    return route.abort()
  })

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Cancel slow preparation")
  await page.getByRole("button", { name: "Review report" }).click()
  await page.getByRole("button", { name: "Submit report" }).click()
  await expect(page.getByRole("button", { name: "Preparing secure submission..." })).toBeVisible()
  await page.getByRole("button", { name: "Cancel", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Manual upload fallback" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Download support code" })).toBeVisible()
  expect(reportRequests).toBe(0)
})

test("report problem attachments stay aligned on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 })
  await page.goto("/#diagnostics")
  await page.locator('[data-testid="report-problem-open"]').dispatchEvent("click")

  const reportDialog = page.getByRole("dialog", { name: "Report a Problem" })
  await expect(reportDialog).toBeVisible()
  await page.getByLabel("Problem summary").fill("Mobile attachment layout test")
  await page.getByLabel("Screenshots").setInputFiles("public/images/channelwatch-logo.png")
  await page.getByRole("button", { name: "Create fresh debug bundle" }).click()
  await expect(page.getByText(/channelwatch_debug_.*\.zip/)).toBeVisible()

  await expectNoDialogHorizontalShift(reportDialog)
  await expect(page.getByRole("button", { name: "Attach previous debug bundle" })).toBeVisible()
})
