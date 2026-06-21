import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"
import { readFileSync } from "node:fs"
import { resolve, dirname } from "node:path"
import { fileURLToPath } from "node:url"

import { fetchWatchHistoryForDvrSelection } from "@/components/watch-history"
import { DvrSelectionContext } from "@/lib/dvr-selection-context"
import type { DvrSelectionContextValue } from "@/lib/dvr-selection-context"
import type { DVRServer } from "@/lib/types"

const __dirname = dirname(fileURLToPath(import.meta.url))
function srcFile(relPath: string): string {
  return readFileSync(resolve(__dirname, relPath), "utf8")
}

const mockDvrs: DVRServer[] = [
  { id: "dvr_abc123", name: "Living Room", host: "192.168.1.10", port: 8089, enabled: true },
  { id: "dvr_def456", name: "Bedroom", host: "192.168.1.11", port: 8089, enabled: true },
]

afterEach(() => {
  vi.restoreAllMocks()
  Reflect.deleteProperty(globalThis, "fetch")
})

function makeCtx(overrides: Partial<DvrSelectionContextValue> = {}): DvrSelectionContextValue {
  return {
    selectedDvr: "all",
    setSelectedDvr: () => {},
    availableDvrs: mockDvrs,
    ...overrides,
  }
}

function TestConsumer() {
  const ctx = React.useContext(DvrSelectionContext)
  return React.createElement("div", {
    "data-dvr": ctx.selectedDvr,
    "data-count": String(ctx.availableDvrs.length),
    "data-first": ctx.availableDvrs[0]?.id ?? "",
  })
}

describe("DvrSelectionContext", () => {
  it("provides default 'all' when no provider is present", () => {
    const html = renderToStaticMarkup(React.createElement(TestConsumer))
    expect(html).toContain('data-dvr="all"')
    expect(html).toContain('data-count="0"')
  })

  it("passes selectedDvr down to consumers", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        DvrSelectionContext.Provider,
        { value: makeCtx({ selectedDvr: "dvr_abc123" }) },
        React.createElement(TestConsumer),
      ),
    )
    expect(html).toContain('data-dvr="dvr_abc123"')
  })

  it("exposes availableDvrs list to consumers", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        DvrSelectionContext.Provider,
        { value: makeCtx() },
        React.createElement(TestConsumer),
      ),
    )
    expect(html).toContain('data-count="2"')
    expect(html).toContain('data-first="dvr_abc123"')
  })
})

describe("DVR selection fallback logic", () => {
  function resolveSelection(saved: string | null, available: DVRServer[]): string {
    if (!saved || saved === "all") return "all"
    return available.some((d) => d.id === saved) ? saved : "all"
  }

  it("returns 'all' when nothing is saved", () => {
    expect(resolveSelection(null, mockDvrs)).toBe("all")
  })

  it("returns saved selection when DVR still exists", () => {
    expect(resolveSelection("dvr_abc123", mockDvrs)).toBe("dvr_abc123")
  })

  it("falls back to 'all' when saved DVR no longer exists", () => {
    expect(resolveSelection("dvr_old_99", mockDvrs)).toBe("all")
  })

  it("returns 'all' when saved value is explicitly 'all'", () => {
    expect(resolveSelection("all", mockDvrs)).toBe("all")
  })

  it("falls back to 'all' when available list is empty", () => {
    expect(resolveSelection("dvr_abc123", [])).toBe("all")
  })
})

describe("localStorage key contract", () => {
  it("uses the expected key name", () => {
    const src = srcFile("../lib/dvr-selection-context.tsx")
    expect(src).toContain('"cw.selected_dvr"')
  })
})

describe("header DVR switcher source structure", () => {
  it("imports useDvrSelection and renders All DVRs option", () => {
    const src = srcFile("../components/header.tsx")
    expect(src).toContain("useDvrSelection")
    expect(src).toContain("header.allDvrs")
  })

  it("scopes activity history by dvr_id in WatchHistory", () => {
    const src = srcFile("../components/watch-history.tsx")
    expect(src).toContain("dvr_id")
    expect(src).toContain("selectedDvr")
  })
})

describe("WatchHistory DVR request routing", () => {
  function mockHistoryFetch() {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [], total: 0, offset: 0, limit: 25 }),
    })
    vi.stubGlobal("fetch", fetchMock)
    return fetchMock
  }

  it("uses aggregate activity history when All DVRs is selected", async () => {
    const fetchMock = mockHistoryFetch()

    await fetchWatchHistoryForDvrSelection("all", {
      offset: 0,
      limit: 25,
      type: "all",
      search: "",
      sort: "desc",
    })

    expect(fetchMock).toHaveBeenCalledWith("/api/activity-history?offset=0&limit=25&sort=desc", { headers: {} })
  })

  it("uses the filtered v1 DVR activity endpoint when a DVR is selected", async () => {
    const fetchMock = mockHistoryFetch()

    await fetchWatchHistoryForDvrSelection("dvr_abc123", {
      offset: 25,
      limit: 25,
      type: "channel",
      search: "living room",
      sort: "asc",
    })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/dvrs/dvr_abc123/activity-history?offset=25&limit=25&type=channel&search=living+room&sort=asc",
      { headers: {} },
    )
  })
})
