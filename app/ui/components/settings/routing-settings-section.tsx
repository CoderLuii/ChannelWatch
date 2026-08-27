"use client"

import { GitMerge, Tv, Film, CircleDot, HardDrive, ShieldAlert } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Switch } from "@/components/base/switch"
import { TabsContent } from "@/components/base/tabs"
import { cn } from "@/lib/utils"
import { t } from "@/lib/i18n"
import type { AppSettings } from "@/lib/types"

export type RoutingState = Record<string, Record<string, Record<string, boolean> | null> | null>

export const ALL_ROUTING_DESTINATIONS = [
  "pushover",
  "discord",
  "email",
  "telegram",
  "slack",
  "gotify",
  "matrix",
  "custom",
  "webhook",
] as const

interface EventColumn {
  key: string
  label: string
  icon: React.ElementType
  accentClass: string
  headerClass: string
}

interface DestChannel {
  key: string
  label: string
}

function getEventColumns(): EventColumn[] {
  return [
    { key: "channel", label: t("routing.event.channel"), icon: Tv, accentClass: "text-blue-400", headerClass: "bg-blue-500/10 border-blue-400/20" },
    { key: "vod", label: t("type.vod"), icon: Film, accentClass: "text-purple-400", headerClass: "bg-purple-500/10 border-purple-400/20" },
    { key: "recording", label: t("type.recording"), icon: CircleDot, accentClass: "text-amber-400", headerClass: "bg-amber-500/10 border-amber-400/20" },
    { key: "disk", label: t("status.monitoring.disk"), icon: HardDrive, accentClass: "text-red-400", headerClass: "bg-red-500/10 border-red-400/20" },
    { key: "health", label: t("routing.event.health"), icon: ShieldAlert, accentClass: "text-orange-400", headerClass: "bg-orange-500/10 border-orange-400/20" },
  ]
}

function getAppriseDestChannels(): Array<{ key: string; label: string; settingKey: keyof AppSettings }> {
  return [
    { key: "pushover", label: t("provider.pushover.name"), settingKey: "apprise_pushover" },
    { key: "discord", label: t("provider.discord.name"), settingKey: "apprise_discord" },
    { key: "email", label: t("provider.email.name"), settingKey: "apprise_email" },
    { key: "telegram", label: t("provider.telegram.name"), settingKey: "apprise_telegram" },
    { key: "slack", label: t("provider.slack.name"), settingKey: "apprise_slack" },
    { key: "gotify", label: t("provider.gotify.name"), settingKey: "apprise_gotify" },
    { key: "matrix", label: t("provider.matrix.name"), settingKey: "apprise_matrix" },
    { key: "custom", label: t("provider.custom.name"), settingKey: "apprise_custom" },
  ]
}

export function getRoutingValue(routing: RoutingState, dvrId: string, eventKey: string, dest: string): boolean {
  if (!Object.prototype.hasOwnProperty.call(routing, dvrId)) return true
  const explicitDvrRoute = routing[dvrId]
  if (explicitDvrRoute === null || typeof explicitDvrRoute !== "object") return false
  if (!Object.prototype.hasOwnProperty.call(explicitDvrRoute, eventKey)) return true
  const explicitEventRoute = explicitDvrRoute[eventKey]
  if (explicitEventRoute === null || typeof explicitEventRoute !== "object") return false
  return typeof explicitEventRoute[dest] === "boolean" ? explicitEventRoute[dest] : false
}

export function setRoutingValue(routing: RoutingState, dvrId: string, eventKey: string, dest: string, value: boolean): RoutingState {
  const explicitDvrRoute = routing[dvrId]
  const explicitEventRoute = explicitDvrRoute?.[eventKey]
  const completeDefaults = Object.fromEntries(
    ALL_ROUTING_DESTINATIONS.map((destination) => [destination, getRoutingValue(routing, dvrId, eventKey, destination)]),
  ) as Record<string, boolean>
  return {
    ...routing,
    [dvrId]: {
      ...(explicitDvrRoute ?? {}),
      [eventKey]: {
        ...completeDefaults,
        ...(explicitEventRoute ?? {}),
        [dest]: value,
      },
    },
  }
}

export function resetDvrRouting(routing: RoutingState, dvrId: string): RoutingState {
  const updated = { ...routing }
  delete updated[dvrId]
  return updated
}

export function activeRoutingServers(servers: AppSettings["dvr_servers"]): AppSettings["dvr_servers"] {
  return (servers ?? []).filter((s) => !s.deleted_at && s.enabled !== false)
}

export function activeRoutingDestinations(settings: AppSettings): DestChannel[] {
  const hasWebhook = (settings.webhooks ?? []).some((w) => w.enabled && w.url)
  return [
    ...getAppriseDestChannels().filter((ch) => {
      const val = settings[ch.settingKey]
      return val && val !== ""
    }),
    ...(hasWebhook ? [{ key: "webhook", label: t("routing.webhook") }] : []),
  ]
}

interface RoutingCellProps {
  dvrId: string
  dvrName: string
  eventKey: string
  routing: RoutingState
  activeChannels: DestChannel[]
  onToggle: (dvrId: string, eventKey: string, dest: string, value: boolean) => void
}

