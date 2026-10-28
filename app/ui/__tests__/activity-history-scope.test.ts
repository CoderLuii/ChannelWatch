import { afterEach, describe, expect, it, vi } from "vitest"
import {
  downloadActivityHistoryCsv,
  fetchActivityClientFilters,
  fetchActivityHistory,
  fetchDvrActivityHistory,
} from "@/lib/api"

afterEach(() => vi.unstubAllGlobals())

describe("activity scope", () => {
  const requests = [
    ["aggregate history", () => fetchActivityHistory({ scope: "watch", client: "Living Room", search: "News" })],
    ["selected DVR history", () => fetchDvrActivityHistory("dvr-main", { scope: "watch" })],
    ["CSV export", () => downloadActivityHistoryCsv({ scope: "watch", dvr_id: "dvr-main" })],
    ["client facets", () => fetchActivityClientFilters({ scope: "watch", hours: 0 })],
  ] as const

  it.each(requests)("sends watch scope for %s", async (_label, request) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], clients: [] })))
    vi.stubGlobal("fetch", fetchMock)
    await request()
    const url = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost")
    expect(url.searchParams.get("scope")).toBe("watch")
  })

  it("keeps generic history, Recent Activity facets and exports unscoped by default", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ items: [], clients: [] }))))
    vi.stubGlobal("fetch", fetchMock)
    await fetchActivityHistory()
    await fetchDvrActivityHistory("dvr-main")
    await fetchActivityClientFilters()
    await downloadActivityHistoryCsv()
    for (const [url] of fetchMock.mock.calls) {
      expect(new URL(String(url), "http://localhost").searchParams.has("scope")).toBe(false)
    }
  })
})
