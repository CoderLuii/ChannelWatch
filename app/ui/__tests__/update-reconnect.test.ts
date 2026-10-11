import { describe, expect, it, vi } from "vitest"

import { applyUpdateAndReconnect, consumeUpdatedDashboardHandoff, isPendingUpdateJob, reloadUpdatedDashboard, requiresUpdateReconnect, UPDATE_HANDOFF_STORAGE_KEY, UpdateJobPendingError, UpdateReadinessError, UpdateReconnectError, waitForUpdatedRuntime } from "@/lib/update-reconnect"

function status(version: string) {
  return {
    current_version: version,
    runtime_abi: "channelwatch-runtime-v1",
    active_bundle: version === "0.9.13" ? null : { version, path: `/config/releases/v${version}` },
  }
}

function durableStatus(version: string, lastJob: Record<string, unknown> | null) {
  return {
    ...status(version),
    last_job: lastJob,
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

  it("allows the full three-minute activation window by default", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.13"))
    const wait = vi.fn().mockResolvedValue(undefined)

    await expect(waitForUpdatedRuntime("0.9.16", { fetchStatus, wait }))
      .rejects.toThrow("ChannelWatch did not reconnect after applying v0.9.16")

    expect(fetchStatus).toHaveBeenCalledTimes(120)
    expect(wait).toHaveBeenCalledTimes(119)
  })

  it("requires the matching durable job to succeed before accepting the target runtime", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const fetchJob = vi.fn()
      .mockResolvedValueOnce({ job_id: "job-1", status: "validating", version: "0.9.16" })
      .mockResolvedValue({ job_id: "job-1", status: "success", version: "0.9.16" })
    const onJob = vi.fn()

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      fetchJob,
      jobId: "job-1",
      onJob,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 3,
      intervalMs: 1,
    })).resolves.toMatchObject({ current_version: "0.9.16" })

    expect(fetchJob).toHaveBeenCalledTimes(3)
    expect(onJob).toHaveBeenCalledTimes(3)
  })

  it("does not spend the activation attempt budget while dependencies are preparing", async () => {
    const preparing = {
      job_id: "browser-job-preparing",
      status: "applying",
      phase: "preparing",
      phase_budget_seconds: 600,
      phase_deadline_at: new Date(Date.now() + 600_000).toISOString(),
      version: "1.4.2",
    }
    const fetchJob = vi.fn()
      .mockResolvedValueOnce(preparing)
      .mockResolvedValueOnce(preparing)
      .mockResolvedValueOnce(preparing)
      .mockResolvedValue({
        job_id: "browser-job-preparing",
        status: "success",
        version: "1.4.2",
      })
    const fetchStatus = vi.fn().mockResolvedValue(status("1.4.2"))
    const onJob = vi.fn()

    await expect(waitForUpdatedRuntime("1.4.2", {
      fetchStatus,
      fetchJob,
      jobId: "browser-job-preparing",
      onJob,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
      intervalMs: 1,
      requiredStableChecks: 1,
    })).resolves.toMatchObject({ current_version: "1.4.2" })

    expect(fetchJob).toHaveBeenCalledTimes(4)
    expect(fetchStatus).toHaveBeenCalledTimes(1)
    expect(onJob).toHaveBeenCalledWith(preparing)
  })

  it("uses the backend-enforced dependency preparation deadline without spending activation attempts", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"))
    try {
      const fetchJob = vi.fn()
        .mockResolvedValueOnce({
          job_id: "browser-job-budget",
          status: "applying",
          phase: "preparing",
          phase_budget_seconds: 600,
          phase_deadline_at: "2026-10-10T12:10:00Z",
          version: "1.4.2",
        })
        .mockImplementation(() => new Promise(() => undefined))
      let settled = false
      const promise = waitForUpdatedRuntime("1.4.2", {
        fetchStatus: vi.fn(),
        fetchJob,
        jobId: "browser-job-budget",
        wait: vi.fn().mockResolvedValue(undefined),
        maxAttempts: 1,
      }).finally(() => {
        settled = true
      })
      const rejection = expect(promise).rejects.toMatchObject({
        name: "UpdatePreparationTimeoutError",
        message: "Runtime preparation exceeded the 600-second limit.",
      })

      await vi.advanceTimersByTimeAsync(599_999)
      expect(settled).toBe(false)
      expect(fetchJob).toHaveBeenCalledTimes(2)

      await vi.advanceTimersByTimeAsync(1)
      await rejection
      expect(settled).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it("surfaces the matching job rollback message immediately", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))

    const promise = waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      fetchJob: vi.fn().mockResolvedValue({
        job_id: "job-1",
        status: "failed",
        version: "0.9.16",
        rollback_applied: true,
        message: "Activation failed; the previous version was restored.",
      }),
      jobId: "job-1",
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 3,
    })

    await expect(promise).rejects.toMatchObject({
      name: "UpdateJobError",
      message: "Activation failed; the previous version was restored.",
    })
    expect(fetchStatus).not.toHaveBeenCalled()
  })

  it("ignores unrelated durable history until the original job succeeds", async () => {
    const fetchStatus = vi.fn()
      .mockResolvedValueOnce(durableStatus("0.9.16", {
        job_id: "stale-job",
        status: "failed",
        message: "Old update failed.",
      }))
      .mockResolvedValue(durableStatus("0.9.16", {
        job_id: "job-1",
        status: "success",
        version: "0.9.16",
      }))

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      jobId: "job-1",
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 3,
      intervalMs: 1,
    })).resolves.toMatchObject({ current_version: "0.9.16" })

    expect(fetchStatus).toHaveBeenCalledTimes(3)
  })

  it("does not accept a matching job ID with the wrong release version", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const fetchJob = vi.fn()
      .mockResolvedValueOnce({ job_id: "job-1", status: "failed", version: "0.9.15", message: "Stale failure." })
      .mockResolvedValue({ job_id: "job-1", status: "success", version: "0.9.16" })

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      fetchJob,
      jobId: "job-1",
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 3,
      intervalMs: 1,
    })).resolves.toMatchObject({ current_version: "0.9.16" })

    expect(fetchJob).toHaveBeenCalledTimes(3)
  })

  it("ignores matching job IDs until the durable record names the exact version", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const fetchJob = vi.fn()
      .mockResolvedValueOnce({ job_id: "job-1", status: "failed", message: "Incomplete stale record." })
      .mockResolvedValueOnce({ job_id: "job-1", status: "success" })
      .mockResolvedValue({ job_id: "job-1", status: "success", version: "0.9.16" })

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      fetchJob,
      jobId: "job-1",
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 4,
      intervalMs: 1,
    })).resolves.toMatchObject({ current_version: "0.9.16" })

    expect(fetchJob).toHaveBeenCalledTimes(4)
  })

  it("retries network gaps but preserves read-only API failures", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const fetchJob = vi.fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue({ job_id: "job-1", status: "success", version: "0.9.16" })

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      fetchJob,
      jobId: "job-1",
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 3,
      intervalMs: 1,
      requiredStableChecks: 1,
    })).resolves.toMatchObject({ current_version: "0.9.16" })

    const rejected = new Error("Authentication required")
    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus: vi.fn().mockRejectedValue(rejected),
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 3,
    })).rejects.toBe(rejected)
  })

  it("cancels promptly while a read-only status request is still pending", async () => {
    const controller = new AbortController()
    const promise = waitForUpdatedRuntime("0.9.16", {
      fetchStatus: () => new Promise<ReturnType<typeof status>>(() => undefined),
      signal: controller.signal,
    })

    controller.abort()

    await expect(Promise.race([
      promise,
      new Promise((_, reject) => globalThis.setTimeout(() => reject(new Error("hung request")), 50)),
    ])).rejects.toMatchObject({ name: "AbortError" })
  })

  it("bounds a status request that never settles", async () => {
    const promise = waitForUpdatedRuntime("0.9.16", {
      fetchStatus: () => new Promise<ReturnType<typeof status>>(() => undefined),
      maxAttempts: 1,
      intervalMs: 5,
    })

    await expect(Promise.race([
      promise,
      new Promise((_, reject) => globalThis.setTimeout(() => reject(new Error("test watchdog")), 50)),
    ])).rejects.toThrow("ChannelWatch did not reconnect after applying v0.9.16")
  })

  it("keeps a hung request pending beyond 120 seconds and times out near 180 seconds", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"))
    try {
      let settled = false
      const promise = waitForUpdatedRuntime("0.9.16", {
        fetchStatus: () => new Promise<ReturnType<typeof status>>(() => undefined),
      }).finally(() => {
        settled = true
      })
      const rejection = expect(promise).rejects.toThrow(
        "ChannelWatch did not reconnect after applying v0.9.16",
      )

      await vi.advanceTimersByTimeAsync(120_001)
      expect(settled).toBe(false)

      await vi.advanceTimersByTimeAsync(59_999)
      await rejection
      expect(settled).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it("bounds a durable job that remains briefly unavailable", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"))
    try {
      let settled = false
      const promise = waitForUpdatedRuntime("0.9.16", {
        fetchStatus: vi.fn().mockResolvedValue(status("0.9.16")),
        fetchJob: vi.fn().mockRejectedValue(new UpdateJobPendingError()),
        jobId: "browser-job-pending",
        wait: (milliseconds) => new Promise((resolve) => {
          globalThis.setTimeout(resolve, milliseconds)
        }),
      }).finally(() => {
        settled = true
      })
      const rejection = promise.catch((error: unknown) => error)

      await vi.advanceTimersByTimeAsync(120_001)
      expect(settled).toBe(false)

      await vi.advanceTimersByTimeAsync(59_999)
      await expect(rejection).resolves.toMatchObject({
        message: expect.stringContaining("ChannelWatch did not reconnect after applying v0.9.16"),
      })
      expect(settled).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it("retries job-persistence gaps only from the tracked job lookup", async () => {
    const readinessFailure = new UpdateJobPendingError("Wrong request layer.")
    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus: vi.fn().mockResolvedValue(status("0.9.16")),
      fetchJob: vi.fn().mockResolvedValue({
        job_id: "browser-job-1",
        status: "success",
        version: "0.9.16",
      }),
      jobId: "browser-job-1",
      verifyReady: vi.fn().mockRejectedValue(readinessFailure),
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
      requiredStableChecks: 1,
    })).rejects.toBe(readinessFailure)

    const programmingFailure = new RangeError("invalid job response")
    const fetchStatus = vi.fn()
    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      fetchJob: vi.fn().mockRejectedValue(programmingFailure),
      jobId: "browser-job-1",
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
    })).rejects.toBe(programmingFailure)
    expect(fetchStatus).not.toHaveBeenCalled()
  })

  it("does not classify a job observer failure as a pending lookup", async () => {
    const observerFailure = new UpdateJobPendingError("Job observer failed.")
    const fetchStatus = vi.fn()

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus,
      fetchJob: vi.fn().mockResolvedValue({
        job_id: "browser-job-1",
        status: "success",
        version: "0.9.16",
      }),
      jobId: "browser-job-1",
      onJob: () => {
        throw observerFailure
      },
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
    })).rejects.toBe(observerFailure)

    expect(fetchStatus).not.toHaveBeenCalled()
  })

  it("retries only explicit readiness failures and propagates programming errors", async () => {
    const readinessError = new UpdateReadinessError("Settings are still loading.")
    const verifyReady = vi.fn()
      .mockRejectedValueOnce(readinessError)
      .mockResolvedValue(undefined)

    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus: vi.fn().mockResolvedValue(status("0.9.16")),
      verifyReady,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
      intervalMs: 1,
      requiredStableChecks: 1,
    })).resolves.toMatchObject({ current_version: "0.9.16" })

    const programmingError = new RangeError("invalid readiness response")
    await expect(waitForUpdatedRuntime("0.9.16", {
      fetchStatus: vi.fn().mockResolvedValue(status("0.9.16")),
      verifyReady: vi.fn().mockRejectedValue(programmingError),
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
    })).rejects.toBe(programmingError)
  })
})

