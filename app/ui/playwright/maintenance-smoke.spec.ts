import { expect, test } from "@playwright/test"

import { installApiMocks, maintenanceWindows } from "./support/mock-api"

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("dashboard shows the known per-DVR maintenance window", async ({ page }) => {
  await page.goto("/#overview")

  const card = page.getByTestId("maintenance-windows-card")
  await expect(card.getByText("Main DVR", { exact: true })).toBeVisible()
  await expect(card.getByText("Known schedule only", { exact: true })).toBeVisible()
  await expect(card.getByText("2h 30m", { exact: true })).toBeVisible()
  await expect(card.getByText(/Later time was not assessed/)).toBeVisible()
})

test("malformed maintenance response shows an error without crashing the dashboard", async ({ page }) => {
  const pageErrors: Error[] = []
  page.on("pageerror", (error) => pageErrors.push(error))
  await page.route("**/api/v1/maintenance-windows**", (route) => route.fulfill({ json: {} }))

  await page.goto("/#overview")

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  await expect(page.getByText("Maintenance windows could not be loaded.", { exact: true })).toBeVisible()
  expect(pageErrors).toEqual([])
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
    await expect(page.getByText("Maintenance windows could not be loaded.", { exact: true })).toBeVisible()
    expect(pageErrors).toEqual([])
  })
}
