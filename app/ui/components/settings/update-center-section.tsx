"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { AlertTriangle, CheckCircle2, DownloadCloud, ExternalLink, Loader2, PackageCheck, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { TabsContent } from "@/components/base/tabs"
import { ApiError, applyUpdate, checkForUpdate, fetchSettings, fetchUpdateJob, fetchUpdatePolicy, fetchUpdateStatus, postponeUpdate, retryUpdate, rollbackUpdate, saveUpdatePolicy, verifyStartupReady, type UpdateJob, type UpdatePolicy, type UpdatePolicyMode, type UpdateStatus } from "@/lib/api"
import { t } from "@/lib/i18n"
import { applyUpdateAndReconnect, isPendingUpdateJob, reloadUpdatedDashboard, requiresUpdateReconnect } from "@/lib/update-reconnect"

type BusyState = "idle" | "checking" | "applying" | "rolling-back" | "polling" | "saving-policy" | "postponing" | "retrying"

function statusTone(status?: string | null): "default" | "secondary" | "destructive" | "outline" {
  if (!status) return "outline"
  if (["success", "current"].includes(status)) return "default"
  if (["failed", "image_required"].includes(status)) return "destructive"
  if (["available", "restarting", "validating", "applying", "backing_up", "verifying"].includes(status)) return "secondary"
  return "outline"
}

function updateErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.payload.message
  return error instanceof Error ? error.message : t("updates.error.default")
}

function updateErrorRemediation(error: unknown): string | null {
  return error instanceof ApiError ? error.payload.remediation ?? null : null
}

function versionLabel(status: UpdateStatus | null): string {
  if (!status) return t("common.unknown")
  const active = status.active_bundle?.version
  return active ? `${status.current_version} (${t("updates.activeBundle", { version: String(active) })})` : status.current_version
}

