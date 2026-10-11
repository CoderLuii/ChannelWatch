export type RuntimeStatus = {
  current_version: string
  active_bundle?: { version?: string | null } | null
  last_job?: RestartJob | null
}

export type ReconnectOptions<T extends RuntimeStatus> = {
  fetchStatus: () => Promise<T>
  fetchJob?: (jobId: string) => Promise<RestartJob>
  jobId?: string
  onJob?: (job: RestartJob) => void
  signal?: AbortSignal
  verifyReady?: () => Promise<void>
  wait?: (milliseconds: number) => Promise<void>
  maxAttempts?: number
  intervalMs?: number
  requiredStableChecks?: number
}

export type RestartJob = {
  job_id?: string | null
  restart_required?: boolean
  status?: string | null
  phase?: string | null
  phase_budget_seconds?: number | null
  phase_deadline_at?: string | null
  version?: string | null
  message?: string | null
}

const PENDING_UPDATE_JOB_STATUSES = new Set([
  "backing_up",
  "downloading",
  "verifying",
  "applying",
  "restarting",
  "validating",
])

const FAILED_UPDATE_JOB_STATUSES = new Set([
  "failed",
  "rejected",
  "rollback",
  "rolled_back",
])

const SUCCESSFUL_UPDATE_JOB_STATUSES = new Set(["success", "current"])

export class UpdateJobError extends Error {
  readonly job: RestartJob

  constructor(job: RestartJob) {
    super(
      job.message?.trim()
      || "ChannelWatch could not finish the update. Check Update Center before trying again.",
    )
    this.name = "UpdateJobError"
    this.job = job
  }
}

export class UpdateReadinessError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options)
    this.name = "UpdateReadinessError"
  }
}

export class UpdateReconnectError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options)
    this.name = "UpdateReconnectError"
  }
}

export class UpdateJobPendingError extends Error {
  constructor(
    message = "The update job is not readable yet.",
    options?: ErrorOptions,
  ) {
    super(message, options)
    this.name = "UpdateJobPendingError"
  }
}

export class UpdateTransactionUnconfirmedError extends Error {
  constructor() {
    super(
      "ChannelWatch could not confirm which update transaction started after the connection closed. "
      + "Refresh Update Center and review the current job before trying again.",
    )
    this.name = "UpdateTransactionUnconfirmedError"
  }
}

export class UpdatePreparationTimeoutError extends Error {
  constructor(budgetSeconds: number) {
    super(`Runtime preparation exceeded the ${budgetSeconds}-second limit.`)
    this.name = "UpdatePreparationTimeoutError"
  }
}

export function isPendingUpdateJob(job: { status?: string | null }): boolean {
  // A durable job is active only when it names an explicit pending state.
  // Legacy records without a status remain history rather than keeping the UI
  // in a permanent polling state.
  if (job.status == null) return false
  return PENDING_UPDATE_JOB_STATUSES.has(job.status)
}

export function requiresUpdateReconnect(job: RestartJob): boolean {
  // Historical apply responses did not always expose status. Preserve their
  // restart/reconnect behavior without treating a persisted status-less job as
  // an active operation on a later page load.
  return Boolean(job.restart_required && (job.status == null || isPendingUpdateJob(job)))
}

type ApplyAndReconnectOptions<TStatus extends RuntimeStatus, TJob extends RestartJob> = ReconnectOptions<TStatus> & {
  apply: (version: string) => Promise<TJob>
  reload: () => void
  isRejectedUpdate?: (error: unknown) => boolean
  isRestartDisconnect?: (error: unknown) => boolean
}

type BrowserLocationForUpdate = {
  hash: string
  reload: () => void
}

type UpdateHandoffStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">

export const UPDATE_HANDOFF_STORAGE_KEY = "channelwatch:update-handoff"

export function consumeUpdatedDashboardHandoff(
  storage: UpdateHandoffStorage = window.sessionStorage,
): boolean {
  if (storage.getItem(UPDATE_HANDOFF_STORAGE_KEY) !== "overview") return false
  storage.removeItem(UPDATE_HANDOFF_STORAGE_KEY)
  return true
}

