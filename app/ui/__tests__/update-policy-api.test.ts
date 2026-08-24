import { beforeEach, describe, expect, it, vi } from "vitest"

import { fetchUpdatePolicy, postponeUpdate, retryUpdate, saveUpdatePolicy } from "@/lib/api"

const policy = {
  mode: "automatic" as const,
  maintenance_window_start: "03:00",
  maintenance_window_minutes: 120,
  postponed_until: null,
  next_attempt_at: "2026-08-25T03:00:00-04:00",
  retry_count: 0,
  last_error: null,
}

describe("Update Center policy API", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => policy,
    }))
  })

  it("loads the authenticated policy", async () => {
    await expect(fetchUpdatePolicy()).resolves.toEqual(policy)
    expect(fetch).toHaveBeenCalledWith("/api/v1/update/policy", expect.objectContaining({
      credentials: "same-origin",
    }))
  })

  it("saves only the bounded public policy fields", async () => {
    await saveUpdatePolicy({
      mode: "notify_only",
      maintenance_window_start: "03:00",
      maintenance_window_minutes: 120,
    })

    expect(fetch).toHaveBeenCalledWith("/api/v1/update/policy", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        mode: "notify_only",
        maintenance_window_start: "03:00",
        maintenance_window_minutes: 120,
      }),
    }))
  })

  it.each([24, 168] as const)("postpones only a supported %i-hour duration", async (hours) => {
    await postponeUpdate(hours)
    expect(fetch).toHaveBeenCalledWith("/api/v1/update/postpone", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ hours }),
    }))
  })

  it("marks the one-time report-draft postponement without changing other snoozes", async () => {
    await postponeUpdate(24, "dirty_report_draft")
    expect(fetch).toHaveBeenCalledWith("/api/v1/update/postpone", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ hours: 24, reason: "dirty_report_draft" }),
    }))
  })

  it("requests a retry without inventing a target URL or version", async () => {
    await retryUpdate()
    expect(fetch).toHaveBeenCalledWith("/api/v1/update/retry", expect.objectContaining({
      method: "POST",
      credentials: "same-origin",
    }))
  })
})
