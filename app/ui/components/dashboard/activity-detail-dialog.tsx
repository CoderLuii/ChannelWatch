"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/base/dialog"
import { Badge } from "@/components/base/badge"
import { Tv, Play, Video, AlertCircle, Clock } from "lucide-react"
import type { ActivityItem } from "@/lib/types"

interface ActivityDetailDialogProps {
  activity: ActivityItem | null
  open: boolean
  onOpenChange: (open: boolean) => void
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

function getActivityIcon(type: string) {
  switch (type) {
    case "watching_channel":
    case "stream_started":
      return <Tv className="h-5 w-5 text-blue-500" />
    case "watching_vod":
    case "vod_playback":
      return <Play className="h-5 w-5 text-purple-500" />
    case "recording_event":
    case "recording_started":
    case "recording_completed":
    case "recording_scheduled":
    case "recording_stopped":
    case "recording_cancelled":
      return <Video className="h-5 w-5 text-amber-500" />
    case "disk_alert":
      return <AlertCircle className="h-5 w-5 text-red-500" />
    default:
      return <Clock className="h-5 w-5 text-muted-foreground" />
  }
}

function getTypeBadge(type: string) {
  switch (type) {
    case "watching_channel":
      return { label: t("dialog.activity.typeLabel.liveTV"), className: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300" }
    case "watching_vod":
      return { label: t("dialog.activity.typeLabel.vod"), className: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300" }
    case "recording_event":
      return { label: t("dialog.activity.typeLabel.recording"), className: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300" }
    case "disk_alert":
      return { label: t("dialog.activity.typeLabel.diskAlert"), className: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300" }
    default:
      return { label: t("dialog.activity.typeLabel.event"), className: "bg-gray-100 text-gray-700 dark:bg-gray-900 dark:text-gray-300" }
  }
}

function isTestDiskAlert(activity: ActivityItem) {
  return activity.is_test === true || (activity.type === "disk_alert" && activity.title.includes("[TEST]"))
}

function getDisplayTitle(activity: ActivityItem) {
  if (!isTestDiskAlert(activity)) {
    return activity.title
  }

  return activity.title.replace(/\[TEST\]\s*/g, "").trim()
}

function DetailRow({ label, value }: { label: string; value?: string }) {
  if (!value) return null
  return (
    <div className="flex justify-between items-start gap-4 py-1.5 border-b border-border/30 last:border-0">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <span className="text-xs text-right">{value}</span>
    </div>
  )
}

export function ActivityDetailDialog({ activity, open, onOpenChange }: ActivityDetailDialogProps) {
  if (!activity) return null

  const badge = getTypeBadge(activity.type)
  const hasImage = !!activity.image_url
  const extra = activity.extra || {}
  const displayTitle = getDisplayTitle(activity)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm p-0 overflow-hidden">
        {hasImage && (
          <div className="relative w-full h-40 bg-muted">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
            <img
              src={activity.image_url}
              alt=""
              className="w-full h-full object-cover"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-background/90 to-transparent" />
            <div className="absolute bottom-3 left-4 right-4">
              <Badge className={`${badge.className} text-xs`}>{badge.label}</Badge>
            </div>
          </div>
        )}
        <div className={hasImage ? "px-4 pb-4 pt-1" : "p-4"}>
          <DialogHeader className={hasImage ? "" : "pb-2"}>
            {!hasImage && (
              <div className="flex items-center gap-2 mb-2">
                {getActivityIcon(activity.type)}
                <Badge className={`${badge.className} text-xs`}>{badge.label}</Badge>
              </div>
            )}
            <DialogTitle className="text-base">{displayTitle}</DialogTitle>
            <DialogDescription className="text-xs">
              {formatFullTimestamp(activity.timestamp)}
              {activity.dvr_name && t("dialog.activity.onDvr", { name: activity.dvr_name })}
            </DialogDescription>
          </DialogHeader>

          <div className="mt-3 space-y-0">
            {activity.type === "watching_channel" && (
              <>
                <DetailRow label={t("dialog.activity.field.program")} value={activity.program_title} />
                <DetailRow label={t("dialog.activity.field.channel")} value={activity.channel_name ? `${activity.channel_name}${activity.channel_number ? ` (Ch ${activity.channel_number})` : ""}` : undefined} />
                <DetailRow label={t("dialog.activity.field.device")} value={activity.device_name} />
                <DetailRow label={t("dialog.activity.field.ipAddress")} value={activity.device_ip} />
                <DetailRow label={t("dialog.activity.field.source")} value={activity.stream_source} />
                <DetailRow label={t("dialog.activity.field.activeStreams")} value={extra.stream_count ? String(extra.stream_count) : undefined} />
              </>
            )}

            {activity.type === "watching_vod" && (
              <>
                <DetailRow label={t("dialog.activity.field.title")} value={activity.program_title} />
                <DetailRow label={t("dialog.activity.field.episode")} value={extra.episode_title} />
                <DetailRow label={t("dialog.activity.field.duration")} value={extra.duration} />
                <DetailRow label={t("dialog.activity.field.year")} value={extra.year} />
                <DetailRow label={t("dialog.activity.field.device")} value={activity.device_name} />
                <DetailRow label={t("dialog.activity.field.ipAddress")} value={activity.device_ip} />
                {extra.summary && (
                  <div className="pt-2 mt-2 border-t border-border/30">
                    <p className="text-xs text-muted-foreground leading-relaxed">{extra.summary}</p>
                  </div>
                )}
              </>
            )}

            {activity.type === "recording_event" && (
              <>
                <DetailRow label={t("dialog.activity.field.program")} value={activity.program_title} />
                <DetailRow label={t("dialog.activity.field.channel")} value={activity.channel_name} />
                <DetailRow label={t("dialog.activity.field.status")} value={extra.recording_type} />
                <DetailRow label={t("dialog.activity.field.duration")} value={extra.duration} />
              </>
            )}

            {activity.type === "disk_alert" && (
              <>
                <DetailRow label={t("dialog.activity.field.path")} value={extra.path} />
              </>
            )}

            {!activity.channel_name && !activity.program_title && !extra.path && (
              <p className="text-sm text-muted-foreground">{activity.message}</p>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
