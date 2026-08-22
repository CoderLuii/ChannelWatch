import { afterEach, describe, expect, it, vi } from "vitest"

import { authHeaders, clearCachedAuthState, logoutSession } from "@/lib/api"

function installBrowserAuth(csrf = "csrf-token") {
  vi.stubGlobal("window", {})
  const storage = new Map<string, string>([["cw_csrf_token", csrf]])
  vi.stubGlobal("sessionStorage", {
    getItem: vi.fn((key: string) => storage.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => storage.set(key, value)),
    removeItem: vi.fn((key: string) => storage.delete(key)),
  })
}

afterEach(() => {
  clearCachedAuthState()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("session logout API helper", () => {
  it("sends the session CSRF token and clears it after success", async () => {
    installBrowserAuth()
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)

    await logoutSession()

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": "csrf-token" },
    })
    expect(authHeaders()).toEqual({})
  })

  it("treats an expired session as locally signed out", async () => {
    installBrowserAuth()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })))

    await expect(logoutSession()).resolves.toBeUndefined()
    expect(authHeaders()).toEqual({})
  })
})
