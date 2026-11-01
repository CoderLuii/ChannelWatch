// Shared client-side pause after the API answers 429. Polling timers check
// isRateLimited() and skip their tick instead of adding to the overload, and
// views can show a "busy" state rather than treating it as an outage.

const DEFAULT_RETRY_AFTER_SECONDS = 15
const MAX_RETRY_AFTER_SECONDS = 120

let pausedUntil = 0

export function parseRetryAfterSeconds(value: string | null | undefined, now: number = Date.now()): number {
  if (!value) return DEFAULT_RETRY_AFTER_SECONDS
  const trimmed = value.trim()
  const seconds = Number(trimmed)
  let result: number
  if (Number.isFinite(seconds)) {
    result = seconds
  } else {
    const date = Date.parse(trimmed)
    result = Number.isNaN(date) ? DEFAULT_RETRY_AFTER_SECONDS : Math.ceil((date - now) / 1000)
  }
  return Math.min(MAX_RETRY_AFTER_SECONDS, Math.max(1, Math.ceil(result)))
}

export function noteRateLimited(response: Pick<Response, "status" | "headers">, now: number = Date.now()): boolean {
  if (response.status !== 429) return false
  const seconds = parseRetryAfterSeconds(response.headers?.get?.("Retry-After"), now)
  pausedUntil = Math.max(pausedUntil, now + seconds * 1000)
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function" && typeof CustomEvent === "function") {
    window.dispatchEvent(new CustomEvent("channelwatch-rate-limited", { detail: { until: pausedUntil } }))
  }
  return true
}

export function rateLimitRemainingMs(now: number = Date.now()): number {
  return Math.max(0, pausedUntil - now)
}

export function isRateLimited(now: number = Date.now()): boolean {
  return rateLimitRemainingMs(now) > 0
}

export function resetRateLimitForTests(): void {
  pausedUntil = 0
}
