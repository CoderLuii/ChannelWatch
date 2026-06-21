"use client"

import { useCallback } from "react"
import { useToast } from "@/hooks/use-toast"
import {
  isErrorPayload,
  networkError,
  type ErrorPayload,
} from "@/lib/error-catalog"
import { ApiError } from "@/lib/api"

export interface ErrorHandlerOptions {
  title?: string
  fallbackMessage?: string
}

export function useErrorHandler() {
  const { toast } = useToast()

  const toastError = useCallback(
    (payload: ErrorPayload, title?: string) => {
      toast({
        variant: "destructive",
        title: title ?? "Error",
        description: payload.message,
      })
    },
    [toast],
  )

  const handleError = useCallback(
    (err: unknown, options: ErrorHandlerOptions = {}): ErrorPayload => {
      let payload: ErrorPayload

      if (err instanceof ApiError) {
        payload = err.payload
      } else if (isErrorPayload(err)) {
        payload = err
      } else if (err instanceof TypeError && err.message.toLowerCase().includes("fetch")) {
        payload = networkError(err.message)
      } else if (err instanceof Error) {
        payload = { code: "ERR_UNKNOWN", message: err.message, remediation: null }
      } else {
        payload = {
          code: "ERR_UNKNOWN",
          message: options.fallbackMessage ?? "An unexpected error occurred.",
          remediation: null,
        }
      }

      toastError(payload, options.title)
      return payload
    },
    [toastError],
  )

  return { handleError, toastError }
}
