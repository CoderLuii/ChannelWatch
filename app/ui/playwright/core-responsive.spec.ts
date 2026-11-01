import { expect, test, type Page } from "@playwright/test"

import { installApiMocks, mockSystemInfo } from "./support/mock-api"

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
  await expect(sidebar).toHaveCount(0)

  const metrics = await page.evaluate(() => {
    const primary = document.querySelector<HTMLElement>('[data-testid="header-primary-controls"]')
    const utility = document.querySelector<HTMLElement>('[data-testid="header-utility-controls"]')
    if (!primary || !utility) throw new Error("Mobile header controls are unavailable")
    const primaryBox = primary.getBoundingClientRect()
    const utilityBox = utility.getBoundingClientRect()
    return {
      primaryRight: primaryBox.right,
      utilityLeft: utilityBox.left,
    }
  })

  expect(metrics.primaryRight).toBeLessThanOrEqual(metrics.utilityLeft)
}

for (const viewport of viewports) {
  test(`${viewport.name} keeps modified surfaces reachable`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await installApiMocks(page)

    await page.goto("/#settings:alerts")
    await expect(page.getByRole("heading", { name: "Alert Policy" })).toBeVisible()
    await expect(page.getByRole("button", { name: /DVR Health/ })).toBeVisible()
    if (viewport.width < 768) await expectCompactMobileHeader(page)
    await expectNoHorizontalOverflow(page)

    await page.goto("/#watch-history")
    await expect(viewport.width < 768
      ? page.getByRole("button", { name: /Filter activity by exact client/ })
      : page.getByRole("combobox", { name: "Filter activity by exact client" })).toBeVisible()
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
    const uptimeTile = page.getByTestId("mobile-uptime-details-tile").getByRole("button").first()
    await expect(uptimeTile.or(page.getByRole("button", { name: /DVR uptime/ })).first()).toBeVisible()
    const tileIsVisible = await uptimeTile.isVisible()
    if (tileIsVisible) await uptimeTile.click()
    else await page.getByRole("button", { name: /DVR uptime/ }).click()
    const uptimeDialog = page.getByRole("dialog", { name: tileIsVisible ? "Uptime" : "DVR uptime" })
    await expect(uptimeDialog).toBeInViewport()
    await expect(uptimeDialog.getByRole("button", { name: "Close" }).first()).toBeInViewport()
    await uptimeDialog.getByRole("button", { name: "Close" }).first().click()
    await expectNoHorizontalOverflow(page)
  })
}

test("dashboard tiles open a sheet and stay where they are", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 })
  await installApiMocks(page)
  await page.goto("/#overview")

  const tile = page.getByTestId("mobile-disk-details-tile")
  const disk = tile.locator("button").first()
  await expect(disk).toHaveAttribute("aria-expanded", "false")
  await expect(disk).toContainText("of 18.03 TB")
  const before = await tile.boundingBox()
  expect(before?.height ?? 0).toBeLessThanOrEqual(120)

  await disk.click()
  const sheet = page.getByRole("dialog", { name: "Disk left" })
  await expect(sheet).toBeInViewport()
  await expect(disk).toHaveAttribute("aria-expanded", "true")
  expect(await tile.boundingBox()).toEqual(before)

  await sheet.getByRole("button", { name: "Close" }).click()
  await expect(sheet).toHaveCount(0)
  await expect(disk).toBeFocused()
})

test("closing the disk sheet opened from the top line leaves a short phone where it was", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 })
  await installApiMocks(page)
  await page.route("**/api/system-info", (route) => route.fulfill({ json: { ...mockSystemInfo, disk_severity: "critical" } }))
  await page.goto("/#overview")

  const seeDisk = page.getByTestId("health-verdict").getByRole("button", { name: "See disk" })
  await seeDisk.click()
  const sheet = page.getByRole("dialog", { name: "Disk left" })
  await expect(sheet).toBeInViewport()

  await sheet.getByRole("button", { name: "Close" }).click()
  await expect(sheet).toHaveCount(0)
  await expect(seeDisk).toBeFocused()
  await page.waitForTimeout(300)
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
})

for (const reducedMotion of ["no-preference", "reduce"] as const) {
  test(`See disk on a wide screen outlines the disk card and moves focus to it with ${reducedMotion} motion`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.emulateMedia({ reducedMotion })
    await installApiMocks(page)
    await page.route("**/api/system-info", (route) => route.fulfill({ json: { ...mockSystemInfo, disk_severity: "critical" } }))
    await page.goto("/#overview")

    const card = page.locator("#mobile-disk-details > *").first()
    await page.getByTestId("health-verdict").getByRole("button", { name: "See disk" }).click()
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await expect(card).toBeFocused()
    await expect(card).toHaveClass(/attention-outline/)
    await expect.poll(() => card.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe("solid")
    expect(await page.evaluate(() => window.scrollY)).toBe(0)
    await expect(card).not.toHaveClass(/attention-outline/, { timeout: 4000 })
  })
}

