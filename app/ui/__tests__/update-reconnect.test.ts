import { describe, expect, it, vi } from "vitest"

import { applyUpdateAndReconnect, isPendingUpdateJob, reloadUpdatedDashboard, requiresUpdateReconnect, waitForUpdatedRuntime } from "@/lib/update-reconnect"

function status(version: string) {
  return {
    current_version: version,
    runtime_abi: "channelwatch-runtime-v1",
    active_bundle: version === "0.9.13" ? null : { version, path: `/config/releases/v${version}` },
  }
}

describe("waitForUpdatedRuntime", () => {
  it("treats restart disconnects as temporary and resolves when the target version is active", async () => {
    const fetchStatus = vi.fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(status("0.9.16"))
    const wait = vi.fn().mockResolvedValue(undefined)

    const result = await waitForUpdatedRuntime("0.9.16", { fetchStatus, wait, maxAttempts: 5, intervalMs: 10, requiredStableChecks: 1 })

    expect(result.current_version).toBe("0.9.16")
    expect(fetchStatus).toHaveBeenCalledTimes(3)
    expect(wait).toHaveBeenCalledTimes(2)
  })

  it("keeps polling while the old runtime responds", async () => {
    const fetchStatus = vi.fn()
      .mockResolvedValueOnce(status("0.9.13"))
      .mockResolvedValueOnce(status("0.9.16"))
    const wait = vi.fn().mockResolvedValue(undefined)

    await expect(waitForUpdatedRuntime("0.9.16", { fetchStatus, wait, maxAttempts: 3, intervalMs: 10, requiredStableChecks: 1 }))
      .resolves.toMatchObject({ current_version: "0.9.16" })
    expect(wait).toHaveBeenCalledTimes(1)
  })

  it("waits for the target bundle when the status exposes an active bundle", async () => {
    const fetchStatus = vi.fn()
      .mockResolvedValueOnce({
        ...status("0.9.16"),
        active_bundle: { version: "0.9.13", path: "/config/releases/v0.9.13" },
      })
      .mockResolvedValueOnce(status("0.9.16"))
    const wait = vi.fn().mockResolvedValue(undefined)

    await waitForUpdatedRuntime("0.9.16", { fetchStatus, wait, maxAttempts: 2, intervalMs: 10, requiredStableChecks: 1 })

    expect(fetchStatus).toHaveBeenCalledTimes(2)
    expect(wait).toHaveBeenCalledTimes(1)
  })

  it("stops after the configured bound and reports a truthful recovery error", async () => {
    const fetchStatus = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    const wait = vi.fn().mockResolvedValue(undefined)

    await expect(waitForUpdatedRuntime("0.9.16", { fetchStatus, wait, maxAttempts: 3, intervalMs: 10 }))
      .rejects.toThrow("ChannelWatch did not reconnect after applying v0.9.16")
    expect(fetchStatus).toHaveBeenCalledTimes(3)
    expect(wait).toHaveBeenCalledTimes(2)
  })

  it("requires two stable target-runtime bootstrap checks before reloading", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const verifyReady = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new TypeError("Load failed"))
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce(undefined)
    const wait = vi.fn().mockResolvedValue(undefined)

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      verifyReady,
      wait,
      maxAttempts: 5,
      intervalMs: 10,
    })).resolves.toMatchObject({ current_version: "0.9.16" })

    expect(fetchStatus).toHaveBeenCalledTimes(4)
    expect(verifyReady).toHaveBeenCalledTimes(4)
    expect(wait).toHaveBeenCalledTimes(3)
  })
})

describe("applyUpdateAndReconnect", () => {
  it("hard-refreshes the dashboard after a verified update", () => {
    const location = { hash: "#settings:updates", reload: vi.fn() }

    reloadUpdatedDashboard(location)

    expect(location.hash).toBe("#overview")
    expect(location.reload).toHaveBeenCalledTimes(1)
  })

  it("keeps a status-less historical job idle while preserving legacy reconnect behavior", () => {
    const legacyJob = { job_id: "legacy-job", restart_required: true }

    expect(isPendingUpdateJob({ status: undefined })).toBe(false)
    expect(requiresUpdateReconnect(legacyJob)).toBe(true)
  })

  it("leaves non-restarting update jobs in the normal in-page flow", async () => {
    const job = { job_id: "job-1", restart_required: false }
    const fetchStatus = vi.fn()
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(job),
      fetchStatus,
      reload,
    })).resolves.toBe(job)
    expect(fetchStatus).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it("refreshes a successful update even when no restart is required", async () => {
    const job = { job_id: "job-success", status: "success", restart_required: false }
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(job),
      fetchStatus: vi.fn(),
      reload,
    })).resolves.toBe(job)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("does not poll for a terminal failed job even when restart_required remains true", async () => {
    const job = { job_id: "job-failed", status: "failed", restart_required: true }
    const fetchStatus = vi.fn()
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(job),
      fetchStatus,
      reload,
    })).resolves.toBe(job)
    expect(fetchStatus).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it("reloads only after a restart-required update reaches the target runtime", async () => {
    const apply = vi.fn().mockResolvedValue({ job_id: "job-1", restart_required: true })
    const fetchStatus = vi.fn()
      .mockRejectedValueOnce(new TypeError("restart disconnect"))
      .mockResolvedValueOnce(status("0.9.16"))
    const wait = vi.fn().mockResolvedValue(undefined)
    const reload = vi.fn()

    const job = await applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus,
      wait,
      reload,
      maxAttempts: 3,
      intervalMs: 10,
      requiredStableChecks: 1,
    })

    expect(job).toEqual({ job_id: "job-1", restart_required: true })
    expect(reload).toHaveBeenCalledTimes(1)
    expect(fetchStatus).toHaveBeenCalledTimes(2)
  })

  it("recovers when the apply request disconnects during the restart", async () => {
    const apply = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus,
      reload,
      maxAttempts: 2,
      intervalMs: 10,
      requiredStableChecks: 1,
    })).resolves.toBeNull()
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it.each([new SyntaxError("bad JSON"), new Error("unexpected client failure")])(
    "does not mistake %s for a restart disconnect",
    async (failure) => {
      const fetchStatus = vi.fn()
      await expect(applyUpdateAndReconnect("0.9.16", {
        apply: vi.fn().mockRejectedValue(failure),
        fetchStatus,
        reload: vi.fn(),
      })).rejects.toBe(failure)
      expect(fetchStatus).not.toHaveBeenCalled()
    },
  )

  it("preserves the original disconnect when reconnect polling times out", async () => {
    const disconnect = new TypeError("Failed to fetch")
    const promise = applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockRejectedValue(disconnect),
      fetchStatus: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
      wait: vi.fn().mockResolvedValue(undefined),
      reload: vi.fn(),
      maxAttempts: 2,
      intervalMs: 1,
      requiredStableChecks: 1,
    })
    await expect(promise).rejects.toMatchObject({ cause: disconnect })
  })

  it("does not hide a rejected API update behind reconnect polling", async () => {
    class RejectedUpdate extends Error {}
    const rejected = new RejectedUpdate("signature rejected")
    const fetchStatus = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockRejectedValue(rejected),
      fetchStatus,
      reload: vi.fn(),
      isRejectedUpdate: (error) => error instanceof RejectedUpdate,
    })).rejects.toBe(rejected)
    expect(fetchStatus).not.toHaveBeenCalled()
  })
})
