"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/base/dialog"
import { StatusBadge, type StatusTone } from "@/components/base/status-badge"
import { Tv, Play, Video, AlertCircle, Clock } from "lucide-react"
import type { ActivityItem } from "@/lib/types"
import { activityRowText, formatDiskAlertMessage, healthEventPresentation, stripLeadingEmoji } from "@/lib/activity-presentation"
import { cn } from "@/lib/utils"

interface ActivityDetailDialogProps {
  activity: ActivityItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onCloseAutoFocus?: (event: Event) => void
}

function formatFullTimestamp(timestamp: string): string {
  try {
    const date = new Date(timestamp)
    return date.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  } catch {
    return timestamp
  }
}

function formatDiskFree(extra: Record<string, unknown>): string | undefined {
  if (typeof extra.free !== "string" || !extra.free) return undefined
  const total = typeof extra.total === "string" && extra.total ? ` / ${extra.total}` : ""
  const pct = typeof extra.free_percentage === "number" ? ` (${extra.free_percentage.toFixed(1)}%)` : ""
  return `${extra.free}${total}${pct}`
}

function getActivityIcon(type: string) {
  const health = healthEventPresentation(type)
  if (health) {
    const Icon = health.icon
    return <Icon className={health.tone === "outage" ? "size-4 text-danger" : "size-4 text-success"} />
  }
  switch (type) {
    case "watching_channel":
    case "stream_started":
      return <Tv className="size-4 text-muted-foreground" />
    case "watching_vod":
    case "vod_playback":
      return <Play className="size-4 text-muted-foreground" />
    case "recording_event":
    case "recording_started":
    case "recording_completed":
    case "recording_scheduled":
    case "recording_stopped":
    case "recording_cancelled":
      return <Video className="size-4 text-muted-foreground" />
    case "disk_alert":
      return <AlertCircle className="size-4 text-muted-foreground" />
    default:
      return <Clock className="size-4 text-muted-foreground" />
  }
}

function getTypeBadge(activity: ActivityItem): { label: string; tone: StatusTone } {
  const health = healthEventPresentation(activity.type)
  if (health) {
    return { label: t(health.labelKey), tone: health.tone === "outage" ? "danger" : "success" }
  }
  switch (activity.type) {
    case "watching_channel":
    case "stream_started":
      return { label: t("dialog.activity.typeLabel.liveTV"), tone: "neutral" }
    case "watching_vod":
    case "vod_playback":
      return { label: t("dialog.activity.typeLabel.vod"), tone: "neutral" }
    case "recording_event":
    case "recording_started":
    case "recording_completed":
    case "recording_scheduled":
    case "recording_stopped":
    case "recording_cancelled":
      return { label: t("dialog.activity.typeLabel.recording"), tone: "neutral" }
    case "disk_alert":
      return { label: t("dialog.activity.typeLabel.diskAlert"), tone: activity.extra?.severity === "critical" ? "danger" : "warning" }
    default:
      return { label: t("dialog.activity.typeLabel.event"), tone: "neutral" }
  }
}

function isTestDiskAlert(activity: ActivityItem) {
  return activity.is_test === true || (activity.type === "disk_alert" && activity.title.includes("[TEST]"))
}

function getDisplayTitle(activity: ActivityItem) {
  if (!isTestDiskAlert(activity)) {
    return stripLeadingEmoji(activity.title)
  }

  return stripLeadingEmoji(activity.title.replace(/\[TEST\]\s*/g, "").trim())
}

// The value drops under its label once large text leaves it too little room, so words stay whole.
export function DetailRow({ label, value, valueClassName }: { label: string; value?: string; valueClassName?: string }) {
  if (!value) return null
  return (
    <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-4 gap-y-0.5 border-b border-hairline py-1.5 last:border-0">
      <span className="min-w-0 break-words text-xs text-muted-foreground">{label}</span>
      <span className={cn("min-w-[min(100%,6rem)] flex-1 break-words text-right text-xs", valueClassName)}>{value}</span>
    </div>
  )
}

