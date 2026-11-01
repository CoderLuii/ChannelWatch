import React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const apiMocks = vi.hoisted(() => ({
  discoverServers: vi.fn(),
  testDvrConnection: vi.fn(),
  saveSettings: vi.fn(),
  fetchSettings: vi.fn(),
}))

type HookRuntime = {
  useState<T>(initial: T | (() => T)): [T, (next: T | ((previous: T) => T)) => void]
}

const hookRuntime = vi.hoisted(() => ({ current: null as HookRuntime | null }))

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>()
  return {
    ...actual,
    useState: <T,>(initial: T | (() => T)) => hookRuntime.current!.useState(initial),
  }
})

vi.mock("@/lib/api", () => apiMocks)
vi.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: vi.fn() }) }))

const passthrough = vi.hoisted(() => ({ children }: { children?: unknown }) => children)

vi.mock("@radix-ui/react-dialog", () => ({
  Root: passthrough,
  Portal: passthrough,
  Overlay: () => null,
  Content: passthrough,
  Title: passthrough,
  Description: passthrough,
}))

vi.mock("@/components/base/label", () => ({ Label: passthrough }))

vi.mock("@/components/base/button", async () => {
  const ReactModule = await import("react")
  return { Button: (props: Record<string, unknown>) => ReactModule.createElement("button", props) }
})

vi.mock("@/components/base/input", async () => {
  const ReactModule = await import("react")
  return { Input: (props: Record<string, unknown>) => ReactModule.createElement("input", props) }
})

import { FirstRunWizard } from "@/components/first-run-wizard"
import type { AppSettings } from "@/lib/types"

type TestNode = { type: string; props: Record<string, unknown>; children: TestChild[] }
type TestChild = TestNode | string

class Harness implements HookRuntime {
  private hooks: unknown[] = []
  private index = 0
  root: TestChild[] = []

  constructor(private readonly element: React.ReactNode) {
    this.render()
  }

  render() {
    this.index = 0
    hookRuntime.current = this
    this.root = toTree(this.element)
    hookRuntime.current = null
  }

  useState<T>(initial: T | (() => T)): [T, (next: T | ((previous: T) => T)) => void] {
    const slot = this.index++
    if (!(slot in this.hooks)) this.hooks[slot] = typeof initial === "function" ? (initial as () => T)() : initial
    return [this.hooks[slot] as T, (next) => {
      const previous = this.hooks[slot] as T
      this.hooks[slot] = typeof next === "function" ? (next as (value: T) => T)(previous) : next
      this.render()
    }]
  }
}

function toTree(node: React.ReactNode): TestChild[] {
  if (node === null || node === undefined || typeof node === "boolean") return []
  if (typeof node === "string" || typeof node === "number") return [String(node)]
  if (Array.isArray(node)) return node.flatMap(toTree)
  if (!React.isValidElement(node)) return []
  const { type } = node
  const props = (node.props ?? {}) as Record<string, unknown>
  if (type === React.Fragment) return toTree(props.children as React.ReactNode)
  if (typeof type === "function") return toTree((type as (componentProps: Record<string, unknown>) => React.ReactNode)(props))
  if (typeof type !== "string") return []
  return [{ type, props, children: toTree(props.children as React.ReactNode) }]
}

const allNodes = (children: TestChild[]): TestNode[] =>
  children.flatMap((child) => (typeof child === "string" ? [] : [child, ...allNodes(child.children)]))
const textOf = (child: TestChild): string => (typeof child === "string" ? child : child.children.map(textOf).join(""))

function open(settings: Partial<AppSettings> = { dvr_servers: [] }) {
  const onComplete = vi.fn()
  const onSkip = vi.fn()
  const harness = new Harness(
    React.createElement(FirstRunWizard, { currentSettings: settings as AppSettings, onComplete, onSkip }),
  )
  const button = (name: RegExp) => {
    const found = allNodes(harness.root).find((node) => node.type === "button" && name.test(`${node.props["aria-label"] ?? ""} ${textOf(node)}`))
    if (!found) throw new Error(`No button matched ${name}`)
    return found
  }
  const field = (id: string) => {
    const found = allNodes(harness.root).find((node) => node.type === "input" && node.props.id === id)
    if (!found) throw new Error(`No input ${id}`)
    return found
  }
  const form = () => {
    const found = allNodes(harness.root).find((node) => node.type === "form")
    if (!found) throw new Error("No form rendered")
    return found
  }
  return {
    onComplete,
    onSkip,
    button,
    field,
    heading: () => allNodes(harness.root).filter((node) => node.type === "h2").map(textOf).join(" | "),
    text: () => harness.root.map(textOf).join(" "),
    click: (name: RegExp) => (button(name).props.onClick as () => void)(),
    type: (id: string, value: string) => (field(id).props.onChange as (event: { target: { value: string } }) => void)({ target: { value } }),
    pressEnter: () => {
      const preventDefault = vi.fn()
      const onKeyDown = form().props.onKeyDown as (event: unknown) => void
      onKeyDown({ key: "Enter", target: { tagName: "INPUT" }, preventDefault })
      // The browser only submits on Enter when the form's default button is enabled.
      const submit = allNodes(form().children).find((node) => node.type === "button" && node.props.type === "submit")
      if (!preventDefault.mock.calls.length && !submit?.props.disabled) {
        (form().props.onSubmit as (event: unknown) => void)({ preventDefault })
      }
    },
  }
}

