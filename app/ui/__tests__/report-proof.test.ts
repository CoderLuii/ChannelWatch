import { describe, expect, it, vi } from "vitest"

import { DEFAULT_REPORT_PROOF_DEADLINE_MS, encodeReportChallengeProof, sha256, solveReportChallenge } from "@/lib/report-proof"

const hex = (bytes: Uint8Array) => Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
const challenge = {
  nonce: "test-nonce",
  expires_at: Date.now() + 60_000,
  route_class: "in_app" as const,
  difficulty: 0,
  key_id: "current",
  signature: "signed-value",
}

describe("report proof preparation", () => {
  it("implements SHA-256 without Web Crypto", () => {
    expect(hex(sha256(new TextEncoder().encode("abc")))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )
  })

  it("solves and encodes the exact Worker proof contract", async () => {
    const proof = await solveReportChallenge(challenge)
    expect(proof.solution).toBe("0")
    const encoded = encodeReportChallengeProof(proof)
    const decoded = JSON.parse(atob(encoded.replace(/-/g, "+").replace(/_/g, "/")))
    expect(decoded).toEqual(proof)
  })

  it("yields progress while solving", async () => {
    const onProgress = vi.fn()
    await solveReportChallenge(
      { ...challenge, difficulty: 12 },
      { onProgress, yieldEvery: 64, deadlineMs: 5000 },
    )
    expect(onProgress).toHaveBeenCalled()
  })

  it("cancels without attempting submission", async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(solveReportChallenge({ ...challenge, difficulty: 24 }, { signal: controller.signal }))
      .rejects.toMatchObject({ name: "AbortError" })
  })

  it("stops bounded work and leaves fallbacks available", async () => {
    vi.spyOn(performance, "now").mockReturnValueOnce(0).mockReturnValue(2)
    await expect(solveReportChallenge(
      { ...challenge, difficulty: 24 },
      { deadlineMs: 1, yieldEvery: 1 },
    )).rejects.toThrow("attachments are still here")
  })

  it("uses a 30-second default deadline", async () => {
    expect(DEFAULT_REPORT_PROOF_DEADLINE_MS).toBe(30000)
  })

  it.each([
    { ...challenge, version: 2 },
    { ...challenge, nonce: "" },
    { ...challenge, expires_at: Date.now() - 1 },
    { ...challenge, route_class: "other" as "in_app" },
    { ...challenge, signature: "" },
  ])("rejects malformed or expired challenge input", async (invalid) => {
    await expect(solveReportChallenge(invalid)).rejects.toThrow("invalid secure challenge")
  })
})
