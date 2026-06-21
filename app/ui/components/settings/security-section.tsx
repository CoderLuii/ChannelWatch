"use client"

import React from "react"
import { useEffect, useState } from "react"
import { AlertTriangle, Loader2, LockKeyhole, ShieldAlert, ShieldCheck, ShieldEllipsis } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { TabsContent } from "@/components/base/tabs"
import { changeCredentials, completeInitialSetup, fetchSecurityStatus, fetchSetupStatus, fetchWhoAmI } from "@/lib/api"
import { t } from "@/lib/i18n"
import type { AppSettings, AuthMode, SecurityMode, SecurityStatus } from "@/lib/types"

const modeCopy: Record<SecurityMode, { label: string; summary: string; tone: string }> = {
  NO_AUTH: {
    label: t("security.mode.noAuth.label"),
    summary: t("security.mode.noAuth.summary"),
    tone: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
  },
  API_KEY_ONLY: {
    label: t("security.mode.apiKeyOnly.label"),
    summary: t("security.mode.apiKeyOnly.summary"),
    tone: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  },
  RBAC_WITH_API_KEY_FALLBACK: {
    label: t("security.mode.rbacWithFallback.label"),
    summary: t("security.mode.rbacWithFallback.summary"),
    tone: "border-orange-500/30 bg-orange-500/10 text-orange-700 dark:text-orange-300",
  },
  RBAC_ONLY: {
    label: t("security.mode.rbacOnly.label"),
    summary: t("security.mode.rbacOnly.summary"),
    tone: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  },
}

function getSecurityModeCopy(status: SecurityStatus) {
  return modeCopy[status.security_mode]
}

export function isPersistedNoAuthMode(
  status: Pick<SecurityStatus, "persisted_mode" | "configured_mode" | "effective_mode" | "runtime_auth_override_active">,
) {
  return (
    status.persisted_mode === "none" &&
    !status.runtime_auth_override_active &&
    status.configured_mode === "none" &&
    status.effective_mode === "none"
  )
}

export function isLegacyApiKeyEffectiveMode(status: Pick<SecurityStatus, "security_mode" | "effective_mode">) {
  return status.security_mode === "API_KEY_ONLY" && status.effective_mode === "api_key"
}

export function getSecuritySectionViewState({
  status,
  setupRequired,
  authenticated,
  authMode,
}: {
  status: Pick<SecurityStatus, "persisted_mode" | "configured_mode" | "effective_mode" | "runtime_auth_override_active" | "security_mode"> | null
  setupRequired: boolean
  authenticated: boolean
  authMode: AuthMode
}) {
  const persistedNoAuth = status ? isPersistedNoAuthMode(status) : false
  const canBootstrapSecureLogin = !authenticated && (persistedNoAuth || setupRequired)
  const showBootstrapCredentials = canBootstrapSecureLogin && (!persistedNoAuth || authMode === "rbac")

  return {
    persistedNoAuth,
    canBootstrapSecureLogin,
    showRuntimeOverrideBanner: Boolean(status?.runtime_auth_override_active),
    showLegacyApiKeyBadge: Boolean(status && isLegacyApiKeyEffectiveMode(status)),
    showCompactNoAuthWarning: persistedNoAuth,
    showCreateLoginAction: persistedNoAuth && authMode !== "rbac",
    showBootstrapCredentials,
  }
}

function getSecurityModeIcon(status: SecurityStatus) {
  if (status.security_mode === "NO_AUTH") return ShieldAlert
  if (status.security_mode === "RBAC_ONLY") return ShieldCheck
  if (status.security_mode === "RBAC_WITH_API_KEY_FALLBACK") return ShieldEllipsis
  return LockKeyhole
}

