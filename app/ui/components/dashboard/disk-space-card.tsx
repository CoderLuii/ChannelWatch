"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Figure } from "@/components/base/figure"
import { Progress } from "@/components/base/progress"
import { StatusBadge, type StatusTone } from "@/components/base/status-badge"
import { HardDrive } from "lucide-react"
import { formatCount, formatDiskSizeFromGB } from "@/lib/utils"

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
  totalGb?: number
  freeGb?: number
}

interface DiskSpaceCardProps {
  diskSpace: DiskSpaceState
  loading: boolean
  hasError: boolean
  serverSeverity?: DiskSeverity
  warningThresholdPercent?: number
  criticalThresholdPercent?: number
  thresholdPercent?: number
  warningThresholdGb?: number
  criticalThresholdGb?: number
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
  const resolvedCriticalThreshold = criticalThresholdPercent ?? thresholdPercent ?? 5
  const resolvedWarningThreshold = Math.max(
    warningThresholdPercent ?? (thresholdPercent != null ? thresholdPercent * 2 : 10),
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

/** One color for one disk level, shared by every screen that shows it. */
export const diskSeverityColors: Record<DiskSeverity, { bar: string; text: string; tone: StatusTone }> = {
  normal: { bar: "bg-neutral", text: "text-foreground", tone: "neutral" },
  warning: { bar: "bg-warning", text: "text-warning-fg", tone: "warning" },
  critical: { bar: "bg-danger", text: "text-danger-fg", tone: "danger" },
}

/**
 * The free space at which an alert level starts. A level triggers on percent or GB,
 * whichever comes first, so the floor is the larger of the two. Base 1024, like the backend.
 */
export function diskFreeFloorGb(totalGb: number, percent?: number, gb?: number): number {
  return Math.max((totalGb * (percent ?? 0)) / 100, gb ?? 0)
}

/** Where a free-space floor falls on a bar that fills as the disk fills. */
export function diskTickPosition(totalGb: number, floorGb: number): number {
  if (totalGb <= 0) return 0
  return Math.min(100, Math.max(0, 100 - (floorGb / totalGb) * 100))
}

export function DiskSpaceCard({
  diskSpace,
  loading,
  hasError,
  serverSeverity,
  warningThresholdPercent,
  criticalThresholdPercent,
  thresholdPercent,
  warningThresholdGb,
  criticalThresholdGb,
}: DiskSpaceCardProps) {
  const severity = resolveDiskSeverity({
    usedPercent: diskSpace.usedPercent,
    serverSeverity,
    warningThresholdPercent,
    criticalThresholdPercent,
    thresholdPercent,
  })
  const colors = diskSeverityColors[severity]
  const totalGb = diskSpace.totalGb ?? 0
  const thresholds = resolveDiskThresholds(warningThresholdPercent, criticalThresholdPercent, thresholdPercent)
  const warningFloor = totalGb > 0 ? diskFreeFloorGb(totalGb, thresholds.warningThresholdPercent, warningThresholdGb) : 0
  const criticalFloor = totalGb > 0 ? diskFreeFloorGb(totalGb, thresholds.criticalThresholdPercent, criticalThresholdGb) : 0
  const meterLine = warningFloor > 0
    ? t("disk.meterLine", { free: diskSpace.freeFormatted, floor: formatDiskSizeFromGB(warningFloor) })
    : t("disk.left", { value: diskSpace.freeFormatted })

  return (
    <Card className="p-4">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 p-0">
        <CardTitle className="min-w-0 text-xs font-normal text-muted-foreground">{t("dashboard.tile.diskLeft")}</CardTitle>
        <div className="flex shrink-0 items-center gap-1.5">
          {!loading && !hasError && severity !== "normal" ? <StatusBadge variant={colors.tone}>{t(`disk.severity.${severity}`)}</StatusBadge> : null}
          <HardDrive className="size-4 text-muted-foreground" aria-hidden="true" />
        </div>
      </CardHeader>
      <CardContent className="p-0 pt-1.5">
        {loading ? (
          <div className="animate-pulse">
            <div className="h-7 w-16 rounded-thumb bg-muted" />
            <div className="mt-1.5 flex h-10 items-center md:h-5">
              <div className="h-3 w-32 max-w-full rounded-thumb bg-muted" />
            </div>
          </div>
        ) : diskSpace.error ? (
          <div>
            <span className="block text-title font-semibold leading-none tracking-tight tabular-nums text-muted-foreground">–</span>
            <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{diskSpace.error}</p>
          </div>
        ) : (
          <>
            <Figure value={diskSpace.freeFormatted} className="block break-words text-title font-semibold leading-none tracking-tight text-foreground [overflow-wrap:anywhere]" />
            <div className="mt-1.5 flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>{t("disk.used", { value: diskSpace.usedFormatted })}</span>
              <span>{t("disk.total", { value: diskSpace.totalFormatted })}</span>
            </div>
            <div className="relative mt-2">
              <Progress
                value={diskSpace.usedPercent}
                className="h-1.5"
                indicatorClassName={colors.bar}
                aria-label={t("disk.title")}
                aria-valuetext={meterLine}
              />
              {warningFloor > 0 ? <span data-testid="disk-tick-warning" aria-hidden="true" className="absolute -inset-y-0.5 w-px bg-foreground/50" style={{ left: `${diskTickPosition(totalGb, warningFloor)}%` }} /> : null}
              {criticalFloor > 0 ? <span data-testid="disk-tick-critical" aria-hidden="true" className="absolute -inset-y-0.5 w-px bg-foreground/50" style={{ left: `${diskTickPosition(totalGb, criticalFloor)}%` }} /> : null}
            </div>
            <p className="mt-1.5 text-xs text-muted-foreground">{meterLine}</p>
            {(diskSpace.libraryShows > 0 || diskSpace.libraryMovies > 0) && (
              <p className="mt-1 text-xs text-muted-foreground">
                {[
                  diskSpace.libraryShows > 0 ? t(diskSpace.libraryShows === 1 ? "disk.showsCountOne" : "disk.showsCount", { count: formatCount(diskSpace.libraryShows) }) : null,
                  diskSpace.libraryMovies > 0 ? t(diskSpace.libraryMovies === 1 ? "disk.moviesCountOne" : "disk.moviesCount", { count: formatCount(diskSpace.libraryMovies) }) : null,
                  diskSpace.libraryEpisodes > 0 ? t(diskSpace.libraryEpisodes === 1 ? "disk.episodesCountOne" : "disk.episodesCount", { count: formatCount(diskSpace.libraryEpisodes) }) : null,
                ].filter(Boolean).join(", ")}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
