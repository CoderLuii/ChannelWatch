import { describe, expect, it } from "vitest"

import { canonicalActivityClientValue, normalizeActivityClientValue } from "@/lib/activity-clients"

describe("activity client values", () => {
  it("groups case, Unicode width, and repeated whitespace consistently", () => {
    expect(normalizeActivityClientValue("  Ｌｉｖｉｎｇ   ROOM ")).toBe("living room")
  })

  it("returns the most recent spelling supplied by the facet endpoint", () => {
    const clients = [{ value: "Living Room", label: "Living Room", count: 12 }]

    expect(canonicalActivityClientValue(clients, "living   room")).toBe("Living Room")
    expect(canonicalActivityClientValue(clients, "Bedroom")).toBeNull()
  })
})
