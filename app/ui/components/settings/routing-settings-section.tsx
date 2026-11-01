"use client"

import { GitMerge, Tv, Film, CircleDot, HardDrive, ShieldAlert } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Button } from "@/components/base/button"
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
    { key: "channel", label: t("routing.event.channel"), icon: Tv, accentClass: "text-muted-foreground", headerClass: "" },
    { key: "vod", label: t("type.vod"), icon: Film, accentClass: "text-muted-foreground", headerClass: "" },
    { key: "recording", label: t("type.recording"), icon: CircleDot, accentClass: "text-muted-foreground", headerClass: "" },
    { key: "disk", label: t("status.monitoring.disk"), icon: HardDrive, accentClass: "text-muted-foreground", headerClass: "" },
    { key: "health", label: t("routing.event.health"), icon: ShieldAlert, accentClass: "text-muted-foreground", headerClass: "" },
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
              className={cn("scale-[0.8] shrink-0", on ? "" : "")}
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

export function RoutingSettingsSection({ form, onGoToNotifications }: RoutingSettingsSectionProps & { onGoToNotifications?: () => void }) {
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
      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
            <div className="flex items-center gap-2">
              <GitMerge className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div>
                <CardTitle>{t("settings.routing.title")}</CardTitle>
                <CardDescription>
                  {t("settings.routing.description")}
                </CardDescription>
              </div>
            </div>
          </CardHeader>
        <CardContent className="pt-4">
          {servers.length === 0 ? (
            <div className="rounded-control border border-dashed border-hairline p-8 text-center text-sm text-muted-foreground">
              {t("settings.routing.noDvr")}
            </div>
          ) : activeChannels.length === 0 ? (
            <div className="rounded-control border border-dashed border-hairline p-8 text-center text-sm text-muted-foreground">
              <p>{t("settings.routing.noDestinations")}</p>
              {onGoToNotifications ? (
                <Button type="button" variant="outline" size="sm" className="mt-3 min-h-11" onClick={onGoToNotifications}>{t("settings.goToNotifications")}</Button>
              ) : null}
            </div>
          ) : (
            <>
            <div data-testid="routing-mobile-list" className="space-y-4 md:hidden">
              {servers.map((server) => {
                const dvrName = server.name || server.host || server.id
                return (
                  <section key={server.id} aria-label={dvrName} className="min-w-0 space-y-3">
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold" title={dvrName}>{dvrName}</h3>
                        <p className="truncate font-mono text-xs text-muted-foreground">{server.host}:{server.port}</p>
                      </div>
                      {routing[server.id] ? (
                        <button
                          type="button"
                          onClick={() => resetDvr(server.id)}
                          className="min-h-11 shrink-0 text-xs text-muted-foreground underline underline-offset-2"
                        >
                          {t("settings.routing.resetToDefaults")}
                        </button>
                      ) : null}
                    </div>
                    {getEventColumns().map((col) => {
                      const Icon = col.icon
                      return (
                        <div key={col.key} className="overflow-hidden rounded-control border border-hairline bg-fill">
                          <div className={"flex items-center gap-2 border-b border-hairline px-3 py-2"}>
                            <Icon className={cn("h-4 w-4 shrink-0", col.accentClass)} aria-hidden="true" />
                            <span className={cn("min-w-0 truncate text-xs font-medium", col.accentClass)}>{col.label}</span>
                          </div>
                          <ul className="divide-y divide-hairline">
                            {activeChannels.map((ch) => {
                              const on = getRoutingValue(routing, server.id, col.key, ch.key)
                              return (
                                <li key={ch.key}>
                                  <label className="flex min-h-[48px] cursor-pointer items-center justify-between gap-3 px-3 py-1.5">
                                    <span className={cn("min-w-0 truncate text-sm", on ? "text-foreground" : "text-muted-foreground")}>{ch.label}</span>
                                    <Switch
                                      checked={on}
                                      onCheckedChange={(v) => handleToggle(server.id, col.key, ch.key, v)}
                                      aria-label={`${ch.label}, ${col.label}, ${dvrName}`}
                                      className="shrink-0"
                                    />
                                  </label>
                                </li>
                              )
                            })}
                          </ul>
                        </div>
                      )
                    })}
                  </section>
                )
              })}
            </div>
            <div className="hidden overflow-x-auto md:block" tabIndex={0} role="region" aria-label={t("settings.routing.title")}>
              <p className="mb-2 text-xs text-muted-foreground xl:hidden">{t("settings.routing.scrollHint")}</p>
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    <th className="text-left p-3 font-medium text-muted-foreground min-w-[140px]">{t("settings.routing.dvrColumn")}</th>
                     {getEventColumns().map((col) => {
                      const Icon = col.icon
                      return (
                        <th
                          key={col.key}
                          className={"min-w-36 border-b border-hairline p-2 text-center"}
                        >
                          <div className="flex items-center justify-center gap-1.5">
                            <Icon className={cn("h-3.5 w-3.5", col.accentClass)} />
                            <span className={cn("break-words text-xs font-medium [overflow-wrap:anywhere]", col.accentClass)}>{col.label}</span>
                          </div>
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody>
                  {servers.map((server, idx: number) => {
                    const hasCustomRouting = !!routing[server.id]
                    return (
                      <tr
                        key={server.id}
                        className={"border-b border-hairline"}
                      >
                        <td className="p-3 align-top">
                          <div className="flex flex-col gap-0.5">
                            <span className="max-w-[160px] break-words text-sm font-medium [overflow-wrap:anywhere]">
                              {server.name || server.host || server.id}
                            </span>
                            <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                              {server.host}:{server.port}
                            </span>
                          </div>
                          {hasCustomRouting && (
                            <button
                              type="button"
                              onClick={() => resetDvr(server.id)}
                              className="mt-1.5 min-h-11 text-xs text-muted-foreground hover:text-foreground transition-colors underline underline-offset-2"
                            >
                              {t("settings.routing.resetToDefaults")}
                            </button>
                          )}
                        </td>
                         {getEventColumns().map((col) => (
                          <td
                            key={col.key}
                            className={cn("p-3 border-x border-hairline align-top", col.headerClass)}
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
            </>
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