export function SecurityModeBadge({ status, compact = false }: { status: SecurityStatus; compact?: boolean }) {
  if (status.security_mode === "API_KEY_ONLY" && !isLegacyApiKeyEffectiveMode(status)) {
    return null
  }

  const copy = getSecurityModeCopy(status)
  const Icon = getSecurityModeIcon(status)

  return (
    <Badge
      variant="outline"
      className={`gap-1.5 border ${copy.tone}`}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{compact ? copy.label : t("security.badge.withPrefix", { label: copy.label })}</span>
    </Badge>
  )
}

export function SecuritySectionSummary({ status }: { status: SecurityStatus }) {
  const copy = getSecurityModeCopy(status)
  const runtimeOverrideActive = status.runtime_auth_override_active

  return (
    <Card className="overflow-hidden border-blue-400/20 shadow-lg dark:shadow-blue-900/10">
      <div className="relative">
        <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-cyan-900/10 z-0" />
        <CardHeader className="relative z-10 border-b border-blue-200/10">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="space-y-1.5">
              <CardTitle className="flex items-center gap-2 text-xl">
                <ShieldCheck className="h-5 w-5 text-blue-400" />
                {t("security.title")}
              </CardTitle>
              <CardDescription>
                {t("security.description")}
              </CardDescription>
            </div>
            <SecurityModeBadge status={status} />
          </div>
        </CardHeader>
      </div>

      <CardContent className="space-y-4 pt-6">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-blue-400/15 bg-blue-500/5 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{t("security.currentMode")}</p>
            <p className="mt-2 text-base font-semibold">{runtimeOverrideActive ? t("security.runtimeOverrideLabel") : copy.label}</p>
            <p className="mt-2 text-sm text-muted-foreground">
              {runtimeOverrideActive ? t("security.runtimeOverrideSummary", { label: copy.label }) : copy.summary}
            </p>
            {runtimeOverrideActive ? (
              <p className="mt-3 text-xs text-muted-foreground">{t("security.runtimeOverrideConfiguredMode", { label: copy.label })}</p>
            ) : null}
          </div>
          <div className="rounded-xl border border-blue-400/15 bg-blue-500/5 p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{t("security.atRestProtection")}</p>
            <p className="mt-2 text-base font-semibold">{t("security.atRestTitle")}</p>
            <p className="mt-2 text-sm text-muted-foreground">
              {t("security.atRestDesc", { path: status.encryption_key_path })}
            </p>
          </div>
        </div>

        {runtimeOverrideActive && (
          <Alert variant="destructive" data-testid="security-runtime-override-banner">
            <ShieldAlert className="h-4 w-4" />
            <AlertTitle>{t("security.alert.authBypassedTitle")}</AlertTitle>
            <AlertDescription>
              <p className="text-sm">{t("security.alert.authBypassedDesc", { label: copy.label })}</p>
            </AlertDescription>
          </Alert>
        )}

        {!runtimeOverrideActive && status.api_key_fallback_active && (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>{t("security.alert.rbacFallbackTitle")}</AlertTitle>
            <AlertDescription>
              <p className="text-sm">{t("security.alert.rbacFallbackDesc")}</p>
            </AlertDescription>
          </Alert>
        )}

        {!runtimeOverrideActive && status.session_setup_required && (
          <Alert>
            <ShieldEllipsis className="h-4 w-4" />
            <AlertTitle>{t("security.alert.rbacSetupTitle")}</AlertTitle>
            <AlertDescription>
              <p className="text-sm">{t("security.alert.rbacSetupDesc")}</p>
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}

interface SecuritySettingsSectionProps {
  form: UseFormReturn<AppSettings>
}

export function SecuritySettingsSection({ form }: SecuritySettingsSectionProps) {
  const [status, setStatus] = useState<SecurityStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [whoami, setWhoAmI] = useState<{ authenticated: boolean; username?: string; role?: string } | null>(null)
  const [setupStatus, setSetupStatus] = useState<{ setup_required: boolean } | null>(null)
  const [credentialError, setCredentialError] = useState<string | null>(null)
  const [credentialSuccess, setCredentialSuccess] = useState<string | null>(null)
  const [isSavingCredentials, setIsSavingCredentials] = useState(false)
  const [currentPassword, setCurrentPassword] = useState("")
  const [newUsername, setNewUsername] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [setupUsername, setSetupUsername] = useState("")
  const [setupPassword, setSetupPassword] = useState("")
  const { setValue, watch } = form
  const authMode: AuthMode = (watch("auth_mode") || "rbac") as AuthMode
  const viewState = getSecuritySectionViewState({
    status,
    setupRequired: Boolean(setupStatus?.setup_required || status?.session_setup_required),
    authenticated: Boolean(whoami?.authenticated),
    authMode,
  })

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        setError(null)
        const nextStatus = await fetchSecurityStatus()
        if (!cancelled) setStatus(nextStatus)
        try {
          const nextSetup = await fetchSetupStatus()
          if (!cancelled) setSetupStatus(nextSetup)
        } catch {
          if (!cancelled) setSetupStatus(null)
        }
        try {
          const user = await fetchWhoAmI()
          if (!cancelled) {
            setWhoAmI(user)
            if (user.username) {
              setNewUsername(user.username)
            }
          }
        } catch {
          if (!cancelled) setWhoAmI({ authenticated: false })
        }
      } catch (nextError) {
        if (!cancelled) setError(nextError instanceof Error ? nextError.message : t("security.loadError"))
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <TabsContent value="security" className="space-y-6">
      {!status && !error ? (
        <Card>
          <CardContent className="flex items-center gap-3 pt-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("security.loading")}
          </CardContent>
        </Card>
      ) : null}

      {error ? (
        <Alert variant="destructive">
          <ShieldAlert className="h-4 w-4" />
          <AlertTitle>{t("security.errorTitle")}</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {status && !viewState.canBootstrapSecureLogin ? <SecuritySectionSummary status={status} /> : null}

      <Card className="border-blue-400/20 overflow-hidden">
        <CardHeader>
          <CardTitle>{t("security.settingsTitle")}</CardTitle>
          <CardDescription>{viewState.canBootstrapSecureLogin ? t("security.bootstrapCredsDescription") : t("security.settingsDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-2">
            <Label>{t("security.modeLabel")}</Label>
            <Select value={authMode} onValueChange={(value) => {
              const nextAuthMode = value as AuthMode
              setValue("auth_mode", nextAuthMode, { shouldDirty: true })
              setValue("rbac_enabled", nextAuthMode === "rbac", { shouldDirty: true })
            }}>
              <SelectTrigger className="max-w-sm" data-testid="security-auth-mode-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="rbac">{t("dashboard.setup.secureMode")}</SelectItem>
                <SelectItem value="none">{t("dashboard.setup.noAuthMode")}</SelectItem>
              </SelectContent>
            </Select>
            {viewState.showCompactNoAuthWarning ? (
              <Alert data-testid="security-noauth-warning" className="max-w-2xl border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  <p>{t("dashboard.setup.noAuthWarning")}</p>
                </AlertDescription>
              </Alert>
            ) : null}
          </div>

          <div className="space-y-3">
            <Label>{viewState.canBootstrapSecureLogin ? t("security.bootstrapActionTitle") : t("security.credentialsTitle")}</Label>
            {viewState.showCreateLoginAction ? (
              <Button
                type="button"
                className="w-full max-w-sm sm:w-auto"
                data-testid="security-create-login-btn"
                onClick={() => {
                  setValue("auth_mode", "rbac", { shouldDirty: true })
                  setValue("rbac_enabled", true, { shouldDirty: true })
                }}
              >
                {t("security.createAdminAndEnable")}
              </Button>
            ) : null}
            {viewState.showBootstrapCredentials ? (
              <div className="space-y-3 max-w-md">
                <div className="space-y-1">
                  <Label htmlFor="security-setup-username">{t("dashboard.login.username")}</Label>
                  <Input
                    id="security-setup-username"
                    data-testid="security-bootstrap-username"
                    value={setupUsername}
                    onChange={(event) => setSetupUsername(event.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="security-setup-password">{t("dashboard.login.password")}</Label>
                  <Input
                    id="security-setup-password"
                    data-testid="security-bootstrap-password"
                    type="password"
                    value={setupPassword}
                    onChange={(event) => setSetupPassword(event.target.value)}
                  />
                </div>
                {credentialError ? <p className="text-sm text-destructive">{credentialError}</p> : null}
                {credentialSuccess ? <p className="text-sm text-emerald-400">{credentialSuccess}</p> : null}
                {authMode !== "rbac" ? <p className="text-sm text-amber-300">{t("security.switchToSecureHint")}</p> : null}
                <Button
                  type="button"
                  disabled={authMode !== "rbac" || !setupUsername.trim() || !setupPassword || isSavingCredentials}
                  onClick={async () => {
                    setIsSavingCredentials(true)
                    setCredentialError(null)
                    setCredentialSuccess(null)
                    try {
                      const result = await completeInitialSetup("rbac", setupUsername.trim(), setupPassword)
                      setCredentialSuccess(result.message)
                      setSetupPassword("")
                      const [nextStatus, nextWhoAmI, nextSetup] = await Promise.all([
                        fetchSecurityStatus(),
                        fetchWhoAmI(),
                        fetchSetupStatus(),
                      ])
                      setStatus(nextStatus)
                      setWhoAmI(nextWhoAmI)
                      setSetupStatus(nextSetup)
                      if (nextWhoAmI.username) {
                        setNewUsername(nextWhoAmI.username)
                      }
                    } catch (err) {
                      setCredentialError(err instanceof Error ? err.message : t("dashboard.auth.setupFailed"))
                    } finally {
                      setIsSavingCredentials(false)
                    }
                  }}
                >
                  {isSavingCredentials ? t("security.savingCredentials") : t("security.createAdminAndEnable")}
                </Button>
              </div>
            ) : viewState.canBootstrapSecureLogin ? null : !whoami?.authenticated ? (
              <p className="text-sm text-muted-foreground">{t("security.credentialsUnavailable")}</p>
            ) : (
              <div className="space-y-3 max-w-md">
                <div className="space-y-1">
                  <Label htmlFor="security-username">{t("dashboard.login.username")}</Label>
                  <Input id="security-username" value={newUsername} onChange={(event) => setNewUsername(event.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="security-current-password">{t("security.currentPassword")}</Label>
                  <Input id="security-current-password" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="security-new-password">{t("security.newPassword")}</Label>
                  <Input id="security-new-password" type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
                </div>
                {credentialError ? <p className="text-sm text-destructive">{credentialError}</p> : null}
                {credentialSuccess ? <p className="text-sm text-emerald-400">{credentialSuccess}</p> : null}
                <Button
                  type="button"
                  disabled={!currentPassword || isSavingCredentials || (!newPassword && !newUsername.trim())}
                  onClick={async () => {
                    setIsSavingCredentials(true)
                    setCredentialError(null)
                    setCredentialSuccess(null)
                    try {
                      const result = await changeCredentials(currentPassword, newUsername.trim(), newPassword)
                      setCredentialSuccess(result.message)
                      setCurrentPassword("")
                      setNewPassword("")
                      const refreshed = await fetchWhoAmI()
                      setWhoAmI(refreshed)
                    } catch (err) {
                      setCredentialError(err instanceof Error ? err.message : t("dashboard.auth.loginFailed"))
                    } finally {
                      setIsSavingCredentials(false)
                    }
                  }}
                >
                  {isSavingCredentials ? t("security.savingCredentials") : t("security.saveCredentials")}
                </Button>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </TabsContent>
  )
}