export function reloadUpdatedDashboard(
  location: BrowserLocationForUpdate = window.location,
  storage: UpdateHandoffStorage = window.sessionStorage,
): void {
  // Set the destination before the hard reload. This guarantees the freshly
  // activated frontend starts on Home instead of reconstructing the Settings
  // route that initiated the update.
  storage.setItem(UPDATE_HANDOFF_STORAGE_KEY, "overview")
  location.hash = "#overview"
  location.reload()
}

const defaultWait = (milliseconds: number) => new Promise<void>((resolve) => {
  window.setTimeout(resolve, milliseconds)
})

function normalizeVersion(version: string): string {
  return version.trim().replace(/^v/i, "")
}

function isTargetRuntimeActive(status: RuntimeStatus, targetVersion: string): boolean {
  const target = normalizeVersion(targetVersion)
  if (status.active_bundle?.version) {
    return normalizeVersion(status.active_bundle.version) === target
  }
  return normalizeVersion(status.current_version) === target
}

function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return
  if (signal.reason instanceof Error) throw signal.reason
  throw new DOMException("The update reconnect was cancelled.", "AbortError")
}

function isReconnectDisconnect(error: unknown): boolean {
  return error instanceof UpdateReconnectError
    || error instanceof TypeError
      && /fetch|network|load failed|connection|socket|offline|restart|disconnect/i.test(error.message)
}

function reconnectTimeoutError(targetVersion: string): Error {
  return new Error(
    `ChannelWatch did not reconnect after applying v${normalizeVersion(targetVersion)}. `
    + "The update may still be finishing. Refresh the page, then check the current version before trying again.",
  )
}

function abortReason(signal: AbortSignal): Error {
  if (signal.reason instanceof Error) return signal.reason
  return new DOMException("The update reconnect was cancelled.", "AbortError")
}

function waitWithinReconnect<T>(
  promise: Promise<T>,
  deadline: number,
  targetVersion: string,
  signal?: AbortSignal,
): Promise<T> {
  throwIfAborted(signal)

  return new Promise<T>((resolve, reject) => {
    let settled = false
    let timer: ReturnType<typeof globalThis.setTimeout>
    const cleanup = () => {
      globalThis.clearTimeout(timer)
      signal?.removeEventListener("abort", onAbort)
    }
    const resolveOnce = (value: T) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(value)
    }
    const rejectOnce = (error: unknown) => {
      if (settled) return
      settled = true
      cleanup()
      reject(error instanceof Error ? error : new Error(String(error)))
    }
    const onAbort = () => rejectOnce(abortReason(signal!))
    const remaining = Math.max(0, deadline - Date.now())
    timer = globalThis.setTimeout(
      () => rejectOnce(reconnectTimeoutError(targetVersion)),
      remaining,
    )

    signal?.addEventListener("abort", onAbort, { once: true })
    promise.then(resolveOnce, rejectOnce)
  })
}

function waitUntilSettledOrAborted<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  throwIfAborted(signal)
  if (!signal) return promise

  return new Promise<T>((resolve, reject) => {
    let settled = false
    const cleanup = () => signal.removeEventListener("abort", onAbort)
    const resolveOnce = (value: T) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(value)
    }
    const rejectOnce = (error: unknown) => {
      if (settled) return
      settled = true
      cleanup()
      reject(error instanceof Error ? error : new Error(String(error)))
    }
    const onAbort = () => rejectOnce(abortReason(signal))
    signal.addEventListener("abort", onAbort, { once: true })
    promise.then(resolveOnce, rejectOnce)
  })
}

function preparationBudget(job: RestartJob | null): { deadline: number; seconds: number } | null {
  const seconds = job?.phase_budget_seconds
  const deadline = Date.parse(job?.phase_deadline_at ?? "")
  if (!Number.isInteger(seconds) || (seconds ?? 0) <= 0 || !Number.isFinite(deadline)) return null
  return { deadline, seconds: seconds! }
}

