import { useState, type Dispatch, type ReactNode, type SetStateAction } from "react"
import { Bell, ChevronDown, Eye, EyeOff, Info, Link, PenLine, RotateCcw, Share2 } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Tag } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Switch } from "@/components/base/switch"
import { TabsContent } from "@/components/base/tabs"
import { cn } from "@/lib/utils"
import { t } from "@/lib/i18n"
import type { AppSettings } from "@/lib/types"

import { DvrHelpers, DvrTabBar } from "./dvr-field-controls"
import {
  TrustedDestinationControl,
  TrustedDestinationList,
} from "./trusted-destination-control"

type TemplateTitleKey = "cw_template_title" | "vod_template_title" | "rd_template_title" | "ds_template_title"
type TemplateBodyKey = "cw_template_body" | "vod_template_body" | "rd_template_body" | "ds_template_body"
type TemplateToggleKey = "cw_template_use_default" | "vod_template_use_default" | "rd_template_use_default" | "ds_template_use_default"
type AppriseSettingKey = "apprise_pushover" | "apprise_discord" | "apprise_email" | "apprise_email_to" | "apprise_telegram" | "apprise_slack" | "apprise_gotify" | "apprise_matrix" | "apprise_custom"

interface TemplateEditorConfig {
  id: string
  label: string
  titleKey: TemplateTitleKey
  bodyKey: TemplateBodyKey
  useDefaultKey: TemplateToggleKey
  defaultTitle: string
  defaultBody: string
  placeholders: string[]
  conditionalTags?: string[]
}

const TEMPLATE_EDITOR_CONFIGS: TemplateEditorConfig[] = [
  {
    id: "cw",
    label: t("alerts.channelWatching.title"),
    titleKey: "cw_template_title",
    bodyKey: "cw_template_body",
    useDefaultKey: "cw_template_use_default",
    defaultTitle: t("notifications.templates.cw.defaultTitle"),
    defaultBody: t("notifications.templates.cw.defaultBody"),
    placeholders: ["channel_name", "channel_number", "program_title", "resolution", "client_name", "client_ip", "stream_source", "stream_count"],
  },
  {
    id: "vod",
    label: t("alerts.vodWatching.label"),
    titleKey: "vod_template_title",
    bodyKey: "vod_template_body",
    useDefaultKey: "vod_template_use_default",
    defaultTitle: t("notifications.templates.vod.defaultTitle"),
    defaultBody: t("notifications.templates.vod.defaultBody"),
    placeholders: ["media_title", "progress_line", "client_name", "client_ip", "summary_block", "info_sections"],
    conditionalTags: ["movie", "episode", "show", "live", "recorded"],
  },
  {
    id: "rd",
    label: t("alerts.recordingEvents.title"),
    titleKey: "rd_template_title",
    bodyKey: "rd_template_body",
    useDefaultKey: "rd_template_use_default",
    defaultTitle: t("notifications.templates.rd.defaultTitle"),
    defaultBody: t("notifications.templates.rd.defaultBody"),
    placeholders: ["channel_name", "channel_number", "status", "details", "summary_block", "time_table"],
    conditionalTags: ["started", "completed", "failed", "cancelled"],
  },
  {
    id: "ds",
    label: t("alerts.diskSpace.label"),
    titleKey: "ds_template_title",
    bodyKey: "ds_template_body",
    useDefaultKey: "ds_template_use_default",
    defaultTitle: t("notifications.templates.ds.defaultTitle"),
    defaultBody: t("notifications.templates.ds.defaultBody"),
    placeholders: ["disk_free", "disk_total", "disk_percent", "disk_used", "disk_path"],
  },
]

