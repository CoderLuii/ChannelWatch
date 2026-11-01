import { readdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"

import { ErrorCode, catalogEntry, networkError } from "../lib/error-catalog"

const en = JSON.parse(readFileSync(path.join(__dirname, "..", "lib", "i18n", "en.json"), "utf8")) as Record<string, string>
const notificationLog = readFileSync(path.join(__dirname, "..", "components", "notification-log.tsx"), "utf8")

const EM_DASH = "—"

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return /\.tsx?$/.test(entry.name) ? [full] : []
  })
}

describe("UI copy style", () => {
  it("keeps em dashes out of user-facing strings", () => {
    const offenders = Object.entries(en).filter(([, value]) => value.includes(EM_DASH)).map(([key]) => key)
    expect(offenders).toEqual([])
  })

  it("keeps em dashes out of the error catalog", () => {
    const entries = [...Object.values(ErrorCode).map((code) => catalogEntry(code)), networkError()]
    const offenders = entries
      .filter((entry) => `${entry.message} ${entry.remediation ?? ""}`.includes(EM_DASH))
      .map((entry) => entry.code)
    expect(offenders).toEqual([])
  })

  it("keeps em dashes and their escapes out of components and lib", () => {
    const appRoot = path.join(__dirname, "..")
    const offenders = ["components", "lib"]
      .flatMap((dir) => sourceFiles(path.join(appRoot, dir)))
      .filter((file) => /—|\\u2014|&mdash;|&#8212;/.test(readFileSync(file, "utf8")))
      .map((file) => path.relative(appRoot, file))
    expect(offenders).toEqual([])
  })

  it("separates the unfiltered and filtered notification log empty states", () => {
    expect(en["notificationLog.empty"]).toBe("No notifications sent yet")
    expect(en["notificationLog.emptyFiltered"]).toBe("Nothing matches these filters")
    expect(notificationLog).toContain('hasActiveFilter ? "notificationLog.emptyFiltered" : "notificationLog.empty"')
  })
})
