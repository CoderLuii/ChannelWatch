import { expect, test, type Locator, type Page } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
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
  await page.getByRole("button", { name: "Submit report" }).click()
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
  await page.getByRole("button", { name: "Submit report" }).click()

  await expect(page.getByText("Could not submit report.")).toBeVisible()
  await expect(page.getByText("Report renderer unavailable")).toBeVisible()
  await expect(page.getByRole("heading", { name: "Manual upload fallback" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Copy support code" })).toBeVisible()
})

test("report problem submits directly without an in-app Cloudflare check", async ({ page }) => {
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
  expect(submittedBodies[0]?.support_code).toMatch(/^CW-REPORT-v1-/)
  expect(submittedBodies[0]?.turnstile_token).toBeUndefined()
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
