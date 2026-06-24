"use client"

import { useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { AlertCircle, Archive, Bell, Check, Database, GitMerge, Loader2, RefreshCw, Save, Server, Shield } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/base/tabs"
import { useToast } from "@/hooks/use-toast"
import { cn } from "@/lib/utils"
import type { AppSettings } from "@/lib/types"
import { discoverServers, fetchSettings, saveSettings, signalRestart, type DiscoveredServer } from "@/lib/api"
import { canonicalDvrId } from "@/lib/dvr-id"
import { t } from "@/lib/i18n"

import { emptyWebhookEntry } from "@/components/settings/constants"
import { AlertsSettingsSection } from "@/components/settings/alerts-settings-section"
import { AdvancedSettingsSection } from "@/components/settings/advanced-settings-section"
import { BackupSettingsSection } from "@/components/settings/backup-section"
import { GeneralSettingsSection } from "@/components/settings/general-settings-section"
import { NotificationsSettingsSection } from "@/components/settings/notifications-settings-section"
import { RoutingSettingsSection } from "@/components/settings/routing-settings-section"
import { SecuritySettingsSection } from "@/components/settings/security-section"
import type { AppSettingsFieldKey, DvrHelpers } from "@/components/settings/dvr-field-controls"
import type { FieldPathValue } from "react-hook-form"

const MASKED_SENTINEL = "****"

interface SettingsFormProps {
  settings?: AppSettings | null
  onSettingsSaved?: (settings: AppSettings) => void
  initialTab?: string
}

export function SettingsForm({ settings: initialSettings, onSettingsSaved, initialTab }: SettingsFormProps) {
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [activeTab, setActiveTab] = useState(initialTab || "general")
  const [saveSuccess, setSaveSuccess] = useState(false)
  const [bannerDismissing, setBannerDismissing] = useState(false)
  const [enabledProviders, setEnabledProviders] = useState({
    pushover: false,
    discord: false,
    email: false,
    telegram: false,
    slack: false,
    gotify: false,
    matrix: false,
    custom: false,
  })
  const [expandedAlerts, setExpandedAlerts] = useState<Record<string, boolean>>({})
  const [isDiscovering, setIsDiscovering] = useState(false)
  const [discoveredServers, setDiscoveredServers] = useState<DiscoveredServer[]>([])
  const [showDiscoverResults, setShowDiscoverResults] = useState(false)
  const [dvrTab, setDvrTab] = useState<Record<string, string>>({})
  const [visibleCredentials, setVisibleCredentials] = useState<Record<string, boolean>>({})
  const lastFetchedRef = useRef<AppSettings | null>(null)
  const { toast } = useToast()

  const form = useForm<AppSettings>({ mode: "onChange" })
  const { handleSubmit, reset, setValue, getValues, watch } = form
  const {
    errors,
    isDirty,
  } = form.formState

  const webhookEntries = watch("webhooks") || []

  const toggleAlert = (key: string) => setExpandedAlerts((prev) => ({ ...prev, [key]: !prev[key] }))

  const setWebhookEntries = (entries: AppSettings["webhooks"]) => {
    setValue("webhooks", entries, { shouldDirty: true })
  }

  const addWebhookEntry = () => {
    const current = getValues("webhooks") || []
    setWebhookEntries([...current, emptyWebhookEntry()])
  }

  const updateWebhookEntry = (index: number, key: "url" | "secret" | "enabled", value: string | boolean) => {
    const current = [...(getValues("webhooks") || [])]
    current[index] = {
      ...(current[index] || emptyWebhookEntry()),
      [key]: value,
    }
    setWebhookEntries(current)
  }

  const removeWebhookEntry = (index: number) => {
    const current = [...(getValues("webhooks") || [])]
    current.splice(index, 1)
    setWebhookEntries(current)
  }

  const setDvrOverride = <K extends AppSettingsFieldKey>(index: number, key: K, value: FieldPathValue<AppSettings, K>) => {
    const current = [...(getValues("dvr_servers") || [])]
    const overrides = { ...(current[index].overrides || {}) }
    overrides[key] = value
    current[index] = { ...current[index], overrides }
    setValue("dvr_servers", current, { shouldDirty: true })
  }

  const removeDvrOverride = <K extends AppSettingsFieldKey>(index: number, key: K) => {
    const current = [...(getValues("dvr_servers") || [])]
    const overrides = { ...(current[index].overrides || {}) }
    delete overrides[key]
    current[index] = { ...current[index], overrides }
    setValue("dvr_servers", current, { shouldDirty: true })
  }

  const getDvrTab = (cardId: string) => {
    const servers = watch("dvr_servers") || []
    const activeId = dvrTab[cardId] || "global"
    if (activeId !== "global" && !servers.find((server) => server.id === activeId)) return "global"
    return activeId
  }

  const getDvrServerIndex = (cardId: string) => {
    const servers = watch("dvr_servers") || []
    const activeId = getDvrTab(cardId)
    return servers.findIndex((server) => server.id === activeId)
  }

  const dvrFieldValue = <K extends AppSettingsFieldKey>(cardId: string, key: K) => {
    const index = getDvrServerIndex(cardId)
    if (index < 0) return { value: watch(key), hasOverride: false, isGlobal: true }
    const servers = getValues("dvr_servers") || []
    const server = servers[index]
    const hasOverride = server?.overrides?.[key] !== undefined
    const value = hasOverride ? server?.overrides?.[key] as FieldPathValue<AppSettings, K> : watch(key)
    return { value, hasOverride, isGlobal: false }
  }

  const dvrFieldSet = <K extends AppSettingsFieldKey>(cardId: string, key: K, value: FieldPathValue<AppSettings, K>) => {
    const index = getDvrServerIndex(cardId)
    if (index >= 0) setDvrOverride(index, key, value)
  }

  const dvrFieldReset = <K extends AppSettingsFieldKey>(cardId: string, key: K) => {
    const index = getDvrServerIndex(cardId)
    if (index >= 0) removeDvrOverride(index, key)
  }

  const cardFieldKeys: Record<string, AppSettingsFieldKey[]> = {
    cw: ["alert_channel_watching", "cw_image_source", "cw_channel_name", "cw_channel_number", "cw_program_name", "cw_device_name", "cw_device_ip", "cw_stream_source"],
    vod: ["alert_vod_watching", "vod_title", "vod_episode_title", "vod_summary", "vod_duration", "vod_progress", "vod_image", "vod_rating", "vod_genres", "vod_cast", "vod_device_name", "vod_device_ip"],
    rec: ["alert_recording_events", "rd_alert_scheduled", "rd_alert_started", "rd_alert_completed", "rd_alert_cancelled", "rd_program_name", "rd_program_desc", "rd_duration", "rd_channel_name", "rd_channel_number", "rd_type"],
    disk: ["alert_disk_space", "ds_warning_threshold_percent", "ds_warning_threshold_gb", "ds_critical_threshold_percent", "ds_critical_threshold_gb", "ds_startup_grace_seconds", "ds_worsening_delta_gb", "ds_worsening_delta_percent", "ds_alert_cooldown", "ds_test_route_override"],
    sc: ["stream_count"],
    cache: ["channel_cache_ttl", "program_cache_ttl", "job_cache_ttl", "vod_cache_ttl"],
    timing: ["cw_alert_cooldown", "vod_alert_cooldown", "vod_significant_threshold"],
    notif: ["apprise_pushover", "apprise_discord", "apprise_email", "apprise_email_to", "apprise_telegram", "apprise_slack", "apprise_gotify", "apprise_matrix", "apprise_custom"],
    rate: ["global_rate_limit", "global_rate_window"],
  }

  const dvrHelpers: DvrHelpers = {
    dvrTab,
    setDvrTab,
    cardFieldKeys,
    servers: watch("dvr_servers") || [],
    getDvrTab,
    dvrFieldValue,
    dvrFieldSet,
    dvrFieldReset,
    watch,
    setValue,
  }

  const handleDiscover = async () => {
    setIsDiscovering(true)
    setShowDiscoverResults(false)
    setDiscoveredServers([])
    try {
      const result = await discoverServers()
      setDiscoveredServers(result.servers || [])
      setShowDiscoverResults(true)
      if (result.error) {
        toast({ title: t("settings.discovery.issue"), description: result.error, variant: "destructive" })
      }
    } catch {
      toast({ title: t("settings.discovery.failed"), description: t("settings.discovery.failedDesc"), variant: "destructive" })
    } finally {
      setIsDiscovering(false)
    }
  }

  const addServer = () => {
    const current = getValues("dvr_servers") || []
    const host = ""
    const port = 8089
    const id = canonicalDvrId(host, port)
    setValue("dvr_servers", [...current, { id, name: "", host, port, enabled: true }], { shouldDirty: true })
  }

  const addDiscoveredServer = (server: DiscoveredServer, index: number) => {
    const current = getValues("dvr_servers") || []
    const id = canonicalDvrId(server.host, server.port)
    setValue("dvr_servers", [...current, { id, name: server.name || "", host: server.host, port: server.port, enabled: true }], { shouldDirty: true })
    setDiscoveredServers((prev) => prev.filter((_, prevIndex) => prevIndex !== index))
  }

  const removeServer = (index: number) => {
    const current = getValues("dvr_servers") || []
    setValue("dvr_servers", current.filter((_: any, serverIndex: number) => serverIndex !== index), { shouldDirty: true })
  }

  useEffect(() => {
    const loadSettings = async () => {
      try {
        setIsLoading(true)
        setError(null)
        const data = initialSettings ?? await fetchSettings()
        const normalizedData = {
          ...data,
          webhooks: Array.isArray(data.webhooks) ? data.webhooks : [],
          trusted_notification_destinations: Array.isArray(data.trusted_notification_destinations) ? data.trusted_notification_destinations : [],
        }

        setEnabledProviders({
          pushover: !!normalizedData.apprise_pushover,
          discord: !!normalizedData.apprise_discord,
          telegram: !!normalizedData.apprise_telegram,
          email: !!(normalizedData.apprise_email || normalizedData.apprise_email_to),
          slack: !!normalizedData.apprise_slack,
          gotify: !!normalizedData.apprise_gotify,
          matrix: !!normalizedData.apprise_matrix,
          custom: !!normalizedData.apprise_custom,
        })

        reset(normalizedData)
        lastFetchedRef.current = { ...normalizedData }
        setIsLoading(false)
      } catch (err) {
        setError(err instanceof Error ? err.message : t("settings.loadError"))
        setIsLoading(false)
      }
    }

    loadSettings()
  }, [initialSettings, reset])

  const onSubmit = async (data: AppSettings) => {
    const submittedData = { ...data }

    const APPRISE_SENSITIVE_FIELDS = [
      "apprise_pushover", "apprise_discord", "apprise_email",
      "apprise_email_to", "apprise_telegram", "apprise_slack",
      "apprise_gotify", "apprise_matrix", "apprise_custom",
    ] as const

    for (const field of APPRISE_SENSITIVE_FIELDS) {
      if ((submittedData as Record<string, unknown>)[field] === MASKED_SENTINEL) {
        (submittedData as Record<string, unknown>)[field] = ""
      }
    }

    try {
      setIsSubmitting(true)
      setError(null)
      await saveSettings(submittedData)

      const refreshedData = await fetchSettings()
      const normalizedRefreshedData = {
        ...refreshedData,
        webhooks: Array.isArray(refreshedData.webhooks) ? refreshedData.webhooks : [],
        trusted_notification_destinations: Array.isArray(refreshedData.trusted_notification_destinations) ? refreshedData.trusted_notification_destinations : [],
      }

      reset(normalizedRefreshedData, {
        keepValues: false,
        keepDirty: false,
        keepIsSubmitted: false,
      })

      lastFetchedRef.current = { ...normalizedRefreshedData }

      setEnabledProviders({
        pushover: !!normalizedRefreshedData.apprise_pushover,
        discord: !!normalizedRefreshedData.apprise_discord,
        telegram: !!normalizedRefreshedData.apprise_telegram,
        email: !!(normalizedRefreshedData.apprise_email || normalizedRefreshedData.apprise_email_to),
        slack: !!normalizedRefreshedData.apprise_slack,
        gotify: !!normalizedRefreshedData.apprise_gotify,
        matrix: !!normalizedRefreshedData.apprise_matrix,
        custom: !!normalizedRefreshedData.apprise_custom,
      })

      setIsSubmitting(false)
      setSaveSuccess(true)

      if (onSettingsSaved) onSettingsSaved(normalizedRefreshedData)

      try {
        await signalRestart()
      } catch {
        toast({
          variant: "destructive",
          title: t("settings.restartFailed"),
          description: t("settings.restartFailedDesc"),
        })
      }

      setTimeout(() => {
        window.location.hash = "#overview"
        window.location.reload()
      }, 3000)
    } catch (err) {
      toast({
        variant: "destructive",
        title: t("settings.saveError"),
          description: err instanceof Error ? err.message : t("common.unknownError"),
      })
      setIsSubmitting(false)
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div>
          <div className="h-8 w-40 bg-muted rounded mb-2" />
          <div className="h-4 w-72 bg-muted/60 rounded" />
        </div>
        <div className="flex gap-4 border-b pb-2">
          <div className="h-8 w-20 bg-muted rounded" />
          <div className="h-8 w-16 bg-muted/60 rounded" />
          <div className="h-8 w-24 bg-muted/60 rounded" />
          <div className="h-8 w-28 bg-muted/60 rounded" />
        </div>
        <div className="rounded-lg border p-6 space-y-6">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-full bg-muted" />
            <div>
              <div className="h-5 w-32 bg-muted rounded mb-1" />
              <div className="h-3 w-56 bg-muted/60 rounded" />
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="container max-w-5xl mx-auto">
      {saveSuccess && (
        <div
          className={`fixed top-16 left-0 right-0 z-10 ${bannerDismissing ? "animate-slide-to-top" : "animate-slide-from-top"}`}
          onAnimationEnd={() => {
            if (bannerDismissing) {
              setSaveSuccess(false)
              setBannerDismissing(false)
            }
          }}
        >
          <div className="bg-gradient-to-r from-blue-600 to-indigo-700 shadow-lg shadow-blue-900/20">
            <div className="w-full flex justify-center">
              <div className="py-2 px-8 relative flex items-center w-full justify-center">
                <div className="flex items-center gap-2.5">
                  <div className="bg-blue-500/30 backdrop-blur-sm rounded-full p-1">
                    <Check className="h-3.5 w-3.5 text-blue-100" />
                  </div>
                  <span className="font-medium text-blue-50 text-sm">{t("settings.saved")}</span>
                </div>
                <button onClick={() => setBannerDismissing(true)} className="absolute right-4 text-blue-200/70 hover:text-blue-100 transition-colors" aria-label={t("common.dismiss")}>
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2 relative">
          <h1 className="text-3xl font-bold tracking-tight">{t("settings.title")}</h1>
          <p className="text-muted-foreground">{t("settings.description")}</p>

          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>{t("common.error")}</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="flex flex-col gap-8 pb-32">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
              <div className="border-b">
                <div className="flex overflow-x-auto">
                  <TabsList className="inline-flex h-10 items-center justify-center rounded-none bg-transparent p-0">
                    <TabsTrigger value="general" className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none">
                      <Server className="mr-2 h-4 w-4" />
                      {t("settings.tabs.general")}
                    </TabsTrigger>
                    <TabsTrigger value="alerts" className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none">
                      <Bell className="mr-2 h-4 w-4" />
                      {t("settings.tabs.alerts")}
                    </TabsTrigger>
                    <TabsTrigger value="advanced" className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none">
                      <Database className="mr-2 h-4 w-4" />
                      {t("settings.tabs.advanced")}
                    </TabsTrigger>
                     <TabsTrigger value="notifications" className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none">
                       <Bell className="mr-2 h-4 w-4" />
                       {t("settings.tabs.notifications")}
                     </TabsTrigger>
                     <TabsTrigger value="routing" className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none">
                       <GitMerge className="mr-2 h-4 w-4" />
                       {t("settings.tabs.routing")}
                     </TabsTrigger>
                     <TabsTrigger value="security" className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none">
                       <Shield className="mr-2 h-4 w-4" />
                       {t("settings.tabs.security")}
                     </TabsTrigger>
                     <TabsTrigger value="backup" className="inline-flex items-center justify-center whitespace-nowrap rounded-none border-b-2 border-b-transparent px-4 py-2 text-sm font-medium ring-offset-background transition-all data-[state=active]:border-b-primary data-[state=active]:text-foreground data-[state=active]:shadow-none">
                       <Archive className="mr-2 h-4 w-4" />
                       {t("settings.tabs.backup")}
                     </TabsTrigger>
                   </TabsList>
                 </div>
               </div>

              <div className="mt-6">
                <GeneralSettingsSection
                  form={form}
                  errors={errors}
                  expandedAlerts={expandedAlerts}
                  toggleAlert={toggleAlert}
                  isDiscovering={isDiscovering}
                  showDiscoverResults={showDiscoverResults}
                  discoveredServers={discoveredServers}
                  onDiscover={handleDiscover}
                  onAddServer={addServer}
                  onAddDiscoveredServer={addDiscoveredServer}
                  onDismissDiscovery={() => setShowDiscoverResults(false)}
                  onRemoveServer={removeServer}
                />
                <AlertsSettingsSection form={form} dvrHelpers={dvrHelpers} expandedAlerts={expandedAlerts} toggleAlert={toggleAlert} />
                <AdvancedSettingsSection form={form} dvrHelpers={dvrHelpers} expandedAlerts={expandedAlerts} toggleAlert={toggleAlert} />
                <NotificationsSettingsSection
                  form={form}
                  dvrHelpers={dvrHelpers}
                  webhookEntries={webhookEntries}
                  visibleCredentials={visibleCredentials}
                  setVisibleCredentials={setVisibleCredentials}
                  enabledProviders={enabledProviders}
                  setEnabledProviders={setEnabledProviders}
                  addWebhookEntry={addWebhookEntry}
                  updateWebhookEntry={updateWebhookEntry}
                  removeWebhookEntry={removeWebhookEntry}
                />
                <RoutingSettingsSection form={form} />
                 <SecuritySettingsSection form={form} />
                 <BackupSettingsSection />
              </div>
            </Tabs>
          </div>
        </form>
      </div>

      <div className="fixed bottom-0 left-0 right-0 bg-background border-t border-border p-1 sm:p-3 flex justify-center z-10">
        <div className="container max-w-5xl mx-auto flex justify-end gap-1 sm:gap-3">
          {isDirty && (
            <Badge variant="outline" className="bg-amber-50 text-amber-700 dark:bg-amber-900 dark:text-amber-300 border-amber-200 dark:border-amber-800 text-[10px] sm:text-xs self-center mr-1 sm:mr-2">
              {t("common.unsavedChanges")}
            </Badge>
          )}
          <Button type="button" variant="outline" onClick={() => { if (lastFetchedRef.current) reset(lastFetchedRef.current); else reset() }} disabled={!isDirty} size="sm" className="h-7 sm:h-10 px-2 sm:px-4 text-xs sm:text-sm">
            <RefreshCw className="mr-1 sm:mr-2 h-3 w-3 sm:h-4 sm:w-4" />
            {t("common.discard")}
          </Button>
          <Button onClick={handleSubmit(onSubmit)} disabled={isSubmitting || !isDirty} size="sm" className="h-7 sm:h-10 px-2 sm:px-4 text-xs sm:text-sm">
            {isSubmitting ? (
              <>
                <Loader2 className="mr-1 sm:mr-2 h-3 w-3 sm:h-4 sm:w-4 animate-spin" />
                {t("common.saving")}
              </>
            ) : (
              <>
                <Save className="mr-1 sm:mr-2 h-3 w-3 sm:h-4 sm:w-4" />
                {t("common.save")}
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
