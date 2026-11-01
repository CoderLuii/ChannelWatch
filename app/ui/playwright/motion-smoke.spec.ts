import { expect, test, type Page, type Route } from "@playwright/test"

import { installApiMocks, mockSettings } from "./support/mock-api"

const fulfillJson = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
})

async function sheetOffsets(page: Page, selector: string, ms: number) {
  return page.evaluate(({ selector, ms }) => new Promise<Array<{ t: number; y: number | null }>>((resolve) => {
    const out: Array<{ t: number; y: number | null }> = []
    const start = performance.now()
    const tick = () => {
      const el = document.querySelector(selector)
      const transform = el ? getComputedStyle(el).transform : "none"
      out.push({ t: performance.now() - start, y: el ? new DOMMatrixReadOnly(transform === "none" ? undefined : transform).m42 : null })
      if (performance.now() - start < ms) requestAnimationFrame(tick)
      else resolve(out)
    }
    requestAnimationFrame(tick)
  }), { selector, ms })
}

/** Milliseconds until the element leaves the page. Watches the DOM, because headless WebKit paints too rarely to time this by frames. */
async function removalTime(page: Page, selector: string, ms: number) {
  return page.evaluate(({ selector, ms }) => new Promise<number>((resolve) => {
    const start = performance.now()
    const observer = new MutationObserver(() => {
      if (document.querySelector(selector)) return
      observer.disconnect()
      resolve(performance.now() - start)
    })
    observer.observe(document.body, { childList: true, subtree: true })
    setTimeout(() => {
      observer.disconnect()
      resolve(Number.POSITIVE_INFINITY)
    }, ms)
  }), { selector, ms })
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce", viewport: { width: 390, height: 844 } })

  test("Home is still when idle and a sheet is final on its first frame", async ({ page }) => {
    await page.goto("/#overview")
    await expect(page.getByTestId("health-verdict")).not.toHaveAttribute("data-tone", "pending")
    await expect.poll(() => page.evaluate(() => document.getAnimations().length)).toBe(0)

    const sampling = sheetOffsets(page, '[data-testid="mobile-more-sheet"]', 240)
    await page.getByRole("button", { name: "More", exact: true }).click()
    const opening = (await sampling).filter((frame) => frame.y !== null)
    expect(opening.length).toBeGreaterThan(0)
    expect(new Set(opening.map((frame) => frame.y))).toEqual(new Set([0]))

    const closing = removalTime(page, '[data-testid="mobile-more-sheet"]', 1000)
    await page.keyboard.press("Escape")
    expect(await closing).toBeLessThan(134)
  })
})

test.describe("reduced motion save result", () => {
  test.use({ reducedMotion: "reduce", viewport: { width: 390, height: 844 } })

  test("the save bar still leaves after its result", async ({ page }) => {
    await page.route("**/api/settings", (route) => {
      if (route.request().method() === "POST") return fulfillJson(route, { message: "Settings saved successfully", applied: "hot" })
      return fulfillJson(route, mockSettings)
    })
    await page.goto("/#settings:general")
    const name = page.getByPlaceholder("e.g., Main DVR").first()
    await expect(name).toBeVisible()
    await name.fill("Renamed DVR")
    await page.getByRole("button", { name: "Save settings" }).click()
    const bar = page.getByTestId("settings-save-bar")
    await expect(bar.getByRole("status")).toContainText("General settings saved and applied")
    await expect(bar).toHaveCount(0, { timeout: 4000 })
  })
})

