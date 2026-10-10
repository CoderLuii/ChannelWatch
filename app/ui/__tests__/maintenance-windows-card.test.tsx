import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  formatMaintenanceRange,
  MaintenanceWindowResults,
} from "@/components/dashboard/maintenance-windows-card"
import type { MaintenanceWindowsResponse } from "@/lib/types"
import { fetchMaintenanceWindows } from "@/lib/api"

const response: MaintenanceWindowsResponse = {
  timezone: "America/New_York",
  minimum_minutes: 60,
  days: 7,
  start_hour: 0,
  end_hour: 24,
  weekdays: [],
  dvrs: [
    {
      dvr_id: "dvr-a",
      dvr_name: "Living Room",
      status: "truncated",
      coverage_end: "2026-10-11T04:00:00+00:00",
      message: "Later time was not assessed because the known schedule ends before the requested range.",
      windows: [
        {
          start: "2026-10-10T05:00:00+00:00",
          end: "2026-10-10T07:30:00+00:00",
          duration_minutes: 150,
        },
      ],
    },
    {
      dvr_id: "dvr-b",
      dvr_name: "Bedroom",
      status: "offline",
      coverage_end: null,
      message: "The DVR schedule could not be reached, so no time was marked free.",
      windows: [],
    },
  ],
}

describe("MaintenanceWindowResults", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("renders per-DVR windows and keeps incomplete schedule warnings visible", () => {
    const html = renderToStaticMarkup(<MaintenanceWindowResults data={response} />)

    expect(html).toContain("Living Room")
    expect(html).toContain("Bedroom")
    expect(html).toContain("2h 30m")
    expect(html).toContain("Known schedule only")
    expect(html).toContain("DVR unavailable")
    expect(html).toContain("Later time was not assessed")
    expect(html).toContain("no time was marked free")
  })

  it("formats window dates in the ChannelWatch timezone", () => {
    expect(formatMaintenanceRange(response.dvrs[0].windows[0], response.timezone)).toContain("1:00 AM")
  })

  it("falls back safely if formatting receives an invalid timezone or date", () => {
    expect(formatMaintenanceRange(response.dvrs[0].windows[0], "Invalid/Zone")).toBe("Time unavailable")
    expect(formatMaintenanceRange({ ...response.dvrs[0].windows[0], start: "not-a-date" }, response.timezone)).toBe("Time unavailable")
  })

  it("rejects malformed API responses before the card tries to render them", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    }))

    await expect(fetchMaintenanceWindows()).rejects.toThrow("Invalid maintenance windows response")
  })

  it.each([
    ["invalid timezone", { ...response, timezone: "Invalid/Zone" }],
    [
      "invalid window date",
      {
        ...response,
        dvrs: response.dvrs.map((dvr, index) => index === 0
          ? { ...dvr, windows: [{ ...dvr.windows[0], start: "not-a-date" }] }
          : dvr),
      },
    ],
  ])("rejects a type-correct response with an %s", async (_name, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => payload,
    }))

    await expect(fetchMaintenanceWindows()).rejects.toThrow("Invalid maintenance windows response")
  })
})
