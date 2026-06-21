import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

const views = [
  { hash: "#overview", readyName: "Dashboard Overview", kind: "heading" as const },
  { hash: "#settings", readyName: "Settings", kind: "heading" as const },
  { hash: "#diagnostics", readyName: "Diagnostics", kind: "heading" as const },
  { hash: "#watch-history", readyName: "Watch History", kind: "title" as const },
  { hash: "#notification-log", readyName: "Notification Delivery Log", kind: "text" as const },
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
