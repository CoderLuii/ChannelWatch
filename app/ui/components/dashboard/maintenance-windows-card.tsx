"use client"

import React, { useEffect, useRef, useState } from "react"
import { CalendarClock, ChevronDown, Loader2, RefreshCw } from "lucide-react"

import { Tag } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader } from "@/components/base/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { fetchMaintenanceWindows } from "@/lib/api"
import { t } from "@/lib/i18n"
import type { MaintenanceWindow, MaintenanceWindowsResponse } from "@/lib/types"
import { cn, revealDisclosure } from "@/lib/utils"

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

function MaintenanceFilter({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[][] }) {
  return (
    <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
      {label}
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger aria-label={label} className="text-sm font-normal text-foreground">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map(([optionValue, optionLabel]) => <SelectItem key={optionValue} value={optionValue}>{optionLabel}</SelectItem>)}
        </SelectContent>
      </Select>
    </label>
  )
}

export function MaintenanceWindowResults({ data }: { data: MaintenanceWindowsResponse }) {
  return (
    <div className={cn("grid gap-3", data.dvrs.length > 1 && "lg:grid-cols-2")}>
      {data.dvrs.map((dvr) => (
        <section key={dvr.dvr_id} className="rounded-control border border-hairline p-4">
          <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
            <h2 className="min-w-0 flex-1 break-words text-sm font-medium [overflow-wrap:anywhere]">{dvr.dvr_name}</h2>
            <Tag className="shrink-0">
              {t(`maintenance.status.${dvr.status}`)}
            </Tag>
          </div>
          {dvr.windows.length > 0 ? (
            <div className="space-y-2">
              {dvr.windows.map((window) => (
                <div key={`${dvr.dvr_id}-${window.start}-${window.end}`} className="flex min-h-11 flex-wrap items-center justify-between gap-3 rounded-control bg-fill px-3 py-2">
                  <span className="min-w-0 break-words text-sm [overflow-wrap:anywhere]">{formatMaintenanceRange(window, data.timezone)}</span>
                  <span className="shrink-0 whitespace-nowrap text-xs font-medium text-muted-foreground">
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
  const [expanded, setExpanded] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [minimumMinutes, setMinimumMinutes] = useState(60)
  const [days, setDays] = useState(7)
  const [dayFilter, setDayFilter] = useState("all")
  const [startHour, setStartHour] = useState(0)
  const [endHour, setEndHour] = useState(24)
  const [data, setData] = useState<MaintenanceWindowsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  // The dashboard's own refresh stays quiet. A change made here still shows the spinner.
  const [quietLoad, setQuietLoad] = useState(false)
  const lastRefreshKey = useRef(refreshKey)
  const [error, setError] = useState(false)
  const [reload, setReload] = useState(0)
  const contentId = "maintenance-windows-content"
  const cardRef = useRef<HTMLDivElement>(null)
  const wasExpanded = useRef(expanded)
  useEffect(() => {
    if (expanded && !wasExpanded.current) revealDisclosure(cardRef.current)
    wasExpanded.current = expanded
  }, [expanded])
  const toggleExpanded = () => setExpanded((open) => !open)
  const summary = error
    ? t("maintenance.error")
    : loading && !data
      ? t("maintenance.loading")
      : null

  useEffect(() => {
    const controller = new AbortController()
    setQuietLoad(lastRefreshKey.current !== refreshKey)
    lastRefreshKey.current = refreshKey
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
    <Card ref={cardRef} data-testid="maintenance-windows-card" className="overflow-hidden">
      <CardHeader className="space-y-0 p-0 sm:p-0">
        <div className="flex flex-wrap items-stretch gap-1 p-2">
          <button
            type="button"
            className="press-row flex min-h-14 min-w-0 flex-1 basis-[12rem] flex-wrap items-center justify-between gap-3 rounded-control px-2 py-2 text-left transition-colors hover:bg-fill focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-expanded={expanded}
            aria-controls={contentId}
            onClick={toggleExpanded}
          >
            <span className="flex min-w-0 items-center gap-2">
              <CalendarClock className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0">
                <span data-card-title="" className="block break-words text-base font-semibold leading-snug tracking-[-0.012em] [overflow-wrap:anywhere]">{t("maintenance.title")}</span>
                {summary ? (
                  <span className={cn("mt-0.5 block break-words text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]", error && "text-danger-fg")} aria-live="polite">
                    {summary}
                  </span>
                ) : null}
              </span>
            </span>
            <ChevronDown className={cn("h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-[var(--motion-base)] ease-out motion-reduce:transition-none", expanded && "rotate-180")} aria-hidden="true" />
          </button>
          <Button variant="outline" size="icon" className="shrink-0 self-center" aria-label={`${t("common.refresh")} ${t("maintenance.title")}`} onClick={() => setReload((value) => value + 1)} disabled={loading}>
            {loading && !quietLoad ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </Button>
        </div>
      </CardHeader>
      <div id={contentId} className={cn("disclosure-enter", !expanded && "hidden")}>
        <CardHeader className="gap-4 px-4 pb-4 pt-2 sm:px-4 sm:pb-4 sm:pt-2">
          <p className="break-words text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{t("maintenance.description")}</p>
          <Button type="button" variant="outline" className="min-h-11 w-full md:hidden" aria-expanded={filtersOpen} aria-controls="maintenance-filters" onClick={() => setFiltersOpen(!filtersOpen)}>{t("filters.title")}</Button>
          <div id="maintenance-filters" className={`${filtersOpen ? "grid" : "hidden"} grid-cols-2 gap-3 md:grid xl:grid-cols-5`}>
            <MaintenanceFilter label={t("maintenance.minimum")} value={String(minimumMinutes)} onChange={(value) => setMinimumMinutes(Number(value))} options={durationOptions.map((value) => [String(value), durationLabel(value)])} />
            <MaintenanceFilter label={t("maintenance.range")} value={String(days)} onChange={(value) => setDays(Number(value))} options={dayOptions.map((value) => [String(value), t("maintenance.dayCount", { count: value })])} />
            <MaintenanceFilter label={t("maintenance.days")} value={dayFilter} onChange={setDayFilter} options={[["all", t("maintenance.daysAll")], ["weekdays", t("maintenance.daysWeekdays")], ["weekends", t("maintenance.daysWeekends")]]} />
            <MaintenanceFilter label={t("maintenance.after")} value={String(startHour)} onChange={(value) => setStartHour(Number(value))} options={Array.from({ length: 24 }, (_, hour) => [String(hour), hourLabel(hour)])} />
            <MaintenanceFilter label={t("maintenance.before")} value={String(endHour)} onChange={(value) => setEndHour(Number(value))} options={Array.from({ length: 25 }, (_, hour) => [String(hour), hourLabel(hour)])} />
          </div>
        </CardHeader>
        <CardContent className="px-4 pb-5 sm:px-4 sm:pb-5">
          {loading && !data ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{t("maintenance.loading")}</div>
          ) : error ? (
            <p className="text-sm text-danger-fg">{t("maintenance.error")}</p>
          ) : data ? (
            <MaintenanceWindowResults data={data} />
          ) : null}
        </CardContent>
      </div>
    </Card>
  )
}
