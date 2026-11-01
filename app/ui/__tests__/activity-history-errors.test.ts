import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError, fetchActivityHistory, fetchDvrActivityHistory } from "@/lib/api"
import { ErrorCode } from "@/lib/error-catalog"
afterEach(() => vi.unstubAllGlobals())
describe.each([
  ["all DVRs", () => fetchActivityHistory()],
  ["selected DVR", () => fetchDvrActivityHistory("living-room")],
] as const)("history errors: %s", (_label, request) => {
  it("preserves the structured message and remediation", async () => {
    const detail = { code: ErrorCode.UNKNOWN, message: "History temporarily unavailable", remediation: "Check the DVR connection and try again." }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail, debug: "private-debug-data" }), { status: 503 })))
    await expect(request()).rejects.toBeInstanceOf(ApiError)
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail, debug: "private-debug-data" }), { status: 503 })))
    await expect(request()).rejects.toMatchObject({ message: detail.message, payload: detail })
  })
  it("says something readable for an unrecognized response, never a status code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ stack: "private-stack-data" }), { status: 503 })))
    await expect(request()).rejects.toMatchObject({ message: "ChannelWatch is not responding. It may be restarting. Try again in a moment.", payload: { remediation: null } })
  })
})
