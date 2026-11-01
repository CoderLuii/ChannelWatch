"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, CheckCircle2, DownloadCloud, ExternalLink, Loader2, PackageCheck, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { StatusBadge, type StatusTone } from "@/components/base/status-badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/base/dialog"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { TabsContent } from "@/components/base/tabs"
import { ApiError, ApiUnavailableError, applyUpdate, checkForUpdate, fetchSettings, fetchUpdateJob, fetchUpdatePolicy, fetchUpdateStatus, postponeUpdate, retryUpdate, rollbackUpdate, saveUpdatePolicy, verifyStartupReady, type UpdateJob, type UpdatePolicy, type UpdatePolicyMode, type UpdateStatus } from "@/lib/api"
import { describeError } from "@/lib/error-catalog"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import { formatAbsoluteTime, formatRelativeTime } from "@/lib/relative-time"
import { applyUpdateAndReconnect, isPendingUpdateJob, reloadUpdatedDashboard, requiresUpdateReconnect, UpdateJobPendingError, UpdateReadinessError, UpdateReconnectError, waitForUpdatedRuntime, type RestartJob } from "@/lib/update-reconnect"

type BusyState = "idle" | "checking" | "applying" | "rolling-back" | "polling" | "saving-policy" | "postponing" | "retrying"

type UpdateFlight = {
  controller: AbortController
  promise: Promise<void>
}

function createUpdateJobId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("")
}

function statusTone(status?: string | null): StatusTone {
  if (!status) return "neutral"
  if (["success", "current"].includes(status)) return "success"
  if (["failed", "image_required"].includes(status)) return "danger"
  return "neutral"
}

function updateErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.payload.message
  return describeError(error, t("updates.error.default")).message
}

function updateErrorRemediation(error: unknown): string | null {
  return error instanceof ApiError ? error.payload.remediation ?? null : null
}

function versionLabel(status: UpdateStatus | null): string {
  if (!status) return t("common.unknown")
  const active = status.active_bundle?.version
  return active ? `${status.current_version} (${t("updates.activeBundle", { version: String(active) })})` : status.current_version
}

function compatibleAppReleaseLabel(status: UpdateStatus | null): string {
  if (!status) return t("updates.notChecked")
  if (status.catalog_state === "checking") return t("updates.catalog.checking")
  if (status.catalog_state === "error") return t("updates.catalog.unavailable")
  const target = status.compatible_app_release ?? (!status.image_required ? status.trusted_target : null)
  if (target && status.update_available && !status.image_required) {
    return target.version_tag || `v${target.version}`
  }
  if (["current", "stale_cache"].includes(status.catalog_state ?? "")) {
    return t("updates.catalog.current", { version: `v${status.current_version.replace(/^v/, "")}` })
  }
  return t("updates.notChecked")
}

