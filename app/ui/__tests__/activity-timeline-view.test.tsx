import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { expect, it } from "vitest"
import { ActivityTimeline } from "@/components/dashboard/activity-timeline"
import type { ActivityTimelinePoint } from "@/lib/activity-timeline"

const point = (index: number, streams: number, recordings: number, vod: number): ActivityTimelinePoint => ({
  timestamp: index * 1200000 + 600000, intervalStart: index * 1200000, intervalEnd: (index + 1) * 1200000,
  streams, recordings, vod, isNow: false,
})
const points = [point(0, 2, 1, 0), point(1, 0, 0, 0), point(2, 0, 0, 3)]
const render = (visibility = { streams: true, recordings: true, vod: true }, data = points) => renderToStaticMarkup(
  <ActivityTimeline streamingData={data} chartVisibility={visibility} onToggleVisibility={() => {}} />,
)
it("exposes exactly the nonzero chart counts and unambiguous interval endpoints", () => {
  const html = render()
  const table = html.match(/<table\b[\s\S]*?<\/table>/)?.[0] ?? ""
  expect(table).toContain("24-hour activity by time")
  expect([...table.matchAll(/<td\b[^>]*>(\d+)<\/td>/g)].map(match => Number(match[1]))).toEqual([2, 1, 0, 0, 0, 3])
  expect(table).toContain("1970-01-01T00:00:00.000Z")
  expect(table).toContain("1970-01-01T01:00:00.000Z")
  expect(table.match(/<tbody>[\s\S]*<\/tbody>/)?.[0].match(/<tr\b/g)).toHaveLength(2)
})
it("honors legend selection without exposing hidden-series-only intervals", () => {
  const table = render({ streams: true, recordings: false, vod: false }).match(/<table\b[\s\S]*?<\/table>/)?.[0] ?? ""
  expect([...table.matchAll(/<td\b[^>]*>(\d+)<\/td>/g)].map(match => Number(match[1]))).toEqual([2])
  expect(table).not.toContain("Recordings")
  expect(table).not.toContain("VOD")
})
it("distinguishes empty data from no selected series", () => {
  expect(render({ streams: false, recordings: false, vod: false })).toContain("Select a series")
  expect(render(undefined, [point(0, 0, 0, 0)])).toContain("No events in the selected series")
  expect(render(undefined, [])).not.toContain("<table")
})
