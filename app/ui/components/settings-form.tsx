"use client"

import { useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { AlertCircle, Archive, Bell, Check, Database, DownloadCloud, GitMerge, Loader2, RefreshCw, Save, Server, Shield } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/base/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
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
import { UpdateCenterSection } from "@/components/settings/update-center-section"
import type { AppSettingsFieldKey, DvrHelpers } from "@/components/settings/dvr-field-controls"
import type { FieldPathValue } from "react-hook-form"

const MASKED_SENTINEL = "****"

interface SettingsFormProps {
  settings?: AppSettings | null
  onSettingsSaved?: (settings: AppSettings) => void
  activeTab?: string
  onTabChange: (tab: string) => void
}

export function SettingsForm({ settings: initialSettings, onSettingsSaved, activeTab = "general", onTabChange }: SettingsFormProps) {
  const [isLoading, setIsLoading] = useState(true)
  const [hasLoadedSettings, setHasLoadedSettings] = useState(false)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
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
  const settingsTabsRef = useRef<HTMLDivElement | null>(null)
  const submitInFlightRef = useRef(false)
  const saveBarRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const saveBar = saveBarRef.current
    if (!saveBar) return
    const updateHeight = () => {
      document.documentElement.style.setProperty("--settings-save-bar-height", `${saveBar.getBoundingClientRect().height}px`)
    }
    updateHeight()
    const observer = new ResizeObserver(updateHeight)
    observer.observe(saveBar)
    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty("--settings-save-bar-height")
    }
  }, [isLoading, hasLoadedSettings])
  const { toast } = useToast()

  const form = useForm<AppSettings>({ mode: "onChange" })
  const { handleSubmit, reset, setValue, getValues, watch } = form
  const {
    errors,
    isDirty,
  } = form.formState

  const webhookEntries = watch("webhooks") || []

  useEffect(() => {
    const activeTrigger = settingsTabsRef.current?.querySelector<HTMLElement>('[data-state="active"]')
    activeTrigger?.scrollIntoView({ block: "nearest", inline: "center" })
  }, [activeTab, hasLoadedSettings])

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
    policy: ["alert_channel_watching", "alert_vod_watching", "alert_disk_space", "alert_recording_events", "alert_dvr_health", "rd_alert_scheduled", "rd_alert_started", "rd_alert_completed", "rd_alert_cancelled", "rd_alert_failed", "rd_alert_skipped", "rd_alert_missed", "rd_alert_interrupted", "dvr_alert_unreachable", "dvr_alert_recovered"],
    health: ["alert_dvr_health", "dvr_alert_unreachable", "dvr_alert_recovered", "dvr_health_alert_delay_seconds"],
    cw: ["alert_channel_watching", "cw_image_source", "cw_channel_name", "cw_channel_number", "cw_program_name", "cw_device_name", "cw_device_ip", "cw_stream_source"],
    vod: ["alert_vod_watching", "vod_title", "vod_episode_title", "vod_summary", "vod_duration", "vod_progress", "vod_image", "vod_rating", "vod_genres", "vod_cast", "vod_device_name", "vod_device_ip"],
    rec: ["alert_recording_events", "rd_alert_scheduled", "rd_alert_started", "rd_alert_completed", "rd_alert_cancelled", "rd_alert_failed", "rd_alert_skipped", "rd_alert_missed", "rd_alert_interrupted", "rd_program_name", "rd_image", "rd_program_desc", "rd_duration", "rd_channel_name", "rd_channel_number", "rd_type"],
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
        setHasLoadedSettings(true)
        setIsLoading(false)
      } catch (err) {
        setError(err instanceof Error ? err.message : t("settings.loadError"))
        setHasLoadedSettings(false)
        setIsLoading(false)
      }
    }

    loadSettings()
  }, [initialSettings, loadAttempt, reset])

  const onSubmit = async (data: AppSettings) => {
    if (submitInFlightRef.current) return
    submitInFlightRef.current = true
    const submittedData = { ...data }

    const APPRISE_SENSITIVE_FIELDS = [
      "apprise_pushover", "apprise_discord", "apprise_email",
      "apprise_email_to", "apprise_telegram", "apprise_slack",
      "apprise_gotify", "apprise_matrix", "apprise_custom",
    ] as const

    const clearFields: (typeof APPRISE_SENSITIVE_FIELDS)[number][] = []
    if (!enabledProviders.pushover && lastFetchedRef.current?.apprise_pushover) clearFields.push("apprise_pushover")
    if (!enabledProviders.discord && lastFetchedRef.current?.apprise_discord) clearFields.push("apprise_discord")
    if (!enabledProviders.email && (lastFetchedRef.current?.apprise_email || lastFetchedRef.current?.apprise_email_to)) {
      clearFields.push("apprise_email")
      clearFields.push("apprise_email_to")
    }
    if (!enabledProviders.telegram && lastFetchedRef.current?.apprise_telegram) clearFields.push("apprise_telegram")
    if (!enabledProviders.slack && lastFetchedRef.current?.apprise_slack) clearFields.push("apprise_slack")
    if (!enabledProviders.gotify && lastFetchedRef.current?.apprise_gotify) clearFields.push("apprise_gotify")
    if (!enabledProviders.matrix && lastFetchedRef.current?.apprise_matrix) clearFields.push("apprise_matrix")
    if (!enabledProviders.custom && lastFetchedRef.current?.apprise_custom) clearFields.push("apprise_custom")
    Object.assign(submittedData, { clear_apprise_fields: clearFields })

    for (const field of APPRISE_SENSITIVE_FIELDS) {
      if ((submittedData as Record<string, unknown>)[field] === MASKED_SENTINEL) {
        (submittedData as Record<string, unknown>)[field] = ""
      }
    }

    try {
      setIsSubmitting(true)
      setError(null)
      await saveSettings(submittedData)

      let refreshedData = data
      try {
        refreshedData = await fetchSettings()
      } catch {
        toast({
          title: t("settings.savedRefreshFailed"),
          description: t("settings.savedRefreshFailedDesc"),
        })
      }
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
    } finally {
      submitInFlightRef.current = false
    }
  }

  if (isLoading) {
    return (
      <div className="animate-pulse space-y-6">
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

  if (!hasLoadedSettings) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-6">
        <div className="space-y-2">
          <h1 className="text-[28px] font-semibold tracking-tight md:text-[32px]">{t("settings.title")}</h1>
          <p className="text-muted-foreground">{t("settings.description")}</p>
        </div>
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>{t("settings.temporarilyUnavailable")}</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>{t("settings.temporarilyUnavailableDesc")}</p>
            {error ? <p className="text-xs">{error}</p> : null}
            <Button type="button" variant="outline" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
              <RefreshCw className="mr-2 h-4 w-4" />
              {t("common.tryAgain")}
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  return (
    <div className="mx-auto w-full max-w-5xl">
      {saveSuccess && (
        <div
          className={`fixed top-[var(--app-header-height,4rem)] left-0 right-0 z-10 ${bannerDismissing ? "animate-slide-to-top" : "animate-slide-from-top"}`}
          onAnimationEnd={() => {
            if (bannerDismissing) {
              setSaveSuccess(false)
              setBannerDismissing(false)
            }
          }}
        >
          <div className="bg-blue-600 shadow-lg shadow-blue-900/20">
            <div className="w-full flex justify-center">
              <div className="py-2 pl-4 pr-16 relative flex items-center w-full justify-center">
                <div className="flex min-w-0 items-center gap-2.5 [overflow-wrap:anywhere]">
                  <div className="bg-blue-500/30 backdrop-blur-sm rounded-full p-1">
                    <Check className="h-3.5 w-3.5 text-blue-100" />
                  </div>
                  <span className="font-medium text-blue-50 text-sm">{t("settings.saved")}</span>
                </div>
                <button onClick={() => setBannerDismissing(true)} className="absolute right-2 flex h-11 w-11 items-center justify-center text-blue-100 hover:text-white transition-colors" aria-label={t("common.dismiss")}>
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
          <h1 className="text-[28px] font-semibold tracking-tight [overflow-wrap:anywhere] md:text-[32px]">{t("settings.title")}</h1>
          <p className="text-base leading-6 text-muted-foreground [overflow-wrap:anywhere]">{t("settings.description")}</p>

          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>{t("common.error")}</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="flex flex-col gap-8 pb-[calc(var(--settings-save-bar-height,5rem)+1rem)]">
            <Tabs value={activeTab} onValueChange={onTabChange} className="w-full">
              <div className="md:hidden">
                <Select value={activeTab} onValueChange={onTabChange}>
                  <SelectTrigger aria-label="Settings section" className="h-auto min-h-11 w-full whitespace-normal py-2">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {["general", "alerts", "advanced", "notifications", "routing", "security", "backup", "updates"].map((section) => (
                      <SelectItem key={section} value={section}>{t(`settings.tabs.${section}`)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="hidden rounded-2xl bg-muted/70 p-1 ring-1 ring-border/60 md:block">
                <div
                  ref={settingsTabsRef}
                  className="flex overflow-x-auto overscroll-x-contain scroll-smooth [scrollbar-width:none] [&::-webkit-scrollbar]:hidden motion-reduce:scroll-auto"
                >
                  <TabsList aria-label={t("settings.title")} className="grid h-auto min-w-full grid-cols-4 items-center gap-1 rounded-none bg-transparent p-0 xl:inline-flex xl:justify-start">
                    <TabsTrigger value="general" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                      <Server className="mr-2 h-4 w-4" />
                      {t("settings.tabs.general")}
                    </TabsTrigger>
                    <TabsTrigger value="alerts" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                      <Bell className="mr-2 h-4 w-4" />
                      {t("settings.tabs.alerts")}
                    </TabsTrigger>
                    <TabsTrigger value="advanced" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                      <Database className="mr-2 h-4 w-4" />
                      {t("settings.tabs.advanced")}
                    </TabsTrigger>
                     <TabsTrigger value="notifications" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                       <Bell className="mr-2 h-4 w-4" />
                       {t("settings.tabs.notifications")}
                     </TabsTrigger>
                     <TabsTrigger value="routing" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                       <GitMerge className="mr-2 h-4 w-4" />
                       {t("settings.tabs.routing")}
                     </TabsTrigger>
                     <TabsTrigger value="security" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                       <Shield className="mr-2 h-4 w-4" />
                       {t("settings.tabs.security")}
                     </TabsTrigger>
                     <TabsTrigger value="backup" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                       <Archive className="mr-2 h-4 w-4" />
                       {t("settings.tabs.backup")}
                     </TabsTrigger>
                     <TabsTrigger value="updates" className="inline-flex h-auto min-h-11 min-w-0 items-center justify-center whitespace-normal rounded-xl px-4 py-2 text-[13px] font-medium transition-all data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
                       <DownloadCloud className="mr-2 h-4 w-4" />
                       {t("settings.tabs.updates")}
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
                 <UpdateCenterSection />
              </div>
            </Tabs>
          </div>
        </form>
      </div>

      <div ref={saveBarRef} className="fixed bottom-[var(--mobile-navigation-height,calc(3.8125rem+max(0.25rem,env(safe-area-inset-bottom))))] left-0 right-0 z-10 flex justify-center border-t border-border bg-background/95 p-2 backdrop-blur-xl sm:p-3 md:bottom-0">
        <div className="container mx-auto flex max-w-5xl flex-wrap justify-end gap-2 sm:flex-nowrap sm:gap-3">
          {isDirty && (
            <Badge variant="outline" className="w-full justify-center border-amber-200 bg-amber-50 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-900 dark:text-amber-300 sm:mr-2 sm:w-auto sm:self-center">
              {t("common.unsavedChanges")}
            </Badge>
          )}
          <Button type="button" variant="outline" onClick={() => { if (lastFetchedRef.current) reset(lastFetchedRef.current); else reset() }} disabled={!isDirty} size="sm" className="h-auto min-h-11 min-w-0 flex-1 whitespace-normal rounded-xl px-3 py-2 text-sm sm:flex-none sm:px-4">
            <RefreshCw className="mr-1 sm:mr-2 h-3 w-3 sm:h-4 sm:w-4" />
            {t("common.discard")}
          </Button>
          <Button onClick={handleSubmit(onSubmit)} disabled={!hasLoadedSettings || isSubmitting || !isDirty} size="sm" className="h-auto min-h-11 min-w-0 flex-1 whitespace-normal rounded-xl px-3 py-2 text-sm sm:flex-none sm:px-4">
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
