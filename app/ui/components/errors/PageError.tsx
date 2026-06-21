"use client"

import { AlertTriangle, RefreshCw } from "lucide-react"
import { Button } from "@/components/base/button"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import type { ErrorPayload } from "@/lib/error-catalog"

interface PageErrorProps {
  error: ErrorPayload | string | null | undefined
  onRetry?: () => void
  className?: string
}

export function PageError({ error, onRetry, className }: PageErrorProps) {
  if (!error) return null

  const message = typeof error === "string" ? error : error.message
  const remediation = typeof error === "string" ? undefined : error.remediation ?? undefined
  const code = typeof error === "string" ? undefined : error.code

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-4 rounded-lg border border-destructive/30 bg-destructive/5 p-8 text-center",
        className,
      )}
    >
      <AlertTriangle className="h-10 w-10 text-destructive" />
      <div className="space-y-1">
        <p className="text-base font-semibold text-destructive">{message}</p>
        {remediation && (
          <p className="text-sm text-muted-foreground">{remediation}</p>
        )}
        {code && (
          <p className="font-mono text-xs text-muted-foreground/60">{code}</p>
        )}
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="mr-2 h-3 w-3" />
          {t("errors.tryAgain")}
        </Button>
      )}
    </div>
  )
}
