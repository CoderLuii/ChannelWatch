import { useEffect, useMemo, useState } from "react"
import { ShieldAlert, ShieldCheck, Trash2 } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/base/dialog"
import { previewNotificationDestinationSafety } from "@/lib/api"
import type {
  AppSettings,
  NotificationDestinationSafetyPreview,
  TrustedNotificationDestination,
  TrustedNotificationDestinationSource,
} from "@/lib/types"
import { t } from "@/lib/i18n"

function destinationKey(destination: TrustedNotificationDestination) {
  return `${destination.source}|${destination.scheme}|${destination.host}|${destination.port}`
}

function sameDestination(
  left: TrustedNotificationDestination,
  right: TrustedNotificationDestination,
) {
  return destinationKey(left) === destinationKey(right)
}

function destinationLabel(destination: TrustedNotificationDestination) {
  const host = destination.host.includes(":") ? `[${destination.host}]` : destination.host
  return `${destination.scheme}://${host}:${destination.port}`
}

interface TrustedDestinationControlProps {
  form: UseFormReturn<AppSettings>
  source: TrustedNotificationDestinationSource
  url: string
  compact?: boolean
}

export function TrustedDestinationControl({
  form,
  source,
  url,
  compact = false,
}: TrustedDestinationControlProps) {
  const { setValue, watch } = form
  const trustedDestinations = watch("trusted_notification_destinations") || []
  const [preview, setPreview] = useState<NotificationDestinationSafetyPreview | null>(null)
  const [error, setError] = useState("")
  const [confirmOpen, setConfirmOpen] = useState(false)

  const trimmedUrl = (url || "").trim()
  const normalized = preview?.normalized

  const alreadyTrusted = useMemo(() => {
    if (!normalized) return false
    return trustedDestinations.some((entry) => sameDestination(entry, normalized))
  }, [normalized, trustedDestinations])

  useEffect(() => {
    if (!trimmedUrl || trimmedUrl.includes("****")) {
      setPreview(null)
      setError("")
      return
    }

    const timeout = window.setTimeout(() => {
      previewNotificationDestinationSafety(source, trimmedUrl)
        .then((result) => {
          setPreview(result)
          setError("")
        })
        .catch(() => {
          setPreview(null)
          setError(t("notifications.trusted.previewError"))
        })
    }, 350)

    return () => window.clearTimeout(timeout)
  }, [source, trimmedUrl])

  if (!trimmedUrl || !preview || preview.status === "unsupported" || preview.status === "public_safe") {
    return error ? <p className="text-xs text-destructive">{error}</p> : null
  }

  const addDestination = () => {
    if (!normalized) return
    const nextEntry: TrustedNotificationDestination = {
      source: normalized.source,
      scheme: normalized.scheme,
      host: normalized.host,
      port: normalized.port,
      label: source === "webhook" ? "Webhook" : "Custom Apprise",
    }
    if (trustedDestinations.some((entry) => sameDestination(entry, nextEntry))) {
      setConfirmOpen(false)
      return
    }
    setValue(
      "trusted_notification_destinations",
      [...trustedDestinations, nextEntry],
      { shouldDirty: true },
    )
    setConfirmOpen(false)
  }

  const removeDestination = (destination: TrustedNotificationDestination) => {
    setValue(
      "trusted_notification_destinations",
      trustedDestinations.filter((entry) => !sameDestination(entry, destination)),
      { shouldDirty: true },
    )
  }

  if (preview.trusted || alreadyTrusted) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-200">
        <ShieldCheck className="h-3.5 w-3.5" />
        <span>{t("notifications.trusted.allowed")}</span>
        {normalized && (
          <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => removeDestination(normalized)}>
            <Trash2 className="mr-1 h-3.5 w-3.5" />
            {t("common.remove")}
          </Button>
        )}
      </div>
    )
  }

  if (!preview.trustable) {
    return (
      <Alert variant="destructive" className={compact ? "py-3" : undefined}>
        <ShieldAlert className="h-4 w-4" />
        <AlertTitle>{t("notifications.trusted.blockedTitle")}</AlertTitle>
        <AlertDescription>{preview.message}</AlertDescription>
      </Alert>
    )
  }

  return (
    <>
      <Alert className={compact ? "py-3" : undefined}>
        <ShieldAlert className="h-4 w-4" />
        <AlertTitle>{t("notifications.trusted.reviewTitle")}</AlertTitle>
        <AlertDescription className="space-y-3">
          <p>{preview.message}</p>
          {normalized && (
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{normalized.source === "webhook" ? "Webhook" : "Custom Apprise"}</Badge>
              <code className="rounded-md bg-muted px-2 py-1 text-xs">{destinationLabel(normalized)}</code>
            </div>
          )}
          <Button type="button" size="sm" onClick={() => setConfirmOpen(true)}>
            <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />
            {t("notifications.trusted.trustBtn")}
          </Button>
        </AlertDescription>
      </Alert>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("notifications.trusted.confirmTitle")}</DialogTitle>
            <DialogDescription>{t("notifications.trusted.confirmDesc")}</DialogDescription>
          </DialogHeader>
          {normalized && (
            <div className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
              <p className="font-medium">{destinationLabel(normalized)}</p>
              <p className="text-xs text-muted-foreground">{t("notifications.trusted.confirmScope")}</p>
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="button" onClick={addDestination}>
              {t("notifications.trusted.confirmBtn")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

interface TrustedDestinationListProps {
  form: UseFormReturn<AppSettings>
}

export function TrustedDestinationList({ form }: TrustedDestinationListProps) {
  const { setValue, watch } = form
  const trustedDestinations = watch("trusted_notification_destinations") || []

  if (trustedDestinations.length === 0) {
    return null
  }

  const removeDestination = (destination: TrustedNotificationDestination) => {
    setValue(
      "trusted_notification_destinations",
      trustedDestinations.filter((entry) => !sameDestination(entry, destination)),
      { shouldDirty: true },
    )
  }

  return (
    <div className="space-y-2 rounded-xl border border-emerald-400/20 bg-emerald-500/5 p-4">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-emerald-400" />
        <p className="text-sm font-medium">{t("notifications.trusted.listTitle")}</p>
      </div>
      <div className="space-y-2">
        {trustedDestinations.map((destination) => (
          <div key={destinationKey(destination)} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/60 bg-background/70 px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-sm">{destinationLabel(destination)}</p>
              <p className="text-xs text-muted-foreground">
                {destination.source === "webhook" ? t("notifications.trusted.sourceWebhook") : t("notifications.trusted.sourceCustom")}
              </p>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={() => removeDestination(destination)}>
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
              {t("common.remove")}
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}