test("the top line's other buttons go to their pages on a wide screen", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installApiMocks(page)
  await page.route("**/api/system-info", (route) => route.fulfill({
    json: {
      ...mockSystemInfo,
      dvr_status: mockSystemInfo.dvr_status.map((dvr) => ({ ...dvr, monitoring_ready: false, monitoring_status: "stale", monitoring_reason: "No freshness update for 601s" })),
    },
  }))
  await page.goto("/#overview")

  await page.getByTestId("health-verdict-action").click()
  await expect(page).toHaveURL(/#diagnostics$/)
  await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible()
})

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

test("activity filter stays on screen in a short landscape viewport", async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 })
  await installApiMocks(page)
  await page.goto("/#overview")
  await page.getByRole("button", { name: /Filter activity by type/ }).click()
  const content = page.locator("[data-radix-popper-content-wrapper] > *")
  await expect(content).toBeVisible()
  const box = await content.boundingBox()
  expect(box?.y).toBeGreaterThanOrEqual(0)
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(390)
})

for (const width of [390, 1440]) {
  test(`status panel keeps Notifications inside the card with a stale DVR at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installApiMocks(page)
    await page.route("**/api/system-info", (route) => route.fulfill({
      json: {
        ...mockSystemInfo,
        dvr_status: mockSystemInfo.dvr_status.map((dvr) => ({
          ...dvr,
          monitoring_ready: false,
          monitoring_status: "stale",
          monitoring_reason: "No freshness update for 601s",
        })),
      },
    }))
    await page.goto("/#overview")

    const row = page.getByTestId("status-notifications-row")
    await expect(row).toBeVisible()
    await expect(page.getByTestId("health-verdict")).toContainText("Stale monitoring on")
    const boxes = await row.evaluate((element) => {
      const card = element.closest<HTMLElement>(".rounded-group")
      if (!card) throw new Error("Status card not found")
      return { row: element.getBoundingClientRect().bottom, card: card.getBoundingClientRect().bottom }
    })
    expect(boxes.row).toBeLessThanOrEqual(boxes.card + 0.5)
  })
}

for (const width of [768, 1024, 1280, 1440]) {
  test(`opening the sidebar leaves the DVR selector clickable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installApiMocks(page)
    await page.goto("/#overview")

    const selector = page.getByTestId("header-dvr-select")
    await expect(selector).toBeVisible()
    const expand = page.getByRole("button", { name: "Expand sidebar" })
    if (width < 1024) {
      await expect(expand).toBeHidden()
      return
    }
    await expand.click()
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeVisible()
    const covered = await selector.evaluate((element) => {
      const box = element.getBoundingClientRect()
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)
      return !hit || !element.contains(hit)
    })
    expect(covered).toBe(false)

    await page.reload()
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeVisible()
  })
}

test("a filter sheet closes instead of leaving a dim page when the screen widens", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installApiMocks(page)
  await page.goto("/#watch-history")

  await page.getByRole("button", { name: /Filter activity by exact client/ }).click()
  await expect(page.getByTestId("filter-sheet")).toBeVisible()

  await page.setViewportSize({ width: 844, height: 390 })
  await expect(page.getByTestId("filter-sheet")).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => document.body.style.pointerEvents)).not.toBe("none")
})

test("every clickable control shows a hand", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installApiMocks(page)
  await page.goto("/#settings:general")

  for (const control of [page.getByRole("tab", { name: "General" }), page.getByTestId("header-dvr-select"), page.getByRole("button", { name: "Expand sidebar" })]) {
    await expect(control).toHaveCSS("cursor", "pointer")
  }
})

test("page titles share one left edge on a wide screen", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installApiMocks(page)

  for (const [view, name] of [["overview", "Home"], ["watch-history", "Watch History"], ["notification-log", "Notification Log"], ["settings:general", "Settings"], ["diagnostics", "Diagnostics"], ["help-feedback", "Help & Feedback"], ["about", "About"]]) {
    await page.goto(`/#${view}`)
    const title = page.getByRole("heading", { level: 1, name, exact: true })
    await expect(title).toBeVisible()
    await expect.poll(async () => Math.round((await title.boundingBox())?.x ?? -1), name).toBe(100)
  }
})

test("Review presets opens the presets", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installApiMocks(page)
  await page.goto("/#settings:alerts")

  await page.getByRole("button", { name: "Review presets" }).click()
  await expect(page.getByRole("option", { name: "Balanced" })).toBeVisible()
})
