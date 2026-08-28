"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Activity, Check } from "lucide-react"
import {
  AreaChart,
  Area,
  ResponsiveContainer,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  Legend,
  ReferenceDot,
  type TooltipContentProps,
} from "recharts"
import {
  activityTimelineTicks,
  type ActivityTimelinePoint,
} from "@/lib/activity-timeline"

interface ChartVisibility {
  streams: boolean
  recordings: boolean
  vod: boolean
}

interface ActivityTimelineProps {
  streamingData: ActivityTimelinePoint[]
  chartVisibility: ChartVisibility
  onToggleVisibility: (key: keyof ChartVisibility) => void
}

interface ActivityTimelineTooltipProps extends TooltipContentProps {
  chartVisibility: ChartVisibility
}

const formatTimelineTime = (timestamp: number) => new Date(timestamp).toLocaleTimeString([], {
  hour: "numeric",
  minute: "2-digit",
})

function ActivityTimelineTooltip({
  active,
  payload,
  chartVisibility,
}: ActivityTimelineTooltipProps) {
  const point = payload
    ?.map((entry) => entry.payload as ActivityTimelinePoint | undefined)
    .find((entry) => entry && Number.isFinite(entry.intervalStart))
  if (!active || !point) return null

  const rows = [
    { key: "streams", label: t("timeline.liveTV"), color: "var(--chart-streams)" },
    { key: "recordings", label: t("timeline.recordings"), color: "var(--chart-recordings)" },
    { key: "vod", label: t("timeline.vod"), color: "var(--chart-vod)" },
  ] as const

  return (
    <div
      className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-md"
      data-testid="activity-timeline-tooltip"
    >
      <p className="mb-1.5 font-medium text-card-foreground">
        {formatTimelineTime(point.intervalStart)}–{formatTimelineTime(point.intervalEnd)}
      </p>
      <div className="space-y-1">
        {rows.filter(({ key }) => chartVisibility[key]).map(({ key, label, color }) => {
          const count = point[key]
          const unit = count === 1 ? t("timeline.event") : t("timeline.events")
          return (
            <div
              key={key}
              className="flex min-w-32 items-center justify-between gap-4"
              data-testid={`activity-timeline-tooltip-${key}`}
            >
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} aria-hidden="true" />
                {label}
              </span>
              <span className="font-medium tabular-nums text-card-foreground">{count} {unit}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function ActivityTimeline({ streamingData, chartVisibility, onToggleVisibility }: ActivityTimelineProps) {
  const timelineTicks = activityTimelineTicks(streamingData)
  const timelineStart = streamingData[0]?.intervalStart
  const timelineEnd = streamingData[streamingData.length - 1]?.intervalEnd
  const nowPoint = streamingData.find((point) => point.isNow)

  return (
    <Card className="md:col-span-2">
      <CardHeader className="pb-1 pt-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Activity className="h-4 w-4 text-primary" />
            {t("timeline.title")}
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{t("timeline.description")}</p>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="h-[180px] sm:h-[200px] w-full relative" role="img" aria-label={t("timeline.ariaChart")}>
          {streamingData.every(d => d.streams === 0 && d.recordings === 0 && d.vod === 0) && (
            <div className="absolute inset-0 flex flex-col items-center justify-center z-10 pointer-events-none gap-1">
              <Activity className="h-6 w-6 text-muted-foreground/30" />
              <p className="text-sm text-muted-foreground bg-background/80 px-3 py-1 rounded-md">{t("timeline.empty")}</p>
              <p className="text-[10px] text-muted-foreground/60">{t("timeline.emptyHint")}</p>
            </div>
          )}
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={streamingData} margin={{ top: 10, right: 0, left: 5, bottom: 10 }}>
              <defs>
                <linearGradient id="colorStreams" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--chart-streams)" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="var(--chart-streams)" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="colorRecordings" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--chart-recordings)" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="var(--chart-recordings)" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="colorVOD" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--chart-vod)" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="var(--chart-vod)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="timestamp"
                type="number"
                scale="time"
                domain={timelineStart != null && timelineEnd != null ? [timelineStart, timelineEnd] : ["auto", "auto"]}
                ticks={timelineTicks}
                tick={{ fontSize: 10 }}
                axisLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                tickLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                padding={{ left: 0, right: 0 }}
                tickFormatter={(value) => new Date(Number(value)).toLocaleTimeString([], { hour: "numeric" })}
                minTickGap={50}
                height={30}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                axisLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                tickLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                domain={[0, 'auto']}
                allowDecimals={false}
                width={25}
              />
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <RechartsTooltip
                isAnimationActive={false}
                content={(props) => (
                  <ActivityTimelineTooltip {...props} chartVisibility={chartVisibility} />
                )}
              />
              <Legend content={() => null} />

              {chartVisibility.streams && (
                <Area
                  type="step"
                  name={t("timeline.liveTV")}
                  stroke="var(--chart-streams)"
                  fillOpacity={1}
                  fill="url(#colorStreams)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 0, strokeWidth: 0 }}
                  isAnimationActive={false}
                  dataKey="streams"
                />
              )}
              {chartVisibility.recordings && (
                <Area
                  type="step"
                  name={t("timeline.recordings")}
                  stroke="var(--chart-recordings)"
                  fillOpacity={1}
                  fill="url(#colorRecordings)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 0, strokeWidth: 0 }}
                  isAnimationActive={false}
                  dataKey="recordings"
                />
              )}
              {chartVisibility.vod && (
                <Area
                  type="step"
                  name={t("timeline.vod")}
                  stroke="var(--chart-vod)"
                  fillOpacity={1}
                  fill="url(#colorVOD)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 0, strokeWidth: 0 }}
                  isAnimationActive={false}
                  dataKey="vod"
                />
              )}
              {nowPoint && (
                <ReferenceDot
                  x={nowPoint.nowTimestamp ?? nowPoint.timestamp}
                  y={0}
                  r={6}
                  stroke="var(--chart-now-stroke)"
                  strokeWidth={2}
                  fill="var(--chart-now-fill)"
                  ifOverflow="visible"
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>
        {/* Custom interactive legend */}
        <div className="flex justify-center items-center gap-6 mt-2 mb-1 text-xs">
          <button
            type="button"
            onClick={() => onToggleVisibility('streams')}
            className="flex min-h-11 items-center gap-1.5 rounded-md px-2 opacity-90 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={t("timeline.ariaToggleLiveTV")}
            aria-pressed={chartVisibility.streams}
          >
            <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.streams ? 'var(--chart-streams)' : 'transparent', border: '1px solid var(--chart-streams)' }}>
              {chartVisibility.streams && <Check className="h-3 w-3 text-white" />}
            </div>
            <span>{t("timeline.liveTV")}</span>
          </button>
          <button
            type="button"
            onClick={() => onToggleVisibility('recordings')}
            className="flex min-h-11 items-center gap-1.5 rounded-md px-2 opacity-90 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={t("timeline.ariaToggleRecordings")}
            aria-pressed={chartVisibility.recordings}
          >
            <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.recordings ? 'var(--chart-recordings)' : 'transparent', border: '1px solid var(--chart-recordings)' }}>
              {chartVisibility.recordings && <Check className="h-3 w-3 text-white" />}
            </div>
            <span>{t("timeline.recordings")}</span>
          </button>
          <button
            type="button"
            onClick={() => onToggleVisibility('vod')}
            className="flex min-h-11 items-center gap-1.5 rounded-md px-2 opacity-90 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={t("timeline.ariaToggleVod")}
            aria-pressed={chartVisibility.vod}
          >
            <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.vod ? 'var(--chart-vod)' : 'transparent', border: '1px solid var(--chart-vod)' }}>
              {chartVisibility.vod && <Check className="h-3 w-3 text-white" />}
            </div>
            <span>{t("timeline.vod")}</span>
          </button>
        </div>
      </CardContent>
    </Card>
  )
}
