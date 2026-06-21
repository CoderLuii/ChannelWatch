import type { Locator, Page } from "@playwright/test"
import { expect, test } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

async function tabTo(page: Page, locator: Locator, maxTabs = 12) {
  for (let index = 0; index < maxTabs; index += 1) {
    await page.keyboard.press("Tab")
    if (await locator.evaluateAll((elements) => elements.some((element) => element === document.activeElement))) {
      return
    }
  }

  await expect(locator).toBeFocused()
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("keyboard: shell tab order exposes skip link and primary navigation", async ({ page }) => {
  await page.goto("/#overview")

  await tabTo(page, page.getByRole("link", { name: "Skip to main content" }), 2)
  await tabTo(page, page.getByRole("button", { name: "Dashboard", exact: true }), 4)
  await tabTo(page, page.getByRole("button", { name: "Watch History", exact: true }), 2)
  await tabTo(page, page.getByRole("button", { name: "Settings", exact: true }), 2)
})

test("keyboard: watch history dialog opens and closes without trapping focus", async ({ page }) => {
  await page.goto("/#watch-history")

  const firstActivity = page.getByRole("button", { name: /Watching HBO/i })
  await expect(firstActivity).toBeVisible()
  await firstActivity.focus()
  await page.keyboard.press("Enter")

  await expect(page.getByRole("dialog")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("dialog")).not.toBeVisible()
  await tabTo(page, page.getByRole("textbox", { name: "Search activity history" }), 20)
})

test("keyboard: major view controls are reachable in a sensible order", async ({ page }) => {
  await page.goto("/#settings")
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible()
  await page.getByRole("tab", { name: "General" }).focus()
  await page.keyboard.press("ArrowRight")
  await expect(page.getByRole("tab", { name: "Alerts" })).toBeFocused()

  await page.goto("/#notification-log")
  await expect(page.getByRole("combobox", { name: "Filter notification log by channel" })).toBeVisible()
  await page.getByRole("combobox", { name: "Filter notification log by channel" }).focus()
  await tabTo(page, page.getByRole("combobox", { name: "Filter notification log by status" }), 2)
  await tabTo(page, page.getByLabel("Filter notification log from date"), 2)

  await page.goto("/#diagnostics")
  await expect(page.getByRole("button", { name: "Download sanitized debug bundle" })).toBeVisible()
  await page.getByRole("button", { name: "Download sanitized debug bundle" }).focus()
  await tabTo(page, page.getByRole("button", { name: "Export diagnostics" }), 2)
  await tabTo(page, page.getByRole("combobox", { name: "Select number of log lines" }), 4)

  await page.goto("/#about")
  await expect(page.getByRole("tab", { name: "Story" })).toBeVisible()
  await page.getByRole("tab", { name: "Story" }).focus()
  await page.keyboard.press("ArrowRight")
  await expect(page.getByRole("tab", { name: "Project" })).toBeFocused()
})
