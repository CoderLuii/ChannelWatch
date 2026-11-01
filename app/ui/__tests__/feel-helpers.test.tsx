import React from "react"
import { readFileSync } from "node:fs"
import path from "node:path"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { NowList } from "@/components/dashboard/now-list"
import { isRecordingActive, recordingMeta } from "@/components/dashboard/upcoming-recordings-list"
import { MetricCard } from "@/components/dashboard/metric-card"
import { rubberband, projectedOffset, shouldDismissSheet } from "@/hooks/use-sheet-drag"
import { activityRowText, formatDiskAlertMessage, recordingEventParts } from "@/lib/activity-presentation"
import { describeDeliveryError } from "@/lib/error-catalog"
import { formatCountdown, formatEventAge, formatFreshness } from "@/lib/relative-time"

const read = (relative: string) => readFileSync(path.resolve(__dirname, "..", relative), "utf8")
const NOW = Date.parse("2026-10-30T12:00:00Z")
const HOUR = 60 * 60

describe("countdowns", () => {
  const at = (seconds: number) => NOW / 1000 + seconds
  it.each([
    [at(5 * 60), "in 5m"],
    [at(9 * HOUR + 32 * 60), "in 9h 32m"],
    [at(27 * HOUR), "in 1d 3h"],
    [at(5 * 24 * HOUR), "in 5d"],
    [at(-60), ""],
    [0, ""],
    [Number.NaN, ""],
  ])("%s reads %s", (start, text) => {
    expect(formatCountdown(start, NOW)).toBe(text)
  })
})

describe("event age", () => {
  it("guards bad dates and goes absolute past a week", () => {
    expect(formatEventAge("not-a-date", NOW)).toBe("–")
    expect(formatEventAge("", NOW)).toBe("–")
    expect(formatEventAge(null, NOW)).toBe("–")
    expect(formatEventAge("1970-01-01T00:00:00Z", NOW)).toBe("–")
    expect(formatEventAge(new Date(NOW - 5 * 60000).toISOString(), NOW)).toBe("5m ago")
    expect(formatEventAge(new Date(NOW - 8 * 24 * 3600000).toISOString(), NOW)).toMatch(/2026/)
  })
})

describe("freshness", () => {
  it("says how old the data is, and that it is the last good data after a failure", () => {
    const twoMinutes = new Date(NOW - 2 * 60000)
    expect(formatFreshness(twoMinutes, false, NOW)).toBe("Updated 2 min ago")
    expect(formatFreshness(new Date(NOW - 5000), false, NOW)).toBe("Updated just now")
    expect(formatFreshness(new Date(NOW - 12 * 60000), true, NOW)).toBe("Last good data 12 min ago")
    expect(formatFreshness(null, false, NOW)).toBe("–")
    expect(formatFreshness(null, true, NOW)).toBe("No data yet")
  })
})

describe("swipe to dismiss", () => {
  it("rubber-bands harder the further it goes", () => {
    const small = rubberband(50, 600)
    const large = rubberband(500, 600)
    expect(small).toBeLessThan(50)
    expect(large).toBeLessThan(500)
    expect(large / 500).toBeLessThan(small / 50)
  })

  it("projects a flick forward, so a short fast one still closes", () => {
    expect(projectedOffset(0, 1000)).toBeCloseTo(499, 0)
    expect(shouldDismissSheet(60, 900, 600)).toBe(true)
    expect(shouldDismissSheet(60, 0, 600)).toBe(false)
    expect(shouldDismissSheet(320, 0, 600)).toBe(true)
    expect(shouldDismissSheet(60, -900, 600)).toBe(false)
    expect(shouldDismissSheet(0, 2000, 600)).toBe(false)
  })
})

describe("activity row grammar", () => {
  const row = (over: Record<string, unknown>) => activityRowText({ type: "watching_channel", title: "Watching Channel", message: "m", ...over } as never, "Display", "Type")

  it("leads with the show, then the device", () => {
    expect(row({ program_title: "News", channel_name: "CBS", device_name: "Apple TV" })).toEqual({ title: "News", detail: "Apple TV" })
    expect(row({ channel_name: "CBS", device_name: "Apple TV" })).toEqual({ title: "CBS", detail: "Apple TV" })
    expect(row({ channel_name: "CBS", device_name: "Unknown device" })).toEqual({ title: "CBS", detail: "CBS" })
  })

  it("turns a recording message into the show and what happened to it", () => {
    expect(recordingEventParts("Recording(1): Show")).toEqual({ word: "Recording", subject: "Show" })
    expect(recordingEventParts("Completed: Sleeping on Trains on HBO for Fri", "HBO")?.subject).toBe("Sleeping on Trains")
    expect(row({ type: "recording_event", title: "Recording Event", message: "Failed: Show on HBO", channel_name: "HBO" })).toEqual({ title: "Show", detail: "Failed" })
  })

  it("writes disk alerts with the figures of the moment, and keeps old rows' words", () => {
    expect(formatDiskAlertMessage({ free: "121 GB", total: "1.82 TB" }, "raw")).toBe("121 GB left of 1.82 TB at the time")
    expect(formatDiskAlertMessage({}, "raw")).toBe("raw")
    expect(row({ type: "disk_alert", title: "x", message: "raw", extra: { free: "46.10 GB", total: "1.82 TB" } })).toEqual({ title: "Display", detail: "46.1 GB left of 1.82 TB" })
    expect(formatDiskAlertMessage({ free: "121.34 GB", total: "1.82 TB" }, "raw")).toBe("121 GB left of 1.82 TB at the time")
  })

  it("falls back to the type label", () => {
    expect(activityRowText({ type: "other", title: "", message: "" }, "", "Type").title).toBe("Type")
  })
})

