"use client"

import { useContext, useEffect, useMemo, useState } from "react"
import { CheckCircle2, ChevronRight, Clock, PowerOff, ServerOff } from "lucide-react"

import { Tag } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/base/dialog"
import { Figure } from "@/components/base/figure"
import { TileSheetContext } from "@/components/dashboard/mobile-dashboard-details"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import { t } from "@/lib/i18n"
import type { DVRStatusInfo } from "@/lib/types"
import { formatUptimeTile } from "@/lib/utils"

interface UptimeCardProps {
  coreUptime: { days: number; hours: number; minutes: number; seconds: number } | null
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
  const inTileSheet = useContext(TileSheetContext)
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
  const configuredCount = dvrStatusList.filter((dvr) => dvr.enabled !== false).length
  const uptimeRows = useMemo(() => dvrStatusList.map((dvr) => ({
    ...dvr,
    displayUptime: currentDvrUptime(dvr, elapsedSeconds),
  })), [dvrStatusList, elapsedSeconds])

  const dvrRows = uptimeRows.length === 0 ? (
    <p className="py-8 text-center text-sm text-muted-foreground">{hasError ? t("uptime.unavailable") : t("uptime.noDvrs")}</p>
  ) : (
    <div className="divide-y divide-hairline">
      {uptimeRows.map((dvr) => {
        const disabled = dvr.enabled === false
        const statusLabel = disabled
          ? t("uptime.disabled")
          : dvr.connected
            ? t("common.connected")
            : t("common.disconnected")
        const StatusIcon = disabled ? PowerOff : dvr.connected ? CheckCircle2 : ServerOff
        return (
          <div key={dvr.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-4">
            <div className="flex min-w-[min(100%,11rem)] flex-1 items-start gap-3">
              <StatusIcon className={`mt-0.5 h-4 w-4 shrink-0 ${disabled || !dvr.connected ? "text-muted-foreground" : "text-success"}`} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="min-w-0 break-words text-sm font-medium">{dvr.name}</p>
                  {selectedDvr === dvr.id ? <Tag>{t("uptime.selected")}</Tag> : null}
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {statusLabel}
                  {dvr.connected ? ` · ${formatDuration(dvr.displayUptime)}` : ""}
                </p>
              </div>
            </div>
            {shortVersion(dvr.version) ? <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">{shortVersion(dvr.version)}</span> : null}
          </div>
        )
      })}
    </div>
  )

  return (
    <Card className="p-4">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 p-0">
        <CardTitle className="min-w-0 text-xs font-normal text-muted-foreground">{t("uptime.title")}</CardTitle>
        <div className="flex shrink-0 items-center gap-1.5">
          <Clock className="size-4 text-muted-foreground" aria-hidden="true" />
        </div>
      </CardHeader>
      <CardContent className="p-0 pt-1.5">
        {loading ? (
          <div className="animate-pulse" role="status" aria-label={t("common.loading")}>
            <div className="h-7 w-16 rounded-thumb bg-muted" />
            <div className="mt-1.5 flex h-10 items-center md:h-5">
              <div className="h-3 w-32 max-w-full rounded-thumb bg-muted" />
            </div>
          </div>
        ) : (
          <>
            <Figure value={coreUptime && !hasError ? formatUptimeTile(coreUptime) : "–"} className={`block text-title font-semibold leading-none tracking-tight ${coreUptime && !hasError ? "text-foreground" : "text-muted-foreground"}`} />
            <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{hasError ? t("uptime.unavailable") : t("uptime.coreProcess")}</p>

            <div className="mt-3 space-y-1 border-t border-hairline pt-2">
              <div className="flex flex-wrap justify-between gap-2 text-xs">
                <span className="text-muted-foreground">{t("uptime.uiProcess")}</span>
                <span className="min-w-0 break-words font-medium tabular-nums text-foreground [overflow-wrap:anywhere]">{uiUptimeDisplay}</span>
              </div>
              {inTileSheet ? (
                <div className="border-t border-hairline pt-2">
                  <p className="text-xs font-medium text-foreground">{t("uptime.dvrUptime")}</p>
                  {dvrRows}
                </div>
              ) : <Dialog>
                <DialogTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    className="min-h-11 w-full justify-between gap-2 px-0 text-xs font-medium text-primary underline-offset-4 hover:bg-transparent hover:text-primary hover:underline"
                  >
                    <span>{t("uptime.dvrUptime")}</span>
                    <span className="ml-auto font-medium">
                      {configuredCount > 0
                        ? t("uptime.dvrConnectedCount", { connected: connectedCount, total: configuredCount })
                        : hasError ? t("common.unavailable") : t("uptime.noDvrs")}
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-md overflow-hidden p-0 sm:w-full">
                  <DialogHeader className="border-b border-hairline p-5 pb-4">
                    <DialogTitle>{t("uptime.dialogTitle")}</DialogTitle>
                    <DialogDescription>{t("uptime.dialogDescription")}</DialogDescription>
                  </DialogHeader>
                  <div className="max-h-[min(60vh,30rem)] overflow-y-auto px-5">
                    {dvrRows}
                  </div>
                </DialogContent>
              </Dialog>}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
