import { afterEach, describe, expect, it, vi } from "vitest"

import {
  ApiError,
  checkReportStatus,
  createReportDraft,
  downloadOfflineReportPackage,
  fetchReportConfig,
  retryPrivateReport,
  submitReport,
  type ReportProblemPayload,
} from "@/lib/api"

function installBrowserAuth(csrf = "csrf-token") {
  vi.stubGlobal("window", {})
  vi.stubGlobal("sessionStorage", {
    getItem: vi.fn((key: string) => (key === "cw_csrf_token" ? csrf : null)),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  })
}

const payload: ReportProblemPayload = {
  summary: "Active Streams shows a stream",
  expected: "Activity should appear",
  getchannels_username: "Matthew_Crommert",
  github_username: "CoderLuii",
  email: "viewer@example.com",
  diagnostics: {
    channelwatch_version: "0.9.3",
    dvr_count: 1,
    connected_dvr_count: 1,
    core_status: "Running",
    monitoring_statuses: ["healthy: 1"],
    notification_providers: ["Pushover"],
    feature_toggles: {
      channel_watching: true,
      vod_watching: false,
      disk_space: true,
      recording_events: true,
      stream_counter: false,
    },
  },
}

function decodeSupportCode(supportCode: string) {
  const encoded = supportCode.replace(/^CW-REPORT-v[12]-/, "")
  const padded = encoded.padEnd(encoded.length + ((4 - (encoded.length % 4)) % 4), "=")
  return JSON.parse(atob(padded.replace(/-/g, "+").replace(/_/g, "/")))
}

