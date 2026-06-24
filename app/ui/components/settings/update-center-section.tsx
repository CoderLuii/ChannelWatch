"use client"

import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, CheckCircle2, DownloadCloud, ExternalLink, Loader2, PackageCheck, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { TabsContent } from "@/components/base/tabs"
import { ApiError, applyUpdate, checkForUpdate, fetchUpdateJob, fetchUpdateStatus, rollbackUpdate, type UpdateJob, type UpdateStatus } from "@/lib/api"
import { t } from "@/lib/i18n"

type BusyState = "idle" | "checking" | "applying" | "rolling-back" | "polling"

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

export function UpdateCenterSection() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [job, setJob] = useState<UpdateJob | null>(null)
  const [busy, setBusy] = useState<BusyState>("idle")
  const [error, setError] = useState<string | null>(null)
  const [remediation, setRemediation] = useState<string | null>(null)

  const latest = status?.latest ?? null
  const latestVersion = latest?.version_tag ?? (latest?.version ? `v${latest.version}` : null)
  const canApply = Boolean(status?.update_available && !status.image_required && latest?.version && busy === "idle")
  const canRollback = Boolean(status?.rollback_available && busy === "idle")

  const primaryMessage = useMemo(() => {
    if (!status) return t("updates.loading")
    if (status.image_required && status.update_available) return t("updates.state.imageRequired")
    if (status.update_available) return t("updates.state.available", { version: latestVersion ?? t("common.unknown") })
    return t("updates.state.current")
  }, [latestVersion, status])

  const loadStatus = async () => {
    const next = await fetchUpdateStatus()
    setStatus(next)
    setJob(next.last_job ?? null)
  }

  useEffect(() => {
    loadStatus().catch((err) => {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
    })
  }, [])

  useEffect(() => {
    if (!job?.job_id || !["restarting", "validating", "applying", "backing_up", "verifying"].includes(job.status)) return
    setBusy("polling")
    const timer = window.setInterval(async () => {
      try {
        const nextJob = await fetchUpdateJob(job.job_id)
        setJob(nextJob)
        if (!["restarting", "validating", "applying", "backing_up", "verifying"].includes(nextJob.status)) {
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
  }, [job?.job_id, job?.status])

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
    setBusy("applying")
    setError(null)
    setRemediation(null)
    try {
      const nextJob = await applyUpdate(latest.version)
      setJob(nextJob)
      if (!nextJob.restart_required) setBusy("idle")
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
      if (!nextJob.restart_required) setBusy("idle")
    } catch (err) {
      setError(updateErrorMessage(err))
      setRemediation(updateErrorRemediation(err))
      setBusy("idle")
    }
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
          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded-xl border border-sky-400/15 bg-sky-500/5 p-4">
              <p className="text-xs uppercase text-muted-foreground">{t("updates.currentVersion")}</p>
              <p className="mt-1 text-lg font-semibold">{versionLabel(status)}</p>
            </div>
            <div className="rounded-xl border border-sky-400/15 bg-sky-500/5 p-4">
              <p className="text-xs uppercase text-muted-foreground">{t("updates.latestVersion")}</p>
              <p className="mt-1 text-lg font-semibold">{latestVersion ?? t("updates.notChecked")}</p>
            </div>
            <div className="rounded-xl border border-sky-400/15 bg-sky-500/5 p-4">
              <p className="text-xs uppercase text-muted-foreground">{t("updates.runtime")}</p>
              <p className="mt-1 text-sm font-mono">{status?.runtime_abi ?? t("common.unknown")}</p>
            </div>
          </div>

          <Alert className="border-sky-400/30 bg-sky-500/10 text-sky-900 dark:text-sky-100 [&>svg]:text-sky-500">
            <PackageCheck className="h-4 w-4" />
            <AlertTitle>{primaryMessage}</AlertTitle>
            <AlertDescription>{t("updates.bootstrapNote")}</AlertDescription>
          </Alert>

          {status?.auth_disabled_warning && (
            <Alert variant="destructive" className="border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-100 [&>svg]:text-amber-500">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{t("updates.noAuthWarningTitle")}</AlertTitle>
              <AlertDescription>{t("updates.noAuthWarningDesc")}</AlertDescription>
            </Alert>
          )}

          {latest?.highlights && latest.highlights.length > 0 && (
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

          {job && (
            <div className="rounded-xl border border-sky-400/15 bg-background/40 p-5 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">{t("updates.lastJob")}</p>
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
            <Button type="button" variant="outline" onClick={handleCheck} disabled={busy !== "idle"} className="gap-2">
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
