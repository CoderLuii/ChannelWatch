import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  ApiError,
  ApiUnavailableError,
  AuthRequiredError,
  applyUpdate,
  checkForUpdate,
  fetchRecoveryUpdateStatus,
  fetchSettings,
  fetchUpdateJob,
  fetchUpdateStatus,
  retryUpdate,
  verifyStartupReady,
} from "@/lib/api"

const unavailableReads = [
  ["update status", () => fetchUpdateStatus()],
  ["update job", () => fetchUpdateJob("0123456789abcdef0123456789abcdef")],
  ["settings", () => fetchSettings()],
  ["startup readiness", () => verifyStartupReady()],
  ["recovery update status", () => fetchRecoveryUpdateStatus()],
] as const

describe("temporarily unavailable read APIs", () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it.each(unavailableReads)("classifies reverse-proxy failures for %s", async (_name, request) => {
    for (const status of [502, 503, 504]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("upstream unavailable", { status })))

      const error = await request().catch((caught: unknown) => caught)

      expect(error).toBeInstanceOf(ApiUnavailableError)
      expect(error).toMatchObject({ name: "ApiUnavailableError", status })
    }
  })

  it("preserves settings authentication handling", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unauthorized", { status: 401 })))

    await expect(fetchSettings()).rejects.toBeInstanceOf(AuthRequiredError)
  })

  it.each([401, 403, 500])("preserves structured update-status errors for HTTP %i", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "ERR_UNKNOWN",
        message: `status ${status}`,
        remediation: null,
      },
    }), {
      status,
      headers: { "Content-Type": "application/json" },
    })))

    const error = await fetchUpdateStatus().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(ApiUnavailableError)
  })

  it.each([
    ["check", () => checkForUpdate()],
    ["apply", () => applyUpdate("1.4.2")],
  ] as const)("does not classify %s mutation failures as temporary read unavailability", async (_name, request) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "ERR_UPDATE_APPLY_FAILED",
        message: "apply failed",
        remediation: null,
      },
    }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    })))

    const error = await request().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(ApiUnavailableError)
  })

  it("preserves ordinary startup readiness errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not ready", { status: 500 })))

    const error = await verifyStartupReady().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(Error)
    expect(error).not.toBeInstanceOf(ApiUnavailableError)
    expect(error).toMatchObject({ message: "ChannelWatch startup is not ready: 500" })
  })

  it.each([
    ["apply", () => applyUpdate("1.4.2", "0123456789abcdef0123456789abcdef")],
    ["retry", () => retryUpdate("1.4.2", "0123456789abcdef0123456789abcdef")],
  ] as const)("classifies an assigned-ID %s gateway response as ambiguous", async (_name, request) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("gateway lost the reply", { status: 502 })))

    const error = await request().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiUnavailableError)
    expect(error).toMatchObject({ status: 502 })
  })

  it("preserves a structured assigned-ID update rejection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "ERR_UPDATE_LOCKED",
        message: "another operation owns the lock",
        remediation: null,
      },
    }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    })))

    const error = await applyUpdate(
      "1.4.2",
      "0123456789abcdef0123456789abcdef",
    ).catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(ApiUnavailableError)
    expect(error).toMatchObject({ payload: { code: "ERR_UPDATE_LOCKED" } })
  })

  it("preserves assigned-ID authentication rejection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unauthorized", { status: 401 })))

    const error = await retryUpdate(
      "1.4.2",
      "0123456789abcdef0123456789abcdef",
    ).catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(ApiUnavailableError)
  })

  it.each([
    ["apply", () => applyUpdate("1.4.2")],
    ["retry", () => retryUpdate()],
  ] as const)("preserves legacy %s gateway handling without an assigned ID", async (_name, request) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("gateway unavailable", { status: 503 })))

    const error = await request().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(ApiUnavailableError)
  })
})