function challengeResponse() {
  return new Response(JSON.stringify({
    nonce: "test-nonce",
    expires_at: Date.now() + 60_000,
    route_class: "in_app",
    difficulty: 0,
    key_id: "current",
    signature: "test-signature",
  }), { status: 200, headers: { "Content-Type": "application/json" } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("support report API helpers", () => {
  it("loads report config with app auth headers", async () => {
    installBrowserAuth("csrf-report")
    const responseBody = {
      mode: "dry-run",
      endpoint: "/api/v1/support/report-dry-run",
      portal_url: "https://channelwatch.coderluii.dev/report",
      max_bytes: 262144,
      attachments_enabled: true,
      max_attachment_bytes: 8388608,
      max_total_attachment_bytes: 20971520,
      max_screenshot_count: 5,
      allowed_attachment_types: [
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
      ],
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    const result = await fetchReportConfig()

    expect(result).toEqual(responseBody)
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/support/report-config", {
      headers: { "X-CSRF-Token": "csrf-report" },
      credentials: "same-origin",
    })
  })

  it("submits local dry-run reports with JSON and app auth headers", async () => {
    installBrowserAuth("csrf-submit")
    const responseBody = {
      mode: "dry-run",
      status: "dry-run-complete",
      issue_title: "[In-App] Active Streams shows a stream",
      issue_body: "report body",
      email_subject: "ChannelWatch report: Active Streams shows a stream",
      email_body: "private report body",
      email_in_public_issue: false,
      attachments: [],
      attachment_total_bytes: 0,
      attachments_sent: false,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    const result = await submitReport("/api/v1/support/report-dry-run", payload)

    expect(result).toEqual(responseBody)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, options] = fetchMock.mock.calls[0]
    expect(options.method).toBe("POST")
    expect(options.credentials).toBe("same-origin")
    expect(options.headers).toEqual({
      "Content-Type": "application/json",
      "X-CSRF-Token": "csrf-submit",
    })
    expect(JSON.parse(options.body)).toMatchObject({ email: "viewer@example.com" })
  })

  it("submits local reports with multipart form data when attachments are present", async () => {
    installBrowserAuth("csrf-multipart")
    const responseBody = {
      mode: "dry-run",
      status: "dry-run-complete",
      issue_title: "[In-App] Active Streams shows a stream",
      issue_body: "report body",
      email_subject: "ChannelWatch report: Active Streams shows a stream",
      email_body: "private report body",
      email_in_public_issue: false,
      attachments: [
        {
          filename: "screen.png",
          content_type: "image/png",
          size_bytes: 12,
          kind: "screenshot",
          sha256: "a".repeat(64),
        },
      ],
      attachment_total_bytes: 12,
      attachments_sent: false,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    const screenshot = new File(["image-bytes"], "screen.png", { type: "image/png" })
    const debugBundle = new File(["zip-bytes"], "channelwatch_debug.zip", { type: "application/zip" })

    const result = await submitReport("/api/v1/support/report-dry-run", payload, {
      screenshots: [screenshot],
      debugBundle,
    })

    expect(result).toEqual(responseBody)
    const [, options] = fetchMock.mock.calls[0]
    expect(options.method).toBe("POST")
    expect(options.credentials).toBe("same-origin")
    expect(options.headers).toEqual({ "X-CSRF-Token": "csrf-multipart" })
    expect(options.body).toBeInstanceOf(FormData)
    const formData = options.body as FormData
    expect(JSON.parse(String(formData.get("payload")))).toMatchObject({ email: "viewer@example.com" })
    expect(formData.getAll("screenshots")).toHaveLength(1)
    expect(formData.get("debug_bundle")).toBeTruthy()
  })

  it("submits external reports with a support code gate instead of raw JSON", async () => {
    const responseBody = {
      mode: "email-test",
      status: "email-test-ready",
      issue_title: "[In-App] Active Streams shows a stream",
      issue_body: "report body",
      email_subject: "ChannelWatch report: Active Streams shows a stream",
      email_body: "private report body",
      email_in_public_issue: false,
      attachments: [],
      attachment_total_bytes: 0,
      attachments_sent: true,
    }
    const fetchMock = vi.fn().mockResolvedValueOnce(challengeResponse()).mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    const draft = createReportDraft(payload)
    await submitReport("https://channelwatch.coderluii.dev/api/reports", payload, {}, {
      supportCode: draft.supportCode,
    })

    const [, options] = fetchMock.mock.calls[1]
    expect(options.credentials).toBe("omit")
    expect(options.headers).toMatchObject({
      "Content-Type": "application/json",
      "X-ChannelWatch-In-App-Report": "1",
    })
    expect(options.headers["X-ChannelWatch-Report-Challenge"]).toBeTruthy()
    const body = JSON.parse(String(options.body))
    expect(body).toEqual({ support_code: draft.supportCode })
    expect(decodeSupportCode(body.support_code)).toMatchObject({
      schema: 2,
      report_id: draft.reportId,
      client: {
        channelwatch_version: "0.9.3",
        submission_source: "in-app",
      },
      report: { email: "viewer@example.com" },
    })
  })

  it("stops before report upload when challenge preparation fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: "REPORT_CHALLENGE_UNAVAILABLE", message: "Secure preparation unavailable." },
    }), { status: 503, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(submitReport("https://channelwatch.coderluii.dev/api/reports", payload))
      .rejects.toThrow("Secure preparation unavailable.")
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe("https://channelwatch.coderluii.dev/api/reports/challenge")
  })

  it("retries private delivery on the dedicated route with the same support code", async () => {
    const draft = createReportDraft(payload)
    const screenshot = new File(["image-bytes"], "same-screen.png", { type: "image/png" })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(challengeResponse())
      .mockResolvedValue(new Response(JSON.stringify({
        mode: "live", status: "private_delivery_exhausted", report_id: draft.reportId,
        detail: { code: "PRIVATE_DELIVERY_EXHAUSTED", message: "Private delivery retry limit reached" },
      }), { status: 409, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(retryPrivateReport(
      "https://channelwatch.coderluii.dev/api/reports",
      payload,
      { screenshots: [screenshot] },
      { supportCode: draft.supportCode },
    )).rejects.toThrow("Private delivery retry limit reached")
    expect(fetchMock.mock.calls[1][0]).toBe("https://channelwatch.coderluii.dev/api/reports/retry-private")
    const retryBody = fetchMock.mock.calls[1][1].body as FormData
    expect(retryBody.get("support_code")).toBe(draft.supportCode)
    expect(retryBody.getAll("screenshots")).toHaveLength(1)
    expect((retryBody.get("screenshots") as File).name).toBe("same-screen.png")
  })

  it.each([
    ["provider_confirmation_pending", 202],
    ["completed", 200],
  ])("checks report status by POST body without putting the code in the URL (%s)", async (status, httpStatus) => {
    const draft = createReportDraft(payload)
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      mode: "live", status, report_id: draft.reportId,
    }), { status: httpStatus, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(checkReportStatus(
      "https://channelwatch.coderluii.dev/api/reports",
      draft.supportCode,
    )).resolves.toMatchObject({ status, report_id: draft.reportId })
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe("https://channelwatch.coderluii.dev/api/reports/status")
    expect(String(url)).not.toContain(draft.supportCode)
    expect(options.method).toBe("POST")
    expect(JSON.parse(String(options.body))).toEqual({ support_code: draft.supportCode })
  })

  it("keeps status-check failures structured and retryable", async () => {
    const draft = createReportDraft(payload)
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: "REPORT_STATUS_UNAVAILABLE", message: "Status is temporarily unavailable." },
    }), { status: 503, headers: { "Content-Type": "application/json" } })))
    await expect(checkReportStatus(
      "https://channelwatch.coderluii.dev/api/reports",
      draft.supportCode,
    )).rejects.toThrow("Status is temporarily unavailable.")
  })

  it("does not send Turnstile tokens from the in-app external report flow", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(challengeResponse()).mockResolvedValue(
      new Response(
        JSON.stringify({
          mode: "live",
          status: "live-ready",
          issue_title: "[In-App] Active Streams shows a stream",
          issue_body: "report body",
          email_subject: "ChannelWatch report: Active Streams shows a stream",
          email_body: "private report body",
          email_in_public_issue: false,
          attachments: [],
          attachment_total_bytes: 0,
          attachments_sent: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    await submitReport("https://channelwatch.coderluii.dev/api/reports", {
      ...payload,
      turnstile_token: "turnstile-test-token",
    })

    const [, options] = fetchMock.mock.calls[1]
    const body = JSON.parse(String(options.body))
    expect(options.headers).toMatchObject({ "X-ChannelWatch-In-App-Report": "1" })
    expect(body).toEqual({ support_code: expect.stringMatching(/^CW-REPORT-v2-/) })
    expect(decodeSupportCode(body.support_code)).toMatchObject({
      report: { turnstile_token: null },
    })
  })

  it("submits external report attachments with a support code gate", async () => {
    const responseBody = {
      mode: "email-test",
      status: "email-test-ready",
      issue_title: "[In-App] Active Streams shows a stream",
      issue_body: "report body",
      email_subject: "ChannelWatch report: Active Streams shows a stream",
      email_body: "private report body",
      email_in_public_issue: false,
      attachments: [],
      attachment_total_bytes: 0,
      attachments_sent: true,
    }
    const fetchMock = vi.fn().mockResolvedValueOnce(challengeResponse()).mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    const screenshot = new File(["image-bytes"], "screen.png", { type: "image/png" })

    await submitReport(
      "https://channelwatch.coderluii.dev/api/reports",
      payload,
      {
        screenshots: [screenshot],
      },
    )

    const [, options] = fetchMock.mock.calls[1]
    expect(options.credentials).toBe("omit")
    expect(options.headers).toMatchObject({ "X-ChannelWatch-In-App-Report": "1" })
    expect(options.body).toBeInstanceOf(FormData)
    const formData = options.body as FormData
    expect(formData.get("payload")).toBeNull()
    expect(String(formData.get("support_code"))).toMatch(/^CW-REPORT-v2-/)
    expect(formData.get("turnstile_token")).toBeNull()
    expect(formData.getAll("screenshots")).toHaveLength(1)
  })

  it("creates a schema-2 portable support code without contacting the network", () => {
    const draft = createReportDraft(payload)
    const decoded = decodeSupportCode(draft.supportCode)

    expect(decoded).toMatchObject({
      schema: 2,
      report_id: draft.reportId,
      client: {
        channelwatch_version: "0.9.3",
        submission_source: "in-app",
      },
      report: {
        summary: "Active Streams shows a stream",
        email: "viewer@example.com",
        diagnostics: {
          channelwatch_version: "0.9.3",
        },
      },
    })
  })

  it("creates a standards-compliant report id when randomUUID is unavailable", () => {
    const originalCrypto = globalThis.crypto
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
        return bytes
      },
    })

    const draft = createReportDraft(payload)

    expect(draft.reportId).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f")
    vi.stubGlobal("crypto", originalCrypto)
  })

  it("fails clearly when Web Crypto is unavailable", () => {
    const originalCrypto = globalThis.crypto
    vi.stubGlobal("crypto", undefined)
    expect(() => createReportDraft(payload)).toThrow("secure random values")
    vi.stubGlobal("crypto", originalCrypto)
  })

  it("reuses one support code for retries of a finalized draft", async () => {
    const draft = createReportDraft(payload)
    const fetchMock = vi.fn().mockImplementation((url: string) => Promise.resolve(
      String(url).endsWith("/challenge") ? challengeResponse() : new Response(JSON.stringify({ mode: "live", status: "completed" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ))
    vi.stubGlobal("fetch", fetchMock)

    await submitReport("https://channelwatch.coderluii.dev/api/reports", payload, {}, {
      supportCode: draft.supportCode,
    })
    await submitReport("https://channelwatch.coderluii.dev/api/reports", payload, {}, {
      supportCode: draft.supportCode,
    })

    const submittedCodes = fetchMock.mock.calls.filter(([url]) => !String(url).endsWith("/challenge")).map(([, options]) =>
      JSON.parse(String(options.body)).support_code,
    )
    expect(submittedCodes).toEqual([draft.supportCode, draft.supportCode])
  })

  it("creates a new report id when a changed draft is finalized", () => {
    const first = createReportDraft(payload)
    const edited = createReportDraft({ ...payload, summary: "Edited summary" })

    expect(edited.reportId).not.toBe(first.reportId)
    expect(edited.supportCode).not.toBe(first.supportCode)
  })

  it("downloads offline packages with multipart form data and app auth headers", async () => {
    installBrowserAuth("csrf-package")
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Blob(["zip-bytes"], { type: "application/zip" }), {
        status: 200,
        headers: { "Content-Type": "application/zip" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    const screenshot = new File(["image-bytes"], "screen.png", { type: "image/png" })
    const debugBundle = new File(["zip-bytes"], "channelwatch_debug.zip", { type: "application/zip" })

    const draft = createReportDraft(payload)
    const result = await downloadOfflineReportPackage(payload, {
      screenshots: [screenshot],
      debugBundle,
    }, draft.supportCode)

    expect(result.type).toBe("application/zip")
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/v1/support/offline-package")
    expect(options.method).toBe("POST")
    expect(options.credentials).toBe("same-origin")
    expect(options.headers).toEqual({ "X-CSRF-Token": "csrf-package" })
    expect(options.body).toBeInstanceOf(FormData)
    expect((options.body as FormData).get("support_code")).toBe(draft.supportCode)
  })

  it("reports structured dry-run errors as ApiError", async () => {
    installBrowserAuth("csrf-error")
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "REPORT_INVALID",
            message: "Report payload must be valid JSON.",
          },
        }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    await expect(submitReport("/api/v1/support/report-dry-run", payload)).rejects.toMatchObject({
      name: "ApiError",
      message: "Report payload must be valid JSON.",
    } satisfies Partial<ApiError>)
  })
})
