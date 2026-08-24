import { beforeEach, describe, expect, it, vi } from "vitest"

import { applyRecoveryUpdate, checkRecoveryUpdate, fetchRecoveryUpdateStatus } from "@/lib/api"

const status = {
  status: "active" as const,
  reason_code: "official_recovery_active" as const,
  current_version: "0.9.17",
  latest: null,
  update_available: false,
  image_required: false,
  recovery_active: true,
  bootstrap_csrf: "bootstrap-review-token",
  confirmation_required: true,
}

describe("official recovery update API", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => status,
    }))
  })

  it("loads only the fixed official recovery status endpoint", async () => {
    await expect(fetchRecoveryUpdateStatus()).resolves.toEqual(status)
    expect(fetch).toHaveBeenCalledWith("/api/v1/update/recovery/status", expect.objectContaining({
      credentials: "same-origin",
    }))
  })

  it("checks the official channel with the one-time bootstrap CSRF", async () => {
    await checkRecoveryUpdate("bootstrap-review-token")
    expect(fetch).toHaveBeenCalledWith("/api/v1/update/recovery/check", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-CSRF-Token": "bootstrap-review-token" }),
      body: JSON.stringify({}),
    }))
  })

  it("applies only an explicit version with the exact recovery confirmation", async () => {
    await applyRecoveryUpdate("0.9.18", "bootstrap-review-token")
    expect(fetch).toHaveBeenCalledWith("/api/v1/update/recovery/apply", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-CSRF-Token": "bootstrap-review-token" }),
      body: JSON.stringify({
        version: "0.9.18",
        confirmation: "INSTALL OFFICIAL UPDATE",
      }),
    }))
    expect(JSON.stringify(vi.mocked(fetch).mock.calls)).not.toContain("catalog_url")
    expect(JSON.stringify(vi.mocked(fetch).mock.calls)).not.toContain("bundle_url")
  })
})
