import { expect, test } from "@playwright/test"

import { installApiMocks } from "./support/mock-api"

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test("release-day smoke: configured bootstrap, core navigation, and diagnostics admin flow", async ({ page }) => {
  await page.goto("/#overview")

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  await expect(page.getByText("1 live stream, 1 recording in progress")).toBeVisible()
  await expect(page.getByText("10.94 TB Free")).toBeVisible()
  await expect(page.getByText("10.94 TB GB Free")).toHaveCount(0)

  await page.getByRole("button", { name: "Watch History", exact: true }).click()
  await expect(page).toHaveURL(/#watch-history$/)
  await expect(page.getByText("Watch History", { exact: true })).toBeVisible()
  await expect(page.getByRole("textbox", { name: "Search activity history" })).toBeVisible()

  await page.getByRole("button", { name: "Notification Log", exact: true }).click()
  await expect(page).toHaveURL(/#notification-log$/)
  await expect(page.getByText("Notification Delivery Log")).toBeVisible()
  await expect(page.getByRole("combobox", { name: "Filter notification log by channel" })).toBeVisible()

  await page.getByRole("button", { name: "Settings", exact: true }).click()
  await expect(page).toHaveURL(/#settings$/)
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible()
  await expect(page.getByRole("tab", { name: "Security" })).toBeVisible()

  await page.getByRole("button", { name: "Diagnostics", exact: true }).click()
  await expect(page).toHaveURL(/#diagnostics$/)
  await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible()
  await expect(page.getByText("10.94 TB").first()).toBeVisible()
  await expect(page.getByText("ChannelWatch started successfully")).toBeVisible()

  const downloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Download sanitized debug bundle" }).click()
  const download = await downloadPromise

  expect(download.suggestedFilename()).toMatch(/^channelwatch_debug_.*\.zip$/)
  await expect.poll(() => download.failure()).toBeNull()
})
