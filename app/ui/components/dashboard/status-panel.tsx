"use client"

import React from "react"
import { t } from "@/lib/i18n"
import { coreStatusLabel } from "@/lib/health-verdict"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { StatusText } from "@/components/base/badge"
import { StatusBadge, statusDotClass, type StatusTone } from "@/components/base/status-badge"
import { ChevronRight, Settings, Shield } from "lucide-react"
import type { AppSettings, DVRStatusInfo } from "@/lib/types"

function getDvrHealthConfig(status: DVRStatusInfo | undefined, isDisabled: boolean): {
  dotClass: string
  title: string
} {
  if (isDisabled) return { dotClass: "bg-neutral", title: t("status.dvr.disabled") }
  if (!status) return { dotClass: "bg-neutral", title: t("status.dvr.unknown") }
  if (!status.connected) return { dotClass: "bg-danger", title: t("status.dvr.notConnected") }
  if (status.monitoring_ready === false) {
    if (status.monitoring_status === "dead") return { dotClass: "bg-danger", title: t("status.dvr.monitoringStopped") }
    if (status.monitoring_status === "starting") return { dotClass: "bg-warning", title: t("status.dvr.monitoringStarting") }
    return { dotClass: "bg-warning", title: t("status.dvr.monitoringStale") }
  }
  return { dotClass: "bg-success", title: t("status.dvr.healthy") }
}

const coreStatusTone: Record<string, StatusTone> = {
  Running: "success",
  Stopped: "danger",
  Starting: "warning",
  Backoff: "danger",
  Stopping: "warning",
  Exited: "danger",
  Fatal: "danger",
  Error: "neutral",
  Unknown: "neutral",
  Loading: "neutral",
}

