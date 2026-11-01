"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Activity, ChevronRight } from "lucide-react"
import type { ActivityTimelinePoint } from "@/lib/activity-timeline"
import { cn } from "@/lib/utils"

type LaneKey = "streams" | "recordings" | "vod"

const LANES: Array<{ key: LaneKey; labelKey: string }> = [
  { key: "streams", labelKey: "timeline.liveTV" },
  { key: "vod", labelKey: "timeline.vod" },
  { key: "recordings", labelKey: "timeline.recordings" },
]

interface ActivityTimelineProps {
  streamingData: ActivityTimelinePoint[]
  loading?: boolean
}

const formatClock = (timestamp: number) => new Date(timestamp).toLocaleTimeString([], {
  hour: "numeric",
  minute: "2-digit",
})

/** Rounds to the hour on purpose: a 20-minute bucket can't honestly say more than "around 9 PM". */
function aroundLabel(timestamp: number): string {
  const hour = new Date(timestamp + 30 * 60 * 1000)
  hour.setMinutes(0, 0, 0)
  return hour.toLocaleTimeString([], { hour: "numeric" })
}

/**
 * One sentence of what the day held, built from buckets, so it never claims an exact time.
 * "1 VOD session around 9 PM, no recordings"
 */
export function buildTimelineVerdict(points: ActivityTimelinePoint[]): string {
  const sum = (key: LaneKey) => points.reduce((total, point) => total + point[key], 0)
  const phrase = (key: LaneKey, one: string, many: string) => {
    const count = sum(key)
    if (count === 0) return null
    const text = count === 1 ? t(one) : t(many, { count })
    if (count > 1) return text
    const only = points.find((point) => point[key] > 0)
    return only ? `${text} ${t("timeline.verdict.around", { time: aroundLabel(only.intervalStart) })}` : text
  }
  const parts = [
    phrase("streams", "timeline.verdict.live.one", "timeline.verdict.live.many"),
    phrase("vod", "timeline.verdict.vod.one", "timeline.verdict.vod.many"),
    phrase("recordings", "timeline.verdict.rec.one", "timeline.verdict.rec.many"),
  ].filter((part): part is string => Boolean(part))
  if (parts.length === 0) return t("timeline.verdict.none")
  if (sum("recordings") === 0) parts.push(t("timeline.verdict.noRecordings"))
  return parts.join(", ")
}

/** Where 6-hour ticks fall, as bucket indexes across the 72-column grid. */
export function timelineSixHourTicks(points: ActivityTimelinePoint[]): Array<{ index: number; label: string }> {
  const ticks: Array<{ index: number; label: string }> = []
  points.forEach((point, index) => {
    const start = new Date(point.intervalStart)
    if (start.getMinutes() === 0 && start.getHours() % 6 === 0) {
      ticks.push({ index, label: start.toLocaleTimeString([], { hour: "numeric" }) })
    }
  })
  return ticks
}

const intervalLabel = (timestamp: number) => new Date(timestamp).toLocaleString([], {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
})

function TickRow({ points, className }: { points: ActivityTimelinePoint[]; className?: string }) {
  const hasNow = points.some((point) => point.isNow)
  // The Now label owns the right edge. In a 190px lane a tick past 76% would run under it.
  const ticks = timelineSixHourTicks(points).filter(({ index }) => !hasNow || (index + 0.5) / 72 <= 0.76)
  return (
    <div aria-hidden="true" className={cn("relative mt-1 h-4 overflow-hidden text-[11px] leading-4 tabular-nums text-muted-foreground", className)}>
      {ticks.map(({ index, label }) => (
        <span key={index} className="absolute top-0 -translate-x-1/2 whitespace-nowrap" style={{ left: `max(1rem, ${((index + 0.5) / 72) * 100}%)` }}>{label}</span>
      ))}
      {hasNow ? <span className="absolute right-0 top-0 bg-card pl-1 text-foreground">{t("timeline.now")}</span> : null}
    </div>
  )
}

function Lane({ label, points, laneKey, max }: { label: string; points: ActivityTimelinePoint[]; laneKey: LaneKey; max: number }) {
  return (
    <div className="min-w-0 xl:flex xl:flex-col" data-testid={`timeline-lane-${laneKey}`}>
      <p className="pb-1 text-xs text-muted-foreground">{label}</p>
      {/* Column gap 1px and a shared scale, so a bar means the same thing in every lane. */}
      <div data-lane="" className="relative grid h-3 grid-cols-[repeat(72,minmax(0,1fr))] items-end gap-x-px border-b border-hairline-strong md:h-12 xl:h-auto xl:min-h-12 xl:flex-1">
        {points.map((point) => {
          const count = point[laneKey]
          return (
            <span key={point.intervalStart} className="group/bar relative flex h-full items-end">
              {count > 0 ? (
                <span className="block w-full bg-[var(--chart-mark)]" style={{ height: `${Math.max(16, (count / max) * 100)}%` }} />
              ) : null}
              {point.isNow ? <span aria-hidden="true" className="absolute inset-y-0 left-0 w-px bg-foreground/60" /> : null}
            </span>
          )
        })}
      </div>
      <TickRow points={points} className="max-md:hidden" />
    </div>
  )
}

