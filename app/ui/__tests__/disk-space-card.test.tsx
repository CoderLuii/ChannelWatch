import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { DiskSpaceCard, type DiskSpaceState } from "@/components/dashboard/disk-space-card"
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
  it("keeps dashboard values below 1024 GB in GB", () => {
    expect(formatDiskSizeFromGB(819.2)).toBe("819.2 GB")
  })

  it("formats dashboard values at or above 1024 GB in TB", () => {
    expect(formatDiskSizeFromGB(11202.56)).toBe("10.94 TB")
    expect(formatDiskSizeFromGB(1024)).toBe("1.00 TB")
  })
})

describe("DiskSpaceCard server severity", () => {
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

    expect(html).toContain("10.94 TB Free")
    expect(html).not.toContain("10.94 TB GB Free")
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

    expect(html).toContain("from-amber-50")
    expect(html).toContain("bg-amber-600")
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

    expect(html).toContain("from-red-50")
    expect(html).toContain("bg-red-600")
  })
})
