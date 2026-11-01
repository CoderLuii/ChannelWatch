"use client"

import { useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { AlertCircle, Archive, Bell, Database, DownloadCloud, GitMerge, Loader2, RefreshCw, Save, Server, Shield, SlidersHorizontal } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Button } from "@/components/base/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/base/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { useToast } from "@/hooks/use-toast"
import { ToastAction } from "@/components/base/toast"
import { PageHeader, pageTitleClassName } from "@/components/base/page-header"
import { cn } from "@/lib/utils"
import type { AppSettings } from "@/lib/types"
import { SESSION_RESTORED_EVENT, discoverServers, failureKind, fetchSettings, saveSettings, signalRestart, type DiscoveredServer } from "@/lib/api"
import { describeError, formatErrorWithRemediation } from "@/lib/error-catalog"
import { canonicalDvrId } from "@/lib/dvr-id"
import { t } from "@/lib/i18n"

import { emptyWebhookEntry, normalizeDiskAdvancedSettings } from "@/components/settings/constants"
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
const SAVE_RESULT_MS = 1600

function providersFrom(settings: AppSettings) {
  return {
    pushover: !!settings.apprise_pushover,
    discord: !!settings.apprise_discord,
    telegram: !!settings.apprise_telegram,
    email: !!(settings.apprise_email || settings.apprise_email_to),
    slack: !!settings.apprise_slack,
    gotify: !!settings.apprise_gotify,
    matrix: !!settings.apprise_matrix,
    custom: !!settings.apprise_custom,
  }
}

function normalizeLoadedSettings(data: AppSettings): AppSettings {
  return {
    ...data,
    webhooks: Array.isArray(data.webhooks) ? data.webhooks : [],
    trusted_notification_destinations: Array.isArray(data.trusted_notification_destinations) ? data.trusted_notification_destinations : [],
  }
}

interface SettingsFormProps {
  settings?: AppSettings | null
  onSettingsSaved?: (settings: AppSettings) => void
  activeTab?: string
  onTabChange: (tab: string) => void
  /** False while the form is kept mounted behind another view, so unsaved edits survive the trip. */
  active?: boolean
}

