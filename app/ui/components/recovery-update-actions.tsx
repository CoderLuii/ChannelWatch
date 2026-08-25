"use client"

import { useEffect, useRef, useState } from "react"
import { DownloadCloud, Loader2, RefreshCw, ShieldCheck } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Button } from "@/components/base/button"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { ApiError, applyRecoveryUpdate, checkRecoveryUpdate, fetchRecoveryUpdateStatus, type RecoveryUpdateStatus } from "@/lib/api"
import { t } from "@/lib/i18n"
import { applyUpdateAndReconnect } from "@/lib/update-reconnect"

const RECOVERY_UPDATE_CONFIRMATION = "INSTALL OFFICIAL UPDATE" as const

function publicStatus(status: RecoveryUpdateStatus): RecoveryUpdateStatus {
  return { ...status, bootstrap_csrf: null }
}

export function OfficialRecoveryUpdateActions({ compact = false }: { compact?: boolean }) {
  const [status, setStatus] = useState<RecoveryUpdateStatus | null>(null)
  const [confirmation, setConfirmation] = useState("")
  const [busy, setBusy] = useState<"idle" | "checking" | "applying">("idle")
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const bootstrapCsrfRef = useRef<string | null>(null)

  const acceptStatus = (next: RecoveryUpdateStatus) => {
    if (!next.recovery_active) bootstrapCsrfRef.current = null
    else if (next.bootstrap_csrf) bootstrapCsrfRef.current = next.bootstrap_csrf
    setStatus(publicStatus(next))
  }

  useEffect(() => {
    let cancelled = false
    fetchRecoveryUpdateStatus()
      .then((next) => {
        if (!cancelled) acceptStatus(next)
      })
      .catch(() => {
        // The recovery surface is intentionally absent outside an active
        // recovery state. Do not turn an unavailable public-safe endpoint into
        // a generic update warning.
      })
    return () => {
      cancelled = true
      bootstrapCsrfRef.current = null
    }
  }, [])

  if (!status?.recovery_active) return null

  const latest = status.latest
  const latestVersion = latest?.version ?? null
  const latestVersionLabel = latest?.version_tag ?? latestVersion ?? t("common.unknown")
  const confirmationValid = !status.confirmation_required || confirmation === RECOVERY_UPDATE_CONFIRMATION
  const authorizationReady = !compact || Boolean(bootstrapCsrfRef.current)
  const canApply = Boolean(status.update_available && !status.image_required && latestVersion && confirmationValid && authorizationReady && busy === "idle")

  const content = (
    <div className="space-y-3" data-testid="official-recovery-update-actions">
      {compact ? (
        <div>
          <p className="font-medium">{t("runtimeRecovery.updateTitle")}</p>
          <p className="mt-1 text-sm text-muted-foreground">{t("runtimeRecovery.updateDescription")}</p>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{t("runtimeRecovery.updateDescription")}</p>
      )}

      {status.confirmation_required && latestVersion ? (
        <div className="space-y-2">
          <Label htmlFor="recovery-update-confirmation">
            {t("runtimeRecovery.updateConfirmLabel", { confirmation: RECOVERY_UPDATE_CONFIRMATION })}
          </Label>
          <Input
            id="recovery-update-confirmation"
            value={confirmation}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size={compact ? "sm" : "default"}
          variant="outline"
          disabled={busy !== "idle" || !authorizationReady}
          onClick={async () => {
            setBusy("checking")
            setError(null)
            setMessage(null)
            try {
              acceptStatus(await checkRecoveryUpdate(bootstrapCsrfRef.current))
            } catch (nextError) {
              setError(nextError instanceof Error ? nextError.message : t("updates.error.default"))
            } finally {
              setBusy("idle")
            }
          }}
        >
          {busy === "checking" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          {t("runtimeRecovery.updateCheck")}
        </Button>

        {latestVersion && status.update_available && !status.image_required ? (
          <Button
            type="button"
            size={compact ? "sm" : "default"}
            disabled={!canApply}
            onClick={async () => {
              setBusy("applying")
              setError(null)
              setMessage(null)
              try {
                const result = await applyUpdateAndReconnect(latestVersion, {
                  apply: (version) => applyRecoveryUpdate(version, bootstrapCsrfRef.current),
                  fetchStatus: async () => {
                    const next = await fetchRecoveryUpdateStatus()
                    acceptStatus(next)
                    return next
                  },
                  reload: () => window.location.reload(),
                  isRejectedUpdate: (nextError) => nextError instanceof ApiError,
                })
                setConfirmation("")
                setMessage(result?.message ?? t("runtimeRecovery.updateStarted"))
              } catch (nextError) {
                setError(nextError instanceof Error ? nextError.message : t("updates.error.default"))
              } finally {
                setBusy("idle")
              }
            }}
          >
            {busy === "applying" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
            {t("runtimeRecovery.updateApply", { version: latestVersionLabel })}
          </Button>
        ) : null}
      </div>

      {!status.update_available ? <p role="status" className="text-sm text-muted-foreground">{t("runtimeRecovery.updateCurrent")}</p> : null}
      {!authorizationReady ? <p role="status" className="text-sm text-muted-foreground">{t("runtimeRecovery.updateSignInRequired")}</p> : null}
      {status.image_required ? <p role="status" className="text-sm text-amber-700 dark:text-amber-300">{t("runtimeRecovery.updateImageRequired")}</p> : null}
      {message ? <p role="status" className="text-sm text-emerald-700 dark:text-emerald-300">{message}</p> : null}
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    </div>
  )

  if (compact) return content

  return (
    <Alert className="border-sky-500/30 bg-sky-500/5">
      <DownloadCloud className="h-4 w-4 text-sky-600" />
      <AlertTitle>{t("runtimeRecovery.updateTitle")}</AlertTitle>
      <AlertDescription>{content}</AlertDescription>
    </Alert>
  )
}
