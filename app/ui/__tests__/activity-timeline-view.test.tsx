import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { expect, it } from "vitest"
import { ActivityTimeline, buildTimelineVerdict } from "@/components/dashboard/activity-timeline"
import type { ActivityTimelinePoint } from "@/lib/activity-timeline"

const point = (index: number, streams: number, recordings: number, vod: number): ActivityTimelinePoint => ({
  timestamp: index * 1200000 + 600000, intervalStart: index * 1200000, intervalEnd: (index + 1) * 1200000,
  streams, recordings, vod, isNow: false,
})
const points = [point(0, 2, 1, 0), point(1, 0, 0, 0), point(2, 0, 0, 3)]
const render = (data = points) => renderToStaticMarkup(<ActivityTimeline streamingData={data} />)

it("exposes exactly the nonzero counts and unambiguous interval endpoints", () => {
  const table = render().match(/<table\b[\s\S]*?<\/table>/)?.[0] ?? ""
  expect(table).toContain("24-hour activity by time")
  expect([...table.matchAll(/<td\b[^>]*>(\d+)<\/td>/g)].map(match => Number(match[1]))).toEqual([2, 0, 1, 0, 3, 0])
  expect(table).toContain("1970-01-01T00:00:00.000Z")
  expect(table).toContain("1970-01-01T01:00:00.000Z")
  expect(table.match(/<tbody>[\s\S]*<\/tbody>/)?.[0].match(/<tr\b/g)).toHaveLength(2)
})

it("draws three lanes from one mark and no legend to toggle", () => {
  const html = render()
  for (const lane of ["streams", "vod", "recordings"]) expect(html).toContain(`timeline-lane-${lane}`)
  expect(html).toContain("var(--chart-mark)")
  expect(html).not.toContain("aria-pressed")
  expect(html).not.toContain("recharts")
})

it("keeps the lanes and says so when nothing happened", () => {
  const html = render([point(0, 0, 0, 0)])
  expect(html).toContain("timeline-lane-streams")
  expect(html).toContain("Nothing watched or recorded")
  expect(html).not.toContain("<table")
})

it("says what the day held without claiming an exact time", () => {
  expect(buildTimelineVerdict([point(0, 0, 0, 0)])).toBe("Nothing watched or recorded")
  expect(buildTimelineVerdict([point(0, 0, 0, 1)])).toMatch(/^1 VOD session around .*, no recordings$/)
  expect(buildTimelineVerdict(points)).toBe("2 live sessions, 3 VOD sessions, 1 recording event around " + new Date(1800000).toLocaleTimeString([], { hour: "numeric" }))
})
