import { describe, expect, it } from "vitest"

import { numberFieldInputValue, normalizeDiskAdvancedSettings, parseNumberFieldInput } from "@/components/settings/constants"

const thresholds = {
  ds_warning_threshold_percent: 10,
  ds_warning_threshold_gb: 50,
  ds_critical_threshold_percent: 5,
  ds_critical_threshold_gb: 25,
}

describe("disk threshold settings", () => {
  it("keeps a cleared field empty while editing instead of turning it into 0", () => {
    expect(parseNumberFieldInput("")).toBeNaN()
    expect(parseNumberFieldInput("  ")).toBeNaN()
    expect(numberFieldInputValue(Number.NaN)).toBe("")
    expect(parseNumberFieldInput("0")).toBe(0)
  })

  it("restores the default for a cleared field on save", () => {
    const { settings, error } = normalizeDiskAdvancedSettings({ ...thresholds, ds_warning_threshold_gb: Number.NaN })
    expect(error).toBeNull()
    expect(settings.ds_warning_threshold_gb).toBe(50)
  })

  it("keeps an explicit 0, which turns that threshold off", () => {
    const { settings, error } = normalizeDiskAdvancedSettings({ ...thresholds, ds_critical_threshold_gb: 0 })
    expect(error).toBeNull()
    expect(settings.ds_critical_threshold_gb).toBe(0)
  })

  it("rejects a critical threshold that is not lower than warning", () => {
    expect(normalizeDiskAdvancedSettings({ ...thresholds, ds_critical_threshold_percent: 10 }).error).toBe("Critical percent must be lower than warning percent.")
    expect(normalizeDiskAdvancedSettings({ ...thresholds, ds_critical_threshold_gb: 60 }).error).toBe("Critical GB must be lower than warning GB.")
  })

  describe("per-DVR overrides", () => {
    const server = (overrides: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
      id: "dvr-1",
      name: "Den",
      host: "den.local",
      port: 8089,
      enabled: true,
      overrides,
      ...extra,
    })

    it("drops a cleared override so the DVR goes back to the global value", () => {
      const { settings, error } = normalizeDiskAdvancedSettings({
        ...thresholds,
        dvr_servers: [server({ ds_warning_threshold_gb: Number.NaN, dvr_health_alert_delay_seconds: Number.NaN, ds_critical_threshold_gb: 10 })],
      })
      expect(error).toBeNull()
      expect(settings.dvr_servers?.[0].overrides).toEqual({ ds_critical_threshold_gb: 10 })
    })

    it("keeps an explicit 0, which turns that threshold off for the DVR", () => {
      const { settings, error } = normalizeDiskAdvancedSettings({
        ...thresholds,
        dvr_servers: [server({ ds_critical_threshold_gb: 0 })],
      })
      expect(error).toBeNull()
      expect(settings.dvr_servers?.[0].overrides).toEqual({ ds_critical_threshold_gb: 0 })
    })

    it("rejects a critical override that is not lower than warning and names the DVR", () => {
      const result = normalizeDiskAdvancedSettings({
        ...thresholds,
        dvr_servers: [server({ ds_warning_threshold_gb: 40, ds_critical_threshold_gb: 40 })],
      })
      expect(result.error).toBe("Critical GB must be lower than warning GB on Den.")
      expect(result.errorDvrId).toBe("dvr-1")
    })

    it("checks an override against the global value it inherits", () => {
      const lowWarning = normalizeDiskAdvancedSettings({ ...thresholds, dvr_servers: [server({ ds_warning_threshold_percent: 3 })] })
      expect(lowWarning.error).toBe("Critical percent must be lower than warning percent on Den.")
      const lowGlobal = normalizeDiskAdvancedSettings({ ...thresholds, ds_warning_threshold_gb: 20, ds_critical_threshold_gb: 10, dvr_servers: [server({ ds_critical_threshold_gb: 30 })] })
      expect(lowGlobal.error).toBe("Critical GB must be lower than warning GB on Den.")
    })

    it("checks the inherited value once a conflicting override is cleared", () => {
      const result = normalizeDiskAdvancedSettings({
        ...thresholds,
        dvr_servers: [server({ ds_warning_threshold_gb: Number.NaN, ds_critical_threshold_gb: 60 })],
      })
      expect(result.error).toBe("Critical GB must be lower than warning GB on Den.")
    })

    it("rejects an override outside the field's range", () => {
      expect(normalizeDiskAdvancedSettings({ ...thresholds, dvr_servers: [server({ ds_warning_threshold_percent: 150 })] }).error).toBe("Warning percent must be between 0 and 100 on Den.")
      expect(normalizeDiskAdvancedSettings({ ...thresholds, dvr_servers: [server({ ds_alert_cooldown: -1 })] }).error).toBe("Disk alert cooldown must be 0 or more on Den.")
    })

    it("ignores archived DVRs when checking, but still removes their cleared values", () => {
      const { settings, error } = normalizeDiskAdvancedSettings({
        ...thresholds,
        dvr_servers: [server({ ds_critical_threshold_gb: 99, ds_alert_cooldown: Number.NaN }, { deleted_at: "2026-10-30T00:00:00Z" })],
      })
      expect(error).toBeNull()
      expect(settings.dvr_servers?.[0].overrides).toEqual({ ds_critical_threshold_gb: 99 })
    })

    it("falls back to the host, then a generic name, in the message", () => {
      expect(normalizeDiskAdvancedSettings({ ...thresholds, dvr_servers: [server({ ds_critical_threshold_gb: 99 }, { name: "" })] }).error).toBe("Critical GB must be lower than warning GB on den.local.")
      expect(normalizeDiskAdvancedSettings({ ...thresholds, dvr_servers: [server({ ds_critical_threshold_gb: 99 }, { name: "", host: "" })] }).error).toBe("Critical GB must be lower than warning GB on this DVR.")
    })
  })
})
