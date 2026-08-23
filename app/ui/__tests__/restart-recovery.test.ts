import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RESTART_RECOVERED_EVENT, fetchMonitoringReadiness, pollForRecovery } from "@/lib/api"

const __dirname = dirname(fileURLToPath(import.meta.url))

function srcFile(rel: string): string {
  return readFileSync(resolve(__dirname, rel), "utf8")
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe("container restart recovery", () => {
  it("uses liveness and startup rather than monitoring readiness", async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response("", { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    const recovered = vi.fn()

    pollForRecovery({ initialDelay: 0, interval: 1, minimumRecoveryMs: 0, onRecovered: recovered })
    await vi.runAllTimersAsync()

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/healthz/live",
      "/healthz/startup",
    ])
    expect(recovered).toHaveBeenCalledTimes(1)
  })

  it("waits for startup after liveness returns", async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response("", { status: 503 }))
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response("", { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    const recovered = vi.fn()

    pollForRecovery({ initialDelay: 0, interval: 1, minimumRecoveryMs: 0, onRecovered: recovered })
    await vi.runAllTimersAsync()

    expect(recovered).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it("never declares a newly requested restart recovered before five seconds", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-08-23T00:00:00Z"))
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    const recovered = vi.fn()

    pollForRecovery({ initialDelay: 0, interval: 1000, onRecovered: recovered })
    await vi.advanceTimersByTimeAsync(4999)
    expect(recovered).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1001)
    expect(recovered).toHaveBeenCalledTimes(1)
  })

  it("classifies an authenticated 503 health response as recovered but degraded", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "degraded", ready: false }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "degraded", ready: false }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      })))
    await expect(fetchMonitoringReadiness()).resolves.toBe(false)
  })

  it("keeps public readiness degradation when authenticated diagnostics are unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "degraded", ready: false }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response("", { status: 401 })))

    await expect(fetchMonitoringReadiness()).resolves.toBe(false)
  })

  it("reruns the full dashboard bootstrap after process recovery", () => {
    const header = srcFile("../components/header.tsx")
    const dashboard = srcFile("../components/dashboard.tsx")

    expect(RESTART_RECOVERED_EVENT).toBe("channelwatch-restart-recovered")
    expect(header).toContain("window.dispatchEvent(new CustomEvent(RESTART_RECOVERED_EVENT))")
    expect(dashboard).toContain("window.addEventListener(RESTART_RECOVERED_EVENT, handleRestartRecovered)")
    expect(dashboard).toContain("void bootstrapApp()")
    expect(dashboard).toContain("window.removeEventListener(RESTART_RECOVERED_EVENT, handleRestartRecovered)")
    expect(header.match(/monitoringReady === true \? "success" : "degraded"/gu)).toHaveLength(2)
  })
})
