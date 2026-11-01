"use client"

import React from "react"
import { useEffect, useRef, useState } from "react"
import { AlertTriangle, Loader2, LockKeyhole, Shield, ShieldAlert, ShieldCheck, ShieldEllipsis } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { StatusBadge, type StatusTone } from "@/components/base/status-badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { TabsContent } from "@/components/base/tabs"
import { changeCredentials, completeInitialSetup, fetchKeyRecoveryStatus, fetchSecurityStatus, fetchSetupStatus, fetchWhoAmI, migrateLegacyKey, resetProtectedCredentials, RUNTIME_PREFLIGHT_CHANGED_EVENT } from "@/lib/api"
import { describeError } from "@/lib/error-catalog"
import { t } from "@/lib/i18n"
import type { AppSettings, AuthMode, KeyRecoveryStatus, SecurityMode, SecurityStatus } from "@/lib/types"
import { OfficialRecoveryUpdateActions } from "@/components/recovery-update-actions"

const KEY_RESET_CONFIRMATION = "RESET PROTECTED CREDENTIALS" as const

const modeCopy: Record<SecurityMode, { label: string; summary: string; tone: StatusTone }> = {
  NO_AUTH: {
    label: t("security.mode.noAuth.label"),
    summary: t("security.mode.noAuth.summary"),
    tone: "warning",
  },
  API_KEY_ONLY: {
    label: t("security.mode.apiKeyOnly.label"),
    summary: t("security.mode.apiKeyOnly.summary"),
    tone: "warning",
  },
  RBAC_WITH_API_KEY_FALLBACK: {
    label: t("security.mode.rbacWithFallback.label"),
    summary: t("security.mode.rbacWithFallback.summary"),
    tone: "neutral",
  },
  RBAC_ONLY: {
    label: t("security.mode.rbacOnly.label"),
    summary: t("security.mode.rbacOnly.summary"),
    tone: "success",
  },
}

// security_mode describes the configured policy. When a runtime override has
// disabled auth, the badge must describe what is actually enforced.
export function getEnforcedSecurityMode(status: Pick<SecurityStatus, "security_mode" | "runtime_auth_override_active">): SecurityStatus["security_mode"] {
  return status.runtime_auth_override_active ? "NO_AUTH" : status.security_mode
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
  const mode = getEnforcedSecurityMode(status)
  if (mode === "NO_AUTH") return ShieldAlert
  if (mode === "RBAC_ONLY") return ShieldCheck
  if (mode === "RBAC_WITH_API_KEY_FALLBACK") return ShieldEllipsis
  return LockKeyhole
}

export function SecurityModeBadge({ status, compact = false }: { status: SecurityStatus; compact?: boolean }) {
  if (!status.runtime_auth_override_active && status.security_mode === "API_KEY_ONLY" && !isLegacyApiKeyEffectiveMode(status)) {
    return null
  }

  const copy = modeCopy[getEnforcedSecurityMode(status)]
  const Icon = getSecurityModeIcon(status)

  return (
    <StatusBadge variant={copy.tone}>
      <Icon className="h-3.5 w-3.5" />
      <span>{compact ? copy.label : t("security.badge.withPrefix", { label: copy.label })}</span>
    </StatusBadge>
  )
}

