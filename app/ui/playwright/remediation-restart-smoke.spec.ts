import { expect, test } from "@playwright/test"
import { installApiMocks } from "./support/mock-api"

test("restart timeout keeps keyboard focus inside recovery controls", async ({ page }) => {
  await installApiMocks(page)
  await page.clock.install()
  await page.goto("/#overview")
  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  await page.route("**/healthz/**", route => route.fulfill({ status: 503, json: { detail: "fixture unavailable" } }))
  await page.route("**/api/**restart**", route => route.fulfill({ json: { message: "Restarting" } }))
  page.once("dialog", dialog => dialog.accept())
  await page.getByRole("button", { name: "Restart", exact: true }).click()

  const modal = page.getByRole("dialog", { name: "Restart", exact: true })
  await expect(modal).toBeVisible()
  await page.clock.fastForward(65000)
  await page.clock.resume()
  await expect(modal.getByRole("button", { name: "Retry", exact: true })).toBeVisible()
  await page.keyboard.press("Tab")
  await expect.poll(() => modal.evaluate(el => el.contains(document.activeElement))).toBe(true)
  await page.keyboard.press("Shift+Tab")
  await expect.poll(() => modal.evaluate(el => el.contains(document.activeElement))).toBe(true)
  await expect.poll(() => page.locator("main").evaluate(el => Boolean(el.closest("[inert]")))).toBe(true)
  await page.keyboard.press("Escape")
  await expect(modal).toHaveCount(0, { timeout: 15000 })
  await expect(page.getByRole("button", { name: "Restart", exact: true })).toBeFocused()
})
