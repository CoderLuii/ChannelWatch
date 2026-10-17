"use client"

import { useEffect, useMemo, useState } from "react"
import { AlertCircle, CheckCircle2, ChevronRight, Clock, PowerOff, ServerOff } from "lucide-react"

import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/base/dialog"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import { t } from "@/lib/i18n"
import type { DVRStatusInfo } from "@/lib/types"

interface UptimeCardProps {
  coreUptime: { days: number; hours: number; minutes: number; seconds: number }
  uiUptimeDisplay: string
  dvrStatusList: DVRStatusInfo[]
  loading: boolean
  hasError: boolean
}

function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds) || totalSeconds < 0) {
    return t("uptime.unavailable")
  }
  let remaining = Math.floor(totalSeconds)
  const days = Math.floor(remaining / 86400)
  remaining %= 86400
  const hours = Math.floor(remaining / 3600)
  remaining %= 3600
  const minutes = Math.floor(remaining / 60)
  if (days > 0) return `${days}d ${hours}h ${minutes}m`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

function currentDvrUptime(dvr: DVRStatusInfo, elapsedSeconds: number): number | null {
  if (!dvr.connected || !dvr.uptime_available || dvr.uptime_seconds == null) return null
  if (!Number.isFinite(dvr.uptime_seconds) || dvr.uptime_seconds < 0) return null
  return Math.floor(dvr.uptime_seconds + elapsedSeconds)
}

function shortVersion(version: string | null): string | null {
  if (!version) return null
  const parts = version.split(".")
  return parts.length >= 3 ? `v${parts.slice(0, 3).join(".")}` : version
}

export function UptimeCard({
  coreUptime,
  uiUptimeDisplay,
  dvrStatusList,
  loading,
  hasError,
}: UptimeCardProps) {
  const { selectedDvr } = useDvrSelection()
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  useEffect(() => {
    const mountedAt = Date.now()
    const timer = window.setInterval(
      () => setElapsedSeconds(Math.floor((Date.now() - mountedAt) / 1000)),
      60_000,
    )
    return () => window.clearInterval(timer)
  }, [])

  const connectedCount = dvrStatusList.filter((dvr) => dvr.enabled !== false && dvr.connected).length
  const configuredCount = dvrStatusList.length
  const uptimeRows = useMemo(() => dvrStatusList.map((dvr) => ({
    ...dvr,
    displayUptime: currentDvrUptime(dvr, elapsedSeconds),
  })), [dvrStatusList, elapsedSeconds])

  return (
    <Card className="border-border/70 bg-card shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{t("uptime.title")}</CardTitle>
        <div className="flex items-center gap-1">
          {hasError ? <AlertCircle className="h-4 w-4 text-red-500" aria-label={t("common.error")} /> : null}
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/10">
            <Clock className="h-[18px] w-[18px] text-primary" aria-hidden="true" />
          </span>
        </div>
      </CardHeader>
      <CardContent className="py-2">
        {loading ? (
          <div className="animate-pulse space-y-2" role="status" aria-label={t("common.loading")}>
            <div className="h-3 w-20 rounded bg-purple-500/20" />
            <div className="flex gap-3">
              {[0, 1, 2, 3].map((item) => <div key={item} className="h-8 w-10 rounded bg-purple-500/20" />)}
            </div>
            <div className="h-9 w-full rounded bg-purple-500/20" />
          </div>
        ) : (
          <>
            <div className="mb-1 text-[13px] font-medium text-muted-foreground">{t("uptime.coreProcess")}</div>
            <div className="grid grid-cols-4 gap-1.5" role="group" aria-label={t("uptime.coreProcess")}>
              {[
                [coreUptime.days, t("uptime.days")],
                [coreUptime.hours, t("uptime.hours")],
                [coreUptime.minutes, t("uptime.mins")],
                [coreUptime.seconds, t("uptime.secs")],
              ].map(([value, label]) => (
                <div key={String(label)} className="flex flex-col items-center">
                  <span className="text-xl font-semibold tabular-nums text-foreground">{value}</span>
                  <span className="text-[13px] font-medium text-muted-foreground">{label}</span>
                </div>
              ))}
            </div>

            <div className="mt-3 space-y-1 border-t border-border/70 pt-2">
              <div className="flex flex-wrap justify-between gap-2 text-xs">
                <span className="text-muted-foreground">{t("uptime.uiProcess")}</span>
                <span className="min-w-0 break-words font-medium text-foreground [overflow-wrap:anywhere]">{uiUptimeDisplay}</span>
              </div>
              <Dialog>
                <DialogTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    className="min-h-11 w-full justify-between gap-2 px-0 text-[13px] text-primary hover:bg-blue-500/10"
                  >
                    <span>{t("uptime.dvrUptime")}</span>
                    <span className="ml-auto font-medium">
                      {configuredCount > 0
                        ? t("uptime.dvrConnectedCount", { connected: connectedCount, total: configuredCount })
                        : t("uptime.noDvrs")}
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-h-[calc(100vh-2rem)] w-[calc(100vw-2rem)] max-w-md overflow-hidden p-0 sm:w-full">
                  <DialogHeader className="border-b p-6 pb-4">
                    <DialogTitle>{t("uptime.dialogTitle")}</DialogTitle>
                    <DialogDescription>{t("uptime.dialogDescription")}</DialogDescription>
                  </DialogHeader>
                  <div className="max-h-[min(60vh,30rem)] overflow-y-auto px-6">
                    {uptimeRows.length === 0 ? (
                      <p className="py-8 text-center text-sm text-muted-foreground">{t("uptime.noDvrs")}</p>
                    ) : (
                      <div className="divide-y">
                        {uptimeRows.map((dvr) => {
                          const disabled = dvr.enabled === false
                          const statusLabel = disabled
                            ? t("uptime.disabled")
                            : dvr.connected
                              ? t("common.connected")
                              : t("common.disconnected")
                          const StatusIcon = disabled ? PowerOff : dvr.connected ? CheckCircle2 : ServerOff
                          return (
                            <div key={dvr.id} className="flex items-start gap-3 py-4">
                              <StatusIcon className={`mt-0.5 h-4 w-4 shrink-0 ${disabled || !dvr.connected ? "text-muted-foreground" : "text-emerald-500"}`} aria-hidden="true" />
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="min-w-0 break-words text-sm font-medium">{dvr.name}</p>
                                  {selectedDvr === dvr.id ? <Badge variant="secondary" className="text-xs">{t("uptime.selected")}</Badge> : null}
                                </div>
                                <p className="mt-1 text-sm text-muted-foreground">
                                  {statusLabel}
                                  {dvr.connected ? ` · ${formatDuration(dvr.displayUptime)}` : ""}
                                </p>
                              </div>
                              {shortVersion(dvr.version) ? <span className="shrink-0 text-xs text-muted-foreground">{shortVersion(dvr.version)}</span> : null}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                  <DialogFooter className="border-t p-4">
                    <DialogClose asChild>
                      <Button type="button" variant="outline">{t("common.close")}</Button>
                    </DialogClose>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
