import { defineConfig, devices } from "@playwright/test"

const port = 3000
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: "./playwright",
  testIgnore: /.*auth-bootstrap-live\.spec\.ts/,
  // Browser text rasterization and line wrapping differ slightly between the
  // macOS review host and GitHub's Linux runners. Keep reviewed baselines for
  // each platform instead of weakening the visual-diff threshold.
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{platform}/{arg}{ext}",
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
    {
      name: "visual",
      testMatch: /.*v101-visual\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1080 },
      },
    },
    {
      name: "v101-firefox",
      testMatch: /.*v101-smoke\.spec\.ts/,
      use: {
        ...devices["Desktop Firefox"],
        viewport: { width: 1440, height: 1080 },
      },
    },
    {
      name: "v101-webkit",
      testMatch: /.*v101-smoke\.spec\.ts/,
      use: {
        ...devices["Desktop Safari"],
        viewport: { width: 1440, height: 1080 },
      },
    },
    {
      name: "v101-mobile-safari",
      testMatch: /.*v101-smoke\.spec\.ts/,
      use: {
        ...devices["iPhone 13"],
        viewport: { width: 375, height: 812 },
      },
    },
    {
      name: "v101-responsive",
      testMatch: /.*v101-responsive\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
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
