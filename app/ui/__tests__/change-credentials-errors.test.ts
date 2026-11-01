import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError, SessionRequiredError, changeCredentials } from "@/lib/api"
import { ErrorCode } from "@/lib/error-catalog"
import { t } from "@/lib/i18n"

afterEach(() => vi.unstubAllGlobals())

function errorResponse(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ detail: { code, message, remediation: null } }), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("changeCredentials errors", () => {
  it("reports a wrong current password without asking the user to sign in again", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(401, ErrorCode.AUTH_CREDENTIALS_INVALID, "Invalid credentials.")),
    )
    const failure = await changeCredentials("wrong", "admin", "next-password").catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect(failure).not.toBeInstanceOf(SessionRequiredError)
    expect((failure as ApiError).message).toBe(t("security.currentPasswordIncorrect"))
  })

  it("treats an expired session as a sign-in requirement", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(401, ErrorCode.AUTH_UNAUTHENTICATED, "Not authenticated.")),
    )
    const failure = await changeCredentials("current", "admin", "next-password").catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(SessionRequiredError)
    expect((failure as SessionRequiredError).message).toBe(t("security.sessionExpired"))
  })

  it("keeps the structured payload for other failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(403, ErrorCode.AUTH_CSRF_INVALID, "CSRF token missing or invalid.")),
    )
    const failure = await changeCredentials("current", "admin", "next-password").catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect((failure as ApiError).payload.code).toBe(ErrorCode.AUTH_CSRF_INVALID)
  })
})
