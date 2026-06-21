import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

import { resolveDashboardBootstrapShell } from "@/components/dashboard"
import type { AuthSetupStatus, SecurityStatus } from "@/lib/types"

const __dirname = dirname(fileURLToPath(import.meta.url))

function srcFile(rel: string): string {
  return readFileSync(resolve(__dirname, rel), "utf8")
}

const baseSetupStatus: AuthSetupStatus = {
  persisted_mode: null,
  configured_mode: "rbac",
  effective_mode: "rbac",
  setup_required: false,
  runtime_auth_override_active: false,
  api_key_fallback_active: false,
  rbac_enabled: true,
  session_auth_available: true,
  session_setup_required: false,
  current_mode: "rbac",
  available_modes: ["rbac", "none"],
  needs_setup: false,
}

const baseSecurityStatus: SecurityStatus = {
  persisted_mode: "rbac",
  configured_mode: "rbac",
  effective_mode: "rbac",
  setup_required: false,
  runtime_auth_override_active: false,
  api_key_fallback_active: false,
  rbac_enabled: true,
  session_auth_available: true,
  session_setup_required: false,
  security_mode: "RBAC_ONLY",
  auth_disabled: false,
  api_key_configured: false,
  encrypted_dvr_api_keys_at_rest: true,
  encryption_key_path: "/config/encryption.key",
  feeds: {
    implemented: true,
    ics_enabled: false,
    rss_enabled: false,
    atom_enabled: false,
  },
}

describe("Dashboard auth bootstrap", () => {
  it("setup status drives correct shell", () => {
    const firstRunSetupStatus: AuthSetupStatus = {
      ...baseSetupStatus,
      configured_mode: "setup",
      effective_mode: "setup",
      current_mode: "setup",
      setup_required: true,
      needs_setup: true,
    }
    const firstRunSecurityStatus: SecurityStatus = {
      ...baseSecurityStatus,
      persisted_mode: null,
      configured_mode: "setup",
      effective_mode: "setup",
      setup_required: true,
      session_setup_required: true,
    }

    const secureBootstrapSetupStatus: AuthSetupStatus = {
      ...baseSetupStatus,
      persisted_mode: "none",
      configured_mode: "rbac",
      effective_mode: "rbac",
      current_mode: "rbac",
      setup_required: true,
      needs_setup: true,
    }
    const secureBootstrapSecurityStatus: SecurityStatus = {
      ...baseSecurityStatus,
      persisted_mode: "none",
      configured_mode: "rbac",
      effective_mode: "rbac",
      setup_required: true,
      session_setup_required: true,
    }

    const noAuthSetupStatus: AuthSetupStatus = {
      ...baseSetupStatus,
      persisted_mode: "none",
      configured_mode: "none",
      effective_mode: "none",
      current_mode: "none",
      rbac_enabled: false,
      session_auth_available: false,
    }
    const noAuthSecurityStatus: SecurityStatus = {
      ...baseSecurityStatus,
      persisted_mode: "none",
      configured_mode: "none",
      effective_mode: "none",
      rbac_enabled: false,
      session_auth_available: false,
      security_mode: "NO_AUTH",
      auth_disabled: true,
    }

    expect(resolveDashboardBootstrapShell(firstRunSetupStatus, firstRunSecurityStatus)).toBe("bootstrap")
    expect(resolveDashboardBootstrapShell(secureBootstrapSetupStatus, secureBootstrapSecurityStatus)).toBe("bootstrap")
    expect(resolveDashboardBootstrapShell(noAuthSetupStatus, noAuthSecurityStatus)).toBe("noauth")
    expect(resolveDashboardBootstrapShell(baseSetupStatus, baseSecurityStatus)).toBe("login")

    const apiKeySecurityStatus: SecurityStatus = {
      ...baseSecurityStatus,
      configured_mode: "api_key",
      effective_mode: "api_key",
      security_mode: "API_KEY_ONLY",
      rbac_enabled: false,
      session_auth_available: false,
      api_key_configured: true,
    }

    expect(resolveDashboardBootstrapShell(baseSetupStatus, apiKeySecurityStatus)).toBe("api_key")

    const src = srcFile("../components/dashboard.tsx")
    expect(src).toContain('data-testid="auth-bootstrap-shell"')
    expect(src).toContain('data-testid="auth-login-shell"')
    expect(src).toContain('data-testid="auth-noauth-shell"')
    expect(src).toContain('id="login-api-key"')
    expect(src).toContain("cacheApiKey(apiKeyInput.trim())")
    expect(src.indexOf("clearCachedAuthState()")).toBeGreaterThan(-1)
    expect(src.indexOf('loadSettings("noauth")')).toBeGreaterThan(src.indexOf("clearCachedAuthState()"))
  })

  it("stale whoami never suppresses secure bootstrap", () => {
    const staleWhoAmI = { authenticated: false, rbac_enabled: true }
    const secureBootstrapSetupStatus: AuthSetupStatus = {
      ...baseSetupStatus,
      persisted_mode: "none",
      configured_mode: "rbac",
      effective_mode: "rbac",
      current_mode: "rbac",
      setup_required: true,
      needs_setup: true,
    }
    const secureBootstrapSecurityStatus: SecurityStatus = {
      ...baseSecurityStatus,
      persisted_mode: "none",
      configured_mode: "rbac",
      effective_mode: "rbac",
      setup_required: true,
      session_setup_required: true,
    }

    expect(staleWhoAmI.authenticated).toBe(false)
    expect(resolveDashboardBootstrapShell(secureBootstrapSetupStatus, secureBootstrapSecurityStatus)).toBe("bootstrap")

    const src = srcFile("../components/dashboard.tsx")
    expect(src).not.toContain("const whoami = await fetchWhoAmI()")
    expect(src).not.toContain("if (whoami.authenticated)")
  })

  it("runtime overrides never masquerade as persisted no-auth access", () => {
    const runtimeNoAuthOverrideStatus: SecurityStatus = {
      ...baseSecurityStatus,
      configured_mode: "rbac",
      effective_mode: "none",
      runtime_auth_override_active: true,
      auth_disabled: true,
    }

    expect(resolveDashboardBootstrapShell(baseSetupStatus, runtimeNoAuthOverrideStatus)).toBe("login")
  })
})
