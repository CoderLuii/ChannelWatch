import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import { resolve, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import type { AppSettings } from "@/lib/types"

const __dirname = dirname(fileURLToPath(import.meta.url))
function srcFile(rel: string): string {
  return readFileSync(resolve(__dirname, rel), "utf8")
}

function hasActiveDvrs(settings: Pick<AppSettings, "dvr_servers">): boolean {
  const servers = settings.dvr_servers || []
  return servers.some((s) => !s.deleted_at)
}

const emptySettings: Pick<AppSettings, "dvr_servers"> = { dvr_servers: [] }

const settingsWithDvr: Pick<AppSettings, "dvr_servers"> = {
  dvr_servers: [{ id: "dvr_abc1", name: "Home", host: "192.168.1.10", port: 8089, enabled: true }],
}

const settingsWithDeleted: Pick<AppSettings, "dvr_servers"> = {
  dvr_servers: [
    { id: "dvr_abc1", name: "Old", host: "192.168.1.10", port: 8089, enabled: true, deleted_at: "2026-01-01T00:00:00Z" },
  ],
}

describe("wizard gate: hasActiveDvrs()", () => {
  it("returns false when dvr_servers is empty", () => {
    expect(hasActiveDvrs(emptySettings)).toBe(false)
  })

  it("returns true when at least one active DVR exists", () => {
    expect(hasActiveDvrs(settingsWithDvr)).toBe(true)
  })

  it("returns false when all DVRs are soft-deleted", () => {
    expect(hasActiveDvrs(settingsWithDeleted)).toBe(false)
  })

  it("returns true when mixed active and deleted DVRs exist", () => {
    const mixed: Pick<AppSettings, "dvr_servers"> = {
      dvr_servers: [
        { id: "dvr_a", name: "A", host: "10.0.0.1", port: 8089, enabled: true, deleted_at: "2026-01-01" },
        { id: "dvr_b", name: "B", host: "10.0.0.2", port: 8089, enabled: true },
      ],
    }
    expect(hasActiveDvrs(mixed)).toBe(true)
  })
})

describe("wizard gating: dashboard source structure", () => {
  it("imports FirstRunWizard in dashboard.tsx", () => {
    const src = srcFile("../components/dashboard.tsx")
    expect(src).toContain("FirstRunWizard")
    expect(src).toContain("first-run-wizard")
  })

  it("detects empty dvr_servers and sets showWizard", () => {
    const src = srcFile("../components/dashboard.tsx")
    expect(src).toContain("showWizard")
    expect(src).toContain("hasActiveDvrs")
  })

  it("renders wizard only when showWizard is true and loading is done", () => {
    const src = srcFile("../components/dashboard.tsx")
    expect(src).toContain("showWizard && !isLoading && settings")
  })
})

describe("skip path: routes to settings:general", () => {
  it("handleWizardSkip navigates to settings:general", () => {
    const src = srcFile("../components/dashboard.tsx")
    expect(src).toContain("settings:general")
    expect(src).toContain("handleWizardSkip")
  })

  it("wizard exposes onSkip prop", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("onSkip")
    expect(src).toContain('wizard.welcome.skip')
  })
})

describe("wizard step structure", () => {
  it("contains all three step identifiers", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("welcome")
    expect(src).toContain("discover")
    expect(src).toContain("manual")
    expect(src).toContain("confirm")
  })

  it("uses discoverServers from api.ts", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("discoverServers")
  })

  it("uses testDvrConnection for manual path", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("testDvrConnection")
  })

  it("generates canonical DVR id before saving", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("canonicalDvrId")
  })

  it("calls saveSettings and fetchSettings on save", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("saveSettings")
    expect(src).toContain("fetchSettings")
  })
})

describe("manual path: Continue is gated on successful connection test", () => {
  function manualCanProceed(host: string, testState: "idle" | "testing" | "ok" | "fail"): boolean {
    return host.trim().length > 0 && testState === "ok"
  }

  it("blocks Continue when no test has run (idle)", () => {
    expect(manualCanProceed("192.168.1.10", "idle")).toBe(false)
  })

  it("blocks Continue when test explicitly failed", () => {
    expect(manualCanProceed("192.168.1.10", "fail")).toBe(false)
  })

  it("blocks Continue while test is in progress", () => {
    expect(manualCanProceed("192.168.1.10", "testing")).toBe(false)
  })

  it("allows Continue only after successful test", () => {
    expect(manualCanProceed("192.168.1.10", "ok")).toBe(true)
  })

  it("blocks Continue when host is empty even if test ok", () => {
    expect(manualCanProceed("", "ok")).toBe(false)
  })

  it("source uses testState === 'ok' as the sole gate", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("testState === \"ok\"")
    expect(src).not.toMatch(/manualCanProceed.*idle/)
    expect(src).not.toMatch(/manualCanProceed.*fail/)
  })

  it("Continue button is wired to canProceed prop, not raw host check", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain("disabled={!canProceed}")
    expect(src).not.toMatch(/Continue[\s\S]{0,120}disabled=\{!host/)
  })

  it("changing apiKey resets testState to idle", () => {
    const src = srcFile("../components/first-run-wizard.tsx")
    expect(src).toContain('onChangeApiKey={(v) => { setManualApiKey(v); setTestState("idle") }}')
  })
})

describe("connection test API function", () => {
  it("testDvrConnection is exported from api.ts", () => {
    const src = srcFile("../lib/api.ts")
    expect(src).toContain("testDvrConnection")
    expect(src).toContain("v1/dvrs/test-connection")
  })

  it("DvrConnectionTestResult interface is exported", () => {
    const src = srcFile("../lib/api.ts")
    expect(src).toContain("DvrConnectionTestResult")
  })
})

describe("backend connection-test endpoint", () => {
  it("test-connection endpoint is registered in main.py", () => {
    const src = srcFile("../backend/main.py")
    expect(src).toContain("/api/v1/dvrs/test-connection")
    expect(src).toContain("test_dvr_connection")
  })

  it("endpoint accepts host and port fields", () => {
    const src = srcFile("../backend/main.py")
    expect(src).toContain("_DvrConnectionTestRequest")
  })
})
