import { describe, expect, it } from "vitest"

import { ROUTINE_ACTIVITY_CLASSES, collapseRepeatedDiskAlerts, diskAlertColorClasses, recordingEventPresentation, stripLeadingEmoji } from "@/lib/activity-presentation"

describe("recordingEventPresentation", () => {
  it.each([
    ["Failed: Example", "bg-danger-bg text-danger-fg"],
    ["Did not start: Example", "bg-danger-bg text-danger-fg"],
    ["Skipped: Example", "bg-warning-bg text-warning-fg"],
    ["Interrupted: Example", ROUTINE_ACTIVITY_CLASSES],
    ["Cancelled: Example", ROUTINE_ACTIVITY_CLASSES],
    ["Completed: Example", ROUTINE_ACTIVITY_CLASSES],
    ["Scheduled: Example", ROUTINE_ACTIVITY_CLASSES],
    ["Recording(1): Example", ROUTINE_ACTIVITY_CLASSES],
  ])("maps %s to an intentional operational state", (message, colorClasses) => {
    const presentation = recordingEventPresentation(message)

    expect(presentation.icon).toBeTypeOf("function")
    expect(presentation.colorClasses).toContain(colorClasses)
  })
})

describe("disk alert level", () => {
  it("uses the recorded level, and treats older rows without one as a warning", () => {
    expect(diskAlertColorClasses({ severity: "critical" })).toContain("danger")
    expect(diskAlertColorClasses({ severity: "warning" })).toContain("warning")
    expect(diskAlertColorClasses({})).toContain("warning")
    expect(diskAlertColorClasses(undefined)).toContain("warning")
  })

  it("never reads the level from the title", () => {
    expect(diskAlertColorClasses({ title: "Low Disk Space Critical" } as never)).toContain("warning")
  })
})

describe("stripLeadingEmoji", () => {
  it("removes one leading emoji and its spacing", () => {
    expect(stripLeadingEmoji("\u26A0\uFE0F Low Disk Space Warning")).toBe("Low Disk Space Warning")
    expect(stripLeadingEmoji("\uD83D\uDEA8 Low Disk Space Critical")).toBe("Low Disk Space Critical")
  })

  it("leaves other titles alone", () => {
    expect(stripLeadingEmoji("Low Disk Space \uD83D\uDEA8")).toBe("Low Disk Space \uD83D\uDEA8")
    expect(stripLeadingEmoji("24 hours of news")).toBe("24 hours of news")
  })
})

describe("collapseRepeatedDiskAlerts", () => {
  const disk = (id: string, title: string, dvr_id = "dvr1", type = "disk_alert") => ({ id, type, title, dvr_id })

  it("keeps one entry for an unchanged disk warning", () => {
    const items = [
      disk("a", "Low Disk Space Warning"),
      { id: "w", type: "watching_channel", title: "Watching", dvr_id: "dvr1" },
      disk("b", "Low Disk Space Warning"),
      disk("c", "Low Disk Space Warning"),
    ]
    expect(collapseRepeatedDiskAlerts(items).map((i) => i.id)).toEqual(["a", "w"])
  })

  it("keeps level changes, recoveries, and other DVRs", () => {
    const items = [
      disk("a", "Low Disk Space Warning"),
      disk("r", "Disk space back to normal", "dvr1", "disk_recovered"),
      disk("b", "Low Disk Space Warning"),
      disk("c", "Low Disk Space Critical"),
      disk("d", "Low Disk Space Warning"),
      disk("e", "Low Disk Space Warning", "dvr2"),
    ]
    expect(collapseRepeatedDiskAlerts(items).map((i) => i.id)).toEqual(["a", "r", "b", "c", "d", "e"])
  })
})
