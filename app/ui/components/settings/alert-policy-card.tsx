"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import type { LucideIcon } from "lucide-react"
import { BellRing, ChevronDown, SlidersHorizontal } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Tag } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/base/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { ALERT_POLICY_KEYS, ALERT_PRESET_VALUES, detectAlertPolicy, summarizeAlertPreset, type AlertPolicyKey, type AlertPreset } from "@/lib/alert-presets"
import { t } from "@/lib/i18n"
import type { AppSettings } from "@/lib/types"
import { cn, revealDisclosure } from "@/lib/utils"

import { DvrTabBar, type DvrHelpers } from "./dvr-field-controls"

const PRESETS: AlertPreset[] = ["monitor_only", "important_only", "balanced", "everything"]

interface AlertDisclosureHeaderProps {
  cardId: string
  icon: LucideIcon
  title: string
  description: string
  summary: string
  expanded: boolean
  onToggle: () => void
}

export function AlertDisclosureHeader({
  cardId,
  icon: Icon,
  title,
  description,
  summary,
  expanded,
  onToggle,
}: AlertDisclosureHeaderProps) {
  const contentId = `alert-section-${cardId}`
  const buttonRef = useRef<HTMLButtonElement>(null)
  const wasExpanded = useRef(expanded)
  useEffect(() => {
    if (expanded && !wasExpanded.current) revealDisclosure(buttonRef.current?.parentElement)
    wasExpanded.current = expanded
  }, [expanded])
  return (
    <button
      ref={buttonRef}
      type="button"
      className="press-row flex min-h-[60px] w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-fill focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      aria-expanded={expanded}
      aria-controls={contentId}
      onClick={onToggle}
    >
      <span className="flex min-w-0 flex-1 items-center gap-2">
        <Icon className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0">
          <span className="block text-base font-medium">{title}</span>
          <span className="block text-sm text-muted-foreground">{description}</span>
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <Tag className="max-w-40 truncate">{summary}</Tag>
        <ChevronDown
          className={cn("h-5 w-5 text-muted-foreground transition-transform duration-[var(--motion-base)] ease-out motion-reduce:transition-none", expanded && "rotate-180")}
          aria-hidden="true"
        />
      </span>
    </button>
  )
}

interface AlertPolicyCardProps {
  form: UseFormReturn<AppSettings>
  dvrHelpers: DvrHelpers
}

export function AlertPolicyCard({ form, dvrHelpers }: AlertPolicyCardProps) {
  const [presetOpen, setPresetOpen] = useState(false)
  const { setValue, watch, formState } = form
  const activeScope = dvrHelpers.getDvrTab("policy")
  const effectiveValues = Object.fromEntries(
    ALERT_POLICY_KEYS.map((key) => [key, Boolean(dvrHelpers.dvrFieldValue("policy", key).value)]),
  ) as Record<AlertPolicyKey, boolean>
  const detectedPolicy = detectAlertPolicy(effectiveValues)
  const fallbackPreset = detectedPolicy === "custom" ? "important_only" : detectedPolicy
  const [selectedPresets, setSelectedPresets] = useState<Record<string, AlertPreset>>({})
  const selectedPreset = selectedPresets[activeScope] ?? fallbackPreset
  const [changeSummary, setChangeSummary] = useState<string | null>(null)
  const preferencesVersion = watch("notification_preferences_version") ?? 0
  const reviewFieldDirty = Boolean(formState.dirtyFields.notification_preferences_version)
  const showUpgradeNotice = preferencesVersion < 1 || reviewFieldDirty

  const overrideCount = useMemo(
    () => dvrHelpers.servers.filter(
      (server) => ALERT_POLICY_KEYS.some(
        (key) => server.overrides?.[key] !== undefined,
      ),
    ).length,
    [dvrHelpers.servers],
  )

  const activeDvr = activeScope === "global"
    ? null
    : dvrHelpers.servers.find((server) => server.id === activeScope) ?? null

  const applyPreset = () => {
    const values = ALERT_PRESET_VALUES[selectedPreset]
    for (const key of ALERT_POLICY_KEYS) {
      if (activeScope === "global") {
        setValue(key, values[key], { shouldDirty: true, shouldValidate: true })
      } else {
        dvrHelpers.dvrFieldSet("policy", key, values[key])
      }
    }
    setValue("notification_preferences_version", 1, { shouldDirty: true })
    const summary = summarizeAlertPreset(selectedPreset)
    setChangeSummary(t("alerts.policy.changeSummary", summary))
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="space-y-3 border-b border-hairline">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <SlidersHorizontal className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div>
              <h2 className="text-base font-semibold leading-snug tracking-[-0.012em]">{t("alerts.policy.title")}</h2>
              <CardDescription>{t("alerts.policy.description")}</CardDescription>
            </div>
          </div>
          <Tag>{t(`alerts.policy.preset.${detectedPolicy}`)}</Tag>
        </div>
        <DvrTabBar cardId="policy" helpers={dvrHelpers} />
      </CardHeader>
      <CardContent className="space-y-4 pt-4">
        {showUpgradeNotice ? (
          <Alert className="border-hairline">
            <BellRing className="h-4 w-4 text-muted-foreground" />
            <AlertTitle>{t("alerts.policy.upgradeTitle")}</AlertTitle>
            <AlertDescription className="space-y-3">
              <p>{t("alerts.policy.upgradeDescription")}</p>
              <div className="flex flex-wrap gap-2">
                <Button type="button" size="sm" variant="outline" className="h-auto min-h-11 max-w-full whitespace-normal py-2 text-center" onClick={() => setPresetOpen(true)}>
                  {t("alerts.policy.reviewPresets")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-auto min-h-11 max-w-full whitespace-normal py-2 text-center"
                  onClick={() => setValue("notification_preferences_version", 1, { shouldDirty: true })}
                >
                  {t("alerts.policy.keepCurrent")}
                </Button>
              </div>
              {reviewFieldDirty ? <p role="status">{t("alerts.policy.saveAcknowledgement")}</p> : null}
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-x-4 gap-y-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
          <label htmlFor="alert-policy-preset" className="text-sm font-medium sm:col-span-2">
            {t("alerts.policy.presetLabel")}
          </label>
          <div className="min-w-0">
            <Select
              open={presetOpen}
              onOpenChange={setPresetOpen}
              value={selectedPreset}
              onValueChange={(value) => setSelectedPresets((current) => ({
                ...current,
                [activeScope]: value as AlertPreset,
              }))}
            >
              <SelectTrigger id="alert-policy-preset" className="min-h-11">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRESETS.map((preset) => (
                  <SelectItem key={preset} value={preset}>{t(`alerts.policy.preset.${preset}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <p className="text-sm text-muted-foreground sm:order-last sm:col-span-2">{t(`alerts.policy.presetDescription.${selectedPreset}`)}</p>
          <Button type="button" className="mt-2 h-auto min-h-11 max-w-full whitespace-normal py-2 text-center sm:mt-0" onClick={applyPreset}>
            {t("alerts.policy.apply")}
          </Button>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-4 text-sm text-muted-foreground">
          <span>
            {activeDvr
              ? t("alerts.policy.scopeDvr", { name: activeDvr.name || activeDvr.host })
              : t("alerts.policy.scopeGlobal")}
          </span>
          {activeScope === "global" && overrideCount > 0 ? (
            <span>{t("alerts.policy.overrideCount", { count: overrideCount })}</span>
          ) : null}
        </div>
        {changeSummary ? <p role="status" aria-live="polite" className="text-sm font-medium">{changeSummary}</p> : null}
      </CardContent>
    </Card>
  )
}
