import { defineConfig, devices } from "@playwright/test"

const baseURL = process.env.CW_E2E_BASE_URL || "http://127.0.0.1:8512"

export default defineConfig({
  testDir: "./playwright",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1080 },
  },
  projects: [
    {
      name: "auth-live",
      testMatch: /.*auth-bootstrap-live\.spec\.ts/,
    },
  ],
})
