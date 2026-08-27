import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

const views = [
  { hash: "#overview", readyName: "Dashboard Overview", kind: "heading" as const },
  { hash: "#settings", readyName: "Settings", kind: "heading" as const },
  { hash: "#settings:alerts", readyName: "Alert policy", kind: "text" as const },
  { hash: "#settings:updates", readyName: "Update policy", kind: "text" as const },
  { hash: "#diagnostics", readyName: "Diagnostics", kind: "heading" as const },
  { hash: "#watch-history", readyName: "Watch History", kind: "title" as const },
  { hash: "#notification-log", readyName: "Notification Delivery Log", kind: "text" as const },
  { hash: "#help-feedback", readyName: "Help & Feedback", kind: "heading" as const },
  { hash: "#about", readyName: "Story", kind: "tab" as const },
]

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

for (const view of views) {
  test(`axe: ${view.hash} has no accessibility violations`, async ({ page }) => {
    await page.goto(`/${view.hash}`)

    if (view.kind === "heading") {
      await expect(page.getByRole("heading", { name: view.readyName })).toBeVisible()
    } else if (view.kind === "tab") {
      await expect(page.getByRole("tab", { name: view.readyName })).toBeVisible()
    } else if (view.kind === "title") {
      await expect(page.locator("main").getByText(view.readyName, { exact: true }).first()).toBeVisible()
    } else {
      await expect(page.getByText(view.readyName)).toBeVisible()
    }

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze()

    expect(results.violations).toEqual([])
  })
}

test("axe: report problem dialog has no accessibility violations", async ({ page }) => {
  await page.goto("/#diagnostics")
  await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible()
  await page.getByRole("button", { name: "Report a ChannelWatch problem" }).click()
  await expect(page.getByRole("dialog", { name: "Report a Problem" })).toBeVisible()

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze()

  expect(results.violations).toEqual([])
})

test("axe: feature request dialog has no accessibility violations", async ({ page }) => {
  await page.goto("/#help-feedback")
  await expect(page.getByRole("heading", { name: "Help & Feedback" })).toBeVisible()
  await page.getByTestId("help-request-feature").click()
  await expect(page.getByRole("dialog", { name: "Request a feature or change" })).toBeVisible()

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze()

  expect(results.violations).toEqual([])
})

test("axe: DVR uptime dialog has no accessibility violations", async ({ page }) => {
  await page.goto("/#overview")
  await page.getByRole("button", { name: /DVR uptime/ }).click()
  await expect(page.getByRole("dialog", { name: "DVR uptime" })).toBeVisible()

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze()

  expect(results.violations).toEqual([])
})

test("axe: authenticated legacy recovery has no accessibility violations", async ({ page }) => {
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
      affected_notification_credentials: 1,
      legacy_input_detected: false,
      message: null,
    }),
  }))
  await page.goto("/#settings:security")
  await expect(page.getByTestId("key-recovery-card")).toBeVisible()

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze()

  expect(results.violations).toEqual([])
})