function maintenanceWindowEnd(start: string, minutes: number): string {
  const match = /^(\d{2}):(\d{2})$/.exec(start)
  if (!match) return t("common.unknown")
  const total = ((Number(match[1]) * 60 + Number(match[2]) + minutes) % 1440 + 1440) % 1440
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`
}

export function UpdateCenterSection({ active = true }: { active?: boolean }) {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [job, setJob] = useState<UpdateJob | null>(null)
  const [policy, setPolicy] = useState<UpdatePolicy | null>(null)
  const [maintenanceStart, setMaintenanceStart] = useState("03:00")
  const [maintenanceMinutes, setMaintenanceMinutes] = useState("120")
  const [busy, setBusy] = useState<BusyState>("idle")
  const [error, setError] = useState<string | null>(null)
  const [remediation, setRemediation] = useState<string | null>(null)
  const [rollbackConfirmOpen, setRollbackConfirmOpen] = useState(false)
  const rollbackButtonRef = useRef<HTMLButtonElement>(null)
  const updateFlightRef = useRef<UpdateFlight | null>(null)
  const updateActionLockedRef = useRef(false)
  const reloadStartedRef = useRef(false)

  const latest = status?.trusted_target ?? null
  const latestVersion = latest?.version_tag ?? (latest?.version ? `v${latest.version}` : null)
  const durableJobActive = Boolean(job && isPendingUpdateJob(job))
  const remoteOperationBusy = Boolean(
    status?.operation_busy
    && status.operation_state !== "idle"
    && (!job || durableJobActive),
  )
  const controlsBusy = busy !== "idle" || remoteOperationBusy
  const canApply = Boolean(status?.update_available && !status.image_required && latest?.version && !controlsBusy)
  const canRollback = Boolean(status?.rollback_available && !controlsBusy)

  const primaryMessage = useMemo(() => {
    if (!status) return t("updates.loading")
    if (status.operation_state === "checking" || busy === "checking") return t("updates.state.checkInProgress")
    if (status.operation_busy && status.operation_state !== "idle") {
      return t("updates.state.operationActive", { state: (status.operation_state ?? "update").replaceAll("_", " ") })
    }
    if (status.image_required && status.update_available) return t("updates.state.imageRequired")
    if (status.update_available) return t("updates.state.available", { version: latestVersion ?? t("common.unknown") })
    return t("updates.state.current")
  }, [busy, latestVersion, status])

  const loadStatus = useCallback(async () => {
    const [statusResult, policyResult] = await Promise.allSettled([fetchUpdateStatus(), fetchUpdatePolicy()])
    if (statusResult.status === "fulfilled") {
      const next = statusResult.value
      setStatus(next)
      setJob(next.last_job ?? null)
    }
    if (policyResult.status === "fulfilled") {
      const nextPolicy = policyResult.value
      setPolicy(nextPolicy)
      setMaintenanceStart(nextPolicy.maintenance_window_start)
      setMaintenanceMinutes(String(nextPolicy.maintenance_window_minutes))
    }
    if (statusResult.status === "rejected") throw statusResult.reason
    if (policyResult.status === "rejected") throw policyResult.reason
  }, [])

  // Settings stays mounted behind other tabs, so this reads again on the way back.
  useEffect(() => {
    if (!active) return
    loadStatus().catch((err) => {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
    })
  }, [active, loadStatus])

  useEffect(() => () => {
    updateFlightRef.current?.controller.abort()
  }, [])

  const verifyUpdatedRuntimeReady = useCallback(async () => {
    try {
      await verifyStartupReady()
    } catch (err) {
      if (err instanceof ApiUnavailableError) {
        throw new UpdateReconnectError(err.message, { cause: err })
      }
      if (
        err instanceof Error
        && (err.name === "TimeoutError" || err.message === "ChannelWatch startup is not ready: 503")
      ) {
        throw new UpdateReadinessError(err.message, { cause: err })
      }
      throw err
    }

    let settings
    try {
      settings = await fetchSettings()
    } catch (err) {
      if (err instanceof ApiUnavailableError) {
        throw new UpdateReconnectError(err.message, { cause: err })
      }
      throw err
    }
    if (!Array.isArray(settings.dvr_servers)) {
      throw new UpdateReadinessError("ChannelWatch settings are not ready yet.")
    }
  }, [])

  const fetchTrackedUpdateStatus = useCallback(async () => {
    try {
      return await fetchUpdateStatus()
    } catch (err) {
      if (err instanceof ApiUnavailableError) {
        throw new UpdateReconnectError(err.message, { cause: err })
      }
      throw err
    }
  }, [])

  const fetchTrackedUpdateJob = useCallback(async (jobId: string) => {
    try {
      return await fetchUpdateJob(jobId)
    } catch (err) {
      if (err instanceof ApiError && err.payload.code === "ERR_UPDATE_JOB_NOT_FOUND") {
        throw new UpdateJobPendingError(err.message, { cause: err })
      }
      if (err instanceof ApiUnavailableError) {
        throw new UpdateReconnectError(err.message, { cause: err })
      }
      throw err
    }
  }, [])

  const observeUpdateJob = useCallback((nextJob: RestartJob) => {
    if (nextJob.job_id && nextJob.status) {
      setJob(nextJob as UpdateJob)
    }
  }, [])

  const reloadUpdatedDashboardOnce = useCallback(() => {
    if (reloadStartedRef.current) return
    reloadStartedRef.current = true
    reloadUpdatedDashboard()
  }, [])

  const startUpdateFlight = useCallback((
    targetVersion: string,
    options: {
      apply?: () => Promise<UpdateJob>
      jobId?: string
      initialBusy: BusyState
    },
  ) => {
    if (updateActionLockedRef.current || updateFlightRef.current) return false

    updateActionLockedRef.current = true
    setBusy(options.initialBusy)
    setError(null)
    setRemediation(null)
    const controller = new AbortController()
    const promise = (async () => {
      try {
        if (options.apply) {
          const observeFlightJob = (nextJob: RestartJob) => {
            observeUpdateJob(nextJob)
            if (requiresUpdateReconnect(nextJob)) setBusy("polling")
          }
          const nextJob = await applyUpdateAndReconnect(targetVersion, {
            apply: async () => {
              try {
                const startedJob = await options.apply!()
                if (requiresUpdateReconnect(startedJob)) setBusy("polling")
                return startedJob
              } catch (err) {
                if (err instanceof ApiUnavailableError) {
                  throw new UpdateReconnectError(err.message, { cause: err })
                }
                throw err
              }
            },
            fetchStatus: fetchTrackedUpdateStatus,
            fetchJob: fetchTrackedUpdateJob,
            jobId: options.jobId,
            onJob: observeFlightJob,
            signal: controller.signal,
            verifyReady: verifyUpdatedRuntimeReady,
            reload: reloadUpdatedDashboardOnce,
            isRejectedUpdate: (err) => err instanceof ApiError,
          })
          if (nextJob && !requiresUpdateReconnect(nextJob)) {
            setJob(nextJob)
          }
          return
        }

        await waitForUpdatedRuntime(targetVersion, {
          fetchStatus: fetchTrackedUpdateStatus,
          fetchJob: fetchTrackedUpdateJob,
          jobId: options.jobId,
          onJob: observeUpdateJob,
          signal: controller.signal,
          verifyReady: verifyUpdatedRuntimeReady,
        })
        reloadUpdatedDashboardOnce()
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return
        setError(updateErrorMessage(err))
        setRemediation(updateErrorRemediation(err))
      } finally {
        if (updateFlightRef.current?.controller === controller) {
          updateFlightRef.current = null
          updateActionLockedRef.current = false
          if (!reloadStartedRef.current) setBusy("idle")
        }
      }
    })()
    updateFlightRef.current = { controller, promise }
    return true
  }, [fetchTrackedUpdateJob, fetchTrackedUpdateStatus, observeUpdateJob, reloadUpdatedDashboardOnce, verifyUpdatedRuntimeReady])

  const activeJobId = job?.job_id
  const activeJobStatus = job?.status
  const activeJobVersion = job?.version
  useEffect(() => {
    if (!activeJobId || !activeJobVersion || !isPendingUpdateJob({ status: activeJobStatus })) return
    startUpdateFlight(activeJobVersion, {
      jobId: activeJobId,
      initialBusy: "polling",
    })
  }, [activeJobId, activeJobStatus, activeJobVersion, startUpdateFlight])

  const pendingJob = durableJobActive
  useEffect(() => {
    if (!remoteOperationBusy || pendingJob || busy !== "idle") return
    const timer = window.setInterval(() => {
      loadStatus().catch((err) => {
        setError(updateErrorMessage(err))
        setRemediation(updateErrorRemediation(err))
      })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [busy, loadStatus, pendingJob, remoteOperationBusy])

  const handleCheck = async () => {
    setBusy("checking")
    setError(null)
    setRemediation(null)
    try {
      const next = await checkForUpdate()
      setStatus(next)
      setJob(next.last_job ?? null)
    } catch (err) {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
    } finally {
      setBusy("idle")
    }
  }

  const handleApply = async () => {
    if (!latest?.version) return
    const targetVersion = latest.version
    const jobId = createUpdateJobId()
    startUpdateFlight(targetVersion, {
      apply: () => applyUpdate(targetVersion, jobId),
      jobId,
      initialBusy: "applying",
    })
  }

  const handleRollback = async () => {
    setRollbackConfirmOpen(false)
    setBusy("rolling-back")
    setError(null)
    setRemediation(null)
    try {
      const nextJob = await rollbackUpdate()
      setJob(nextJob)
      setBusy(isPendingUpdateJob(nextJob) ? "polling" : "idle")
    } catch (err) {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
      setBusy("idle")
    }
  }

  const handlePolicyMode = async (mode: UpdatePolicyMode) => {
    if (!policyFieldsValid) return
    setBusy("saving-policy")
    setError(null)
    setRemediation(null)
    try {
      const next = await saveUpdatePolicy({
        mode,
        maintenance_window_start: maintenanceStart,
        maintenance_window_minutes: Number(maintenanceMinutes),
      })
      setPolicy(next)
      setMaintenanceStart(next.maintenance_window_start)
      setMaintenanceMinutes(String(next.maintenance_window_minutes))
    } catch (err) {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
    } finally {
      setBusy("idle")
    }
  }

  const handlePostpone = async (hours: 24 | 168) => {
    setBusy("postponing")
    setError(null)
    try {
      setPolicy(await postponeUpdate(hours))
    } catch (err) {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
    } finally {
      setBusy("idle")
    }
  }

  const handleRetry = async () => {
    // The backend retries the currently trusted catalog target, which may be
    // newer than the failed job. Only reconnect against the matching target
    // the administrator reviewed in this view.
    const targetVersion = latest?.version ?? job?.version
    if (!targetVersion) return
    const jobId = createUpdateJobId()
    startUpdateFlight(targetVersion, {
      apply: () => retryUpdate(targetVersion, jobId),
      jobId,
      initialBusy: "retrying",
    })
  }

  const updateStage = useMemo(() => {
    if (!["applying", "retrying", "polling"].includes(busy)) return null
    if (job?.status === "applying" && job.phase === "preparing") {
      return t("updates.progress.preparingDependencies")
    }
    if (busy === "applying" || busy === "retrying" || ["downloading", "backing_up", "verifying", "applying"].includes(job?.status ?? "")) {
      return t("updates.progress.downloading")
    }
    if (job?.status === "restarting" || (busy === "polling" && !job)) {
      return t("updates.progress.restarting")
    }
    return t("updates.progress.checking")
  }, [busy, job])

  // The summary is the one headline: what is happening now, then what the last check found.
  // It only claims "Up to date" after a release check has confirmed it.
  const summaryMessage = updateStage
    ?? (!status && error ? t("updates.summary.loadFailed") : null)
    ?? (!status || status.update_available || primaryMessage !== t("updates.state.current")
      ? primaryMessage
      : status.catalog_state === "error"
        ? t("updates.summary.checkFailed")
        : status.catalog_state === "not_checked"
          ? t("updates.summary.notChecked")
          : t("updates.summary.upToDate"))

  const windowStartMatch = /^(\d{2}):(\d{2})$/.exec(maintenanceStart)
  const windowStartValid = Boolean(
    windowStartMatch
    && Number(windowStartMatch[1]) <= 23
    && Number(windowStartMatch[2]) <= 59,
  )
  const parsedWindowMinutes = Number(maintenanceMinutes)
  const windowMinutesValid = Number.isInteger(parsedWindowMinutes) && parsedWindowMinutes >= 15 && parsedWindowMinutes <= 720
  const policyFieldsValid = windowStartValid && windowMinutesValid

  const failureReason = policy?.last_error || (job?.status === "failed" ? job.message : null) || null
  // "Nothing compatible" is a fact about the catalog, not something that went wrong.
  const failureIsNotice = /no release compatible/i.test(failureReason ?? "")
  const canRetry = !failureIsNotice && Boolean((policy?.last_error && (latest?.version ?? job?.version)) || (job?.status === "failed" && status?.trusted_target))
  const isBusy = busy !== "idle" || Boolean(status?.operation_busy && status.operation_state !== "idle")
  const showNote = Boolean(status) && !isBusy && status?.catalog_state !== "not_checked" && status?.catalog_state !== "error"

  const handlePolicySchedule = async () => {
    await handlePolicyMode(policy?.mode ?? "automatic")
  }

  // These fields sit inside the settings form, but they save to the update policy.
  const handlePolicyFieldKey = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return
    event.preventDefault()
    if (policyFieldsValid && !controlsBusy) void handlePolicySchedule()
  }

  return (
    <TabsContent value="updates" className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
            <div className="flex items-center gap-2">
              <DownloadCloud className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div>
                <CardTitle>{t("updates.title")}</CardTitle>
              </div>
            </div>
          </CardHeader>

        <CardContent className="space-y-6 pt-4">
          <div data-testid="update-center-summary" className="space-y-3">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 [overflow-wrap:anywhere]">
                <p className="text-lg font-semibold">{status ? t("updates.summary.onVersion", { version: `v${status.current_version.replace(/^v/, "")}` }) : error ? t("updates.title") : <span aria-hidden="true" className="inline-block h-5 w-40 animate-pulse rounded-thumb bg-muted align-middle" />}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {summaryMessage}
                  {status?.catalog_checked_at ? (
                    <>
                      {" "}
                      <span title={formatAbsoluteTime(status.catalog_checked_at)}>{t("updates.summary.lastChecked", { time: formatRelativeTime(status.catalog_checked_at) })}</span>
                    </>
                  ) : null}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                {status?.update_available && !status.image_required ? (
                  <Button type="button" onClick={handleApply} disabled={!canApply} className="gap-2">
                    {busy === "applying" || busy === "polling" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                    {busy === "applying" || busy === "polling" ? t("updates.applying") : t("updates.apply")}
                  </Button>
                ) : (
                  <Button type="button" variant="default" onClick={handleCheck} disabled={controlsBusy} className="gap-2">
                    {busy === "checking" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                    {busy === "checking" ? t("updates.checking") : t("updates.check")}
                  </Button>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-1 gap-y-1 text-sm">
              {status?.update_available && !status.image_required ? (
                <Button type="button" variant="ghost" onClick={handleCheck} disabled={controlsBusy} className="gap-2">
                  {busy === "checking" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  {busy === "checking" ? t("updates.checking") : t("updates.check")}
                </Button>
              ) : null}
              {status?.rollback_available ? (
                <Button type="button" variant="ghost" ref={rollbackButtonRef} onClick={() => setRollbackConfirmOpen(true)} disabled={!canRollback} className="gap-2">
                  {busy === "rolling-back" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                  {busy === "rolling-back" ? t("updates.rollingBack") : t("updates.rollback")}
                </Button>
              ) : null}
              {status?.update_available ? (
                <>
                  <Button type="button" variant="ghost" onClick={() => handlePostpone(24)} disabled={controlsBusy}>
                    {t("updates.postponeDay")}
                  </Button>
                  <Button type="button" variant="ghost" onClick={() => handlePostpone(168)} disabled={controlsBusy}>
                    {t("updates.postponeWeek")}
                  </Button>
                </>
              ) : null}
              {latest?.release_url && (
                <Button type="button" variant="ghost" asChild className="gap-2">
                  <a href={latest.release_url} target="_blank" rel="noreferrer">
                    <ExternalLink className="h-4 w-4" />
                    {t("updates.releaseNotes")}
                  </a>
                </Button>
              )}
            </div>
            {failureReason || (canRetry && job?.status === "failed") ? (
              <Alert data-testid="update-failure" variant={failureIsNotice ? "default" : "destructive"} className={cn("sm:[&>svg+div]:translate-y-0 sm:[&>svg]:top-1/2 sm:[&>svg]:-translate-y-1/2", failureIsNotice && "[&>svg]:text-muted-foreground")}>
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
                  <span className="min-w-0 flex-1 basis-[12rem] [overflow-wrap:anywhere]">
                    {policy?.last_error ? t("updates.policy.lastError", { error: policy.last_error }) : failureReason ?? t("updates.jobNoMessage")}
                  </span>
                  {canRetry ? (
                    <Button type="button" variant="outline" size="sm" onClick={handleRetry} disabled={controlsBusy} className="min-h-11 shrink-0 gap-2">
                      {busy === "retrying" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                      {t("updates.retry")}
                    </Button>
                  ) : null}
                </AlertDescription>
              </Alert>
            ) : null}
            {updateStage ? (
              <Alert aria-live="polite" className="[&>svg]:text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <AlertTitle>{updateStage}</AlertTitle>
                <AlertDescription>{t("updates.progress.refreshWhenReady")}</AlertDescription>
              </Alert>
            ) : null}
            {error && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>{t("updates.error.title")}</AlertTitle>
                <AlertDescription>
                  <span className="block">{error}</span>
                  {remediation && <span className="mt-1 block">{remediation}</span>}
                </AlertDescription>
              </Alert>
            )}
            {status?.rollback_available ? (
              <p data-testid="update-rollback-note" className="text-xs text-muted-foreground">{t("updates.summary.rollbackNote")}</p>
            ) : null}
          </div>
          {status ? <dl data-testid="update-facts" className="min-w-0 [overflow-wrap:anywhere]">
            {[
              [t("updates.appVersion"), versionLabel(status), t(`updates.runtimeSource.${status?.runtime_source ?? "unknown"}`)],
              [
                t("updates.imageVersion"),
                status?.image_version ? `v${status.image_version.replace(/^v/, "")}` : t("common.unknown"),
                status?.image_update_available && status.recommended_image_version
                  ? t("updates.imageUpdateAvailable", { version: `v${status.recommended_image_version.replace(/^v/, "")}` })
                  : status?.image_refresh_recommended
                    ? t("updates.imageRefreshRecommended")
                    : t("updates.imageCompatible"),
              ],
              [
                t("updates.compatibleAppVersion"),
                compatibleAppReleaseLabel(status),
                status?.catalog_state === "update_available"
                  ? t(`updates.delivery.${status.delivery_mode ?? "app_update"}`)
                  : ["current", "stale_cache"].includes(status?.catalog_state ?? "")
                    ? t("updates.catalog.currentDescription")
                    : "",
              ],
              [t("updates.protocol"), status?.runtime_abi ?? t("common.unknown"), t("updates.launcherProtocol", { protocol: status?.launcher_protocol ?? t("common.unknown") })],
            ].map(([label, value, detail]) => (
              <div key={label} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 border-t border-hairline py-2.5 text-sm">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="text-right font-mono text-sm">{value}</dd>
                {detail ? <dd className="w-full text-xs text-muted-foreground">{detail}</dd> : null}
              </div>
            ))}
          </dl> : error ? null : (
            <div data-testid="update-facts-loading" aria-hidden="true" className="min-w-0 animate-pulse">
              {[0, 1, 2, 3].map((row) => (
                <div key={row} className="border-t border-hairline py-2.5">
                  <div className="flex items-center justify-between gap-3">
                    <span className="h-5 w-32 rounded-thumb bg-muted" />
                    <span className="h-5 w-16 rounded-thumb bg-muted" />
                  </div>
                  <div className="mt-0.5 h-4 w-2/3 rounded-thumb bg-muted" />
                </div>
              ))}
            </div>
          )}

          {isBusy && !updateStage ? (
            <Alert aria-live="polite" className="[&>svg]:text-muted-foreground">
              <PackageCheck className="h-4 w-4" />
              <AlertTitle>{primaryMessage}</AlertTitle>
            </Alert>
          ) : showNote ? (
            <p data-testid="update-note" className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
              {status?.update_available && !status.image_required && status.image_update_available && status.recommended_image_version
                ? t("updates.intermediateAppAvailable", {
                    appVersion: latestVersion ?? t("common.unknown"),
                    imageVersion: `v${status.recommended_image_version.replace(/^v/, "")}`,
                  })
                : t("updates.bootstrapNote")}
            </p>
          ) : null}

          {status?.cached_release_stale ? (
            <Alert data-testid="update-stale-cache-notice">
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>{t("updates.staleCache.title")}</AlertTitle>
              <AlertDescription>{t("updates.staleCache.description")}</AlertDescription>
            </Alert>
          ) : null}

          {status?.auth_disabled_warning && (
            <Alert variant="destructive" className="border-warning-border bg-warning-bg text-warning-fg [&>svg]:text-warning-fg">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{t("updates.noAuthWarningTitle")}</AlertTitle>
              <AlertDescription>{t("updates.noAuthWarningDesc")}</AlertDescription>
            </Alert>
          )}

          {status?.catalog_state === "update_available" && latest?.highlights && latest.highlights.length > 0 && (
            <div className="space-y-3 border-t border-hairline pt-4">
              <p className="text-sm font-semibold">{t("updates.highlights")}</p>
              <ul className="space-y-2 text-sm text-muted-foreground">
                {latest.highlights.map((item) => (
                  <li key={item} className="flex gap-2">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-4 border-t border-hairline pt-4">
            <div>
              <p className="text-sm font-semibold">{t("updates.policy.title")}</p>
              <p className="mt-1 text-sm text-muted-foreground">{t("updates.policy.description")}</p>
            </div>
            <div className="grid gap-1 rounded-control bg-fill p-1 md:grid-cols-2">
              <Button
                type="button"
                variant="ghost"
                disabled={controlsBusy || !policyFieldsValid}
                aria-pressed={(policy?.mode ?? "automatic") === "automatic"}
                onClick={() => { if ((policy?.mode ?? "automatic") !== "automatic") void handlePolicyMode("automatic") }}
                className={cn("h-auto min-h-11 min-w-0 whitespace-normal rounded-thumb", (policy?.mode ?? "automatic") === "automatic" ? "bg-thumb text-foreground shadow-2 hover:bg-thumb" : "text-muted-foreground")}
              >
                {t("updates.policy.automatic")}
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={controlsBusy || !policyFieldsValid}
                aria-pressed={policy?.mode === "notify_only"}
                onClick={() => { if (policy?.mode !== "notify_only") void handlePolicyMode("notify_only") }}
                className={cn("h-auto min-h-11 min-w-0 whitespace-normal rounded-thumb", policy?.mode === "notify_only" ? "bg-thumb text-foreground shadow-2 hover:bg-thumb" : "text-muted-foreground")}
              >
                {t("updates.policy.notifyOnly")}
              </Button>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="min-w-0 space-y-2">
                <Label htmlFor="update-maintenance-start">{t("updates.policy.startLabel")}</Label>
                <Input
                  id="update-maintenance-start"
                  type="time"
                  step={60}
                  value={maintenanceStart}
                  onChange={(event) => setMaintenanceStart(event.target.value)}
                  onKeyDown={handlePolicyFieldKey}
                  aria-invalid={!windowStartValid}
                  className="time-input block min-w-0"
                />
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="update-maintenance-minutes">{t("updates.policy.durationLabel")}</Label>
                <Input
                  id="update-maintenance-minutes"
                  type="number"
                  min={15}
                  max={720}
                  step={15}
                  value={maintenanceMinutes}
                  onChange={(event) => setMaintenanceMinutes(event.target.value)}
                  onKeyDown={handlePolicyFieldKey}
                  aria-invalid={!windowMinutesValid}
                  className="min-w-0"
                />
              </div>
            </div>
            {!policyFieldsValid ? <p role="alert" className="text-sm text-danger-fg">{t("updates.policy.windowInvalid")}</p> : null}
            <Button type="button" variant="outline" className="h-auto min-h-11 max-w-full whitespace-normal py-2 text-center" disabled={controlsBusy || !policyFieldsValid} onClick={handlePolicySchedule}>
              {busy === "saving-policy" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {t("updates.policy.saveWindow")}
            </Button>
            <p className="break-words text-sm text-muted-foreground [overflow-wrap:anywhere]">
              {t("updates.policy.window", {
                start: maintenanceStart,
                end: maintenanceWindowEnd(maintenanceStart, Number(maintenanceMinutes)),
              })}
            </p>
            {policy?.postponed_until ? <p role="status" className="break-words text-sm text-warning-fg [overflow-wrap:anywhere]"><span title={formatAbsoluteTime(policy.postponed_until)}>{t("updates.policy.postponedUntil", { time: formatRelativeTime(policy.postponed_until) })}</span></p> : null}
            {policy?.next_attempt_at ? <p className="break-words text-xs text-muted-foreground [overflow-wrap:anywhere]"><span title={formatAbsoluteTime(policy.next_attempt_at)}>{t("updates.policy.nextAttempt", { time: formatRelativeTime(policy.next_attempt_at) })}</span></p> : null}
          </div>

          {job && (
            <div className="space-y-3 border-t border-hairline pt-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">
                  {isPendingUpdateJob(job) ? t("updates.activeJob") : t("updates.lastJob")}
                </p>
                <StatusBadge variant={statusTone(job.status)}>{job.status}</StatusBadge>
              </div>
              {job.status === "failed" ? null : <p className="text-sm text-muted-foreground">{job.message ?? t("updates.jobNoMessage")}</p>}
              {job.backup_path && <p className="text-xs text-muted-foreground">{t("updates.backupCreated", { path: job.backup_path })}</p>}
            </div>
          )}

        </CardContent>
      </Card>

      <Dialog open={rollbackConfirmOpen} onOpenChange={setRollbackConfirmOpen}>
        <DialogContent
          className="max-w-sm"
          // Opened by state rather than a trigger, so focus is sent back by hand.
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            rollbackButtonRef.current?.focus()
          }}
        >
          <DialogHeader>
            <DialogTitle className="pr-10">{t("updates.rollbackConfirmTitle")}</DialogTitle>
            <DialogDescription className="text-sm">{t("updates.rollbackConfirmDesc")}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={() => setRollbackConfirmOpen(false)}>{t("common.cancel")}</Button>
            <Button type="button" variant="destructive" onClick={handleRollback}>{t("updates.rollback")}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </TabsContent>
  )
}
