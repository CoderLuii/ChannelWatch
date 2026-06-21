import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import {
  ErrorCode,
  catalogEntry,
  isErrorPayload,
  parseApiError,
  networkError,
  type ErrorPayload,
} from "../lib/error-catalog"

describe("ErrorCode", () => {
  it("all codes start with ERR_", () => {
    const codes = Object.values(ErrorCode)
    expect(codes.length).toBeGreaterThanOrEqual(20)
    for (const code of codes) {
      expect(code).toMatch(/^ERR_/)
    }
  })

  it("codes are unique strings", () => {
    const codes = Object.values(ErrorCode)
    const unique = new Set(codes)
    expect(unique.size).toBe(codes.length)
  })
})

describe("catalogEntry", () => {
  it("returns the entry for a known code", () => {
    const entry = catalogEntry(ErrorCode.DVR_NOT_FOUND)
    expect(entry.code).toBe(ErrorCode.DVR_NOT_FOUND)
    expect(typeof entry.message).toBe("string")
    expect(entry.message.length).toBeGreaterThan(0)
  })

  it("returns UNKNOWN entry for an unrecognised code", () => {
    const entry = catalogEntry("ERR_TOTALLY_MADE_UP")
    expect(entry.code).toBe(ErrorCode.UNKNOWN)
  })

  it("every known ErrorCode has a catalog entry with a non-empty message", () => {
    for (const code of Object.values(ErrorCode)) {
      const entry = catalogEntry(code)
      expect(entry.code).toBe(code)
      expect(entry.message.trim().length).toBeGreaterThan(0)
    }
  })

  it("mirrors backend public ErrorCode constants", () => {
    const backendSource = readFileSync(
      new URL("../backend/error_catalog.py", import.meta.url),
      "utf8",
    )
    const backendCodes = Array.from(
      backendSource.matchAll(/=\s*"(ERR_[A-Z0-9_]+)"/g),
      (match) => match[1],
    )
    const frontendCodes = new Set<string>(Object.values(ErrorCode))

    expect(backendCodes.length).toBeGreaterThan(20)
    expect(backendCodes.filter((code) => !frontendCodes.has(code))).toEqual([])
  })

  it("entries with remediation have non-empty remediation strings", () => {
    const entry = catalogEntry(ErrorCode.SETTINGS_SAVE_FAILED)
    expect(entry.remediation).toBeTruthy()
    expect((entry.remediation ?? "").length).toBeGreaterThan(0)
  })
})

describe("isErrorPayload", () => {
  it("returns true for a valid ErrorPayload", () => {
    const p: ErrorPayload = { code: "ERR_UNKNOWN", message: "oops" }
    expect(isErrorPayload(p)).toBe(true)
  })

  it("returns false for a plain string", () => {
    expect(isErrorPayload("some error string")).toBe(false)
  })

  it("returns false for null", () => {
    expect(isErrorPayload(null)).toBe(false)
  })

  it("returns false for an object missing code", () => {
    expect(isErrorPayload({ message: "oops" })).toBe(false)
  })

  it("returns false for an object missing message", () => {
    expect(isErrorPayload({ code: "ERR_X" })).toBe(false)
  })

  it("returns false for an object with non-string code", () => {
    expect(isErrorPayload({ code: 42, message: "oops" })).toBe(false)
  })
})

describe("parseApiError", () => {
  function makeResponse(body: unknown, status = 400): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  }

  it("parses a structured ErrorPayload in detail", async () => {
    const payload: ErrorPayload = {
      code: ErrorCode.DVR_NOT_FOUND,
      message: "DVR not found",
      remediation: "Add a DVR in settings",
      docs_url: null,
    }
    const resp = makeResponse({ detail: payload }, 404)
    const result = await parseApiError(resp)
    expect(result.code).toBe(ErrorCode.DVR_NOT_FOUND)
    expect(result.message).toBe("DVR not found")
    expect(result.remediation).toBe("Add a DVR in settings")
  })

  it("parses a plain string detail", async () => {
    const resp = makeResponse({ detail: "Something went wrong" }, 500)
    const result = await parseApiError(resp)
    expect(result.code).toBe(ErrorCode.UNKNOWN)
    expect(result.message).toBe("Something went wrong")
  })

  it("parses a Pydantic validation error array", async () => {
    const resp = makeResponse({
      detail: [
        { loc: ["body", "tz"], msg: "field required", type: "missing" },
        { loc: ["body", "api_key"], msg: "invalid format", type: "value_error" },
      ],
    }, 422)
    const result = await parseApiError(resp)
    expect(result.code).toBe(ErrorCode.UNKNOWN)
    expect(result.message).toContain("tz")
    expect(result.message).toContain("api_key")
  })

  it("falls back gracefully when body is not JSON", async () => {
    const resp = new Response("Internal Server Error", {
      status: 500,
      headers: { "Content-Type": "text/plain" },
    })
    const result = await parseApiError(resp)
    expect(result.code).toBe(ErrorCode.UNKNOWN)
    expect(typeof result.message).toBe("string")
  })

  it("falls back to HTTP status when body is empty", async () => {
    const resp = new Response("", { status: 503 })
    const result = await parseApiError(resp)
    expect(result.code).toBe(ErrorCode.UNKNOWN)
    expect(result.message).toContain("503")
  })
})

describe("networkError", () => {
  it("returns NETWORK code", () => {
    const err = networkError()
    expect(err.code).toBe(ErrorCode.NETWORK)
  })

  it("uses provided message", () => {
    const err = networkError("fetch failed")
    expect(err.message).toBe("fetch failed")
  })

  it("has remediation text", () => {
    const err = networkError()
    expect(err.remediation).toBeTruthy()
  })
})
