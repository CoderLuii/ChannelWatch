import { spawnSync } from "node:child_process"
import path from "node:path"

import { describe, expect, it } from "vitest"

const uiRoot = path.resolve(import.meta.dirname, "..")
const playwrightCli = path.join(uiRoot, "node_modules", "@playwright", "test", "cli.js")

function listTests(args: string[], baseURL = "") {
  const result = spawnSync(process.execPath, [playwrightCli, "test", ...args, "--list"], {
    cwd: uiRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      CHANNELWATCH_PLAYWRIGHT_BASE_URL: baseURL,
    },
  })
  return {
    status: result.status,
    output: result.stdout + result.stderr,
  }
}

describe("runtime-live Playwright isolation", () => {
  it("keeps the normal suite at 168 tests and requires the dedicated external URL", () => {
    const normal = listTests([])
    expect(normal.status, normal.output).toBe(0)
    expect(normal.output).toContain("Total: 168 tests in 13 files")
    expect(normal.output).not.toContain("runtime-update-live-smoke.spec.ts")

    const missingURL = listTests([
      "-c",
      "playwright.runtime-live.config.ts",
      "--project=runtime-live",
    ])
    expect(missingURL.status).not.toBe(0)
    expect(missingURL.output).toContain(
      "CHANNELWATCH_PLAYWRIGHT_BASE_URL is required for runtime-live Playwright.",
    )

    const dedicated = listTests(
      [
        "-c",
        "playwright.runtime-live.config.ts",
        "playwright/runtime-update-live-smoke.spec.ts",
        "--project=runtime-live",
      ],
      "http://127.0.0.1:65534",
    )
    expect(dedicated.status, dedicated.output).toBe(0)
    expect(dedicated.output).toContain("Total: 1 test in 1 file")
  }, 30_000)
})