function waitWithinPreparationBudget<T>(
  promise: Promise<T>,
  deadline: number,
  budgetSeconds: number,
  signal?: AbortSignal,
): Promise<T> {
  throwIfAborted(signal)

  return new Promise<T>((resolve, reject) => {
    let settled = false
    let timer: ReturnType<typeof globalThis.setTimeout>
    const cleanup = () => {
      globalThis.clearTimeout(timer)
      signal?.removeEventListener("abort", onAbort)
    }
    const resolveOnce = (value: T) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(value)
    }
    const rejectOnce = (error: unknown) => {
      if (settled) return
      settled = true
      cleanup()
      reject(error instanceof Error ? error : new Error(String(error)))
    }
    const onAbort = () => rejectOnce(abortReason(signal!))
    timer = globalThis.setTimeout(
      () => rejectOnce(new UpdatePreparationTimeoutError(budgetSeconds)),
      Math.max(0, deadline - Date.now()),
    )

    signal?.addEventListener("abort", onAbort, { once: true })
    promise.then(resolveOnce, rejectOnce)
  })
}

function matchingJob(
  job: RestartJob | null | undefined,
  jobId: string,
  targetVersion: string,
): RestartJob | null {
  if (job?.job_id?.trim() !== jobId) return null
  if (!job.version?.trim() || normalizeVersion(job.version) !== normalizeVersion(targetVersion)) return null
  return job
}

function observeJob(job: RestartJob | null, onJob?: (job: RestartJob) => void): RestartJob | null {
  if (!job) return null
  onJob?.(job)
  if (FAILED_UPDATE_JOB_STATUSES.has(job.status?.trim().toLowerCase() ?? "")) {
    throw new UpdateJobError(job)
  }
  return job
}

function throwIfImageRequired(job: RestartJob | null): void {
  if (job?.status?.trim().toLowerCase() === "image_required") {
    throw new UpdateJobError(job)
  }
}

export async function waitForUpdatedRuntime<T extends RuntimeStatus>(
  targetVersion: string,
  {
    fetchStatus,
    fetchJob,
    jobId,
    onJob,
    signal,
    wait = defaultWait,
    maxAttempts = 120,
    intervalMs = 1500,
    verifyReady,
    requiredStableChecks = 2,
  }: ReconnectOptions<T>,
): Promise<T> {
  const attempts = Math.max(1, maxAttempts)
  const stableChecksRequired = Math.max(1, requiredStableChecks)
  const trackedJobId = jobId?.trim() || null
  const reconnectWindowMs = Math.max(1, attempts * Math.max(1, intervalMs))
  let deadline: number | null = Date.now() + reconnectWindowMs
  let activationAttempts = 0
  let preparingDependencies = false
  let preparationDeadline: number | null = null
  let preparationBudgetSeconds: number | null = null
  let stableChecks = 0

  while (activationAttempts < attempts) {
    throwIfAborted(signal)
    let chargedAttempt = false
    try {
      let job: RestartJob | null = null
      let jobLookupPending = false
      if (trackedJobId && fetchJob) {
        let fetchedJob: RestartJob | null = null
        try {
          const lookup = fetchJob(trackedJobId)
          fetchedJob = preparingDependencies
            ? preparationDeadline !== null && preparationBudgetSeconds !== null
              ? await waitWithinPreparationBudget(
                  lookup,
                  preparationDeadline,
                  preparationBudgetSeconds,
                  signal,
                )
              : await waitUntilSettledOrAborted(lookup, signal)
            : await waitWithinReconnect(lookup, deadline!, targetVersion, signal)
        } catch (error) {
          if (!(error instanceof UpdateJobPendingError)) throw error
          jobLookupPending = true
          stableChecks = 0
          if (!preparingDependencies) {
            activationAttempts += 1
            chargedAttempt = true
          }
        }
        if (!jobLookupPending) {
          job = observeJob(matchingJob(fetchedJob, trackedJobId, targetVersion), onJob)
          throwIfImageRequired(job)
          preparingDependencies = job?.status?.trim().toLowerCase() === "applying"
            && job.phase?.trim().toLowerCase() === "preparing"
          if (preparingDependencies) {
            const budget = preparationBudget(job)
            preparationDeadline = budget?.deadline ?? null
            preparationBudgetSeconds = budget?.seconds ?? null
            if (
              preparationDeadline !== null
              && preparationBudgetSeconds !== null
              && preparationDeadline <= Date.now()
            ) {
              throw new UpdatePreparationTimeoutError(preparationBudgetSeconds)
            }
            deadline = null
            stableChecks = 0
          } else if (deadline === null) {
            preparationDeadline = null
            preparationBudgetSeconds = null
            deadline = Date.now() + reconnectWindowMs
          }
        }
      }

      if (!jobLookupPending && !preparingDependencies) {
        activationAttempts += 1
        chargedAttempt = true
        const status = await waitWithinReconnect(fetchStatus(), deadline!, targetVersion, signal)
        if (trackedJobId && !fetchJob) {
          job = observeJob(matchingJob(status.last_job, trackedJobId, targetVersion), onJob)
          throwIfImageRequired(job)
        }

        const durableJobSucceeded = !trackedJobId || SUCCESSFUL_UPDATE_JOB_STATUSES.has(
          job?.status?.trim().toLowerCase() ?? "",
        )
        if (durableJobSucceeded && isTargetRuntimeActive(status, targetVersion)) {
          try {
            if (verifyReady) {
              await waitWithinReconnect(verifyReady(), deadline!, targetVersion, signal)
            }
            stableChecks += 1
            if (stableChecks >= stableChecksRequired) return status
          } catch (error) {
            if (error instanceof UpdateReadinessError || isReconnectDisconnect(error)) {
              stableChecks = 0
            } else {
              throw error
            }
          }
        } else {
          stableChecks = 0
        }
      }
    } catch (error) {
      if (error instanceof UpdateJobError || !isReconnectDisconnect(error)) throw error
      // A short disconnect is expected while the backend restarts.
      stableChecks = 0
      if (!preparingDependencies && !chargedAttempt) activationAttempts += 1
    }

    if (preparingDependencies || activationAttempts < attempts) {
      const pause = wait(intervalMs)
      if (preparingDependencies) {
        if (preparationDeadline !== null && preparationBudgetSeconds !== null) {
          await waitWithinPreparationBudget(
            pause,
            preparationDeadline,
            preparationBudgetSeconds,
            signal,
          )
        } else {
          await waitUntilSettledOrAborted(pause, signal)
        }
      } else {
        await waitWithinReconnect(pause, deadline!, targetVersion, signal)
      }
      throwIfAborted(signal)
    }
  }

  throw reconnectTimeoutError(targetVersion)
}

