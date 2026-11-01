import { readFileSync, readdirSync } from "node:fs"
import path from "node:path"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { StatusBadge } from "@/components/base/status-badge"
import { kpiTone } from "@/components/dashboard/metric-card"

const css = readFileSync(path.resolve(__dirname, "../app/globals.css"), "utf8")

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`)
  expect(start).toBeGreaterThanOrEqual(0)
  const body = css.slice(start, css.indexOf("}", start))
  const vars: Record<string, string> = {}
  for (const match of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) vars[match[1]] = match[2].trim()
  return vars
}

const light = block(":root")
const dark = { ...light, ...block(".dark") }

function hslToRgb(value: string): [number, number, number] {
  const [h, s, l] = value.replace(/%/g, "").split(/\s+/).map(Number)
  const sat = s / 100
  const lig = l / 100
  const k = (n: number) => (n + h / 30) % 12
  const a = sat * Math.min(lig, 1 - lig)
  const f = (n: number) => lig - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  return [f(0), f(8), f(4)]
}

function luminance(value: string): number {
  const [r, g, b] = hslToRgb(value).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(theme: Record<string, string>, fg: string, bg: string): number {
  const a = luminance(theme[fg])
  const b = luminance(theme[bg])
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

const tones = ["success", "warning", "danger", "info", "neutral"]

describe("design tokens", () => {
  for (const [name, theme] of [["light", light], ["dark", dark]] as const) {
    it(`keeps status text readable on tinted, card and page surfaces (${name})`, () => {
      for (const tone of tones) {
        expect(contrast(theme, `${tone}-fg`, `${tone}-bg`), `${tone}-fg on ${tone}-bg`).toBeGreaterThanOrEqual(4.5)
        expect(contrast(theme, `${tone}-fg`, "card"), `${tone}-fg on card`).toBeGreaterThanOrEqual(4.5)
        expect(contrast(theme, `${tone}-fg`, "background"), `${tone}-fg on background`).toBeGreaterThanOrEqual(4.5)
        expect(contrast(theme, tone, "card"), `${tone} dot on card`).toBeGreaterThanOrEqual(3)
      }
    })

    it(`keeps destructive usable as text and as a filled button (${name})`, () => {
      expect(contrast(theme, "destructive-foreground", "destructive")).toBeGreaterThanOrEqual(4.5)
      expect(contrast(theme, "destructive", "card")).toBeGreaterThanOrEqual(4.5)
      expect(contrast(theme, "muted-foreground", "card")).toBeGreaterThanOrEqual(4.5)
    })
  }

  it("uses a 12px base radius", () => {
    expect(light.radius).toBe("0.75rem")
  })
})

describe("surface system", () => {
  it("keeps three distinct dark levels", () => {
    expect(dark.background).toBe("220 16% 7%")
    expect(dark.card).toBe("220 13% 12%")
    expect(dark.popover).toBe("220 12% 19%")
    expect(contrast(dark, "card", "background")).toBeGreaterThanOrEqual(1.1)
    expect(contrast(dark, "popover", "card")).toBeGreaterThanOrEqual(1.1)
  })

  it("keeps the light levels", () => {
    expect(light.background).toBe("220 17% 94.5%")
    expect(contrast(light, "card", "background")).toBeGreaterThanOrEqual(1.13)
    expect(light.card).toBe("0 0% 100%")
    expect(light.popover).toBe("0 0% 100%")
  })

  it("keeps muted text readable on a fill on an overlay", () => {
    expect(contrast(dark, "muted-foreground", "popover")).toBeGreaterThanOrEqual(5.5)
  })

  it("keeps a group's outer edge stronger than the lines inside it", () => {
    expect(light.edge).toBe("0.13")
    expect(dark.edge).toBe("0.09")
    expect(css).toContain("--color-hairline: hsl(var(--foreground) / 0.08);")
  })

  it("keeps danger text readable on overlays", () => {
    expect(contrast(dark, "danger-fg", "popover")).toBeGreaterThanOrEqual(4.5)
    expect(contrast(light, "danger-fg", "popover")).toBeGreaterThanOrEqual(4.5)
  })

  it("never sets an elevation to none, which would remove focus rings", () => {
    for (const theme of [light, dark]) {
      for (const level of ["elevation-1", "elevation-2", "elevation-3"]) expect(theme[level]).not.toBe("none")
    }
    expect(light["elevation-1"]).toBe("0 0 #0000")
    expect(dark["elevation-2"]).toContain("rgb(0 0 0")
  })

  it("defines one radius scale", () => {
    expect(css).toContain("--radius-group: var(--radius);")
    expect(css).toContain("--radius-control: calc(var(--radius) - 4px);")
    expect(css).toContain("--radius-thumb: calc(var(--radius) - 8px);")
    expect(css).toContain("--radius-sheet: 1.25rem;")
  })
})

describe("one-off class gate", () => {
  const root = path.resolve(__dirname, "..")
  const sources: Array<[string, string]> = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (/\.tsx?$/.test(entry.name)) sources.push([path.relative(root, full), readFileSync(full, "utf8")])
    }
  }
  walk(path.join(root, "components"))
  walk(path.join(root, "app"))

  const offenders = (pattern: RegExp) => sources.filter(([, text]) => pattern.test(text)).map(([file]) => file)

  it("uses the radius scale only", () => {
    expect(offenders(/\brounded-(?:t-|b-|l-|r-)?(?:sm|md|lg|xl|2xl|3xl)\b/)).toEqual([])
    expect(offenders(/\brounded-t-\[/)).toEqual([])
    expect(offenders(/\brounded(?=[\s"'`])/)).toEqual([])
  })

  it("keeps decorative hues off surfaces", () => {
    expect(offenders(/\bbg-(?:amber|blue|purple|emerald|violet|teal|cyan|indigo|sky|fuchsia|orange|yellow)-\d/)).toEqual([])
  })

  it("uses the line and fill tokens", () => {
    expect(offenders(/\b(?:border|divide)-border\/\d/)).toEqual([])
    expect(offenders(/\bbg-(?:muted|background)\/\d/)).toEqual([])
    expect(offenders(/\bbackdrop-blur/)).toEqual([])
  })
})

