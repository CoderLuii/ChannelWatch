import { describe, expect, it } from "vitest"

import { escapeMarkdownTableCell } from "@/lib/report-markdown"

describe("report markdown helpers", () => {
  it("escapes table-breaking values without dropping readable text", () => {
    const value = "online | healthy\\path\r\nnext\tline\u0007tail"

    expect(escapeMarkdownTableCell(value)).toBe("online \\| healthy\\\\path\\nnext line tail")
  })
})
