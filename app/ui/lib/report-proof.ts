export interface ReportChallenge {
  version?: number
  nonce: string
  expires_at: number
  route_class: "in_app"
  difficulty: number
  key_id: string
  signature: string
}

export interface ReportChallengeProof extends ReportChallenge {
  solution: string
}

const roundConstants = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
])

const rotateRight = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits))

export function sha256(input: Uint8Array): Uint8Array {
  const length = input.length
  const paddedLength = Math.ceil((length + 9) / 64) * 64
  const bytes = new Uint8Array(paddedLength)
  bytes.set(input)
  bytes[length] = 0x80
  const bitLength = length * 8
  const view = new DataView(bytes.buffer)
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000), false)
  view.setUint32(paddedLength - 4, bitLength >>> 0, false)
  const state = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19])
  const words = new Uint32Array(64)
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4, false)
    for (let index = 16; index < 64; index += 1) {
      const a = words[index - 15]
      const b = words[index - 2]
      const s0 = rotateRight(a, 7) ^ rotateRight(a, 18) ^ (a >>> 3)
      const s1 = rotateRight(b, 17) ^ rotateRight(b, 19) ^ (b >>> 10)
      words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0
    }
    let [a, b, c, d, e, f, g, h] = state
    for (let index = 0; index < 64; index += 1) {
      const s1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25)
      const choice = (e & f) ^ (~e & g)
      const temp1 = (h + s1 + choice + roundConstants[index] + words[index]) >>> 0
      const s0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22)
      const majority = (a & b) ^ (a & c) ^ (b & c)
      const temp2 = (s0 + majority) >>> 0
      h = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0
    }
    state[0] = (state[0] + a) >>> 0; state[1] = (state[1] + b) >>> 0
    state[2] = (state[2] + c) >>> 0; state[3] = (state[3] + d) >>> 0
    state[4] = (state[4] + e) >>> 0; state[5] = (state[5] + f) >>> 0
    state[6] = (state[6] + g) >>> 0; state[7] = (state[7] + h) >>> 0
  }
  const output = new Uint8Array(32)
  const outputView = new DataView(output.buffer)
  state.forEach((value, index) => outputView.setUint32(index * 4, value, false))
  return output
}

function hasLeadingZeroBits(bytes: Uint8Array, difficulty: number): boolean {
  const wholeBytes = Math.floor(difficulty / 8)
  const remainingBits = difficulty % 8
  for (let index = 0; index < wholeBytes; index += 1) if (bytes[index] !== 0) return false
  return remainingBits === 0 || (bytes[wholeBytes] & (0xff << (8 - remainingBits))) === 0
}

export interface ReportChallengeSolveOptions {
  signal?: AbortSignal
  onProgress?: (attempts: number) => void
  deadlineMs?: number
  yieldEvery?: number
}

export const DEFAULT_REPORT_PROOF_DEADLINE_MS = 30000

function validateChallenge(challenge: ReportChallenge): void {
  if (
    (challenge.version !== undefined && challenge.version !== 1) ||
    challenge.route_class !== "in_app" ||
    !challenge.nonce ||
    !challenge.key_id ||
    !challenge.signature ||
    !Number.isFinite(challenge.expires_at) ||
    challenge.expires_at <= Date.now() ||
    !Number.isInteger(challenge.difficulty) ||
    challenge.difficulty < 0 ||
    challenge.difficulty > 24
  ) {
    throw new Error("The reporting service returned an invalid secure challenge.")
  }
}

export async function solveReportChallengeInline(
  challenge: ReportChallenge,
  options: ReportChallengeSolveOptions = {},
): Promise<ReportChallengeProof> {
  validateChallenge(challenge)
  const started = performance.now()
  const deadlineMs = Number.isFinite(options.deadlineMs) && Number(options.deadlineMs) >= 0
    ? Number(options.deadlineMs)
    : DEFAULT_REPORT_PROOF_DEADLINE_MS
  const yieldEvery = Number.isInteger(options.yieldEvery) && Number(options.yieldEvery) > 0
    ? Number(options.yieldEvery)
    : 256
  const encoder = new TextEncoder()
  let lastProgressAt = -Infinity
  for (let attempt = 0; attempt < Number.MAX_SAFE_INTEGER; attempt += 1) {
    if (options.signal?.aborted) throw new DOMException("Submission preparation was cancelled.", "AbortError")
    const solution = String(attempt)
    if (hasLeadingZeroBits(sha256(encoder.encode(`${challenge.nonce}.${solution}`)), challenge.difficulty)) {
      return { ...challenge, solution }
    }
    if ((attempt + 1) % yieldEvery === 0) {
      const now = performance.now()
      if (now - lastProgressAt >= 250) {
        options.onProgress?.(attempt + 1)
        lastProgressAt = now
      }
      if (now - started >= deadlineMs) throw new Error("Secure report preparation timed out. Your report and attachments are still here; retry, use the upload portal, or download the offline package.")
      await new Promise<void>((resolve) => setTimeout(resolve, 0))
    }
  }
  throw new Error("Secure report preparation could not be completed.")
}

export async function solveReportChallenge(
  challenge: ReportChallenge,
  options: ReportChallengeSolveOptions = {},
): Promise<ReportChallengeProof> {
  validateChallenge(challenge)
  if (typeof Worker === "undefined") {
    return solveReportChallengeInline(challenge, options)
  }

  return new Promise<ReportChallengeProof>((resolve, reject) => {
    const worker = new Worker(new URL("./report-proof.worker.js", import.meta.url), { type: "module" })
    let settled = false

    const finish = (callback: () => void) => {
      if (settled) return
      settled = true
      options.signal?.removeEventListener("abort", onAbort)
      worker.terminate()
      callback()
    }
    const onAbort = () => finish(() => reject(new DOMException("Submission preparation was cancelled.", "AbortError")))

    if (options.signal?.aborted) {
      onAbort()
      return
    }
    options.signal?.addEventListener("abort", onAbort, { once: true })
    worker.onmessage = (event: MessageEvent<{ type: string; attempts?: number; proof?: ReportChallengeProof; message?: string }>) => {
      if (event.data.type === "progress" && typeof event.data.attempts === "number") {
        options.onProgress?.(event.data.attempts)
      } else if (event.data.type === "success" && event.data.proof) {
        finish(() => resolve(event.data.proof as ReportChallengeProof))
      } else if (event.data.type === "error") {
        finish(() => reject(new Error(event.data.message || "Secure report preparation failed.")))
      }
    }
    worker.onerror = () => finish(() => reject(new Error("Secure report preparation worker failed.")))
    const deadlineMs = Number.isFinite(options.deadlineMs) && Number(options.deadlineMs) >= 0
      ? Number(options.deadlineMs)
      : DEFAULT_REPORT_PROOF_DEADLINE_MS
    const yieldEvery = Number.isInteger(options.yieldEvery) && Number(options.yieldEvery) > 0
      ? Number(options.yieldEvery)
      : 256
    worker.postMessage({
      challenge,
      deadlineMs,
      yieldEvery,
    })
  })
}

export function encodeReportChallengeProof(proof: ReportChallengeProof): string {
  const bytes = new TextEncoder().encode(JSON.stringify(proof))
  let binary = ""
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/u, "")
}
