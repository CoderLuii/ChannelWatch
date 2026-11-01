import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { DiskSpaceCard, diskFreeFloorGb, diskSeverityColors, diskTickPosition, resolveDiskSeverity, type DiskSpaceState } from "@/components/dashboard/disk-space-card"
import { formatDiskSizeFromGB } from "@/lib/utils"

const baseDiskSpace: DiskSpaceState = {
  usedPercent: 20,
  freePercent: 80,
  loading: false,
  error: null,
  totalFormatted: "1.00 TB",
  usedFormatted: "204.8 GB",
  freeFormatted: "819.2 GB",
  libraryShows: 0,
  libraryMovies: 0,
  libraryEpisodes: 0,
}

describe("formatDiskSizeFromGB", () => {
  it("shows one decimal below 100 GB and none from 100 GB up to 1024 GB", () => {
    expect(formatDiskSizeFromGB(45.26)).toBe("45.3 GB")
    expect(formatDiskSizeFromGB(99.96)).toBe("100 GB")
    expect(formatDiskSizeFromGB(819.2)).toBe("819 GB")
  })

  it("formats dashboard values at or above 1024 GB in TB", () => {
    expect(formatDiskSizeFromGB(11202.56)).toBe("10.94 TB")
    expect(formatDiskSizeFromGB(1024)).toBe("1.00 TB")
  })
})

describe("disk meter", () => {
  it("takes the larger of the percent and GB floor, base 1024", () => {
    expect(diskFreeFloorGb(1863.68, 10, 50)).toBeCloseTo(186.37, 1)
    expect(diskFreeFloorGb(100, 10, 50)).toBe(50)
    expect(diskFreeFloorGb(100)).toBe(0)
  })

  it("places a tick where the used fill reaches the floor", () => {
    expect(diskTickPosition(1000, 100)).toBe(90)
    expect(diskTickPosition(0, 100)).toBe(0)
    expect(diskTickPosition(100, 500)).toBe(0)
  })

  it("draws both ticks and says the same thing to a screen reader", () => {
    const html = renderToStaticMarkup(
      React.createElement(DiskSpaceCard, {
        diskSpace: { ...baseDiskSpace, freeFormatted: "116 GB", totalGb: 1863.68, freeGb: 116, usedPercent: 94, freePercent: 6 },
        loading: false,
        hasError: false,
        serverSeverity: "warning",
        warningThresholdPercent: 10,
        criticalThresholdPercent: 5,
      }),
    )
    expect(html).toContain('data-testid="disk-tick-warning"')
    expect(html).toContain('data-testid="disk-tick-critical"')
    expect(html).toContain('aria-valuetext="116 GB left · warns at 186 GB"')
    expect(html).toContain("116 GB left · warns at 186 GB")
  })

  it("keeps the free value in the text color at every level, so only the bar and word carry status", () => {
    for (const serverSeverity of ["normal", "warning", "critical"] as const) {
      const html = renderToStaticMarkup(
        React.createElement(DiskSpaceCard, { diskSpace: baseDiskSpace, loading: false, hasError: false, serverSeverity }),
      )
      expect(html).toContain(diskSeverityColors[serverSeverity].bar)
      expect(html).toMatch(/text-title[^"]*text-foreground/)
    }
  })

  it("uses the same severity fallbacks as the backend defaults", () => {
    expect(resolveDiskSeverity({ usedPercent: 96 })).toBe("critical")
    expect(resolveDiskSeverity({ usedPercent: 92 })).toBe("warning")
    expect(resolveDiskSeverity({ usedPercent: 85 })).toBe("normal")
  })
})

describe("DiskSpaceCard server severity", () => {
  it("uses the same warning and critical threshold resolution as the dashboard summary", () => {
    expect(resolveDiskSeverity({
      usedPercent: 85,
      warningThresholdPercent: 20,
      criticalThresholdPercent: 10,
    })).toBe("warning")
    expect(resolveDiskSeverity({
      usedPercent: 95,
      warningThresholdPercent: 20,
      criticalThresholdPercent: 10,
    })).toBe("critical")
    expect(resolveDiskSeverity({
      usedPercent: 20,
      serverSeverity: "warning",
      warningThresholdPercent: 20,
      criticalThresholdPercent: 10,
    })).toBe("warning")
  })

  it("renders TB free values without appending a hardcoded GB unit", () => {
    const html = renderToStaticMarkup(
      React.createElement(DiskSpaceCard, {
        diskSpace: {
          ...baseDiskSpace,
          totalFormatted: "18.03 TB",
          usedFormatted: "7.09 TB",
          freeFormatted: "10.94 TB",
        },
        loading: false,
        hasError: false,
      }),
    )

    expect(html).toContain(">10.94<")
    expect(html).toContain("> TB<")
    expect(html).not.toContain("TB GB")
  })

  it("renders backend warning severity even when local percentage is normal", () => {
    const html = renderToStaticMarkup(
      React.createElement(DiskSpaceCard, {
        diskSpace: baseDiskSpace,
        loading: false,
        hasError: false,
        serverSeverity: "warning",
      }),
    )

    expect(html).toContain("text-warning-fg")
    expect(html).toMatch(/\bbg-warning(?![\w/-])/)
  })

  it("renders backend critical severity for selected-DVR system-info responses", () => {
    const html = renderToStaticMarkup(
      React.createElement(DiskSpaceCard, {
        diskSpace: baseDiskSpace,
        loading: false,
        hasError: false,
        serverSeverity: "critical",
      }),
    )

    expect(html).toContain("text-danger-fg")
    expect(html).toMatch(/\bbg-danger(?![\w/-])/)
  })
})