test.describe("phone sheets", () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test("the More sheet turns around when closed while opening", async ({ page }) => {
    await page.goto("/#overview")
    await expect(page.getByTestId("health-verdict")).toBeVisible()
    const sampling = sheetOffsets(page, '[data-testid="mobile-more-sheet"]', 640)
    await page.getByRole("button", { name: "More", exact: true }).click()
    await page.waitForFunction(() => {
      const el = document.querySelector('[data-testid="mobile-more-sheet"]')
      if (!el) return false
      const y = new DOMMatrixReadOnly(getComputedStyle(el).transform).m42
      return y < el.getBoundingClientRect().height * 0.7
    }, null, { polling: "raf" })
    await page.keyboard.press("Escape")
    const frames = (await sampling).filter((frame): frame is { t: number; y: number } => frame.y !== null)
    const lowest = Math.min(...frames.map((frame) => frame.y))
    expect(frames.at(-1)?.y ?? 0).toBeGreaterThan(lowest)
    await expect(page.getByTestId("mobile-more-sheet")).toHaveCount(0)
  })

  test("an activity sheet keeps its content while it leaves and returns focus to the row", async ({ page }) => {
    await page.goto("/#overview")
    const row = page.locator("button.group\\/row").first()
    await row.click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    const length = (await dialog.innerText()).length
    expect(length).toBeGreaterThan(0)
    const lengths = page.evaluate(() => new Promise<number[]>((resolve) => {
      const out: number[] = []
      const start = performance.now()
      const tick = () => {
        const el = document.querySelector<HTMLElement>('[role="dialog"]')
        if (el) out.push(el.innerText.length)
        if (performance.now() - start < 180) requestAnimationFrame(tick)
        else resolve(out)
      }
      requestAnimationFrame(tick)
    }))
    await page.keyboard.press("Escape")
    expect(new Set(await lengths)).toEqual(new Set([length]))
    await expect(dialog).toHaveCount(0)
    await expect(row).toBeFocused()
  })
})

test.describe("anchored menus", () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test("the filter popover scales from its trigger the whole way", async ({ page }) => {
    await page.goto("/#overview")
    const filter = page.getByRole("button", { name: /all activity|filter/i }).first()
    await filter.scrollIntoViewIfNeeded()
    const moving = page.evaluate(() => new Promise<string[]>((resolve) => {
      const seen = new Set<string>()
      const start = performance.now()
      const tick = () => {
        const el = document.querySelector("[data-radix-popper-content-wrapper] > *")
        if (el) for (const animation of el.getAnimations()) if (animation instanceof CSSTransition) seen.add(animation.transitionProperty)
        if (performance.now() - start < 500) requestAnimationFrame(tick)
        else resolve([...seen])
      }
      requestAnimationFrame(tick)
    }))
    await filter.click()
    expect(await moving).toEqual([])
    await expect(page.locator("[data-radix-popper-content-wrapper] > *").first()).toBeVisible()
  })
})

test.describe("new activity rows", () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test("a time range change does not slide rows in, a refresh that brings a row does", async ({ page }) => {
    const at = Date.now()
    const rows = (prefix: string, count: number) => Array.from({ length: count }, (_, index) => ({
      id: `${prefix}-${index}`,
      type: "vod_playback",
      title: `${prefix} row ${index}`,
      message: `${prefix} row ${index}`,
      timestamp: new Date(at - (index + 1) * 3_600_000).toISOString(),
      extra: {},
    }))
    let fresh = 0
    await page.route("**/api/activity-history?**", (route) => {
      const url = new URL(route.request().url())
      const hours = Number(url.searchParams.get("hours") ?? 24)
      const items = [...rows("fresh", fresh), ...rows("base", 4), ...(hours > 24 ? rows("older", 4) : [])]
      return fulfillJson(route, { items, total: items.length, activities: items, count: items.length, has_more: false })
    })
    await page.goto("/#overview")
    await expect(page.locator('button[class*="group/row"]', { hasText: "base row 0" }).first()).toBeVisible()
    await expect(page.locator("[data-new]")).toHaveCount(0)

    const range = page.getByRole("combobox").filter({ hasText: /last 24 hours/i }).first()
    await range.click()
    await page.getByRole("option").filter({ hasText: /7 days/i }).first().click()
    await expect(page.locator('button[class*="group/row"]', { hasText: "older row 0" }).first()).toBeVisible()
    await expect(page.locator("[data-new]")).toHaveCount(0)

    fresh = 1
    await page.getByRole("button", { name: /refresh/i }).first().click()
    await expect(page.locator('button[class*="group/row"]', { hasText: "fresh row 0" }).first()).toBeVisible()
    await expect(page.locator("[data-new]")).toHaveCount(1)
  })
})

