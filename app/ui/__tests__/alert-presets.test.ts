import { describe, expect, it } from "vitest"

import {
  ALERT_POLICY_KEYS,
  ALERT_PRESET_VALUES,
  detectAlertPolicy,
  summarizeAlertPreset,
  type AlertPreset,
} from "@/lib/alert-presets"

describe("alert presets", () => {
  it.each(Object.keys(ALERT_PRESET_VALUES) as AlertPreset[])(
    "detects %s exactly",
    (preset) => {
      expect(detectAlertPolicy(ALERT_PRESET_VALUES[preset])).toBe(preset)
    },
  )

  it("reports custom when one switch differs", () => {
    const values = { ...ALERT_PRESET_VALUES.important_only, alert_vod_watching: true }
    expect(detectAlertPolicy(values)).toBe("custom")
  })

  it("keeps Monitor Only delivery fully disabled", () => {
    expect(ALERT_POLICY_KEYS.every((key) => !ALERT_PRESET_VALUES.monitor_only[key])).toBe(true)
  })

  it("keeps routine viewing and recording progress out of Important Only", () => {
    const values = ALERT_PRESET_VALUES.important_only
    expect(values.alert_channel_watching).toBe(false)
    expect(values.alert_vod_watching).toBe(false)
    expect(values.rd_alert_scheduled).toBe(false)
    expect(values.rd_alert_started).toBe(false)
    expect(values.rd_alert_completed).toBe(false)
    expect(values.rd_alert_failed).toBe(true)
    expect(values.dvr_alert_unreachable).toBe(true)
  })

  it("adds recording progress only in Balanced", () => {
    expect(ALERT_PRESET_VALUES.balanced.rd_alert_started).toBe(true)
    expect(ALERT_PRESET_VALUES.balanced.rd_alert_completed).toBe(true)
    expect(ALERT_PRESET_VALUES.balanced.rd_alert_scheduled).toBe(false)
  })

  it("reports an accurate unsaved change summary", () => {
    const summary = summarizeAlertPreset("everything")
    expect(summary.enabled + summary.disabled).toBe(ALERT_POLICY_KEYS.length)
    expect(summary.disabled).toBe(0)
  })
})
