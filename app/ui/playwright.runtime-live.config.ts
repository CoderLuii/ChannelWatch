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
  reporter: [["line"], ["html", { open: "never" }]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    headless: true,
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
