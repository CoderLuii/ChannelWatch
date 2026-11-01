import { describe, expect, it } from "vitest"

import { buildHealthVerdict, coreStatusLabel, type HealthVerdictInput } from "@/lib/health-verdict"

const connected = { connected: true, enabled: true, name: "Main" }
const offline = { connected: false, enabled: true, name: "Den" }
const base: HealthVerdictInput = { loaded: true, coreStatus: "Running", dvrs: [connected], diskSeverity: "normal", hasFailedFetches: false }
const verdict = (over: Partial<HealthVerdictInput>) => buildHealthVerdict({ ...base, ...over })
const monitor = (monitoring_status: string, extra = {}) => ({ ...connected, monitoring_ready: false, monitoring_status, ...extra })

describe("one truthful line", () => {
  it("is healthy with nothing wrong, and drops the version", () => {
    const result = verdict({})
    expect(result).toMatchObject({ tone: "healthy", headline: "Monitoring healthy", details: ["1 DVR connected"] })
    expect(result.more).toBeUndefined()
    expect(result.action).toBeUndefined()
  })

  it("ranks danger over warning over pending and counts the rest", () => {
    const result = verdict({ coreStatus: "Stopped", diskSeverity: "warning", dvrs: [connected, monitor("stale")] })
    expect(result).toMatchObject({ tone: "danger", headline: "Monitoring engine is not running", more: 2 })
    expect(result.action?.kind).toBe("diagnostics")
  })

  it.each([
    [{ coreStatus: "Stopped" }, "danger", "Monitoring engine is not running"],
    [{ coreStatus: "Error" }, "warning", "Can't confirm the monitoring engine. Open Diagnostics."],
    [{ dvrs: [] }, "danger", "No DVR connected"],
    [{ dvrs: [offline] }, "danger", "No DVR connected"],
    [{ diskSeverity: "critical" }, "danger", "Disk space is critically low"],
    [{ dvrs: [monitor("dead")] }, "danger", "Monitoring stopped on Main"],
    [{ dvrs: [connected, offline] }, "warning", "1 of 2 DVRs not connected"],
    [{ diskSeverity: "warning" }, "warning", "Disk space is running low"],
    [{ dvrs: [monitor("stale")] }, "warning", "Stale monitoring on Main"],
    [{ coreStatus: "Unknown" }, "warning", "Can't confirm the monitoring engine. Open Diagnostics."],
    [{ hasFailedFetches: true }, "warning", "Some dashboard data could not be loaded"],
    [{ dvrs: [monitor("starting")] }, "pending", "Monitoring starting on Main"],
    [{ dvrs: [monitor("reconnecting")] }, "pending", "Waiting for monitoring on Main"],
    [{ dvrs: [monitor("missing")] }, "pending", "Waiting for monitoring on Main"],
    [{ coreStatus: "Starting" }, "pending", "Checking system status"],
    [{ loaded: false }, "pending", "Checking system status"],
  ] as Array<[Partial<HealthVerdictInput>, string, string]>)("%j reads as %s: %s", (over, tone, headline) => {
    expect(verdict(over)).toMatchObject({ tone, headline })
  })

  it("does not guess from old data when nothing came back", () => {
    const result = verdict({ coreStatus: "Unavailable", hasFailedFetches: true, dvrs: [offline], diskSeverity: "critical" })
    expect(result).toMatchObject({ tone: "warning", headline: "Can't reach ChannelWatch. Check the container, then Refresh." })
    expect(result.more).toBeUndefined()
  })

  it.each([
    ["rate_limited", "ChannelWatch is busy. It will try again shortly."],
    ["server_error", "ChannelWatch could not load this data. Try Refresh."],
    ["unreachable", "Can't reach ChannelWatch. Check the container, then Refresh."],
  ] as const)("a %s read says so, and only an outage blames the container", (failureKind, headline) => {
    expect(verdict({ coreStatus: "Unavailable", hasFailedFetches: true, failureKind }).headline).toBe(headline)
  })

  it("ignores disabled DVRs and monitors", () => {
    expect(verdict({ dvrs: [connected, { connected: false, enabled: false }] }).tone).toBe("healthy")
    expect(verdict({ dvrs: [{ ...connected, monitoring_ready: false, monitoring_status: "disabled" }] }).tone).toBe("healthy")
  })

  it("does not stack a monitor complaint on a DVR already reported offline", () => {
    const result = verdict({ dvrs: [connected, { ...offline, monitoring_ready: false, monitoring_status: "stale" }] })
    expect(result.headline).toBe("1 of 2 DVRs not connected")
    expect(result.more).toBeUndefined()
  })

  it("keeps the monitor's own reason and links to Diagnostics", () => {
    const result = verdict({ dvrs: [monitor("stale", { monitoring_reason: "No fresh event stream data." })] })
    expect(result.details).toEqual(["No fresh event stream data."])
    expect(result.action).toEqual({ kind: "diagnostics", label: "Diagnose" })
  })

  it("explains startup with its own copy, not the raw reason", () => {
    const result = verdict({ dvrs: [monitor("starting", { monitoring_reason: "No freshness state published yet" })] })
    expect(result.details.join(" ")).toContain("after the first check")
    expect(result.details.join(" ")).not.toContain("No freshness state")
    expect(result.action).toBeUndefined()
  })

  it("gives disk warnings the figures and a way to the disk sheet", () => {
    const result = verdict({ diskSeverity: "warning", diskDetail: "116 GB left of 1.82 TB" })
    expect(result.details).toEqual(["116 GB left of 1.82 TB"])
    expect(result.action).toEqual({ kind: "disk", label: "See disk" })
  })
})

describe("alerts off", () => {
  it("is said once, on an otherwise healthy line", () => {
    const result = verdict({ notificationServices: 0 })
    expect(result).toMatchObject({ tone: "healthy", headline: "DVR healthy" })
    expect(result.action).toEqual({ kind: "notifications", label: "Set up notifications" })
  })

  it("waits for settings to load, and gives way to any real problem", () => {
    expect(verdict({ notificationServices: undefined }).headline).toBe("Monitoring healthy")
    expect(verdict({ notificationServices: 1 }).headline).toBe("Monitoring healthy")
    expect(verdict({ notificationServices: 0, diskSeverity: "warning" }).headline).toBe("Disk space is running low")
  })
})

describe("coreStatusLabel", () => {
  it.each([
    ["Error", "Unknown"],
    ["Backoff", "Retrying start"],
    ["Exited", "Not running"],
    ["Fatal", "Not running"],
    ["Running", "Running"],
    ["Stopped", "Stopped"],
  ])("%s reads as %s", (status, label) => {
    expect(coreStatusLabel(status)).toBe(label)
  })
})
