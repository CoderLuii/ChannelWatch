import { ChevronDown, Clock, Database, Gauge, HardDrive, Info, Bug } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Card, CardContent } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { TabsContent } from "@/components/base/tabs"
import type { AppSettings } from "@/lib/types"
import { cn } from "@/lib/utils"
import { t } from "@/lib/i18n"

import { diskAdvancedBehaviorFields, diskAdvancedThresholdFields } from "./constants"
import { DvrFieldInput, DvrFieldSelect, DvrFieldText, DvrHelpers, DvrTabBar } from "./dvr-field-controls"

type NumericSettingKey =
  | "channel_cache_ttl"
  | "program_cache_ttl"
  | "job_cache_ttl"
  | "vod_cache_ttl"
  | "cw_alert_cooldown"
  | "vod_alert_cooldown"
  | "vod_significant_threshold"
  | "ds_warning_threshold_percent"
  | "ds_warning_threshold_gb"
  | "ds_critical_threshold_percent"
  | "ds_critical_threshold_gb"
  | "ds_startup_grace_seconds"
  | "ds_worsening_delta_gb"
  | "ds_worsening_delta_percent"
  | "ds_alert_cooldown"

interface AdvancedSettingsSectionProps {
  form: UseFormReturn<AppSettings>
  dvrHelpers: DvrHelpers
  expandedAlerts: Record<string, boolean>
  toggleAlert: (key: string) => void
}

