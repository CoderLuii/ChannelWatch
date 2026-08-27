import { afterEach, describe, expect, it, vi } from "vitest"

import { fetchCompleteActivityHistory } from "@/lib/api"

const response = (items: Array<{ id: string; timestamp: string }>, total: number, offset: number) => ({
  ok: true,
  json: async () => ({
    items: items.map((item) => ({
      ...item,
      type: "watching_channel",
      title: item.id,
      message: item.id,
      icon: "activity",
    })),
    total,
    offset,
    limit: 100,
  }),
})

describe("fetchCompleteActivityHistory", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("paginates the complete 24-hour history without the old 250-event truncation", async () => {
    const fetchMock = vi.fn()
    for (let page = 0; page < 3; page += 1) {
      const size = page < 2 ? 100 : 61
      fetchMock.mockResolvedValueOnce(response(
        Array.from({ length: size }, (_, index) => ({
          id: `event-${page * 100 + index}`,
          timestamp: new Date(Date.UTC(2026, 7, 27, 12, 0, page * 100 + index)).toISOString(),
        })),
        261,
        page * 100,
      ))
    }
    vi.stubGlobal("fetch", fetchMock)

    const items = await fetchCompleteActivityHistory({ hours: 24, dvr_id: "dvr-a" })

    expect(items).toHaveLength(261)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("offset=0&limit=100"),
      expect.stringContaining("offset=100&limit=100"),
      expect.stringContaining("offset=200&limit=100"),
    ])
    expect(fetchMock.mock.calls[0][0]).toContain("dvr_id=dvr-a")
    expect(fetchMock.mock.calls.every(([url]) => String(url).includes("limit=100"))).toBe(true)
    expect(fetchMock.mock.calls.every(([url]) => !String(url).includes("limit=250"))).toBe(true)
  })

  it("deduplicates overlapping pages and respects the recent-list maximum", async () => {
    const duplicate = { id: "same", timestamp: "2026-08-27T12:00:00.000Z" }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([
        duplicate,
        ...Array.from({ length: 99 }, (_, index) => ({
          id: `first-${index}`,
          timestamp: `2026-08-27T11:${String(index % 60).padStart(2, "0")}:00.000Z`,
        })),
      ], 150, 0))
      .mockResolvedValueOnce(response([
        duplicate,
        ...Array.from({ length: 49 }, (_, index) => ({
          id: `second-${index}`,
          timestamp: `2026-08-27T10:${String(index).padStart(2, "0")}:00.000Z`,
        })),
      ], 150, 100))
    vi.stubGlobal("fetch", fetchMock)

    const items = await fetchCompleteActivityHistory({ hours: 24 }, 125)

    expect(items).toHaveLength(125)
    expect(items.filter((item) => item.id === "same")).toHaveLength(1)
  })
})