function RoutingCell({ dvrId, dvrName, eventKey, routing, activeChannels, onToggle }: RoutingCellProps) {
  if (activeChannels.length === 0) {
    return <span className="text-xs text-muted-foreground italic">{t("settings.routing.noneConfigured")}</span>
  }

  return (
    <div className="flex flex-col gap-1.5 items-start">
      {activeChannels.map((ch) => {
        const on = getRoutingValue(routing, dvrId, eventKey, ch.key)
        return (
          <label key={ch.key} className="flex min-h-11 w-full items-center gap-2 md:min-h-0">
            <Switch
              checked={on}
              onCheckedChange={(v) => onToggle(dvrId, eventKey, ch.key, v)}
              aria-label={`${ch.label}, ${eventKey}, ${dvrName}`}
              className={cn("scale-[0.65] shrink-0", on ? "data-[state=checked]:bg-teal-600" : "")}
            />
            <span className={cn("text-xs leading-tight transition-colors", on ? "text-foreground" : "text-muted-foreground line-through")}>
              {ch.label}
            </span>
          </label>
        )
      })}
    </div>
  )
}

interface RoutingSettingsSectionProps {
  form: UseFormReturn<AppSettings>
}

export function RoutingSettingsSection({ form }: RoutingSettingsSectionProps) {
  const { watch, setValue } = form

  const routing: RoutingState = watch("notification_routing") ?? {}
  const servers = activeRoutingServers(watch("dvr_servers") ?? [])

  const activeChannels = activeRoutingDestinations({
    ...watch(),
    dvr_servers: watch("dvr_servers") ?? [],
    webhooks: watch("webhooks") ?? [],
  })

  function handleToggle(dvrId: string, eventKey: string, dest: string, value: boolean) {
    setValue("notification_routing", setRoutingValue(routing, dvrId, eventKey, dest, value), { shouldDirty: true })
  }

  function resetDvr(dvrId: string) {
    setValue("notification_routing", resetDvrRouting(routing, dvrId), { shouldDirty: true })
  }

  return (
    <TabsContent value="routing" className="space-y-6">
      <Card className="overflow-hidden border-teal-400/20 dark:border-teal-500/20 shadow-lg dark:shadow-teal-900/10">
        <div className="relative">
          <div className="absolute inset-0 bg-gradient-to-r from-teal-900/10 to-emerald-900/10 z-0" />
          <div className="absolute -top-14 -right-14 w-32 h-32 rounded-full bg-teal-500/10 backdrop-blur-3xl" />
          <CardHeader className="relative z-10 border-b border-teal-200/10">
            <div className="flex items-center gap-2">
              <div className="w-10 h-10 rounded-full bg-teal-500/20 backdrop-blur-sm flex items-center justify-center">
                <GitMerge className="h-5 w-5 text-teal-400" />
              </div>
              <div>
                <CardTitle>{t("settings.routing.title")}</CardTitle>
                <CardDescription>
                  {t("settings.routing.description")}
                </CardDescription>
              </div>
            </div>
          </CardHeader>
        </div>
        <CardContent className="relative z-10 pt-6">
          {servers.length === 0 ? (
            <div className="rounded-xl border border-dashed border-teal-400/30 bg-teal-500/5 p-8 text-center text-sm text-muted-foreground">
              {t("settings.routing.noDvr")}
            </div>
          ) : activeChannels.length === 0 ? (
            <div className="rounded-xl border border-dashed border-teal-400/30 bg-teal-500/5 p-8 text-center text-sm text-muted-foreground">
              {t("settings.routing.noDestinations")}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    <th className="text-left p-3 font-medium text-muted-foreground min-w-[140px]">{t("settings.routing.dvrColumn")}</th>
                     {getEventColumns().map((col) => {
                      const Icon = col.icon
                      return (
                        <th
                          key={col.key}
                          className={cn("p-2 text-center rounded-t-lg border-x border-t", col.headerClass)}
                          style={{ minWidth: `${Math.max(100, activeChannels.length * 18 + 32)}px` }}
                        >
                          <div className="flex items-center justify-center gap-1.5">
                            <Icon className={cn("h-3.5 w-3.5", col.accentClass)} />
                            <span className={cn("font-semibold text-xs uppercase tracking-wide", col.accentClass)}>{col.label}</span>
                          </div>
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody>
                  {servers.map((server, idx: number) => {
                    const hasCustomRouting = !!routing[server.id]
                    const isEven = idx % 2 === 0
                    return (
                      <tr
                        key={server.id}
                        className={cn("border-b border-border/40 transition-colors", isEven ? "bg-background/40" : "bg-muted/10")}
                      >
                        <td className="p-3 align-top">
                          <div className="flex flex-col gap-0.5">
                            <span className="font-medium text-sm truncate max-w-[160px]">
                              {server.name || server.host || server.id}
                            </span>
                            <span className="text-xs text-muted-foreground font-mono truncate max-w-[160px]">
                              {server.host}:{server.port}
                            </span>
                          </div>
                          {hasCustomRouting && (
                            <button
                              type="button"
                              onClick={() => resetDvr(server.id)}
                              className="mt-1.5 min-h-11 text-xs text-teal-400/80 hover:text-teal-400 transition-colors underline underline-offset-2 md:min-h-0"
                            >
                              {t("settings.routing.resetToDefaults")}
                            </button>
                          )}
                        </td>
                         {getEventColumns().map((col) => (
                          <td
                            key={col.key}
                            className={cn("p-3 border-x border-border/20 align-top", col.headerClass)}
                          >
                            <RoutingCell
                              dvrId={server.id}
                              dvrName={server.name || server.host || server.id}
                              eventKey={col.key}
                              routing={routing}
                              activeChannels={activeChannels}
                              onToggle={handleToggle}
                            />
                          </td>
                        ))}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {activeChannels.length > 0 && (
            <p className="mt-4 text-xs text-muted-foreground">
              {t("settings.routing.managementHint", { tab: t("settings.routing.notificationsTab") })}
            </p>
          )}
        </CardContent>
      </Card>
    </TabsContent>
  )
}
