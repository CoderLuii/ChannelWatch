import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, authHeaders, cacheApiKey, downloadBackup, downloadDebugBundle, restoreFromBackup } from "@/lib/api"
import { ErrorCode } from "@/lib/error-catalog"

function installBrowserAuth(csrf = "csrf-token") {
  vi.stubGlobal("window", {})
  const storage = new Map<string, string>()
  vi.stubGlobal("sessionStorage", {
    getItem: vi.fn((key: string) => {
      if (key === "cw_csrf_token") return csrf
      return storage.get(key) ?? null
    }),
    setItem: vi.fn((key: string, value: string) => storage.set(key, value)),
    removeItem: vi.fn((key: string) => storage.delete(key)),
  })
  vi.stubGlobal("localStorage", {
    getItem: vi.fn(),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("backup/debug/restore API helpers", () => {
  it("keeps legacy API keys in session storage for protected frontend requests", () => {
    installBrowserAuth("")

    cacheApiKey("legacy-api-key")

    expect(authHeaders()).toEqual({ "X-API-Key": "legacy-api-key" })
  })

  it("downloads backups from the backup endpoint with auth headers", async () => {
    installBrowserAuth()
    const blob = new Blob(["backup"])
    const fetchMock = vi.fn().mockResolvedValue(new Response(blob))
    vi.stubGlobal("fetch", fetchMock)

    const result = await downloadBackup()

    expect(result).toBeInstanceOf(Blob)
    expect(await result.text()).toBe("backup")
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/backup/download", {
      headers: { "X-CSRF-Token": "csrf-token" },
    })
  })

  it("parses structured debug-bundle errors as ApiError", async () => {
    installBrowserAuth("csrf-debug")
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: ErrorCode.DEBUG_BUNDLE_CREATE_FAILED,
        message: "debug bundle failed",
      },
    }), { status: 500, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(downloadDebugBundle()).rejects.toMatchObject({
      name: "ApiError",
      message: "debug bundle failed",
    } satisfies Partial<ApiError>)
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/debug/bundle", {
      headers: { "X-CSRF-Token": "csrf-debug" },
    })
  })

  it("uploads restore files as FormData and returns JSON", async () => {
    installBrowserAuth("csrf-restore")
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      message: "Restore complete",
      manifest: { files: 3 },
    }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)
    const file = new File(["zip"], "backup.zip", { type: "application/zip" })

    const result = await restoreFromBackup(file)

    expect(result.message).toBe("Restore complete")
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, options] = fetchMock.mock.calls[0]
    expect(options.method).toBe("POST")
    expect(options.headers).toEqual({ "X-CSRF-Token": "csrf-restore" })
    expect(options.body).toBeInstanceOf(FormData)
    expect(options.body.get("file")).toBe(file)
  })
})
