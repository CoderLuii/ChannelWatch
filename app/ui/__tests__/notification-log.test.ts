import React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const apiMocks = vi.hoisted(() => ({
  fetchNotificationLog: vi.fn(),
}))

const selectionMock = vi.hoisted(() => ({ selectedDvr: "all" }))

type HookRuntime = {
  useState<T>(initial: T | (() => T)): [T, (next: T | ((previous: T) => T)) => void]
  useEffect(effect: () => void | Promise<void>, deps?: unknown[]): void
  useCallback<T extends (...args: never[]) => unknown>(callback: T, deps?: unknown[]): T
  useRef<T>(initial: T): { current: T }
}

const hookRuntime = vi.hoisted(() => ({ current: null as HookRuntime | null }))

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>()
  return {
    ...actual,
    useState: <T,>(initial: T | (() => T)) => hookRuntime.current!.useState(initial),
    useEffect: (effect: () => void | Promise<void>, deps?: unknown[]) => hookRuntime.current!.useEffect(effect, deps),
    useCallback: <T extends (...args: never[]) => unknown>(callback: T, deps?: unknown[]) => hookRuntime.current!.useCallback(callback, deps),
    useRef: <T,>(initial: T) => hookRuntime.current!.useRef(initial),
  }
})

vi.mock("@/lib/api", () => apiMocks)
vi.mock("@/lib/dvr-selection-context", () => ({
  useDvrSelection: () => ({ selectedDvr: selectionMock.selectedDvr }),
}))

vi.mock("@/components/base/select", async () => {
  const ReactModule = await import("react")
  function findLabel(children: React.ReactNode): string | undefined {
    const array = ReactModule.Children.toArray(children)
    for (const child of array) {
      if (ReactModule.isValidElement(child)) {
        const props = child.props as Record<string, unknown>
        if (typeof props["aria-label"] === "string") return props["aria-label"]
        const nested = findLabel(props.children as React.ReactNode)
        if (nested) return nested
      }
    }
    return undefined
  }
  return {
    Select: ({ value, onValueChange, children }: { value?: string; onValueChange?: (value: string) => void; children?: React.ReactNode }) => ReactModule.createElement(
      "select",
      {
        value,
        "aria-label": findLabel(children),
        onChange: (event: { target: { value: string } }) => onValueChange?.(event.target.value),
      },
      children,
    ),
    SelectTrigger: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => ReactModule.createElement("span", props, children),
    SelectValue: ({ placeholder }: { placeholder?: string }) => ReactModule.createElement("span", null, placeholder),
    SelectContent: ({ children }: React.PropsWithChildren) => ReactModule.createElement(ReactModule.Fragment, null, children),
    SelectItem: ({ value, children }: React.PropsWithChildren<{ value: string }>) => ReactModule.createElement("option", { value }, children),
  }
})

import { NotificationLog, buildNotificationLogOptions, deliveryRowFacts, destinationLabel } from "@/components/notification-log"

type TestNode = {
  type: string
  props: Record<string, unknown>
  children: TestChild[]
  value?: string
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

  useEffect(effect: () => void | Promise<void>, deps?: unknown[]) {
    const index = this.hookIndex++
    const previous = this.hooks[index] as unknown[] | undefined
    if (depsChanged(previous, deps)) {
      this.hooks[index] = deps
      this.pendingEffects.push(effect)
    }
  }

  useRef<T>(initial: T): { current: T } {
    const index = this.hookIndex++
    if (!(index in this.hooks)) this.hooks[index] = { current: initial }
    return this.hooks[index] as { current: T }
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

  return [{
    type,
    props,
    children: renderReactNode(props.children as React.ReactNode),
    value: typeof props.value === "string" ? props.value : "",
  }]
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
    getByLabelText(label: Matcher) {
      const node = allNodes(harness.root).find((item) => typeof item.props["aria-label"] === "string" && matches(item.props["aria-label"], label))
      if (!node) throw new Error(`No rendered label matched ${String(label)}`)
      return node
    },
    getAllByRole(role: string) {
      const nodes = allNodes(harness.root).filter((item) => (role === "button" && item.type === "button") || item.props.role === role)
      if (!nodes.length) throw new Error(`No rendered role matched ${role}`)
      return nodes
    },
    getByText(label: Matcher) {
      const node = allNodes(harness.root).find((item) => matches(textContent(item), label))
      if (!node) throw new Error(`No rendered text matched ${String(label)}`)
      return node
    },
  }
}

