import React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const apiMocks = vi.hoisted(() => ({
  downloadBackup: vi.fn(),
  downloadDebugBundle: vi.fn(),
  restoreFromBackup: vi.fn(),
}))

type HookRuntime = {
  useState<T>(initial: T | (() => T)): [T, (next: T | ((previous: T) => T)) => void]
  useRef<T>(initial: T): { current: T }
  useEffect(effect: () => void | Promise<void>, deps?: unknown[]): void
  useCallback<T extends (...args: never[]) => unknown>(callback: T, deps?: unknown[]): T
}

const hookRuntime = vi.hoisted(() => ({ current: null as HookRuntime | null }))

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>()
  return {
    ...actual,
    useState: <T,>(initial: T | (() => T)) => hookRuntime.current!.useState(initial),
    useRef: <T,>(initial: T) => hookRuntime.current!.useRef(initial),
    useEffect: (effect: () => void | Promise<void>, deps?: unknown[]) => hookRuntime.current!.useEffect(effect, deps),
    useCallback: <T extends (...args: never[]) => unknown>(callback: T, deps?: unknown[]) => hookRuntime.current!.useCallback(callback, deps),
  }
})

vi.mock("@/lib/api", () => apiMocks)

vi.mock("@/components/base/tabs", async () => {
  const ReactModule = await import("react")
  return {
    TabsContent: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => ReactModule.createElement("div", props, children),
  }
})

import {
  BackupSettingsSection,
  backupFilename,
  backupTimestamp,
  debugBundleFilename,
  restoreErrorMessage,
  restoreSuccessMessage,
} from "@/components/settings/backup-section"

type TestNode = {
  type: string
  props: Record<string, unknown>
  children: TestChild[]
  value?: string
  click?: ReturnType<typeof vi.fn>
}
type TestChild = TestNode | string
type Matcher = string | RegExp

function isElement(value: unknown): value is React.ReactElement<Record<string, unknown>> {
  return React.isValidElement(value)
}

function depsChanged(previous: unknown[] | undefined, next: unknown[] | undefined): boolean {
  if (!previous || !next || previous.length !== next.length) return true
  return previous.some((value, index) => !Object.is(value, next[index]))
}

class RenderHarness implements HookRuntime {
  private readonly element: React.ReactNode
  private hooks: unknown[] = []
  private hookIndex = 0
  private pendingEffects: Array<() => void | Promise<void>> = []
  root: TestChild[] = []

  constructor(element: React.ReactNode) {
    this.element = element
    this.render()
  }

  render() {
    this.hookIndex = 0
    this.pendingEffects = []
    hookRuntime.current = this
    this.root = renderReactNode(this.element)
    hookRuntime.current = null
    for (const effect of this.pendingEffects) void effect()
  }

  useState<T>(initial: T | (() => T)): [T, (next: T | ((previous: T) => T)) => void] {
    const index = this.hookIndex++
    if (!(index in this.hooks)) {
      this.hooks[index] = typeof initial === "function" ? (initial as () => T)() : initial
    }
    const setState = (next: T | ((previous: T) => T)) => {
      const previous = this.hooks[index] as T
      this.hooks[index] = typeof next === "function" ? (next as (value: T) => T)(previous) : next
      this.render()
    }
    return [this.hooks[index] as T, setState]
  }

  useRef<T>(initial: T): { current: T } {
    const index = this.hookIndex++
    if (!(index in this.hooks)) this.hooks[index] = { current: initial }
    return this.hooks[index] as { current: T }
  }

  useEffect(effect: () => void | Promise<void>, deps?: unknown[]) {
    const index = this.hookIndex++
    const previous = this.hooks[index] as unknown[] | undefined
    if (depsChanged(previous, deps)) {
      this.hooks[index] = deps
      this.pendingEffects.push(effect)
    }
  }

  useCallback<T extends (...args: never[]) => unknown>(callback: T, deps?: unknown[]): T {
    const index = this.hookIndex++
    const previous = this.hooks[index] as { deps?: unknown[]; callback: T } | undefined
    if (!previous || depsChanged(previous.deps, deps)) {
      this.hooks[index] = { deps, callback }
      return callback
    }
    return previous.callback
  }
}

