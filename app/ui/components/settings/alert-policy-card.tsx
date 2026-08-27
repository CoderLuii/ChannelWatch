"use client"

import { useMemo, useRef, useState } from "react"
import type { LucideIcon } from "lucide-react"
import { BellRing, ChevronDown, SlidersHorizontal } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader } from "@/components/base/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { ALERT_POLICY_KEYS, ALERT_PRESET_VALUES, detectAlertPolicy, summarizeAlertPreset, type AlertPolicyKey, type AlertPreset } from "@/lib/alert-presets"
import { t } from "@/lib/i18n"
import type { AppSettings } from "@/lib/types"
import { cn } from "@/lib/utils"

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
  return (
    <button
      type="button"
      className="flex min-h-11 w-full items-center justify-between gap-3 p-4 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      aria-expanded={expanded}
      aria-controls={contentId}
      onClick={onToggle}
    >
      <span className="flex min-w-0 items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-500/20">
          <Icon className="h-5 w-5 text-blue-500" aria-hidden="true" />
        </span>
        <span className="min-w-0">
          <span className="block text-base font-medium">{title}</span>
          <span className="block text-sm text-muted-foreground">{description}</span>
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <Badge variant="outline" className="max-w-28 truncate text-xs font-normal">
          {summary}
        </Badge>
        <ChevronDown
          className={cn("h-5 w-5 text-muted-foreground transition-transform motion-reduce:transition-none", expanded && "rotate-180")}
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
  const selectRef = useRef<HTMLButtonElement>(null)
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
    <Card className="order-1 overflow-hidden border-blue-400/20">
      <CardHeader className="space-y-3 border-b bg-muted/20 p-4 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-500/20">
              <SlidersHorizontal className="h-5 w-5 text-blue-500" aria-hidden="true" />
            </span>
            <div>
              <h2 className="text-base font-semibold leading-none tracking-tight">{t("alerts.policy.title")}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{t("alerts.policy.description")}</p>
            </div>
          </div>
          <Badge variant={detectedPolicy === "custom" ? "secondary" : "outline"}>
            {t(`alerts.policy.preset.${detectedPolicy}`)}
          </Badge>
        </div>
        <DvrTabBar cardId="policy" helpers={dvrHelpers} />
      </CardHeader>
      <CardContent className="space-y-4 p-4 sm:p-6">
        {showUpgradeNotice ? (
          <Alert className="border-blue-500/30 bg-blue-500/5">
            <BellRing className="h-4 w-4 text-blue-500" />
            <AlertTitle>{t("alerts.policy.upgradeTitle")}</AlertTitle>
            <AlertDescription className="space-y-3">
              <p>{t("alerts.policy.upgradeDescription")}</p>
              <div className="flex flex-wrap gap-2">
                <Button type="button" size="sm" variant="outline" onClick={() => selectRef.current?.focus()}>
                  {t("alerts.policy.reviewPresets")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setValue("notification_preferences_version", 1, { shouldDirty: true })}
                >
                  {t("alerts.policy.keepCurrent")}
                </Button>
              </div>
              {reviewFieldDirty ? <p role="status">{t("alerts.policy.saveAcknowledgement")}</p> : null}
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <div className="space-y-2">
            <label htmlFor="alert-policy-preset" className="text-sm font-medium">
              {t("alerts.policy.presetLabel")}
            </label>
            <Select
              value={selectedPreset}
              onValueChange={(value) => setSelectedPresets((current) => ({
                ...current,
                [activeScope]: value as AlertPreset,
              }))}
            >
              <SelectTrigger id="alert-policy-preset" ref={selectRef} className="min-h-11">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRESETS.map((preset) => (
                  <SelectItem key={preset} value={preset}>{t(`alerts.policy.preset.${preset}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">{t(`alerts.policy.presetDescription.${selectedPreset}`)}</p>
          </div>
          <Button type="button" className="min-h-11" onClick={applyPreset}>
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
