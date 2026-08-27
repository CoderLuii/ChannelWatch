import type { AppSettings } from "@/lib/types"

export type AlertPreset = "monitor_only" | "important_only" | "balanced" | "everything"
export type AlertPolicyState = AlertPreset | "custom"

export const ALERT_POLICY_KEYS = [
  "alert_channel_watching",
  "alert_vod_watching",
  "alert_disk_space",
  "alert_recording_events",
  "alert_dvr_health",
  "rd_alert_scheduled",
  "rd_alert_started",
  "rd_alert_completed",
  "rd_alert_cancelled",
  "rd_alert_failed",
  "rd_alert_skipped",
  "rd_alert_missed",
  "rd_alert_interrupted",
  "dvr_alert_unreachable",
  "dvr_alert_recovered",
] as const satisfies readonly (keyof AppSettings)[]

export type AlertPolicyKey = (typeof ALERT_POLICY_KEYS)[number]
export type AlertPolicyValues = Record<AlertPolicyKey, boolean>

const MONITOR_ONLY: AlertPolicyValues = {
  alert_channel_watching: false,
  alert_vod_watching: false,
  alert_disk_space: false,
  alert_recording_events: false,
  alert_dvr_health: false,
  rd_alert_scheduled: false,
  rd_alert_started: false,
  rd_alert_completed: false,
  rd_alert_cancelled: false,
  rd_alert_failed: false,
  rd_alert_skipped: false,
  rd_alert_missed: false,
  rd_alert_interrupted: false,
  dvr_alert_unreachable: false,
  dvr_alert_recovered: false,
}

const IMPORTANT_ONLY: AlertPolicyValues = {
  ...MONITOR_ONLY,
  alert_disk_space: true,
  alert_recording_events: true,
  alert_dvr_health: true,
  rd_alert_cancelled: true,
  rd_alert_failed: true,
  rd_alert_skipped: true,
  rd_alert_missed: true,
  rd_alert_interrupted: true,
  dvr_alert_unreachable: true,
  dvr_alert_recovered: true,
}

const BALANCED: AlertPolicyValues = {
  ...IMPORTANT_ONLY,
  rd_alert_started: true,
  rd_alert_completed: true,
}

const EVERYTHING: AlertPolicyValues = {
  ...BALANCED,
  alert_channel_watching: true,
  alert_vod_watching: true,
  rd_alert_scheduled: true,
}

export const ALERT_PRESET_VALUES: Record<AlertPreset, AlertPolicyValues> = {
  monitor_only: MONITOR_ONLY,
  important_only: IMPORTANT_ONLY,
  balanced: BALANCED,
  everything: EVERYTHING,
}

export function detectAlertPolicy(values: Partial<Record<AlertPolicyKey, unknown>>): AlertPolicyState {
  for (const preset of Object.keys(ALERT_PRESET_VALUES) as AlertPreset[]) {
    const expected = ALERT_PRESET_VALUES[preset]
    if (ALERT_POLICY_KEYS.every((key) => Boolean(values[key]) === expected[key])) {
      return preset
    }
  }
  return "custom"
}

export function summarizeAlertPreset(preset: AlertPreset): { enabled: number; disabled: number } {
  const values = ALERT_PRESET_VALUES[preset]
  const enabled = ALERT_POLICY_KEYS.filter((key) => values[key]).length
  return { enabled, disabled: ALERT_POLICY_KEYS.length - enabled }
}
