import { t } from "@/lib/i18n"

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 365 * 24 * 60 * 60],
  ["month", 30 * 24 * 60 * 60],
  ["week", 7 * 24 * 60 * 60],
  ["day", 24 * 60 * 60],
  ["hour", 60 * 60],
  ["minute", 60],
]

/**
 * Formats an ISO timestamp as a short relative phrase such as "in 3 hours"
 * or "2 days ago". Returns the original value when it cannot be parsed so
 * callers never show "Invalid Date".
 */
export function formatRelativeTime(value: string | null | undefined, now: number = Date.now(), locale?: string): string {
  if (!value) return ""
  const time = Date.parse(value)
  if (Number.isNaN(time)) return value
  const seconds = Math.round((time - now) / 1000)
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" })
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit)
    }
  }
  return formatter.format(0, "minute")
}

/** Full local date and time, used as the hover title next to a relative date. */
export function formatAbsoluteTime(value: string | null | undefined, locale?: string): string {
  if (!value) return ""
  const time = Date.parse(value)
  if (Number.isNaN(time)) return value
  return new Date(time).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" })
}

/**
 * "in 9h 32m", "in 1d 3h", "in 5d". Empty once the time has passed, so callers say
 * what that means for them. Takes epoch seconds, as recordings report them.
 */
export function formatCountdown(startSeconds: number, now: number = Date.now()): string {
  if (!Number.isFinite(startSeconds) || startSeconds <= 0) return ""
  const minutes = Math.floor((startSeconds * 1000 - now) / 60000)
  if (minutes <= 0) return ""
  const hours = Math.floor(minutes / 60)
  if (hours >= 48) return `in ${Math.floor(hours / 24)}d`
  if (hours >= 24) return `in ${Math.floor(hours / 24)}d ${hours % 24}h`
  return hours > 0 ? `in ${hours}h ${minutes % 60}m` : `in ${minutes % 60}m`
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000

/** Age of an event for a list row. A bad timestamp reads as a dash, a stale one as its date. */
export function formatEventAge(timestamp: string | null | undefined, now: number = Date.now()): string {
  const time = timestamp ? Date.parse(timestamp) : Number.NaN
  if (!Number.isFinite(time) || time <= 0) return "–"
  const age = now - time
  if (age >= WEEK_MS) return new Date(time).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
  const seconds = Math.floor(age / 1000)
  if (seconds < 60) return t("activity.justNow")
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return t("activity.minutesAgo", { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t("activity.hoursAgo", { n: hours })
  return t("activity.daysAgo", { n: Math.floor(hours / 24) })
}

/** "Updated just now", "Updated 2 min ago", or the same age as "Last good data" after a failure. */
export function formatFreshness(lastSuccessAt: Date | null, failing: boolean, now: number = Date.now()): string {
  if (!lastSuccessAt) return failing ? t("dashboard.freshness.none") : "–"
  const prefix = failing ? "dashboard.freshness.stale" : "dashboard.freshness."
  const key = (unit: string) => (failing ? `${prefix}${unit}` : `${prefix}${unit.charAt(0).toLowerCase()}${unit.slice(1)}`)
  const seconds = Math.max(0, Math.floor((now - lastSuccessAt.getTime()) / 1000))
  if (seconds < 60) return t(key("JustNow"))
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return t(key("Minutes"), { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t(key("Hours"), { n: hours })
  return t(key("Days"), { n: Math.floor(hours / 24) })
}
