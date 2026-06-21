import type { Dispatch, SetStateAction } from "react"
import type { FieldPath, FieldPathValue, UseFormSetValue, UseFormWatch } from "react-hook-form"
import { Link, PenLine, RotateCcw } from "lucide-react"

import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { Switch } from "@/components/base/switch"
import { cn } from "@/lib/utils"
import { t } from "@/lib/i18n"
import type { AppSettings } from "@/lib/types"

export type AppSettingsFieldKey = Extract<keyof AppSettings, FieldPath<AppSettings>>
type DvrFieldValue<K extends AppSettingsFieldKey> = {
  value: FieldPathValue<AppSettings, K>
  hasOverride: boolean
  isGlobal: boolean
}

export interface DvrHelpers {
  dvrTab: Record<string, string>
  setDvrTab: Dispatch<SetStateAction<Record<string, string>>>
  cardFieldKeys: Record<string, AppSettingsFieldKey[]>
  servers: AppSettings["dvr_servers"]
  getDvrTab: (cardId: string) => string
  dvrFieldValue: <K extends AppSettingsFieldKey>(cardId: string, key: K) => DvrFieldValue<K>
  dvrFieldSet: <K extends AppSettingsFieldKey>(cardId: string, key: K, value: FieldPathValue<AppSettings, K>) => void
  dvrFieldReset: <K extends AppSettingsFieldKey>(cardId: string, key: K) => void
  watch: UseFormWatch<AppSettings>
  setValue: UseFormSetValue<AppSettings>
}

interface DvrTabBarProps {
  cardId: string
  helpers: DvrHelpers
}

export function DvrTabBar({ cardId, helpers }: DvrTabBarProps) {
  const { servers, dvrTab, setDvrTab, cardFieldKeys, getDvrTab } = helpers

  if (servers.length === 0) return null

  const active = getDvrTab(cardId)
  const fieldKeys = cardFieldKeys[cardId] || []

  return (
    <div className="flex gap-1 mb-4 p-1 rounded-lg bg-muted/30 border border-blue-400/10 overflow-x-auto">
      <button
        type="button"
        className={cn(
          "px-3 py-1 rounded-md text-xs font-medium transition-colors shrink-0",
          active === "global"
            ? "bg-blue-600 text-white"
            : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
        )}
        onClick={() => setDvrTab((prev) => ({ ...prev, [cardId]: "global" }))}
      >
        {t("dvr.globalTab")}
      </button>
      {servers.map((server) => {
        const overrideCount = fieldKeys.filter((key) => server.overrides?.[key] !== undefined).length
        return (
          <button
            key={server.id}
            type="button"
            className={cn(
              "px-3 py-1 rounded-md text-xs font-medium transition-colors truncate max-w-[140px] shrink-0",
              active === server.id
                ? "bg-blue-600 text-white"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            )}
            onClick={() => setDvrTab((prev) => ({ ...prev, [cardId]: server.id }))}
          >
            {server.name || server.host || t("dvr.serverFallback")}
            {overrideCount > 0 && <span className="ml-1 opacity-70">({overrideCount})</span>}
          </button>
        )
      })}
    </div>
  )
}

interface DvrFieldProps {
  cardId: string
  fieldKey: string
  label: string
  desc: string
  helpers: DvrHelpers
}

