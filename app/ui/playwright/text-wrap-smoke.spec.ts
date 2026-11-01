import { expect, test, type Locator, type Page } from "@playwright/test"
import { applyDoubleText, findBrokenWords, findTextOverButtons } from "./support/broken-words"
import { installApiMocks } from "./support/mock-api"

const artwork = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="100%" height="100%" fill="#27466c"/></svg>'

const liveRow = {
  id: "live-row",
  type: "watching_channel",
  title: "Eyewitness News at Midnight",
  program_title: "Eyewitness News at Midnight",
  message: "Control Room is watching a channel",
  timestamp: "2026-10-29T03:45:00Z",
  image_url: "/test-artwork.svg",
  dvr_id: "main-dvr",
  dvr_name: "Production DVR",
  channel_name: "Metropolitan Broadcasting",
  channel_number: "7",
  device_name: "Basement-Production",
  device_ip: "192.0.2.40",
  stream_source: "Television",
}

const longWordRow = {
  ...liveRow,
  id: "long-word-row",
  title: "Donaudampfschifffahrtsgesellschaftskapitan",
  program_title: "Donaudampfschifffahrtsgesellschaftskapitan",
}

const recording = {
  id: "rec-1",
  title: "Eyewitness Production Special",
  start_time: Math.floor(Date.now() / 1000) + 7200,
  end_time: Math.floor(Date.now() / 1000) + 9000,
  channel: "Metropolitan Broadcasting",
  scheduled_time: "Wednesday at 11:30 PM",
  image: "/test-artwork.svg",
  dvr_id: "main-dvr",
  dvr_name: "Production DVR",
}

const prepare = async (page: Page, theme: "light" | "dark") => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.addInitScript((value) => localStorage.setItem("theme", value), theme)
  await applyDoubleText(page)
  await installApiMocks(page)
  await page.route("**/test-artwork.svg", (route) => route.fulfill({ contentType: "image/svg+xml", body: artwork }))
  await page.route("**/api/recent-activity**", (route) => route.fulfill({ json: [liveRow, longWordRow] }))
  await page.route("**/api/activity-history**", (route) => route.fulfill({ json: { items: [liveRow, longWordRow], total: 2, offset: 0, limit: 25 } }))
  await page.route("**/api/recordings/upcoming**", (route) => route.fulfill({ json: [recording] }))
}

const expectNoSidewaysScroll = async (dialog: Locator) => {
  const overflow = await dialog.evaluate((element) => element.scrollWidth - element.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
}

const row = (page: Page, title: string) => page.locator("main").getByRole("button").filter({ has: page.getByText(title, { exact: true }) }).first()

for (const theme of ["light", "dark"] as const) {
  test(`detail sheets wrap between words at 375px with 200% text in ${theme} mode`, async ({ page }, testInfo) => {
    await prepare(page, theme)
    await page.goto("/#overview")

    await row(page, liveRow.title).scrollIntoViewIfNeeded()
    await row(page, liveRow.title).click()
    const dialog = page.getByRole("dialog")
    await expect(dialog.getByText(liveRow.device_name)).toBeVisible()
    await page.waitForTimeout(400)
    await dialog.screenshot({ path: testInfo.outputPath(`activity-${theme}.png`) })
    expect(await findBrokenWords(dialog)).toEqual([])
    await expectNoSidewaysScroll(dialog)
    await dialog.getByRole("button", { name: "Close", exact: true }).click()
    await expect(dialog).toBeHidden()

    const upcoming = row(page, recording.title)
    await upcoming.scrollIntoViewIfNeeded()
    await upcoming.click()
    await expect(dialog.getByText("Wednesday at 11:30 PM")).toBeVisible()
    await page.waitForTimeout(400)
    await dialog.screenshot({ path: testInfo.outputPath(`recording-${theme}.png`) })
    expect(await findBrokenWords(dialog)).toEqual([])
    await expectNoSidewaysScroll(dialog)
  })

  test(`a word wider than the sheet still breaks without sideways scroll in ${theme} mode`, async ({ page }, testInfo) => {
    await prepare(page, theme)
    await page.goto("/#overview")
    await row(page, longWordRow.title).scrollIntoViewIfNeeded()
    await row(page, longWordRow.title).click()
    const dialog = page.getByRole("dialog")
    const title = dialog.getByRole("heading", { name: longWordRow.title })
    await expect(title).toBeVisible()
    await page.waitForTimeout(400)
    await dialog.screenshot({ path: testInfo.outputPath(`long-word-${theme}.png`) })
    expect(await title.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    await expectNoSidewaysScroll(dialog)
  })
}

for (const section of ["overview", "watch-history", "notification-log", "diagnostics", "settings:general", "settings:alerts", "settings:advanced", "settings:notifications", "settings:routing", "settings:security", "settings:backup", "settings:updates", "help-feedback", "about"]) {
  test(`${section} keeps words whole at 375px with 200% text`, async ({ page }) => {
    await prepare(page, "dark")
    await page.goto(`/#${section}`)
    await expect(page.locator("main")).toBeVisible()
    await page.waitForLoadState("networkidle")
    await page.waitForTimeout(500)
    expect(await findBrokenWords(page.locator("main"))).toEqual([])
    expect(await findTextOverButtons(page.locator("main"))).toEqual([])
  })
}

test("tile, More and report sheets keep words whole at 375px with 200% text", async ({ page }) => {
  await prepare(page, "dark")
  await page.goto("/#overview")
  const dialog = page.getByRole("dialog")
  for (const name of ["Active Streams", "Upcoming", "Disk Space", "ChannelWatch Uptime"]) {
    const tile = page.locator(`main button[data-tile-button][aria-label^="${name}"]`).first()
    if (!(await tile.isVisible())) continue
    await tile.click()
    await expect(dialog).toBeVisible()
    await page.waitForTimeout(400)
    expect(await findBrokenWords(dialog), name).toEqual([])
    await dialog.getByRole("button", { name: "Close", exact: true }).click()
    await expect(dialog).toBeHidden()
  }
  await page.getByRole("button", { name: "More", exact: true }).first().click()
  await expect(page.getByTestId("mobile-more-sheet")).toBeVisible()
  await page.waitForTimeout(400)
  expect(await findBrokenWords(page.getByTestId("mobile-more-sheet"))).toEqual([])
  await page.goto("/#help-feedback")
  await page.getByTestId("help-report-problem").first().click()
  await expect(dialog).toBeVisible()
  await page.waitForTimeout(400)
  expect(await findBrokenWords(dialog)).toEqual([])
})