export function SettingsForm({ settings: initialSettings, onSettingsSaved, activeTab = "general", onTabChange, active = true }: SettingsFormProps) {
  const [isLoading, setIsLoading] = useState(true)
  const [hasLoadedSettings, setHasLoadedSettings] = useState(false)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [loadOutage, setLoadOutage] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [justSaved, setJustSaved] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null)

  useEffect(() => {
    if (!justSaved) return
    const timer = window.setTimeout(() => setJustSaved(false), SAVE_RESULT_MS)
    return () => window.clearTimeout(timer)
  }, [justSaved])
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
  const allowUnloadRef = useRef(false)
  const settingsTabsRef = useRef<HTMLDivElement | null>(null)
  const submitInFlightRef = useRef(false)
  const saveBarRef = useRef<HTMLDivElement | null>(null)

  const { toast } = useToast()

  const form = useForm<AppSettings>({ mode: "onChange" })
  const { handleSubmit, reset, setValue, getValues, watch } = form
  const {
    errors,
    isDirty,
  } = form.formState

  // The bar stays on screen for the result, then slides away. Space for it is only given back after that.
  const saveBarWanted = hasLoadedSettings && (isDirty || isSubmitting || justSaved)
  const [saveBarShown, setSaveBarShown] = useState(false)
  if (saveBarWanted && !saveBarShown) setSaveBarShown(true)
  // A new edit wins: the result text gives way at once.
  if (justSaved && isDirty) setJustSaved(false)
  const saveBarLeaving = saveBarShown && !saveBarWanted
  // Nothing renders to finish the slide while the form is out of view.
  if (!active && saveBarLeaving) setSaveBarShown(false)
  const saveBarVisible = saveBarShown && active
  const lastSaveResult = useRef(false)
  if (saveBarWanted) lastSaveResult.current = justSaved && !isDirty && !isSubmitting
  // The bar keeps what it was saying while it slides away.
  const showSaveResult = lastSaveResult.current

  useEffect(() => {
    if (!isDirty) return
    const warn = (event: BeforeUnloadEvent) => {
      if (!allowUnloadRef.current) event.preventDefault()
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [isDirty])

  useEffect(() => {
    const root = document.documentElement.style
    const saveBar = saveBarRef.current
    if (!saveBarVisible || !saveBar) {
      root.setProperty("--settings-save-bar-height", "0px")
      return () => root.removeProperty("--settings-save-bar-height")
    }
    const updateHeight = () => root.setProperty("--settings-save-bar-height", `${saveBar.getBoundingClientRect().height}px`)
    updateHeight()
    const observer = new ResizeObserver(updateHeight)
    observer.observe(saveBar)
    return () => {
      observer.disconnect()
      root.removeProperty("--settings-save-bar-height")
    }
  }, [saveBarVisible])

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
    if (activeId !== "global" && !servers.find((server) => server.id === activeId && !server.deleted_at)) return "global"
    return activeId
  }

  const getDvrServerIndex = (cardId: string) => {
    const servers = watch("dvr_servers") || []
    const activeId = getDvrTab(cardId)
    return servers.findIndex((server) => server.id === activeId && !server.deleted_at)
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
    // Archived DVRs keep their entry until purge but take no per-DVR overrides.
    servers: (watch("dvr_servers") || []).filter((server) => !server.deleted_at),
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
    // A placeholder until the host is known, so two new rows never share an id. Saving sets the real one.
    const id = canonicalDvrId(`unsaved-${Date.now()}-${current.length}`, port)
    setValue("dvr_servers", [...current, { id, name: "", host, port, enabled: true }], { shouldDirty: true })
  }

  const addDiscoveredServer = (server: DiscoveredServer, index: number) => {
    const current = getValues("dvr_servers") || []
    const id = canonicalDvrId(server.host, server.port)
    setValue("dvr_servers", [...current, { id, name: server.name || "", host: server.host, port: server.port, enabled: true }], { shouldDirty: true })
    setDiscoveredServers((prev) => prev.filter((_, prevIndex) => prevIndex !== index))
  }

  // A saved DVR is archived (kept for 30 days and restorable) instead of being
  // dropped from config. Unsaved rows are simply removed. Both can be undone.
  const removeServer = (index: number) => {
    const current = getValues("dvr_servers") || []
    const target = current[index]
    if (!target) return
    const wasSaved = (lastFetchedRef.current?.dvr_servers || []).some(
      (saved) => saved.id === target.id && !saved.deleted_at,
    )
    const next = wasSaved
      ? current.map((server, serverIndex) => serverIndex === index ? { ...server, deleted_at: new Date().toISOString() } : server)
      : current.filter((_, serverIndex) => serverIndex !== index)
    setValue("dvr_servers", next, { shouldDirty: true })
    const label = target.name || target.host || t("settings.general.serverN", { n: current.slice(0, index + 1).filter((server) => !server.deleted_at).length })
    toast({
      title: t("settings.general.removedTitle", { name: label }),
      description: wasSaved ? t("settings.general.removedArchivedDesc") : t("settings.general.removedUnsavedDesc"),
      action: (
        <ToastAction altText={t("settings.general.undoRemove")} onClick={() => setValue("dvr_servers", current, { shouldDirty: true })}>
          {t("settings.general.undoRemove")}
        </ToastAction>
      ),
    })
  }

  useEffect(() => {
    const loadSettings = async () => {
      try {
        setIsLoading(true)
        setError(null)
        const data = initialSettings ?? await fetchSettings()
        const normalizedData = normalizeLoadedSettings(data)

        // A background reload, such as after a restart, must not throw away edits in progress.
        if (form.formState.isDirty && lastFetchedRef.current) {
          lastFetchedRef.current = { ...normalizedData }
          setIsLoading(false)
          return
        }

        setEnabledProviders(providersFrom(normalizedData))

        reset(normalizedData)
        lastFetchedRef.current = { ...normalizedData }
        setHasLoadedSettings(true)
        setIsLoading(false)
      } catch (err) {
        setError(describeError(err, t("settings.loadError")).message)
        setLoadOutage(failureKind(err) === "unreachable")
        setHasLoadedSettings(false)
        setIsLoading(false)
      }
    }

    loadSettings()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSettings, loadAttempt, reset])

  // Whatever went wrong with the old session is no longer the problem.
  useEffect(() => {
    const clear = () => setError(null)
    window.addEventListener(SESSION_RESTORED_EVENT, clear)
    return () => window.removeEventListener(SESSION_RESTORED_EVENT, clear)
  }, [])

  const discardChanges = () => {
    const snapshot = getValues()
    const providersSnapshot = enabledProviders
    setError(null)
    if (lastFetchedRef.current) {
      reset(lastFetchedRef.current)
      // A provider left switched off here would clear its saved credential on the next save.
      setEnabledProviders(providersFrom(lastFetchedRef.current))
    } else reset()
    toast({
      title: t("settings.discarded"),
      action: (
        <ToastAction
          altText={t("settings.undoDiscard")}
          onClick={() => {
            reset(snapshot, { keepDefaultValues: true })
            setEnabledProviders(providersSnapshot)
          }}
        >
          {t("settings.undoDiscard")}
        </ToastAction>
      ),
    })
  }

  // A restore replaces what is on the server, so what this form holds is out of date.
  const handleRestored = async () => {
    try {
      const restored = normalizeLoadedSettings(await fetchSettings())
      reset(restored)
      lastFetchedRef.current = { ...restored }
      setEnabledProviders(providersFrom(restored))
      if (onSettingsSaved) onSettingsSaved(restored)
    } catch {
      // The form can no longer be trusted, so its edits are not worth a prompt.
      allowUnloadRef.current = true
      window.location.reload()
    }
  }

  const onSubmit = async (data: AppSettings) => {
    // Enter in a field that belongs to something else must not report a save that changed nothing.
    if (submitInFlightRef.current || !isDirty) return
    submitInFlightRef.current = true
    const savedIds = new Set((lastFetchedRef.current?.dvr_servers || []).map((server) => server.id))
    const blankRow = (data.dvr_servers || []).findIndex((server) => !server.deleted_at && !server.host?.trim())
    if (blankRow >= 0) {
      submitInFlightRef.current = false
      const rowNumber = (data.dvr_servers || []).slice(0, blankRow + 1).filter((server) => !server.deleted_at).length
      const description = t("settings.general.hostRequired", { name: data.dvr_servers?.[blankRow]?.name || t("settings.general.serverN", { n: rowNumber }) })
      setError(description)
      toast({ variant: "destructive", title: t("settings.saveError"), description })
      return
    }
    // Saving would quietly switch this provider off again, with no hint why. A saved credential counts as details.
    const saved = lastFetchedRef.current
    const emptyProvider = (Object.keys(enabledProviders) as (keyof typeof enabledProviders)[]).find((provider) => {
      if (!enabledProviders[provider]) return false
      return provider === "email"
        ? !data.apprise_email && !data.apprise_email_to && !saved?.apprise_email && !saved?.apprise_email_to
        : !data[`apprise_${provider}`] && !saved?.[`apprise_${provider}`]
    })
    if (emptyProvider) {
      submitInFlightRef.current = false
      const description = t("notifications.providerEmpty", { name: t(`provider.${emptyProvider}.name`) })
      setError(description)
      toast({ variant: "destructive", title: t("settings.saveError"), description })
      return
    }
    const activeAddresses = new Set<string>()
    for (const server of data.dvr_servers || []) {
      if (server.deleted_at) continue
      const address = `${server.host?.trim().toLowerCase()}:${server.port || 8089}`
      if (activeAddresses.has(address)) {
        submitInFlightRef.current = false
        const description = t("settings.general.duplicateAddress", { address })
        setError(description)
        toast({ variant: "destructive", title: t("settings.saveError"), description })
        return
      }
      activeAddresses.add(address)
    }
    // A new row takes the id its address gives it, unless a saved or archived row already holds that id.
    const takenIds = new Set((data.dvr_servers || []).filter((server) => savedIds.has(server.id)).map((server) => server.id))
    const newIds = new Map<string, string>()
    const dvrServers = (data.dvr_servers || []).map((server) => {
      const port = server.port || 8089
      const host = server.host?.trim() ?? ""
      if (savedIds.has(server.id)) return { ...server, host, port }
      let id = canonicalDvrId(host, port)
      for (let attempt = 2; takenIds.has(id); attempt += 1) id = canonicalDvrId(`${host}#${attempt}`, port)
      takenIds.add(id)
      newIds.set(server.id, id)
      return { ...server, host, port, id }
    })
    // Routing set on a row before its first save is keyed by the placeholder id.
    const notificationRouting = Object.fromEntries(
      Object.entries(data.notification_routing ?? {}).map(([dvrId, routing]) => [newIds.get(dvrId) ?? dvrId, routing]),
    )
    const diskCheck = normalizeDiskAdvancedSettings({ ...data, dvr_servers: dvrServers, notification_routing: notificationRouting })
    if (diskCheck.error) {
      submitInFlightRef.current = false
      setError(diskCheck.error)
      const { errorDvrId } = diskCheck
      if (errorDvrId) setDvrTab((prev) => ({ ...prev, disk: errorDvrId }))
      toast({
        variant: "destructive",
        title: t("settings.saveError"),
        description: diskCheck.error,
      })
      return
    }
    const submittedData = { ...diskCheck.settings }

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
      const saveResult = await saveSettings(submittedData)

      let refreshedData = data
      try {
        refreshedData = await fetchSettings()
      } catch {
        toast({
          title: t("settings.savedRefreshFailed"),
          description: t("settings.savedRefreshFailedDesc"),
        })
      }
      const normalizedRefreshedData = normalizeLoadedSettings(refreshedData)

      reset(normalizedRefreshedData, {
        keepValues: false,
        keepDirty: false,
        keepIsSubmitted: false,
      })

      lastFetchedRef.current = { ...normalizedRefreshedData }

      setEnabledProviders(providersFrom(normalizedRefreshedData))

      setIsSubmitting(false)
      setJustSaved(true)
      setLastSavedAt(new Date())

      if (onSettingsSaved) onSettingsSaved(normalizedRefreshedData)

      // The backend already hot-reloads core after a save. Only fall back to
      // an explicit core restart when that reload could not be signalled.
      if (saveResult?.applied !== "hot") {
        try {
          await signalRestart()
        } catch (restartErr) {
          toast({
            variant: "destructive",
            title: t("settings.restartFailed"),
            description: formatErrorWithRemediation(restartErr, t("settings.restartFailedDesc")),
          })
        }
      }
    } catch (err) {
      const description = formatErrorWithRemediation(err, t("common.unknownError"))
      setError(description)
      toast({
        variant: "destructive",
        title: t("settings.saveError"),
        description,
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
          <div className="h-8 w-40 bg-muted rounded-thumb mb-2" />
          <div className="h-4 w-72 bg-muted rounded-thumb" />
        </div>
        <div className="flex gap-4 border-b pb-2">
          <div className="h-8 w-20 bg-muted rounded-thumb" />
          <div className="h-8 w-16 bg-muted rounded-thumb" />
          <div className="h-8 w-24 bg-muted rounded-thumb" />
          <div className="h-8 w-28 bg-muted rounded-thumb" />
        </div>
        <div className="rounded-group border border-hairline p-4 space-y-6">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-full bg-muted" />
            <div>
              <div className="h-5 w-32 bg-muted rounded-thumb mb-1" />
              <div className="h-3 w-56 bg-muted rounded-thumb" />
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (!hasLoadedSettings) {
    return (
      <div className="mx-auto w-full max-w-5xl space-y-6">
        <PageHeader title={t("settings.title")} />
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>{t("settings.temporarilyUnavailable")}</AlertTitle>
          <AlertDescription className="space-y-3">
            {loadOutage ? <p>{t("settings.temporarilyUnavailableDesc")}</p> : null}
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
    <div className="w-full max-w-5xl [overflow-anchor:none]">
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2 relative">
          <h1 className={pageTitleClassName}>{t("settings.title")}</h1>
          {lastSavedAt ? (
            <p data-testid="settings-last-saved" className="text-xs tabular-nums text-muted-foreground" title={lastSavedAt.toLocaleString()}>
              {t("settings.lastSaved", { time: lastSavedAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) })}
            </p>
          ) : null}

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
                  <SelectTrigger aria-label={t("settings.sectionLabel")} className="h-auto min-h-11 w-full whitespace-normal py-2">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {["general", "alerts", "advanced", "notifications", "routing", "security", "backup", "updates"].map((section) => (
                      <SelectItem key={section} value={section}>{t(`settings.tabs.${section}`)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="hidden rounded-control bg-fill p-1 md:block">
                <div
                  ref={settingsTabsRef}
                  className="flex overflow-x-auto overscroll-x-contain scroll-smooth [scrollbar-width:none] [&::-webkit-scrollbar]:hidden motion-reduce:scroll-auto"
                >
                  <TabsList aria-label={t("settings.title")} className="grid h-auto min-w-full grid-cols-4 items-center gap-1 rounded-none bg-transparent p-0 xl:inline-flex xl:justify-start">
                    <TabsTrigger value="general" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
                      <Server className="mr-2 h-4 w-4" />
                      {t("settings.tabs.general")}
                    </TabsTrigger>
                    <TabsTrigger value="alerts" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
                      <SlidersHorizontal className="mr-2 h-4 w-4" />
                      {t("settings.tabs.alerts")}
                    </TabsTrigger>
                    <TabsTrigger value="advanced" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
                      <Database className="mr-2 h-4 w-4" />
                      {t("settings.tabs.advanced")}
                    </TabsTrigger>
                     <TabsTrigger value="notifications" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
                       <Bell className="mr-2 h-4 w-4" />
                       {t("settings.tabs.notifications")}
                     </TabsTrigger>
                     <TabsTrigger value="routing" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
                       <GitMerge className="mr-2 h-4 w-4" />
                       {t("settings.tabs.routing")}
                     </TabsTrigger>
                     <TabsTrigger value="security" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
                       <Shield className="mr-2 h-4 w-4" />
                       {t("settings.tabs.security")}
                     </TabsTrigger>
                     <TabsTrigger value="backup" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
                       <Archive className="mr-2 h-4 w-4" />
                       {t("settings.tabs.backup")}
                     </TabsTrigger>
                     <TabsTrigger value="updates" className="h-auto min-h-11 min-w-0 whitespace-normal px-4 py-2 font-medium">
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
                <RoutingSettingsSection form={form} onGoToNotifications={() => onTabChange("notifications")} />
                 <SecuritySettingsSection form={form} />
                 <BackupSettingsSection onRestored={handleRestored} hasUnsavedChanges={isDirty} />
                 <UpdateCenterSection active={active} />
              </div>
            </Tabs>
          </div>
        </form>
      </div>

      {saveBarVisible ? (
        <div
          ref={saveBarRef}
          data-testid="settings-save-bar"
          data-leaving={saveBarLeaving ? "" : undefined}
          onAnimationEnd={(event) => {
            if (saveBarLeaving && event.target === event.currentTarget) setSaveBarShown(false)
          }}
          className="save-bar right-scroll-bar-position fixed bottom-[var(--mobile-navigation-height,calc(3.8125rem+max(0.25rem,env(safe-area-inset-bottom))))] left-0 right-0 z-10 border-t border-hairline-strong bg-raised pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)] md:bottom-0 md:pb-[env(safe-area-inset-bottom)] md:pl-[calc(var(--app-sidebar-width,68px)+2rem)] md:pr-[calc(2rem+env(safe-area-inset-right))]"
        >
          <div className="mx-auto flex min-h-14 w-full max-w-5xl flex-wrap items-center justify-end gap-x-3 gap-y-2 px-4 py-1.5 sm:px-6 md:px-0">
            <div role="status" aria-live="polite" className="flex min-w-0 flex-1 basis-24 items-center gap-2 text-xs text-muted-foreground">
              {showSaveResult ? (
                <>
                  <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-success" />
                  <span className="min-w-0 break-words text-foreground">{t("settings.savedSection", { section: t(`settings.tabs.${activeTab}`) })}</span>
                </>
              ) : isDirty || saveBarLeaving ? (
                <span className="min-w-0 break-words">{t("common.unsavedChanges")}</span>
              ) : null}
            </div>
            {showSaveResult ? null : (
              <div className="flex shrink-0 items-center gap-2">
                <Button type="button" variant="ghost" onClick={discardChanges} disabled={!isDirty || isSubmitting} size="sm" className="px-3 text-sm">
                  {t("common.discard")}
                </Button>
                <Button onClick={handleSubmit(onSubmit)} disabled={!hasLoadedSettings || isSubmitting || !isDirty} size="sm" className="px-4 text-sm sm:min-w-[9rem]">
                  {isSubmitting ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {t("common.saving")}
                    </>
                  ) : (
                    <>
                      <Save className="h-4 w-4" />
                      {t("common.save")}
                    </>
                  )}
                </Button>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}
