"use client"

import React, { useState, useMemo } from "react"
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
      <Card className="flex flex-col h-[300px] sm:h-[350px] md:h-[420px] max-w-full overflow-hidden">
        <CardHeader className="pb-2 flex-shrink-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <Calendar className="h-4 w-4 text-primary" />
            {t("recordings.title")}
            {recordings.length > 0 && (
              <Badge variant="secondary" className="text-[10px] py-0 h-4 px-1.5 font-normal leading-none">
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
                  <span className="text-[10px] font-medium text-slate-700 dark:text-slate-300 uppercase tracking-wider">{group.label}</span>
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
                      onClick={() => setSelectedRecording(recording)}
                      className={`flex items-center gap-2 p-2 transition-colors border-b border-border/20 w-full text-left cursor-pointer ${rowBg}`}
                    >
                      <div className={`rounded-full ${today ? "bg-amber-500/20" : "bg-amber-500/10"} p-1.5 flex-shrink-0`}>
                        <Clock className={`h-3.5 w-3.5 ${today ? "text-amber-600 dark:text-amber-400" : "text-amber-600 dark:text-amber-400"}`} />
                      </div>
                      <div className="flex-1 min-w-0 pr-1 overflow-hidden">
                        <div className="flex items-center gap-1.5 mb-1">
                          <p className="text-sm font-medium leading-none truncate">{recording.title}</p>
                          {recording.dvr_name && (
                            <Badge variant="outline" className="flex-shrink-0 text-[9px] py-0 h-3.5 px-1 font-normal text-muted-foreground border-muted-foreground/30">
                              {recording.dvr_name}
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground truncate max-w-full">{recording.channel} - {recording.scheduled_time}</p>
                      </div>
                      <Badge className={`flex-shrink-0 text-xs py-0 h-5 px-1.5 ${
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
        onOpenChange={(open) => { if (!open) setSelectedRecording(null) }}
      />
    </>
  )
}
