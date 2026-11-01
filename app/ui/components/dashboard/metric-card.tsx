"use client"

import React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Figure } from "@/components/base/figure"
import { ChevronRight, type LucideIcon } from "lucide-react"
import { t } from "@/lib/i18n"
import { cn, formatCount } from "@/lib/utils"
import type { StatusTone } from "@/components/base/status-badge"

export interface KpiThresholds {
  warning: number
  danger: number
  /** "above" (default): higher values are worse. "below": lower values are worse. */
  direction?: "above" | "below"
}

export function kpiTone(value: unknown, thresholds?: KpiThresholds): StatusTone | null {
  if (!thresholds) return null
  const numeric = typeof value === "number" ? value : Number(value)
  if (typeof value === "boolean" || value === "" || value === null || !Number.isFinite(numeric)) return null
  const below = thresholds.direction === "below"
  const crosses = (limit: number) => (below ? numeric <= limit : numeric >= limit)
  if (crosses(thresholds.danger)) return "danger"
  if (crosses(thresholds.warning)) return "warning"
  return "success"
}

const toneValueClass: Record<StatusTone, string> = {
  success: "text-foreground",
  warning: "text-warning-fg",
  danger: "text-danger-fg",
  info: "text-foreground",
  neutral: "text-foreground",
}

const toneCardClass: Record<StatusTone, string> = {
  success: "",
  warning: "border-warning-border",
  danger: "border-danger-border",
  info: "",
  neutral: "",
}

interface MetricCardProps {
  title: string
  /** Shorter label for the half-width phone tile. */
  mobileTitle?: string
  icon: LucideIcon
  /** Null until something has actually been read. Never shown as zero. */
  value: string | number | null
  subtitle: string
  /** When the value on screen was last good, for when the latest read failed. */
  asOf?: string
  /** Makes the whole tile a button. */
  onActivate?: () => void
  activateLabel?: string
  loading: boolean
  hasError: boolean
  backgroundImage?: string
  thresholds?: KpiThresholds
  className?: string
  children?: React.ReactNode
}

export function KpiTile({
  title,
  mobileTitle,
  icon: Icon,
  value,
  subtitle,
  loading,
  hasError,
  asOf,
  onActivate,
  activateLabel,
  backgroundImage,
  thresholds,
  className,
  children,
}: MetricCardProps) {
  const tone = loading || hasError ? null : kpiTone(value, thresholds)
  const [failedArtwork, setFailedArtwork] = React.useState("")
  const showBackgroundImage = Boolean(
    backgroundImage &&
    backgroundImage !== failedArtwork &&
    !/channelwatch-logo\.(?:png|svg|webp)(?:[?#]|$)/i.test(backgroundImage),
  )
  const valueClass = tone ? toneValueClass[tone] : hasError || value === 0 || value === null ? "text-muted-foreground" : "text-foreground"
  const shownSubtitle = hasError ? (value === null || !asOf ? t("dashboard.tile.unknownReason") : t("dashboard.tile.asOf", { time: asOf })) : subtitle

  return (
    <Card data-tile="" data-tone={tone ?? undefined} className={cn("relative overflow-hidden p-4", tone ? toneCardClass[tone] : "", className)}>
      {showBackgroundImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable
        <img
          src={backgroundImage}
          alt=""
          data-testid="metric-card-artwork"
          onError={() => setFailedArtwork(backgroundImage || "")}
          className="pointer-events-none absolute bottom-0 right-0 hidden object-cover xl:block xl:h-full xl:w-[44%] xl:opacity-35 xl:[mask-image:linear-gradient(to_right,transparent,black)]"
        />
      ) : null}
      {onActivate ? (
        <button
          type="button"
          aria-label={activateLabel}
          onClick={onActivate}
          className="press-row absolute inset-0 z-20 rounded-group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        />
      ) : null}
      <CardHeader className="relative z-10 flex flex-row items-center justify-between gap-2 space-y-0 p-0">
        <CardTitle className="min-w-0 text-xs font-normal text-muted-foreground">
          {mobileTitle ? (
            <>
              <span className="md:hidden" aria-hidden="true">{mobileTitle}</span>
              <span className="max-md:sr-only">{title}</span>
            </>
          ) : title}
        </CardTitle>
        <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </CardHeader>
      <CardContent className={cn("relative z-10 p-0 pt-1.5", showBackgroundImage && "xl:pr-20")}>
        {loading ? (
          <div className="animate-pulse">
            <div className="h-7 w-16 rounded-thumb bg-muted" />
            <div className="mt-1.5 flex h-10 items-center md:h-5">
              <div className="h-3 w-32 max-w-full rounded-thumb bg-muted" />
            </div>
          </div>
        ) : children ? (
          children
        ) : (
          <>
            <Figure value={value === null ? "–" : typeof value === "number" ? formatCount(value) : value} className={cn("block text-title font-semibold leading-none tracking-tight", valueClass)} />
            <div className="mt-1.5 flex min-w-0 items-start gap-1">
              <p className="line-clamp-2 min-h-10 min-w-0 break-words text-xs leading-5 text-muted-foreground md:min-h-0 xl:line-clamp-3" title={shownSubtitle}>{shownSubtitle}</p>
              {onActivate ? <ChevronRight className="ml-auto mt-0.5 size-4 shrink-0 text-muted-foreground xl:hidden" aria-hidden="true" /> : null}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export const MetricCard = KpiTile
