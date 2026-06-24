import { describe, it, expect } from "vitest"
import { t } from "@/lib/i18n"

describe("t()", () => {
  it("resolves a known key to its English value", () => {
    expect(t("dashboard.title")).toBe("Dashboard Overview")
  })

  it("returns the bare key for an unknown key (visible fallback)", () => {
    expect(t("unknown.key.that.does.not.exist")).toBe("unknown.key.that.does.not.exist")
  })

  it("substitutes a single {param} placeholder", () => {
    expect(t("dashboard.lastUpdated", { time: "12:34:56 PM" })).toBe("Last updated: 12:34:56 PM")
  })

  it("substitutes multiple {param} placeholders in one string", () => {
    expect(t("wizard.save.successDesc", { name: "Living Room DVR" })).toBe(
      "Living Room DVR is ready. Welcome to ChannelWatch!"
    )
  })

  it("leaves unmatched placeholders intact when params omit them", () => {
    expect(t("dashboard.nextRecording", { title: "Nature Doc" })).toContain("{countdown}")
  })

  it("accepts numeric param values", () => {
    const result = t("status.notificationsCount", { count: 3 })
    expect(result).toBe("3 Active")
  })

  it("resolves nav keys correctly", () => {
    expect(t("nav.dashboard")).toBe("Dashboard")
    expect(t("nav.watchHistory")).toBe("Watch History")
    expect(t("nav.settings")).toBe("Settings")
    expect(t("nav.diagnostics")).toBe("Diagnostics")
    expect(t("nav.about")).toBe("About")
  })

  it("resolves settings tab labels", () => {
    expect(t("settings.tabs.general")).toBe("General")
    expect(t("settings.tabs.alerts")).toBe("Alerts")
    expect(t("settings.tabs.advanced")).toBe("Advanced")
    expect(t("settings.tabs.notifications")).toBe("Notifications")
    expect(t("settings.tabs.routing")).toBe("Routing")
    expect(t("settings.tabs.security")).toBe("Security")
    expect(t("settings.tabs.backup")).toBe("Backup")
    expect(t("settings.tabs.updates")).toBe("Updates")
  })

  it("resolves wizard step copy", () => {
    expect(t("wizard.welcome.title")).toBe("Welcome to ChannelWatch")
    expect(t("wizard.welcome.skip")).toBe("Skip for now")
    expect(t("wizard.confirm.saveBtn")).toBe("Save & Get Started")
  })

  it("resolves status-panel strings", () => {
    expect(t("status.coreEngine")).toBe("Core Engine")
    expect(t("status.monitoringAlwaysActive")).toBe("Always Active")
    expect(t("status.dvr.healthy")).toBe("Connected and healthy")
    expect(t("status.badge.notConnected")).toBe("Not Connected")
  })

  it("resolves common shared strings", () => {
    expect(t("common.loading")).toBe("Loading ChannelWatch...")
    expect(t("common.refresh")).toBe("Refresh")
    expect(t("common.save")).toBe("Save Settings")
    expect(t("common.discard")).toBe("Discard")
    expect(t("common.tryAgain")).toBe("Try again")
    expect(t("errors.tryAgain")).toBe("Try again")
  })

  it("wizard plural discovery count", () => {
    expect(t("wizard.discover.serverFound", { count: 1 })).toBe("1 server found")
    expect(t("wizard.discover.serversFound", { count: 3 })).toBe("3 servers found")
  })

  it("returns original string when params arg is undefined", () => {
    const raw = t("dashboard.title", undefined)
    expect(raw).toBe("Dashboard Overview")
  })
})
