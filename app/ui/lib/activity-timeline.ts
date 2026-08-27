import type { ActivityItem } from "@/lib/types"

export const ACTIVITY_TIMELINE_BUCKET_MINUTES = 20
export const ACTIVITY_TIMELINE_HOURS = 24

const BUCKET_MS = ACTIVITY_TIMELINE_BUCKET_MINUTES * 60 * 1000
const BUCKET_COUNT = (ACTIVITY_TIMELINE_HOURS * 60) / ACTIVITY_TIMELINE_BUCKET_MINUTES

export interface ActivityTimelinePoint {
  timestamp: number
  intervalStart: number
  intervalEnd: number
  streams: number
  recordings: number
  vod: number
  isNow: boolean
  nowTimestamp?: number
}

const STREAM_TYPES = new Set(["watching_channel", "stream_started"])
const RECORDING_TYPES = new Set([
  "recording_event",
  "recording_started",
  "recording_completed",
  "recording_scheduled",
  "recording_stopped",
  "recording_cancelled",
])
const VOD_TYPES = new Set(["watching_vod", "vod_playback"])

/**
 * Build 72 non-overlapping, half-open 20-minute intervals ending at the next
 * interval boundary. The final interval contains `now`, so current activity is
 * visible without assigning an event to a future point.
 *
 * Each point is placed at the interval midpoint. Recharts' `step` curve changes
 * at the midpoint between adjacent points, which makes every visible plateau
 * correspond to the same interval selected by its tooltip.
 */
export function buildActivityTimeline(
  activityItems: ActivityItem[],
  now: Date = new Date(),
): ActivityTimelinePoint[] {
  const nowTimestamp = now.getTime()
  if (!Number.isFinite(nowTimestamp)) return []

  const currentIntervalStart = Math.floor(nowTimestamp / BUCKET_MS) * BUCKET_MS
  const windowEnd = currentIntervalStart + BUCKET_MS
  const windowStart = windowEnd - BUCKET_COUNT * BUCKET_MS
  const points: ActivityTimelinePoint[] = Array.from({ length: BUCKET_COUNT }, (_, index) => {
    const intervalStart = windowStart + index * BUCKET_MS
    const intervalEnd = intervalStart + BUCKET_MS
    return {
      timestamp: intervalStart + BUCKET_MS / 2,
      intervalStart,
      intervalEnd,
      streams: 0,
      recordings: 0,
      vod: 0,
      isNow: nowTimestamp >= intervalStart && nowTimestamp < intervalEnd,
      nowTimestamp: nowTimestamp >= intervalStart && nowTimestamp < intervalEnd
        ? nowTimestamp
        : undefined,
    }
  })

  const countedActivityIds = new Set<string>()
  for (const activity of activityItems) {
    if (countedActivityIds.has(activity.id)) continue
    countedActivityIds.add(activity.id)

    const activityTimestamp = new Date(activity.timestamp).getTime()
    if (
      !Number.isFinite(activityTimestamp)
      || activityTimestamp < windowStart
      || activityTimestamp >= windowEnd
    ) {
      continue
    }

    const index = Math.floor((activityTimestamp - windowStart) / BUCKET_MS)
    const point = points[index]
    if (!point) continue

    if (STREAM_TYPES.has(activity.type)) point.streams += 1
    else if (RECORDING_TYPES.has(activity.type)) point.recordings += 1
    else if (VOD_TYPES.has(activity.type)) point.vod += 1
  }

  return points
}

export function activityTimelineTicks(points: ActivityTimelinePoint[]): number[] {
  if (points.length === 0) return []

  const start = points[0].intervalStart
  const end = points[points.length - 1].intervalEnd
  const cursor = new Date(start)
  cursor.setMinutes(0, 0, 0)
  while (cursor.getTime() < start || cursor.getHours() % 3 !== 0) {
    cursor.setHours(cursor.getHours() + 1)
  }

  const ticks: number[] = []
  while (cursor.getTime() < end) {
    ticks.push(cursor.getTime())
    cursor.setHours(cursor.getHours() + 3)
  }
  return ticks
}
