import { describe, expect, it } from "vitest"

import { formatAbsoluteTime, formatRelativeTime } from "@/lib/relative-time"

const NOW = Date.parse("2026-10-29T12:00:00Z")

describe("formatRelativeTime", () => {
  it("formats future and past timestamps in plain language", () => {
    expect(formatRelativeTime("2026-10-29T15:00:00Z", NOW, "en")).toBe("in 3 hours")
    expect(formatRelativeTime("2026-10-27T12:00:00Z", NOW, "en")).toBe("2 days ago")
    expect(formatRelativeTime("2026-11-05T12:00:00Z", NOW, "en")).toBe("next week")
  })

  it("treats sub-minute differences as now", () => {
    expect(formatRelativeTime("2026-10-29T12:00:20Z", NOW, "en")).toBe("this minute")
  })

  it("never renders Invalid Date for bad input", () => {
    expect(formatRelativeTime("not-a-date", NOW, "en")).toBe("not-a-date")
    expect(formatRelativeTime(null, NOW, "en")).toBe("")
    expect(formatAbsoluteTime("not-a-date", "en")).toBe("not-a-date")
  })
})
