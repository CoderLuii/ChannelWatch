import React from "react"
import { readFileSync } from "node:fs"
import path from "node:path"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

import { Tabs } from "@/components/base/tabs"
import { HealthVerdictLine } from "@/components/dashboard/health-verdict-line"
import { MobileDashboardDetails } from "@/components/dashboard/mobile-dashboard-details"
import { RoutingSettingsSection } from "@/components/settings/routing-settings-section"
import { Sidebar, mobileMoreNavigation } from "@/components/sidebar"
import { buildHealthVerdict } from "@/lib/health-verdict"

const read = (relative: string) => readFileSync(path.resolve(__dirname, "..", relative), "utf8")

const connected = { connected: true, enabled: true }
const offline = { connected: false, enabled: true }

describe("overview health verdict", () => {
  it("leads with an all-clear line that names the DVR count", () => {
    const verdict = buildHealthVerdict({ loaded: true, coreStatus: "Running", dvrs: [connected], diskSeverity: "normal", hasFailedFetches: false })
    expect(verdict.tone).toBe("healthy")
    expect([verdict.headline, ...verdict.details].join(" · ")).toBe("Monitoring healthy · 1 DVR connected")
  })

  it("reports the most serious problem instead of claiming health", () => {
    expect(buildHealthVerdict({ loaded: true, coreStatus: "Stopped", dvrs: [connected], hasFailedFetches: false }).headline).toBe("Monitoring engine is not running")
    expect(buildHealthVerdict({ loaded: true, coreStatus: "Running", dvrs: [connected, offline], hasFailedFetches: false })).toMatchObject({ tone: "warning", headline: "1 of 2 DVRs not connected" })
    expect(buildHealthVerdict({ loaded: true, coreStatus: "Running", dvrs: [], hasFailedFetches: false })).toMatchObject({ tone: "danger", headline: "No DVR connected" })
    expect(buildHealthVerdict({ loaded: true, coreStatus: "Running", dvrs: [connected], diskSeverity: "critical", hasFailedFetches: false }).tone).toBe("danger")
    expect(buildHealthVerdict({ loaded: true, coreStatus: "Running", dvrs: [connected], hasFailedFetches: true }).headline).toBe("Some dashboard data could not be loaded")
  })

  it("ignores disabled DVRs and stays pending until data loads", () => {
    expect(buildHealthVerdict({ loaded: true, coreStatus: "Running", dvrs: [connected, { connected: false, enabled: false }], hasFailedFetches: false }).tone).toBe("healthy")
    expect(buildHealthVerdict({ loaded: false, coreStatus: "Loading", dvrs: [], hasFailedFetches: false }).tone).toBe("pending")
  })

  it("renders as a polite status line", () => {
    const html = renderToStaticMarkup(<HealthVerdictLine verdict={{ tone: "healthy", headline: "Monitoring healthy", details: ["1 DVR connected"], more: 2, action: { kind: "disk", label: "See disk" } }} onAction={vi.fn()} />)
    expect(html).toContain('role="status"')
    expect(html).toContain('data-tone="healthy"')
    expect(html).toContain("Monitoring healthy")
    expect(html).toContain("+2 more")
    expect(html).toContain("See disk")
    expect(html).toContain("min-h-11")
  })
})

describe("mobile tab bar", () => {
  it("keeps tab labels on one line at any text size", () => {
    const html = renderToStaticMarkup(<Sidebar activeView="overview" setActiveView={vi.fn()} isMobile />)
    expect(html).toContain("whitespace-nowrap")
    expect(html).not.toContain("overflow-wrap:anywhere")
    for (const label of ["Home", "History", "Delivery", "Settings", "More"]) expect(html).toContain(`>${label}</span>`)
    expect(html).toContain('aria-haspopup="dialog"')
  })

  it("does not render the desktop drawer on phones", () => {
    const html = renderToStaticMarkup(<Sidebar activeView="overview" setActiveView={vi.fn()} isMobile />)
    expect(html).not.toContain("<aside")
  })

  it("lists the secondary destinations in the More sheet", () => {
    expect(mobileMoreNavigation.map((item) => item.view)).toEqual(["diagnostics", "help-feedback", "about"])
  })

  it("keeps the bar opaque with a hairline, so nothing ghosts through it", () => {
    const html = renderToStaticMarkup(<Sidebar activeView="overview" setActiveView={vi.fn()} isMobile />)
    expect(html).toContain("bg-background")
    expect(html).toContain("border-hairline-strong")
    expect(html).not.toContain("backdrop-blur")
  })
})

