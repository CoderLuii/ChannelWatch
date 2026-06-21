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
  gradientClasses,
  iconBgClass,
  iconColorClass,
  valueColorClass,
  subtitleColorClass,
  loadingColorClass,
  backgroundImage,
  children,
}: MetricCardProps) {
  return (
    <Card className={`${gradientClasses} relative overflow-hidden`}>
      {backgroundImage && (
        // eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable
        <img
          src={backgroundImage}
          alt=""
          className="absolute inset-0 w-full h-full object-cover opacity-[0.12] pointer-events-none"
        />
      )}
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2 relative z-10">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <div className="flex items-center gap-1">
          {hasError && <AlertCircle className="h-3.5 w-3.5 text-red-500" />}
          <div className={`rounded-full ${iconBgClass} p-1`}>
            <Icon className={`h-4 w-4 ${iconColorClass}`} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="py-2 relative z-10">
        {loading ? (
          <div className="space-y-2 animate-pulse">
            <div className={`h-8 w-16 rounded ${iconBgClass}`} />
            <div className={`h-3 w-32 rounded ${iconBgClass}`} />
          </div>
        ) : children ? (
          children
        ) : (
          <>
            <div className={`text-3xl font-bold ${valueColorClass}`}>{value}</div>
            <p className={`text-xs ${subtitleColorClass}`}>{subtitle}</p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
