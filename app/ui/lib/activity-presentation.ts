import type { LucideIcon } from "lucide-react"
import { AlertCircle, Calendar, CheckCircle, SkipForward, Square, Video, X } from "lucide-react"

export type RecordingEventPresentation = {
  icon: LucideIcon
  colorClasses: string
}

export function recordingEventPresentation(message?: string): RecordingEventPresentation {
  const value = String(message ?? "").trim().toLocaleLowerCase()

  if (value.startsWith("scheduled:")) {
    return { icon: Calendar, colorClasses: "bg-amber-500/20 text-amber-600 dark:text-amber-400" }
  }
  if (value.startsWith("failed:") || value.startsWith("did not start:")) {
    return { icon: AlertCircle, colorClasses: "bg-red-500/20 text-red-600 dark:text-red-400" }
  }
  if (value.startsWith("cancelled:")) {
    return { icon: X, colorClasses: "bg-red-500/20 text-red-600 dark:text-red-400" }
  }
  if (value.startsWith("skipped:")) {
    return { icon: SkipForward, colorClasses: "bg-amber-500/20 text-amber-700 dark:text-amber-300" }
  }
  if (value.startsWith("interrupted:") || value.startsWith("stopped:")) {
    return { icon: Square, colorClasses: "bg-slate-500/20 text-slate-600 dark:text-slate-300" }
  }
  if (value.startsWith("completed")) {
    return { icon: CheckCircle, colorClasses: "bg-purple-500/20 text-purple-600 dark:text-purple-400" }
  }
  if (value.startsWith("recording(") || value.startsWith("recording (")) {
    return { icon: Video, colorClasses: "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400" }
  }
  return { icon: Video, colorClasses: "bg-slate-500/20 text-slate-600 dark:text-slate-300" }
}
