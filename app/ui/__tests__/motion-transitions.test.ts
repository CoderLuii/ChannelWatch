import { readdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { describe, expect, it } from "vitest"

const read = (relative: string) => readFileSync(path.resolve(__dirname, "..", relative), "utf8")

// Tailwind v4 emits translate-*, rotate-* and scale-* as the individual
// `translate`, `rotate` and `scale` properties, not as `transform`. A custom
// transition list has to name those properties or the motion snaps.
function transitionLists(src: string): string[][] {
  return [...src.matchAll(/transition-\[([^\]]+)\]/g)].map((match) => match[1].split(","))
}

function sourceFiles(dir: string): string[] {
  return readdirSync(path.resolve(__dirname, "..", dir), { withFileTypes: true }).flatMap((entry) => {
    const relative = `${dir}/${entry.name}`
    if (entry.isDirectory()) return sourceFiles(relative)
    return /\.tsx?$/.test(entry.name) ? [relative] : []
  })
}

const css = read("app/globals.css")
const uiSources = [...sourceFiles("components"), ...sourceFiles("hooks")].map((file) => ({ file, src: read(file) }))

describe("motion tokens", () => {
  it("keeps one small set of durations and easings", () => {
    expect(css).toContain("--motion-fast: 150ms")
    expect(css).toContain("--motion-base: 220ms")
    expect(css).toContain("--motion-slow: 320ms")
    expect(css).toContain("--ease-out: cubic-bezier(0.23, 1, 0.32, 1)")
    expect(css).toContain("--ease-drawer: cubic-bezier(0.32, 0.72, 0, 1)")
    expect(css.match(/cubic-bezier\(/g)).toHaveLength(2)
    expect(css).not.toMatch(/\bease-in\b/)
    expect(css).not.toMatch(/\b\d+(?:\.\d+)?m?s (?:ease|linear|cubic)/)
  })

  it("points bare transition utilities at the tokens", () => {
    expect(css).toContain("--default-transition-duration: var(--motion-fast)")
    expect(css).toContain("--default-transition-timing-function: var(--ease-out)")
  })

  it("uses no one-off durations, ease-in, transition-all or infinite decoration in components", () => {
    for (const { file, src } of uiSources) {
      expect(src, file).not.toMatch(/\bduration-\d+/)
      expect(src, file).not.toMatch(/\bease-in\b/)
      expect(src, file).not.toMatch(/\btransition-all\b/)
      expect(src, file).not.toMatch(/\banimate-(ping|bounce)\b/)
    }
  })

  it("never leaves a duration on the default transition-property: all", () => {
    for (const { file, src } of uiSources) {
      for (const literal of src.match(/"[^"\n]*\bduration-\[[^"\n]*"/g) ?? []) {
        expect(literal, file).toMatch(/\btransition-/)
      }
    }
  })

  it("animates no layout properties", () => {
    for (const { file, src } of uiSources) {
      for (const list of transitionLists(src)) {
        expect(list.filter((prop) => ["width", "height", "margin", "padding", "top", "left"].includes(prop)), file).toEqual([])
      }
    }
  })
})

describe("motion transitions", () => {
  it("moves the desktop sidebar and the page beside it together", () => {
    expect(transitionLists(read("components/sidebar.tsx"))).toEqual([])
  })

  it("uses one interruptible phone sheet for More and every dialog", () => {
    const sidebar = read("components/sidebar.tsx")
    const dialog = read("components/base/dialog.tsx")
    expect(sidebar).toContain("mobile-sheet ")
    expect(dialog).toContain("dialog-sheet")
    for (const selector of [".mobile-sheet", ".dialog-sheet"]) {
      expect(css).toContain(`${selector} {\n`)
      expect(css).toContain(`${selector}[data-state="closed"] {`)
    }
    expect(css.match(/transition: transform var\(--motion-slow\) var\(--ease-drawer\)/g)).toHaveLength(2)
    expect(css.match(/animation: sheet-hold var\(--motion-base\) linear/g)).toHaveLength(4)
    expect(css).toContain("@starting-style")
  })

  it("does not make centered dialogs travel diagonally", () => {
    const dialog = read("components/base/dialog.tsx")
    expect(dialog).not.toMatch(/slide-(in|out)-(from|to)-(left|top)/)
    expect(dialog).toContain("sm:data-[state=open]:zoom-in-[0.96]")
    expect(dialog).toContain("sm:data-[state=closed]:zoom-out-[0.96]")
    expect(dialog).not.toContain("backdrop-blur")
  })

  it("opens anchored menus from their trigger", () => {
    for (const [file, name] of [["popover", "popover"], ["select", "select"], ["dropdown-menu", "dropdown-menu"], ["tooltip", "tooltip"]]) {
      const src = read(`components/base/${file}.tsx`)
      expect(src, file).toContain(`origin-[var(--radix-${name}-content-transform-origin)]`)
      expect(src, file).toContain("duration-[var(--motion-fast)]")
      expect(src, file).not.toMatch(/slide-in-from-/)
    }
    expect(read("components/base/tooltip.tsx")).toContain("data-[state=instant-open]:animate-none")
  })

  it("changes tabs instantly and only fades the pill color", () => {
    const tabs = read("components/base/tabs.tsx")
    expect(tabs).toContain("transition-colors duration-[var(--motion-fast)]")
    expect(tabs).not.toMatch(/animate-|slide-/)
  })

  it("keeps the leaving sheet's content until it has gone", () => {
    for (const file of ["components/dashboard/activity-detail-dialog.tsx", "components/dashboard/recording-detail-dialog.tsx"]) {
      const src = read(file)
      expect(src, file).toContain("React.useRef(current)")
      expect(src, file).toContain("onCloseAutoFocus={onCloseAutoFocus}")
    }
  })

  it("fades settings sections with no height animation", () => {
    expect(css).toMatch(/\.disclosure-enter \{\s*transition: opacity var\(--motion-fast\) var\(--ease-out\);/)
    expect(css).not.toContain("grid-template-rows")
    expect(read("components/settings/alerts-settings-section.tsx").match(/disclosure-enter/g)).toHaveLength(6)
    expect(read("components/settings/advanced-settings-section.tsx").match(/disclosure-enter/g)).toHaveLength(5)
  })

  it("settles only rows a refresh brought in", () => {
    const src = read("components/dashboard/recent-activity-list.tsx")
    expect(src).toContain('data-new={isNewRow(activity.id) ? "" : undefined}')
    expect(src).toContain("`${selectedDvr}|")
    expect(css).toMatch(/\[data-new\] \{[\s\S]*translate: 0 -8px/)
  })

  it("bounces only the success icon in the restart overlay", () => {
    expect(read("components/header.tsx").match(/animate-icon-bounce/g)).toHaveLength(1)
  })

  it("swaps the theme icon without a morph", () => {
    const src = read("components/mode-toggle.tsx")
    expect(src).not.toMatch(/transition|scale-0|rotate-/)
  })

  it("honors reduced motion everywhere with one global rule", () => {
    const rule = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"))
    expect(rule).toMatch(/animation-duration: 0\.01ms !important/)
    expect(rule).toMatch(/transition-duration: 0\.01ms !important/)
    expect(rule).toMatch(/\.mobile-sheet,\s*\.dialog-sheet,\s*\.mobile-sheet-overlay,\s*\.disclosure-enter,\s*\[data-new\] \{\s*transition: none !important/)
    expect(read("components/report-problem-dialog.tsx")).toContain('matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"')
  })

  it("animates the toast swipe snap-back", () => {
    const src = read("components/base/toast.tsx")
    expect(src).toContain("data-[swipe=cancel]:translate-y-0")
    expect(transitionLists(src)).toContainEqual(expect.arrayContaining(["translate", "opacity"]))
  })

  it("names the individual transform property Tailwind actually animates", () => {
    expect(read("components/base/button.tsx")).toContain("transition-[background-color,color,border-color,scale,box-shadow]")
    for (const list of transitionLists(read("components/base/toast.tsx"))) {
      if (list.includes("transform")) expect(list).toContain("translate")
    }
  })
})

describe("toast durations", () => {
  async function latestToast(props: Parameters<typeof import("@/hooks/use-toast").toast>[0]) {
    const React = await import("react")
    const { renderToStaticMarkup } = await import("react-dom/server")
    const { toast, useToast } = await import("@/hooks/use-toast")
    const handle = toast(props)
    let toasts: ReturnType<typeof useToast>["toasts"] = []
    function Probe() {
      toasts = useToast().toasts
      return null
    }
    renderToStaticMarkup(React.createElement(Probe))
    handle.dismiss()
    return toasts.find((entry) => entry.id === handle.id)
  }

  it("keeps failure toasts open until dismissed", async () => {
    expect((await latestToast({ variant: "destructive", title: "Save failed" }))?.duration).toBe(Infinity)
  })

  it("leaves other toasts on the provider default", async () => {
    const entry = await latestToast({ title: "Saved" })
    expect(entry).toBeDefined()
    expect(entry && "duration" in entry).toBe(false)
  })

  it("respects an explicit duration on a failure toast", async () => {
    expect((await latestToast({ variant: "destructive", title: "Failed", duration: 8000 }))?.duration).toBe(8000)
  })
})
