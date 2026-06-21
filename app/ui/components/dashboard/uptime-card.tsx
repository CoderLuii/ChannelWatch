"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { AlertCircle, Clock, Loader2 } from "lucide-react"
import type { DVRStatusInfo } from "@/lib/types"

interface UptimeCardProps {
  coreUptime: { days: number; hours: number; minutes: number; seconds: number }
  containerUptimeDisplay: string
  dvrStatusList: DVRStatusInfo[]
  loading: boolean
  hasError: boolean
}

export function UptimeCard({
  coreUptime,
  containerUptimeDisplay,
  dvrStatusList,
  loading,
  hasError,
}: UptimeCardProps) {
  const primaryDvr = dvrStatusList.find(d => d.connected) ?? dvrStatusList[0] ?? null
  return (
    <Card className="bg-gradient-to-br from-purple-50 to-purple-100 dark:from-purple-950 dark:to-purple-900 border-purple-200 dark:border-purple-800">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{t("uptime.title")}</CardTitle>
        <div className="flex items-center gap-1">
          {hasError && <AlertCircle className="h-3.5 w-3.5 text-red-500" />}
          <div className="rounded-full bg-purple-500/20 p-1">
            <Clock className="h-4 w-4 text-purple-600 dark:text-purple-400" />
          </div>
        </div>
      </CardHeader>
      <CardContent className="py-2">
        {loading ? (
          <div className="space-y-2 animate-pulse">
            <div className="h-3 w-20 rounded bg-purple-500/20" />
            <div className="flex gap-3">
              <div className="h-8 w-10 rounded bg-purple-500/20" />
              <div className="h-8 w-10 rounded bg-purple-500/20" />
              <div className="h-8 w-10 rounded bg-purple-500/20" />
              <div className="h-8 w-10 rounded bg-purple-500/20" />
            </div>
            <div className="h-3 w-32 rounded bg-purple-500/20" />
          </div>
        ) : (
          <>
            <div className="text-[10px] uppercase font-medium text-purple-500 dark:text-purple-400 mb-1">{t("uptime.coreProcess")}</div>
            <div className="grid grid-cols-4 gap-1.5">
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{coreUptime.days}</div>
                <div className="text-[10px] uppercase font-medium text-purple-700 dark:text-purple-400">{t("uptime.days")}</div>
              </div>
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{coreUptime.hours}</div>
                <div className="text-[10px] uppercase font-medium text-purple-700 dark:text-purple-400">{t("uptime.hours")}</div>
              </div>
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{coreUptime.minutes}</div>
                <div className="text-[10px] uppercase font-medium text-purple-700 dark:text-purple-400">{t("uptime.mins")}</div>
              </div>
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{coreUptime.seconds}</div>
                <div className="text-[10px] uppercase font-medium text-purple-700 dark:text-purple-400">{t("uptime.secs")}</div>
              </div>
            </div>

            <div className="mt-3 space-y-1 border-t border-purple-200 dark:border-purple-700 pt-2">
              <div className="flex justify-between text-[11px]">
                <span className="text-purple-600 dark:text-purple-400">{t("uptime.container")}</span>
                <span className="font-medium text-purple-700 dark:text-purple-300">
                  {containerUptimeDisplay}
                </span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span className="text-purple-600 dark:text-purple-400">{t("uptime.dvrConnected")}</span>
                <span className="font-medium text-purple-700 dark:text-purple-300">
                  {primaryDvr
                    ? primaryDvr.connected
                      ? primaryDvr.version
                        ? (() => {
                            const parts = primaryDvr.version.split(".")
                            return parts.length >= 3 ? `v${parts.slice(0, 3).join(".")}` : primaryDvr.version
                          })()
                        : t("common.connected")
                      : t("common.disconnected")
                    : t("uptime.notConfigured")}
                </span>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