describe("applyUpdateAndReconnect", () => {
  it("hard-refreshes the dashboard after a verified update", () => {
    const location = { hash: "#settings:updates", reload: vi.fn() }
    const values = new Map<string, string>()
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    }

    reloadUpdatedDashboard(location, storage)

    expect(location.hash).toBe("#overview")
    expect(location.reload).toHaveBeenCalledTimes(1)
    expect(values.get(UPDATE_HANDOFF_STORAGE_KEY)).toBe("overview")
    expect(consumeUpdatedDashboardHandoff(storage)).toBe(true)
    expect(consumeUpdatedDashboardHandoff(storage)).toBe(false)
  })

  it("keeps a status-less historical job idle while preserving legacy reconnect behavior", () => {
    const legacyJob = { job_id: "legacy-job", restart_required: true }

    expect(isPendingUpdateJob({ status: undefined })).toBe(false)
    expect(requiresUpdateReconnect(legacyJob)).toBe(true)
  })

  it("keeps a restored dependency download active", () => {
    const downloadingJob = {
      job_id: "runtime-download-job",
      status: "downloading",
      version: "1.5.1",
      restart_required: true,
    }

    expect(isPendingUpdateJob(downloadingJob)).toBe(true)
    expect(requiresUpdateReconnect(downloadingJob)).toBe(true)
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

  it("validates a durable success response before reloading", async () => {
    const job = {
      job_id: "job-success",
      status: "success",
      version: "0.9.16",
      restart_required: false,
    }
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const fetchJob = vi.fn().mockResolvedValue(job)
    const verifyReady = vi.fn().mockResolvedValue(undefined)
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(job),
      fetchStatus,
      fetchJob,
      verifyReady,
      wait: vi.fn().mockResolvedValue(undefined),
      reload,
      maxAttempts: 2,
      intervalMs: 1,
    })).resolves.toBe(job)

    expect(fetchStatus).toHaveBeenCalledTimes(2)
    expect(fetchJob).toHaveBeenCalledTimes(2)
    expect(verifyReady).toHaveBeenCalledTimes(2)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("validates an exact durable current response before reloading", async () => {
    const job = {
      job_id: "browser-job-current",
      status: "current",
      version: "0.9.16",
      restart_required: false,
    }
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const fetchJob = vi.fn().mockResolvedValue(job)
    const verifyReady = vi.fn().mockResolvedValue(undefined)
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(job),
      fetchStatus,
      fetchJob,
      jobId: "browser-job-current",
      verifyReady,
      wait: vi.fn().mockResolvedValue(undefined),
      reload,
      maxAttempts: 2,
      intervalMs: 1,
    })).resolves.toBe(job)

    expect(fetchStatus).toHaveBeenCalledTimes(2)
    expect(fetchJob).toHaveBeenCalledTimes(2)
    expect(verifyReady).toHaveBeenCalledTimes(2)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("returns an exact image-required response without polling or reloading", async () => {
    const job = {
      job_id: "browser-job-image",
      status: "image_required",
      version: "0.9.16",
      restart_required: false,
      message: "Install the new container image to continue.",
    }
    const fetchStatus = vi.fn()
    const fetchJob = vi.fn()
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(job),
      fetchStatus,
      fetchJob,
      jobId: "browser-job-image",
      reload,
    })).resolves.toBe(job)

    expect(fetchStatus).not.toHaveBeenCalled()
    expect(fetchJob).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it("surfaces a terminal Apply failure without reconnect polling", async () => {
    const job = {
      job_id: "job-failed",
      status: "failed",
      restart_required: true,
      message: "Activation failed; the previous version was restored.",
    }
    const fetchStatus = vi.fn()
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(job),
      fetchStatus,
      reload,
    })).rejects.toMatchObject({
      name: "UpdateJobError",
      message: "Activation failed; the previous version was restored.",
    })
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

  it("follows the version selected by a retry response when the catalog target changed", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.17"))
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue({
        job_id: "retry-new-target",
        version: "0.9.17",
        status: "restarting",
        restart_required: true,
      }),
      fetchStatus,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 1,
      reload,
      requiredStableChecks: 1,
    })).resolves.toMatchObject({ version: "0.9.17" })

    expect(fetchStatus).toHaveBeenCalledTimes(1)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("preserves legacy hookless recovery when the Apply response disconnects", async () => {
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

  it("follows a preassigned durable job when the Apply response disconnects", async () => {
    const apply = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    const fetchStatus = vi.fn().mockResolvedValue(status("0.9.16"))
    const fetchJob = vi.fn().mockResolvedValue({
      job_id: "browser-job-1",
      status: "success",
      version: "0.9.16",
    })
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus,
      fetchJob,
      jobId: "browser-job-1",
      reload,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
      intervalMs: 1,
    })).resolves.toBeNull()

    expect(apply).toHaveBeenCalledTimes(1)
    expect(fetchJob).toHaveBeenCalledTimes(2)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("waits for a preassigned job to become readable after a lost Apply response", async () => {
    const apply = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    const durablePending = {
      job_id: "browser-job-persisting",
      status: "validating",
      version: "0.9.16",
    }
    const durableSuccess = {
      job_id: "browser-job-persisting",
      status: "success",
      version: "0.9.16",
    }
    const fetchJob = vi.fn()
      .mockRejectedValueOnce(new UpdateJobPendingError())
      .mockResolvedValueOnce(durablePending)
      .mockResolvedValue(durableSuccess)
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus: vi.fn().mockResolvedValue(status("0.9.16")),
      fetchJob,
      jobId: "browser-job-persisting",
      reload,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 4,
      intervalMs: 1,
    })).resolves.toBeNull()

    expect(apply).toHaveBeenCalledTimes(1)
    expect(fetchJob).toHaveBeenCalledTimes(4)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("retries a transient status read after an accepted Apply loses its response", async () => {
    const apply = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    const durablePending = {
      job_id: "browser-job-proxy",
      status: "validating",
      version: "0.9.16",
    }
    const durableSuccess = {
      job_id: "browser-job-proxy",
      status: "success",
      version: "0.9.16",
    }
    const fetchJob = vi.fn()
      .mockResolvedValueOnce(durablePending)
      .mockResolvedValueOnce(durablePending)
      .mockResolvedValue(durableSuccess)
    const fetchStatus = vi.fn()
      .mockRejectedValueOnce(new UpdateReconnectError("The proxy is restarting."))
      .mockResolvedValue(status("0.9.16"))
    const verifyReady = vi.fn().mockResolvedValue(undefined)
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus,
      fetchJob,
      jobId: "browser-job-proxy",
      verifyReady,
      reload,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 4,
      intervalMs: 1,
    })).resolves.toBeNull()

    expect(apply).toHaveBeenCalledTimes(1)
    expect(fetchJob).toHaveBeenCalledTimes(4)
    expect(fetchStatus).toHaveBeenCalledTimes(4)
    expect(verifyReady).toHaveBeenCalledTimes(2)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("follows a preassigned job when the Apply response is lost at the proxy", async () => {
    const proxyFailure = new UpdateReconnectError("The proxy lost the Apply response.")
    const apply = vi.fn().mockRejectedValue(proxyFailure)
    const durableSuccess = {
      job_id: "browser-job-post-proxy",
      status: "success",
      version: "0.9.16",
    }
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus: vi.fn().mockResolvedValue(status("0.9.16")),
      fetchJob: vi.fn().mockResolvedValue(durableSuccess),
      jobId: "browser-job-post-proxy",
      reload,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
      intervalMs: 1,
    })).resolves.toBeNull()

    expect(apply).toHaveBeenCalledTimes(1)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("keeps a proxy Apply error fatal without a preassigned job", async () => {
    const proxyFailure = new UpdateReconnectError("The proxy lost the Apply response.")
    const fetchStatus = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockRejectedValue(proxyFailure),
      fetchStatus,
      reload: vi.fn(),
    })).rejects.toBe(proxyFailure)

    expect(fetchStatus).not.toHaveBeenCalled()
  })

  it("surfaces a durable image-required result after a lost Apply response", async () => {
    const apply = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    const imageRequired = {
      job_id: "browser-job-image-lost",
      status: "image_required",
      version: "0.9.16",
      message: "Install the new container image to continue.",
    }
    const fetchStatus = vi.fn()
    const onJob = vi.fn()
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus,
      fetchJob: vi.fn().mockResolvedValue(imageRequired),
      jobId: "browser-job-image-lost",
      onJob,
      reload,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
      intervalMs: 1,
    })).rejects.toMatchObject({
      name: "UpdateJobError",
      message: "Install the new container image to continue.",
    })

    expect(apply).toHaveBeenCalledTimes(1)
    expect(fetchStatus).not.toHaveBeenCalled()
    expect(onJob).toHaveBeenCalledTimes(1)
    expect(onJob).toHaveBeenCalledWith(imageRequired)
    expect(reload).not.toHaveBeenCalled()
  })

  it("keeps a preassigned update pinned to the requested version", async () => {
    const foreignReply = {
      job_id: "browser-job-1",
      status: "image_required",
      version: "0.9.15",
      message: "An unrelated release needs a new image.",
      restart_required: false,
    }
    const durableJob = {
      job_id: "browser-job-1",
      status: "success",
      version: "0.9.16",
    }
    const fetchJob = vi.fn().mockResolvedValue(durableJob)
    const onJob = vi.fn()
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockResolvedValue(foreignReply),
      fetchStatus: vi.fn().mockResolvedValue(status("0.9.16")),
      fetchJob,
      jobId: "browser-job-1",
      onJob,
      reload,
      wait: vi.fn().mockResolvedValue(undefined),
      maxAttempts: 2,
      intervalMs: 1,
    })).resolves.toBe(foreignReply)

    expect(fetchJob).toHaveBeenCalledTimes(2)
    expect(onJob).toHaveBeenCalledTimes(2)
    expect(onJob).toHaveBeenCalledWith(durableJob)
    expect(onJob).not.toHaveBeenCalledWith(foreignReply)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("does not infer a modern update transaction after a lost Apply response", async () => {
    const fetchStatus = vi.fn()
    const fetchJob = vi.fn()
    const reload = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
      fetchStatus,
      fetchJob,
      reload,
    })).rejects.toMatchObject({
      name: "UpdateTransactionUnconfirmedError",
      message: expect.stringContaining("could not confirm which update transaction started"),
    })

    expect(fetchStatus).not.toHaveBeenCalled()
    expect(fetchJob).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
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

  it("cancels an abandoned reconnect without reloading or resubmitting Apply", async () => {
    const controller = new AbortController()
    const apply = vi.fn().mockResolvedValue({
      job_id: "job-1",
      status: "restarting",
      restart_required: true,
      version: "0.9.16",
    })
    const reload = vi.fn()

    const promise = applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus: vi.fn().mockResolvedValue(status("0.9.13")),
      fetchJob: vi.fn().mockResolvedValue({ job_id: "job-1", status: "restarting" }),
      wait: async () => controller.abort(),
      reload,
      maxAttempts: 3,
      intervalMs: 1,
      signal: controller.signal,
    })

    await expect(promise).rejects.toMatchObject({ name: "AbortError" })
    expect(apply).toHaveBeenCalledTimes(1)
    expect(reload).not.toHaveBeenCalled()
  })

  it("does not reload when Apply finishes after the reconnect owner is cancelled", async () => {
    const controller = new AbortController()
    let finishApply!: (job: {
      job_id: string
      status: string
      restart_required: boolean
      version: string
    }) => void
    const apply = vi.fn().mockImplementation(() => new Promise((resolve) => {
      finishApply = resolve
    }))
    const onJob = vi.fn()
    const reload = vi.fn()
    const promise = applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus: vi.fn(),
      onJob,
      reload,
      signal: controller.signal,
    })

    controller.abort()
    finishApply({
      job_id: "job-success",
      status: "success",
      restart_required: false,
      version: "0.9.16",
    })

    await expect(promise).rejects.toMatchObject({ name: "AbortError" })
    expect(apply).toHaveBeenCalledTimes(1)
    expect(onJob).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled()
  })

  it("does not start Apply when the reconnect owner is already cancelled", async () => {
    const controller = new AbortController()
    controller.abort()
    const apply = vi.fn()

    await expect(applyUpdateAndReconnect("0.9.16", {
      apply,
      fetchStatus: vi.fn(),
      reload: vi.fn(),
      signal: controller.signal,
    })).rejects.toMatchObject({ name: "AbortError" })

    expect(apply).not.toHaveBeenCalled()
  })
})