describe("recording rows", () => {
  it("knows what is recording right now", () => {
    expect(isRecordingActive({ start_time: NOW / 1000 - 60, end_time: NOW / 1000 + 60 }, NOW)).toBe(true)
    expect(isRecordingActive({ start_time: NOW / 1000 - 120, end_time: NOW / 1000 - 60 }, NOW)).toBe(false)
    expect(isRecordingActive({ start_time: NOW / 1000 + 60 }, NOW)).toBe(false)
    expect(isRecordingActive({ start_time: 0 }, NOW)).toBe(false)
  })

  it("says the time is unknown instead of 1969", () => {
    expect(recordingMeta({ channel: "CBS", start_time: 0 })).toBe("CBS · Time unknown")
    expect(recordingMeta({ channel: "", start_time: Number.NaN })).toBe("Time unknown")
  })
})

describe("Now", () => {
  it("is one dim line when nothing is on", () => {
    const html = renderToStaticMarkup(<NowList watching={[]} recording={[]} loading={false} hasError={false} />)
    expect(html).toContain("Nothing playing")
    expect(html).toContain("data-empty")
    expect(html).not.toContain("rounded-group")
  })

  it("does not claim nothing is on when it could not tell", () => {
    const html = renderToStaticMarkup(<NowList watching={[]} recording={[]} loading={false} hasError />)
    expect(html).not.toContain("Nothing playing")
    expect(html).toContain("Can&#x27;t tell")
  })

  it("marks live and recording rows without inventing time left", () => {
    const html = renderToStaticMarkup(<NowList watching={[{ device: "Apple TV", channel: "CBS", image: "" }]} recording={[{ title: "News", until: "8:00 PM" }]} loading={false} hasError={false} />)
    expect(html).toContain("CBS")
    expect(html).toContain("Apple TV")
    expect(html).toContain("Live")
    expect(html).toContain("until 8:00 PM")
    expect(html).toContain("size-2 rounded-full bg-foreground")
  })
})

describe("unknown is never red or zero", () => {
  const Icon = () => <svg />
  const tile = (over: Record<string, unknown>) => renderToStaticMarkup(<MetricCard title="T" icon={Icon as never} value={null} subtitle="S" loading={false} hasError={false} {...over} />)

  it("shows a dash with a reason, not zero", () => {
    const html = tile({ hasError: true })
    expect(html).toContain("–")
    expect(html).toContain("Could not load this. Retrying.")
    expect(html).not.toContain("danger")
  })

  it("keeps a known value muted with when it was true", () => {
    const html = tile({ hasError: true, value: 3, asOf: "5:26 AM" })
    expect(html).toContain(">3<")
    expect(html).toContain("As of 5:26 AM")
    expect(html).toContain("text-muted-foreground")
  })
})

describe("delivery errors", () => {
  it.each([
    ["Connection timed out", "did not answer in time"],
    ["HTTP 401 Unauthorized", "rejected the credentials"],
    ["404 Not Found", "could not find that address"],
    ["429 Too Many Requests", "limiting requests"],
    ["getaddrinfo ENOTFOUND", "could not reach"],
    ["HTTP 503", "on its side"],
    ["???", "reported a problem"],
    ["Circuit open \u2014 delivery skipped", "paused this service for 5 minutes"],
  ])("%s is told plainly", (raw, phrase) => {
    expect(describeDeliveryError(raw)).toContain(phrase)
  })
})

describe("phone basics", () => {
  const css = read("app/globals.css")
  it("keeps fields at 16px on touch and stops double-tap delay on controls", () => {
    expect(css).toContain("@media (pointer: coarse)")
    expect(css).toContain("font-size: max(16px, 1rem)")
    expect(css).toContain("touch-action: manipulation")
    expect(css).toContain("-webkit-touch-callout: none")
  })

  it("gives press feedback the moment the finger lands", () => {
    expect(css).toMatch(/\.press-row:active \{[^}]*transition-duration: 0ms/)
  })

  it("wires keyboards and the status bar", () => {
    const dashboard = read("components/dashboard.tsx")
    expect(dashboard).toContain('autoComplete="current-password"')
    expect(dashboard).toContain('autoComplete="new-password"')
    expect(dashboard).toContain('enterKeyHint="go"')
    expect(read("components/theme-provider.tsx")).toContain("ThemeColorSync")
    expect(read("components/settings/general-settings-section.tsx")).toContain('inputMode="url"')
  })

  it("never restarts from a native confirm", () => {
    expect(read("components/header.tsx")).not.toContain("confirm(t(")
  })
})