function trustedReleaseLabel(status: UpdateStatus | null): string {
  if (!status) return t("updates.notChecked")
  if (status.catalog_state === "checking") return t("updates.catalog.checking")
  if (status.catalog_state === "error") return t("updates.catalog.unavailable")
  const target = status.trusted_target
  if (target && status.catalog_state === "update_available") {
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

export function UpdateCenterSection() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [job, setJob] = useState<UpdateJob | null>(null)
  const [policy, setPolicy] = useState<UpdatePolicy | null>(null)
  const [maintenanceStart, setMaintenanceStart] = useState("03:00")
  const [maintenanceMinutes, setMaintenanceMinutes] = useState("120")
  const [busy, setBusy] = useState<BusyState>("idle")
  const [error, setError] = useState<string | null>(null)
  const [remediation, setRemediation] = useState<string | null>(null)

  const latest = status?.trusted_target ?? null
  const latestVersion = latest?.version_tag ?? (latest?.version ? `v${latest.version}` : null)
  const remoteOperationBusy = Boolean(status?.operation_busy && status.operation_state !== "idle")
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

  useEffect(() => {
    loadStatus().catch((err) => {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
    })
  }, [loadStatus])

  const activeJobId = job?.job_id
  const activeJobStatus = job?.status
  useEffect(() => {
    if (!activeJobId || !isPendingUpdateJob({ status: activeJobStatus })) return
    setBusy("polling")
    const timer = window.setInterval(async () => {
      try {
        const nextJob = await fetchUpdateJob(activeJobId)
        setJob(nextJob)
        if (!isPendingUpdateJob(nextJob)) {
          setBusy("idle")
          await loadStatus()
        }
      } catch (err) {
        setBusy("idle")
        setError(updateErrorMessage(err))
        setRemediation(updateErrorRemediation(err))
      }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [activeJobId, activeJobStatus, loadStatus])

  const pendingJob = Boolean(job && isPendingUpdateJob(job))
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
    setBusy("applying")
    setError(null)
    setRemediation(null)
    try {
      const nextJob = await applyUpdateAndReconnect(targetVersion, {
        apply: async (version) => {
          const startedJob = await applyUpdate(version)
          if (requiresUpdateReconnect(startedJob)) {
            setBusy("polling")
          } else {
            setJob(startedJob)
          }
          return startedJob
        },
        fetchStatus: fetchUpdateStatus,
        verifyReady: async () => {
          const [, settings] = await Promise.all([verifyStartupReady(), fetchSettings()])
          if (!Array.isArray(settings.dvr_servers)) {
            throw new Error("ChannelWatch settings are not ready yet.")
          }
        },
        reload: reloadUpdatedDashboard,
        isRejectedUpdate: (err) => err instanceof ApiError,
      })
      if (nextJob && !requiresUpdateReconnect(nextJob)) {
        setJob(nextJob)
        setBusy("idle")
      }
    } catch (err) {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
      setBusy("idle")
    }
  }

  const handleRollback = async () => {
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
    setBusy("retrying")
    setError(null)
    try {
      const nextJob = await retryUpdate()
      setJob(nextJob)
      setBusy(isPendingUpdateJob(nextJob) ? "polling" : "idle")
    } catch (err) {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
      setBusy("idle")
    }
  }

  const windowStartMatch = /^(\d{2}):(\d{2})$/.exec(maintenanceStart)
  const windowStartValid = Boolean(
    windowStartMatch
    && Number(windowStartMatch[1]) <= 23
    && Number(windowStartMatch[2]) <= 59,
  )
  const parsedWindowMinutes = Number(maintenanceMinutes)
  const windowMinutesValid = Number.isInteger(parsedWindowMinutes) && parsedWindowMinutes >= 15 && parsedWindowMinutes <= 720
  const policyFieldsValid = windowStartValid && windowMinutesValid

  const handlePolicySchedule = async () => {
    await handlePolicyMode(policy?.mode ?? "automatic")
  }

  return (
    <TabsContent value="updates" className="space-y-6">
      <Card className="border-blue-400/20 overflow-hidden">
        <div className="relative">
          <div className="absolute inset-0 bg-gradient-to-r from-sky-900/10 to-emerald-900/10 z-0" />
          <CardHeader className="relative z-10 border-b border-blue-200/10">
            <div className="flex items-center gap-3">
              <div className="flex-shrink-0 w-10 h-10 rounded-full bg-sky-500/20 flex items-center justify-center">
                <DownloadCloud className="h-5 w-5 text-sky-400" />
              </div>
              <div>
                <CardTitle className="text-xl">{t("updates.title")}</CardTitle>
                <CardDescription>{t("updates.description")}</CardDescription>
              </div>
            </div>
          </CardHeader>
        </div>

        <CardContent className="space-y-6 pt-6">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl border border-sky-400/15 bg-sky-500/5 p-4">
              <p className="text-xs uppercase text-muted-foreground">{t("updates.appVersion")}</p>
              <p className="mt-1 text-lg font-semibold">{versionLabel(status)}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t(`updates.runtimeSource.${status?.runtime_source ?? "unknown"}`)}</p>
            </div>
            <div className="rounded-xl border border-sky-400/15 bg-sky-500/5 p-4">
              <p className="text-xs uppercase text-muted-foreground">{t("updates.imageVersion")}</p>
              <p className="mt-1 text-lg font-semibold">{status?.image_version ? `v${status.image_version.replace(/^v/, "")}` : t("common.unknown")}</p>
              <p className="mt-1 text-xs text-muted-foreground">{status?.image_refresh_recommended ? t("updates.imageRefreshRecommended") : t("updates.imageCompatible")}</p>
            </div>
            <div className="rounded-xl border border-sky-400/15 bg-sky-500/5 p-4">
              <p className="text-xs uppercase text-muted-foreground">{t("updates.latestVersion")}</p>
              <p className="mt-1 text-lg font-semibold">{trustedReleaseLabel(status)}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {status?.catalog_state === "update_available"
                  ? t(`updates.delivery.${status.delivery_mode ?? "app_update"}`)
                  : t("updates.catalog.currentDescription")}
              </p>
            </div>
            <div className="rounded-xl border border-sky-400/15 bg-sky-500/5 p-4">
              <p className="text-xs uppercase text-muted-foreground">{t("updates.protocol")}</p>
              <p className="mt-1 text-sm font-mono">{status?.runtime_abi ?? t("common.unknown")}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t("updates.launcherProtocol", { protocol: status?.launcher_protocol ?? t("common.unknown") })}</p>
            </div>
          </div>

          <Alert aria-live="polite" className="border-sky-400/30 bg-sky-500/10 text-sky-900 dark:text-sky-100 [&>svg]:text-sky-500">
            <PackageCheck className="h-4 w-4" />
            <AlertTitle>{primaryMessage}</AlertTitle>
            <AlertDescription>{t("updates.bootstrapNote")}</AlertDescription>
          </Alert>

          {status?.cached_release_stale ? (
            <Alert data-testid="update-stale-cache-notice">
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>{t("updates.staleCache.title")}</AlertTitle>
              <AlertDescription>{t("updates.staleCache.description")}</AlertDescription>
            </Alert>
          ) : null}

          {status?.auth_disabled_warning && (
            <Alert variant="destructive" className="border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-100 [&>svg]:text-amber-500">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{t("updates.noAuthWarningTitle")}</AlertTitle>
              <AlertDescription>{t("updates.noAuthWarningDesc")}</AlertDescription>
            </Alert>
          )}

          {status?.catalog_state === "update_available" && latest?.highlights && latest.highlights.length > 0 && (
            <div className="rounded-xl border border-sky-400/15 bg-background/40 p-5 space-y-3">
              <p className="text-sm font-semibold">{t("updates.highlights")}</p>
              <ul className="space-y-2 text-sm text-muted-foreground">
                {latest.highlights.map((item) => (
                  <li key={item} className="flex gap-2">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="rounded-xl border border-sky-400/15 bg-background/40 p-5 space-y-4">
            <div>
              <p className="text-sm font-semibold">{t("updates.policy.title")}</p>
              <p className="mt-1 text-sm text-muted-foreground">{t("updates.policy.description")}</p>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <Button
                type="button"
                variant={(policy?.mode ?? "automatic") === "automatic" ? "default" : "outline"}
                disabled={controlsBusy || !policyFieldsValid}
                aria-pressed={(policy?.mode ?? "automatic") === "automatic"}
                onClick={() => handlePolicyMode("automatic")}
              >
                {t("updates.policy.automatic")}
              </Button>
              <Button
                type="button"
                variant={policy?.mode === "notify_only" ? "default" : "outline"}
                disabled={controlsBusy || !policyFieldsValid}
                aria-pressed={policy?.mode === "notify_only"}
                onClick={() => handlePolicyMode("notify_only")}
              >
                {t("updates.policy.notifyOnly")}
              </Button>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="update-maintenance-start">{t("updates.policy.startLabel")}</Label>
                <Input
                  id="update-maintenance-start"
                  type="time"
                  step={60}
                  value={maintenanceStart}
                  onChange={(event) => setMaintenanceStart(event.target.value)}
                  aria-invalid={!windowStartValid}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="update-maintenance-minutes">{t("updates.policy.durationLabel")}</Label>
                <Input
                  id="update-maintenance-minutes"
                  type="number"
                  min={15}
                  max={720}
                  step={15}
                  value={maintenanceMinutes}
                  onChange={(event) => setMaintenanceMinutes(event.target.value)}
                  aria-invalid={!windowMinutesValid}
                />
              </div>
            </div>
            {!policyFieldsValid ? <p role="alert" className="text-sm text-destructive">{t("updates.policy.windowInvalid")}</p> : null}
            <Button type="button" variant="outline" disabled={controlsBusy || !policyFieldsValid} onClick={handlePolicySchedule}>
              {busy === "saving-policy" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {t("updates.policy.saveWindow")}
            </Button>
            <p className="text-sm text-muted-foreground">
              {t("updates.policy.window", {
                start: maintenanceStart,
                end: maintenanceWindowEnd(maintenanceStart, Number(maintenanceMinutes)),
              })}
            </p>
            {policy?.postponed_until ? <p role="status" className="text-sm text-amber-700 dark:text-amber-300">{t("updates.policy.postponedUntil", { time: policy.postponed_until })}</p> : null}
            {policy?.next_attempt_at ? <p className="text-xs text-muted-foreground">{t("updates.policy.nextAttempt", { time: policy.next_attempt_at })}</p> : null}
            {policy?.last_error ? <p role="alert" className="text-sm text-destructive">{t("updates.policy.lastError", { error: policy.last_error })}</p> : null}
          </div>

          {job && (
            <div className="rounded-xl border border-sky-400/15 bg-background/40 p-5 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">
                  {isPendingUpdateJob(job) ? t("updates.activeJob") : t("updates.lastJob")}
                </p>
                <Badge variant={statusTone(job.status)}>{job.status}</Badge>
              </div>
              <p className="text-sm text-muted-foreground">{job.message ?? t("updates.jobNoMessage")}</p>
              {job.backup_path && <p className="text-xs text-muted-foreground">{t("updates.backupCreated", { path: job.backup_path })}</p>}
            </div>
          )}

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

          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" variant="outline" onClick={handleCheck} disabled={controlsBusy} className="gap-2">
              {busy === "checking" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {busy === "checking" ? t("updates.checking") : t("updates.check")}
            </Button>
            <Button type="button" onClick={handleApply} disabled={!canApply} className="gap-2">
              {busy === "applying" || busy === "polling" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
              {busy === "applying" || busy === "polling" ? t("updates.applying") : t("updates.apply")}
            </Button>
            <Button type="button" variant="outline" onClick={handleRollback} disabled={!canRollback} className="gap-2">
              {busy === "rolling-back" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
              {busy === "rolling-back" ? t("updates.rollingBack") : t("updates.rollback")}
            </Button>
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
            {(policy?.last_error || (job?.status === "failed" && Boolean(status?.trusted_target))) ? (
              <Button type="button" variant="outline" onClick={handleRetry} disabled={controlsBusy} className="gap-2">
                {busy === "retrying" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                {t("updates.retry")}
              </Button>
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
        </CardContent>
      </Card>
    </TabsContent>
  )
}