describe("overview KPI tiles", () => {
  it("renders disk and uptime as tiles that open a sheet", () => {
    const Icon = () => <svg />
    const html = renderToStaticMarkup(
      <MobileDashboardDetails id="disk" title="Disk Space" summary="Free of 2 TB" tile={{ label: "Disk Space", value: "121.4 GB", icon: Icon as never }}>
        <div>details</div>
      </MobileDashboardDetails>,
    )
    expect(html).toContain('data-testid="disk-tile"')
    expect(html).toContain("121.4 GB")
    expect(html).toContain('aria-haspopup="dialog"')
    expect(html).toContain('aria-expanded="false"')
    expect(html).toContain('aria-label="Disk Space: 121.4 GB"')
    expect(html).not.toContain("col-span-full")
  })

  it("holds the tile's shape with a skeleton while loading", () => {
    const Icon = () => <svg />
    const html = renderToStaticMarkup(
      <MobileDashboardDetails id="disk" title="Disk Space" summary="" tile={{ label: "Disk left", value: "0 GB", icon: Icon as never, loading: true }}>
        <div>details</div>
      </MobileDashboardDetails>,
    )
    expect(html).toContain("animate-pulse")
    expect(html).toContain("h-7 w-16")
    expect(html).toContain("h-5")
    expect(html).not.toContain("0 GB<")
  })

  it("uses a reflowing grid so tiles sit 2x2 on phones and stack at large text", () => {
    const src = read("components/status-overview.tsx")
    expect(src).toContain("grid-cols-[repeat(auto-fit,minmax(min(100%,9.5rem),1fr))]")
    expect(src).toContain("<HealthVerdictLine")
  })
})

describe("phone tiles", () => {
  it("keeps all four tiles on phones", () => {
    const src = read("components/status-overview.tsx")
    expect(src).not.toContain("max-md:hidden")
    expect(src).toContain("<NowList")
    expect(src).toContain("md:grid-cols-2 lg:grid-cols-4")
    expect(read("components/dashboard/home-skeleton.tsx")).not.toContain("max-md:hidden")
  })
})

describe("number formats", () => {
  it("shows tile uptime without seconds", async () => {
    const { formatUptimeTile, formatCount } = await import("@/lib/utils")
    expect(formatUptimeTile({ days: 2, hours: 23, minutes: 59 })).toBe("2d 23h")
    expect(formatUptimeTile({ days: 0, hours: 23, minutes: 59 })).toBe("23h 59m")
    expect(formatUptimeTile({ days: 0, hours: 0, minutes: 35 })).toBe("35m")
    expect(formatCount(12345)).toBe("12,345")
  })

  it("steps units down without changing the text", async () => {
    const { Figure } = await import("@/components/base/figure")
    const html = renderToStaticMarkup(<Figure value="116 GB" />)
    expect(html).toContain("tabular-nums")
    expect(html.replace(/<[^>]+>/g, "")).toBe("116 GB")
    expect(renderToStaticMarkup(<Figure value="2d 23h" />).replace(/<[^>]+>/g, "")).toBe("2d 23h")
  })
})

describe("routing matrix on phones", () => {
  it("renders stacked cards with full-row switch targets", () => {
    const values: Record<string, unknown> = {
      dvr_servers: [{ id: "dvr_a", name: "Living Room", host: "10.0.0.2", port: 8089, enabled: true }],
      notification_routing: {},
      webhooks: [],
      apprise_discord: "discord://token",
    }
    const form = {
      watch: (name?: string) => (name ? values[name] : values),
      setValue: vi.fn(),
    } as never
    const html = renderToStaticMarkup(
      <Tabs value="routing">
        <RoutingSettingsSection form={form} />
      </Tabs>,
    )
    expect(html).toContain('data-testid="routing-mobile-list"')
    expect(html).toContain("min-h-[48px]")
    expect(html).toContain('aria-label="Discord, VOD, Living Room"')
  })
})

describe("phone layout source contracts", () => {
  it("shows the settings save bar only with unsaved changes or a save result", () => {
    const src = read("components/settings-form.tsx")
    expect(src).toContain("hasLoadedSettings && (isDirty || isSubmitting || justSaved)")
    expect(src).toContain("{saveBarVisible ? (")
    expect(src).not.toContain("animate-slide-")
  })

  it("keeps the report dialog footer outside the scroll area with safe-area padding", () => {
    const src = read("components/report-problem-dialog.tsx")
    const scrollEnd = src.indexOf('data-testid="report-problem-footer"')
    expect(scrollEnd).toBeGreaterThan(0)
    expect(src).toContain("pb-[max(16px,env(safe-area-inset-bottom))]")
    expect(src).toContain('className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"')
  })

  it("gives the notification log date filters equal label columns", () => {
    const src = read("components/notification-log.tsx")
    expect(src).toContain("basis-[3rem]")
    expect(src).toContain("date-input")
  })

  it("keeps the DVR selector on a single truncated line", () => {
    const src = read("components/header.tsx")
    expect(src).toContain('data-testid="header-dvr-select"')
    expect(src).toMatch(/overflow-hidden text-ellipsis whitespace-nowrap/)
  })
})
