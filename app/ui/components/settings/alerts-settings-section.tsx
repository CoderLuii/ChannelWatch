import { Clock, HardDrive, HeartPulse, Info, Share2, Tv, Video } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Card, CardContent } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { Switch } from "@/components/base/switch"
import { TabsContent } from "@/components/base/tabs"
import { cn } from "@/lib/utils"
import { t } from "@/lib/i18n"
import type { AppSettings } from "@/lib/types"

import { AlertDisclosureHeader, AlertPolicyCard } from "./alert-policy-card"
import { DvrFieldInput, DvrFieldSelect, DvrFieldToggle, DvrHelpers, DvrTabBar } from "./dvr-field-controls"

type BooleanSettingKey =
  | "cw_channel_name"
  | "cw_channel_number"
  | "cw_program_name"
  | "cw_device_name"
  | "cw_device_ip"
  | "cw_stream_source"
  | "vod_title"
  | "vod_episode_title"
  | "vod_summary"
  | "vod_duration"
  | "vod_progress"
  | "vod_image"
  | "vod_rating"
  | "vod_genres"
  | "vod_cast"
  | "vod_device_name"
  | "vod_device_ip"
  | "rd_alert_scheduled"
  | "rd_alert_started"
  | "rd_alert_completed"
  | "rd_alert_cancelled"
  | "rd_alert_failed"
  | "rd_alert_skipped"
  | "rd_alert_missed"
  | "rd_alert_interrupted"
  | "rd_program_name"
  | "rd_program_desc"
  | "rd_duration"
  | "rd_channel_name"
  | "rd_channel_number"
  | "rd_type"

interface AlertsSettingsSectionProps {
  form: UseFormReturn<AppSettings>
  dvrHelpers: DvrHelpers
  expandedAlerts: Record<string, boolean>
  toggleAlert: (key: string) => void
}