const flush = async () => {
  for (let tick = 0; tick < 4; tick += 1) await Promise.resolve()
}

describe("FirstRunWizard", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset())
    apiMocks.discoverServers.mockResolvedValue({ servers: [{ name: "Living Room DVR", host: "192.168.1.50", port: 8089 }] })
    apiMocks.saveSettings.mockResolvedValue({ message: "ok" })
    apiMocks.fetchSettings.mockResolvedValue({ dvr_servers: [] })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("keeps Continue off until a connection test passes, and any edit asks for a new test", async () => {
    apiMocks.testDvrConnection.mockResolvedValue({ success: true, name: "Living Room" })
    const wizard = open()
    wizard.click(/Add manually/i)
    expect(wizard.button(/Continue/i).props.disabled).toBe(true)
    expect(wizard.button(/Test connection/i).props.disabled).toBe(true)

    wizard.type("wizard-host", "192.168.1.9")
    wizard.click(/Test connection/i)
    await flush()
    expect(apiMocks.testDvrConnection).toHaveBeenCalledWith("192.168.1.9", 8089, undefined)
    expect(wizard.button(/Continue/i).props.disabled).toBe(false)

    wizard.type("wizard-api-key", "secret")
    expect(wizard.button(/Continue/i).props.disabled).toBe(true)
  })

  it("shows why a test failed and stays on the form", async () => {
    apiMocks.testDvrConnection.mockResolvedValue({ success: false, error: "Cannot reach 192.168.1.9:8089." })
    const wizard = open()
    wizard.click(/Add manually/i)
    wizard.type("wizard-host", "192.168.1.9")
    wizard.click(/Test connection/i)
    await flush()
    expect(wizard.text()).toContain("Cannot reach 192.168.1.9:8089.")
    expect(wizard.button(/Continue/i).props.disabled).toBe(true)
  })

  it("runs the test on Enter until it passes, then Enter moves on", async () => {
    apiMocks.testDvrConnection.mockResolvedValue({ success: true, name: "Living Room" })
    const wizard = open()
    wizard.click(/Add manually/i)

    wizard.pressEnter()
    expect(apiMocks.testDvrConnection).not.toHaveBeenCalled()

    wizard.type("wizard-host", "192.168.1.9")
    wizard.pressEnter()
    await flush()
    expect(apiMocks.testDvrConnection).toHaveBeenCalledTimes(1)
    expect(wizard.heading()).toContain("Add DVR manually")

    wizard.pressEnter()
    expect(apiMocks.testDvrConnection).toHaveBeenCalledTimes(1)
    expect(wizard.text()).toContain("192.168.1.9:8089")
    expect(() => wizard.field("wizard-host")).toThrow()
  })

  it("Back from a discovered DVR returns to the discovery list", async () => {
    const wizard = open()
    wizard.click(/Discover on network/i)
    await flush()
    const discoverHeading = wizard.heading()
    wizard.click(/^\s*Use/i)
    expect(wizard.text()).toContain("192.168.1.50:8089")

    wizard.click(/Back/i)
    expect(wizard.heading()).toBe(discoverHeading)
    expect(wizard.text()).toContain("Living Room DVR")
    expect(() => wizard.field("wizard-host")).toThrow()
  })

  it("Back from a typed DVR returns to the form with its values", async () => {
    apiMocks.testDvrConnection.mockResolvedValue({ success: true })
    const wizard = open()
    wizard.click(/Add manually/i)
    wizard.type("wizard-host", "192.168.1.9")
    wizard.click(/Test connection/i)
    await flush()
    wizard.pressEnter()
    wizard.click(/Back/i)
    expect(wizard.field("wizard-host").props.value).toBe("192.168.1.9")
  })

  it("saves the new DVR beside existing entries under its canonical id", async () => {
    const archived = { id: "dvr_old", name: "Old", host: "10.0.0.2", port: 8089, enabled: true, deleted_at: "2026-10-01T00:00:00Z" }
    const refreshed = { dvr_servers: [archived] } as unknown as AppSettings
    apiMocks.fetchSettings.mockResolvedValue(refreshed)
    const wizard = open({ dvr_servers: [archived] })
    wizard.click(/Discover on network/i)
    await flush()
    wizard.click(/^\s*Use/i)
    wizard.click(/Save|Finish|Add/i)
    await flush()

    const posted = apiMocks.saveSettings.mock.calls[0][0] as AppSettings
    expect(posted.dvr_servers).toHaveLength(2)
    expect(posted.dvr_servers?.[0]).toMatchObject({ id: "dvr_old" })
    expect(posted.dvr_servers?.[1]).toMatchObject({ host: "192.168.1.50", port: 8089, enabled: true })
    expect(posted.dvr_servers?.[1].id).toMatch(/^dvr_[0-9a-f]{8}$/)
    expect(wizard.onComplete).toHaveBeenCalledWith(refreshed)
  })

  it("Skip hands control back without saving anything", () => {
    const wizard = open()
    wizard.click(/Skip/i)
    expect(wizard.onSkip).toHaveBeenCalledTimes(1)
    expect(apiMocks.saveSettings).not.toHaveBeenCalled()
  })
})
