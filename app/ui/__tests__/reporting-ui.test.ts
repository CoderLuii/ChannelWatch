import { afterEach, describe, expect, it, vi } from "vitest"

import {
  copySupportCode,
  isPrivateDeliveryFailure,
  reportSubmitLabelKey,
  supportCodeDownloadFilename,
} from "@/lib/reporting-ui"

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("reporting UI behavior", () => {
  it.each([
    ["dry-run", "supportReport.review.preview"],
    ["email-test", "supportReport.review.sendTest"],
    ["live", "supportReport.review.submit"],
  ] as const)("uses truthful action text for %s mode", (mode, key) => {
    expect(reportSubmitLabelKey(mode)).toBe(key)
  })

  it("copies through the Clipboard API when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("navigator", { clipboard: { writeText } })
    const field = { focus: vi.fn(), select: vi.fn() }

    await expect(copySupportCode("CW-REPORT-v2-test", field)).resolves.toBe("copied")
    expect(writeText).toHaveBeenCalledWith("CW-REPORT-v2-test")
    expect(field.select).not.toHaveBeenCalled()
  })

  it("selects the visible code and uses the browser fallback when clipboard permission is denied", async () => {
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    })
    const execCommand = vi.fn().mockReturnValue(true)
    vi.stubGlobal("document", { execCommand })
    const field = { focus: vi.fn(), select: vi.fn() }

    await expect(copySupportCode("CW-REPORT-v2-test", field)).resolves.toBe("copied")
    expect(field.focus).toHaveBeenCalled()
    expect(field.select).toHaveBeenCalled()
    expect(execCommand).toHaveBeenCalledWith("copy")
  })

  it("leaves the visible code selected for manual copying when all copy methods fail", async () => {
    vi.stubGlobal("navigator", {})
    vi.stubGlobal("document", { execCommand: vi.fn().mockReturnValue(false) })
    const field = { focus: vi.fn(), select: vi.fn() }

    await expect(copySupportCode("CW-REPORT-v2-test", field)).resolves.toBe("manual")
    expect(field.focus).toHaveBeenCalled()
    expect(field.select).toHaveBeenCalled()
  })

  it("uses the stable support-code text filename", () => {
    expect(supportCodeDownloadFilename).toBe("channelwatch-support-code.txt")
  })

  it("classifies partial private delivery as a retryable warning, not success", () => {
    expect(
      isPrivateDeliveryFailure({
        status: "completed_with_private_delivery_failure",
        private_delivery_status: "failed",
      }),
    ).toBe(true)
    expect(
      isPrivateDeliveryFailure({ status: "completed", private_delivery_status: "delivered" }),
    ).toBe(false)
  })
})
