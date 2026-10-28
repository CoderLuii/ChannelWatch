import { expect, test } from "@playwright/test"
import { installApiMocks } from "./support/mock-api"

const recording = {
  id: "watch-recording", type: "recording_event", title: "Recording Event",
  message: "Completed: Evening News", timestamp: "2026-10-28T12:00:00Z",
  icon: "video", program_title: "Evening News", device_name: "Living Room Apple TV",
  dvr_id: "main-dvr", dvr_name: "Main DVR",
}
const health = {
  id: "health-recovered", type: "dvr_recovered", title: "DVR connection recovered",
  message: "ChannelWatch is connected to Main DVR again.", timestamp: "2026-10-28T13:00:00Z",
  icon: "bell", dvr_id: "main-dvr", dvr_name: "Main DVR",
}

for (const width of [375, 768, 1440]) {
  for (const theme of ["light", "dark"]) {
    test(`Watch History scopes rows, facets and export at ${width}px in ${theme} mode`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 })
      await page.addInitScript(value => localStorage.setItem("theme", value), theme)
      await installApiMocks(page)
      const queries: URL[] = []
      await page.route("**/api/activity-history**", route => {
        const url = new URL(route.request().url())
        queries.push(url)
        const items = url.searchParams.get("scope") === "watch" ? [recording] : [health, recording]
        return route.fulfill({ json: { items, total: items.length, offset: 0, limit: 25 } })
      })
      await page.route("**/api/v1/activity-history/filters**", route => {
        queries.push(new URL(route.request().url()))
        return route.fulfill({ json: { clients: [{ value: recording.device_name, label: recording.device_name, count: 1 }] } })
      })
      await page.route("**/api/v1/history/export**", route => {
        queries.push(new URL(route.request().url()))
        return route.fulfill({
          contentType: "text/csv",
          headers: { "content-disposition": 'attachment; filename="history.csv"' },
          body: "id,type,title\r\nwatch-recording,recording_event,Evening News\r\n",
        })
      })
      await page.route("**/api/recent-activity**", route => route.fulfill({ json: [health, recording] }))

      await page.goto("/?wh_client=Living+Room+Apple+TV#watch-history")
      await expect(page.getByText(recording.message, { exact: true })).toBeVisible()
      await expect(page.getByText(health.title, { exact: true })).toHaveCount(0)
      await expect(page.getByText("Showing 1-1 of 1", { exact: true })).toBeVisible()
      const facet = queries.find(url => url.pathname.endsWith("/filters"))
      expect(facet?.searchParams.get("scope")).toBe("watch")
      expect(facet?.searchParams.get("hours")).toBe("0")
      expect(queries.filter(url => url.pathname === "/api/activity-history").every(url => url.searchParams.get("scope") === "watch")).toBe(true)
      await page.getByRole("combobox", { name: "Filter activity type", exact: true }).click()
      await expect(page.getByRole("option", { name: "Disk Alerts", exact: true })).toHaveCount(0)
      await expect(page.getByRole("option", { name: "Recordings", exact: true })).toBeVisible()
      await page.keyboard.press("Escape")
      const download = page.waitForEvent("download")
      await page.getByRole("button", { name: "Export CSV" }).click()
      await (await download).saveAs(testInfo.outputPath("watch-history.csv"))
      expect(queries.find(url => url.pathname.endsWith("/export"))?.searchParams.get("scope")).toBe("watch")
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
      await page.screenshot({ path: testInfo.outputPath(`watch-history-${theme}-${width}.png`), fullPage: true })

      await page.goto("/#overview")
      await expect(page.getByText(health.title, { exact: true })).toBeVisible()
      await page.screenshot({ path: testInfo.outputPath(`recent-activity-${theme}-${width}.png`), fullPage: true })
    })
  }
}

test("an old Disk Alerts bookmark falls back to all watch activity before fetching", async ({ page }) => {
  await installApiMocks(page)
  const requestedTypes: Array<string | null> = []
  await page.route("**/api/activity-history**", route => {
    requestedTypes.push(new URL(route.request().url()).searchParams.get("type"))
    return route.fulfill({ json: { items: [recording], total: 1, offset: 0, limit: 25 } })
  })
  await page.goto("/?wh_type=disk#watch-history")
  await expect(page.getByText(recording.message, { exact: true })).toBeVisible()
  await expect(page.getByRole("combobox", { name: "Filter activity type", exact: true })).toHaveText("All activity")
  expect(requestedTypes.length).toBeGreaterThan(0)
  expect(requestedTypes.every(type => type !== "disk")).toBe(true)
  await expect.poll(() => new URL(page.url()).searchParams.has("wh_type")).toBe(false)
})
