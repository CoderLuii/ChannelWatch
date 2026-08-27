type RuntimeStatus = {
  current_version: string
  active_bundle?: { version?: string | null } | null
}

type ReconnectOptions<T extends RuntimeStatus> = {
  fetchStatus: () => Promise<T>
  wait?: (milliseconds: number) => Promise<void>
  maxAttempts?: number
  intervalMs?: number
}

type RestartJob = { restart_required?: boolean; status?: string | null }

const PENDING_UPDATE_JOB_STATUSES = new Set([
  "backing_up",
  "verifying",
  "applying",
  "restarting",
  "validating",
])

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

export async function waitForUpdatedRuntime<T extends RuntimeStatus>(
  targetVersion: string,
  {
    fetchStatus,
    wait = defaultWait,
    maxAttempts = 20,
    intervalMs = 1500,
  }: ReconnectOptions<T>,
): Promise<T> {
  const attempts = Math.max(1, maxAttempts)

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const status = await fetchStatus()
      if (isTargetRuntimeActive(status, targetVersion)) return status
    } catch {
      // A short disconnect is expected while the backend restarts.
    }

    if (attempt < attempts) await wait(intervalMs)
  }

  throw new Error(
    `ChannelWatch did not reconnect after applying v${normalizeVersion(targetVersion)}. `
    + "The update may still be finishing. Refresh the page, then check the current version before trying again.",
  )
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

  try {
    job = await options.apply(targetVersion)
    if (!requiresUpdateReconnect(job)) return job
  } catch (error) {
    if (options.isRejectedUpdate?.(error)) throw error
    const isRestartDisconnect = options.isRestartDisconnect
      ? options.isRestartDisconnect(error)
      : error instanceof TypeError && /fetch|network|load failed|connection/i.test(error.message)
    if (!isRestartDisconnect) throw error
    restartDisconnect = error
  }

  try {
    await waitForUpdatedRuntime(targetVersion, options)
  } catch (error) {
    if (restartDisconnect && error instanceof Error) {
      throw new Error(error.message, { cause: restartDisconnect })
    }
    throw error
  }
  options.reload()
  return job
}
