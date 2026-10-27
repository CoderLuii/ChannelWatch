import { expect, test, type Page } from "@playwright/test"
import { readFile } from "node:fs/promises"

import { installApiMocks } from "./support/mock-api"

const diagnosticCommands = [
  "Test Connectivity",
  "Test API Endpoints",
  "Test Channel Watching Alert",
  "Test VOD Watching Alert",
  "Test Disk Space Alert",
  "Test Recording Scheduled Alert",
  "Test Recording Started Alert",
  "Test Recording Completed Alert",
  "Test Recording Stopped Alert",
  "Test Recording Cancelled Alert",
] as const

async function installDiagnosticRunner(page: Page, failedCommand?: string, skippedCommands: readonly string[] = []) {
  const commands: string[] = []
  await installApiMocks(page)
  await page.route("**/api/run_test/*", async (route) => {
    expect(route.request().method()).toBe("POST")
    const command = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1)!).replaceAll("_", " ")
    commands.push(command)
    const knownCommand = diagnosticCommands.some((name) => name === command)
    const skipped = skippedCommands.includes(command)
    const success = knownCommand && command !== failedCommand && !skipped
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        test_name: command,
        success,
        status: skipped ? "skipped" : success ? "passed" : "failed",
        reason: skipped ? "This alert is disabled in Alerts settings." : null,
        target_dvr: { id: "dvr_fixture", name: "First DVR" },
        message: !knownCommand
          ? `Unknown test name received: ${command}`
          : skipped
            ? "This alert is disabled in Alerts settings."
          : success
            ? `Test '${command}' succeeded`
            : "Notification delivery failed",
      }),
    })
  })
  return commands
}

for (const viewport of [
  { name: "mobile", width: 320, height: 844 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "desktop", width: 1440, height: 1080 },
]) {
  test(`disabled alerts are skipped without inflating failed or passed counts on ${viewport.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    const commands = await installDiagnosticRunner(page, undefined, [
      "Test Channel Watching Alert", "Test VOD Watching Alert", "Test Recording Scheduled Alert",
    ])
    await page.goto("/#diagnostics")
    await page.getByRole("button", { name: "Run All Tests", exact: true }).click()
    await expect(page.getByText("7 passed", { exact: true })).toBeVisible()
    await expect(page.getByText("0 failed", { exact: true })).toBeVisible()
    await expect(page.getByText("3 skipped", { exact: true })).toBeVisible()
    await expect(page.getByText("This alert is disabled in Alerts settings.", { exact: true })).toHaveCount(3)
    await expect(page.getByText("Tested DVR: First DVR", { exact: true })).toBeVisible()
    expect(commands).toEqual(diagnosticCommands)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`diagnostics-skipped-${viewport.name}.png`), fullPage: true })
  })
}

for (const viewport of [
  { name: "mobile", width: 390, height: 844 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "desktop", width: 1440, height: 1080 },
]) {
  test(`Run All dispatches supported commands independently of labels on ${viewport.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    const commands = await installDiagnosticRunner(page)
    await page.goto("/#diagnostics")
    await expect(page.getByRole("heading", { name: "Diagnostic Tests", exact: true })).toBeVisible()
    await page.getByRole("button", { name: "Run All Tests", exact: true }).click()
    await expect(page.getByText("Results:", { exact: true })).toBeVisible()
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`diagnostics-${viewport.name}.png`), fullPage: true })

    await expect(page.getByText("10 passed", { exact: true })).toBeVisible()
    await expect(page.getByText("0 failed", { exact: true })).toBeVisible()
    expect(commands).toEqual(diagnosticCommands)
    await expect(page.getByText(/Unknown test name received:/)).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}

test("an individual diagnostic uses its command and shows a real failure", async ({ page }, testInfo) => {
  const commands = await installDiagnosticRunner(page, "Test Disk Space Alert")
  await page.goto("/#diagnostics")
  const diagnostic = page.locator("div.grid").filter({
    has: page.getByRole("heading", { name: "Disk Space Alert", exact: true }),
  }).last()
  await diagnostic.getByRole("button", { name: "Run", exact: true }).click()
  await expect(page.getByText("Results:", { exact: true })).toBeVisible()

  await expect(diagnostic.getByText("Notification delivery failed", { exact: true })).toBeVisible()
  await expect(page.getByText("0 passed", { exact: true })).toBeVisible()
  await expect(page.getByText("1 failed", { exact: true })).toBeVisible()
  expect(commands).toEqual(["Test Disk Space Alert"])

  await page.getByRole("button", { name: "Run All Tests", exact: true }).click()
  await expect(page.getByText("9 passed", { exact: true })).toBeVisible()
  await expect(page.getByText("1 failed", { exact: true })).toBeVisible()
  expect(commands).toEqual(["Test Disk Space Alert", ...diagnosticCommands])
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath("diagnostic-failure.png"), fullPage: true })
})

test("diagnostic export distinguishes skipped alerts from unfinished tests", async ({ page }) => {
  await installDiagnosticRunner(page, undefined, ["Test Channel Watching Alert"])
  await page.goto("/#diagnostics")
  await page.getByRole("button", { name: "Run All Tests", exact: true }).click()
  await expect(page.getByText("1 skipped", { exact: true })).toBeVisible()
  const downloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Export diagnostics", exact: true }).click()
  const download = await downloadPromise
  const contents = await readFile((await download.path())!, "utf8")
  expect(contents).toContain("SKIP Channel Watching Alert")
  expect(contents).toContain("This alert is disabled in Alerts settings.")
  expect(contents).toContain("Tested DVR: First DVR")
  expect(contents).not.toContain("... Channel Watching Alert")
})