const fireEvent = {
  click(node: TestNode) {
    const handler = node.props.onClick as (() => void) | undefined
    handler?.()
  },
  change(node: TestNode, value: string) {
    const handler = node.props.onChange as ((event: { target: { value: string } }) => void) | undefined
    handler?.({ target: { value } })
  },
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

function deliveryResponse(total = 100) {
  return {
    total,
    items: [
      {
        id: "delivery-1",
        sent_at: "2026-04-28T01:00:00Z",
        provider_type: "webhook",
        channel: "webhook",
        event_type: "disk",
        status: "sent",
        retry_count: 0,
        payload_size: 42,
        error: null,
      },
    ],
  }
}

describe("NotificationLog rendered behavior", () => {
  beforeEach(() => {
    vi.stubGlobal("React", React)
    apiMocks.fetchNotificationLog.mockReset()
    apiMocks.fetchNotificationLog.mockResolvedValue(deliveryResponse())
    selectionMock.selectedDvr = "all"
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("omits all-DVR scope, sends real DVR scope, applies filters, and paginates", async () => {
    render(React.createElement(NotificationLog))
    await flushPromises()

    expect(apiMocks.fetchNotificationLog).toHaveBeenLastCalledWith(expect.objectContaining({
      dvr_id: undefined,
      offset: 0,
      limit: 25,
    }))

    selectionMock.selectedDvr = "dvr_aaa11111"
    apiMocks.fetchNotificationLog.mockClear()
    const view = render(React.createElement(NotificationLog))
    await flushPromises()

    expect(apiMocks.fetchNotificationLog).toHaveBeenLastCalledWith(expect.objectContaining({
      dvr_id: "dvr_aaa11111",
      offset: 0,
      limit: 25,
    }))

    fireEvent.change(view.getByLabelText("Filter notification log by destination"), "webhook")
    await flushPromises()
    fireEvent.change(view.getByLabelText("Filter notification log by status"), "failed")
    await flushPromises()
    fireEvent.change(view.getByLabelText("Filter notification log from date"), "2026-04-27")
    await flushPromises()
    fireEvent.change(view.getByLabelText("Filter notification log to date"), "2026-04-28")
    await flushPromises()

    expect(apiMocks.fetchNotificationLog).toHaveBeenLastCalledWith({
      dvr_id: "dvr_aaa11111",
      channel: "webhook",
      status: "failed",
      since: new Date(2026, 3, 27).toISOString(),
      until: new Date(2026, 3, 28, 23, 59, 59, 999).toISOString(),
      offset: 0,
      limit: 25,
    })

    expect(view.getByText(/Page 1 of 4/)).toBeTruthy()
    const nextButton = view.getAllByRole("button").find((button) => button.props["aria-label"] === "Next" && button.props.disabled !== true)
    if (!nextButton) throw new Error("Next page button was not rendered")
    fireEvent.click(nextButton)
    await flushPromises()

    expect(apiMocks.fetchNotificationLog).toHaveBeenLastCalledWith(expect.objectContaining({
      dvr_id: "dvr_aaa11111",
      offset: 25,
      limit: 25,
    }))
  })
})

describe("NotificationLog failed delivery", () => {
  beforeEach(() => {
    vi.stubGlobal("React", React)
    selectionMock.selectedDvr = "all"
    apiMocks.fetchNotificationLog.mockReset()
    apiMocks.fetchNotificationLog.mockResolvedValue({
      offset: 0,
      limit: 25,
      total: 1,
      items: [{ id: 9, dvr_id: "dvr_aaa11111", activity_event_id: null, provider_type: "apprise", channel_id: "discord", channel: "discord", event_type: "disk_alert", status: "circuit_open", sent_at: "2026-04-28T12:00:00Z", retry_count: 0, payload_size: 0, error: "Circuit open \u2014 delivery skipped" }],
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("leads with the friendly text and keeps the raw error behind Details", async () => {
    const view = render(React.createElement(NotificationLog))
    await flushPromises()

    const article = view.getAllByRole("listitem")[0]
    const details = allNodes(article.children).find((node) => node.type === "details")
    if (!details) throw new Error("Details disclosure was not rendered")
    expect(textContent(article)).toContain("ChannelWatch paused this service for 5 minutes")
    expect(textContent(details)).toContain("Circuit open \u2014 delivery skipped")
    expect(details.props.open).toBeUndefined()
    const outside = textContent(article).replace(textContent(details), "")
    expect(outside).not.toContain("Circuit open")
  })
})

describe("NotificationLog phone row", () => {
  it("keeps one line of facts and leaves out what says nothing", () => {
    expect(deliveryRowFacts({ event_type: "disk_alert", payload_size: 512, retry_count: 0 })).toMatch(/^.+ · 512 Bytes$/)
    expect(deliveryRowFacts({ event_type: "disk_alert", payload_size: 0, retry_count: 1 })).toMatch(/^.+ · 1 retry$/)
    expect(deliveryRowFacts({ event_type: "disk_alert", payload_size: 0, retry_count: 3 })).toMatch(/^.+ · 3 retries$/)
    expect(deliveryRowFacts({ event_type: "disk_alert", payload_size: 0, retry_count: 0 })).not.toContain("·")
  })
})

describe("NotificationLog filter and pagination helpers", () => {
  it("builds API options for selected DVR/date filter values", () => {
    expect(buildNotificationLogOptions({
      selectedDvr: "dvr_aaa11111",
      channel: "webhook",
      status: "failed",
      since: "2026-04-27",
      until: "2026-04-28",
      offset: 50,
    })).toEqual({
      dvr_id: "dvr_aaa11111",
      channel: "webhook",
      status: "failed",
      since: new Date(2026, 3, 27).toISOString(),
      until: new Date(2026, 3, 28, 23, 59, 59, 999).toISOString(),
      offset: 50,
      limit: 25,
    })
  })
})

describe("NotificationLog local-day date range", () => {
  it("includes the whole selected 'to' day in local time", () => {
    const options = buildNotificationLogOptions({ since: "2026-10-29", until: "2026-10-29", offset: 0 })
    const since = new Date(options.since!)
    const until = new Date(options.until!)
    expect([since.getFullYear(), since.getMonth(), since.getDate(), since.getHours()]).toEqual([2026, 9, 29, 0])
    expect([until.getFullYear(), until.getMonth(), until.getDate(), until.getHours(), until.getMinutes()]).toEqual([2026, 9, 29, 23, 59])
  })

  it("ignores a stale response that resolves after a newer filter request", async () => {
    vi.stubGlobal("React", React)
    apiMocks.fetchNotificationLog.mockReset()
    let resolveFirst: (value: unknown) => void = () => {}
    apiMocks.fetchNotificationLog
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValue({ total: 0, items: [] })
    const view = render(React.createElement(NotificationLog))
    fireEvent.change(view.getByLabelText("Filter notification log by status"), "failed")
    await flushPromises()
    resolveFirst(deliveryResponse(100))
    await flushPromises()
    expect(() => view.getByText(/Page 1 of 4/)).toThrow()
    vi.unstubAllGlobals()
  })
})

describe("destinationLabel", () => {
  it.each([
    [{ channel: "apprise", channel_id: "pushover" }, "Pushover"],
    [{ channel: "apprise", channel_id: "discord:2" }, "Discord"],
    [{ channel: "apprise", channel_id: "custom" }, "Custom URL"],
    [{ channel: "webhook", channel_id: "webhook" }, "Webhook"],
    [{ channel: "webhook", channel_id: "" }, "Webhook"],
    [{ channel: "apprise", channel_id: "apprise" }, "Notification services"],
    [{ channel: "apprise", channel_id: "" }, "Notification services"],
  ])("%o reads as %s", (item, label) => {
    expect(destinationLabel(item)).toBe(label)
  })
})