test.describe("settings save failure on a wide screen", () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test("the failure toast leaves Save reachable", async ({ page }) => {
    await page.route("**/api/settings", (route) => {
      if (route.request().method() === "POST") return fulfillJson(route, { detail: "Could not write settings" }, 500)
      return fulfillJson(route, mockSettings)
    })
    await page.goto("/#settings:general")
    const name = page.getByPlaceholder("e.g., Main DVR").first()
    await expect(name).toBeVisible()
    await name.fill("Renamed DVR")
    const save = page.getByRole("button", { name: "Save settings" })
    await save.click()
    await expect(page.getByText("Error saving settings").first()).toBeVisible()
    await page.waitForTimeout(300)
    await save.click({ trial: true, timeout: 3000 })
    await page.getByRole("button", { name: /discard/i }).click({ trial: true, timeout: 3000 })
  })
})

test.describe("settings save result", () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test("the bar keeps its words while it leaves", async ({ page }) => {
    await page.route("**/api/settings", (route) => {
      if (route.request().method() === "POST") return fulfillJson(route, { message: "Settings saved successfully", applied: "hot" })
      return fulfillJson(route, mockSettings)
    })
    await page.goto("/#settings:general")
    const name = page.getByPlaceholder("e.g., Main DVR").first()
    await expect(name).toBeVisible()
    const leavingText = () => page.evaluate(() => new Promise<string>((resolve) => {
      const tick = () => {
        const bar = document.querySelector<HTMLElement>('[data-testid="settings-save-bar"][data-leaving]')
        if (bar) resolve(bar.innerText)
        else requestAnimationFrame(tick)
      }
      tick()
    }))

    await name.fill("Renamed DVR")
    let leaving = leavingText()
    await page.getByRole("button", { name: "Save settings" }).click()
    expect(await leaving).toContain("General settings saved and applied")
    await expect(page.getByTestId("settings-save-bar")).toHaveCount(0)

    await name.fill("Renamed again")
    await expect(page.getByTestId("settings-save-bar")).toBeVisible()
    leaving = leavingText()
    await page.getByRole("button", { name: /discard/i }).click()
    expect(await leaving).toContain("Unsaved changes")
    await expect(page.getByTestId("settings-save-bar")).toHaveCount(0)
  })

  test("the bar says saved, then leaves and gives its space back", async ({ page }) => {
    await page.route("**/api/settings", (route) => {
      if (route.request().method() === "POST") return fulfillJson(route, { message: "Settings saved successfully", applied: "hot" })
      return fulfillJson(route, mockSettings)
    })
    await page.goto("/#settings:general")
    const name = page.getByPlaceholder("e.g., Main DVR").first()
    await expect(name).toBeVisible()
    const bar = page.getByTestId("settings-save-bar")
    await expect(bar).toHaveCount(0)

    await name.fill("Renamed DVR")
    await expect(bar).toBeVisible()
    await expect(bar).toContainText("Unsaved changes")
    expect((await bar.boundingBox())?.height).toBeLessThanOrEqual(60)
    await page.getByRole("button", { name: "Save settings" }).click()
    await expect(bar.getByRole("status")).toContainText("General settings saved and applied")
    await expect(page.getByRole("button", { name: "Save settings" })).toHaveCount(0)
    const heightWhileLeaving = await page.evaluate(() => new Promise<string>((resolve) => {
      const tick = () => {
        if (document.querySelector('[data-testid="settings-save-bar"][data-leaving]')) resolve(document.documentElement.style.getPropertyValue("--settings-save-bar-height"))
        else requestAnimationFrame(tick)
      }
      tick()
    }))
    expect(heightWhileLeaving).not.toBe("0px")
    await expect(bar).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue("--settings-save-bar-height"))).toBe("0px")
  })
})
