"use client"

import React, { useState, useMemo, useRef } from "react"
import { t } from "@/lib/i18n"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Count } from "@/components/base/badge"
import { AlertCircle, Calendar, ChevronRight, Clock } from "lucide-react"
import { RecordingDetailDialog } from "@/components/dashboard/recording-detail-dialog"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import { formatCountdown } from "@/lib/relative-time"
import { formatCount } from "@/lib/utils"

interface Recording {
  id: string
  title: string
  start_time: number
  end_time?: number
  channel: string
  scheduled_time: string
  image: string
  artwork_fallback_exhausted?: boolean
  dvr_id?: string
  dvr_name?: string
}

interface UpcomingRecordingsListProps {
  recordings: Recording[]
  count: number
  loading?: boolean
  hasError?: boolean
  onRetry?: () => void
}

/** Rows shown before "Show all" below 1280, where the list sits in the page instead of its own scroller. */
export const COLLAPSED_ROWS = 5

const hasStartTime = (startTime: number) => Number.isFinite(startTime) && startTime > 0

function getDayLabel(startTime: number): string {
  if (!hasStartTime(startTime)) return t("recordings.unknownTime")
  const date = new Date(startTime * 1000)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const tomorrow = new Date(today.getTime() + 86400000)
  const itemDate = new Date(date.getFullYear(), date.getMonth(), date.getDate())

  if (itemDate.getTime() === today.getTime()) return t("common.today")
  if (itemDate.getTime() === tomorrow.getTime()) return t("common.tomorrow")
  return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
}

export function isRecordingActive(recording: Pick<Recording, "start_time" | "end_time">, now: number = Date.now()): boolean {
  if (!hasStartTime(recording.start_time) || recording.start_time * 1000 > now) return false
  return !recording.end_time || recording.end_time * 1000 > now
}

