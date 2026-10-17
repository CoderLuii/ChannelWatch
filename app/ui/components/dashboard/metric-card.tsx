"use client"

import React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { AlertCircle, Loader2 } from "lucide-react"
import type { LucideIcon } from "lucide-react"

interface MetricCardProps {
  title: string
  icon: LucideIcon
  value: string | number
  subtitle: string
  loading: boolean
  hasError: boolean
  gradientClasses: string
  iconBgClass: string
  iconColorClass: string
  valueColorClass: string
  subtitleColorClass: string
  loadingColorClass: string
  backgroundImage?: string
  children?: React.ReactNode
}

export function MetricCard({
  title,
  icon: Icon,
  value,
  subtitle,
  loading,
  hasError,
  gradientClasses: _gradientClasses,
  iconBgClass,
  iconColorClass,
  valueColorClass: _valueColorClass,
  subtitleColorClass: _subtitleColorClass,
  loadingColorClass: _loadingColorClass,
  backgroundImage,
  children,
}: MetricCardProps) {
  const [failedArtwork, setFailedArtwork] = React.useState("")
  const showBackgroundImage = Boolean(
    backgroundImage &&
    backgroundImage !== failedArtwork &&
    !/channelwatch-logo\.(?:png|svg|webp)(?:[?#]|$)/i.test(backgroundImage),
  )

  return (
    <Card className="relative overflow-hidden border-border/70 bg-card shadow-sm">
      {showBackgroundImage ? (
        // eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable
        <img
          src={backgroundImage}
          alt=""
          data-testid="metric-card-artwork"
          onError={() => setFailedArtwork(backgroundImage || "")}
          className="pointer-events-none absolute bottom-4 right-4 h-16 w-16 rounded-xl object-cover opacity-100 md:bottom-0 md:right-0 md:h-full md:w-[44%] md:rounded-none md:opacity-35 md:[mask-image:linear-gradient(to_right,transparent,black)]"
        />
      ) : null}
      <CardHeader className={`relative z-10 flex flex-row items-center justify-between space-y-0 px-4 pb-0 pt-3 md:pb-1 md:pt-4 ${showBackgroundImage ? "pr-20 md:pr-4" : ""}`}>
        <CardTitle className="text-[13px] font-medium text-muted-foreground">{title}</CardTitle>
        <div className={`${showBackgroundImage && !hasError ? "hidden md:flex" : "flex"} items-center gap-1`}>
          {hasError && <AlertCircle className="h-3.5 w-3.5 text-red-500" />}
          <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${iconBgClass}`}>
            <Icon className={`h-[18px] w-[18px] ${iconColorClass}`} />
          </div>
        </div>
      </CardHeader>
      <CardContent className={`relative z-10 px-4 pb-3 pt-0 md:pb-4 md:pt-1 ${showBackgroundImage ? "pr-20" : ""}`}>
        {loading ? (
          <div className="space-y-2 animate-pulse">
            <div className={`h-8 w-16 rounded ${iconBgClass}`} />
            <div className={`h-3 w-32 rounded ${iconBgClass}`} />
          </div>
        ) : children ? (
          children
        ) : (
          <>
            <div className="text-[28px] font-semibold leading-none tracking-tight tabular-nums text-foreground">{value}</div>
            <p className="mt-1.5 break-words text-[13px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">{subtitle}</p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
