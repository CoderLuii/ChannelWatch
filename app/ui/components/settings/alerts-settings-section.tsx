import { Bell, ChevronDown, Clock, HardDrive, Info, Share2, Tv, Video } from "lucide-react"
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

  return (
    <TabsContent value="alerts" className="space-y-4">
      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("sc")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Share2 className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("alerts.streamCounter.title")}</p>
              <p className="text-sm text-muted-foreground">{t("alerts.streamCounter.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.sc && "rotate-180")} />
        </div>
        {expandedAlerts.sc && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-3">
            <DvrTabBar cardId="sc" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("sc") === "global" ? (
              <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                <Label htmlFor="stream_count_global" className="cursor-pointer">
                  <span className="text-sm block">{t("alerts.field.streamCounter")}</span>
                  <span className="text-[11px] text-muted-foreground">{t("alerts.field.streamCounterDesc")}</span>
                </Label>
                <Switch id="stream_count_global" checked={watch("stream_count")} onCheckedChange={(checked) => setValue("stream_count", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
              </div>
            ) : (
              <DvrFieldToggle cardId="sc" fieldKey="stream_count" label={t("alerts.field.streamCounter")} desc={t("alerts.field.streamCounterDvrDesc")} helpers={dvrHelpers} />
            )}
          </CardContent>
        )}
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("cw")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Tv className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("alerts.channelWatching.title")}</p>
              <p className="text-sm text-muted-foreground">{t("alerts.channelWatching.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.cw && "rotate-180")} />
        </div>
        {expandedAlerts.cw && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-5">
            <DvrTabBar cardId="cw" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("cw") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_channel_watching_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.channelWatching.title")}</span>
                     <span className="text-[11px] text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_channel_watching_global" checked={watch("alert_channel_watching")} onCheckedChange={(checked) => setValue("alert_channel_watching", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                <div className={cn("space-y-5", !watch("alert_channel_watching") && "opacity-50 pointer-events-none")}>
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
                     <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
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
                             <span className="text-[11px] text-muted-foreground">{desc}</span>
                           </Label>
                          <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                         </div>
                       ))}
                     </div>
                   </div>
                 </div>
               </>
            ) : (
               <>
                 <DvrFieldToggle cardId="cw" fieldKey="alert_channel_watching" label={t("alerts.channelWatching.title")} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <div className={cn("space-y-5", !dvrHelpers.dvrFieldValue("cw", "alert_channel_watching").value && "opacity-50 pointer-events-none")}>
                   <DvrFieldSelect cardId="cw" fieldKey="cw_image_source" label={t("alerts.notifImage")} desc={t("alerts.field.imageInNotif")} options={[{ value: "PROGRAM", label: t("alerts.field.programImage") }, { value: "CHANNEL", label: t("alerts.field.channelLogo") }]} defaultValue="PROGRAM" helpers={dvrHelpers} />
                   <div className="space-y-2">
                     <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                     <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
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
                 </div>
               </>
             )}
          </CardContent>
        )}
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("vod")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Video className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("alerts.vodWatching.title")}</p>
              <p className="text-sm text-muted-foreground">{t("alerts.vodWatching.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.vod && "rotate-180")} />
        </div>
        {expandedAlerts.vod && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <DvrTabBar cardId="vod" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("vod") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_vod_watching_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.vodWatching.title")}</span>
                     <span className="text-[11px] text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_vod_watching_global" checked={watch("alert_vod_watching")} onCheckedChange={(checked) => setValue("alert_vod_watching", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                 <div className={cn("space-y-2", !watch("alert_vod_watching") && "opacity-50 pointer-events-none")}>
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                   <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
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
                           <span className="text-[11px] text-muted-foreground">{desc}</span>
                         </Label>
                        <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                       </div>
                     ))}
                   </div>
                 </div>
               </>
             ) : (
               <>
                 <DvrFieldToggle cardId="vod" fieldKey="alert_vod_watching" label={t("alerts.vodWatching.title")} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <div className={cn("space-y-2", !dvrHelpers.dvrFieldValue("vod", "alert_vod_watching").value && "opacity-50 pointer-events-none")}>
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                   <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
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
                 </div>
               </>
             )}
          </CardContent>
        )}
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("rec")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <Clock className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("alerts.recordingEvents.title")}</p>
              <p className="text-sm text-muted-foreground">{t("alerts.recordingEvents.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.rec && "rotate-180")} />
        </div>
        {expandedAlerts.rec && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-5">
            <DvrTabBar cardId="rec" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("rec") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_recording_events_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.recordingEvents.title")}</span>
                     <span className="text-[11px] text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_recording_events_global" checked={watch("alert_recording_events")} onCheckedChange={(checked) => setValue("alert_recording_events", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                <div className={cn("space-y-5", !watch("alert_recording_events") && "opacity-50 pointer-events-none")}>
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.notifyWhenRecording")}</p>
                    <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                      {[
                        { id: "rd_alert_scheduled", label: t("alerts.rec.scheduled"), desc: t("alerts.rec.scheduledDesc") },
                        { id: "rd_alert_started", label: t("alerts.rec.started"), desc: t("alerts.rec.startedDesc") },
                        { id: "rd_alert_completed", label: t("alerts.rec.completed"), desc: t("alerts.rec.completedDesc") },
                        { id: "rd_alert_cancelled", label: t("alerts.rec.cancelled"), desc: t("alerts.rec.cancelledDesc") },
                      ].map(({ id, label, desc }) => (
                        <div key={id} className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10 transition-colors hover:bg-blue-500/10">
                          <Label htmlFor={id} className="cursor-pointer">
                            <span className="text-sm block">{label}</span>
                            <span className="text-[11px] text-muted-foreground">{desc}</span>
                          </Label>
                          <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                    <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
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
                             <span className="text-[11px] text-muted-foreground">{desc}</span>
                           </Label>
                            <Switch id={id} checked={watch(id as BooleanSettingKey)} onCheckedChange={(checked) => setValue(id as BooleanSettingKey, checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                         </div>
                       ))}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <>
                 <DvrFieldToggle cardId="rec" fieldKey="alert_recording_events" label={t("alerts.recordingEvents.title")} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <div className={cn("space-y-5", !dvrHelpers.dvrFieldValue("rec", "alert_recording_events").value && "opacity-50 pointer-events-none")}>
                   <div className="space-y-2">
                     <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.notifyWhenRecording")}</p>
                     <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                       {[
                         { id: "rd_alert_scheduled", label: t("alerts.rec.scheduled"), desc: t("alerts.rec.scheduledDesc") },
                         { id: "rd_alert_started", label: t("alerts.rec.started"), desc: t("alerts.rec.startedDesc") },
                         { id: "rd_alert_completed", label: t("alerts.rec.completed"), desc: t("alerts.rec.completedDesc") },
                         { id: "rd_alert_cancelled", label: t("alerts.rec.cancelled"), desc: t("alerts.rec.cancelledDesc") },
                       ].map(({ id, label, desc }) => (
                         <DvrFieldToggle key={id} cardId="rec" fieldKey={id} label={label} desc={desc} helpers={dvrHelpers} />
                       ))}
                     </div>
                   </div>
                   <div className="space-y-2">
                     <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.showInNotif")}</p>
                     <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
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
                 </div>
              </>
            )}
          </CardContent>
        )}
      </Card>

      <Card className="border-blue-400/20 overflow-hidden">
        <div className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => toggleAlert("disk")}>
          <div className="flex gap-3 items-center">
            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
              <HardDrive className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <p className="text-base font-medium">{t("alerts.diskSpace.title")}</p>
              <p className="text-sm text-muted-foreground">{t("alerts.diskSpace.desc")}</p>
            </div>
          </div>
          <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expandedAlerts.disk && "rotate-180")} />
        </div>
        {expandedAlerts.disk && (
          <CardContent className="border-t border-blue-400/10 bg-muted/20 pt-5 space-y-2">
            <DvrTabBar cardId="disk" helpers={dvrHelpers} />
            {dvrHelpers.getDvrTab("disk") === "global" ? (
              <>
                <div className="flex items-center justify-between p-2.5 rounded-lg bg-muted/40 border border-blue-400/10">
                  <Label htmlFor="alert_disk_space_global" className="cursor-pointer">
                    <span className="text-sm block">{t("alerts.diskSpace.title")} Alert</span>
                     <span className="text-[11px] text-muted-foreground">{t("alerts.enableGlobal")}</span>
                  </Label>
                  <Switch id="alert_disk_space_global" checked={watch("alert_disk_space")} onCheckedChange={(checked) => setValue("alert_disk_space", checked, { shouldDirty: true })} className="data-[state=checked]:bg-blue-600" />
                </div>
                <div className={cn("space-y-2", !watch("alert_disk_space") && "opacity-50 pointer-events-none")}>
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.diskThreshold")}</p>
                  <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                    <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                      <Label htmlFor="ds_warning_threshold_percent">
                         <span className="text-sm block">{t("alerts.disk.percentage")}</span>
                         <span className="text-[11px] text-muted-foreground">{t("alerts.disk.percentageDesc")}</span>
                      </Label>
                      <div className="flex items-center gap-2">
                        <Input id="ds_warning_threshold_percent" type="number" min="0" max="100" step="1" placeholder="10" {...register("ds_warning_threshold_percent", { valueAsNumber: true, min: { value: 0, message: t("alerts.disk.validation.range100") }, max: { value: 100, message: t("alerts.disk.validation.range100") } })} className={errors.ds_warning_threshold_percent ? "h-8 text-sm border-red-500" : "h-8 text-sm"} />
                        <span className="text-muted-foreground text-sm">%</span>
                      </div>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/40 border border-blue-400/10 space-y-2">
                      <Label htmlFor="ds_warning_threshold_gb">
                         <span className="text-sm block">{t("alerts.disk.gigabytes")}</span>
                         <span className="text-[11px] text-muted-foreground">{t("alerts.disk.gigabytesDesc")}</span>
                      </Label>
                      <div className="flex items-center gap-2">
                        <Input id="ds_warning_threshold_gb" type="number" min="0" step="1" placeholder="50" {...register("ds_warning_threshold_gb", { valueAsNumber: true })} className="h-8 text-sm" />
                        <span className="text-muted-foreground text-sm">GB</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-start gap-2 rounded-lg border border-blue-400/10 bg-blue-500/5 p-3">
                    <Info className="h-4 w-4 text-blue-400 mt-0.5 shrink-0" />
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      {t("alerts.diskAdvancedHint")}
                    </p>
                  </div>
                </div>
              </>
            ) : (
              <>
                <DvrFieldToggle cardId="disk" fieldKey="alert_disk_space" label={t("alerts.diskSpace.title") + " Alert"} desc={t("alerts.enableForDvr")} helpers={dvrHelpers} />
                 <div className={cn("space-y-2", !dvrHelpers.dvrFieldValue("disk", "alert_disk_space").value && "opacity-50 pointer-events-none")}>
                   <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{t("alerts.diskThreshold")}</p>
                  <div className="grid grid-cols-2 gap-2 rounded-xl p-3 border border-blue-400/10 bg-background/50">
                     <DvrFieldInput cardId="disk" fieldKey="ds_warning_threshold_percent" label={t("alerts.disk.percentage")} desc={t("alerts.disk.percentageDesc")} min={0} max={100} suffix="%" helpers={dvrHelpers} />
                     <DvrFieldInput cardId="disk" fieldKey="ds_warning_threshold_gb" label={t("alerts.disk.gigabytes")} desc={t("alerts.disk.gigabytesDesc")} min={0} suffix="GB" helpers={dvrHelpers} />
                  </div>
                  <div className="flex items-start gap-2 rounded-lg border border-blue-400/10 bg-blue-500/5 p-3">
                    <Info className="h-4 w-4 text-blue-400 mt-0.5 shrink-0" />
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      {t("alerts.diskAdvancedHintDvr")}
                    </p>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        )}
      </Card>
    </TabsContent>
  )
}