function renderReactNode(node: React.ReactNode): TestChild[] {
  if (node === null || node === undefined || typeof node === "boolean") return []
  if (typeof node === "string" || typeof node === "number") return [String(node)]
  if (Array.isArray(node)) return node.flatMap(renderReactNode)
  if (!isElement(node)) return []

  const type = node.type
  const props = node.props ?? {}
  if (type === React.Fragment) return renderReactNode(props.children as React.ReactNode)
  if (typeof type === "function") {
    return renderReactNode((type as (componentProps: Record<string, unknown>) => React.ReactNode)(props))
  }
  if (typeof type === "object" && type && "render" in type) {
    const forwardRefType = type as { render?: (componentProps: Record<string, unknown>, ref: unknown) => React.ReactNode }
    if (typeof forwardRefType.render === "function") {
      return renderReactNode(forwardRefType.render(props, props.ref ?? null))
    }
  }
  if (typeof type !== "string") return []

  const children = renderReactNode(props.children as React.ReactNode)
  const testNode: TestNode = {
    type,
    props,
    children,
    value: typeof props.value === "string" ? props.value : "",
    click: vi.fn(() => fireEvent.click(testNode)),
  }
  const ref = props.ref as { current?: unknown } | undefined
  if (ref && typeof ref === "object") ref.current = testNode
  return [testNode]
}

function textContent(child: TestChild): string {
  return typeof child === "string" ? child : child.children.map(textContent).join("")
}

function allNodes(children: TestChild[]): TestNode[] {
  return children.flatMap((child) => typeof child === "string" ? [] : [child, ...allNodes(child.children)])
}

function matches(text: string, matcher: Matcher): boolean {
  return typeof matcher === "string" ? text === matcher : matcher.test(text)
}

function render(element: React.ReactNode) {
  const harness = new RenderHarness(element)
  return {
    harness,
    getByText(matcher: Matcher) {
      const candidates = allNodes(harness.root).filter((node) => matches(textContent(node), matcher))
      const leaf = candidates.find((node) => !node.children.some((child) => typeof child !== "string" && matches(textContent(child), matcher)))
      if (!leaf && candidates.length === 0) throw new Error(`No rendered text matched ${String(matcher)}`)
      return leaf ?? candidates[0]
    },
    getByRole(role: string, options?: { name?: Matcher }) {
      const found = allNodes(harness.root).find((node) => roleOf(node) === role && (!options?.name || matches(accessibleName(node), options.name)))
      if (!found) throw new Error(`No rendered role matched ${role}`)
      return found
    },
    getByInputAccept(accept: string) {
      const found = allNodes(harness.root).find((node) => node.type === "input" && node.props.accept === accept)
      if (!found) throw new Error(`No input accepted ${accept}`)
      return found
    },
  }
}

function roleOf(node: TestNode): string | undefined {
  if (typeof node.props.role === "string") return node.props.role
  if (node.type === "button") return "button"
  return undefined
}

function accessibleName(node: TestNode): string {
  return typeof node.props["aria-label"] === "string" ? node.props["aria-label"] : textContent(node)
}

const fireEvent = {
  click(node: TestNode) {
    const handler = node.props.onClick as ((event: { target: TestNode; currentTarget: TestNode }) => void) | undefined
    handler?.({ target: node, currentTarget: node })
  },
  change(node: TestNode, event: { target: { files?: File[]; value: string } }) {
    const handler = node.props.onChange as ((event: { target: { files?: File[]; value: string } }) => void | Promise<void>) | undefined
    return handler?.(event)
  },
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
}

