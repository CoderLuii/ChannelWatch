import { CheckIcon, ChevronDown, Server, Trash2, Tv } from "lucide-react"
import { Controller, type FieldErrors, type UseFormReturn } from "react-hook-form"

import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem } from "@/components/base/command"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/base/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { Separator } from "@/components/base/separator"
import { TabsContent } from "@/components/base/tabs"
import { cn } from "@/lib/utils"
import { t } from "@/lib/i18n"
import type { DiscoveredServer } from "@/lib/api"
import type { AppSettings, DVRServer } from "@/lib/types"

import { timezones } from "./constants"

interface GeneralSettingsSectionProps {
  form: UseFormReturn<AppSettings>
  errors: FieldErrors<AppSettings>
  expandedAlerts: Record<string, boolean>
  toggleAlert: (key: string) => void
  isDiscovering: boolean
  showDiscoverResults: boolean
  discoveredServers: DiscoveredServer[]
  onDiscover: () => void
  onAddServer: () => void
  onAddDiscoveredServer: (server: DiscoveredServer, index: number) => void
  onDismissDiscovery: () => void
  onRemoveServer: (index: number) => void
}

export function GeneralSettingsSection({
  form,
  errors,
  expandedAlerts,
  toggleAlert,
  isDiscovering,
  showDiscoverResults,
  discoveredServers,
  onDiscover,
  onAddServer,
  onAddDiscoveredServer,
  onDismissDiscovery,
  onRemoveServer,
}: GeneralSettingsSectionProps) {
  const { control, getValues, setValue, register, watch } = form

  return (
    <TabsContent value="general" className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
            <div className="flex items-center gap-2">
              <Server className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div>
                <CardTitle>{t("settings.general.title")}</CardTitle>
              </div>
            </div>
          </CardHeader>
        <CardContent className="space-y-6 pt-4">
          <div className="space-y-4">
            <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
              <h3 className="text-sm font-medium">{t("settings.general.dvrServers")}</h3>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={isDiscovering}
                  onClick={onDiscover}
                >
                  {isDiscovering ? t("settings.general.scanning") : t("settings.general.discover")}
                </Button>
                <Button type="button" variant="outline" size="sm" className="h-7 text-xs" onClick={onAddServer}>
                  {t("settings.general.addServer")}
                </Button>
              </div>
            </div>

            {showDiscoverResults && (
              <div className="p-2 rounded-control bg-fill space-y-2">
                <p className="text-xs font-medium text-muted-foreground">
                  {discoveredServers.length > 0
                    ? t(discoveredServers.length === 1 ? "settings.general.foundServers" : "settings.general.foundServersPlural", { count: discoveredServers.length })
                    : t("settings.general.noNewServers")}
                </p>
                {discoveredServers.map((server, index) => (
                  <div key={`${server.host}-${server.port}-${index}`} className="flex min-w-0 flex-col items-start gap-2 rounded-thumb p-2 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0 break-words text-xs">
                      <span className="font-medium">{server.name || server.host}</span>
                      <span className="text-muted-foreground ml-2">
                        {server.host}:{server.port}
                      </span>
                      {server.version && <span className="text-muted-foreground ml-2">v{server.version}</span>}
                    </div>
                    <Button type="button" variant="outline" size="sm" className="min-h-11 shrink-0 px-3 text-xs" onClick={() => onAddDiscoveredServer(server, index)}>
                      {t("settings.general.addBtn")}
                    </Button>
                  </div>
                ))}
                <button type="button" className="min-h-11 px-2 text-xs text-muted-foreground hover:text-foreground underline" onClick={onDismissDiscovery}>
                  {t("common.dismiss")}
                </button>
              </div>
            )}

            {(watch("dvr_servers") || []).map((server: DVRServer, index: number, servers: DVRServer[]) => server.deleted_at ? null : (
              <div key={server.id || index} className="p-3 rounded-control border border-hairline space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted-foreground">{t("settings.general.serverN", { n: servers.slice(0, index + 1).filter((entry) => !entry.deleted_at).length })}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="-mr-[13px] h-11 w-11 shrink-0 p-0 text-danger-fg hover:text-danger-fg"
                    aria-label={`Remove ${server.name || server.host || `server ${index + 1}`}`}
                    onClick={() => onRemoveServer(index)}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </div>
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="space-y-1">
                    <Label htmlFor={`dvr-${index}-name`} className="text-xs">{t("settings.general.nameLbl")}</Label>
                    <Input
                       id={`dvr-${index}-name`}
                       placeholder={t("settings.general.namePlaceholder")}
                      value={server.name || ""}
                      onChange={(event) => {
                        const current = [...(getValues("dvr_servers") || [])]
                        current[index] = { ...current[index], name: event.target.value }
                        setValue("dvr_servers", current, { shouldDirty: true })
                      }}
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`dvr-${index}-host`} className="text-xs">{t("settings.general.hostLbl")}</Label>
                    <Input
                      id={`dvr-${index}-host`}
                      placeholder="e.g., 192.168.1.100"
                      inputMode="url"
                      autoCapitalize="none"
                      autoCorrect="off"
                      spellCheck={false}
                      value={server.host || ""}
                      onChange={(event) => {
                        const current = [...(getValues("dvr_servers") || [])]
                        current[index] = { ...current[index], host: event.target.value }
                        setValue("dvr_servers", current, { shouldDirty: true })
                      }}
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`dvr-${index}-port`} className="text-xs">{t("settings.general.portLbl")}</Label>
                    <Input
                      id={`dvr-${index}-port`}
                      type="number"
                      placeholder="8089"
                      // Empty while typing. Saving an empty port uses 8089.
                      value={server.port || ""}
                      onChange={(event) => {
                        const current = [...(getValues("dvr_servers") || [])]
                        current[index] = { ...current[index], port: Number(event.target.value) || 0 }
                        setValue("dvr_servers", current, { shouldDirty: true })
                      }}
                      className="h-8 text-sm"
                    />
                  </div>
                </div>
              </div>
            ))}

            {!(watch("dvr_servers") || []).some((server: DVRServer) => !server.deleted_at) && (
              <p className="text-xs text-muted-foreground text-center py-4">{t("settings.general.noDvrEmpty")}</p>
            )}
          </div>

          <Separator />

          <div className="grid gap-6 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="tz">{t("settings.general.timezoneLbl")}</Label>
              <Controller
                control={control}
                name="tz"
                render={({ field }) => (
                    <Popover>
                      <PopoverTrigger asChild>
                        <Button
                          id="tz"
                          variant="outline"
                          role="combobox"
                          aria-label={t("settings.general.timezoneLbl")}
                          className={cn("w-full justify-between bg-well px-3.5 font-normal [&_svg]:size-4", !field.value && "text-muted-foreground")}
                        >
                          {field.value ? field.value : t("settings.general.timezoneSelect")}
                          <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-[300px] p-0">
                      <Command>
                        <CommandInput placeholder={t("settings.general.timezoneSearch")} className="h-9" />
                        <CommandEmpty>{t("settings.general.timezoneEmpty")}</CommandEmpty>
                        <CommandGroup className="max-h-[300px] overflow-y-auto">
                          {timezones.map((timezone) => (
                            <CommandItem
                              key={timezone}
                              value={timezone}
                              onSelect={() => {
                                field.onChange(timezone)
                                setValue("tz", timezone, { shouldDirty: true })
                              }}
                            >
                              {timezone}
                              <CheckIcon className={cn("ml-auto h-4 w-4", field.value === timezone ? "opacity-100" : "opacity-0")} />
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </Command>
                    </PopoverContent>
                  </Popover>
                )}
              />
              <p className="text-xs text-muted-foreground">{t("settings.general.timezoneHint")}</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="log_level">{t("settings.general.logLevelLbl")}</Label>
              <Select onValueChange={(value) => setValue("log_level", Number.parseInt(value), { shouldDirty: true })} value={String(watch("log_level") ?? 1)}>
                <SelectTrigger id="log_level" aria-label={t("settings.general.logLevelLbl")}>
                  <SelectValue placeholder={t("settings.general.logLevelSelect")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1">{t("settings.general.logLevelStandard")}</SelectItem>
                  <SelectItem value="2">{t("settings.general.logLevelVerbose")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="log_retention_days">{t("settings.general.logRetentionLbl")}</Label>
            <Input
              id="log_retention_days"
              type="number"
              min="1"
              {...register("log_retention_days", { valueAsNumber: true, min: { value: 1, message: t("settings.general.logRetentionMin") } })}
              className={errors.log_retention_days ? "border-danger" : undefined}
            />
            <p className="text-xs text-muted-foreground">{t("settings.general.logRetentionHint")}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <button type="button" className="press-row flex min-h-[60px] w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-fill transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" aria-expanded={!!expandedAlerts.display} aria-controls="settings-display-content" onClick={() => toggleAlert("display")}>
          <span className="flex gap-2 items-center">
            <Tv className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span>
              <span className="block text-base font-semibold">{t("settings.general.bgImages")}</span>
              <span className="block text-sm text-muted-foreground">{t("settings.general.bgImagesDesc")}</span>
            </span>
          </span>
          <ChevronDown className={cn("h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-[var(--motion-base)] ease-out", expandedAlerts.display && "rotate-180")} />
        </button>
        {expandedAlerts.display && (
          <CardContent id="settings-display-content" className="disclosure-enter border-t border-hairline pt-4 space-y-4">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div className="p-3 rounded-control bg-fill space-y-2">
                <Label>
                   <span className="block text-sm">{t("settings.general.bgActiveStreams")}</span>
                </Label>
                <Select value={watch("stream_card_image") || "program"} onValueChange={(value) => setValue("stream_card_image", value, { shouldDirty: true })}>
                  <SelectTrigger className="h-8 text-sm" aria-label={t("settings.general.bgActiveStreams")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="program">{t("settings.general.imgProgram")}</SelectItem>
                     <SelectItem value="channel">{t("settings.general.imgChannel")}</SelectItem>
                     <SelectItem value="none">{t("settings.general.imgNone")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="p-3 rounded-control bg-fill space-y-2">
                <Label>
                   <span className="block text-sm">{t("settings.general.bgUpcoming")}</span>
                </Label>
                <Select value={watch("recording_card_image") || "program"} onValueChange={(value) => setValue("recording_card_image", value, { shouldDirty: true })}>
                  <SelectTrigger className="h-8 text-sm" aria-label={t("settings.general.bgUpcoming")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="program">{t("settings.general.imgProgram")}</SelectItem>
                     <SelectItem value="none">{t("settings.general.imgNone")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        )}
      </Card>
    </TabsContent>
  )
}
