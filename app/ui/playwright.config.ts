import { defineConfig, devices } from "@playwright/test"

const port = 3000
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: "./playwright",
  testIgnore: /.*auth-bootstrap-live\.spec\.ts/,
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "a11y",
      testMatch: /.*(a11y|keyboard)\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1080 },
      },
    },
    {
      name: "smoke",
      testMatch: /.*smoke\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1080 },
      },
    },
  ],
  webServer: {
    command: `corepack pnpm build && corepack pnpm exec vite preview --host 127.0.0.1 --port ${port} --strictPort --outDir out`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
})
