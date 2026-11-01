import { readdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { describe, it, expect } from "vitest"
import { t } from "@/lib/i18n"

describe("t()", () => {
  it("resolves a known key to its English value", () => {
    expect(t("dashboard.title")).toBe("Home")
  })

  it("returns the bare key for an unknown key (visible fallback)", () => {
    expect(t("unknown.key.that.does.not.exist")).toBe("unknown.key.that.does.not.exist")
  })

  it("substitutes a single {param} placeholder", () => {
    expect(t("dashboard.tile.asOf", { time: "12:34 PM" })).toBe("As of 12:34 PM")
  })

  it("substitutes multiple {param} placeholders in one string", () => {
    expect(t("wizard.save.successDesc", { name: "Living Room DVR" })).toBe(
      "Living Room DVR is ready."
    )
  })

  it("leaves unmatched placeholders intact when params omit them", () => {
    expect(t("dashboard.nextRecording", { title: "Nature Doc" })).toContain("{countdown}")
  })

  it("accepts numeric param values", () => {
    const result = t("status.notificationsCount", { count: 3 })
    expect(result).toBe("3 active")
  })

  it("resolves nav keys correctly", () => {
    expect(t("nav.dashboard")).toBe("Home")
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
    expect(t("wizard.confirm.saveBtn")).toBe("Save & get started")
  })

  it("resolves status-panel strings", () => {
    expect(t("status.coreEngine")).toBe("Monitoring engine")
    expect(t("status.monitoringAlwaysActive")).toBe("Even with alerts off")
    expect(t("status.dvr.healthy")).toBe("Connected and healthy")
    expect(t("status.badge.notConnected")).toBe("Not connected")
  })

  it("resolves common shared strings", () => {
    expect(t("common.loading")).toBe("Loading...")
    expect(t("common.refresh")).toBe("Refresh")
    expect(t("common.save")).toBe("Save settings")
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
    expect(raw).toBe("Home")
  })
})

// t() prints the bare key for an unknown key, so a missing entry never fails at runtime.
describe("en.json key coverage", () => {
  const appRoot = path.join(__dirname, "..")
  const catalog = JSON.parse(readFileSync(path.join(appRoot, "lib", "i18n", "en.json"), "utf8")) as Record<string, string>
  const keys = Object.keys(catalog)

  function sourceFiles(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) return sourceFiles(full)
      return /\.tsx?$/.test(entry.name) ? [full] : []
    })
  }

  function missingKeys(source: string): string[] {
    const missing: string[] = []
    for (const call of source.matchAll(/\bt\(\s*([^)]*)/g)) {
      let args = call[1]
      for (const template of args.matchAll(/`([^`]*\$\{[^`]*)`/g)) {
        const pattern = new RegExp(`^${template[1].split(/\$\{[^}]*\}/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(".+")}$`)
        if (!keys.some((key) => pattern.test(key))) missing.push(template[0])
      }
      args = args.replace(/`[^`]*`/g, "")
      for (const literal of args.split(",")[0].matchAll(/["']([A-Za-z_]\w*(?:\.\w+)+)["']/g)) {
        if (!(literal[1] in catalog)) missing.push(literal[1])
      }
    }
    for (const literal of source.matchAll(/\b\w*[kK]ey:\s*"([A-Za-z_]\w*(?:\.\w+)+)"/g)) {
      if (!(literal[1] in catalog)) missing.push(literal[1])
    }
    return missing
  }

  it("catches a key that is not in en.json", () => {
    expect(missingKeys('t("diagnostics.title"); t("no.such.key", { a: 1 })')).toEqual(["no.such.key"])
    expect(missingKeys('t(flag ? "diagnostics.title" : "no.such.key")')).toEqual(["no.such.key"])
    expect(missingKeys("t(`no.such.${key}`)")).toEqual(["`no.such.${key}`"])
  })

  it("every static t() key used by the app exists", () => {
    const offenders = ["components", "lib", "app", "hooks"]
      .flatMap((dir) => sourceFiles(path.join(appRoot, dir)))
      .flatMap((file) => missingKeys(readFileSync(file, "utf8")).map((key) => `${path.relative(appRoot, file)}: ${key}`))
    expect(offenders).toEqual([])
  })
})
