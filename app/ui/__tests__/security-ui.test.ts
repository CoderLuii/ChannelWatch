import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { SecurityModeBadge, SecuritySectionSummary } from "@/components/settings/security-section"
import type { SecurityStatus } from "@/lib/types"


const baseStatus: SecurityStatus = {
  persisted_mode: null,
  configured_mode: "api_key",
  effective_mode: "api_key",
  setup_required: false,
  runtime_auth_override_active: false,
  security_mode: "API_KEY_ONLY",
  auth_disabled: false,
  rbac_enabled: false,
  api_key_configured: true,
  api_key_fallback_active: false,
  session_auth_available: false,
  session_setup_required: false,
  encrypted_dvr_api_keys_at_rest: true,
  encryption_key_path: "/config/encryption.key",
  feeds: {
    implemented: true,
    ics_enabled: false,
    rss_enabled: false,
    atom_enabled: false,
  },
}

describe("SecurityModeBadge", () => {
  it("renders the configured mode label", () => {
    const html = renderToStaticMarkup(
      React.createElement(SecurityModeBadge, {
        status: {
          ...baseStatus,
          security_mode: "RBAC_ONLY",
          rbac_enabled: true,
          session_auth_available: true,
          api_key_configured: false,
        },
      }),
    )

    expect(html).toContain("Security: RBAC only")
  })

  it("keeps the configured mode badge during a runtime override", () => {
    const html = renderToStaticMarkup(
      React.createElement(SecurityModeBadge, {
        status: {
          ...baseStatus,
          configured_mode: "api_key",
          auth_disabled: true,
          runtime_auth_override_active: true,
        },
      }),
    )

    expect(html).toContain("Security: Legacy API key mode")
  })
})

describe("SecuritySectionSummary", () => {
  it("shows the break-glass warning as a temporary runtime override", () => {
    const html = renderToStaticMarkup(
      React.createElement(SecuritySectionSummary, {
        status: {
          ...baseStatus,
          configured_mode: "rbac",
          effective_mode: "none",
          runtime_auth_override_active: true,
          security_mode: "RBAC_ONLY",
          auth_disabled: true,
          rbac_enabled: true,
          api_key_configured: false,
          session_auth_available: true,
        },
      }),
    )

    expect(html).toContain("Temporary runtime auth bypass is active")
    expect(html).toContain("Configured mode remains: RBAC only")
    expect(html).toContain("does not rewrite the saved auth mode")
  })

  it("shows the fallback warning when RBAC still allows the shared API key", () => {
    const html = renderToStaticMarkup(
      React.createElement(SecuritySectionSummary, {
        status: {
          ...baseStatus,
          security_mode: "RBAC_WITH_API_KEY_FALLBACK",
          rbac_enabled: true,
          session_auth_available: true,
          session_setup_required: true,
          api_key_fallback_active: true,
        },
      }),
    )

    expect(html).toContain("Legacy API key fallback is still active")
    expect(html).toContain("older shared API key path still works")
    expect(html).not.toContain("ICS and RSS feeds are not implemented yet")
  })

  it("describes api-key-only mode as legacy compatibility", () => {
    const html = renderToStaticMarkup(
      React.createElement(SecuritySectionSummary, {
        status: {
          ...baseStatus,
          security_mode: "API_KEY_ONLY",
          rbac_enabled: false,
          api_key_configured: true,
        },
      }),
    )

    expect(html).toContain("Legacy API key mode")
    expect(html).toContain("This is a legacy install that still relies on a shared API key")
  })

  it("keeps persisted no-auth copy distinct from the runtime override banner", () => {
    const html = renderToStaticMarkup(
      React.createElement(SecuritySectionSummary, {
        status: {
          ...baseStatus,
          persisted_mode: "none",
          configured_mode: "none",
          effective_mode: "none",
          security_mode: "NO_AUTH",
          auth_disabled: true,
          api_key_configured: false,
        },
      }),
    )

    expect(html).toContain("No authentication")
    expect(html).not.toContain("Temporary runtime auth bypass is active")
    expect(html).not.toContain("Temporary break-glass override")
  })
})
