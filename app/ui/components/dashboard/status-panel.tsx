"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Badge } from "@/components/base/badge"
import { AlertTriangle, Settings, Shield, Stethoscope } from "lucide-react"
import type { AppSettings, DVRStatusInfo } from "@/lib/types"

function getDvrHealthConfig(status: DVRStatusInfo | undefined, isDisabled: boolean): {
  dotClass: string
  ping: boolean
  title: string
} {
  if (isDisabled) return { dotClass: "bg-gray-400", ping: false, title: t("status.dvr.disabled") }
  if (!status) return { dotClass: "bg-gray-400", ping: false, title: t("status.dvr.unknown") }
  if (!status.connected) return { dotClass: "bg-red-500", ping: false, title: t("status.dvr.notConnected") }
  if (status.monitoring_ready === false) {
    if (status.monitoring_status === "dead") return { dotClass: "bg-red-500", ping: false, title: t("status.dvr.monitoringStopped") }
    return { dotClass: "bg-amber-500", ping: false, title: t("status.dvr.monitoringStale") }
  }
  return { dotClass: "bg-green-500", ping: true, title: t("status.dvr.healthy") }
}

const coreStatusConfig: Record<string, { color: string; bgClass: string; dotClass: string }> = {
  Running: { color: "text-white", bgClass: "bg-green-700 hover:bg-green-800", dotClass: "bg-green-600" },
  Stopped: { color: "text-white", bgClass: "bg-red-700 dark:bg-red-600", dotClass: "bg-red-500" },
  Starting: { color: "text-amber-50", bgClass: "bg-amber-600 hover:bg-amber-700", dotClass: "bg-amber-500" },
  Backoff: { color: "text-white", bgClass: "bg-red-700 dark:bg-red-600", dotClass: "bg-red-500" },
  Stopping: { color: "text-amber-50", bgClass: "bg-amber-600 hover:bg-amber-700", dotClass: "bg-amber-500" },
  Exited: { color: "text-white", bgClass: "bg-red-700 dark:bg-red-600", dotClass: "bg-red-500" },
  Fatal: { color: "text-white", bgClass: "bg-red-700 dark:bg-red-600", dotClass: "bg-red-500" },
  Error: { color: "text-white", bgClass: "bg-red-700 dark:bg-red-600", dotClass: "bg-red-500" },
  Unknown: { color: "text-gray-50", bgClass: "bg-gray-500 hover:bg-gray-600", dotClass: "bg-gray-400" },
  Loading: { color: "text-gray-50", bgClass: "bg-gray-400 hover:bg-gray-500", dotClass: "bg-gray-400" },
}

const providerColors: Record<string, string> = {
  Discord: "border-indigo-400 text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-950/30",
  Pushover: "border-blue-400 text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/30",
  Email: "border-gray-400 text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-950/30",
  Telegram: "border-sky-400 text-sky-600 dark:text-sky-400 bg-sky-50 dark:bg-sky-950/30",
  Slack: "border-purple-400 text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-950/30",
  Gotify: "border-teal-400 text-teal-600 dark:text-teal-400 bg-teal-50 dark:bg-teal-950/30",
  Matrix: "border-emerald-400 text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/30",
  Custom: "border-orange-400 text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-950/30",
}

function getMonitoringTypes() {
  return [
    t("status.monitoring.liveTV"),
    t("status.monitoring.disk"),
    t("status.monitoring.vod"),
    t("status.monitoring.recordings"),
  ]
}

function getAlertTypeLabels(): Record<string, string> {
  return {
    [t("alerts.channelWatching.title")]: t("status.monitoring.liveTV"),
    [t("alerts.diskSpace.title")]: t("status.monitoring.disk"),
    [t("alerts.vodWatching.title")]: t("status.monitoring.vod"),
    [t("alerts.recordingEvents.title")]: t("status.monitoring.recordings"),
  }
}

interface StatusPanelProps {
  dvrStatusList: DVRStatusInfo[]
  activeNotificationServices: number
  activeProviderNames: string[]
  activeAlertTypes: string[]
  coreProcessStatus: string
  channelwatchVersion: string
  currentSettings: AppSettings | null
  onNavigate: ((view: string) => void) | undefined
  selectedDvr?: string
}

