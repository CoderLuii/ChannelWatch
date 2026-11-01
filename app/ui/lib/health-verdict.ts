import type { FailureKind } from "@/lib/error-catalog"
import { t } from "@/lib/i18n"

export type HealthVerdictTone = "healthy" | "warning" | "danger" | "pending"

export interface HealthVerdictDvr {
  connected: boolean
  enabled?: boolean
  monitoring_ready?: boolean
  monitoring_status?: string
  monitoring_reason?: string | null
  name?: string
}

export interface HealthVerdictInput {
  loaded: boolean
  coreStatus: string
  dvrs: HealthVerdictDvr[]
  diskSeverity?: "normal" | "warning" | "critical" | string
  /** Already formatted: "116 GB left of 1.82 TB". */
  diskDetail?: string
  hasFailedFetches: boolean
  /** Why the system read failed. Only an outage blames the container. */
  failureKind?: FailureKind
  /** Providers with a destination. Undefined until settings have loaded. */
  notificationServices?: number
}

export type HealthVerdictActionKind = "diagnostics" | "disk" | "notifications" | "add-dvr"

export interface HealthVerdictAction {
  kind: HealthVerdictActionKind
  label: string
}

export interface HealthVerdict {
  tone: HealthVerdictTone
  headline: string
  details: string[]
  /** Other problems the headline is standing in front of. */
  more?: number
  action?: HealthVerdictAction
}

type Issue = Omit<HealthVerdict, "tone" | "more"> & { rank: 3 | 2 | 1 }

const RANK_TONE = { 3: "danger", 2: "warning", 1: "pending" } as const

const CORE_PENDING = new Set(["Loading", "Starting"])
const MONITORING_PENDING = new Set(["starting", "reconnecting", "missing"])

/** Supervisor's state names mean nothing to the reader, and "Error" only says the state could not be read. */
export function coreStatusLabel(status: string): string {
  if (status === "Error") return t("common.unknown")
  if (status === "Backoff") return t("status.core.retrying")
  if (status === "Exited" || status === "Fatal") return t("status.core.notRunning")
  return status
}

const diagnose = (): HealthVerdictAction => ({ kind: "diagnostics", label: t("status.diagnose") })

/** A DVR that is off the network already has its own issue; its monitor being stale adds nothing. */
function monitoringIssue(dvr: HealthVerdictDvr): Issue | null {
  if (!dvr.connected || dvr.monitoring_status === "disabled" || dvr.monitoring_ready !== false) return null
  const name = dvr.name ?? t("status.dvrConnection")
  const status = dvr.monitoring_status ?? ""
  if (status === "dead") {
    return { rank: 3, headline: t("status.banner.stopped", { name }), details: [dvr.monitoring_reason || t("status.banner.defaultDetail")], action: diagnose() }
  }
  if (MONITORING_PENDING.has(status)) {
    const key = status === "starting" ? "status.banner.starting" : "status.banner.waiting"
    return { rank: 1, headline: t(key, { name }), details: [t("status.banner.startingDetail")] }
  }
  const key = status === "stale" ? "status.banner.stale" : "status.banner.degraded"
  return { rank: 2, headline: t(key, { name }), details: [dvr.monitoring_reason || t("status.banner.defaultDetail")], action: diagnose() }
}

function collectIssues(input: HealthVerdictInput): Issue[] {
  // Nothing came back, so anything said about DVRs or disk would be a guess from old data.
  if (input.coreStatus === "Unavailable" && input.hasFailedFetches) {
    const key = input.failureKind === "rate_limited"
      ? "dashboard.verdict.rateLimited"
      : input.failureKind === "server_error" ? "dashboard.verdict.serverError" : "dashboard.verdict.cannotLoad"
    return [{ rank: 2, headline: t(key), details: [] }]
  }

  const issues: Issue[] = []
  const enabled = input.dvrs.filter((dvr) => dvr.enabled !== false)
  const connected = enabled.filter((dvr) => dvr.connected).length
  const disconnected = enabled.length - connected
  const dvrSummary = enabled.length > 0
    ? [t(connected === 1 ? "dashboard.verdict.dvrConnected" : "dashboard.verdict.dvrsConnected", { count: connected })]
    : []

  if (input.coreStatus === "Unknown" || input.coreStatus === "Error") {
    issues.push({ rank: 2, headline: t("dashboard.verdict.coreUnknown"), details: [], action: diagnose() })
  } else if (input.coreStatus !== "Running" && !CORE_PENDING.has(input.coreStatus)) {
    issues.push({ rank: 3, headline: t("dashboard.verdict.coreDown"), details: dvrSummary, action: diagnose() })
  }

  if (enabled.length === 0 || connected === 0) {
    const headline = enabled.length > 1 ? t("dashboard.verdict.dvrDown", { count: disconnected, total: enabled.length }) : t("dashboard.verdict.noDvr")
    issues.push({ rank: 3, headline, details: [], action: enabled.length === 0 ? { kind: "add-dvr", label: t("dashboard.verdict.addDvr") } : diagnose() })
  } else if (disconnected > 0) {
    issues.push({ rank: 2, headline: t("dashboard.verdict.dvrDown", { count: disconnected, total: enabled.length }), details: dvrSummary, action: diagnose() })
  }

  const diskDetails = input.diskDetail ? [input.diskDetail] : []
  const diskAction: HealthVerdictAction = { kind: "disk", label: t("dashboard.verdict.seeDisk") }
  if (input.diskSeverity === "critical") {
    issues.push({ rank: 3, headline: t("dashboard.verdict.diskCritical"), details: diskDetails, action: diskAction })
  } else if (input.diskSeverity === "warning") {
    issues.push({ rank: 2, headline: t("dashboard.verdict.diskWarning"), details: diskDetails, action: diskAction })
  }

  for (const dvr of enabled) {
    const issue = monitoringIssue(dvr)
    if (issue) issues.push(issue)
  }

  if (input.hasFailedFetches) {
    issues.push({ rank: 2, headline: t("dashboard.verdict.dataStale"), details: [], action: diagnose() })
  }

  if (CORE_PENDING.has(input.coreStatus)) {
    issues.push({ rank: 1, headline: t("dashboard.verdict.checking"), details: dvrSummary })
  }

  return issues
}

/** One slot, one truthful line: the worst thing wrong, and how many more things sit behind it. */
export function buildHealthVerdict(input: HealthVerdictInput): HealthVerdict {
  if (!input.loaded) {
    return { tone: "pending", headline: t("dashboard.verdict.checking"), details: [t("dashboard.verdict.connecting")] }
  }

  const issues = collectIssues(input)
  if (issues.length > 0) {
    const worst = issues.reduce((lead, issue) => (issue.rank > lead.rank ? issue : lead))
    const { rank, ...verdict } = worst
    return { ...verdict, tone: RANK_TONE[rank], more: issues.length - 1 || undefined }
  }

  const enabled = input.dvrs.filter((dvr) => dvr.enabled !== false)
  const connected = enabled.filter((dvr) => dvr.connected).length
  const details = [t(connected === 1 ? "dashboard.verdict.dvrConnected" : "dashboard.verdict.dvrsConnected", { count: connected })]

  // Everything is fine except nobody would hear about it if it stopped being fine. Said once, here.
  if (input.notificationServices === 0) {
    return {
      tone: "healthy",
      headline: t("dashboard.verdict.dvrHealthy"),
      details,
      action: { kind: "notifications", label: t("dashboard.verdict.alertsOff") },
    }
  }
  return { tone: "healthy", headline: t("dashboard.verdict.healthy"), details }
}
