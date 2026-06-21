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
  activeAlertTypes: ["Channel Watching"],
  coreProcessStatus: "Running",
  channelwatchVersion: "0.9.1",
  currentSettings: baseSettings,
  onNavigate: vi.fn(),
}

describe("StatusPanel monitoring banner", () => {
  it("renders a diagnose banner when DVR monitoring is stale", () => {
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

    expect(html).toContain("Monitoring degraded")
    expect(html).toContain("Stale monitoring on Living Room")
    expect(html).toContain("Diagnose")
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
    expect(html).toContain("bg-green-500")
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
    expect(html).toContain("bg-amber-500")
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
    expect(html).toContain("bg-red-500")
  })

  it("renders red health dot when DVR is not connected", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [{ ...baseDvr, connected: false }],
      }),
    )
    expect(html).toContain("bg-red-500")
  })

  it("renders gray health dot when no DVR status is available", () => {
    const html = renderToStaticMarkup(
      React.createElement(StatusPanel, {
        ...baseProps,
        dvrStatusList: [],
      }),
    )
    expect(html).toContain("bg-gray-400")
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
