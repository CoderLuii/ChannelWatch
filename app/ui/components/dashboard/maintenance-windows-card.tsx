"use client"

import React, { useEffect, useState } from "react"
import { CalendarClock, Loader2, RefreshCw } from "lucide-react"

import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { fetchMaintenanceWindows } from "@/lib/api"
import { t } from "@/lib/i18n"
import type { MaintenanceWindow, MaintenanceWindowsResponse } from "@/lib/types"

const durationOptions = [30, 60, 120, 240]
const dayOptions = [1, 3, 7, 14]

function hourLabel(hour: number): string {
  if (hour === 24) return t("maintenance.midnightEnd")
  if (hour === 0) return t("maintenance.midnight")
  if (hour === 12) return t("maintenance.noon")
  return hour < 12 ? `${hour}:00 AM` : `${hour - 12}:00 PM`
}

export function formatMaintenanceRange(
  window: MaintenanceWindow,
  timezoneName: string,
): string {
  try {
    const formatter = new Intl.DateTimeFormat(undefined, {
      timeZone: timezoneName,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
    return `${formatter.format(new Date(window.start))} – ${formatter.format(new Date(window.end))}`
  } catch {
    return t("maintenance.invalidWindow")
  }
}

function durationLabel(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  if (hours === 0) return `${minutes}m`
  return remainder === 0 ? `${hours}h` : `${hours}h ${remainder}m`
}

export function MaintenanceWindowResults({ data }: { data: MaintenanceWindowsResponse }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {data.dvrs.map((dvr) => (
        <section key={dvr.dvr_id} className="rounded-lg border bg-muted/20 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">{dvr.dvr_name}</h3>
            <Badge variant={dvr.status === "available" ? "secondary" : "outline"}>
              {t(`maintenance.status.${dvr.status}`)}
            </Badge>
          </div>
          {dvr.windows.length > 0 ? (
            <div className="space-y-2">
              {dvr.windows.map((window) => (
                <div key={`${dvr.dvr_id}-${window.start}-${window.end}`} className="flex items-center justify-between gap-3 rounded-md bg-background px-3 py-2">
                  <span className="text-sm">{formatMaintenanceRange(window, data.timezone)}</span>
                  <span className="whitespace-nowrap text-xs font-medium text-muted-foreground">
                    {durationLabel(window.duration_minutes)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{dvr.message}</p>
          )}
          {dvr.status === "truncated" && dvr.windows.length > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">{dvr.message}</p>
          )}
        </section>
      ))}
      {data.dvrs.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("maintenance.noDvrs")}</p>
      )}
    </div>
  )
}

interface MaintenanceWindowsCardProps {
  selectedDvr: string
  refreshKey?: number
}

export function MaintenanceWindowsCard({ selectedDvr, refreshKey = 0 }: MaintenanceWindowsCardProps) {
  const [minimumMinutes, setMinimumMinutes] = useState(60)
  const [days, setDays] = useState(7)
  const [dayFilter, setDayFilter] = useState("all")
  const [startHour, setStartHour] = useState(0)
  const [endHour, setEndHour] = useState(24)
  const [data, setData] = useState<MaintenanceWindowsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(false)
    fetchMaintenanceWindows({
      minimumMinutes,
      days,
      weekdays: dayFilter === "weekdays" ? [0, 1, 2, 3, 4] : dayFilter === "weekends" ? [5, 6] : undefined,
      startHour,
      endHour,
      dvrId: selectedDvr,
      signal: controller.signal,
    })
      .then((response) => setData(response))
      .catch((requestError) => {
        if (requestError instanceof DOMException && requestError.name === "AbortError") return
        setError(true)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [minimumMinutes, days, dayFilter, startHour, endHour, selectedDvr, refreshKey, reload])

  return (
    <Card data-testid="maintenance-windows-card">
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <CalendarClock className="h-4 w-4 text-primary" />
              {t("maintenance.title")}
            </CardTitle>
            <CardDescription className="mt-1">{t("maintenance.description")}</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => setReload((value) => value + 1)} disabled={loading}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            {t("common.refresh")}
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label className="space-y-1 text-xs font-medium text-muted-foreground">
            {t("maintenance.minimum")}
            <select className="h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground" value={minimumMinutes} onChange={(event) => setMinimumMinutes(Number(event.target.value))}>
              {durationOptions.map((value) => <option key={value} value={value}>{durationLabel(value)}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-xs font-medium text-muted-foreground">
            {t("maintenance.range")}
            <select className="h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground" value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {dayOptions.map((value) => <option key={value} value={value}>{t("maintenance.dayCount", { count: value })}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-xs font-medium text-muted-foreground">
            {t("maintenance.days")}
            <select className="h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground" value={dayFilter} onChange={(event) => setDayFilter(event.target.value)}>
              <option value="all">{t("maintenance.daysAll")}</option>
              <option value="weekdays">{t("maintenance.daysWeekdays")}</option>
              <option value="weekends">{t("maintenance.daysWeekends")}</option>
            </select>
          </label>
          <label className="space-y-1 text-xs font-medium text-muted-foreground">
            {t("maintenance.after")}
            <select className="h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground" value={startHour} onChange={(event) => setStartHour(Number(event.target.value))}>
              {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{hourLabel(hour)}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-xs font-medium text-muted-foreground">
            {t("maintenance.before")}
            <select className="h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground" value={endHour} onChange={(event) => setEndHour(Number(event.target.value))}>
              {Array.from({ length: 25 }, (_, hour) => <option key={hour} value={hour}>{hourLabel(hour)}</option>)}
            </select>
          </label>
        </div>
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t("maintenance.loading")}</div>
        ) : error ? (
          <p className="text-sm text-destructive">{t("maintenance.error")}</p>
        ) : data ? (
          <MaintenanceWindowResults data={data} />
        ) : null}
      </CardContent>
    </Card>
  )
}