export function DvrFieldToggle({ cardId, fieldKey, label, desc, helpers }: DvrFieldProps) {
  const { dvrFieldValue, setValue, dvrFieldSet, dvrFieldReset } = helpers
  const settingsKey = fieldKey as AppSettingsFieldKey
  const { value, hasOverride, isGlobal } = dvrFieldValue(cardId, settingsKey)
  const showInherited = !isGlobal && !hasOverride
  const showOverridden = !isGlobal && hasOverride

  return (
    <div
      className={cn(
        "flex items-center justify-between p-2.5 rounded-lg transition-colors border-l-2",
        isGlobal && "border-l-transparent border border-blue-400/10 bg-muted/40 hover:bg-blue-500/10",
        showInherited && "border-l-blue-400/30 border border-blue-400/10 bg-muted/20",
        showOverridden && "border-l-amber-400/70 border border-amber-400/20 bg-amber-500/5"
      )}
    >
      <Label htmlFor={`${cardId}_${fieldKey}`} className="cursor-pointer">
        <div className="flex items-center gap-1.5">
          {showInherited && <Link className="h-3.5 w-3.5 text-muted-foreground/50" />}
          {showOverridden && <PenLine className="h-3.5 w-3.5 text-amber-400" />}
          <span className="text-sm">{label}</span>
        </div>
        <span className="text-[11px] text-muted-foreground">{desc}</span>
        {showInherited && <span className="text-[10px] text-blue-400/60 italic block">{t("dvr.usingGlobal")}</span>}
        {showOverridden && <span className="text-[10px] text-amber-400/80 block">{t("dvr.overriddenForDvr")}</span>}
      </Label>
      <div className="flex items-center gap-1.5">
        <Switch
          id={`${cardId}_${fieldKey}`}
          checked={!!value}
          onCheckedChange={(checked) => {
            if (isGlobal) {
              setValue(settingsKey, checked as FieldPathValue<AppSettings, typeof settingsKey>, { shouldDirty: true })
            } else {
              dvrFieldSet(cardId, settingsKey, checked as FieldPathValue<AppSettings, typeof settingsKey>)
            }
          }}
          className={cn("data-[state=checked]:bg-blue-600", showInherited && "opacity-50")}
        />
        {showOverridden && (
          <button
            type="button"
            className="p-0.5 text-amber-400/70 hover:text-red-400 transition-colors"
            title={t("dvr.resetToGlobal")}
            onClick={() => dvrFieldReset(cardId, settingsKey)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

interface DvrFieldSelectProps extends DvrFieldProps {
  options: { value: string; label: string }[]
  defaultValue: string
}

export function DvrFieldSelect({ cardId, fieldKey, label, desc, options, defaultValue, helpers }: DvrFieldSelectProps) {
  const { dvrFieldValue, setValue, dvrFieldSet, dvrFieldReset } = helpers
  const settingsKey = fieldKey as AppSettingsFieldKey
  const { value, hasOverride, isGlobal } = dvrFieldValue(cardId, settingsKey)
  const showInherited = !isGlobal && !hasOverride
  const showOverridden = !isGlobal && hasOverride

  return (
    <div
      className={cn(
        "p-3 rounded-lg space-y-2 border-l-2",
        isGlobal && "border-l-transparent border border-blue-400/10 bg-muted/40",
        showInherited && "border-l-blue-400/30 border border-blue-400/10 bg-muted/20",
        showOverridden && "border-l-amber-400/70 border border-amber-400/20 bg-amber-500/5"
      )}
    >
      <Label>
        <div className="flex items-center gap-1.5">
          {showInherited && <Link className="h-3.5 w-3.5 text-muted-foreground/50" />}
          {showOverridden && <PenLine className="h-3.5 w-3.5 text-amber-400" />}
          <span className="text-sm">{label}</span>
        </div>
        <span className="text-[11px] text-muted-foreground">{desc}</span>
        {showInherited && <span className="text-[10px] text-blue-400/60 italic block">{t("dvr.usingGlobal")}</span>}
        {showOverridden && <span className="text-[10px] text-amber-400/80 block">{t("dvr.overriddenForDvr")}</span>}
      </Label>
      <div className="flex items-center gap-2">
        <Select
          value={String(value ?? defaultValue)}
          onValueChange={(nextValue) => {
            const parsedValue =
              fieldKey.includes("cache") ||
              fieldKey.includes("cooldown") ||
              fieldKey.includes("threshold") ||
              fieldKey.includes("rate")
                ? Number(nextValue)
                : nextValue

            if (isGlobal) setValue(settingsKey, parsedValue as FieldPathValue<AppSettings, typeof settingsKey>, { shouldDirty: true })
            else dvrFieldSet(cardId, settingsKey, parsedValue as FieldPathValue<AppSettings, typeof settingsKey>)
          }}
        >
          <SelectTrigger className={cn("h-8 text-sm flex-1", showInherited && "opacity-50")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {showOverridden && (
          <button
            type="button"
            className="p-0.5 text-amber-400/70 hover:text-red-400 transition-colors"
            title={t("dvr.resetToGlobal")}
            onClick={() => dvrFieldReset(cardId, settingsKey)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

interface DvrFieldInputProps extends DvrFieldProps {
  min?: number
  max?: number
  step?: number
  suffix?: string
}

export function DvrFieldInput({ cardId, fieldKey, label, desc, min, max, step, suffix, helpers }: DvrFieldInputProps) {
  const { dvrFieldValue, setValue, dvrFieldSet, dvrFieldReset } = helpers
  const settingsKey = fieldKey as AppSettingsFieldKey
  const { value, hasOverride, isGlobal } = dvrFieldValue(cardId, settingsKey)
  const showInherited = !isGlobal && !hasOverride
  const showOverridden = !isGlobal && hasOverride

  return (
    <div
      className={cn(
        "p-3 rounded-lg space-y-2 border-l-2",
        isGlobal && "border-l-transparent border border-blue-400/10 bg-muted/40",
        showInherited && "border-l-blue-400/30 border border-blue-400/10 bg-muted/20",
        showOverridden && "border-l-amber-400/70 border border-amber-400/20 bg-amber-500/5"
      )}
    >
      <Label>
        <div className="flex items-center gap-1.5">
          {showInherited && <Link className="h-3.5 w-3.5 text-muted-foreground/50" />}
          {showOverridden && <PenLine className="h-3.5 w-3.5 text-amber-400" />}
          <span className="text-sm">{label}</span>
        </div>
        <span className="text-[11px] text-muted-foreground">{desc}</span>
        {showInherited && <span className="text-[10px] text-blue-400/60 italic block">{t("dvr.usingGlobal")}</span>}
        {showOverridden && <span className="text-[10px] text-amber-400/80 block">{t("dvr.overriddenForDvr")}</span>}
      </Label>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min={min}
          max={max}
          step={step || 1}
          value={typeof value === "number" ? value : ""}
          onChange={(event) => {
            const parsedValue = Number(event.target.value) || 0
            if (isGlobal) setValue(settingsKey, parsedValue as FieldPathValue<AppSettings, typeof settingsKey>, { shouldDirty: true })
            else dvrFieldSet(cardId, settingsKey, parsedValue as FieldPathValue<AppSettings, typeof settingsKey>)
          }}
          className={cn("h-8 text-sm", showInherited && "opacity-50")}
        />
        {suffix && <span className="text-muted-foreground text-sm shrink-0">{suffix}</span>}
        {showOverridden && (
          <button
            type="button"
            className="p-0.5 text-amber-400/70 hover:text-red-400 transition-colors"
            title={t("dvr.resetToGlobal")}
            onClick={() => dvrFieldReset(cardId, settingsKey)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

interface DvrFieldTextProps extends DvrFieldProps {
  placeholder?: string
}

export function DvrFieldText({ cardId, fieldKey, label, desc, placeholder, helpers }: DvrFieldTextProps) {
  const { dvrFieldValue, setValue, dvrFieldSet, dvrFieldReset } = helpers
  const settingsKey = fieldKey as AppSettingsFieldKey
  const { value, hasOverride, isGlobal } = dvrFieldValue(cardId, settingsKey)
  const showInherited = !isGlobal && !hasOverride
  const showOverridden = !isGlobal && hasOverride

  return (
    <div
      className={cn(
        "p-3 rounded-lg space-y-2 border-l-2",
        isGlobal && "border-l-transparent border border-blue-400/10 bg-muted/40",
        showInherited && "border-l-blue-400/30 border border-blue-400/10 bg-muted/20",
        showOverridden && "border-l-amber-400/70 border border-amber-400/20 bg-amber-500/5"
      )}
    >
      <Label>
        <div className="flex items-center gap-1.5">
          {showInherited && <Link className="h-3.5 w-3.5 text-muted-foreground/50" />}
          {showOverridden && <PenLine className="h-3.5 w-3.5 text-amber-400" />}
          <span className="text-sm">{label}</span>
        </div>
        <span className="text-[11px] text-muted-foreground">{desc}</span>
        {showInherited && <span className="text-[10px] text-blue-400/60 italic block">{t("dvr.usingGlobal")}</span>}
        {showOverridden && <span className="text-[10px] text-amber-400/80 block">{t("dvr.overriddenForDvr")}</span>}
      </Label>
      <div className="flex items-center gap-2">
        <Input
          type="text"
          placeholder={showInherited ? (value ? t("dvr.usingGlobalValue") : placeholder || t("common.notConfigured")) : placeholder || ""}
          value={showInherited ? "" : typeof value === "string" || typeof value === "number" ? value : ""}
          onChange={(event) => {
            if (isGlobal) setValue(settingsKey, event.target.value as FieldPathValue<AppSettings, typeof settingsKey>, { shouldDirty: true })
            else dvrFieldSet(cardId, settingsKey, event.target.value as FieldPathValue<AppSettings, typeof settingsKey>)
          }}
          onFocus={() => {
            if (showInherited) dvrFieldSet(cardId, settingsKey, (value ?? "") as FieldPathValue<AppSettings, typeof settingsKey>)
          }}
          className={cn("h-8 text-sm", showInherited && "opacity-50")}
        />
        {showOverridden && (
          <button
            type="button"
            className="p-0.5 text-amber-400/70 hover:text-red-400 transition-colors"
            title={t("dvr.resetToGlobal")}
            onClick={() => dvrFieldReset(cardId, settingsKey)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}
