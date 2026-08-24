import { beforeEach, describe, expect, it, vi } from "vitest"

import { fetchKeyRecoveryStatus, migrateLegacyKey, resetProtectedCredentials } from "@/lib/api"

const recovery = {
  state: "legacy_recovery_required",
  recovery_required: true,
  can_migrate: false,
  can_reset: true,
  blocker_code: "protected_credentials_locked",
  affected_dvr_credentials: 1,
  affected_notification_credentials: 2,
  legacy_input_detected: false,
}

describe("authenticated legacy key recovery API", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => recovery,
    }))
  })

  it("loads recovery state from the admin endpoint", async () => {
    await expect(fetchKeyRecoveryStatus()).resolves.toEqual(recovery)
    expect(fetch).toHaveBeenCalledWith("/api/v1/runtime/key-recovery/status", expect.objectContaining({
      credentials: "same-origin",
    }))
  })

  it("sends a legacy wrapping value only in a one-time multipart request", async () => {
    const legacyWrappingKey = "legacy-review-only-0123456789abcdef"
    await migrateLegacyKey({ legacyWrappingKey })
    expect(fetch).toHaveBeenCalledWith("/api/v1/runtime/key-recovery/migrate", expect.objectContaining({
      method: "POST",
    }))
    const request = vi.mocked(fetch).mock.calls[0]?.[1]
    expect(request?.body).toBeInstanceOf(FormData)
    expect((request?.body as FormData).get("legacy_storage_key")).toBe(legacyWrappingKey)
    expect((request?.body as FormData).get("raw_key_file")).toBeNull()
  })

  it("can upload the original raw key file without putting it in JSON or storage", async () => {
    const rawKey = new File([new Uint8Array(32)], "encryption.key", { type: "application/octet-stream" })
    await migrateLegacyKey({ rawKeyFile: rawKey })
    const request = vi.mocked(fetch).mock.calls[0]?.[1]
    const body = request?.body as FormData
    expect(body.get("legacy_storage_key")).toBeNull()
    expect(body.get("raw_key_file")).toBeInstanceOf(File)
    expect((body.get("raw_key_file") as File).size).toBe(32)
  })

  it("sends only the exact typed reset confirmation", async () => {
    await resetProtectedCredentials("RESET PROTECTED CREDENTIALS")
    expect(fetch).toHaveBeenCalledWith("/api/v1/runtime/key-recovery/reset", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ confirmation: "RESET PROTECTED CREDENTIALS" }),
    }))
  })
})