export function ActivityDetailDialog({ activity: current, open, onOpenChange, onCloseAutoFocus }: ActivityDetailDialogProps) {
  const [failedArtwork, setFailedArtwork] = React.useState("")
  // The sheet keeps showing the last item while it slides away.
  const last = React.useRef(current)
  if (current) last.current = current
  const activity = current ?? last.current

  if (!activity) return null

  const badge = getTypeBadge(activity)
  const hasImage = Boolean(activity.image_url && activity.image_url !== failedArtwork)
  const extra = activity.extra || {}
  const displayTitle = activity.program_title || activityRowText(activity, getDisplayTitle(activity), badge.label).title
  const isWatchingChannel = activity.type === "watching_channel" || activity.type === "stream_started"
  const isWatchingVod = activity.type === "watching_vod" || activity.type === "vod_playback"
  const isRecording = activity.type.startsWith("recording_")
  const isDisk = activity.type === "disk_alert" || activity.type === "disk_recovered"
  const hasParsedFields = isWatchingChannel || isWatchingVod || isRecording || Boolean(activity.channel_name || activity.device_name || extra.path)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onCloseAutoFocus={onCloseAutoFocus} className="max-h-[calc(100dvh-2rem)] min-w-0 max-w-sm overflow-x-hidden overflow-y-auto p-0">
        <div className="min-w-0 p-5 max-sm:pt-2">
          <DialogHeader className="min-w-0 pr-10">
            <div className="flex min-w-0 flex-wrap items-start gap-3">
              {hasImage ? (
                <span aria-hidden="true" className="flex h-[108px] w-[72px] shrink-0 items-center justify-center overflow-hidden rounded-thumb bg-fill">
                  {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
                  <img src={activity.image_url} alt="" onError={() => setFailedArtwork(activity.image_url || "")} className="h-full w-full object-contain" />
                </span>
              ) : null}
              <div className="min-w-[min(100%,9rem)] flex-1 space-y-1.5">
                <div className="flex items-center gap-2">
                  {getActivityIcon(activity.type)}
                  <StatusBadge variant={badge.tone}>{badge.label}</StatusBadge>
                </div>
                <DialogTitle className="min-w-0 break-words text-left text-base">{displayTitle}</DialogTitle>
                <DialogDescription className="min-w-0 break-words text-left text-xs">
                  {formatFullTimestamp(activity.timestamp)}
                  {activity.dvr_name && t("dialog.activity.onDvr", { name: activity.dvr_name })}
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>

          <div className="mt-3 min-w-0 space-y-0">
            {isWatchingChannel && (
              <>
                <DetailRow label={t("dialog.activity.field.channel")} value={activity.channel_name ? `${activity.channel_name}${activity.channel_number ? ` (Ch ${activity.channel_number})` : ""}` : undefined} />
                <DetailRow label={t("dialog.activity.field.device")} value={activity.device_name} />
                <DetailRow label={t("dialog.activity.field.ipAddress")} value={activity.device_ip} />
                <DetailRow label={t("dialog.activity.field.source")} value={activity.stream_source} />
                <DetailRow label={t("dialog.activity.field.activeStreams")} value={extra.stream_count ? String(extra.stream_count) : undefined} />
              </>
            )}

            {isWatchingVod && (
              <>
                <DetailRow label={t("dialog.activity.field.episode")} value={extra.episode_title} />
                <DetailRow label={t("dialog.activity.field.duration")} value={extra.duration} />
                <DetailRow label={t("dialog.activity.field.year")} value={extra.year} />
                <DetailRow label={t("dialog.activity.field.device")} value={activity.device_name} />
                <DetailRow label={t("dialog.activity.field.ipAddress")} value={activity.device_ip} />
                {extra.summary && (
                  <div className="pt-2 mt-2 border-t border-hairline">
                    <p className="break-words text-xs leading-relaxed text-muted-foreground">{extra.summary}</p>
                  </div>
                )}
              </>
            )}

            {isRecording && (
              <>
                <DetailRow label={t("dialog.activity.field.channel")} value={activity.channel_name} />
                <DetailRow label={t("dialog.activity.field.status")} value={extra.recording_type} />
                <DetailRow label={t("dialog.activity.field.reason")} value={extra.reason} />
                <DetailRow label={t("dialog.activity.field.duration")} value={extra.duration} />
              </>
            )}

            {isDisk && (
              <>
                <DetailRow label={t("dialog.activity.field.freeSpace")} value={formatDiskFree(extra)} />
                <DetailRow label={t("dialog.activity.field.usedSpace")} value={extra.used} />
                <DetailRow label={t("dialog.activity.field.path")} value={extra.path} />
              </>
            )}

            {isDisk ? (
              <p className="break-words pt-2 text-sm text-muted-foreground">{formatDiskAlertMessage(extra, activity.message)}</p>
            ) : !hasParsedFields ? (
              <p className="break-words text-sm text-muted-foreground">{activity.message}</p>
            ) : null}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
