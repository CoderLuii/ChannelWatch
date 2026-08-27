import { describe, expect, it } from "vitest"

import {
  ACTIVITY_TIMELINE_BUCKET_MINUTES,
  activityTimelineTicks,
  buildActivityTimeline,
} from "@/lib/activity-timeline"
import type { ActivityItem } from "@/lib/types"

const event = (id: string, type: string, timestamp: string): ActivityItem => ({
  id,
  type,
  timestamp,
  title: id,
  message: id,
  icon: "activity",
})

describe("buildActivityTimeline", () => {
  const now = new Date("2026-08-27T14:07:00.000Z")

  it("builds 72 unique half-open 20-minute intervals covering the trailing day", () => {
    const points = buildActivityTimeline([], now)

    expect(points).toHaveLength(72)
    expect(new Set(points.map((point) => point.timestamp)).size).toBe(72)
    expect(points.every((point) => point.intervalEnd - point.intervalStart === 20 * 60 * 1000)).toBe(true)
    expect(points.slice(1).every((point, index) => point.intervalStart === points[index].intervalEnd)).toBe(true)
    expect(points.filter((point) => point.isNow)).toHaveLength(1)
    expect(points.at(-1)?.isNow).toBe(true)
    expect(points.at(-1)?.nowTimestamp).toBe(now.getTime())
  })

  it("assigns boundary events once and excludes the half-open window end", () => {
    const empty = buildActivityTimeline([], now)
    const start = empty[4].intervalStart
    const end = empty[4].intervalEnd
    const points = buildActivityTimeline([
      event("at-start", "watching_channel", new Date(start).toISOString()),
      event("before-end", "watching_channel", new Date(end - 1).toISOString()),
      event("at-end", "watching_channel", new Date(end).toISOString()),
    ], now)

    expect(points[4].streams).toBe(2)
    expect(points[5].streams).toBe(1)
  })

  it("counts event records by category rather than duration or concurrency", () => {
    const empty = buildActivityTimeline([], now)
    const timestamp = new Date(empty[10].intervalStart + 1).toISOString()
    const points = buildActivityTimeline([
      event("channel", "watching_channel", timestamp),
      event("recording-1", "recording_scheduled", timestamp),
      event("recording-2", "recording_completed", timestamp),
      event("vod", "watching_vod", timestamp),
      event("other", "disk_alert", timestamp),
    ], now)

    expect(points[10]).toMatchObject({ streams: 1, recordings: 2, vod: 1 })
  })

  it("counts a recovered activity UUID only once", () => {
    const empty = buildActivityTimeline([], now)
    const duplicate = event(
      "same-event",
      "watching_vod",
      new Date(empty[10].intervalStart + 1).toISOString(),
    )

    const points = buildActivityTimeline([duplicate, duplicate], now)

    expect(points[10].vod).toBe(1)
  })

  it("ignores invalid timestamps and events outside the displayed window", () => {
    const empty = buildActivityTimeline([], now)
    const points = buildActivityTimeline([
      event("invalid", "watching_channel", "not-a-time"),
      event("old", "watching_channel", new Date(empty[0].intervalStart - 1).toISOString()),
      event("future", "watching_channel", new Date(empty.at(-1)!.intervalEnd).toISOString()),
    ], now)

    expect(points.reduce((sum, point) => sum + point.streams, 0)).toBe(0)
  })

  it("uses real timestamps for ordered three-hour axis ticks", () => {
    const points = buildActivityTimeline([], now)
    const ticks = activityTimelineTicks(points)

    expect(ticks.length).toBeGreaterThanOrEqual(7)
    expect(ticks.every((tick, index) => index === 0 || tick > ticks[index - 1])).toBe(true)
    expect(ticks.every((tick) => new Date(tick).getHours() % 3 === 0)).toBe(true)
    expect(ACTIVITY_TIMELINE_BUCKET_MINUTES).toBe(20)
  })
})