describe("BackupSettingsSection rendered behavior", () => {
  beforeEach(() => {
    apiMocks.downloadBackup.mockReset()
    apiMocks.downloadDebugBundle.mockReset()
    apiMocks.restoreFromBackup.mockReset()
    vi.stubGlobal("React", React)
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-04-28T01:02:03.456Z"))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("downloads backup/debug ZIPs and restores selected files through rendered controls", async () => {
    const backupBlob = new Blob(["backup"])
    const debugBlob = new Blob(["debug"])
    apiMocks.downloadBackup.mockResolvedValue(backupBlob)
    apiMocks.downloadDebugBundle.mockResolvedValue(debugBlob)
    apiMocks.restoreFromBackup.mockResolvedValue({ message: "Restore complete", manifest: {} })
    const createObjectURL = vi.fn()
      .mockReturnValueOnce("blob:backup")
      .mockReturnValueOnce("blob:debug")
    const revokeObjectURL = vi.fn()
    const clickedDownloads: string[] = []
    const appendChild = vi.fn()
    const removeChild = vi.fn()
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL })
    vi.stubGlobal("document", {
      body: { appendChild, removeChild },
      createElement: vi.fn(() => ({
        href: "",
        download: "",
        click() {
          clickedDownloads.push(this.download)
        },
      })),
    })
    const reload = vi.fn()
    vi.stubGlobal("window", { location: { reload } })

    const view = render(React.createElement(BackupSettingsSection))

    fireEvent.click(view.getByRole("button", { name: /Download backup/i }))
    await flushPromises()

    expect(apiMocks.downloadBackup).toHaveBeenCalledTimes(1)
    expect(createObjectURL).toHaveBeenCalledWith(backupBlob)
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:backup")
    expect(clickedDownloads).toContain("channelwatch_backup_2026-04-28T01-02-03.zip")

    fireEvent.click(view.getByRole("button", { name: /Download debug bundle/i }))
    await flushPromises()

    expect(apiMocks.downloadDebugBundle).toHaveBeenCalledTimes(1)
    expect(createObjectURL).toHaveBeenCalledWith(debugBlob)
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:debug")
    expect(clickedDownloads).toContain("channelwatch_debug_2026-04-28T01-02-03.zip")

    const fileInput = view.getByInputAccept(".zip")
    fireEvent.click(view.getByRole("button", { name: /Choose backup file/i }))
    expect(fileInput.click).toHaveBeenCalledTimes(1)

    const selectedFile = new File(["zip"], "channelwatch.zip")
    const changeEvent = { target: { files: [selectedFile], value: "channelwatch.zip" } }
    await fireEvent.change(fileInput, changeEvent)
    await flushPromises()

    expect(changeEvent.target.value).toBe("")
    expect(apiMocks.restoreFromBackup).toHaveBeenCalledWith(selectedFile)
    expect(view.getByText("Restore complete")).toBeTruthy()
    fireEvent.click(view.getByRole("button", { name: /Reload page/i }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("renders restore API failures from the file input flow", async () => {
    apiMocks.restoreFromBackup.mockRejectedValue(new Error("bad archive"))
    vi.stubGlobal("window", { location: { reload: vi.fn() } })
    const view = render(React.createElement(BackupSettingsSection))
    const fileInput = view.getByInputAccept(".zip")

    await fireEvent.change(fileInput, { target: { files: [new File(["bad"], "bad.zip")], value: "bad.zip" } })
    await flushPromises()

    expect(apiMocks.restoreFromBackup).toHaveBeenCalledTimes(1)
    expect(view.getByText("bad archive")).toBeTruthy()
  })
})

describe("BackupSettingsSection behavior helpers", () => {
  const fixedDate = new Date("2026-04-28T01:02:03.456Z")

  it("builds stable backup and debug bundle filenames", () => {
    expect(backupTimestamp(fixedDate)).toBe("2026-04-28T01-02-03")
    expect(backupFilename(fixedDate)).toBe("channelwatch_backup_2026-04-28T01-02-03.zip")
    expect(debugBundleFilename(fixedDate)).toBe("channelwatch_debug_2026-04-28T01-02-03.zip")
  })

  it("uses restore API messages and preserves thrown error messages", () => {
    expect(restoreSuccessMessage({ message: "Restore completed" })).toBe("Restore completed")
    expect(restoreErrorMessage(new Error("bad archive"))).toBe("bad archive")
  })
})
