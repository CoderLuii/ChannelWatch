import type { LucideIcon } from "lucide-react"
import { AlertCircle, Calendar, CheckCircle, HardDrive, HeartPulse, SkipForward, Square, Video, WifiOff, X } from "lucide-react"

import { t } from "@/lib/i18n"
import { formatDiskSizeFromGB } from "@/lib/utils"

export type RecordingEventPresentation = {
  icon: LucideIcon
  colorClasses: string
}

/** Routine events stay in the text color. Tints mean something needs attention. */
export const ROUTINE_ACTIVITY_CLASSES = "bg-transparent text-muted-foreground"

/** A disk alert keeps the level it was recorded with. Older rows have none and read as a warning. */
export function diskAlertColorClasses(extra?: { severity?: unknown } | null): string {
  return extra?.severity === "critical" ? "bg-danger-bg text-danger-fg" : "bg-warning-bg text-warning-fg"
}

/** Alert titles lead with an emoji for push notifications; screens do not need it. */
export function stripLeadingEmoji(title: string): string {
  return title.replace(/^\p{Extended_Pictographic}\uFE0F?\s*/u, "")
}

export function recordingEventPresentation(message?: string): RecordingEventPresentation {
  const value = String(message ?? "").trim().toLocaleLowerCase()

  if (value.startsWith("scheduled:")) {
    return { icon: Calendar, colorClasses: ROUTINE_ACTIVITY_CLASSES }
  }
  if (value.startsWith("failed:") || value.startsWith("did not start:")) {
    return { icon: AlertCircle, colorClasses: "bg-danger-bg text-danger-fg" }
  }
  if (value.startsWith("cancelled:")) {
    return { icon: X, colorClasses: ROUTINE_ACTIVITY_CLASSES }
  }
  if (value.startsWith("skipped:")) {
    return { icon: SkipForward, colorClasses: "bg-warning-bg text-warning-fg" }
  }
  if (value.startsWith("interrupted:") || value.startsWith("stopped:")) {
    return { icon: Square, colorClasses: ROUTINE_ACTIVITY_CLASSES }
  }
  if (value.startsWith("completed")) {
    return { icon: CheckCircle, colorClasses: ROUTINE_ACTIVITY_CLASSES }
  }
  if (value.startsWith("recording(") || value.startsWith("recording (")) {
    return { icon: Video, colorClasses: ROUTINE_ACTIVITY_CLASSES }
  }
  return { icon: Video, colorClasses: ROUTINE_ACTIVITY_CLASSES }
}

export type HealthEventPresentation = RecordingEventPresentation & {
  labelKey: string
  tone: "outage" | "recovery"
}

// Outage and recovery events need different icons, colors and labels so a
// resolved problem never reads like a new one.
export function healthEventPresentation(type?: string): HealthEventPresentation | null {
  switch (type) {
    case "dvr_unreachable":
      return { icon: WifiOff, colorClasses: "bg-danger-bg text-danger-fg", labelKey: "type.dvrUnreachable", tone: "outage" }
    case "dvr_recovered":
      return { icon: HeartPulse, colorClasses: "bg-success-bg text-success-fg", labelKey: "type.dvrRecovered", tone: "recovery" }
    case "disk_recovered":
      return { icon: HardDrive, colorClasses: "bg-success-bg text-success-fg", labelKey: "type.diskRecovered", tone: "recovery" }
    default:
      return null
  }
}

type DiskActivityLike = {
  type: string
  title: string
  dvr_id?: string
}

// Older history can hold the same disk warning recorded several times in a
// row. Show one entry per unchanged disk level: an alert is dropped when the
// previous disk entry for the same DVR (in list order) is the same alert.
export function collapseRepeatedDiskAlerts<T extends DiskActivityLike>(items: T[]): T[] {
  const lastDiskKey = new Map<string, string>()
  const result: T[] = []
  for (const item of items) {
    if (item.type !== "disk_alert" && item.type !== "disk_recovered") {
      result.push(item)
      continue
    }
    const dvr = item.dvr_id ?? ""
    const key = `${item.type}:${item.title}`
    if (item.type === "disk_alert" && lastDiskKey.get(dvr) === key) {
      continue
    }
    lastDiskKey.set(dvr, key)
    result.push(item)
  }
  return result
}

/** "Completed: Show on HBO for Fri" gives the word and the show, "Recording(1): Show" gives Recording. */
export function recordingEventParts(message?: string, channel?: string): { word: string; subject: string } | null {
  const match = /^\s*([^:(]+?)\s*(?:\(\d+\))?\s*:\s*(.+)$/.exec(String(message ?? ""))
  if (!match) return null
  const at = channel ? match[2].lastIndexOf(` on ${channel}`) : -1
  return { word: match[1], subject: (at > 0 ? match[2].slice(0, at) : match[2]).trim() }
}

/** Stored figures carry two decimals; a sentence reads the same size the Disk tile shows. */
function diskFigure(value: unknown): string {
  if (typeof value !== "string") return ""
  const gb = value.match(/^(\d+(?:\.\d+)?) GB$/)
  return gb ? formatDiskSizeFromGB(Number(gb[1])) : value
}

/** A disk alert's level as it stood then. Stored rows without figures keep their own sentence. */
export function formatDiskAlertMessage(extra: Record<string, unknown> | null | undefined, raw: string): string {
  const free = diskFigure(extra?.free)
  const total = diskFigure(extra?.total)
  return free && total ? t("activity.diskAtTheTime", { free, total }) : raw
}

type ActivityRowInput = {
  type: string
  title: string
  message: string
  program_title?: string
  channel_name?: string
  device_name?: string
  extra?: Record<string, unknown> | null
}

/**
 * One grammar for every activity row: what it was, then who or what happened to it.
 * The show name leads when there is one, because that is what the row is about.
 */
export function activityRowText(activity: ActivityRowInput, displayTitle: string, typeLabel: string): { title: string; detail: string | null } {
  if (activity.type === "disk_alert" || activity.type === "disk_recovered") {
    const free = diskFigure(activity.extra?.free)
    const total = diskFigure(activity.extra?.total)
    return { title: displayTitle || typeLabel, detail: free && total ? t("dashboard.verdict.diskDetail", { free, total }) : activity.message }
  }
  const recording = activity.type === "recording_event" ? recordingEventParts(activity.message, activity.channel_name) : null
  const title = activity.program_title || recording?.subject || displayTitle || typeLabel
  const device = activity.device_name && activity.device_name !== "Unknown device" ? activity.device_name : null
  if (activity.type === "watching_channel" || activity.type === "stream_started") {
    return { title: activity.program_title || activity.channel_name || title, detail: device ?? activity.channel_name ?? null }
  }
  if (recording) return { title, detail: recording.word }
  if (device) return { title, detail: device }
  return { title, detail: activity.message && activity.message !== title ? activity.message : null }
}
