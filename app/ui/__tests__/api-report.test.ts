import { afterEach, describe, expect, it, vi } from "vitest"

import {
  ApiError,
  createReportSupportCode,
  downloadOfflineReportPackage,
  fetchReportConfig,
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
  const encoded = supportCode.replace("CW-REPORT-v1-", "")
  const padded = encoded.padEnd(encoded.length + ((4 - (encoded.length % 4)) % 4), "=")
  return JSON.parse(atob(padded.replace(/-/g, "+").replace(/_/g, "/")))
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
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await submitReport("https://channelwatch.coderluii.dev/api/reports", payload)

    const [, options] = fetchMock.mock.calls[0]
    expect(options.credentials).toBe("omit")
    expect(options.headers).toEqual({ "Content-Type": "application/json" })
    const body = JSON.parse(String(options.body))
    expect(body).toEqual({ support_code: expect.stringMatching(/^CW-REPORT-v1-/) })
    expect(decodeSupportCode(body.support_code)).toMatchObject({
      schema: 1,
      source: "channelwatch",
      report: { email: "viewer@example.com" },
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
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    const screenshot = new File(["image-bytes"], "screen.png", { type: "image/png" })

    await submitReport("https://channelwatch.coderluii.dev/api/reports", payload, {
      screenshots: [screenshot],
    })

    const [, options] = fetchMock.mock.calls[0]
    expect(options.credentials).toBe("omit")
    expect(options.headers).toEqual({})
    expect(options.body).toBeInstanceOf(FormData)
    const formData = options.body as FormData
    expect(formData.get("payload")).toBeNull()
    expect(String(formData.get("support_code"))).toMatch(/^CW-REPORT-v1-/)
    expect(formData.getAll("screenshots")).toHaveLength(1)
  })

  it("creates a portable support code without contacting the network", () => {
    const supportCode = createReportSupportCode(payload)
    const decoded = decodeSupportCode(supportCode)

    expect(decoded).toMatchObject({
      schema: 1,
      source: "channelwatch",
      report: {
        summary: "Active Streams shows a stream",
        email: "viewer@example.com",
        diagnostics: {
          channelwatch_version: "0.9.3",
        },
      },
    })
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

    const result = await downloadOfflineReportPackage(payload, {
      screenshots: [screenshot],
      debugBundle,
    })

    expect(result.type).toBe("application/zip")
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/v1/support/offline-package")
    expect(options.method).toBe("POST")
    expect(options.credentials).toBe("same-origin")
    expect(options.headers).toEqual({ "X-CSRF-Token": "csrf-package" })
    expect(options.body).toBeInstanceOf(FormData)
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
