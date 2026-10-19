import { expect, test } from "@playwright/test"

import { installApiMocks, maintenanceWindows } from "./support/mock-api"

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("maintenance windows follow activity and recordings and start compact", async ({ page }) => {
  await page.goto("/#overview")

  const card = page.getByTestId("maintenance-windows-card")
  const disclosure = card.locator('button[aria-controls="maintenance-windows-content"]')
  const activityRecordings = page.getByTestId("dashboard-activity-recordings")

  await expect(disclosure).toHaveAttribute("aria-expanded", "false")
  await expect(card.getByText("Main DVR", { exact: true })).toBeHidden()
  await expect(card.getByText(/Potential open time/)).toBeHidden()
  const cardHandle = await card.elementHandle()
  expect(cardHandle).not.toBeNull()
  expect(await activityRecordings.evaluate((node, maintenanceCard) => Boolean(node.compareDocumentPosition(maintenanceCard as Node) & Node.DOCUMENT_POSITION_FOLLOWING), cardHandle)).toBe(true)
  expect((await card.boundingBox())?.height).toBeLessThanOrEqual(90)
})

test("dashboard shows the known per-DVR maintenance window after expansion", async ({ page }) => {
  await page.goto("/#overview")

  const card = page.getByTestId("maintenance-windows-card")
  const disclosure = card.locator('button[aria-controls="maintenance-windows-content"]')
  await disclosure.click()

  await expect(disclosure).toHaveAttribute("aria-expanded", "true")
  await expect(card.getByText(/Potential open time/)).toBeVisible()
  await expect(card.getByText("Main DVR", { exact: true })).toBeVisible()
  await expect(card.getByText("Known schedule only", { exact: true })).toBeVisible()
  await expect(card.getByText("2h 30m", { exact: true })).toBeVisible()
  await expect(card.getByText(/Later time was not assessed/)).toBeVisible()

  const filteredRequest = page.waitForRequest((request) => {
    const url = new URL(request.url())
    return url.pathname === "/api/v1/maintenance-windows" && url.searchParams.get("minimum_minutes") === "120"
  })
  await card.getByLabel("Minimum window").selectOption("120")
  await filteredRequest
})

test("maintenance disclosure supports keyboard expansion and collapse", async ({ page }) => {
  await page.goto("/#overview")

  const disclosure = page.getByTestId("maintenance-windows-card").locator('button[aria-controls="maintenance-windows-content"]')
  await disclosure.focus()
  await page.keyboard.press("Enter")
  await expect(disclosure).toHaveAttribute("aria-expanded", "true")
  await page.keyboard.press("Space")
  await expect(disclosure).toHaveAttribute("aria-expanded", "false")
})

test("collapsed maintenance state reports loading before an offline DVR result", async ({ page }) => {
  let releaseResponse = () => {}
  const responseReady = new Promise<void>((resolve) => {
    releaseResponse = resolve
  })
  await page.route("**/api/v1/maintenance-windows**", async (route) => {
    await responseReady
    await route.fulfill({
      json: {
        ...maintenanceWindows,
        dvrs: [{
          ...maintenanceWindows.dvrs[0],
          status: "offline",
          coverage_end: null,
          windows: [],
          message: "The DVR schedule could not be reached, so no time was marked free.",
        }],
      },
    })
  })

  await page.goto("/#overview")
  const card = page.getByTestId("maintenance-windows-card")
  const disclosure = card.locator('button[aria-controls="maintenance-windows-content"]')
  await expect(disclosure.getByText("Checking recording schedules...", { exact: true })).toBeVisible()

  releaseResponse()
  await expect(disclosure.getByText("Checking recording schedules...", { exact: true })).toBeHidden()
  await disclosure.click()
  await expect(card.getByText("DVR unavailable", { exact: true })).toBeVisible()
  await expect(card.getByText(/no time was marked free/)).toBeVisible()
})

test("malformed maintenance response shows an error without crashing the dashboard", async ({ page }) => {
  const pageErrors: Error[] = []
  page.on("pageerror", (error) => pageErrors.push(error))
  await page.route("**/api/v1/maintenance-windows**", (route) => route.fulfill({ json: {} }))

  await page.goto("/#overview")

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  const card = page.getByTestId("maintenance-windows-card")
  const disclosure = card.locator('button[aria-controls="maintenance-windows-content"]')
  await expect(disclosure).toHaveAttribute("aria-expanded", "false")
  await expect(disclosure.getByText("Maintenance windows could not be loaded.", { exact: true })).toBeVisible()
  await disclosure.click()
  await expect(card.getByText("Maintenance windows could not be loaded.", { exact: true })).toHaveCount(2)
  expect(pageErrors).toEqual([])
})

test("long DVR names wrap without overflowing at mobile, tablet, or desktop widths", async ({ page }) => {
  const longName = "Living Room Channels DVR With A Very Long Name That Must Wrap Cleanly"
  await page.route("**/api/v1/maintenance-windows**", (route) => route.fulfill({
    json: {
      ...maintenanceWindows,
      dvrs: maintenanceWindows.dvrs.map((dvr) => ({ ...dvr, dvr_name: longName })),
    },
  }))

  await page.goto("/#overview")
  const card = page.getByTestId("maintenance-windows-card")
  await card.locator('button[aria-controls="maintenance-windows-content"]').click()

  for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(card.getByText(longName, { exact: true })).toBeVisible()
    expect(await card.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true)
  }
})

for (const [caseName, payload] of [
  ["invalid timezone", { ...maintenanceWindows, timezone: "Invalid/Zone" }],
  [
    "invalid window date",
    {
      ...maintenanceWindows,
      dvrs: maintenanceWindows.dvrs.map((dvr) => ({
        ...dvr,
        windows: dvr.windows.map((window) => ({ ...window, start: "not-a-date" })),
      })),
    },
  ],
] as const) {
  test(`type-correct ${caseName} response does not crash the dashboard`, async ({ page }) => {
    const pageErrors: Error[] = []
    page.on("pageerror", (error) => pageErrors.push(error))
    await page.route("**/api/v1/maintenance-windows**", (route) => route.fulfill({ json: payload }))

    await page.goto("/#overview")

    await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
    await expect(page.getByTestId("maintenance-windows-card").locator('button[aria-controls="maintenance-windows-content"]').getByText("Maintenance windows could not be loaded.", { exact: true })).toBeVisible()
    expect(pageErrors).toEqual([])
  })
}