export function AlertsSettingsSection({ form, dvrHelpers, expandedAlerts, toggleAlert }: AlertsSettingsSectionProps) {
  const { register, setValue, watch, formState: { errors } } = form
  const recordingOutcomeKeys: BooleanSettingKey[] = [
    "rd_alert_scheduled",
    "rd_alert_started",
    "rd_alert_completed",
    "rd_alert_cancelled",
    "rd_alert_failed",
    "rd_alert_skipped",
    "rd_alert_missed",
    "rd_alert_interrupted",
  ]
  const enabledRecordingOutcomes = recordingOutcomeKeys.filter((key) => watch(key)).length
  const healthAlertCount = [watch("dvr_alert_unreachable"), watch("dvr_alert_recovered")].filter(Boolean).length
  const scopedSummary = (cardId: string, globalSummary: string) => {
    const activeScope = dvrHelpers.getDvrTab(cardId)
    if (activeScope === "global") return globalSummary

    const activeDvr = dvrHelpers.servers.find((server) => server.id === activeScope)
    const hasOverride = (dvrHelpers.cardFieldKeys[cardId] ?? []).some(
      (key) => activeDvr?.overrides?.[key] !== undefined,
    )
    return hasOverride ? t("alerts.summary.overridden") : t("alerts.summary.inherited")
  }

  return (
    <TabsContent value="alerts" className="flex flex-col gap-4">
      <AlertPolicyCard form={form} dvrHelpers={dvrHelpers} />

      <Card className="order-7 border-blue-400/20 overflow-hidden">
        <AlertDisclosureHeader
          cardId="sc"
          icon={Share2}
          title={t("alerts.streamCounter.title")}
          description={t("alerts.streamCounter.desc")}
          summary={scopedSummary("sc", watch("stream_count") ? t("alerts.summary.on") : t("alerts.summary.off"))}
          expanded={Boolean(expandedAlerts.sc)}
          onToggle={() => toggleAlert("sc")}
        />
        {expandedAlerts.sc && (
          <CardContent id="alert-section-sc" className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-3">
            <DvrTabBar cardId="sc" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("sc") === "global" ? (
              <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                <Label htmlFor="stream_count_global" className="cursor-pointer">
                  <span className="text-sm block">{t("alerts.field.streamCounter")}</span>
                  <span className="text-xs text-muted-foreground">{t("alerts.field.streamCounterDesc")}</span>
                </Label>
                <Switch id="stream_count_global" checked={watch("stream_count")} onCheckedChange={(checked) => setValue("stream_count", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
              </div>
            ) : (
              <DvrFieldToggle cardId="sc" fieldKey="stream_count" label={t("alerts.field.streamCounter")} desc={t("alerts.field.streamCounterDvrDesc")} helpers={dvrHelpers} />
            )}
          </CardContent>
        )}
      </Card>

      <Card className="order-5 border-blue-400/20 overflow-hidden">
        <AlertDisclosureHeader
          cardId="cw"
          icon={Tv}
          title={t("alerts.channelWatching.title")}
          description={t("alerts.channelWatching.desc")}
          summary={scopedSummary("cw", watch("alert_channel_watching") ? t("alerts.summary.on") : t("alerts.summary.off"))}
          expanded={Boolean(expandedAlerts.cw)}
          onToggle={() => toggleAlert("cw")}
        />
        {expandedAlerts.cw && (
          <CardContent id="alert-section-cw" className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-5">
            <DvrTabBar cardId="cw" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("cw") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_channel_watching_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.channelWatching.title")}</span>
                     <span className="text-xs text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_channel_watching_global" checked={watch("alert_channel_watching")} onCheckedChange={(checked) => setValue("alert_channel_watching", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                <fieldset disabled={!watch("alert_channel_watching")} className={cn("space-y-5", !watch("alert_channel_watching") && "opacity-60")}>
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.notifImage")}</p>
                    <Select onValueChange={(value) => setValue("cw_image_source", value, { shouldDirty: true })} value={watch("cw_image_source")}>
                      <SelectTrigger className="border-blue-400/20 bg-background"><SelectValue placeholder={t("alerts.field.selectImageSource")} /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="PROGRAM">{t("alerts.field.programImage")}</SelectItem>
                        <SelectItem value="CHANNEL">{t("alerts.field.channelLogo")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                   <div className="space-y-2">
                     <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                     <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                       {[
                         { id: "cw_channel_name", label: t("alerts.cw.channelName"), desc: t("alerts.cw.channelNameDesc") },
                         { id: "cw_channel_number", label: t("alerts.cw.channelNumber"), desc: t("alerts.cw.channelNumberDesc") },
                         { id: "cw_program_name", label: t("alerts.cw.programName"), desc: t("alerts.cw.programNameDesc") },
                         { id: "cw_device_name", label: t("alerts.cw.deviceName"), desc: t("alerts.cw.deviceNameDesc") },
                         { id: "cw_device_ip", label: t("alerts.cw.deviceIp"), desc: t("alerts.cw.deviceIpDesc") },
                         { id: "cw_stream_source", label: t("alerts.cw.streamSource"), desc: t("alerts.cw.streamSourceDesc") },
                       ].map(({ id, label, desc }) => (
                         <div key={id} className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10 transition-colors hover:bg-blue-500/10">
                           <Label htmlFor={id} className="cursor-pointer">
                             <span className="text-sm block">{label}</span>
                             <span className="text-xs text-muted-foreground">{desc}</span>
                           </Label>
                          <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                         </div>
                       ))}
                     </div>
                   </div>
                 </fieldset>
               </>
            ) : (
               <>
                 <DvrFieldToggle cardId="cw" fieldKey="alert_channel_watching" label={t("alerts.channelWatching.title")} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <fieldset disabled={!dvrHelpers.dvrFieldValue("cw", "alert_channel_watching").value} className={cn("space-y-5", !dvrHelpers.dvrFieldValue("cw", "alert_channel_watching").value && "opacity-60")}>
                   <DvrFieldSelect cardId="cw" fieldKey="cw_image_source" label={t("alerts.notifImage")} desc={t("alerts.field.imageInNotif")} options={[{ value: "PROGRAM", label: t("alerts.field.programImage") }, { value: "CHANNEL", label: t("alerts.field.channelLogo") }]} defaultValue="PROGRAM" helpers={dvrHelpers} />
                   <div className="space-y-2">
                     <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                     <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                       {[
                         { id: "cw_channel_name", label: t("alerts.cw.channelName"), desc: t("alerts.cw.channelNameDesc") },
                         { id: "cw_channel_number", label: t("alerts.cw.channelNumber"), desc: t("alerts.cw.channelNumberDesc") },
                         { id: "cw_program_name", label: t("alerts.cw.programName"), desc: t("alerts.cw.programNameDesc") },
                         { id: "cw_device_name", label: t("alerts.cw.deviceName"), desc: t("alerts.cw.deviceNameDesc") },
                         { id: "cw_device_ip", label: t("alerts.cw.deviceIp"), desc: t("alerts.cw.deviceIpDesc") },
                         { id: "cw_stream_source", label: t("alerts.cw.streamSource"), desc: t("alerts.cw.streamSourceDesc") },
                       ].map(({ id, label, desc }) => (
                         <DvrFieldToggle key={id} cardId="cw" fieldKey={id} label={label} desc={desc} helpers={dvrHelpers} />
                       ))}
                     </div>
                   </div>
                 </fieldset>
               </>
             )}
          </CardContent>
        )}
      </Card>

      <Card className="order-6 border-blue-400/20 overflow-hidden">
        <AlertDisclosureHeader
          cardId="vod"
          icon={Video}
          title={t("alerts.vodWatching.title")}
          description={t("alerts.vodWatching.desc")}
          summary={scopedSummary("vod", watch("alert_vod_watching") ? t("alerts.summary.on") : t("alerts.summary.off"))}
          expanded={Boolean(expandedAlerts.vod)}
          onToggle={() => toggleAlert("vod")}
        />
        {expandedAlerts.vod && (
          <CardContent id="alert-section-vod" className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <DvrTabBar cardId="vod" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("vod") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_vod_watching_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.vodWatching.title")}</span>
                     <span className="text-xs text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_vod_watching_global" checked={watch("alert_vod_watching")} onCheckedChange={(checked) => setValue("alert_vod_watching", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                 <fieldset disabled={!watch("alert_vod_watching")} className={cn("space-y-2", !watch("alert_vod_watching") && "opacity-60")}>
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                   <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                     {[
                       { id: "vod_title", label: t("alerts.vod.title"), desc: t("alerts.vod.titleDesc") },
                       { id: "vod_episode_title", label: t("alerts.vod.episodeTitle"), desc: t("alerts.vod.episodeTitleDesc") },
                       { id: "vod_summary", label: t("alerts.vod.summary"), desc: t("alerts.vod.summaryDesc") },
                       { id: "vod_duration", label: t("alerts.vod.duration"), desc: t("alerts.vod.durationDesc") },
                       { id: "vod_progress", label: t("alerts.vod.progress"), desc: t("alerts.vod.progressDesc") },
                       { id: "vod_image", label: t("alerts.vod.image"), desc: t("alerts.vod.imageDesc") },
                       { id: "vod_rating", label: t("alerts.vod.rating"), desc: t("alerts.vod.ratingDesc") },
                       { id: "vod_genres", label: t("alerts.vod.genres"), desc: t("alerts.vod.genresDesc") },
                       { id: "vod_cast", label: t("alerts.vod.cast"), desc: t("alerts.vod.castDesc") },
                       { id: "vod_device_name", label: t("alerts.vod.deviceName"), desc: t("alerts.vod.deviceNameDesc") },
                       { id: "vod_device_ip", label: t("alerts.vod.deviceIp"), desc: t("alerts.vod.deviceIpDesc") },
                     ].map(({ id, label, desc }) => (
                       <div key={id} className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10 transition-colors hover:bg-blue-500/10">
                         <Label htmlFor={id} className="cursor-pointer">
                           <span className="text-sm block">{label}</span>
                           <span className="text-xs text-muted-foreground">{desc}</span>
                         </Label>
                        <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                       </div>
                     ))}
                   </div>
                 </fieldset>
               </>
             ) : (
               <>
                 <DvrFieldToggle cardId="vod" fieldKey="alert_vod_watching" label={t("alerts.vodWatching.title")} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <fieldset disabled={!dvrHelpers.dvrFieldValue("vod", "alert_vod_watching").value} className={cn("space-y-2", !dvrHelpers.dvrFieldValue("vod", "alert_vod_watching").value && "opacity-60")}>
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                   <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                     {[
                       { id: "vod_title", label: t("alerts.vod.title"), desc: t("alerts.vod.titleDesc") },
                       { id: "vod_episode_title", label: t("alerts.vod.episodeTitle"), desc: t("alerts.vod.episodeTitleDesc") },
                       { id: "vod_summary", label: t("alerts.vod.summary"), desc: t("alerts.vod.summaryDesc") },
                       { id: "vod_duration", label: t("alerts.vod.duration"), desc: t("alerts.vod.durationDesc") },
                       { id: "vod_progress", label: t("alerts.vod.progress"), desc: t("alerts.vod.progressDesc") },
                       { id: "vod_image", label: t("alerts.vod.image"), desc: t("alerts.vod.imageDesc") },
                       { id: "vod_rating", label: t("alerts.vod.rating"), desc: t("alerts.vod.ratingDesc") },
                       { id: "vod_genres", label: t("alerts.vod.genres"), desc: t("alerts.vod.genresDesc") },
                       { id: "vod_cast", label: t("alerts.vod.cast"), desc: t("alerts.vod.castDesc") },
                       { id: "vod_device_name", label: t("alerts.vod.deviceName"), desc: t("alerts.vod.deviceNameDesc") },
                       { id: "vod_device_ip", label: t("alerts.vod.deviceIp"), desc: t("alerts.vod.deviceIpDesc") },
                     ].map(({ id, label, desc }) => (
                       <DvrFieldToggle key={id} cardId="vod" fieldKey={id} label={label} desc={desc} helpers={dvrHelpers} />
                     ))}
                   </div>
                 </fieldset>
               </>
             )}
          </CardContent>
        )}
      </Card>

      <Card className="order-2 border-blue-400/20 overflow-hidden">
        <AlertDisclosureHeader
          cardId="health"
          icon={HeartPulse}
          title={t("alerts.health.title")}
          description={t("alerts.health.description")}
          summary={scopedSummary("health", watch("alert_dvr_health") ? t("alerts.summary.count", { enabled: healthAlertCount, total: 2 }) : t("alerts.summary.off"))}
          expanded={Boolean(expandedAlerts.health)}
          onToggle={() => toggleAlert("health")}
        />
        {expandedAlerts.health && (
          <CardContent id="alert-section-health" className="space-y-4 border-t border-blue-400/10 bg-muted/20 pt-5">
            <DvrTabBar cardId="health" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("health") === "global" ? (
              <>
                <div className="flex items-center justify-between gap-4 rounded-lg border border-blue-400/10 bg-muted/40 p-3">
                  <Label htmlFor="alert_dvr_health_global" className="cursor-pointer">
                    <span className="block text-sm">{t("alerts.health.title")}</span>
                    <span className="text-xs text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_dvr_health_global" checked={watch("alert_dvr_health")} onCheckedChange={(checked) => setValue("alert_dvr_health", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                <fieldset disabled={!watch("alert_dvr_health")} className={cn("space-y-3", !watch("alert_dvr_health") && "opacity-60")}>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {[
                      { id: "dvr_alert_unreachable", label: t("alerts.health.unreachable"), desc: t("alerts.health.unreachableDescription") },
                      { id: "dvr_alert_recovered", label: t("alerts.health.recovered"), desc: t("alerts.health.recoveredDescription") },
                    ].map(({ id, label, desc }) => (
                      <div key={id} className="flex items-center justify-between gap-3 rounded-lg border border-blue-400/10 bg-muted/40 p-3">
                        <Label htmlFor={id} className="cursor-pointer">
                          <span className="block text-sm">{label}</span>
                          <span className="text-xs text-muted-foreground">{desc}</span>
                        </Label>
                        <Switch id={id} checked={watch(id as "dvr_alert_unreachable" | "dvr_alert_recovered")} onCheckedChange={(checked) => setValue(id as "dvr_alert_unreachable" | "dvr_alert_recovered", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                      </div>
                    ))}
                  </div>
                  <div className="max-w-sm space-y-2">
                    <Label htmlFor="dvr_health_alert_delay_seconds">
                      <span className="block text-sm">{t("alerts.health.delay")}</span>
                      <span className="text-xs text-muted-foreground">{t("alerts.health.delayDescription")}</span>
                    </Label>
                    <div className="flex items-center gap-2">
                      <Input id="dvr_health_alert_delay_seconds" type="number" min="30" max="3600" step="30" {...register("dvr_health_alert_delay_seconds", { valueAsNumber: true, min: 30, max: 3600 })} />
                      <span className="text-sm text-muted-foreground">seconds</span>
                    </div>
                  </div>
                </fieldset>
              </>
            ) : (
              <>
                <DvrFieldToggle cardId="health" fieldKey="alert_dvr_health" label={t("alerts.health.title")} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                <fieldset disabled={!dvrHelpers.dvrFieldValue("health", "alert_dvr_health").value} className={cn("space-y-3", !dvrHelpers.dvrFieldValue("health", "alert_dvr_health").value && "opacity-60")}>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <DvrFieldToggle cardId="health" fieldKey="dvr_alert_unreachable" label={t("alerts.health.unreachable")} desc={t("alerts.health.unreachableDescription")} helpers={dvrHelpers} />
                    <DvrFieldToggle cardId="health" fieldKey="dvr_alert_recovered" label={t("alerts.health.recovered")} desc={t("alerts.health.recoveredDescription")} helpers={dvrHelpers} />
                  </div>
                  <DvrFieldInput cardId="health" fieldKey="dvr_health_alert_delay_seconds" label={t("alerts.health.delay")} desc={t("alerts.health.delayDescription")} min={30} max={3600} step={30} suffix="seconds" helpers={dvrHelpers} />
                </fieldset>
              </>
            )}
            <div className="flex items-start gap-2 rounded-lg border border-blue-400/10 bg-blue-500/5 p-3">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" aria-hidden="true" />
              <p className="text-xs text-muted-foreground">{t("alerts.health.limit")}</p>
            </div>
          </CardContent>
        )}
      </Card>

      <Card className="order-3 border-blue-400/20 overflow-hidden">
        <AlertDisclosureHeader
          cardId="rec"
          icon={Clock}
          title={t("alerts.recordingEvents.title")}
          description={t("alerts.recordingEvents.desc")}
          summary={scopedSummary("rec", watch("alert_recording_events") ? t("alerts.summary.count", { enabled: enabledRecordingOutcomes, total: recordingOutcomeKeys.length }) : t("alerts.summary.off"))}
          expanded={Boolean(expandedAlerts.rec)}
          onToggle={() => toggleAlert("rec")}
        />
        {expandedAlerts.rec && (
          <CardContent id="alert-section-rec" className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-5">
            <DvrTabBar cardId="rec" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("rec") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_recording_events_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.recordingEvents.title")}</span>
                     <span className="text-xs text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_recording_events_global" checked={watch("alert_recording_events")} onCheckedChange={(checked) => setValue("alert_recording_events", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                <fieldset disabled={!watch("alert_recording_events")} className={cn("space-y-5", !watch("alert_recording_events") && "opacity-60")}>
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.notifyWhenRecording")}</p>
                    <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                      {[
                        { id: "rd_alert_scheduled", label: t("alerts.rec.scheduled"), desc: t("alerts.rec.scheduledDesc") },
                        { id: "rd_alert_started", label: t("alerts.rec.started"), desc: t("alerts.rec.startedDesc") },
                        { id: "rd_alert_completed", label: t("alerts.rec.completed"), desc: t("alerts.rec.completedDesc") },
                        { id: "rd_alert_cancelled", label: t("alerts.rec.cancelled"), desc: t("alerts.rec.cancelledDesc") },
                        { id: "rd_alert_failed", label: t("alerts.rec.failed"), desc: t("alerts.rec.failedDesc") },
                        { id: "rd_alert_skipped", label: t("alerts.rec.skipped"), desc: t("alerts.rec.skippedDesc") },
                        { id: "rd_alert_missed", label: t("alerts.rec.missed"), desc: t("alerts.rec.missedDesc") },
                        { id: "rd_alert_interrupted", label: t("alerts.rec.interrupted"), desc: t("alerts.rec.interruptedDesc") },
                      ].map(({ id, label, desc }) => (
                        <div key={id} className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10 transition-colors hover:bg-blue-500/10">
                          <Label htmlFor={id} className="cursor-pointer">
                            <span className="text-sm block">{label}</span>
                            <span className="text-xs text-muted-foreground">{desc}</span>
                          </Label>
                          <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                    <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                       {[
                         { id: "rd_program_name", label: t("alerts.rec.programName"), desc: t("alerts.rec.programNameDesc") },
                         { id: "rd_program_desc", label: t("alerts.rec.description"), desc: t("alerts.rec.descriptionDesc") },
                         { id: "rd_duration", label: t("alerts.rec.duration"), desc: t("alerts.rec.durationDesc") },
                         { id: "rd_channel_name", label: t("alerts.rec.channelName"), desc: t("alerts.rec.channelNameDesc") },
                         { id: "rd_channel_number", label: t("alerts.rec.channelNumber"), desc: t("alerts.rec.channelNumberDesc") },
                         { id: "rd_type", label: t("alerts.rec.type"), desc: t("alerts.rec.typeDesc") },
                       ].map(({ id, label, desc }) => (
                         <div key={id} className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10 transition-colors hover:bg-blue-500/10">
                           <Label htmlFor={id} className="cursor-pointer">
                             <span className="text-sm block">{label}</span>
                             <span className="text-xs text-muted-foreground">{desc}</span>
                           </Label>
                            <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                         </div>
                       ))}
                    </div>
                  </div>
                </fieldset>
              </>
            ) : (
              <>
                 <DvrFieldToggle cardId="rec" fieldKey="alert_recording_events" label={t("alerts.recordingEvents.title")} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <fieldset disabled={!dvrHelpers.dvrFieldValue("rec", "alert_recording_events").value} className={cn("space-y-5", !dvrHelpers.dvrFieldValue("rec", "alert_recording_events").value && "opacity-60")}>
                   <div className="space-y-2">
                     <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.notifyWhenRecording")}</p>
                     <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                       {[
                         { id: "rd_alert_scheduled", label: t("alerts.rec.scheduled"), desc: t("alerts.rec.scheduledDesc") },
                         { id: "rd_alert_started", label: t("alerts.rec.started"), desc: t("alerts.rec.startedDesc") },
                         { id: "rd_alert_completed", label: t("alerts.rec.completed"), desc: t("alerts.rec.completedDesc") },
                         { id: "rd_alert_cancelled", label: t("alerts.rec.cancelled"), desc: t("alerts.rec.cancelledDesc") },
                         { id: "rd_alert_failed", label: t("alerts.rec.failed"), desc: t("alerts.rec.failedDesc") },
                         { id: "rd_alert_skipped", label: t("alerts.rec.skipped"), desc: t("alerts.rec.skippedDesc") },
                         { id: "rd_alert_missed", label: t("alerts.rec.missed"), desc: t("alerts.rec.missedDesc") },
                         { id: "rd_alert_interrupted", label: t("alerts.rec.interrupted"), desc: t("alerts.rec.interruptedDesc") },
                       ].map(({ id, label, desc }) => (
                         <DvrFieldToggle key={id} cardId="rec" fieldKey={id} label={label} desc={desc} helpers={dvrHelpers} />
                       ))}
                     </div>
                   </div>
                   <div className="space-y-2">
                     <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                     <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                       {[
                         { id: "rd_program_name", label: t("alerts.rec.programName"), desc: t("alerts.rec.programNameDesc") },
                         { id: "rd_program_desc", label: t("alerts.rec.description"), desc: t("alerts.rec.descriptionDesc") },
                         { id: "rd_duration", label: t("alerts.rec.duration"), desc: t("alerts.rec.durationDesc") },
                         { id: "rd_channel_name", label: t("alerts.rec.channelName"), desc: t("alerts.rec.channelNameDesc") },
                         { id: "rd_channel_number", label: t("alerts.rec.channelNumber"), desc: t("alerts.rec.channelNumberDesc") },
                         { id: "rd_type", label: t("alerts.rec.type"), desc: t("alerts.rec.typeDesc") },
                       ].map(({ id, label, desc }) => (
                         <DvrFieldToggle key={id} cardId="rec" fieldKey={id} label={label} desc={desc} helpers={dvrHelpers} />
                       ))}
                     </div>
                   </div>
                 </fieldset>
              </>
            )}
          </CardContent>
        )}
      </Card>

      <Card className="order-4 border-blue-400/20 overflow-hidden">
        <AlertDisclosureHeader
          cardId="disk"
          icon={HardDrive}
          title={t("alerts.diskSpace.title")}
          description={t("alerts.diskSpace.desc")}
          summary={scopedSummary("disk", watch("alert_disk_space") ? t("alerts.summary.on") : t("alerts.summary.off"))}
          expanded={Boolean(expandedAlerts.disk)}
          onToggle={() => toggleAlert("disk")}
        />
        {expandedAlerts.disk && (
          <CardContent id="alert-section-disk" className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <DvrTabBar cardId="disk" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("disk") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_disk_space_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.diskSpace.title")} Alert</span>
                     <span className="text-xs text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_disk_space_global" checked={watch("alert_disk_space")} onCheckedChange={(checked) => setValue("alert_disk_space", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                <fieldset disabled={!watch("alert_disk_space")} className={cn("space-y-2", !watch("alert_disk_space") && "opacity-60")}>
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.diskThreshold")}</p>
                  <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                    <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                      <Label htmlFor="ds_warning_threshold_percent">
                         <span className="text-sm block">{t("alerts.disk.percentage")}</span>
                         <span className="text-xs text-muted-foreground">{t("alerts.disk.percentageDesc")}</span>
                      </Label>
                      <div className="flex items-center gap-2">
                        <Input id="ds_warning_threshold_percent" type="number" min="0" max="100" step="1" placeholder="10" {...register("ds_warning_threshold_percent", { valueAsNumber: true, min: { value: 0, message: t("alerts.disk.validation.range100") }, max: { value: 100, message: t("alerts.disk.validation.range100") } })} className={errors.ds_warning_threshold_percent ? "h-8 text-sm border-red-500" : "h-8 text-sm"} />
                        <span className="text-muted-foreground text-sm">%</span>
                      </div>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                      <Label htmlFor="ds_warning_threshold_gb">
                         <span className="text-sm block">{t("alerts.disk.gigabytes")}</span>
                         <span className="text-xs text-muted-foreground">{t("alerts.disk.gigabytesDesc")}</span>
                      </Label>
                      <div className="flex items-center gap-2">
                        <Input id="ds_warning_threshold_gb" type="number" min="0" step="1" placeholder="50" {...register("ds_warning_threshold_gb", { valueAsNumber: true })} className="h-8 text-sm" />
                        <span className="text-muted-foreground text-sm">GB</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-start gap-2 rounded-lg border border-blue-400/10 bg-blue-500/5 p-3">
                    <Info className="h-4 w-4 text-blue-400 mt-0.5 shrink-0" />
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      {t("alerts.diskAdvancedHint")}
                    </p>
                  </div>
                </fieldset>
              </>
            ) : (
              <>
                <DvrFieldToggle cardId="disk" fieldKey="alert_disk_space" label={t("alerts.diskSpace.title") + " Alert"} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <fieldset disabled={!dvrHelpers.dvrFieldValue("disk", "alert_disk_space").value} className={cn("space-y-2", !dvrHelpers.dvrFieldValue("disk", "alert_disk_space").value && "opacity-60")}>
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.diskThreshold")}</p>
                  <div className="grid grid-cols-1 gap-2 rounded-lg border border-blue-400/10 bg-background/50 p-3 sm:grid-cols-2">
                     <DvrFieldInput cardId="disk" fieldKey="ds_warning_threshold_percent" label={t("alerts.disk.percentage")} desc={t("alerts.disk.percentageDesc")} min={0} max={100} suffix="%" helpers={dvrHelpers} />
                     <DvrFieldInput cardId="disk" fieldKey="ds_warning_threshold_gb" label={t("alerts.disk.gigabytes")} desc={t("alerts.disk.gigabytesDesc")} min={0} suffix="GB" helpers={dvrHelpers} />
                  </div>
                  <div className="flex items-start gap-2 rounded-lg border border-blue-400/10 bg-blue-500/5 p-3">
                    <Info className="h-4 w-4 text-blue-400 mt-0.5 shrink-0" />
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      {t("alerts.diskAdvancedHintDvr")}
                    </p>
                  </div>
                </fieldset>
              </>
            )}
          </CardContent>
        )}
      </Card>
    </TabsContent>
  )
}
