import { CheckIcon, ChevronDown, ChevronsUpDown, Server, Tv } from "lucide-react"
import { Controller, type FieldErrors, type UseFormReturn } from "react-hook-form"

import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
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
      <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
        <div className="relative">
          <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-indigo-900/10 z-0" />
          <div className="absolute -top-14 -right-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl" />
          <CardHeader className="relative z-10 border-b border-blue-200/10">
            <div className="flex items-center gap-2">
              <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                <Server className="h-5 w-5 text-blue-400" />
              </div>
              <div>
                <CardTitle>{t("settings.general.title")}</CardTitle>
                <CardDescription>{t("settings.general.description")}</CardDescription>
              </div>
            </div>
          </CardHeader>
        </div>
        <CardContent className="space-y-6 relative z-10 pt-6">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">{t("settings.general.dvrServers")}</Label>
              <div className="flex items-center gap-2">
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
              <div className="p-2 rounded-lg border border-green-400/20 bg-green-500/5 space-y-2">
                <p className="text-xs font-medium text-muted-foreground">
                  {discoveredServers.length > 0
                    ? t(discoveredServers.length === 1 ? "settings.general.foundServers" : "settings.general.foundServersPlural", { count: discoveredServers.length })
                    : t("settings.general.noNewServers")}
                </p>
                {discoveredServers.map((server, index) => (
                  <div key={`${server.host}-${server.port}-${index}`} className="flex items-center justify-between p-2 rounded border border-green-400/10 bg-background/50">
                    <div className="text-xs">
                      <span className="font-medium">{server.name || server.host}</span>
                      <span className="text-muted-foreground ml-2">
                        {server.host}:{server.port}
                      </span>
                      {server.version && <span className="text-muted-foreground ml-2">v{server.version}</span>}
                    </div>
                    <Button type="button" variant="outline" size="sm" className="h-6 text-[10px] px-2" onClick={() => onAddDiscoveredServer(server, index)}>
                      {t("settings.general.addBtn")}
                    </Button>
                  </div>
                ))}
                <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground underline" onClick={onDismissDiscovery}>
                  {t("common.dismiss")}
                </button>
              </div>
            )}

            {(watch("dvr_servers") || []).map((server: DVRServer, index: number) => (
              <div key={server.id || index} className="p-3 rounded-lg border border-blue-400/20 bg-blue-500/5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-700 dark:text-slate-200">{t("settings.general.serverN", { n: index + 1 })}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0 text-red-400 hover:text-red-600"
                    onClick={() => onRemoveServer(index)}
                  >
                    X
                  </Button>
                </div>
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="space-y-1">
                    <Label className="text-xs">{t("settings.general.nameLbl")}</Label>
                    <Input
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
                    <Label className="text-xs">{t("settings.general.hostLbl")}</Label>
                    <Input
                      placeholder="e.g., 192.168.1.100"
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
                    <Label className="text-xs">{t("settings.general.portLbl")}</Label>
                    <Input
                      type="number"
                      placeholder="8089"
                      value={server.port || 8089}
                      onChange={(event) => {
                        const current = [...(getValues("dvr_servers") || [])]
                        current[index] = { ...current[index], port: Number(event.target.value) || 8089 }
                        setValue("dvr_servers", current, { shouldDirty: true })
                      }}
                      className="h-8 text-sm"
                    />
                  </div>
                </div>
              </div>
            ))}

            {(!watch("dvr_servers") || watch("dvr_servers").length === 0) && (
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
                          variant="outline"
                          role="combobox"
                          aria-label={t("settings.general.timezoneLbl")}
                          className={cn("w-full justify-between", !field.value && "text-muted-foreground")}
                        >
                          {field.value ? field.value : t("settings.general.timezoneSelect")}
                          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
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
              <p className="text-xs text-muted-foreground">{t("settings.general.logLevelHint")}</p>
            </div>
          </div>

          <div className="space-y-2">
              <Label htmlFor="log_retention_days">{t("settings.general.logRetentionLbl")}</Label>
            <Input
              id="log_retention_days"
              type="number"
              min="1"
              {...register("log_retention_days", { valueAsNumber: true, min: { value: 1, message: t("settings.general.logRetentionMin") } })}
              className={errors.log_retention_days ? "max-w-xs border-red-500" : "max-w-xs"}
            />
            <p className="text-xs text-muted-foreground">{t("settings.general.logRetentionHint")}</p>
          </div>
        </CardContent>
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("display")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Tv className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("settings.general.bgImages")}</p>
              <p className="text-sm text-muted-foreground">{t("settings.general.bgImagesDesc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.display && "rotate-180")} />
        </div>
        {expandedAlerts.display && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-4">
            <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
              <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                <Label>
                   <span className="text-sm block">{t("settings.general.bgActiveStreams")}</span>
                   <span className="text-[11px] text-muted-foreground">{t("settings.general.bgActiveStreamsHint")}</span>
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
              <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                <Label>
                   <span className="text-sm block">{t("settings.general.bgUpcoming")}</span>
                   <span className="text-[11px] text-muted-foreground">{t("settings.general.bgUpcomingHint")}</span>
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
