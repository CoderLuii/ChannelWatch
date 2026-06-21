import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { DiskSpaceCard, type DiskSpaceState } from "@/components/dashboard/disk-space-card"

const baseDiskSpace: DiskSpaceState = {
  usedPercent: 20,
  freePercent: 80,
  loading: false,
  error: null,
  totalTB: "1.00",
  usedTB: "0.20",
  freeGB: "819.2",
  libraryShows: 0,
  libraryMovies: 0,
  libraryEpisodes: 0,
}

describe("DiskSpaceCard server severity", () => {
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