function recordingClock(startTime: number): string {
  return hasStartTime(startTime)
    ? new Date(startTime * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : t("recordings.unknownTime")
}

export function recordingMeta(recording: Pick<Recording, "channel" | "start_time">): string {
  return [recording.channel, recordingClock(recording.start_time)].filter(Boolean).join(" · ")
}

function trailingCountdown(startTime: number): string {
  if (!hasStartTime(startTime) || startTime * 1000 - Date.now() >= 24 * 60 * 60 * 1000) return ""
  return formatCountdown(startTime)
}

/** Five day-headed rows, the height a real list collapses to, so the tiles below don't jump on load. */
export function UpcomingSkeletonRows() {
  return (
    <div role="status" aria-label={t("statusOverview.loading")} className="animate-pulse">
      {[0, 1, 2, 3, 4].map((row) => (
        <div key={row} className="border-t border-hairline first:border-t-0">
          <div className="px-4 pb-2 pt-3.5">
            <div className="h-3 w-16 rounded-thumb bg-muted" />
          </div>
          <div className="flex min-h-[60px] items-center gap-3 px-4">
            <div className="size-4 shrink-0 rounded-thumb bg-muted" />
            <div className="min-w-0 flex-1 space-y-2">
              <div className="h-3 w-2/3 rounded-thumb bg-muted" />
              <div className="h-3 w-1/3 rounded-thumb bg-muted" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function UpcomingRecordingsList({ recordings, count, loading = false, hasError = false, onRetry }: UpcomingRecordingsListProps) {
  const [selectedRecording, setSelectedRecording] = useState<Recording | null>(null)
  const [expanded, setExpanded] = useState(false)
  const dialogTriggerRef = useRef<HTMLButtonElement | null>(null)
  const { availableDvrs } = useDvrSelection()
  const collapsedHidden = expanded ? 0 : Math.max(0, recordings.length - COLLAPSED_ROWS)
  const collapsedIds = useMemo(() => new Set(recordings.slice(COLLAPSED_ROWS).map((recording) => recording.id)), [recordings])

  const groupedRecordings = useMemo(() => {
    const groups: { label: string; items: Recording[] }[] = []
    let currentLabel = ""
    for (const rec of recordings) {
      const label = getDayLabel(rec.start_time)
      if (label !== currentLabel) {
        currentLabel = label
        groups.push({ label, items: [rec] })
      } else {
        groups[groups.length - 1].items.push(rec)
      }
    }
    return groups
  }, [recordings])

  return (
    <>
      <Card data-testid="upcoming-recordings" className="flex min-h-0 min-w-0 max-w-full flex-col overflow-hidden xl:max-h-[505px] xl:min-h-[280px]">
        <CardHeader className="flex-shrink-0 px-4 pb-2 pt-4">
          <CardTitle className="flex items-center gap-2 text-base font-semibold">
            <Calendar className="size-4 text-muted-foreground" aria-hidden="true" />
            {t("recordings.title")}
            {count > 0 && <Count>{formatCount(count)}</Count>}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex-grow overflow-x-hidden p-0 xl:overflow-y-auto xl:overscroll-contain">
          {loading ? (
            <UpcomingSkeletonRows />
          ) : groupedRecordings.length > 0 ? (
            <>
              {groupedRecordings.map((group) => {
                const hiddenAll = !expanded && group.items.every((recording) => collapsedIds.has(recording.id))
                return (
                  <div key={group.label} className={hiddenAll ? "border-t border-hairline first:border-t-0 max-xl:hidden" : "border-t border-hairline first:border-t-0"}>
                    <div className="px-4 pb-1 pt-3 text-xs font-medium text-muted-foreground xl:sticky xl:top-0 xl:z-[5] xl:bg-card">
                      {group.label}
                    </div>
                    {group.items.map((recording) => {
                      const active = isRecordingActive(recording)
                      const countdown = active ? "" : trailingCountdown(recording.start_time)
                      const meta = recordingMeta(recording)
                      return (
                        <button
                          key={recording.id}
                          type="button"
                          onClick={(event) => {
                            dialogTriggerRef.current = event.currentTarget
                            setSelectedRecording(recording)
                          }}
                          className={`press-row group/row flex min-h-14 w-full cursor-pointer flex-wrap items-start gap-x-3 pl-4 text-left transition-colors hover:bg-fill ${!expanded && collapsedIds.has(recording.id) ? "max-xl:hidden" : ""}`}
                        >
                          <Clock className="mt-3 size-4 flex-shrink-0 text-muted-foreground group-first-of-type/row:mt-1.5" aria-hidden="true" />
                          <div className="flex min-w-0 flex-1 basis-[8rem] flex-wrap items-start justify-between gap-x-3 border-t border-hairline py-2.5 pr-4 group-first-of-type/row:border-t-0 group-first-of-type/row:pt-1">
                            <div className="min-w-0 flex-1 basis-[8rem]">
                              <p className="line-clamp-2 min-w-0 break-words text-sm font-medium leading-snug" title={recording.title}>{recording.title}</p>
                              <p className="mt-0.5 flex min-w-0 items-baseline gap-1 text-xs leading-snug text-muted-foreground" title={meta}>
                                {recording.channel ? (
                                  <>
                                    <span className="min-w-0 truncate">{recording.channel}</span>
                                    <span aria-hidden="true">·</span>
                                  </>
                                ) : null}
                                <span className="shrink-0 tabular-nums">{recordingClock(recording.start_time)}</span>
                              </p>
                              {recording.dvr_name && availableDvrs.length > 1 && (
                                <p className="mt-0.5 min-w-0 truncate text-xs text-muted-foreground" title={recording.dvr_name}>{recording.dvr_name}</p>
                              )}
                            </div>
                            {active ? (
                              <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-foreground">
                                <span aria-hidden="true" className="size-2 rounded-full bg-foreground" />
                                {t("recordings.active")}
                              </span>
                            ) : countdown ? (
                              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{countdown}</span>
                            ) : null}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )
              })}
              {recordings.length > COLLAPSED_ROWS ? (
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => setExpanded((current) => !current)}
                  className="press-row flex min-h-11 w-full items-center justify-between gap-2 border-t border-hairline px-4 text-left text-sm font-medium text-primary transition-colors hover:bg-fill xl:hidden"
                >
                  {collapsedHidden > 0 ? t("recordings.showAll", { count: formatCount(recordings.length) }) : t("recordings.showFewer")}
                  <ChevronRight className={`size-4 shrink-0 text-muted-foreground ${expanded ? "-rotate-90" : "rotate-90"}`} aria-hidden="true" />
                </button>
              ) : null}
            </>
          ) : hasError ? (
            <div className="flex flex-col items-center justify-center gap-2 p-6 text-center">
              <AlertCircle className="size-6 text-muted-foreground/60" aria-hidden="true" />
              <span className="text-sm text-muted-foreground">{t("recordings.loadError")}</span>
              {onRetry ? <Button type="button" variant="outline" size="sm" onClick={onRetry}>{t("activity.retry")}</Button> : null}
            </div>
          ) : (
            <p className="px-4 pb-4 text-sm text-muted-foreground">{t("recordings.empty")}</p>
          )}
        </CardContent>
      </Card>
      <RecordingDetailDialog
        recording={selectedRecording}
        open={!!selectedRecording}
        onOpenChange={(open) => {
          if (!open) setSelectedRecording(null)
        }}
        onCloseAutoFocus={(event) => {
          const trigger = dialogTriggerRef.current
          if (trigger?.isConnected && trigger.getClientRects().length > 0) {
            event.preventDefault()
            trigger.focus()
          }
        }}
      />
    </>
  )
}
