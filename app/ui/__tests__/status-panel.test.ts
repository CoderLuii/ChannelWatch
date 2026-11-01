import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

import { StatusPanel } from "@/components/dashboard/status-panel"
import type { AppSettings } from "@/lib/types"


const baseDvr = {
  id: "dvr_aaa11111",
  name: "Living Room",
  host: "192.168.1.10",
  port: 8089,
  connected: true,
  version: "2026.01.01",
  version_compatible: true,
  version_warning: null,
  disk_usage_percent: 10,
  disk_total_gb: 500,
  disk_free_gb: 450,
  active_streams: 0,
  library_shows: 10,
  library_movies: 4,
  library_episodes: 20,
}

const baseSettings = {
  dvr_servers: [{ id: "dvr_aaa11111", name: "Living Room", host: "192.168.1.10", port: 8089, enabled: true }],
} as unknown as AppSettings

const baseProps = {
  activeNotificationServices: 1,
  activeProviderNames: ["Discord"],
  activeAlertTypes: ["Live TV"],
  coreProcessStatus: "Running",
  channelwatchVersion: "0.9.3",
  currentSettings: baseSettings,
  onNavigate: vi.fn(),
}

describe("StatusPanel monitoring", () => {
  it("leaves monitoring problems to the verdict line, so they are said once", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [
          {
            ...baseDvr,
            monitoring_status: "stale",
            monitoring_ready: false,
            monitoring_reason: "No freshness update for 601s",
            freshness_status: "stale",
          },
        ],
      }),
    )

    expect(html).not.toContain("Stale monitoring on Living Room")
    expect(html).not.toContain("Diagnose")
    expect(html).toMatch(/\bbg-warning(?![\w/-])/)
  })
})

describe("StatusPanel DVR health dots", () => {
  it("renders green health dot when DVR is connected and monitoring is healthy", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [{ ...baseDvr, monitoring_ready: true }],
      }),
    )
    expect(html).toMatch(/\bbg-success(?![\w/-])/)
  })

  it("renders amber health dot when monitoring is stale", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [
          { ...baseDvr, monitoring_ready: false, monitoring_status: "stale" },
        ],
      }),
    )
    expect(html).toMatch(/\bbg-warning(?![\w/-])/)
  })

  it("renders red health dot when monitoring is dead", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [
          { ...baseDvr, monitoring_ready: false, monitoring_status: "dead" },
        ],
      }),
    )
    expect(html).toMatch(/\bbg-danger(?![\w/-])/)
  })

  it("renders red health dot when DVR is not connected", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [{ ...baseDvr, connected: false }],
      }),
    )
    expect(html).toMatch(/\bbg-danger(?![\w/-])/)
  })

  it("renders gray health dot when no DVR status is available", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [],
      }),
    )
    expect(html).toMatch(/\bbg-neutral(?![\w/-])/)
  })
})

describe("StatusPanel core status badge", () => {
  it.each(["Unknown", "Loading", "SomethingElse"])("renders %s as a tonal neutral badge with readable text", (coreProcessStatus) => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, { ...baseProps, coreProcessStatus, dvrStatusList: [baseDvr] }),
    )
    expect(html).toContain("bg-neutral-bg")
    expect(html).toContain("text-neutral-fg")
    expect(html).not.toMatch(/\bbg-neutral(?:\/\d+)? [^"]*text-white/)
  })
})

describe("StatusPanel unknown DVR", () => {
  it("says Loading during the first read and Unknown after, never Not connected", () => {
    const loading = renderToStaticMarkup(React.createElement(StatusPanel, { ...baseProps, dvrStatusList: [], dataLoaded: false }))
    expect(loading).toContain("Loading")
    expect(loading).not.toContain("Not connected")
    const unknown = renderToStaticMarkup(React.createElement(StatusPanel, { ...baseProps, dvrStatusList: [] }))
    expect(unknown).toContain("Unknown")
    expect(unknown).not.toContain("bg-danger")
  })

  it("only a DVR reported disconnected reads as danger", () => {
    const html = renderToStaticMarkup(React.createElement(StatusPanel, { ...baseProps, dvrStatusList: [{ ...baseDvr, connected: false }] }))
    expect(html).toContain("Not connected")
    expect(html).toContain("bg-danger")
  })
})

describe("StatusPanel selectedDvr filtering", () => {
  const twoServerSettings = {
    dvr_servers: [
      { id: "dvr_aaa11111", name: "Living Room", host: "192.168.1.10", port: 8089, enabled: true },
      { id: "dvr_bbb22222", name: "Bedroom", host: "192.168.1.11", port: 8089, enabled: true },
    ],
  } as unknown as AppSettings

  it("shows all DVR rows when selectedDvr is 'all'", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        currentSettings: twoServerSettings,
        dvrStatusList: [],
        selectedDvr: "all",
      }),
    )
    expect(html).toContain("Living Room")
    expect(html).toContain("Bedroom")
  })

  it("shows only the selected DVR row when a specific DVR is selected", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        currentSettings: twoServerSettings,
        dvrStatusList: [],
        selectedDvr: "dvr_aaa11111",
      }),
    )
    expect(html).toContain("Living Room")
    expect(html).not.toContain("Bedroom")
  })

  it("shows all DVR rows when selectedDvr is undefined (default)", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        currentSettings: twoServerSettings,
        dvrStatusList: [],
      }),
    )
    expect(html).toContain("Living Room")
    expect(html).toContain("Bedroom")
  })
})
