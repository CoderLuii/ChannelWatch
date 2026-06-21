import React from "react"
import type { UseFormReturn } from "react-hook-form"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/components/base/tabs", async () => {
  const ReactModule = await import("react")
  return {
    TabsContent: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => ReactModule.createElement("div", props, children),
  }
})

vi.mock("@/components/base/switch", async () => {
  const ReactModule = await import("react")
  return {
    Switch: ({ checked, onCheckedChange, ...props }: { checked?: boolean; onCheckedChange?: (checked: boolean) => void } & Record<string, unknown>) => ReactModule.createElement(
      "button",
      {
        ...props,
        type: "button",
        role: "switch",
        "aria-checked": checked ? "true" : "false",
        onClick: () => onCheckedChange?.(!checked),
      },
      checked ? "on" : "off",
    ),
  }
})

import {
  RoutingSettingsSection,
  activeRoutingDestinations,
  activeRoutingServers,
  getRoutingValue,
  resetDvrRouting,
  setRoutingValue,
  type RoutingState,
} from "@/components/settings/routing-settings-section"
import type { AppSettings } from "@/lib/types"

type TestNode = {
  type: string
  props: Record<string, unknown>
  children: TestChild[]
}
type TestChild = TestNode | string
type Matcher = string | RegExp

function isElement(value: unknown): value is React.ReactElement<Record<string, unknown>> {
  return React.isValidElement(value)
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

  return [{ type, props, children: renderReactNode(props.children as React.ReactNode) }]
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
  const root = renderReactNode(element)
  return {
    queryByText(matcher: Matcher) {
      return allNodes(root).find((node) => matches(textContent(node), matcher)) ?? null
    },
    getByText(matcher: Matcher) {
      const candidates = allNodes(root).filter((node) => matches(textContent(node), matcher))
      const leaf = candidates.find((node) => !node.children.some((child) => typeof child !== "string" && matches(textContent(child), matcher)))
      if (!leaf && candidates.length === 0) throw new Error(`No rendered text matched ${String(matcher)}`)
      return leaf ?? candidates[0]
    },
    getAllByRole(role: string) {
      const nodes = allNodes(root).filter((node) => node.props.role === role || (role === "button" && node.type === "button"))
      if (!nodes.length) throw new Error(`No rendered role matched ${role}`)
      return nodes
    },
  }
}

function fireClick(node: TestNode) {
  const handler = node.props.onClick as (() => void) | undefined
  handler?.()
}

const routingState: RoutingState = {
  living: { disk: { webhook: false } },
}

const settings = {
  dvr_servers: [
    { id: "living", name: "Living Room", host: "10.0.0.10", port: 8089, enabled: true },
    { id: "disabled", name: "Disabled DVR", host: "10.0.0.11", port: 8089, enabled: false },
    { id: "deleted", name: "Deleted DVR", host: "10.0.0.12", port: 8089, enabled: true, deleted_at: "2026-04-28T00:00:00Z" },
  ],
  notification_routing: routingState,
  apprise_pushover: "pover://token/user",
  apprise_discord: "",
  apprise_email: "",
  apprise_telegram: "",
  apprise_slack: "",
  apprise_gotify: "",
  apprise_matrix: "",
  apprise_custom: "",
  webhooks: [{ url: "https://hooks.example/channelwatch", secret: "s", enabled: true }],
} as unknown as AppSettings

describe("RoutingSettingsSection rendered behavior", () => {
  beforeEach(() => {
    vi.stubGlobal("React", React)
  })

  it("renders active DVR/destinations, defaults missing routes on, toggles, and resets", () => {
    const setValue = vi.fn()
    const form = {
      watch: (name?: keyof AppSettings) => (name ? settings[name] : settings),
      setValue,
    } as unknown as UseFormReturn<AppSettings>

    const view = render(React.createElement(RoutingSettingsSection, { form }))

    expect(view.getByText("Living Room")).toBeTruthy()
    expect(view.queryByText("Disabled DVR")).toBeNull()
    expect(view.queryByText("Deleted DVR")).toBeNull()
    expect(view.getByText("Pushover")).toBeTruthy()
    expect(view.getByText("Webhook")).toBeTruthy()

    const switches = view.getAllByRole("switch")
    expect(switches[0].props["aria-checked"]).toBe("true")
    fireClick(switches[0])
    expect(setValue).toHaveBeenCalledWith(
      "notification_routing",
      { living: { disk: { webhook: false }, channel: { pushover: false } } },
      { shouldDirty: true },
    )

    fireClick(view.getByText("Reset to defaults"))
    expect(setValue).toHaveBeenCalledWith("notification_routing", {}, { shouldDirty: true })
  })
})

describe("notification routing matrix helpers", () => {
  it("show only enabled DVRs, active destinations, default-on routes, and reset copies", () => {
    expect(activeRoutingServers(settings.dvr_servers).map((server) => server.id)).toEqual(["living"])
    expect(activeRoutingDestinations(settings).map((dest) => dest.key)).toEqual(["pushover", "webhook"])
    expect(getRoutingValue({}, "living", "disk", "webhook")).toBe(true)
    expect(setRoutingValue({}, "living", "disk", "webhook", false)).toEqual({ living: { disk: { webhook: false } } })
    expect(resetDvrRouting(routingState, "living")).toEqual({})
  })
})
