import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, ApiUnavailableError, failureKind, fetchNotificationLog, fetchSecurityStatus, fetchSetupStatus, loadFailureHeadline } from "@/lib/api"
import { ErrorCode, describeError, formatErrorWithRemediation } from "@/lib/error-catalog"
import { healthEventPresentation } from "@/lib/activity-presentation"

describe("describeError", () => {
  it("keeps the server message and remediation from a structured error", () => {
    const error = new ApiError({ code: ErrorCode.SETTINGS_SAVE_FAILED, message: "Could not save.", remediation: "Check /config." })
    expect(describeError(error, "fallback")).toMatchObject({ message: "Could not save.", remediation: "Check /config." })
    expect(formatErrorWithRemediation(error, "fallback")).toBe("Could not save. Check /config.")
  })

  it("fills the remediation from the catalog when the server omitted it", () => {
    const error = new ApiError({ code: ErrorCode.AUTH_ADMIN_EXISTS, message: "Admin exists.", remediation: null })
    const described = describeError(error, "fallback")
    expect(described.code).toBe(ErrorCode.AUTH_ADMIN_EXISTS)
    expect(described.remediation).toBeTruthy()
  })

  it("does not invent a remediation for unknown errors", () => {
    const error = new ApiError({ code: ErrorCode.UNKNOWN, message: "HTTP 500", remediation: null })
    expect(formatErrorWithRemediation(error, "fallback")).toBe("HTTP 500")
  })

  it("falls back for plain errors and non-errors", () => {
    expect(formatErrorWithRemediation(new Error("boom"), "fallback")).toBe("boom")
    expect(formatErrorWithRemediation("weird", "fallback")).toBe("fallback")
  })
})

describe("healthEventPresentation", () => {
  it("renders outage and recovery events differently", () => {
    const outage = healthEventPresentation("dvr_unreachable")
    const recovered = healthEventPresentation("dvr_recovered")
    const disk = healthEventPresentation("disk_recovered")
    expect(outage?.tone).toBe("outage")
    expect(recovered?.tone).toBe("recovery")
    expect(disk?.tone).toBe("recovery")
    expect(outage?.labelKey).not.toBe(recovered?.labelKey)
    expect(outage?.colorClasses).toContain("danger")
    expect(recovered?.colorClasses).toContain("success")
    expect(healthEventPresentation("watching_channel")).toBeNull()
  })
})

describe("failed reads", () => {
  afterEach(() => vi.unstubAllGlobals())

  const answer = (status: number, body: unknown = "") => vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(typeof body === "string" ? body : JSON.stringify(body), { status })))
  const thrown = (request: () => Promise<unknown>) => request().catch((caught: unknown) => caught)

  it.each([
    ["setup status", fetchSetupStatus],
    ["security status", fetchSecurityStatus],
    ["notification log", () => fetchNotificationLog({})],
  ])("%s failures carry no status code in their message", async (_name, request) => {
    for (const status of [429, 500, 502]) {
      answer(status, "<html>nope</html>")
      const error = await thrown(request) as Error
      expect(error).toBeInstanceOf(ApiError)
      expect(error.message).not.toMatch(/HTTP|Failed to fetch|\d{3}|<html>/)
    }
  })

  it.each([
    [429, "rate_limited"],
    [500, "server_error"],
    [404, "server_error"],
    [502, "unreachable"],
    [503, "unreachable"],
    [504, "unreachable"],
  ])("HTTP %i reads as %s", async (status, kind) => {
    answer(status)
    expect(failureKind(await thrown(fetchSecurityStatus))).toBe(kind)
  })

  it("a structured 503 from the app is not an outage", async () => {
    answer(503, { detail: { code: ErrorCode.RUNTIME_SETUP_REQUIRED, message: "Setup needed.", remediation: null } })
    expect(failureKind(await thrown(fetchSecurityStatus))).toBe("server_error")
  })

  it("no answer at all is an outage, in any browser's wording", () => {
    for (const message of ["Failed to fetch", "NetworkError when attempting to fetch resource.", "Load failed"]) {
      expect(failureKind(new TypeError(message))).toBe("unreachable")
      expect(describeError(new TypeError(message), "fallback").message).not.toContain(message)
    }
    expect(failureKind(new ApiUnavailableError("x", 502))).toBe("unreachable")
  })

  it("only an outage blames a restart", async () => {
    answer(429)
    expect(loadFailureHeadline(await thrown(fetchSecurityStatus), "the log")).toBe("ChannelWatch is busy. Wait a moment, then try again.")
    answer(500)
    expect(loadFailureHeadline(await thrown(fetchSecurityStatus), "the log")).toBe("Couldn't load the log.")
    answer(502)
    expect(loadFailureHeadline(await thrown(fetchSecurityStatus), "the log")).toContain("may be restarting")
  })
})