export function AdvancedSettingsSection({ form, dvrHelpers, expandedAlerts, toggleAlert }: AdvancedSettingsSectionProps) {
  const { register, setValue, watch } = form

  return (
    <TabsContent value="advanced" className="space-y-4">
      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("cache")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Database className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("advanced.cache.title")}</p>
              <p className="text-sm text-muted-foreground">{t("advanced.cache.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.cache && "rotate-180")} />
        </div>
        {expandedAlerts.cache && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <DvrTabBar cardId="cache" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("cache") === "global" ? (
              <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                 {[
                   { id: "channel_cache_ttl", label: t("advanced.cache.channelList"), desc: t("advanced.cache.channelListDesc"), placeholder: "86400", hint: t("advanced.cache.channelListHint") },
                   { id: "program_cache_ttl", label: t("advanced.cache.programGuide"), desc: t("advanced.cache.programGuideDesc"), placeholder: "86400", hint: t("advanced.cache.programGuideHint") },
                   { id: "job_cache_ttl", label: t("advanced.cache.recordingJobs"), desc: t("advanced.cache.recordingJobsDesc"), placeholder: "3600", hint: t("advanced.cache.recordingJobsHint") },
                   { id: "vod_cache_ttl", label: t("advanced.cache.vodLibrary"), desc: t("advanced.cache.vodLibraryDesc"), placeholder: "86400", hint: t("advanced.cache.vodLibraryHint") },
                 ].map(({ id, label, desc, placeholder, hint }) => (
                  <div key={id} className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                    <Label htmlFor={id}>
                      <span className="text-sm block">{label}</span>
                      <span className="text-[11px] text-muted-foreground">{desc}</span>
                    </Label>
                    <Input id={id} type="number" min="0" max="604800" step="1" placeholder={placeholder} {...register(id as keyof AppSettings, { valueAsNumber: true, min: { value: 0, message: t("advanced.cache.validation.minZero") }, max: { value: 604800, message: t("advanced.cache.validation.max7days") } })} className="h-8 text-sm" />
                    <p className="text-[11px] text-muted-foreground">{hint}</p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                <DvrFieldSelect cardId="cache" fieldKey="channel_cache_ttl" label={t("advanced.cache.channelList")} desc={t("advanced.cache.channelListDesc")} defaultValue="86400" options={[{ value: "3600", label: t("opt.1hour") }, { value: "21600", label: t("opt.6hours") }, { value: "43200", label: t("opt.12hours") }, { value: "86400", label: t("opt.24hours.default") }, { value: "259200", label: t("opt.3days") }, { value: "604800", label: t("opt.7days") }]} helpers={dvrHelpers} />
                <DvrFieldSelect cardId="cache" fieldKey="program_cache_ttl" label={t("advanced.cache.programGuide")} desc={t("advanced.cache.programGuideDesc")} defaultValue="86400" options={[{ value: "3600", label: t("opt.1hour") }, { value: "21600", label: t("opt.6hours") }, { value: "43200", label: t("opt.12hours") }, { value: "86400", label: t("opt.24hours.default") }, { value: "259200", label: t("opt.3days") }, { value: "604800", label: t("opt.7days") }]} helpers={dvrHelpers} />
                <DvrFieldSelect cardId="cache" fieldKey="job_cache_ttl" label={t("advanced.cache.recordingJobs")} desc={t("advanced.cache.recordingJobsDesc")} defaultValue="3600" options={[{ value: "900", label: t("opt.15min") }, { value: "1800", label: t("opt.30min") }, { value: "3600", label: t("opt.1hour.default") }, { value: "21600", label: t("opt.6hours") }, { value: "43200", label: t("opt.12hours") }, { value: "86400", label: t("opt.24hours") }]} helpers={dvrHelpers} />
                <DvrFieldSelect cardId="cache" fieldKey="vod_cache_ttl" label={t("advanced.cache.vodLibrary")} desc={t("advanced.cache.vodLibraryDesc")} defaultValue="86400" options={[{ value: "3600", label: t("opt.1hour") }, { value: "21600", label: t("opt.6hours") }, { value: "43200", label: t("opt.12hours") }, { value: "86400", label: t("opt.24hours.default") }, { value: "259200", label: t("opt.3days") }, { value: "604800", label: t("opt.7days") }]} helpers={dvrHelpers} />
              </div>
            )}
          </CardContent>
        )}
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("rate")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Gauge className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("advanced.rate.title")}</p>
              <p className="text-sm text-muted-foreground">{t("advanced.rate.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.rate && "rotate-180")} />
        </div>
        {expandedAlerts.rate && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <DvrTabBar cardId="rate" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("rate") === "global" ? (
              <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                  <Label>
                    <span className="text-sm block">{t("advanced.rate.maxNotif")}</span>
                     <span className="text-[11px] text-muted-foreground">{t("advanced.rate.maxNotifDesc")}</span>
                  </Label>
                  <Select value={String(watch("global_rate_limit") || 20)} onValueChange={(value) => setValue("global_rate_limit", Number(value), { shouldDirty: true })}>
                    <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
                     <SelectContent>
                       <SelectItem value="5">{t("opt.5notif")}</SelectItem>
                       <SelectItem value="10">{t("opt.10notif")}</SelectItem>
                       <SelectItem value="20">{t("opt.20notif.default")}</SelectItem>
                       <SelectItem value="50">{t("opt.50notif")}</SelectItem>
                       <SelectItem value="100">{t("opt.100notif")}</SelectItem>
                     </SelectContent>
                  </Select>
                </div>
                <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                  <Label>
                    <span className="text-sm block">{t("advanced.rate.window")}</span>
                     <span className="text-[11px] text-muted-foreground">{t("advanced.rate.windowDesc")}</span>
                  </Label>
                  <Select value={String(watch("global_rate_window") || 300)} onValueChange={(value) => setValue("global_rate_window", Number(value), { shouldDirty: true })}>
                    <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
                     <SelectContent>
                       <SelectItem value="60">{t("opt.1min")}</SelectItem>
                       <SelectItem value="300">{t("opt.5min.default")}</SelectItem>
                       <SelectItem value="900">{t("opt.15min")}</SelectItem>
                       <SelectItem value="1800">{t("opt.30min")}</SelectItem>
                       <SelectItem value="3600">{t("opt.1hour")}</SelectItem>
                     </SelectContent>
                  </Select>
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                <DvrFieldSelect cardId="rate" fieldKey="global_rate_limit" label={t("advanced.rate.maxNotif")} desc={t("advanced.rate.maxNotifDesc")} defaultValue="20" options={[{ value: "5", label: t("opt.5notif") }, { value: "10", label: t("opt.10notif") }, { value: "20", label: t("opt.20notif.default") }, { value: "50", label: t("opt.50notif") }, { value: "100", label: t("opt.100notif") }]} helpers={dvrHelpers} />
                <DvrFieldSelect cardId="rate" fieldKey="global_rate_window" label={t("advanced.rate.window")} desc={t("advanced.rate.windowDesc")} defaultValue="300" options={[{ value: "60", label: t("opt.1min") }, { value: "300", label: t("opt.5min.default") }, { value: "900", label: t("opt.15min") }, { value: "1800", label: t("opt.30min") }, { value: "3600", label: t("opt.1hour") }]} helpers={dvrHelpers} />
              </div>
            )}
          </CardContent>
        )}
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("timing")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Clock className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("advanced.timing.title")}</p>
              <p className="text-sm text-muted-foreground">{t("advanced.timing.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.timing && "rotate-180")} />
        </div>
        {expandedAlerts.timing && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <DvrTabBar cardId="timing" helpers={dvrHelpers} />
            <div className="grid grid-cols-1 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
              {dvrHelpers.getDvrTab("timing") === "global" ? (
                <>
                   {([
                      { key: "cw_alert_cooldown", label: t("advanced.timing.cwCooldown"), desc: t("advanced.timing.cwCooldownDesc"), defaultVal: "300", options: [{ value: "60", label: t("opt.1min") }, { value: "300", label: t("opt.5min.default") }, { value: "900", label: t("opt.15min") }, { value: "1800", label: t("opt.30min") }, { value: "3600", label: t("opt.1hour") }] },
                      { key: "vod_alert_cooldown", label: t("advanced.timing.vodCooldown"), desc: t("advanced.timing.vodCooldownDesc"), defaultVal: "300", options: [{ value: "60", label: t("opt.1min") }, { value: "300", label: t("opt.5min.default") }, { value: "900", label: t("opt.15min") }, { value: "1800", label: t("opt.30min") }, { value: "3600", label: t("opt.1hour") }] },
                      { key: "vod_significant_threshold", label: t("advanced.timing.vodMinWatch"), desc: t("advanced.timing.vodMinWatchDesc"), defaultVal: "300", options: [{ value: "30", label: t("opt.30sec") }, { value: "60", label: t("opt.1min") }, { value: "300", label: t("opt.5min.default") }, { value: "600", label: t("opt.10min") }, { value: "900", label: t("opt.15min") }] },
                   ] satisfies Array<{ key: NumericSettingKey; label: string; desc: string; defaultVal: string; options: Array<{ value: string; label: string }> }>).map(({ key, label, desc, defaultVal, options }) => (
                    <div key={key} className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                      <Label>
                        <span className="text-sm block">{label}</span>
                        <span className="text-[11px] text-muted-foreground">{desc}</span>
                      </Label>
                       <Select value={String(watch(key) || defaultVal)} onValueChange={(value) => setValue(key, Number(value), { shouldDirty: true })}>
                        <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {options.map((option) => (
                            <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  ))}
                </>
              ) : (
                <>
                  <DvrFieldSelect cardId="timing" fieldKey="cw_alert_cooldown" label={t("advanced.timing.cwCooldown")} desc={t("advanced.timing.cwCooldownDesc")} defaultValue="300" options={[{ value: "60", label: t("opt.1min") }, { value: "300", label: t("opt.5min.default") }, { value: "900", label: t("opt.15min") }, { value: "1800", label: t("opt.30min") }, { value: "3600", label: t("opt.1hour") }]} helpers={dvrHelpers} />
                   <DvrFieldSelect cardId="timing" fieldKey="vod_alert_cooldown" label={t("advanced.timing.vodCooldown")} desc={t("advanced.timing.vodCooldownDesc")} defaultValue="300" options={[{ value: "60", label: t("opt.1min") }, { value: "300", label: t("opt.5min.default") }, { value: "900", label: t("opt.15min") }, { value: "1800", label: t("opt.30min") }, { value: "3600", label: t("opt.1hour") }]} helpers={dvrHelpers} />
                   <DvrFieldSelect cardId="timing" fieldKey="vod_significant_threshold" label={t("advanced.timing.vodMinWatch")} desc={t("advanced.timing.vodMinWatchDesc")} defaultValue="300" options={[{ value: "30", label: t("opt.30sec") }, { value: "60", label: t("opt.1min") }, { value: "300", label: t("opt.5min.default") }, { value: "600", label: t("opt.10min") }, { value: "900", label: t("opt.15min") }]} helpers={dvrHelpers} />
                </>
              )}
            </div>
          </CardContent>
        )}
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("diskAdvanced")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <HardDrive className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("advanced.diskControls.title")}</p>
              <p className="text-sm text-muted-foreground">{t("advanced.diskControls.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.diskAdvanced && "rotate-180")} />
        </div>
        {expandedAlerts.diskAdvanced && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-4">
            <DvrTabBar cardId="disk" helpers={dvrHelpers} />
            <div className="flex items-start gap-2 rounded-lg border border-blue-400/10 bg-blue-500/5 p-3">
              <Info className="h-4 w-4 text-blue-400 mt-0.5 shrink-0" />
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                {t("advanced.diskControls.hint")}
              </p>
            </div>

            {dvrHelpers.getDvrTab("disk") === "global" ? (
              <div className={cn("space-y-4", !watch("alert_disk_space") && "opacity-50 pointer-events-none")}>
                 <div className="space-y-2">
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("advanced.diskControls.severityThresholds")}</p>
                  <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                    {diskAdvancedThresholdFields.map(({ id, label, desc, min, max, step, suffix, placeholder }) => (
                      <div key={id} className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                        <Label htmlFor={id}>
                          <span className="text-sm block">{label}</span>
                          <span className="text-[11px] text-muted-foreground">{desc}</span>
                        </Label>
                        <div className="flex items-center gap-2">
                          <Input id={id} type="number" min={min} max={max} step={step} placeholder={placeholder} value={watch(id) ?? ""} onChange={(event) => setValue(id, Number(event.target.value) || 0, { shouldDirty: true })} className="h-8 text-sm" />
                          <span className="text-muted-foreground text-sm shrink-0">{suffix}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("advanced.diskControls.timingBehavior")}</p>
                   <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                     {diskAdvancedBehaviorFields.map(({ id, label, desc, min, max, step, suffix, placeholder }) => (
                      <div key={id} className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                        <Label htmlFor={id}>
                          <span className="text-sm block">{label}</span>
                          <span className="text-[11px] text-muted-foreground">{desc}</span>
                        </Label>
                        <div className="flex items-center gap-2">
                          <Input id={id} type="number" min={min} max={max} step={step} placeholder={placeholder} value={watch(id) ?? ""} onChange={(event) => setValue(id, Number(event.target.value) || 0, { shouldDirty: true })} className="h-8 text-sm" />
                          <span className="text-muted-foreground text-sm shrink-0">{suffix}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("advanced.diskControls.testRouting")}</p>
                   <div className="rounded-xl p-3 border border-blue-400/10 bg-background/50">
                     <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                       <Label htmlFor="ds_test_route_override">
                         <span className="text-sm block">{t("advanced.diskControls.testRouteLabel")}</span>
                         <span className="text-[11px] text-muted-foreground">{t("advanced.diskControls.testRouteDesc")}</span>
                       </Label>
                       <Input id="ds_test_route_override" type="text" placeholder={t("advanced.diskControls.testRoutePlaceholder")} value={watch("ds_test_route_override") ?? ""} onChange={(event) => setValue("ds_test_route_override", event.target.value, { shouldDirty: true })} className="h-8 text-sm" />
                       <p className="text-[11px] text-muted-foreground">{t("advanced.diskControls.testRouteFallback")}</p>
                     </div>
                   </div>
                 </div>
              </div>
            ) : (
              <div className={cn("space-y-4", !dvrHelpers.dvrFieldValue("disk", "alert_disk_space").value && "opacity-50 pointer-events-none")}>
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("advanced.diskControls.severityThresholds")}</p>
                   <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                     {diskAdvancedThresholdFields.map(({ id, label, desc, min, max, step, suffix }) => (
                       <DvrFieldInput key={id} cardId="disk" fieldKey={id} label={label} desc={desc} min={min} max={max} step={step} suffix={suffix} helpers={dvrHelpers} />
                     ))}
                   </div>
                 </div>

                 <div className="space-y-2">
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("advanced.diskControls.timingBehavior")}</p>
                  <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                    {diskAdvancedBehaviorFields.map(({ id, label, desc, min, max, step, suffix }) => (
                      <DvrFieldInput key={id} cardId="disk" fieldKey={id} label={label} desc={desc} min={min} max={max} step={step} suffix={suffix} helpers={dvrHelpers} />
                    ))}
                  </div>
                </div>

                 <div className="space-y-2">
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("advanced.diskControls.testRouting")}</p>
                   <div className="rounded-xl p-3 border border-blue-400/10 bg-background/50">
                     <DvrFieldText cardId="disk" fieldKey="ds_test_route_override" label={t("advanced.diskControls.testRouteLabel")} desc={t("advanced.diskControls.testRouteDesc")} placeholder={t("advanced.diskControls.testRoutePlaceholder")} helpers={dvrHelpers} />
                   </div>
                   <p className="text-[11px] text-muted-foreground">{t("advanced.diskControls.testRouteFallbackDvr")}</p>
                 </div>
              </div>
            )}
          </CardContent>
        )}
      </Card>
      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("errorReporting")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Bug className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("advanced.errorReporting.title")}</p>
              <p className="text-sm text-muted-foreground">{t("advanced.errorReporting.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.errorReporting && "rotate-180")} />
        </div>
        {expandedAlerts.errorReporting && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <div className="rounded-xl p-3 border border-blue-400/10 bg-background/50">
              <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                <Label htmlFor="error_reporting_dsn">
                  <span className="text-sm block">{t("advanced.errorReporting.dsnLabel")}</span>
                  <span className="text-[11px] text-muted-foreground">{t("advanced.errorReporting.dsnDesc")}</span>
                </Label>
                <Input
                  id="error_reporting_dsn"
                  type="text"
                  placeholder={t("advanced.errorReporting.dsnPlaceholder")}
                  value={watch("error_reporting_dsn") ?? ""}
                  onChange={(e) => setValue("error_reporting_dsn", e.target.value, { shouldDirty: true })}
                  className="h-8 text-sm"
                />
              </div>
            </div>
          </CardContent>
        )}
      </Card>
    </TabsContent>
  )
}
