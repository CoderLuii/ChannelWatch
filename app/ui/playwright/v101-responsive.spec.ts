import { expect, test, type Page } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

const viewports = [
  { name: "narrow-mobile", width: 320, height: 568 },
  { name: "mobile", width: 375, height: 812 },
  { name: "tablet-portrait", width: 768, height: 1024 },
  { name: "tablet-landscape", width: 1024, height: 768 },
  { name: "desktop", width: 1440, height: 1080 },
  { name: "wide-desktop", width: 1920, height: 1080 },
] as const

async function expectNoHorizontalOverflow(page: Page) {
  const metrics = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }))
  expect(metrics.documentWidth, `document width at ${metrics.viewportWidth}px`).toBeLessThanOrEqual(metrics.viewportWidth + 1)
}

async function expectCompactMobileHeader(page: Page) {
  const sidebar = page.getByRole("dialog", { name: "Primary navigation", includeHidden: true })
  await expect(sidebar).toHaveAttribute("aria-hidden", "true")

  const metrics = await page.evaluate(() => {
    const primary = document.querySelector<HTMLElement>('[data-testid="header-primary-controls"]')
    const utility = document.querySelector<HTMLElement>('[data-testid="header-utility-controls"]')
    const aside = document.querySelector<HTMLElement>("aside")
    if (!primary || !utility || !aside) throw new Error("Mobile header controls are unavailable")
    const primaryBox = primary.getBoundingClientRect()
    const utilityBox = utility.getBoundingClientRect()
    const asideBox = aside.getBoundingClientRect()
    return {
      primaryRight: primaryBox.right,
      utilityLeft: utilityBox.left,
      asideRight: asideBox.right,
    }
  })

  expect(metrics.primaryRight).toBeLessThanOrEqual(metrics.utilityLeft)
  expect(metrics.asideRight).toBeLessThanOrEqual(0)
}

for (const viewport of viewports) {
  test(`${viewport.name} keeps modified v1.0.1 surfaces reachable`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await installApiMocks(page)

    await page.goto("/#settings:alerts")
    await expect(page.getByRole("heading", { name: "Alert policy" })).toBeVisible()
    await expect(page.getByRole("button", { name: /DVR Health/ })).toBeVisible()
    if (viewport.width < 768) await expectCompactMobileHeader(page)
    await expectNoHorizontalOverflow(page)

    await page.goto("/#watch-history")
    await expect(page.getByRole("combobox", { name: "Filter activity by exact client" })).toBeVisible()
    await expectNoHorizontalOverflow(page)

    await page.goto("/#help-feedback")
    await expect(page.getByRole("heading", { name: "Help & Feedback" })).toBeVisible()
    await page.getByTestId("help-request-feature").click()
    const featureDialog = page.getByRole("dialog", { name: "Request a feature or change" })
    await expect(featureDialog).toBeInViewport()
    await expect(featureDialog.getByRole("button", { name: "Submit request" })).toBeInViewport()
    await featureDialog.getByRole("button", { name: "Cancel", exact: true }).click()
    await expectNoHorizontalOverflow(page)

    await page.goto("/#overview")
    await page.getByRole("button", { name: /DVR uptime/ }).click()
    const uptimeDialog = page.getByRole("dialog", { name: "DVR uptime" })
    await expect(uptimeDialog).toBeInViewport()
    await expect(uptimeDialog.getByRole("button", { name: "Close" }).first()).toBeInViewport()
    await uptimeDialog.getByRole("button", { name: "Close" }).first().click()
    await expectNoHorizontalOverflow(page)
  })
}

test("modified surfaces remain usable at an effective 200 percent zoom", async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 540 })
  await installApiMocks(page)
  await page.goto("/#help-feedback")
  await page.evaluate(() => {
    document.documentElement.style.zoom = "2"
  })
  await expect(page.getByRole("heading", { name: "Help & Feedback" })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByTestId("help-request-feature").click()
  await expect(page.getByRole("dialog", { name: "Request a feature or change" })).toBeInViewport()
})

test("modified surfaces honor reduced motion and forced colors", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce", forcedColors: "active" })
  await installApiMocks(page)
  await page.goto("/#settings:alerts")
  const disclosure = page.getByRole("button", { name: /DVR Health/ })
  await disclosure.click()
  await expect(disclosure).toHaveAttribute("aria-expanded", "true")
  await expect(disclosure).toBeFocused()
})
