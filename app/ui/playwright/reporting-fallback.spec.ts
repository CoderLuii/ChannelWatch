import { expect, test } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

const liveConfig = {
  mode: "live",
  endpoint: "https://channelwatch.coderluii.dev/api/reports",
  portal_url: "https://channelwatch.coderluii.dev/report",
  max_bytes: 262144,
  turnstile_site_key: null,
  attachments_enabled: true,
  max_attachment_bytes: 8388608,
  max_total_attachment_bytes: 20971520,
  max_screenshot_count: 5,
  allowed_attachment_types: ["image/png", "image/jpeg", "image/webp", "application/zip"],
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
  await page.route("**/api/v1/support/report-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(liveConfig),
    })
  })
})

test("blocked hosted reporting preserves one exact support code across both downloads", async ({ page }) => {
  await page.route("https://channelwatch.coderluii.dev/api/reports**", async (route) => {
    await route.abort("blockedbyclient")
  })

  let packageSupportCode = ""
  await page.route("**/api/v1/support/offline-package", async (route) => {
    const requestBody = route.request().postDataBuffer()?.toString("utf8") ?? ""
    const match = requestBody.match(/CW-REPORT-v2-[A-Za-z0-9_-]+/)
    packageSupportCode = match?.[0] ?? ""
    await route.fulfill({
      status: 200,
      contentType: "application/zip",
      headers: { "Content-Disposition": 'attachment; filename="channelwatch-support-report.zip"' },
      body: `mock-package-containing:${packageSupportCode}`,
    })
  })

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Hosted endpoint blocked by network policy")
  await page.getByRole("button", { name: "Review report" }).click()
  await page.getByRole("button", { name: "Submit report" }).click()

  await expect(page.getByText("Could not submit report.")).toBeVisible()
  const supportCodeField = page.getByTestId("report-support-code")
  await expect(supportCodeField).toBeVisible()
  const visibleSupportCode = await supportCodeField.inputValue()
  expect(visibleSupportCode).toMatch(/^CW-REPORT-v2-/)

  const codeDownloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download support code" }).click()
  const codeDownload = await codeDownloadPromise
  expect(codeDownload.suggestedFilename()).toBe("channelwatch-support-code.txt")
  const codeStream = await codeDownload.createReadStream()
  const codeChunks: Buffer[] = []
  for await (const chunk of codeStream) codeChunks.push(Buffer.from(chunk))
  expect(Buffer.concat(codeChunks).toString("utf8")).toBe(`${visibleSupportCode}\n`)

  const packageDownloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download offline package" }).click()
  const packageDownload = await packageDownloadPromise
  await expect.poll(() => packageSupportCode).toBe(visibleSupportCode)
  expect(packageDownload.suggestedFilename()).toMatch(/^channelwatch_support_report_.*\.zip$/)
})

test("blocked hosted reporting keeps the reviewed draft available", async ({ page }) => {
  await page.route("https://channelwatch.coderluii.dev/api/reports**", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Reporting service is temporarily unavailable." }),
    })
  })

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Keep this draft after failure")
  await page.getByRole("button", { name: "Review report" }).click()
  await page.getByRole("button", { name: "Submit report" }).click()

  await expect(page.getByText("Reporting service is temporarily unavailable.")).toBeVisible()
  await expect(page.getByText("Keep this draft after failure", { exact: true })).toBeVisible()
  await expect(page.getByTestId("report-support-code")).toHaveValue(/^CW-REPORT-v2-/)
  await expect(page.getByRole("link", { name: "Open upload portal" })).toHaveAttribute(
    "href",
    "https://channelwatch.coderluii.dev/report",
  )
})