/** Healthy reads as a quiet word beside the row's own dot. Everything else gets a chip. */
function StatusWord({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  if (tone === "success") return <StatusText tone={tone} dot={false}>{children}</StatusText>
  return <StatusBadge variant={tone}>{children}</StatusBadge>
}

// At very large text the trailing word moves under the name, so the name column never shrinks to one letter wide.
const rowClassName = "grid w-full min-w-0 grid-cols-[1rem_minmax(0,1fr)_auto] gap-x-2 px-4 py-3 text-left @max-[15rem]:grid-cols-[1rem_minmax(0,1fr)]"
const trailingClassName = "flex shrink-0 items-center gap-1 @max-[15rem]:col-start-2 @max-[15rem]:justify-self-start"
const interactiveRowClassName = `${rowClassName} press-row transition-colors hover:bg-fill`

/** Brackets a bare IPv6 host. The port is only worth showing when the host stands in for a name. */
export function formatDvrAddress(host: string, port: number | undefined, named: boolean): string {
  const shown = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host
  return named ? shown : `${shown}:${port || 8089}`
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
  noSignIn?: boolean
  /** False while the first read is in flight, so a row with no status says Loading, not Not Connected. */
  dataLoaded?: boolean
}

function formatDvrVersion(version: string | null): string | null {
  if (!version) return null
  const parts = version.split(".")
  return parts.length >= 3 ? `v${parts.slice(0, 3).join(".")}` : version
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
  noSignIn = false,
  dataLoaded = true,
}: StatusPanelProps) {
  const coreTone = coreStatusTone[coreProcessStatus] ?? "neutral"

  const alertTypeLabels = getAlertTypeLabels()
  const triggerLabels = activeAlertTypes.map(type => alertTypeLabels[type] || type)

  const allServers: AppSettings["dvr_servers"] = currentSettings?.dvr_servers ?? []
  const displayedServers = (selectedDvr && selectedDvr !== "all")
    ? allServers.filter((s) => s.id === selectedDvr)
    : allServers

  return (
    <Card className="@container flex h-full min-w-0 flex-col overflow-hidden">
      <CardHeader className="flex-shrink-0 px-4 pb-0 pt-3">
        <CardTitle className="grid min-w-0 grid-cols-[1rem_minmax(0,1fr)_auto] items-center gap-x-2">
          <Shield className="size-4 text-muted-foreground" aria-hidden="true" />
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
            {t("status.title")}
            {channelwatchVersion && (
              <span className="font-mono text-xs font-normal text-muted-foreground">v{channelwatchVersion}</span>
            )}
          </div>
          <button
            onClick={() => onNavigate?.('settings')}
            className="press-row -my-2 -mr-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground"
            aria-label={t("status.goToSettings")}
          >
            <Settings className="size-4" />
          </button>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col flex-grow p-0">
        <div className="grid min-w-0 flex-1 grid-cols-1 content-start divide-y divide-hairline">
          {/* Core Engine Status */}
          <div className={rowClassName}>
            <span aria-hidden="true" className={`mt-1.5 size-2 rounded-full ${statusDotClass[coreTone]}`} />
            <span className="min-w-0 text-sm font-medium">{t("status.coreEngine")}</span>
            <span className={trailingClassName}><StatusWord tone={coreTone}>{coreStatusLabel(coreProcessStatus)}</StatusWord></span>
          </div>

          {/* DVR Connections - one row per server */}
          {displayedServers.length > 0 ? (
            displayedServers.map((server, idx: number) => {
              const status = dvrStatusList.find((s) => s.id === server.id)
              const isConnected = status?.connected ?? false
              const versionDisplay = formatDvrVersion(status?.version ?? null)
              const isDisabled = server.enabled === false
              const health = getDvrHealthConfig(status, isDisabled)
              const statusUnknown = status === undefined && !isDisabled
              const connectionTone: StatusTone = isDisabled || statusUnknown ? "neutral" : isConnected ? "success" : "danger"
              const connectionLabel = isDisabled
                ? t("status.badge.disabled")
                : statusUnknown
                  ? dataLoaded ? t("status.dvr.unknown") : t("statusOverview.loading")
                  : isConnected ? t("status.badge.connected") : t("status.badge.notConnected")
              return (
                <button
                  key={server.id || idx}
                  onClick={() => onNavigate?.('settings:general')}
                  className={interactiveRowClassName}
                >
                  <span
                    className={`mt-1.5 size-2 rounded-full ${health.dotClass}`}
                    title={health.title}
                    aria-label={health.title}
                  />
                  <span className="block min-w-0 break-words">
                    <span className="block break-words text-sm font-medium">{server.name || server.host}</span>
                    <span className="block break-words font-mono text-xs text-muted-foreground">
                      {[formatDvrAddress(server.host, server.port, Boolean(server.name)), versionDisplay].filter(Boolean).join(" · ")}
                    </span>
                    {!isDisabled && !isConnected && status?.connection_error && (
                      <span className="block break-words text-xs text-danger-fg">{status.connection_error}</span>
                    )}
                  </span>
                  <span className={trailingClassName}>
                    <StatusWord tone={connectionTone}>{connectionLabel}</StatusWord>
                    <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  </span>
                </button>
              )
            })
          ) : (
            <button
              onClick={() => onNavigate?.('settings:general')}
              className={interactiveRowClassName}
            >
              <span aria-hidden="true" className="mt-1.5 size-2 rounded-full bg-danger" />
              <span className="block min-w-0 text-sm font-medium">{t("status.dvrConnection")}</span>
              <span className={trailingClassName}>
                <StatusBadge variant="danger">{t("status.notConfigured")}</StatusBadge>
                <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </span>
            </button>
          )}

          {/* Monitoring - always active, not clickable */}
          <div className={rowClassName}>
            <span aria-hidden="true" />
            <span className="block min-w-0">
              <span className="block text-sm font-medium">{t("status.monitoring")}</span>
              <span className="block text-xs leading-5 text-muted-foreground">{getMonitoringTypes().join(" · ")}</span>
            </span>
            <span className={`${trailingClassName} text-xs text-muted-foreground`}>{t("status.monitoringAlwaysActive")}</span>
          </div>

          {noSignIn && (
            <button
              data-testid="status-no-sign-in-row"
              onClick={() => onNavigate?.('settings:security')}
              className={interactiveRowClassName}
            >
              <span aria-hidden="true" className="mt-1.5 size-2 rounded-full bg-warning" />
              <span className="min-w-0 text-sm font-medium">{t("status.security")}</span>
              <span className={trailingClassName}>
                <span className="text-xs text-muted-foreground">{t("security.noSignInSet")}</span>
                <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </span>
            </button>
          )}

          {/* Notifications - clickable */}
          <button
            data-testid="status-notifications-row"
            onClick={() => onNavigate?.('settings:notifications')}
            className={interactiveRowClassName}
          >
            <span aria-hidden="true" />
            <span className="block min-w-0">
              <span className="block text-sm font-medium">{t("status.notifications")}</span>
              <span className="block text-xs leading-5 text-muted-foreground">
                {activeProviderNames.length > 0 ? activeProviderNames.join(" · ") : t("common.noneConfigured")}
              </span>
              {activeNotificationServices > 0 && triggerLabels.length > 0 && (
                <span className="block text-xs leading-5 text-muted-foreground">
                  {t("status.triggers")} {triggerLabels.join(" · ")}
                </span>
              )}
            </span>
            <span className={trailingClassName}>
              <span className="text-xs tabular-nums text-muted-foreground">{t("status.notificationsCount", { count: activeNotificationServices })}</span>
              <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </span>
          </button>
        </div>
      </CardContent>
    </Card>
  )
}