export function ActivityTimeline({ streamingData, loading = false }: ActivityTimelineProps) {
  const [tip, setTip] = React.useState<{ point: ActivityTimelinePoint; leftPercent: number } | null>(null)
  const max = Math.max(1, ...streamingData.flatMap((point) => LANES.map(({ key }) => point[key])))
  const nonzeroPoints = streamingData.filter((point) => LANES.some(({ key }) => point[key] > 0))
  const verdict = loading ? t("statusOverview.loading") : buildTimelineVerdict(streamingData)
  const stripRef = React.useRef<HTMLDivElement | null>(null)

  const showTipAt = (clientX: number) => {
    const strip = stripRef.current
    if (!strip || streamingData.length === 0) return
    // Lanes stack on a phone and sit side by side from 768, so read the one under the finger.
    const lane = Array.from(strip.querySelectorAll<HTMLElement>("[data-lane]")).find((element) => {
      const box = element.getBoundingClientRect()
      return clientX >= box.left && clientX <= box.right
    })
    if (!lane) return
    const box = lane.getBoundingClientRect()
    const stripBox = strip.getBoundingClientRect()
    const ratio = Math.min(0.999, Math.max(0, (clientX - box.left) / box.width))
    const point = streamingData[Math.floor(ratio * streamingData.length)]
    const leftPercent = Math.min(100 - (176 / stripBox.width) * 100, Math.max(0, ((clientX - stripBox.left) / stripBox.width) * 100 - 10))
    setTip({ point, leftPercent: Math.max(0, leftPercent) })
  }

  return (
    <Card data-testid="activity-timeline" className="min-w-0 overflow-hidden xl:flex xl:flex-col">
      <CardHeader className="px-4 pb-1 pt-3">
        <CardTitle className="flex items-center gap-2 text-base font-semibold">
          <Activity className="size-4 text-muted-foreground" aria-hidden="true" />
          {t("timeline.heading")}
        </CardTitle>
        <p data-testid="timeline-verdict" className="mt-1 text-sm leading-5 text-foreground [overflow-wrap:anywhere]">{verdict}</p>
      </CardHeader>
      <CardContent className="px-4 pb-3 pt-2 xl:flex xl:flex-1 xl:flex-col">
        <div
          ref={stripRef}
          role="group"
          aria-label={t("timeline.group")}
          data-testid="timeline-lanes"
          className="relative touch-pan-y xl:flex xl:flex-1 xl:flex-col"
          onPointerDown={(event) => showTipAt(event.clientX)}
          onPointerMove={(event) => {
            if (event.pointerType === "mouse" || event.buttons > 0) showTipAt(event.clientX)
          }}
          onPointerLeave={() => setTip(null)}
          onPointerCancel={() => setTip(null)}
        >
          <div className="grid gap-2 md:grid-cols-3 md:gap-4 xl:flex-1">
            {LANES.map(({ key, labelKey }) => (
              <Lane key={key} laneKey={key} label={t(labelKey)} points={streamingData} max={max} />
            ))}
          </div>
          <TickRow points={streamingData} className="md:hidden" />
          {tip ? (
            <div
              className="pointer-events-none absolute top-5 z-10 w-[11rem] rounded-control border border-hairline-strong bg-raised px-3 py-2 text-xs shadow-3"
              style={{ left: `${tip.leftPercent}%` }}
              data-testid="activity-timeline-tooltip"
            >
              <p className="mb-1 font-medium text-card-foreground tabular-nums">{formatClock(tip.point.intervalStart)}–{formatClock(tip.point.intervalEnd)}</p>
              {LANES.map(({ key, labelKey }) => (
                <div key={key} className="flex items-center justify-between gap-3" data-testid={`activity-timeline-tooltip-${key}`}>
                  <span className="text-muted-foreground">{t(labelKey)}</span>
                  <span className="font-medium tabular-nums text-card-foreground">{tip.point[key]}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <details className="group/details mt-3 rounded-control border border-hairline px-3 py-2 text-sm">
          <summary className="press-row -mx-1 flex min-h-11 cursor-pointer items-center rounded-thumb px-1 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {t("timeline.viewData")}
            <ChevronRight className="ml-auto size-4 shrink-0 text-muted-foreground transition-transform duration-[var(--motion-base)] ease-out group-open/details:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
          </summary>
          {nonzeroPoints.length === 0 ? (
            <p className="mt-2 text-muted-foreground">{t("timeline.noSelectedEvents")}</p>
          ) : (
            <>
              <p className="my-2 text-xs text-muted-foreground">{t("timeline.zeroIntervals")}</p>
              <div className="max-h-64 overflow-auto">
                <table className="w-full text-left text-xs">
                  <caption className="pb-2 text-left font-medium">{t("timeline.dataCaption")}</caption>
                  <thead><tr>
                    <th scope="col" className="p-2">{t("timeline.interval")}</th>
                    {LANES.map(({ key, labelKey }) => <th key={key} scope="col" className={cn("p-2 text-right")}>{t(labelKey)}</th>)}
                  </tr></thead>
                  <tbody>{nonzeroPoints.map((point) => (
                    <tr key={point.intervalStart} className="border-t border-hairline">
                      <th scope="row" className="p-2 font-normal">
                        <time dateTime={new Date(point.intervalStart).toISOString()}>{intervalLabel(point.intervalStart)}</time>
                        {" – "}
                        <time dateTime={new Date(point.intervalEnd).toISOString()}>{intervalLabel(point.intervalEnd)}</time>
                      </th>
                      {LANES.map(({ key }) => <td key={key} className="p-2 text-right tabular-nums">{point[key]}</td>)}
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </>
          )}
        </details>
      </CardContent>
    </Card>
  )
}
