import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const __dirname = dirname(fileURLToPath(import.meta.url))

function srcFile(rel: string): string {
  return readFileSync(resolve(__dirname, rel), "utf8")
}

const providerSettings = [
  "apprise_pushover",
  "apprise_discord",
  "apprise_email",
  "apprise_telegram",
  "apprise_slack",
  "apprise_gotify",
  "apprise_matrix",
  "apprise_custom",
]

describe("notification provider settings matrix", () => {
  it("keeps per-DVR provider editor aligned with shipped global providers", () => {
    const src = srcFile("../components/settings/notifications-settings-section.tsx")

    for (const setting of providerSettings) {
      expect(src).toContain(`key: "${setting}"`)
      expect(src).toContain(`setValue("${setting}"`)
    }

    expect(src).toContain("apprise_telegram")
    expect(src).toContain("provider.telegram.name")
    expect(src).toContain("bottoken/ChatID")
  })

  it("keeps notification override keys aligned with per-DVR tab badges", () => {
    const src = srcFile("../components/settings-form.tsx")
    const notifLine = src.split("\n").find((line) => line.includes("notif: [")) || ""

    for (const setting of [...providerSettings, "apprise_email_to"]) {
      expect(notifLine).toContain(`"${setting}"`)
    }
  })

  it("keeps routing destinations aligned with provider settings", () => {
    const src = srcFile("../components/settings/routing-settings-section.tsx")

    for (const setting of providerSettings) {
      expect(src).toContain(`settingKey: "${setting}"`)
    }

    expect(src).toContain("key: \"telegram\"")
    expect(src).toContain("provider.telegram.name")
  })
})
