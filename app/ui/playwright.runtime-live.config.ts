import { defineConfig, devices } from "@playwright/test"

const baseURL = process.env.CHANNELWATCH_PLAYWRIGHT_BASE_URL?.trim()
if (!baseURL) {
  throw new Error("CHANNELWATCH_PLAYWRIGHT_BASE_URL is required for runtime-live Playwright.")
}

export default defineConfig({
  testDir: "./playwright",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  globalTimeout: 25 * 60_000,
  timeout: 25 * 60_000,
  reporter: [["line"], ["html", { open: "never" }]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1080 },
  },
  projects: [
    {
      name: "runtime-live",
      testMatch: /.*runtime-update-live-smoke\.spec\.ts/,
    },
  ],
})
