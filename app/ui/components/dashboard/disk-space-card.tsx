"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Progress } from "@/components/base/progress"
import { AlertCircle, HardDrive, Loader2 } from "lucide-react"

export interface DiskSpaceState {
  usedPercent: number
  freePercent: number
  loading: boolean
  error: string | null
  totalFormatted: string
  usedFormatted: string
  freeFormatted: string
  libraryShows: number
  libraryMovies: number
  libraryEpisodes: number
}

interface DiskSpaceCardProps {
  diskSpace: DiskSpaceState
  loading: boolean
  hasError: boolean
  serverSeverity?: DiskSeverity
  warningThresholdPercent?: number
  criticalThresholdPercent?: number
  thresholdPercent?: number
}

export type DiskSeverity = "normal" | "warning" | "critical"

interface DiskThresholds {
  warningThresholdPercent: number
  criticalThresholdPercent: number
}

function resolveDiskThresholds(
  warningThresholdPercent?: number,
  criticalThresholdPercent?: number,
  thresholdPercent?: number
): DiskThresholds {
  const resolvedCriticalThreshold = criticalThresholdPercent ?? thresholdPercent ?? 10
  const resolvedWarningThreshold = Math.max(
    warningThresholdPercent ?? (thresholdPercent != null ? thresholdPercent * 2 : resolvedCriticalThreshold * 2),
    resolvedCriticalThreshold
  )

  return {
    warningThresholdPercent: resolvedWarningThreshold,
    criticalThresholdPercent: resolvedCriticalThreshold,
  }
}

function getThresholdSeverity(usedPercent: number, thresholds: DiskThresholds): DiskSeverity {
  const freePercent = 100 - usedPercent
  if (freePercent <= thresholds.criticalThresholdPercent) return "critical"
  if (freePercent <= thresholds.warningThresholdPercent) return "warning"
  return "normal"
}

export function resolveDiskSeverity({
  usedPercent,
  serverSeverity,
  warningThresholdPercent,
  criticalThresholdPercent,
  thresholdPercent,
}: {
  usedPercent: number
  serverSeverity?: DiskSeverity
  warningThresholdPercent?: number
  criticalThresholdPercent?: number
  thresholdPercent?: number
}): DiskSeverity {
  const thresholds = resolveDiskThresholds(warningThresholdPercent, criticalThresholdPercent, thresholdPercent)
  return serverSeverity ?? getThresholdSeverity(usedPercent, thresholds)
}

function getDiskStatusColor(severity: DiskSeverity) {
  if (severity === "critical") return "bg-red-600 dark:bg-red-400"
  if (severity === "warning") return "bg-amber-600 dark:bg-amber-400"
  return "bg-emerald-600 dark:bg-emerald-400"
}

function getCardGradient(_severity: DiskSeverity) {
  return "border-border/70 bg-card shadow-sm"
}

function getTextColor(severity: DiskSeverity) {
  if (severity === "critical") return { heading: "text-red-700 dark:text-red-300", sub: "text-red-700 dark:text-red-400", icon: "text-red-600 dark:text-red-400", iconBg: "bg-red-500/20" }
  if (severity === "warning") return { heading: "text-amber-700 dark:text-amber-300", sub: "text-amber-700 dark:text-amber-400", icon: "text-amber-600 dark:text-amber-400", iconBg: "bg-amber-500/20" }
  return { heading: "text-emerald-700 dark:text-emerald-300", sub: "text-emerald-700 dark:text-emerald-400", icon: "text-emerald-600 dark:text-emerald-400", iconBg: "bg-emerald-500/20" }
}

export function DiskSpaceCard({
  diskSpace,
  loading,
  hasError,
  serverSeverity,
  warningThresholdPercent,
  criticalThresholdPercent,
  thresholdPercent,
}: DiskSpaceCardProps) {
  const severity = resolveDiskSeverity({
    usedPercent: diskSpace.usedPercent,
    serverSeverity,
    warningThresholdPercent,
    criticalThresholdPercent,
    thresholdPercent,
  })
  const colors = getTextColor(severity)
  const cardGradient = getCardGradient(severity)

  return (
    <Card className={cardGradient}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{t("disk.title")}</CardTitle>
        <div className="flex items-center gap-1">
          {hasError && <AlertCircle className="h-3.5 w-3.5 text-red-500" />}
          <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${colors.iconBg}`}>
            <HardDrive className={`h-[18px] w-[18px] ${colors.icon}`} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="py-2">
        {loading ? (
          <div className="space-y-2 animate-pulse">
            <div className="h-8 w-28 rounded bg-emerald-500/20" />
            <div className="h-3 w-full rounded bg-emerald-500/20" />
            <div className="h-2 w-full rounded bg-emerald-500/20" />
          </div>
        ) : diskSpace.error ? (
          <div className="text-sm text-red-700 dark:text-red-400">{diskSpace.error}</div>
        ) : (
          <>
            <div className={`break-words text-3xl font-bold leading-tight [overflow-wrap:anywhere] ${colors.heading}`}>{t("disk.free", { value: diskSpace.freeFormatted })}</div>
            <div className="mt-0.5 flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs">
              <span className={colors.sub}>{t("disk.used", { value: diskSpace.usedFormatted })}</span>
              <span className={colors.sub}>{t("disk.total", { value: diskSpace.totalFormatted })}</span>
            </div>
            <Progress
              value={diskSpace.usedPercent}
              className="h-2 mt-2"
              indicatorClassName={getDiskStatusColor(severity)}
              aria-label={t("disk.title")}
            />
            <div className="flex flex-wrap justify-between items-center gap-x-3 gap-y-1 mt-1">
              <span className={`text-xs ${colors.sub}`}>{t("disk.percentFree", { value: diskSpace.freePercent })}</span>
              {(diskSpace.libraryShows > 0 || diskSpace.libraryMovies > 0) && (
                <span className={`text-xs ${colors.sub}`}>
                  {[
                    diskSpace.libraryShows > 0 ? t("disk.showsCount", { count: diskSpace.libraryShows }) : null,
                    diskSpace.libraryMovies > 0 ? t("disk.moviesCount", { count: diskSpace.libraryMovies }) : null,
                    diskSpace.libraryEpisodes > 0 ? t("disk.episodesCount", { count: diskSpace.libraryEpisodes }) : null,
                  ].filter(Boolean).join(", ")}
                </span>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