describe("StatusBadge", () => {
  it("renders semantic token classes and an optional dot", () => {
    const html = renderToStaticMarkup(React.createElement(StatusBadge, { variant: "danger", dot: true }, "Offline"))
    expect(html).toContain("text-danger-fg")
    expect(html).toContain("bg-danger-bg")
    expect(html).toContain("bg-danger")
    expect(html).toContain('data-status="danger"')
  })

  it("defaults to neutral", () => {
    expect(renderToStaticMarkup(React.createElement(StatusBadge, null, "Idle"))).toContain("text-neutral-fg")
  })
})

describe("kpiTone", () => {
  it("returns null without thresholds or a numeric value", () => {
    expect(kpiTone(5)).toBeNull()
    expect(kpiTone("n/a", { warning: 1, danger: 2 })).toBeNull()
    expect(kpiTone("", { warning: 1, danger: 2 })).toBeNull()
  })

  it("escalates as values cross thresholds", () => {
    const thresholds = { warning: 80, danger: 95 }
    expect(kpiTone(10, thresholds)).toBe("success")
    expect(kpiTone(80, thresholds)).toBe("warning")
    expect(kpiTone("96", thresholds)).toBe("danger")
  })

  it("supports lower-is-worse thresholds", () => {
    const thresholds = { warning: 20, danger: 5, direction: "below" as const }
    expect(kpiTone(50, thresholds)).toBe("success")
    expect(kpiTone(20, thresholds)).toBe("warning")
    expect(kpiTone(3, thresholds)).toBe("danger")
  })
})

describe("cn with design tokens", () => {
  it("keeps custom type and elevation tokens next to color classes", async () => {
    const { cn } = await import("@/lib/utils")
    expect(cn("text-title text-foreground")).toBe("text-title text-foreground")
    expect(cn("text-2xs text-muted-foreground")).toBe("text-2xs text-muted-foreground")
    expect(cn("shadow-1 shadow-black/10")).toBe("shadow-1 shadow-black/10")
    expect(cn("text-xs text-title")).toBe("text-title")
    expect(cn("shadow-1", "shadow-3")).toBe("shadow-3")
    expect(cn("rounded-control", "rounded-group")).toBe("rounded-group")
    expect(cn("rounded-thumb bg-fill")).toBe("rounded-thumb bg-fill")
  })
})

describe("scroll padding", () => {
  it("keeps focus clear of fixed chrome on the document scroll container", () => {
    const start = css.indexOf("  html {")
    expect(start).toBeGreaterThanOrEqual(0)
    const body = css.slice(start, css.indexOf("}", start))
    expect(body).toMatch(/scroll-padding-top:\s*calc\(var\(--app-header-height/)
    expect(body).toMatch(/scroll-padding-bottom:[^;]*--mobile-navigation-height[^;]*--settings-save-bar-height/)
  })
})
