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
} from "recharts"

interface StreamingDataPoint {
  name: string
  streams: number
  recordings: number
  vod: number
  isNow?: boolean
  hour?: number
  minute?: number
  timestamp?: number
}

interface ChartVisibility {
  streams: boolean
  recordings: boolean
  vod: boolean
}

interface ActivityTimelineProps {
  streamingData: StreamingDataPoint[]
  chartVisibility: ChartVisibility
  onToggleVisibility: (key: keyof ChartVisibility) => void
}

export function ActivityTimeline({ streamingData, chartVisibility, onToggleVisibility }: ActivityTimelineProps) {
  return (
    <Card className="md:col-span-2">
      <CardHeader className="pb-1 pt-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Activity className="h-4 w-4 text-primary" />
          {t("timeline.title")}
        </CardTitle>
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
                dataKey="name"
                tick={{ fontSize: 10 }}
                axisLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                tickLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                padding={{ left: 0, right: 0 }}
                tickFormatter={(value) => value || ""}
                interval={0}
                minTickGap={50}
                height={30}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                axisLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                tickLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                domain={[0, 'auto']}
                width={25}
              />
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <RechartsTooltip
                contentStyle={{
                  backgroundColor: "hsl(var(--card))",
                  borderColor: "hsl(var(--border))",
                  fontSize: "12px",
                }}
                formatter={(value, name) => {
                  return [value ?? 0, name ?? ""]
                }}
                labelFormatter={(label, payload) => {
                  if (label === "Now") {
                    return t("timeline.now")
                  }
                  if (Array.isArray(payload) && payload.length > 0) {
                    const chartX = payload[0].payload?.timestamp
                    if (chartX) {
                      const point = streamingData.find(d => d.timestamp === chartX)
                      if (point) {
                        let displayHour = (point.hour ?? 0) % 12
                        if (displayHour === 0) displayHour = 12
                        const amPm = (point.hour ?? 0) < 12 ? "AM" : "PM"
                        const formattedMinute = (point.minute ?? 0).toString().padStart(2, '0')
                        return `${displayHour}:${formattedMinute} ${amPm}`
                      }
                    }
                  }
                  if (label && label !== "") {
                    return label
                  }
                  return t("timeline.unknownTime")
                }}
              />
              <Legend content={() => null} />

              {chartVisibility.streams && (
                <Area
                  type="monotone"
                  name={t("timeline.liveTV")}
                  stroke="var(--chart-streams)"
                  fillOpacity={1}
                  fill="url(#colorStreams)"
                  strokeWidth={2}
                  connectNulls={true}
                  dataKey="streams"
                />
              )}
              {chartVisibility.recordings && (
                <Area
                  type="monotone"
                  name={t("timeline.recordings")}
                  stroke="var(--chart-recordings)"
                  fillOpacity={1}
                  fill="url(#colorRecordings)"
                  strokeWidth={2}
                  connectNulls={true}
                  dataKey="recordings"
                />
              )}
              {chartVisibility.vod && (
                <Area
                  type="monotone"
                  name={t("timeline.vod")}
                  stroke="var(--chart-vod)"
                  fillOpacity={1}
                  fill="url(#colorVOD)"
                  strokeWidth={2}
                  connectNulls={true}
                  dataKey="vod"
                />
              )}
              {streamingData.findIndex(d => d.isNow) >= 0 && (
                <Area
                  type="monotone"
                  dataKey={() => 0}
                  name=""
                  fill="none"
                  stroke="none"
                  legendType="none"
                  dot={(props) => {
                    const nowIndex = streamingData.findIndex(d => d.isNow)
                    if (props.index !== nowIndex) return <g />
                    return (
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={6}
                        stroke="var(--chart-now-stroke)"
                        strokeWidth={2}
                        fill="var(--chart-now-fill)"
                      />
                    )
                  }}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>
        {/* Custom interactive legend */}
        <div className="flex justify-center items-center gap-6 mt-2 mb-1 text-xs">
          <button
            onClick={() => onToggleVisibility('streams')}
            className="flex items-center gap-1.5 opacity-90 hover:opacity-100 transition-opacity"
            aria-label={t("timeline.ariaToggleLiveTV")}
          >
            <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.streams ? 'var(--chart-streams)' : 'transparent', border: '1px solid var(--chart-streams)' }}>
              {chartVisibility.streams && <Check className="h-3 w-3 text-white" />}
            </div>
            <span>{t("timeline.liveTV")}</span>
          </button>
          <button
            onClick={() => onToggleVisibility('recordings')}
            className="flex items-center gap-1.5 opacity-90 hover:opacity-100 transition-opacity"
            aria-label={t("timeline.ariaToggleRecordings")}
          >
            <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.recordings ? 'var(--chart-recordings)' : 'transparent', border: '1px solid var(--chart-recordings)' }}>
              {chartVisibility.recordings && <Check className="h-3 w-3 text-white" />}
            </div>
            <span>{t("timeline.recordings")}</span>
          </button>
          <button
            onClick={() => onToggleVisibility('vod')}
            className="flex items-center gap-1.5 opacity-90 hover:opacity-100 transition-opacity"
            aria-label={t("timeline.ariaToggleVod")}
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
