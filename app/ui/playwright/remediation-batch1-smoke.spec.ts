import AxeBuilder from "@axe-core/playwright"
import { expect, test, type Page } from "@playwright/test"
import { installApiMocks, mockSettings } from "./support/mock-api"

async function settleProviderTransitions(page: Page) {
  await page.getByRole("tabpanel", { name: "Notifications", exact: true }).evaluate(async element => {
    await Promise.all(element.getAnimations({ subtree: true })
      .filter(animation => animation.effect?.getTiming().iterations !== Infinity)
      .map(animation => animation.finished.catch(() => undefined)))
  })
}

test.beforeEach(async ({ page }) => { await installApiMocks(page) })

test("Settings mounted hash changes and history preserve unsaved values", async ({ page }) => {
  await page.goto("/#settings:general")
  const name = page.getByPlaceholder("e.g., Main DVR")
  await name.fill("Unsaved DVR name")
  await page.evaluate(() => { location.hash = "settings:security" })
  await expect(page.getByRole("tab", { name: "Security", exact: true })).toHaveAttribute("aria-selected", "true")
  await page.goBack()
  await expect(page.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("aria-selected", "true")
  await expect(name).toHaveValue("Unsaved DVR name")
  await page.getByRole("tab", { name: "Advanced", exact: true }).click()
  await expect(page).toHaveURL(/#settings:advanced$/)
  await page.evaluate(() => { location.hash = "settings:unknown" })
  await expect(page.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("aria-selected", "true")
})

for (const [tab, name] of [
  ["general", "Background Images"], ["advanced", "Cache Settings"],
  ["advanced", "Rate Limiting"], ["advanced", "Alert Timing"],
  ["advanced", "Disk Alert Controls"], ["advanced", "Error Reporting"],
]) {
  test(`Settings disclosure ${name} supports keyboard and state`, async ({ page }) => {
    await page.goto(`/#settings:${tab}`)
    const control = page.getByRole("button", { name: new RegExp(name) })
    await expect(control).toHaveAttribute("aria-expanded", "false")
    await control.focus()
    await page.keyboard.press("Enter")
    await expect(control).toHaveAttribute("aria-expanded", "true")
    const id = await control.getAttribute("aria-controls")
    expect(id).toBeTruthy()
    await expect(page.locator(`[id="${id}"]`)).toBeVisible()
    await page.keyboard.press("Space")
    await expect(control).toHaveAttribute("aria-expanded", "false")
    await expect(control).toBeFocused()
  })
}

for (const theme of ["light", "dark"]) {
for (const rule of ["button-name", "color-contrast"]) {
  test(`Notifications ${rule} in enabled ${theme}-theme provider states`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("theme", value), theme)
    await page.route("**/api/settings", route => route.fulfill({ json: {
      ...mockSettings, apprise_pushover: "test-user@test-token", apprise_discord: "test/test",
      apprise_email: "user:pass@example.test", apprise_email_to: "to@example.test",
      apprise_telegram: "test-token/test-chat", apprise_slack: "test/test/test",
      apprise_gotify: "example.test/test", apprise_matrix: "user:pass@example.test/#room",
      apprise_custom: "json://example.test/notify",
      webhooks: [{ url: "https://example.test/webhook", secret: "test-secret", enabled: true }],
    }}))
    await page.goto("/#settings:notifications")
    await expect(page.getByRole("tab", { name: "Notifications", exact: true })).toHaveAttribute("aria-selected", "true")
    await settleProviderTransitions(page)
    const result = await new AxeBuilder({ page }).include("[role=tabpanel]").withRules([rule]).analyze()
    expect(result.violations).toEqual([])
    if (rule === "button-name") await page.screenshot({ path: test.info().outputPath(`notifications-${theme}.png`), fullPage: true })
    // Inspect inherited then overridden controls in the same provider panel.
    await page.getByRole("button", { name: "Main DVR", exact: true }).click()
    await settleProviderTransitions(page)
    const inherited = await new AxeBuilder({ page }).include("[role=tabpanel]").withRules([rule]).analyze()
    expect(inherited.violations).toEqual([])
    await page.getByRole("switch", { name: "Pushover", exact: true }).click()
    await settleProviderTransitions(page)
    const overridden = await new AxeBuilder({ page }).include("[role=tabpanel]").withRules([rule]).analyze()
    expect(overridden.violations).toEqual([])
  })
}
}

test("Restart is a named modal with contained focus", async ({ page }) => {
  await page.route("**/api/**restart**", route => route.fulfill({ json: { message: "Restarting" } }))
  await page.goto("/#overview")
  page.once("dialog", dialog => dialog.accept())
  await page.getByRole("button", { name: "Restart", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: /Restart/i })
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveAttribute("aria-modal", "true")
  await page.screenshot({ path: test.info().outputPath("restart-modal.png") })
  await expect.poll(() => page.locator("main").evaluate(element => !!element.closest('[inert]'))).toBe(true)
  for (let index = 0; index < 4; index++) {
    await page.keyboard.press("Tab")
    expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true)
  }
  await expect(dialog).toHaveCount(0, { timeout: 15000 })
  await expect(page.getByRole("button", { name: "Restart", exact: true })).toBeFocused()
})

for (const endpoint of ["system-info", "recordings/upcoming", "activity-history"]) {
  test(`Dashboard marks ${endpoint} failure stale and clears it on recovery`, async ({ page }) => {
    await page.goto("/#overview")
    await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Refresh", exact: true })).toBeEnabled()
    await page.route(`**/api/${endpoint}**`, route => route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } }))
    await page.getByRole("button", { name: "Refresh", exact: true }).click()
    await expect(page.getByText(/some data may be stale/)).toBeVisible()
    await page.unroute(`**/api/${endpoint}**`)
    await page.getByRole("button", { name: "Refresh", exact: true }).click()
    await expect(page.getByText(/some data may be stale/)).toHaveCount(0)
  })
}
