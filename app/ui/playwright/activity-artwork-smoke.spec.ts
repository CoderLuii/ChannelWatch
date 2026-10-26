import { expect, test } from "@playwright/test"
import { installApiMocks } from "./support/mock-api"

const artwork = [
  { name: "landscape", width: 320, height: 180, title: "ABC NEWS" },
  { name: "square", width: 200, height: 200, title: "CHANNEL 24" },
  { name: "portrait", width: 200, height: 300, title: "Movie poster" },
]

for (const width of [320, 375, 390, 768, 1024, 1440]) {
  for (const theme of ["light", "dark"]) {
    test(`complete artwork stays visible and rows aligned at ${width}px in ${theme} mode`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 })
      await page.addInitScript(value => localStorage.setItem("theme", value), theme)
      await installApiMocks(page)
      for (const image of artwork) {
        await page.route(`**/test-artwork-${image.name}.svg`, route => route.fulfill({
          contentType: "image/svg+xml",
          body: `<svg xmlns="http://www.w3.org/2000/svg" width="${image.width}" height="${image.height}" viewBox="0 0 ${image.width} ${image.height}"><rect width="100%" height="100%" fill="#27466c"/><rect x="2" y="2" width="${image.width - 4}" height="${image.height - 4}" rx="8" fill="none" stroke="#ffca62" stroke-width="4"/><text x="50%" y="50%" fill="white" text-anchor="middle" font-family="sans-serif" font-size="22">${image.title}</text><circle cx="12" cy="${image.height / 2}" r="7" fill="#ffca62"/><circle cx="${image.width - 12}" cy="${image.height / 2}" r="7" fill="#ffca62"/></svg>`,
        }))
      }
      const items = ["landscape", "square", "portrait", "missing", "broken"].map((name, index) => ({
        id: `artwork-${name}`,
        type: "watching_channel",
        title: `${name} artwork with a long channel name`,
        message: "Living Room is watching a channel",
        timestamp: "2026-08-26T11:45:00Z",
        image_url: name === "missing" ? undefined : `/test-artwork-${name}.svg`,
        dvr_id: "main-dvr",
        dvr_name: "Main DVR with a long display name",
        channel_name: "ABC NEWS",
        channel_number: "24",
      }))
      await page.route("**/test-artwork-broken.svg", route => route.fulfill({ status: 404, body: "" }))
      await page.route("**/api/activity-history**", route => route.fulfill({
        json: { items, total: items.length, offset: 0, limit: 25 },
      }))
      await page.route("**/api/recent-activity**", route => route.fulfill({ json: items }))

      for (const section of ["watch-history", "overview"]) {
        await page.goto(`/#${section}`)
        await expect(page.getByText(items[4].title, { exact: true })).toBeVisible()
        await expect(page.locator('img[src="/test-artwork-broken.svg"]')).toHaveCount(0)
        const rows = items.map(item => page.getByRole("button").filter({ has: page.getByText(item.title, { exact: true }) }))
        const positions = await Promise.all(items.map(item => page.getByText(item.title, { exact: true }).boundingBox()))
        expect(positions.every(box => box !== null && box.width >= 120)).toBe(true)
        expect(Math.max(...positions.map(box => box!.x)) - Math.min(...positions.map(box => box!.x))).toBeLessThanOrEqual(1)
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
        expect(overflow).toBeLessThanOrEqual(1)
        await page.screenshot({ path: testInfo.outputPath(`${section}-${theme}-${width}.png`), fullPage: true })

        for (let index = 0; index < artwork.length; index++) {
          const image = rows[index].locator("img")
          await expect(image).toBeVisible()
          await expect(image).toHaveJSProperty("complete", true)
          const geometry = await image.evaluate(element => ({
            fit: getComputedStyle(element).objectFit,
            width: element.parentElement!.getBoundingClientRect().width,
            height: element.parentElement!.getBoundingClientRect().height,
            loaded: (element as HTMLImageElement).naturalWidth > 0,
          }))
          expect.soft(geometry.loaded).toBe(true)
          expect.soft(geometry.fit).toBe("contain")
          expect.soft(geometry.width / geometry.height).toBeGreaterThanOrEqual(1.6)
          expect.soft(geometry.width).toBeGreaterThanOrEqual(64)
        }
      }

      await page.getByRole("button").filter({ has: page.getByText(items[2].title, { exact: true }) }).click()
      const dialog = page.getByRole("dialog")
      await expect(dialog).toBeVisible()
      expect.soft(await dialog.locator("img").evaluate(element => getComputedStyle(element).objectFit)).toBe("contain")
      await dialog.screenshot({ path: testInfo.outputPath(`dialog-${theme}-${width}.png`) })
      await page.keyboard.press("Escape")
      await expect(dialog).not.toBeVisible()
    })
  }
}
