"use client"

import React, { useState, useMemo, useRef } from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Badge } from "@/components/base/badge"
import { Calendar, Clock } from "lucide-react"
import { RecordingDetailDialog } from "@/components/dashboard/recording-detail-dialog"

interface Recording {
  id: string
  title: string
  start_time: number
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
}

function getDayLabel(timestamp: number): string {
  const date = new Date(timestamp * 1000)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const tomorrow = new Date(today.getTime() + 86400000)
  const itemDate = new Date(date.getFullYear(), date.getMonth(), date.getDate())

  if (itemDate.getTime() === today.getTime()) return t("common.today")
  if (itemDate.getTime() === tomorrow.getTime()) return t("common.tomorrow")
  return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
}

function isToday(timestamp: number): boolean {
  const date = new Date(timestamp * 1000)
  const now = new Date()
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()
}

function isTomorrow(timestamp: number): boolean {
  const date = new Date(timestamp * 1000)
  const now = new Date()
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  return date.getFullYear() === tomorrow.getFullYear() && date.getMonth() === tomorrow.getMonth() && date.getDate() === tomorrow.getDate()
}

export function UpcomingRecordingsList({ recordings, count }: UpcomingRecordingsListProps) {
  const [selectedRecording, setSelectedRecording] = useState<Recording | null>(null)
  const dialogTriggerRef = useRef<HTMLButtonElement | null>(null)

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
      <Card className="flex max-h-[480px] min-h-0 sm:min-h-[280px] max-w-full flex-col overflow-hidden border-border/70 shadow-sm">
        <CardHeader className="flex-shrink-0 px-4 pb-3 pt-4">
          <CardTitle className="flex items-center gap-2 text-[17px] font-semibold">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/10"><Calendar className="h-[18px] w-[18px] text-amber-600 dark:text-amber-400" /></span>
            {t("recordings.title")}
            {recordings.length > 0 && (
              <Badge variant="secondary" className="text-[13px] py-0 min-h-5 px-1.5 font-normal leading-none">
                {recordings.length}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0 flex-grow overflow-y-auto overflow-x-hidden pr-1">
          {groupedRecordings.length > 0 ? (
            groupedRecordings.map((group) => (
              <div key={group.label}>
                <div className="sticky top-0 z-[5] bg-muted/80 backdrop-blur-sm px-3 py-1">
                  <span className="text-[13px] font-medium text-slate-700 dark:text-slate-300 uppercase tracking-wider">{group.label}</span>
                </div>
                {group.items.map((recording) => {
                  const today = isToday(recording.start_time)
                  const tomorrow = isTomorrow(recording.start_time)
                  const rowBg = today
                    ? "bg-amber-50/50 dark:bg-amber-950/20 hover:bg-amber-100/50 dark:hover:bg-amber-950/30"
                    : tomorrow
                      ? "hover:bg-muted/50"
                      : "hover:bg-muted/50"
                  return (
                    <button
                      key={recording.id}
                      onClick={(event) => {
                        dialogTriggerRef.current = event.currentTarget
                        setSelectedRecording(recording)
                      }}
                      className={`flex min-h-14 w-full cursor-pointer items-start gap-3 border-b border-border/20 px-4 py-2.5 text-left transition-colors ${rowBg}`}
                    >
                      <div className={`rounded-full ${today ? "bg-amber-500/20" : "bg-amber-500/10"} p-1.5 flex-shrink-0`}>
                        <Clock className={`h-3.5 w-3.5 ${today ? "text-amber-600 dark:text-amber-400" : "text-amber-600 dark:text-amber-400"}`} />
                      </div>
                      <div className="min-w-0 flex-1 pr-1">
                        <div className="mb-1 flex min-w-0 flex-wrap items-start gap-1.5">
                          <p className="min-w-0 flex-1 break-words text-sm font-medium leading-snug [overflow-wrap:anywhere]">{recording.title}</p>
                          {recording.dvr_name && (
                            <Badge variant="outline" className="h-auto min-h-5 max-w-full whitespace-normal break-words border-muted-foreground/30 px-1 py-0 text-[13px] font-normal leading-snug text-muted-foreground [overflow-wrap:anywhere]">
                              {recording.dvr_name}
                            </Badge>
                          )}
                        </div>
                        <p className="max-w-full break-words text-[13px] leading-snug text-muted-foreground [overflow-wrap:anywhere]">{recording.channel} - {recording.scheduled_time}</p>
                      </div>
                      <Badge className={`h-auto min-h-5 max-w-[42%] flex-shrink-0 whitespace-normal break-words px-1.5 py-0 text-center text-xs leading-snug [overflow-wrap:anywhere] ${
                        today
                          ? "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-200 hover:bg-amber-200 dark:hover:bg-amber-900/40"
                          : "bg-amber-100 text-amber-900 dark:bg-amber-900/25 dark:text-amber-200 hover:bg-amber-200 dark:hover:bg-amber-900/35"
                       }`}>
                        {today ? t("recordings.today") : t("recordings.scheduled")}
                      </Badge>
                    </button>
                  )
                })}
              </div>
            ))
          ) : (
            <div className="flex flex-col items-center justify-center h-full p-6 gap-2">
              <Calendar className="h-8 w-8 text-muted-foreground/40" />
              <span className="text-sm text-muted-foreground">{t("recordings.empty")}</span>
            </div>
          )}
        </CardContent>
      </Card>
      <RecordingDetailDialog
        recording={selectedRecording}
        open={!!selectedRecording}
        onOpenChange={(open) => {
          if (open) return
          setSelectedRecording(null)
          requestAnimationFrame(() => {
            const trigger = dialogTriggerRef.current
            if (trigger?.isConnected && trigger.getClientRects().length > 0) trigger.focus()
          })
        }}
      />
    </>
  )
}