export async function applyUpdateAndReconnect<
  TStatus extends RuntimeStatus,
  TJob extends RestartJob,
>(
  targetVersion: string,
  options: ApplyAndReconnectOptions<TStatus, TJob>,
): Promise<TJob | null> {
  let job: TJob | null = null
  let restartDisconnect: unknown

  throwIfAborted(options.signal)
  try {
    job = await options.apply(targetVersion)
    throwIfAborted(options.signal)
    const expectedJobId = options.jobId?.trim()
    const postedJob = expectedJobId
      ? matchingJob(job, expectedJobId, targetVersion)
      : job
    observeJob(postedJob, options.onJob)
    if (postedJob?.status?.trim().toLowerCase() === "image_required") return job
  } catch (error) {
    if (options.isRejectedUpdate?.(error)) throw error
    const isRestartDisconnect = options.isRestartDisconnect
      ? options.isRestartDisconnect(error)
      : error instanceof UpdateReconnectError
        ? Boolean(options.jobId?.trim())
        : error instanceof TypeError && /fetch|network|load failed|connection/i.test(error.message)
    if (!isRestartDisconnect) throw error
    restartDisconnect = error
  }

  const selectedVersion = options.jobId?.trim()
    ? targetVersion
    : job?.version?.trim() || targetVersion
  const jobId = options.jobId?.trim() || (options.fetchJob ? job?.job_id?.trim() : undefined)
  if (restartDisconnect && options.fetchJob && !jobId) {
    throw new UpdateTransactionUnconfirmedError()
  }
  const shouldValidate = Boolean(jobId)
    || job == null
    || requiresUpdateReconnect(job)
  if (!shouldValidate) {
    if (job?.status === "success") {
      throwIfAborted(options.signal)
      options.reload()
    }
    return job
  }

  try {
    await waitForUpdatedRuntime(selectedVersion, { ...options, jobId })
  } catch (error) {
    if (
      restartDisconnect
      && error instanceof Error
      && !(error instanceof UpdateJobError)
      && error.name !== "AbortError"
    ) {
      throw new Error(error.message, { cause: restartDisconnect })
    }
    throw error
  }
  throwIfAborted(options.signal)
  options.reload()
  return job
}