function formatDvrVersion(version: string | null): string | null {
  if (!version) return null
  const parts = version.split(".")
  return parts.length >= 3 ? `v${parts.slice(0, 3).join(".")}` : version
}

function buildMonitoringBanner(dvrStatusList: DVRStatusInfo[]) {
  const degraded = dvrStatusList.filter((dvr) => dvr.monitoring_ready === false)
  if (!degraded.length) return null

  const stale = degraded.filter((dvr) => dvr.monitoring_status === "stale")
  const dead = degraded.filter((dvr) => dvr.monitoring_status === "dead")
  const lead = stale[0] ?? dead[0] ?? degraded[0]
  const label = stale.length
    ? t("status.banner.stale", { name: lead.name })
    : dead.length
      ? t("status.banner.stopped", { name: lead.name })
      : t("status.banner.degraded", { name: lead.name })
  const suffix = degraded.length > 1 ? t("status.banner.more", { count: degraded.length - 1 }) : ""

  return {
    label,
    detail: lead.monitoring_reason || t("status.banner.defaultDetail"),
    suffix,
  }
}

export function StatusPanel({
  dvrStatusList,
  activeNotificationServices,
  activeProviderNames,
  activeAlertTypes,
  coreProcessStatus,
  channelwatchVersion,
  currentSettings,
  onNavigate,
  selectedDvr,
}: StatusPanelProps) {
  const statusStyle = coreStatusConfig[coreProcessStatus] || coreStatusConfig.Unknown
  const isRunning = coreProcessStatus === "Running"

  const alertTypeLabels = getAlertTypeLabels()
  const triggerLabels = activeAlertTypes.map(type => alertTypeLabels[type] || type)
  const monitoringBanner = buildMonitoringBanner(dvrStatusList)

  const allServers: AppSettings["dvr_servers"] = currentSettings?.dvr_servers ?? []
  const displayedServers = (selectedDvr && selectedDvr !== "all")
    ? allServers.filter((s) => s.id === selectedDvr)
    : allServers

  return (
    <Card className="overflow-hidden flex flex-col h-full">
      <CardHeader className="pb-1 pt-3 flex-shrink-0">
        <CardTitle className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-primary" />
            {t("status.title")}
            {channelwatchVersion && (
              <span className="text-[10px] text-muted-foreground font-normal">v{channelwatchVersion}</span>
            )}
          </div>
          <button
            onClick={() => onNavigate?.('settings')}
            className="text-muted-foreground hover:text-foreground transition-colors"
            aria-label={t("status.goToSettings")}
          >
            <Settings className="h-3.5 w-3.5" />
          </button>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0 flex-grow">
        {monitoringBanner && (
          <div className="mx-3 mb-3 rounded-xl border border-red-500/40 bg-red-950/90 px-3 py-2.5 text-red-50 shadow-[0_12px_30px_rgba(127,29,29,0.22)]">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 gap-2.5">
                <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-200" />
                <div className="min-w-0">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-red-200/90">{t("status.monitoringDegraded")}</div>
                  <div className="mt-0.5 text-sm font-medium leading-tight">
                    {monitoringBanner.label}
                    {monitoringBanner.suffix && <span className="text-red-200/80">{monitoringBanner.suffix}</span>}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-red-100/85">{monitoringBanner.detail}</p>
                </div>
              </div>
              <button
                onClick={() => onNavigate?.('diagnostics')}
                className="inline-flex items-center gap-1 rounded-full border border-red-300/30 bg-red-400/10 px-2.5 py-1 text-[11px] font-medium text-red-50 transition-colors hover:bg-red-400/20"
              >
                <Stethoscope className="h-3 w-3" />
                {t("status.diagnose")}
              </button>
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 divide-y h-full">
          {/* Core Engine Status */}
          <div className="p-3 hover:bg-muted/50 transition-colors flex items-center justify-between flex-grow">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2.5 w-2.5">
                {isRunning && (
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${statusStyle.dotClass} opacity-75`} />
                )}
                <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${statusStyle.dotClass}`} />
              </span>
              <span className="text-sm font-medium">{t("status.coreEngine")}</span>
            </div>
            <Badge className={`${statusStyle.bgClass} ${statusStyle.color}`}>
              {coreProcessStatus}
            </Badge>
          </div>

          {/* DVR Connections - one row per server */}
          {displayedServers.length > 0 ? (
            displayedServers.map((server, idx: number) => {
              const status = dvrStatusList.find((s) => s.id === server.id)
              const isConnected = status?.connected ?? false
              const versionDisplay = formatDvrVersion(status?.version ?? null)
              const isDisabled = server.enabled === false
              const health = getDvrHealthConfig(status, isDisabled)
              return (
                <button
                  key={server.id || idx}
                  onClick={() => onNavigate?.('settings:general')}
                  className="p-3 hover:bg-muted/50 transition-colors flex items-center justify-between flex-grow text-left w-full"
                >
                  <div className="flex items-start gap-2">
                    <span
                      className="relative flex h-2 w-2 mt-1.5 flex-shrink-0"
                      title={health.title}
                      aria-label={health.title}
                    >
                      {health.ping && (
                        <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${health.dotClass} opacity-75`} />
                      )}
                      <span className={`relative inline-flex rounded-full h-2 w-2 ${health.dotClass}`} />
                    </span>
                    <div>
                      <div className="text-sm font-medium">{server.name || server.host}</div>
                      <div className="text-xs text-muted-foreground">
                        {server.host}:{server.port || 8089}
                      </div>
                      {versionDisplay && (
                      <div className="text-[10px] text-muted-foreground">{versionDisplay}</div>
                      )}
                    </div>
                  </div>
                  <Badge
                    variant={isConnected ? "default" : "secondary"}
                    className={isConnected ? "bg-green-700 text-white hover:bg-green-800" : isDisabled ? "" : "bg-red-700 text-white dark:bg-red-600"}
                  >
                    {isDisabled ? t("status.badge.disabled") : isConnected ? t("status.badge.connected") : t("status.badge.notConnected")}
                  </Badge>
                </button>
              )
            })
          ) : (
            <button
              onClick={() => onNavigate?.('settings:general')}
              className="p-3 hover:bg-muted/50 transition-colors flex items-center justify-between flex-grow text-left w-full"
            >
              <div>
                <div className="text-sm font-medium">{t("status.dvrConnection")}</div>
                <div className="text-xs text-muted-foreground">{t("common.notConfigured")}</div>
              </div>
              <Badge variant="destructive" className="bg-red-700 text-white dark:bg-red-600">{t("status.notConfigured")}</Badge>
            </button>
          )}

          {/* Monitoring - always active, not clickable */}
          <div className="p-3 flex-grow">
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-sm font-medium">{t("status.monitoring")}</div>
              <span className="text-xs text-muted-foreground">{t("status.monitoringAlwaysActive")}</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {getMonitoringTypes().map((type) => (
                <Badge key={type} variant="secondary" className="text-[10px] py-0 h-4 px-1 font-normal leading-none">
                  {type}
                </Badge>
              ))}
            </div>
          </div>

          {/* Notifications - clickable */}
          <button
            onClick={() => onNavigate?.('settings:notifications')}
            className="p-3 hover:bg-muted/50 transition-colors flex-grow text-left w-full"
          >
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-sm font-medium">{t("status.notifications")}</div>
              <span className="text-xs text-muted-foreground">{t("status.notificationsCount", { count: activeNotificationServices })}</span>
            </div>
            {activeProviderNames.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {activeProviderNames.map((name) => (
                  <Badge
                    key={name}
                    variant="outline"
                    className={`text-xs py-0 h-5 px-1.5 font-normal ${providerColors[name] || "border-gray-400 text-gray-600 dark:text-gray-400"}`}
                  >
                    {name}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{t("common.noneConfigured")}</p>
            )}
            {triggerLabels.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                <span className="text-[10px] text-muted-foreground mr-0.5">{t("status.triggers")}</span>
                {triggerLabels.map((label) => (
                  <Badge key={label} variant="secondary" className="text-[10px] py-0 h-4 px-1 font-normal leading-none">
                    {label}
                  </Badge>
                ))}
              </div>
            )}
          </button>
        </div>
      </CardContent>
    </Card>
  )
}
