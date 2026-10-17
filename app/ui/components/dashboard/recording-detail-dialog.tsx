"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/base/dialog"
import { Badge } from "@/components/base/badge"
import { Calendar } from "lucide-react"

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

interface RecordingDetailDialogProps {
  recording: Recording | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function getRecordingArtworkNote(recording: Recording | null): string | null {
  if (!recording || recording.image) return null
  const artworkExhausted =
    recording.artwork_fallback_exhausted === true ||
    (recording.artwork_fallback_exhausted === undefined && !recording.image)
  return artworkExhausted ? t("dialog.recording.noArtwork") : null
}

function getCountdown(startTime: number): string {
  const diffMin = Math.floor((startTime * 1000 - Date.now()) / 60000)
  if (diffMin <= 0) return t("dialog.recording.recordingNow")
  const h = Math.floor(diffMin / 60)
  const m = diffMin % 60
  return h > 0 ? `in ${h}h ${m}m` : `in ${m}m`
}

function isToday(timestamp: number): boolean {
  const date = new Date(timestamp * 1000)
  const now = new Date()
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()
}

export function RecordingDetailDialog({ recording, open, onOpenChange }: RecordingDetailDialogProps) {
  const [failedArtwork, setFailedArtwork] = React.useState("")

  if (!recording) return null

  const hasImage = Boolean(recording.image && recording.image !== failedArtwork)
  const countdown = getCountdown(recording.start_time)
  const recordingToday = isToday(recording.start_time)
  const noArtworkNote = getRecordingArtworkNote(recording)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] min-w-0 max-w-sm overflow-x-hidden overflow-y-auto p-0">
        {hasImage && (
          <div className="relative h-48 min-w-0 w-full bg-muted">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
            <img
              src={recording.image}
              alt=""
              onError={() => setFailedArtwork(recording.image)}
              className="w-full h-full object-cover"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-background/90 to-transparent" />
            <div className="absolute bottom-3 left-4 right-4 flex items-center justify-between">
              <Badge className={recordingToday
                ? "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300 text-xs"
                : "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300 text-xs"
              }>
                {t("dialog.recording.badge")}
              </Badge>
              <span className="text-xs text-white/90 font-medium">{countdown}</span>
            </div>
          </div>
        )}
        <div className={hasImage ? "min-w-0 px-4 pb-4 pt-2" : "min-w-0 p-4"}>
          <DialogHeader className="min-w-0">
            {!hasImage && (
              <div className="mb-2 flex min-h-11 items-start gap-2 pr-14 pt-0.5">
                <Calendar className="h-5 w-5 text-amber-500" />
                <Badge className={recordingToday
                  ? "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300 text-xs"
                  : "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300 text-xs"
                }>
                  {t("dialog.recording.badge")}
                </Badge>
                <span className="text-xs text-muted-foreground ml-auto">{countdown}</span>
              </div>
            )}
            <DialogTitle className={`min-w-0 [overflow-wrap:anywhere] text-left text-base ${hasImage ? "" : "mt-1"}`}>{recording.title}</DialogTitle>
            <DialogDescription className="min-w-0 [overflow-wrap:anywhere] text-left text-[13px]">
              {recording.channel}
            </DialogDescription>
          </DialogHeader>

          {noArtworkNote && (
            <p className="mt-2 min-w-0 [overflow-wrap:anywhere] text-xs text-muted-foreground">
              {noArtworkNote}
            </p>
          )}

          <div className="mt-3 min-w-0 space-y-0">
            <div className="flex min-w-0 items-start justify-between gap-4 border-b border-border/30 py-1.5">
              <span className="shrink-0 text-[13px] text-muted-foreground">{t("dialog.recording.field.scheduled")}</span>
              <span className="min-w-0 flex-1 [overflow-wrap:anywhere] text-right text-[13px]">{recording.scheduled_time}</span>
            </div>
            <div className="flex min-w-0 items-start justify-between gap-4 border-b border-border/30 py-1.5">
              <span className="shrink-0 text-[13px] text-muted-foreground">{t("dialog.recording.field.channel")}</span>
              <span className="min-w-0 flex-1 [overflow-wrap:anywhere] text-right text-[13px]">{recording.channel}</span>
            </div>
            {recording.dvr_name && (
              <div className="flex min-w-0 items-start justify-between gap-4 border-b border-border/30 py-1.5">
                <span className="shrink-0 text-[13px] text-muted-foreground">{t("dialog.recording.field.dvr")}</span>
                <span className="min-w-0 flex-1 [overflow-wrap:anywhere] text-right text-[13px]">{recording.dvr_name}</span>
              </div>
            )}
            {!hasImage && (
              <div className="flex min-w-0 items-start justify-between gap-4 py-1.5">
                <span className="shrink-0 text-[13px] text-muted-foreground">{t("dialog.recording.field.countdown")}</span>
                <span className="min-w-0 flex-1 [overflow-wrap:anywhere] text-right text-[13px]">{countdown}</span>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
