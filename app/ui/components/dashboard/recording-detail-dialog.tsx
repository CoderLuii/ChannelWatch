"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/base/dialog"
import { StatusBadge } from "@/components/base/status-badge"
import { Calendar } from "lucide-react"
import { DetailRow } from "@/components/dashboard/activity-detail-dialog"
import { isRecordingActive } from "@/components/dashboard/upcoming-recordings-list"
import { formatCountdown } from "@/lib/relative-time"

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

interface RecordingDetailDialogProps {
  recording: Recording | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onCloseAutoFocus?: (event: Event) => void
}

export function getRecordingArtworkNote(recording: Recording | null): string | null {
  if (!recording || recording.image) return null
  const artworkExhausted =
    recording.artwork_fallback_exhausted === true ||
    (recording.artwork_fallback_exhausted === undefined && !recording.image)
  return artworkExhausted ? t("dialog.recording.noArtwork") : null
}

function getCountdown(startTime: number): string {
  if (!Number.isFinite(startTime) || startTime <= 0) return t("recordings.unknownTime")
  return formatCountdown(startTime) || t("dialog.recording.recordingNow")
}

export function RecordingDetailDialog({ recording: current, open, onOpenChange, onCloseAutoFocus }: RecordingDetailDialogProps) {
  const [failedArtwork, setFailedArtwork] = React.useState("")
  // The sheet keeps showing the last item while it slides away.
  const last = React.useRef(current)
  if (current) last.current = current
  const recording = current ?? last.current

  if (!recording) return null

  const hasImage = Boolean(recording.image && recording.image !== failedArtwork)
  const countdown = getCountdown(recording.start_time)
  const noArtworkNote = getRecordingArtworkNote(recording)
  const active = isRecordingActive(recording)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onCloseAutoFocus={onCloseAutoFocus} className="max-h-[calc(100dvh-2rem)] min-w-0 max-w-sm overflow-x-hidden overflow-y-auto p-0">
        <div className="min-w-0 p-5 max-sm:pt-2">
          <DialogHeader className="min-w-0 pr-10">
            <div className="flex min-w-0 flex-wrap items-start gap-3">
              {hasImage ? (
                <span aria-hidden="true" className="flex h-[108px] w-[72px] shrink-0 items-center justify-center overflow-hidden rounded-thumb bg-fill">
                  {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
                  <img src={recording.image} alt="" onError={() => setFailedArtwork(recording.image)} className="h-full w-full object-cover" />
                </span>
              ) : null}
              <div className="min-w-[min(100%,9rem)] flex-1 space-y-1.5">
                <div className="flex items-center gap-2">
                  <Calendar className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <StatusBadge variant="neutral">{active ? t("recordings.active") : t("dialog.recording.badge")}</StatusBadge>
                </div>
                <DialogTitle className="min-w-0 break-words text-left text-base">{recording.title}</DialogTitle>
                <DialogDescription className="min-w-0 break-words text-left text-xs">
                  {recording.channel}
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>

          {noArtworkNote && (
            <p className="mt-2 min-w-0 break-words text-xs text-muted-foreground">
              {noArtworkNote}
            </p>
          )}

          <div className="mt-3 min-w-0 space-y-0">
            <DetailRow label={t("dialog.recording.field.scheduled")} value={recording.scheduled_time} />
            <DetailRow label={t("dialog.recording.field.channel")} value={recording.channel} />
            <DetailRow label={t("dialog.recording.field.dvr")} value={recording.dvr_name} />
            <DetailRow label={t("dialog.recording.field.countdown")} value={countdown} valueClassName="tabular-nums" />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
