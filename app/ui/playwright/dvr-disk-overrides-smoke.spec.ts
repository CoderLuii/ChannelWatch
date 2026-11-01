import { expect, test, type Page, type Route } from "@playwright/test"

import { installApiMocks, mockSettings } from "./support/mock-api"

const fulfillJson = (route: Route, body: unknown) => route.fulfill({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
})

type SavedSettings = { dvr_servers: Array<{ overrides?: Record<string, unknown> }> }

async function openDiskAlertsForDvr(page: Page, overrides: Record<string, unknown>) {
  const saves: SavedSettings[] = []
  const settings = {
    ...mockSettings,
    dvr_servers: mockSettings.dvr_servers.map((server, index) => index === 0 ? { ...server, overrides } : server),
  }
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "POST") {
      saves.push(route.request().postDataJSON() as SavedSettings)
      return fulfillJson(route, { message: "Settings saved successfully" })
    }
    return fulfillJson(route, settings)
  })
  await page.goto("/#settings:alerts")
  await page.getByRole("button", { name: /Disk Space/ }).first().click()
  const section = page.locator("#alert-section-disk")
  await section.getByRole("button", { name: mockSettings.dvr_servers[0].name }).click()
  return { saves, section }
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("clearing a per-DVR disk field goes back to the global value instead of saving 0", async ({ page }) => {
  const { saves, section } = await openDiskAlertsForDvr(page, { ds_warning_threshold_gb: 80 })
  const field = section.locator('input[type="number"]').nth(1)
  await expect(field).toHaveValue("80")

  await field.fill("")
  await expect(field).toHaveValue("")
  await expect(field).toHaveAttribute("placeholder", String(mockSettings.ds_warning_threshold_gb))

  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0].dvr_servers[0].overrides).not.toHaveProperty("ds_warning_threshold_gb")
})

test("a cleared per-DVR field can be retyped without picking up the old digits", async ({ page }) => {
  const { saves, section } = await openDiskAlertsForDvr(page, { ds_warning_threshold_gb: 80 })
  const field = section.locator('input[type="number"]').nth(1)

  await field.fill("")
  await field.pressSequentially("65")
  await expect(field).toHaveValue("65")

  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0].dvr_servers[0].overrides?.ds_warning_threshold_gb).toBe(65)
})

test("a per-DVR explicit 0 is still saved, which turns that threshold off", async ({ page }) => {
  const { saves, section } = await openDiskAlertsForDvr(page, { ds_warning_threshold_gb: 80 })
  await section.locator('input[type="number"]').nth(1).fill("0")

  await page.getByRole("button", { name: "Save settings" }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0].dvr_servers[0].overrides?.ds_warning_threshold_gb).toBe(0)
})

test("a per-DVR critical threshold at or above warning blocks the save and names the DVR", async ({ page }) => {
  const { saves, section } = await openDiskAlertsForDvr(page, { ds_critical_threshold_gb: 40 })
  await section.locator('input[type="number"]').nth(1).fill("40")
  await section.getByRole("button", { name: "Global", exact: true }).click()
  await page.getByRole("button", { name: "Save settings" }).click()

  const message = `Critical GB must be lower than warning GB on ${mockSettings.dvr_servers[0].name}.`
  await expect(page.getByText(message).first()).toBeVisible()
  expect(saves).toHaveLength(0)
  await expect(section.locator('input[type="number"]').nth(1)).toHaveValue("40")
})
