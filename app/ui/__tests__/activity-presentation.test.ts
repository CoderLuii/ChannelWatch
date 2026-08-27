import { describe, expect, it } from "vitest"

import { recordingEventPresentation } from "@/lib/activity-presentation"

describe("recordingEventPresentation", () => {
  it.each([
    ["Failed: Example", "bg-red-500/20 text-red-600"],
    ["Did not start: Example", "bg-red-500/20 text-red-600"],
    ["Skipped: Example", "bg-amber-500/20 text-amber-700"],
    ["Interrupted: Example", "bg-slate-500/20 text-slate-600"],
    ["Cancelled: Example", "bg-red-500/20 text-red-600"],
    ["Completed: Example", "bg-purple-500/20 text-purple-600"],
  ])("maps %s to an intentional operational state", (message, colorClasses) => {
    const presentation = recordingEventPresentation(message)

    expect(presentation.icon).toBeTypeOf("function")
    expect(presentation.colorClasses).toContain(colorClasses)
  })
})
