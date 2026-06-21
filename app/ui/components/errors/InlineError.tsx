"use client"

import { AlertCircle } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { cn } from "@/lib/utils"
import type { ErrorPayload } from "@/lib/error-catalog"

interface InlineErrorProps {
  error: ErrorPayload | string | null | undefined
  className?: string
  showRemediation?: boolean
}

export function InlineError({ error, className, showRemediation = true }: InlineErrorProps) {
  if (!error) return null

  const message = typeof error === "string" ? error : error.message
  const remediation = typeof error === "string" ? undefined : error.remediation ?? undefined

  return (
    <Alert variant="destructive" className={cn("py-3", className)}>
      <AlertCircle className="h-4 w-4" />
      <AlertTitle className="text-sm">{message}</AlertTitle>
      {showRemediation && remediation && (
        <AlertDescription className="mt-1 text-xs opacity-80">{remediation}</AlertDescription>
      )}
    </Alert>
  )
}