function TemplateEditorCard({ config, form }: { config: TemplateEditorConfig; form: UseFormReturn<AppSettings> }) {
  const { setValue, watch } = form
  const [editorOpen, setEditorOpen] = useState(false)
  const useDefault = !!watch(config.useDefaultKey)
  const titleValue = (watch(config.titleKey) as string) || ""
  const bodyValue = (watch(config.bodyKey) as string) || ""

  const resetToDefault = () => {
    setValue(config.titleKey, config.defaultTitle, { shouldDirty: true })
    setValue(config.bodyKey, config.defaultBody, { shouldDirty: true })
    setValue(config.useDefaultKey, true, { shouldDirty: true })
  }

  return (
    <div className="space-y-4 rounded-control border border-hairline p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <PenLine className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-base font-medium">{config.label}</p>
              <Tag>{t(useDefault ? "notifications.templates.defaultBadge" : "notifications.templates.customBadge")}</Tag>
            </div>
          </div>
        </div>

      </div>

      <Button
        type="button"
        variant="outline"
        className="min-h-11 w-full justify-between"
        aria-expanded={editorOpen}
        aria-controls={`${config.id}-template-editor`}
        onClick={() => setEditorOpen((open) => !open)}
      >
        <span>{editorOpen ? t("notifications.templates.doneBtn") : t("notifications.templates.editBtn")}</span>
        <ChevronDown className={cn("h-4 w-4 transition-transform duration-[var(--motion-base)] ease-out", editorOpen && "rotate-180")} aria-hidden="true" />
      </Button>

      <div id={`${config.id}-template-editor`} className={cn("disclosure-enter space-y-4", !editorOpen && "hidden")}>
        <div className="flex flex-wrap items-center gap-3 lg:justify-end">
          <div className="flex items-center gap-2 rounded-control bg-fill px-3 py-2">
            <Label htmlFor={`${config.id}_use_default`} className="text-xs text-muted-foreground">{t("notifications.templates.useDefault")}</Label>
            <Switch
              id={`${config.id}_use_default`}
              checked={useDefault}
              onCheckedChange={(checked) => setValue(config.useDefaultKey, checked, { shouldDirty: true })}

            />
          </div>
          <Button type="button" variant="outline" size="sm" onClick={resetToDefault}>
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            {t("notifications.templates.resetBtn")}
          </Button>
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor={`${config.id}_template_title`}>{t("notifications.templates.titleLbl")}</Label>
            <span className="block text-xs font-normal text-muted-foreground">{t("notifications.templates.singleLine")}</span>
          </div>
          <Input
            id={`${config.id}_template_title`}
            value={titleValue}
            onChange={(event) => setValue(config.titleKey, event.target.value, { shouldDirty: true })}
            disabled={useDefault}
            className={cn("h-9 text-sm", useDefault && "opacity-70")}
          />
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor={`${config.id}_template_body`}>{t("notifications.templates.bodyLbl")}</Label>
            <span className="block text-xs font-normal text-muted-foreground">{t("notifications.templates.multiLine")}</span>
          </div>
          <textarea
            id={`${config.id}_template_body`}
            value={bodyValue}
            onChange={(event) => setValue(config.bodyKey, event.target.value, { shouldDirty: true })}
            disabled={useDefault}
            className={cn(
              "flex min-h-[108px] w-full rounded-control border border-input bg-well px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-70",
              useDefault && "opacity-70"
            )}
          />
        </div>
        </div>

        <div className="space-y-3 rounded-control bg-fill p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">{t("notifications.templates.placeholdersLbl")}</span>
          {config.placeholders.map((placeholder) => (
            <code key={placeholder} className="rounded-thumb bg-fill px-1.5 py-0.5 text-xs">
              {`{${placeholder}}`}
            </code>
          ))}
        </div>
        {config.conditionalTags && config.conditionalTags.length > 0 && (
          <p className="text-xs leading-5 text-muted-foreground">
            {t("notifications.templates.conditionalTags")} {config.conditionalTags.map((tag) => <code key={tag} className="mr-1">{`<{tag}>... </${tag}>`.replace("{tag}", tag)}</code>)}
          </p>
        )}
        </div>
      </div>
    </div>
  )
}

