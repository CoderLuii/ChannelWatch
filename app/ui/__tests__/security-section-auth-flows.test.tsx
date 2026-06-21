import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

import { getSecuritySectionViewState, isLegacyApiKeyEffectiveMode } from "@/components/settings/security-section"
import type { SecurityStatus } from "@/lib/types"

const __dirname = dirname(fileURLToPath(import.meta.url))

function srcFile(rel: string): string {
  return readFileSync(resolve(__dirname, rel), "utf8")
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

describe("Security section auth flows", () => {
  it("noauth hides credential fields until secure login is selected", () => {
    const persistedNoAuthStatus: SecurityStatus = {
      ...baseSecurityStatus,
      persisted_mode: "none",
      configured_mode: "none",
      effective_mode: "none",
      rbac_enabled: false,
      session_auth_available: false,
      security_mode: "NO_AUTH",
      auth_disabled: true,
    }

    const compactState = getSecuritySectionViewState({
      status: persistedNoAuthStatus,
      setupRequired: false,
      authenticated: false,
      authMode: "none",
    })

    expect(compactState.persistedNoAuth).toBe(true)
    expect(compactState.canBootstrapSecureLogin).toBe(true)
    expect(compactState.showCompactNoAuthWarning).toBe(true)
    expect(compactState.showCreateLoginAction).toBe(true)
    expect(compactState.showBootstrapCredentials).toBe(false)

    const secureLoginState = getSecuritySectionViewState({
      status: persistedNoAuthStatus,
      setupRequired: false,
      authenticated: false,
      authMode: "rbac",
    })

    expect(secureLoginState.showCreateLoginAction).toBe(false)
    expect(secureLoginState.showBootstrapCredentials).toBe(true)

    const src = srcFile("../components/settings/security-section.tsx")
    expect(src).toContain('data-testid="security-auth-mode-select"')
    expect(src).toContain('data-testid="security-noauth-warning"')
    expect(src).toContain('data-testid="security-create-login-btn"')
    expect(src).toContain('data-testid="security-bootstrap-username"')
    expect(src).toContain('data-testid="security-bootstrap-password"')
  })

  it("legacy badge is restricted to api key states", () => {
    expect(isLegacyApiKeyEffectiveMode({
      security_mode: "API_KEY_ONLY",
      effective_mode: "api_key",
    })).toBe(true)

    expect(isLegacyApiKeyEffectiveMode({
      security_mode: "API_KEY_ONLY",
      effective_mode: "rbac",
    })).toBe(false)

    expect(isLegacyApiKeyEffectiveMode({
      security_mode: "RBAC_ONLY",
      effective_mode: "rbac",
    })).toBe(false)

    expect(isLegacyApiKeyEffectiveMode({
      security_mode: "NO_AUTH",
      effective_mode: "none",
    })).toBe(false)

    const src = srcFile("../components/settings/security-section.tsx")
    expect(src).toContain('data-testid="security-runtime-override-banner"')
    expect(src).toContain('status.security_mode === "API_KEY_ONLY" && !isLegacyApiKeyEffectiveMode(status)')
  })

  it("runtime override keeps the warning banner distinct from persisted no-auth controls", () => {
    const runtimeOverrideStatus: SecurityStatus = {
      ...baseSecurityStatus,
      effective_mode: "none",
      runtime_auth_override_active: true,
      auth_disabled: true,
    }

    const state = getSecuritySectionViewState({
      status: runtimeOverrideStatus,
      setupRequired: false,
      authenticated: false,
      authMode: "rbac",
    })

    expect(state.persistedNoAuth).toBe(false)
    expect(state.showRuntimeOverrideBanner).toBe(true)
    expect(state.showCompactNoAuthWarning).toBe(false)
    expect(state.showCreateLoginAction).toBe(false)
    expect(state.showBootstrapCredentials).toBe(false)
  })
})
