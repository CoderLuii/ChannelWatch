import { expect, test } from "@playwright/test"
import { installApiMocks } from "./support/mock-api"

for (const width of [320, 390, 834, 1440]) {
  test(`artwork, missing artwork and broken artwork keep text aligned at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installApiMocks(page)
    const items = ["With artwork", "Without artwork", "Broken artwork"].map((title, index) => ({
      id: `alignment-${index}`,
      type: "watching_channel",
      title,
      message: "Living Room is watching a channel",
      timestamp: "2026-08-26T11:45:00Z",
      image_url: index === 0 ? "/images/channelwatch-logo.png" : index === 2 ? "/missing-artwork.png" : undefined,
      dvr_id: "main-dvr",
      dvr_name: "Main DVR",
    }))
    await page.route("**/api/activity-history**", route => route.fulfill({
      contentType: "application/json", body: JSON.stringify({ items, total: items.length, offset: 0, limit: 25 }),
    }))
    await page.route("**/api/recent-activity**", route => route.fulfill({ json: items }))
    await page.route("**/missing-artwork.png", route => route.fulfill({ status: 404, body: "" }))
    await page.goto("/#watch-history")
    await expect(page.getByText("Broken artwork", { exact: true })).toBeVisible()
    await expect(page.locator('img[src="/missing-artwork.png"]')).toHaveCount(0)
    const positions = await Promise.all(items.map(async item => {
      const box = await page.getByText(item.title, { exact: true }).boundingBox()
      expect(box).not.toBeNull()
      return box!.x
    }))
    expect(Math.max(...positions) - Math.min(...positions)).toBeLessThanOrEqual(1)
    const widths = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, document: document.documentElement.scrollWidth }))
    expect(widths.document).toBeLessThanOrEqual(widths.viewport + 1)
    await page.goto("/#overview")
    await expect(page.getByText("Broken artwork", { exact: true })).toBeVisible()
    await expect(page.locator('img[src="/missing-artwork.png"]')).toHaveCount(0)
    const recentPositions = await Promise.all(items.map(async item => {
      const box = await page.getByText(item.title, { exact: true }).boundingBox()
      expect(box).not.toBeNull()
      return box!.x
    }))
    expect(Math.max(...recentPositions) - Math.min(...recentPositions)).toBeLessThanOrEqual(1)
  })
}

test("initial deep link marks the current desktop navigation item", async ({ page }) => {
  await installApiMocks(page)
  await page.goto("/#watch-history")
  await expect(page.getByRole("heading", { name: "Watch History" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Watch History", exact: true })).toHaveAttribute("aria-current", "page")
  await expect(page.getByRole("button", { name: "Dashboard", exact: true })).not.toHaveAttribute("aria-current", "page")
})