function MobileProviderConfiguration({ label, children }: { label: string; children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const contentId = `provider-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-configuration`

  return (
    <div className="space-y-3 px-4 pb-4">
      <Button
        type="button"
        variant="ghost"
        className="h-auto min-h-11 w-full min-w-0 justify-between gap-2 whitespace-normal border border-input px-3 py-2 text-center md:hidden"
        aria-expanded={mobileOpen}
        aria-controls={contentId}
        onClick={() => setMobileOpen((open) => !open)}
      >
        <span className="min-w-0 break-words [overflow-wrap:anywhere]">{label} configuration</span>
        <ChevronDown className={cn("h-4 w-4 transition-transform duration-[var(--motion-base)] ease-out", mobileOpen && "rotate-180")} aria-hidden="true" />
      </Button>
      <div id={contentId} className={cn("disclosure-enter space-y-3 md:block md:pl-8", !mobileOpen && "hidden")}>
        {children}
      </div>
    </div>
  )
}

interface NotificationsSettingsSectionProps {
  form: UseFormReturn<AppSettings>
  dvrHelpers: DvrHelpers
  webhookEntries: AppSettings["webhooks"]
  visibleCredentials: Record<string, boolean>
  setVisibleCredentials: Dispatch<SetStateAction<Record<string, boolean>>>
  enabledProviders: {
    pushover: boolean
    discord: boolean
    email: boolean
    telegram: boolean
    slack: boolean
    gotify: boolean
    matrix: boolean
    custom: boolean
  }
  setEnabledProviders: Dispatch<
    SetStateAction<{
      pushover: boolean
      discord: boolean
      email: boolean
      telegram: boolean
      slack: boolean
      gotify: boolean
      matrix: boolean
      custom: boolean
    }>
  >
  addWebhookEntry: () => void
  updateWebhookEntry: (index: number, key: "url" | "secret" | "enabled", value: string | boolean) => void
  removeWebhookEntry: (index: number) => void
}

export function NotificationsSettingsSection({
  form,
  dvrHelpers,
  webhookEntries,
  visibleCredentials,
  setVisibleCredentials,
  enabledProviders,
  setEnabledProviders,
  addWebhookEntry,
  updateWebhookEntry,
  removeWebhookEntry,
}: NotificationsSettingsSectionProps) {
  const { getValues, register, setValue, watch } = form
  const [mobileWebhooksOpen, setMobileWebhooksOpen] = useState(false)
  const [templatesOpen, setTemplatesOpen] = useState(false)

  return (
    <TabsContent value="notifications" className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
            <div className="flex items-start gap-2">
              <Share2 className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div>
                <CardTitle>{t("notifications.webhooks.title")}</CardTitle>
                <CardDescription>{t("notifications.webhooks.description")}</CardDescription>
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              className="mt-3 h-auto min-h-11 w-full justify-between whitespace-normal py-2 text-left md:hidden"
              aria-expanded={mobileWebhooksOpen}
              aria-controls="mobile-webhook-settings"
              onClick={() => setMobileWebhooksOpen((open) => !open)}
            >
              <span>{mobileWebhooksOpen ? "Hide webhook settings" : "Show webhook settings"}</span>
              <ChevronDown className={cn("h-4 w-4 transition-transform duration-[var(--motion-base)] ease-out", mobileWebhooksOpen && "rotate-180")} aria-hidden="true" />
            </Button>
          </CardHeader>
        <CardContent id="mobile-webhook-settings" className={cn("relative z-10 space-y-4 pt-4", !mobileWebhooksOpen && "hidden md:block")}>
          <Alert>
            <Info className="h-4 w-4" />
            <AlertTitle>{t("notifications.webhooks.alertTitle")}</AlertTitle>
            <AlertDescription>
              {t("notifications.webhooks.alertDescPre")}<code>X-ChannelWatch-Signature</code>{", "}<code>X-ChannelWatch-Delivery</code>{", and "}<code>X-ChannelWatch-Event</code>{t("notifications.webhooks.alertDescPost")}
            </AlertDescription>
          </Alert>
          <TrustedDestinationList form={form} />

          <div className="space-y-4">
            {webhookEntries.length === 0 ? (
              <div className="rounded-control border border-dashed border-hairline p-6 text-sm text-muted-foreground">{t("notifications.webhooks.noWebhooks")}</div>
            ) : (
              webhookEntries.map((webhook, index) => {
                const secretKey = `global_webhook_${index}_secret`
                const secretVisible = visibleCredentials[secretKey]
                return (
                  <div key={`webhook-${index}`} className="space-y-4 rounded-control border border-hairline p-4">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-medium">{t("notifications.webhooks.entryTitle", { n: index + 1 })}</p>
                      </div>
                      <div className="flex items-center gap-3">
                        <div className="flex items-center gap-2">
                          <Label htmlFor={`webhook-enabled-${index}`} className="text-xs text-muted-foreground">{t("notifications.webhooks.enabledLbl")}</Label>
                          <Switch id={`webhook-enabled-${index}`} aria-label={`${t("notifications.webhooks.entryTitle", { n: index + 1 })}: ${t("notifications.webhooks.enabledLbl")}`} checked={!!webhook?.enabled} onCheckedChange={(checked) => updateWebhookEntry(index, "enabled", checked)}  />
                        </div>
                          <Button type="button" variant="outline" size="sm" onClick={() => removeWebhookEntry(index)}>
                          {t("notifications.webhooks.removeBtn")}
                        </Button>
                      </div>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="space-y-2 md:col-span-2">
                        <Label htmlFor={`webhook-url-${index}`}>{t("notifications.webhooks.urlLbl")}</Label>
                        <Input id={`webhook-url-${index}`} type="url" placeholder="http://receiver.local:9000/channelwatch" value={webhook?.url || ""} onChange={(event) => updateWebhookEntry(index, "url", event.target.value)} className="h-11 text-sm md:h-9" />
                        <TrustedDestinationControl form={form} source="webhook" url={webhook?.url || ""} compact />
                      </div>

                      <div className="space-y-2 md:col-span-2">
                        <Label htmlFor={`webhook-secret-${index}`}>{t("notifications.webhooks.secretLbl")}</Label>
                        <div className="flex items-center gap-2">
                          <Input id={`webhook-secret-${index}`} type={secretVisible ? "text" : "password"} placeholder="shared-secret" value={webhook?.secret || ""} onChange={(event) => updateWebhookEntry(index, "secret", event.target.value)} className="h-11 text-sm md:h-9" />
                          <button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(secretVisible ? "notifications.hideCredential" : "notifications.showCredential", { name: t("notifications.webhooks.entryTitle", { n: index + 1 }) })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, [secretKey]: !prev[secretKey] }))}>
                            {secretVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </button>
                        </div>
                        <p className="text-xs text-muted-foreground">{t("notifications.webhooks.secretHint")}</p>
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>

          <Button type="button" variant="outline" onClick={addWebhookEntry}>
            {t("notifications.webhooks.addBtn")}
          </Button>
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
            <div className="flex min-w-0 items-start gap-2">
              <Bell className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div className="min-w-0">
                <CardTitle className="break-words [overflow-wrap:anywhere]">{t("notifications.providers.title")}</CardTitle>
                <CardDescription className="break-words [overflow-wrap:anywhere]">{t("notifications.providers.description")}</CardDescription>
              </div>
            </div>
          </CardHeader>
        <CardContent className="space-y-4 pt-4">
          <DvrTabBar cardId="notif" helpers={dvrHelpers} />
          {dvrHelpers.getDvrTab("notif") !== "global" ? (
            <div className="-mx-4 -mb-4 divide-y divide-hairline border-t border-hairline">
              {([
                { key: "apprise_pushover", label: t("provider.pushover.name"), desc: t("provider.pushover.desc"), placeholder: "user_key@api_token", sensitive: true },
                { key: "apprise_discord", label: t("provider.discord.name"), desc: t("provider.discord.desc"), placeholder: "webhook_id/token", sensitive: true },
                { key: "apprise_email", label: t("provider.email.labelFrom"), desc: t("provider.email.smtpLbl"), placeholder: "user:pass@smtp.example.com", sensitive: true },
                { key: "apprise_email_to", label: t("provider.email.labelTo"), desc: t("provider.email.recipientLbl"), placeholder: "user@example.com", sensitive: false },
                { key: "apprise_telegram", label: t("provider.telegram.name"), desc: t("provider.telegram.desc"), placeholder: "bottoken/ChatID", sensitive: true },
                { key: "apprise_slack", label: t("provider.slack.name"), desc: t("provider.slack.desc"), placeholder: "token_a/token_b/token_c", sensitive: true },
                { key: "apprise_gotify", label: t("provider.gotify.name"), desc: t("provider.gotify.desc"), placeholder: "hostname/token", sensitive: true },
                { key: "apprise_matrix", label: t("provider.matrix.name"), desc: t("provider.matrix.desc"), placeholder: "user:pass@hostname/#room", sensitive: true },
                { key: "apprise_custom", label: t("provider.custom.name"), desc: t("provider.custom.desc"), placeholder: "apprise://...", sensitive: true },
              ] satisfies Array<{ key: AppriseSettingKey; label: string; desc: string; placeholder: string; sensitive: boolean }>).map(({ key, label, desc, placeholder, sensitive }) => {
                const { value, hasOverride, isGlobal } = dvrHelpers.dvrFieldValue("notif", key)
                const showInherited = !isGlobal && !hasOverride
                const showOverridden = !isGlobal && hasOverride
                const isEnabled = showOverridden ? !!value : !!watch(key)
                const isDisabledOverride = showOverridden && !value
                const credKey = `dvr_${dvrHelpers.getDvrTab("notif")}_${key}`
                const isVisible = visibleCredentials[credKey]

                return (
                  <div key={key}>
                    <div
                      className={cn(
                        "flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]",
                        showInherited && "bg-info-bg/40",
                        showOverridden && "bg-warning-bg/60"
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <div className="flex size-5 shrink-0 items-center justify-center text-muted-foreground">
                          {showInherited && <Link className="h-4 w-4 text-muted-foreground" />}
                          {showOverridden && <PenLine className="h-4 w-4 text-warning-fg" />}
                          {!showInherited && !showOverridden && <Bell className="size-5 text-muted-foreground" />}
                        </div>
                        <div className="flex flex-col justify-center">
                          <span className="text-base font-medium">{label}</span>
                          <span className="block text-xs font-normal text-muted-foreground">{desc}</span>
                          {showInherited && <span className="text-xs leading-5 text-muted-foreground">{t("notifications.provider.usingGlobal")}</span>}
                          {isDisabledOverride && <span className="text-xs leading-5 text-warning-fg">{t("notifications.provider.disabledForDvr")}</span>}
                          {showOverridden && !isDisabledOverride && <span className="text-xs leading-5 text-warning-fg">{t("notifications.provider.overriddenForDvr")}</span>}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Switch
                          aria-label={label}
                          checked={isEnabled}
                          onCheckedChange={(checked) => {
                            if (checked) {
                              dvrHelpers.dvrFieldSet("notif", key, watch(key) || "")
                            } else {
                              dvrHelpers.dvrFieldSet("notif", key, "")
                            }
                          }}
                          className={cn(showInherited && "opacity-50")}
                        />
                        {showOverridden && (
                          <button type="button" className="flex h-11 w-11 items-center justify-center text-warning-fg hover:text-danger-fg transition-colors" title={t("dvr.resetToGlobal")} aria-label={`${label}: ${t("dvr.resetToGlobal")}`} onClick={() => dvrHelpers.dvrFieldReset("notif", key)}>
                            <RotateCcw className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                     {showOverridden && isEnabled && (
                       <div className="space-y-2 px-4 pb-4 pl-12">
                         <Label htmlFor={`provider-${key}-${dvrHelpers.getDvrTab("notif")}`}>{label} URL</Label>
                        <div className="flex items-center gap-2">
                          <Input id={`provider-${key}-${dvrHelpers.getDvrTab("notif")}`} type={sensitive && !isVisible ? "password" : "text"} placeholder={placeholder} value={value || ""} onChange={(event) => dvrHelpers.dvrFieldSet("notif", key, event.target.value)} className="h-11 text-sm md:h-9" />
                          {sensitive && (
                            <button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(isVisible ? "notifications.hideCredential" : "notifications.showCredential", { name: label })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, [credKey]: !prev[credKey] }))}>
                              {isVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                            </button>
                          )}
                        </div>
                        {key === "apprise_custom" && (
                          <TrustedDestinationControl form={form} source="apprise_custom" url={String(value || "")} compact />
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="-mx-4 -mb-4 divide-y divide-hairline border-t border-hairline">
              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-2">
                    <Bell className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <div className="flex flex-col justify-center">
                      <span className="text-base font-medium">{t("provider.pushover.name")}</span>
                      <span className="block text-xs font-normal text-muted-foreground">{t("provider.pushover.desc")}</span>
                    </div>
                  </div>
                  <Switch
                    id="pushover-toggle" aria-label={t("provider.pushover.name")}
                    checked={enabledProviders.pushover}
                    onCheckedChange={(checked) => {
                      setEnabledProviders((prev) => ({ ...prev, pushover: checked }))
                      if (checked) setValue("apprise_pushover", getValues("apprise_pushover") || "", { shouldDirty: true })
                      else setValue("apprise_pushover", "", { shouldDirty: true })
                    }}
                  />
                </div>

                {enabledProviders.pushover && (
                  <MobileProviderConfiguration label={t("provider.pushover.name")}>
                  <div className="space-y-2">
                    <Label htmlFor="apprise_pushover">{t("provider.pushover.urlLbl")}</Label>
                    <div className="flex items-center gap-2">
                      <Input id="apprise_pushover" type={visibleCredentials["global_apprise_pushover"] ? "text" : "password"} placeholder="user_key@api_token" {...register("apprise_pushover")} className="h-11 text-sm md:h-9" />
                      <button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_pushover"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.pushover.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_pushover: !prev.global_apprise_pushover }))}>
                        {visibleCredentials["global_apprise_pushover"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                    <p className="text-xs text-muted-foreground">{t("provider.pushover.hint")}</p>
                  </div>
                  </MobileProviderConfiguration>
                )}
              </div>

              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-2">
                    <svg className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3847-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914a.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286z" />
                      </svg>
                    <div className="flex flex-col justify-center">
                      <span className="text-base font-medium">{t("provider.discord.name")}</span>
                      <span className="block text-xs font-normal text-muted-foreground">{t("provider.discord.desc")}</span>
                    </div>
                  </div>
                  <Switch
                    id="discord-toggle" aria-label={t("provider.discord.name")}
                    checked={enabledProviders.discord}
                    onCheckedChange={(checked) => {
                      setEnabledProviders((prev) => ({ ...prev, discord: checked }))
                      if (checked) setValue("apprise_discord", getValues("apprise_discord") || "", { shouldDirty: true })
                      else setValue("apprise_discord", "", { shouldDirty: true })
                    }}
                  />
                </div>
                {enabledProviders.discord && (
                  <MobileProviderConfiguration label={t("provider.discord.name")}>
                  <div className="space-y-3">
                    <div className="space-y-2">
                     <Label htmlFor="apprise_discord">{t("provider.discord.webhookLbl")}</Label>
                       <div className="flex items-center gap-2">
                         <Input id="apprise_discord" type={visibleCredentials["global_apprise_discord"] ? "text" : "password"} placeholder="webhook_id/token" {...register("apprise_discord")} className="h-11 text-sm md:h-9" />
                         <button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_discord"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.discord.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_discord: !prev.global_apprise_discord }))}>
                           {visibleCredentials["global_apprise_discord"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                         </button>
                       </div>
                       <p className="text-xs text-muted-foreground">{t("provider.discord.hint")}</p>
                    </div>
                  </div>
                  </MobileProviderConfiguration>
                )}
              </div>

              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-2">
                    <svg className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .24z" /></svg>
                    <div className="flex flex-col justify-center">
                      <span className="text-base font-medium">{t("provider.telegram.name")}</span>
                      <span className="block text-xs font-normal text-muted-foreground">{t("provider.telegram.desc")}</span>
                    </div>
                  </div>
                  <Switch id="telegram-toggle" aria-label={t("provider.telegram.name")} checked={enabledProviders.telegram} onCheckedChange={(checked) => {
                    setEnabledProviders((prev) => ({ ...prev, telegram: checked }))
                    if (checked) setValue("apprise_telegram", getValues("apprise_telegram") || "", { shouldDirty: true })
                    else setValue("apprise_telegram", "", { shouldDirty: true })
                  }} />
                </div>
                {enabledProviders.telegram && (
                  <MobileProviderConfiguration label={t("provider.telegram.name")}>
                  <div className="space-y-3">
                    <div className="space-y-2">
                       <Label htmlFor="apprise_telegram">{t("provider.telegram.tokenLbl")}</Label>
                       <div className="flex items-center gap-2">
                         <Input id="apprise_telegram" type={visibleCredentials["global_apprise_telegram"] ? "text" : "password"} placeholder="bottoken/ChatID" className="h-11 text-sm md:h-9" {...register("apprise_telegram")} />
                         <button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_telegram"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.telegram.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_telegram: !prev.global_apprise_telegram }))}>
                           {visibleCredentials["global_apprise_telegram"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                         </button>
                       </div>
                       <p className="text-xs text-muted-foreground">{t("provider.telegram.hint")}</p>
                    </div>
                  </div>
                  </MobileProviderConfiguration>
                )}
              </div>

              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-2">
                    <svg className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>
                    <div className="flex flex-col justify-center">
                      <span className="text-base font-medium">{t("provider.email.name")}</span>
                      <span className="block text-xs font-normal text-muted-foreground">{t("provider.email.desc")}</span>
                    </div>
                  </div>
                  <Switch id="email-toggle" aria-label={t("provider.email.name")} checked={enabledProviders.email} onCheckedChange={(checked) => {
                    setEnabledProviders((prev) => ({ ...prev, email: checked }))
                    if (checked) setValue("apprise_email", getValues("apprise_email") || "", { shouldDirty: true })
                    else {
                      setValue("apprise_email", "", { shouldDirty: true })
                      setValue("apprise_email_to", "", { shouldDirty: true })
                    }
                  }} />
                </div>
                {enabledProviders.email && (
                  <MobileProviderConfiguration label={t("provider.email.name")}>
                  <div className="space-y-3">
                    <div className="space-y-2">
                       <Label htmlFor="apprise_email">{t("provider.email.smtpLbl")}</Label>
                       <div className="flex items-center gap-2">
                         <Input id="apprise_email" type={visibleCredentials["global_apprise_email"] ? "text" : "password"} placeholder="user:password@domain.com" className="h-11 text-sm md:h-9" {...register("apprise_email")} />
                         <button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_email"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.email.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_email: !prev.global_apprise_email }))}>
                           {visibleCredentials["global_apprise_email"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                         </button>
                       </div>
                       <div className="text-xs text-muted-foreground space-y-2">
                         <p className="font-medium">{t("provider.email.basicFormatLabel")} <code>user:password@domain.com</code></p>
                         <details className="cursor-pointer"><summary>{t("provider.email.customFormatSummary")}</summary><div className="pl-3 pt-1"><p>Parameters:</p><ul className="list-disc pl-5 pt-1"><li><code>user</code> - username/email-address</li><li><code>pass</code> - password</li><li><code>smtp</code> - mail server address</li><li><code>port</code> - port number</li></ul><p className="pt-2">Example:</p><p><code className="[overflow-wrap:anywhere]">user=myemail@domain.com&amp;pass=mypassword&amp;smtp=smtp.gmail.com&amp;port=587</code></p></div></details>
                         <details className="cursor-pointer"><summary>{t("provider.email.builtInSummary")}</summary><div className="pl-3 pt-1"><ul className="list-disc pl-5 pt-1"><li>Gmail: <code>user:app-password@gmail.com</code></li><li>Yahoo: <code>user:app-password@yahoo.com</code></li><li>Hotmail/Live: <code>user:password@hotmail.com</code></li><li>Fastmail: <code>user:app-password@fastmail.com</code></li><li>Zoho: <code>user:password@zoho.com</code></li><li>Yandex: <code>user:password@yandex.com</code></li></ul><p className="pt-2 text-xs italic">Note: Google and Yahoo require app-specific passwords if you use 2FA</p></div></details>
                         <p className="mt-2 text-xs"><a href="https://github.com/caronc/apprise/wiki/Notify_email" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{t("provider.email.moreInfo")}</a></p>
                       </div>
                    </div>
                    <div className="space-y-2">
                       <Label htmlFor="apprise_email_to">{t("provider.email.recipientLbl")}</Label>
                       <Input id="apprise_email_to" type="email" placeholder="recipient@example.com" className="h-11 text-sm md:h-9" {...register("apprise_email_to")} />
                    </div>
                  </div>
                  </MobileProviderConfiguration>
                )}
              </div>

              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-2">
                    <Share2 className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <div className="flex flex-col justify-center"><span className="text-base font-medium">{t("provider.slack.name")}</span><span className="block text-xs font-normal text-muted-foreground">{t("provider.slack.desc")}</span></div>
                  </div>
                  <Switch id="slack-toggle" aria-label={t("provider.slack.name")} checked={enabledProviders.slack} onCheckedChange={(checked) => {
                    setEnabledProviders((prev) => ({ ...prev, slack: checked }))
                    if (checked) setValue("apprise_slack", getValues("apprise_slack") || "", { shouldDirty: true })
                    else setValue("apprise_slack", "", { shouldDirty: true })
                  }} />
                </div>
                {enabledProviders.slack && <MobileProviderConfiguration label={t("provider.slack.name")}><div className="space-y-2"><Label htmlFor="apprise_slack">{t("provider.slack.webhookLbl")}</Label><div className="flex items-center gap-2"><Input id="apprise_slack" type={visibleCredentials["global_apprise_slack"] ? "text" : "password"} placeholder="tokenA/tokenB/tokenC" className="h-8 text-sm" {...register("apprise_slack")} /><button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_slack"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.slack.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_slack: !prev.global_apprise_slack }))}>{visibleCredentials["global_apprise_slack"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div><p className="text-xs text-muted-foreground">{t("provider.slack.hint")}</p></div></MobileProviderConfiguration>}
              </div>

              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-3"><Share2 className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />                  <div className="flex flex-col justify-center"><span className="text-base font-medium">{t("provider.gotify.name")}</span><span className="block text-xs font-normal text-muted-foreground">{t("provider.gotify.desc")}</span></div></div>
                  <Switch id="gotify-toggle" aria-label={t("provider.gotify.name")} checked={enabledProviders.gotify} onCheckedChange={(checked) => {
                    setEnabledProviders((prev) => ({ ...prev, gotify: checked }))
                    if (checked) setValue("apprise_gotify", getValues("apprise_gotify") || "", { shouldDirty: true })
                    else setValue("apprise_gotify", "", { shouldDirty: true })
                  }} />
                </div>
                {enabledProviders.gotify && <MobileProviderConfiguration label={t("provider.gotify.name")}><div className="space-y-2"><Label htmlFor="apprise_gotify">{t("provider.gotify.serverLbl")}</Label><div className="flex items-center gap-2"><Input id="apprise_gotify" type={visibleCredentials["global_apprise_gotify"] ? "text" : "password"} placeholder="host.com/token" className="h-8 text-sm" {...register("apprise_gotify")} /><button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_gotify"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.gotify.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_gotify: !prev.global_apprise_gotify }))}>{visibleCredentials["global_apprise_gotify"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div><p className="text-xs text-muted-foreground">{t("provider.gotify.hint")}</p></div></MobileProviderConfiguration>}
              </div>

              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-3"><Share2 className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />                  <div className="flex flex-col justify-center"><span className="text-base font-medium">{t("provider.matrix.name")}</span><span className="block text-xs font-normal text-muted-foreground">{t("provider.matrix.desc")}</span></div></div>
                  <Switch id="matrix-toggle" aria-label={t("provider.matrix.name")} checked={enabledProviders.matrix} onCheckedChange={(checked) => {
                    setEnabledProviders((prev) => ({ ...prev, matrix: checked }))
                    if (checked) setValue("apprise_matrix", getValues("apprise_matrix") || "", { shouldDirty: true })
                    else setValue("apprise_matrix", "", { shouldDirty: true })
                  }} />
                </div>
                {enabledProviders.matrix && <MobileProviderConfiguration label={t("provider.matrix.name")}><div className="space-y-2"><Label htmlFor="apprise_matrix">{t("provider.matrix.serverLbl")}</Label><div className="flex items-center gap-2"><Input id="apprise_matrix" type={visibleCredentials["global_apprise_matrix"] ? "text" : "password"} placeholder="user:pass@domain/#room" className="h-8 text-sm" {...register("apprise_matrix")} /><button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_matrix"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.matrix.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_matrix: !prev.global_apprise_matrix }))}>{visibleCredentials["global_apprise_matrix"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div><p className="text-xs text-muted-foreground">{t("provider.matrix.hint")}</p></div></MobileProviderConfiguration>}
              </div>

              <div>
                <div className="flex min-h-[60px] min-w-0 flex-wrap items-center justify-between gap-3 px-4 py-3 [&>div:first-child]:min-w-0 [&>div:first-child]:flex-1 [&_span]:break-words [&_span]:[overflow-wrap:anywhere]">
                  <div className="flex items-center gap-3"><Share2 className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />                  <div className="flex flex-col justify-center"><span className="text-base font-medium">{t("provider.custom.name")}</span><span className="block text-xs font-normal text-muted-foreground">{t("provider.custom.desc")}</span></div></div>
                  <Switch id="custom-toggle" aria-label={t("provider.custom.name")} checked={enabledProviders.custom} onCheckedChange={(checked) => {
                    setEnabledProviders((prev) => ({ ...prev, custom: checked }))
                    if (checked) setValue("apprise_custom", getValues("apprise_custom") || "", { shouldDirty: true })
                    else setValue("apprise_custom", "", { shouldDirty: true })
                  }} />
                </div>
                {enabledProviders.custom && <MobileProviderConfiguration label={t("provider.custom.name")}><div className="space-y-2"><Label htmlFor="apprise_custom">{t("provider.custom.urlLbl")}</Label><div className="flex items-center gap-2"><Input id="apprise_custom" type={visibleCredentials["global_apprise_custom"] ? "text" : "password"} placeholder="service://user:pass@host.com/path" className="h-8 text-sm" {...register("apprise_custom")} /><button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground hover:text-foreground transition-colors" aria-label={t(visibleCredentials["global_apprise_custom"] ? "notifications.hideCredential" : "notifications.showCredential", { name: t("provider.custom.name") })} onClick={() => setVisibleCredentials((prev) => ({ ...prev, global_apprise_custom: !prev.global_apprise_custom }))}>{visibleCredentials["global_apprise_custom"] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div><p className="text-xs text-muted-foreground">{t("provider.custom.hint")}</p><TrustedDestinationControl form={form} source="apprise_custom" url={watch("apprise_custom") || ""} compact /></div></MobileProviderConfiguration>}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
            <div className="flex items-center gap-2">
              <PenLine className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div>
                <CardTitle>{t("notifications.templates.title")}</CardTitle>
                <CardDescription>{t("notifications.templates.description")}</CardDescription>
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              className="mt-3 min-h-11 w-full justify-between"
              aria-expanded={templatesOpen}
              aria-controls="mobile-notification-template-settings"
              onClick={() => setTemplatesOpen((open) => !open)}
            >
              <span>{templatesOpen ? t("notifications.templates.hideSettings") : t("notifications.templates.showSettings")}</span>
              <ChevronDown className={cn("h-4 w-4 transition-transform duration-[var(--motion-base)] ease-out", templatesOpen && "rotate-180")} aria-hidden="true" />
            </Button>
          </CardHeader>
        <CardContent id="mobile-notification-template-settings" className={cn("disclosure-enter relative z-10 space-y-4 pt-4", !templatesOpen && "hidden")}>
          <Alert>
            <Info className="h-4 w-4" />
            <AlertTitle>{t("notifications.templates.alertTitle")}</AlertTitle>
            <AlertDescription>
              {t("notifications.templates.alertDesc")}
            </AlertDescription>
          </Alert>

          <div className="grid gap-4 2xl:grid-cols-2">
            {TEMPLATE_EDITOR_CONFIGS.map((config) => (
              <TemplateEditorCard key={config.id} config={config} form={form} />
            ))}
          </div>
        </CardContent>
      </Card>
    </TabsContent>
  )
}
