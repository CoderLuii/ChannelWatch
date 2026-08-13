import { defineConfig, devices } from "@playwright/test"

const port = 3012
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: "./playwright",
  testMatch: /.*reporting-fallback\.spec\.ts/,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [{ name: "fallback-chromium" }],
  webServer: {
    command: `corepack pnpm build && corepack pnpm exec vite preview --host 127.0.0.1 --port ${port} --strictPort --outDir out`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120000,
  },
})
