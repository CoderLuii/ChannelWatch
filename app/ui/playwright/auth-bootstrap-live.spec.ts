import { expect, test } from "@playwright/test"

const bootstrapUsername = "cwadmin"
const bootstrapPassword = "ChannelWatch!234"
const secureSetupUsername = "secureadmin"
const secureSetupPassword = "ChannelWatch!567"

function secureSetupBaseURL(baseURL: string | undefined) {
  if (!baseURL) {
    return undefined
  }

  return process.env.CW_E2E_SECURE_SETUP_BASE_URL || baseURL.replace(":8512", ":8514")
}

async function openSecuritySettings(
  page: import("@playwright/test").Page,
  baseURL?: string,
) {
  const target = baseURL ? `${baseURL}/#settings:security` : "/#settings:security"
  await page.goto(target)
  const skipButton = page.getByRole("button", { name: "Skip for now" })
  if (await skipButton.isVisible().catch(() => false)) {
    await skipButton.click()
  }
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible()
  await expect(page.getByRole("tab", { name: "Security" })).toHaveAttribute("data-state", "active")
}

async function logoutThroughBackend(page: import("@playwright/test").Page) {
  await page.evaluate(async () => {
    const csrfToken = window.sessionStorage.getItem("cw_csrf_token") || ""
    const response = await window.fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
    })

    if (!response.ok) {
      throw new Error(`Logout failed with status ${response.status}`)
    }

    window.sessionStorage.removeItem("cw_csrf_token")
  })
}

test("live auth bootstrap covers fresh secure setup", async ({ page, baseURL }) => {
  const secureBaseURL = secureSetupBaseURL(baseURL)
  test.skip(!secureBaseURL, "CW_E2E_SECURE_SETUP_BASE_URL must point at a running secure-setup backend")

  await page.goto(`${secureBaseURL}/#overview`)

  await expect(page.getByTestId("auth-bootstrap-shell")).toBeVisible()
  await page.getByLabel("Admin username").fill(secureSetupUsername)
  await page.getByLabel("Admin password").fill(secureSetupPassword)
  await page.getByRole("button", { name: "Finish setup" }).click()

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
  await openSecuritySettings(page, secureBaseURL)
  await expect(page.getByTestId("security-auth-mode-select")).toBeVisible()
  await expect(page.getByTestId("security-runtime-override-banner")).toHaveCount(0)
  await expect(page.getByTestId("security-create-login-btn")).toHaveCount(0)

  await logoutThroughBackend(page)
  await page.goto(`${secureBaseURL}/`)
  await expect(page.getByTestId("auth-login-shell")).toBeVisible()
  await page.getByLabel("Username").fill(secureSetupUsername)
  await page.getByLabel("Password").fill(secureSetupPassword)
  await page.getByRole("button", { name: "Sign in" }).click()
  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()
})

test("live auth bootstrap covers setup, no-auth reversal, logout, and login", async ({ page, baseURL }) => {
  test.skip(!baseURL, "CW_E2E_BASE_URL must point at a running ChannelWatch backend")

  await page.goto("/#overview")

  await expect(page.getByTestId("auth-bootstrap-shell")).toBeVisible()
  await page.getByTestId("auth-bootstrap-shell").getByRole("button", { name: "No authentication" }).click()
  await page.getByRole("button", { name: "Finish setup" }).click()

  await expect(page.getByTestId("auth-noauth-shell")).toBeVisible()
  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()

  await openSecuritySettings(page)
  await expect(page.getByTestId("security-auth-mode-select")).toBeVisible()
  await expect(page.getByTestId("security-noauth-warning")).toBeVisible()
  await expect(page.getByTestId("security-create-login-btn")).toBeVisible()
  await expect(page.getByTestId("security-bootstrap-username")).toHaveCount(0)
  await expect(page.getByTestId("security-bootstrap-password")).toHaveCount(0)
  await expect(page.getByTestId("security-runtime-override-banner")).toHaveCount(0)

  await page.getByTestId("security-create-login-btn").click()
  await expect(page.getByTestId("security-bootstrap-username")).toBeVisible()
  await expect(page.getByTestId("security-bootstrap-password")).toBeVisible()

  await page.getByTestId("security-bootstrap-username").fill(bootstrapUsername)
  await page.getByTestId("security-bootstrap-password").fill(bootstrapPassword)
  await page.getByRole("button", { name: "Create admin and enable secure login" }).click()

  await expect(page.getByText("Admin user created")).toBeVisible()
  await expect(page.getByTestId("security-noauth-warning")).toHaveCount(0)
  await expect(page.getByTestId("security-create-login-btn")).toHaveCount(0)
  await expect(page.getByTestId("security-runtime-override-banner")).toHaveCount(0)

  await logoutThroughBackend(page)
  await page.goto("/")
  await expect(page.getByTestId("auth-login-shell")).toBeVisible()

  await page.getByLabel("Username").fill(bootstrapUsername)
  await page.getByLabel("Password").fill(bootstrapPassword)
  await page.getByRole("button", { name: "Sign in" }).click()

  await expect(page.getByRole("heading", { name: "Dashboard Overview" })).toBeVisible()

  await openSecuritySettings(page)
  await expect(page.getByTestId("security-auth-mode-select")).toBeVisible()
  await expect(page.getByTestId("security-runtime-override-banner")).toHaveCount(0)
})
