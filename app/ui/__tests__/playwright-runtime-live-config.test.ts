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
    stdout: result.stdout,
  }
}

describe("runtime-live Playwright isolation", () => {
  it("keeps live mutations out of the normal suite and requires the dedicated external URL", () => {
    const normal = listTests([])
    expect(normal.status, normal.output).toBe(0)
    expect(normal.output).toMatch(/Total: \d+ tests in \d+ files/)
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
        "--reporter=json",
      ],
      "http://127.0.0.1:65534",
    )
    expect(dedicated.status, dedicated.output).toBe(0)
    const report = JSON.parse(dedicated.stdout) as {
      config: { globalTimeout: number, projects: Array<{ name: string, timeout: number }> }
      suites: Array<{ specs: Array<{ title: string, tests: Array<{ timeout: number }> }> }>
    }
    expect(report.config.globalTimeout).toBe(25 * 60_000)
    expect(report.config.projects).toEqual([
      expect.objectContaining({ name: "runtime-live", timeout: 25 * 60_000 }),
    ])
    const specs = report.suites.flatMap((suite) => suite.specs)
    expect(specs.map((spec) => spec.title)).toEqual([
      "runtime polling enforces restart, rollback, and exact readiness responses",
      "status reset audit binds request identity and recovery (rollback)",
      "status reset audit binds request identity and recovery (apply)",
      "status reset audit binds request identity and recovery (unobserved_apply_ack)",
      "status reset audit binds request identity and recovery (steady)",
      "status reset audit binds request identity and recovery (unacknowledged)",
      "status reset audit binds request identity and recovery (started_before_ack)",
      "status reset audit binds request identity and recovery (stale_ack)",
      "status reset audit binds request identity and recovery (wrong_origin)",
      "status reset audit binds request identity and recovery (query)",
      "status reset audit binds request identity and recovery (method)",
      "status reset audit binds request identity and recovery (static)",
      "status reset audit binds request identity and recovery (unrecovered)",
      "status reset audit binds request identity and recovery (javascript)",
      "status reset audit binds request identity and recovery (pageerror)",
      "authenticated live Update Center applies and rolls back the exact candidate once",
    ])
    expect(specs.flatMap((spec) => spec.tests.map((listedTest) => listedTest.timeout))).toEqual(
      Array(16).fill(25 * 60_000),
    )
  }, 30_000)
})
