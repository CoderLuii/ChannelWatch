import { expect, test } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Crypto.prototype, "randomUUID", {
      configurable: true,
      value: undefined,
    })
  })
  await installApiMocks(page)
})

test("insecure LAN review uses getRandomValues and stays local before submit", async ({ page, context }) => {
  const hostedReportRequests: string[] = []
  page.on("request", (request) => {
    if (request.url().startsWith("https://channelwatch.coderluii.dev/api/reports")) {
      hostedReportRequests.push(request.url())
    }
  })

  await page.goto("/#diagnostics")
  await expect.poll(() => page.evaluate(() => window.isSecureContext)).toBe(false)
  await expect.poll(() => page.evaluate(() => typeof crypto.randomUUID)).toBe("undefined")
  await expect.poll(() => page.evaluate(() => typeof crypto.getRandomValues)).toBe("function")

  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Insecure LAN report review")
  await context.setOffline(true)
  await page.getByRole("button", { name: "Review report" }).click()

  await expect(page.getByTestId("report-problem-review")).toBeVisible()
  expect(hostedReportRequests).toEqual([])
})

test("LAN review visibly redacts private report text before submit", async ({ page }) => {
  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page
    .getByLabel("Problem summary")
    .fill("Local fe80::beef fe80::1%eth0 fd12::1%5 ![tracker][pixel] admin@example.com")
  await page
    .getByLabel("What did you expect?")
    .fill(
      "Public 2001:db8::1 remains; https://files.example.com/report?X-Amz-Signature=secret&" +
        "AWSAccessKeyId=AKIAEXAMPLE&GoogleAccessId=gcs-signer&sig=compact-secret&safe=value.\n\n" +
        "[pixel]: https://evil.example/pixel",
    )
  await page.getByRole("button", { name: "Review report" }).click()

  const preview = page.getByTestId("report-problem-review")
  await expect(preview).toBeVisible()
  await expect(preview).toContainText("2001:db8::1")
  await expect(preview).toContainText("X-Amz-Signature=[redacted]")
  await expect(preview).toContainText("AWSAccessKeyId=[redacted]")
  await expect(preview).toContainText("GoogleAccessId=[redacted]")
  await expect(preview).toContainText("sig=[redacted]")
  await expect(preview).not.toContainText("fe80::beef")
  await expect(preview).not.toContainText("fe80::1%eth0")
  await expect(preview).not.toContainText("fd12::1%5")
  await expect(preview).not.toContainText("admin@example.com")
  await expect(preview).not.toContainText("secret")
  await expect(preview).not.toContainText("AKIAEXAMPLE")
  await expect(preview).not.toContainText("gcs-signer")
  await expect(preview).not.toContainText("compact-secret")
  await expect(preview).not.toContainText("evil.example")
  await expect(preview).not.toContainText("![")
})

test("LAN validation scrolls to and focuses the first invalid field", async ({ page }) => {
  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByRole("button", { name: "Review report" }).click()

  await expect(page.getByLabel("Problem summary")).toBeFocused()
  await expect(page.getByText("Problem summary is required.")).toBeVisible()
  await expect(page.getByLabel("Problem summary")).toBeInViewport()
})

test("LAN report preparation can be cancelled without submitting", async ({ page }) => {
  let submitAttempts = 0
  await page.route("https://channelwatch.coderluii.dev/api/reports**", async (route) => {
    submitAttempts += 1
    await route.abort("blockedbyclient")
  })

  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await page.getByLabel("Problem summary").fill("Cancelled LAN draft")
  await page.getByRole("button", { name: "Cancel" }).click()

  await expect(page.getByRole("dialog", { name: "Report a Problem" })).toHaveCount(0)
  expect(submitAttempts).toBe(0)
})