export function SecuritySectionSummary({ status }: { status: SecurityStatus }) {
  const copy = getSecurityModeCopy(status)
  const runtimeOverrideActive = status.runtime_auth_override_active

  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b border-hairline">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="space-y-1.5">
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                {t("security.title")}
              </CardTitle>
            </div>
            <SecurityModeBadge status={status} />
          </div>
        </CardHeader>

      <CardContent className="space-y-4 pt-4">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="min-w-0 rounded-control bg-fill p-4 [overflow-wrap:anywhere]">
            <p className="break-words text-xs font-medium text-muted-foreground">{t("security.currentMode")}</p>
            <p className="mt-2 break-words text-base font-semibold">{runtimeOverrideActive ? t("security.runtimeOverrideLabel") : copy.label}</p>
            <p className="mt-2 break-words text-sm text-muted-foreground">
              {runtimeOverrideActive ? t("security.runtimeOverrideSummary") : copy.summary}
            </p>
            {runtimeOverrideActive ? (
              <p className="mt-3 text-xs text-muted-foreground">{t("security.runtimeOverrideConfiguredMode", { label: copy.label })}</p>
            ) : null}
          </div>
          <div className="min-w-0 rounded-control bg-fill p-4 [overflow-wrap:anywhere]">
            <p className="break-words text-xs font-medium text-muted-foreground">{t("security.atRestProtection")}</p>
            <p className="mt-2 break-words text-base font-semibold">{t("security.atRestTitle")}</p>
            <p className="mt-2 break-words text-sm text-muted-foreground [overflow-wrap:anywhere]">
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

export function KeyRecoveryCard() {
  const [recovery, setRecovery] = useState<KeyRecoveryStatus | null>(null)
  const [confirmation, setConfirmation] = useState("")
  const [legacyWrappingKey, setLegacyWrappingKey] = useState("")
  const [rawKeyFile, setRawKeyFile] = useState<File | null>(null)
  const [busy, setBusy] = useState<"idle" | "migrating" | "resetting">("idle")
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const wrappingInputRef = useRef<HTMLInputElement | null>(null)
  const rawKeyInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchKeyRecoveryStatus()
      .then((next) => {
        if (!cancelled) setRecovery(next)
      })
      .catch(() => {
        // Recovery is an authenticated admin-only surface. Other roles should
        // not receive a misleading warning or learn recovery state details.
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => () => {
    if (wrappingInputRef.current) wrappingInputRef.current.value = ""
    if (rawKeyInputRef.current) rawKeyInputRef.current.value = ""
  }, [])

  if (!recovery) return null

  if (!recovery.recovery_required) {
    if (!message) return null
    return (
      <Card data-testid="key-recovery-complete" className="border-success-border">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-success" />
            {t("runtimeRecovery.completeTitle")}
          </CardTitle>
          <CardDescription role="status">{message}</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const affectedCount = recovery.affected_dvr_credentials + recovery.affected_notification_credentials
  const normalizedWrappingKey = legacyWrappingKey.trim()
  const wrappingKeyValid = normalizedWrappingKey.length >= 32
  const rawKeyValid = rawKeyFile?.size === 32
  const migrationInputsConflict = Boolean(normalizedWrappingKey && rawKeyFile)
  const migrationInputValid = recovery.legacy_input_detected || wrappingKeyValid || rawKeyValid

  const clearMigrationInputs = () => {
    setLegacyWrappingKey("")
    setRawKeyFile(null)
    if (wrappingInputRef.current) wrappingInputRef.current.value = ""
    if (rawKeyInputRef.current) rawKeyInputRef.current.value = ""
  }

  return (
    <Card data-testid="key-recovery-card" className="border-warning-border">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-warning" />
          {t("runtimeRecovery.title")}
        </CardTitle>
        <CardDescription>{t("runtimeRecovery.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <Alert className="border-warning-border bg-warning-bg">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t("runtimeRecovery.lockedTitle")}</AlertTitle>
          <AlertDescription>
            {t("runtimeRecovery.lockedDescription", { count: affectedCount })}
          </AlertDescription>
        </Alert>

        {recovery.can_migrate ? (
          <div className="space-y-3 rounded-control border border-success-border bg-success-bg p-4">
            <p className="font-medium">{t("runtimeRecovery.migrateTitle")}</p>
            <p className="text-sm text-muted-foreground">{t("runtimeRecovery.migrateDescription")}</p>
            {!recovery.legacy_input_detected ? (
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="legacy-wrapping-key">{t("runtimeRecovery.wrappingKeyLabel")}</Label>
                  <Input
                    ref={wrappingInputRef}
                    id="legacy-wrapping-key"
                    type="password"
                    autoComplete="off"
                    value={legacyWrappingKey}
                    onChange={(event) => setLegacyWrappingKey(event.target.value)}
                    aria-describedby="legacy-wrapping-key-help"
                  />
                  <p id="legacy-wrapping-key-help" className="text-xs text-muted-foreground">{t("runtimeRecovery.wrappingKeyHelp")}</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="legacy-raw-key-file">{t("runtimeRecovery.rawKeyLabel")}</Label>
                  <Input
                    ref={rawKeyInputRef}
                    id="legacy-raw-key-file"
                    type="file"
                    accept="application/octet-stream"
                    onChange={(event) => setRawKeyFile(event.target.files?.[0] ?? null)}
                    aria-describedby="legacy-raw-key-help"
                  />
                  <p id="legacy-raw-key-help" className="text-xs text-muted-foreground">{t("runtimeRecovery.rawKeyHelp")}</p>
                </div>
                {migrationInputsConflict ? <p role="alert" className="text-sm text-danger-fg">{t("runtimeRecovery.inputConflict")}</p> : null}
                {rawKeyFile && !rawKeyValid ? <p role="alert" className="text-sm text-danger-fg">{t("runtimeRecovery.rawKeyInvalid")}</p> : null}
              </div>
            ) : null}
            <Button
              type="button"
              disabled={busy !== "idle" || !migrationInputValid || migrationInputsConflict}
              onClick={async () => {
                setBusy("migrating")
                setError(null)
                setMessage(null)
                try {
                  const result = await migrateLegacyKey({
                    legacyWrappingKey: wrappingKeyValid ? normalizedWrappingKey : undefined,
                    rawKeyFile: rawKeyValid ? rawKeyFile ?? undefined : undefined,
                  })
                  setRecovery(result)
                  setMessage(result.message)
                  window.dispatchEvent(new CustomEvent(RUNTIME_PREFLIGHT_CHANGED_EVENT))
                } catch (nextError) {
                  setError(describeError(nextError, t("runtimeRecovery.error")).message)
                } finally {
                  clearMigrationInputs()
                  setBusy("idle")
                }
              }}
            >
              {busy === "migrating" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {t("runtimeRecovery.migrateAction")}
            </Button>
          </div>
        ) : (
          <div className="space-y-2 rounded-control border border-hairline p-4">
            <p className="font-medium">{t("runtimeRecovery.restoreTitle")}</p>
            <p className="text-sm text-muted-foreground">{t("runtimeRecovery.restoreDescription")}</p>
          </div>
        )}

        {recovery.can_reset ? (
          <div className="space-y-3 rounded-control border border-danger-border p-4">
            <p className="font-medium text-foreground">{t("runtimeRecovery.resetTitle")}</p>
            <p className="text-sm text-muted-foreground">{t("runtimeRecovery.resetDescription")}</p>
            <div className="space-y-2">
              <Label htmlFor="key-recovery-confirmation">{t("runtimeRecovery.confirmLabel", { confirmation: KEY_RESET_CONFIRMATION })}</Label>
              <Input
                id="key-recovery-confirmation"
                value={confirmation}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setConfirmation(event.target.value)}
              />
            </div>
            <Button
              type="button"
              variant="destructive"
              disabled={busy !== "idle" || confirmation.trim().toUpperCase() !== KEY_RESET_CONFIRMATION}
              onClick={async () => {
                setBusy("resetting")
                setError(null)
                setMessage(null)
                try {
                  const result = await resetProtectedCredentials(KEY_RESET_CONFIRMATION)
                  setRecovery(result)
                  setConfirmation("")
                  setMessage(result.message)
                  window.dispatchEvent(new CustomEvent(RUNTIME_PREFLIGHT_CHANGED_EVENT))
                } catch (nextError) {
                  setError(describeError(nextError, t("runtimeRecovery.error")).message)
                } finally {
                  setBusy("idle")
                }
              }}
            >
              {busy === "resetting" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {t("runtimeRecovery.resetAction")}
            </Button>
          </div>
        ) : null}

        <OfficialRecoveryUpdateActions />

        {message ? <p role="status" className="text-sm text-success-fg">{message}</p> : null}
        {error ? <p role="alert" className="text-sm text-danger-fg">{error}</p> : null}
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
        if (!cancelled) setError(describeError(nextError, t("security.loadError")).message)
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
          <CardContent className="flex items-center gap-3 pt-6 text-sm text-muted-foreground sm:pt-6">
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

      <KeyRecoveryCard />

      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
          <div className="flex items-center gap-2">
            <Shield className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <div>
              <CardTitle>{t("security.settingsTitle")}</CardTitle>
              {viewState.canBootstrapSecureLogin ? <CardDescription>{t("security.bootstrapCredsDescription")}</CardDescription> : null}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6 pt-4">
          <div className="space-y-2">
            <Label htmlFor="security-auth-mode-select">{t("security.modeLabel")}</Label>
            <Select value={authMode} onValueChange={(value) => {
              const nextAuthMode = value as AuthMode
              setValue("auth_mode", nextAuthMode, { shouldDirty: true })
              setValue("rbac_enabled", nextAuthMode === "rbac", { shouldDirty: true })
            }}>
              <SelectTrigger id="security-auth-mode-select" data-testid="security-auth-mode-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="rbac">{t("dashboard.setup.secureMode")}</SelectItem>
                <SelectItem value="none">{t("dashboard.setup.noAuthMode")}</SelectItem>
              </SelectContent>
            </Select>
            {viewState.showCompactNoAuthWarning ? (
              <Alert data-testid="security-noauth-warning" className="border-warning-border bg-warning-bg text-warning-fg [&>svg]:text-warning-fg">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  <p>{authMode !== "rbac" ? t("dashboard.setup.noAuthWarning") : viewState.canBootstrapSecureLogin ? t("security.signInOffUntilCreated") : t("security.signInOffUntilSaved")}</p>
                </AlertDescription>
              </Alert>
            ) : null}
          </div>

          <div className="space-y-3">
            {viewState.canBootstrapSecureLogin && viewState.showCreateLoginAction ? null : <Label className="block">{t("security.credentialsTitle")}</Label>}
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
                {t("security.setupSignIn")}
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
                {credentialError ? <p className="text-sm text-danger-fg">{credentialError}</p> : null}
                {credentialSuccess ? <p className="text-sm text-success-fg">{credentialSuccess}</p> : null}
                {authMode !== "rbac" ? <p className="text-sm text-warning-fg">{t("security.switchToSecureHint")}</p> : null}
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
                      window.dispatchEvent(new CustomEvent("channelwatch-auth-state-changed"))
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
                      setCredentialError(describeError(err, t("dashboard.auth.setupFailed")).message)
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
                {credentialError ? <p className="text-sm text-danger-fg">{credentialError}</p> : null}
                {credentialSuccess ? <p className="text-sm text-success-fg">{credentialSuccess}</p> : null}
                <Button
                  type="button"
                  disabled={!currentPassword || isSavingCredentials || (!newPassword && !newUsername.trim())}
                  onClick={async () => {
                    setIsSavingCredentials(true)
                    setCredentialError(null)
                    setCredentialSuccess(null)
                    try {
                      await changeCredentials(currentPassword, newUsername.trim(), newPassword)
                      // Changing credentials signs out every session, including
                      // this one. Keep the confirmation visible and let the
                      // header pick up the signed-out state.
                      setCredentialSuccess(t("security.credentialsChangedSignIn"))
                      setCurrentPassword("")
                      setNewPassword("")
                      window.dispatchEvent(new CustomEvent("channelwatch-auth-state-changed"))
                    } catch (err) {
                      setCredentialError(describeError(err, t("dashboard.auth.loginFailed")).message)
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
