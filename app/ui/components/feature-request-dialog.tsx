"use client"

import React, { useCallback, useEffect, useRef, useState } from "react"
import { CheckCircle2, ImagePlus, Lightbulb, Loader2, RefreshCw, Trash2, X } from "lucide-react"
import { z } from "zod"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Button } from "@/components/base/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/base/dialog"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/base/select"
import { Textarea } from "@/components/base/textarea"
import {
  createReportDraft,
  fetchReportConfig,
  submitReport,
  type ReportArea,
  type ReportConfig,
  type ReportProblemPayload,
} from "@/lib/api"
import { t } from "@/lib/i18n"

const screenshotTypes = new Set(["image/png", "image/jpeg", "image/webp"])
const defaultMaxAttachmentBytes = 8 * 1024 * 1024

const featureRequestSchema = z.object({
  summary: z.string().trim().min(1, "Enter a short title.").max(160, "Use 160 characters or fewer."),
  expected: z.string().trim().min(1, "Describe what should change.").max(2000, "Use 2,000 characters or fewer."),
  use_case: z.string().trim().min(1, "Explain why this would help.").max(2000, "Use 2,000 characters or fewer."),
  area: z.enum([
    "dashboard",
    "activity",
    "notifications",
    "dvr_monitoring",
    "updates",
    "backup_restore",
    "authentication_security",
    "other",
  ]),
  email: z.string().trim().refine(
    (value) => value === "" || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value),
    "Enter a valid email address or leave it blank.",
  ),
})

type FeatureRequestForm = z.infer<typeof featureRequestSchema>
type FeatureRequestField = keyof FeatureRequestForm

const initialForm: FeatureRequestForm = {
  summary: "",
  expected: "",
  use_case: "",
  area: "dashboard",
  email: "",
}

const areaOptions: Array<{ value: ReportArea; label: string }> = [
  { value: "dashboard", label: "Dashboard" },
  { value: "activity", label: "Activity and history" },
  { value: "notifications", label: "Notifications" },
  { value: "dvr_monitoring", label: "DVR setup and monitoring" },
  { value: "updates", label: "Updates" },
  { value: "backup_restore", label: "Backup and restore" },
  { value: "authentication_security", label: "Authentication and security" },
  { value: "other", label: "Other" },
]

interface FeatureRequestDialogProps {
  trigger?: React.ReactNode
}

function validateScreenshot(file: File, config: ReportConfig | null): string | null {
  if (!config?.attachments_enabled) return "Screenshots are unavailable for this report endpoint."
  if (!screenshotTypes.has(file.type) || !/\.(png|jpe?g|webp)$/i.test(file.name)) {
    return "Choose a PNG, JPEG, or WebP screenshot."
  }
  const maxBytes = config.max_attachment_bytes || defaultMaxAttachmentBytes
  if (file.size > maxBytes) {
    return `The screenshot must be smaller than ${Math.ceil(maxBytes / (1024 * 1024))} MB.`
  }
  return null
}

export function FeatureRequestDialog({ trigger }: FeatureRequestDialogProps) {
  const screenshotInputRef = useRef<HTMLInputElement | null>(null)
  const submissionAbortRef = useRef<AbortController | null>(null)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<FeatureRequestForm>(initialForm)
  const [errors, setErrors] = useState<Partial<Record<FeatureRequestField, string>>>({})
  const [config, setConfig] = useState<ReportConfig | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [configLoading, setConfigLoading] = useState(false)
  const [configAttempted, setConfigAttempted] = useState(false)
  const [screenshot, setScreenshot] = useState<File | null>(null)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [confirmDiscard, setConfirmDiscard] = useState(false)

  const hasDraft = Boolean(
    form.summary.trim()
    || form.expected.trim()
    || form.use_case.trim()
    || form.email.trim()
    || form.area !== initialForm.area
    || screenshot,
  )

  const loadConfig = useCallback(async () => {
    setConfigAttempted(true)
    setConfigLoading(true)
    setConfigError(null)
    try {
      setConfig(await fetchReportConfig())
    } catch (error) {
      setConfigError(error instanceof Error ? error.message : t("feedback.feature.configErrorDescription"))
    } finally {
      setConfigLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open || config || configAttempted) return
    void loadConfig()
  }, [config, configAttempted, loadConfig, open])

  useEffect(() => () => submissionAbortRef.current?.abort(), [])

  const updateField = <Field extends FeatureRequestField>(field: Field, value: FeatureRequestForm[Field]) => {
    setForm((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
    setSubmitError(null)
    setConfirmDiscard(false)
  }

  const clearDraft = () => {
    submissionAbortRef.current?.abort()
    setForm({ ...initialForm })
    setErrors({})
    setScreenshot(null)
    setAttachmentError(null)
    setSubmitError(null)
    setSubmitting(false)
    setPreparing(false)
    setSubmitted(false)
    setConfirmDiscard(false)
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) submissionAbortRef.current?.abort()
    if (!nextOpen && submitted) setSubmitted(false)
    setOpen(nextOpen)
    if (nextOpen) setConfirmDiscard(false)
  }

  const handleScreenshot = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    event.target.value = ""
    if (!file) return
    const error = validateScreenshot(file, config)
    if (error) {
      setAttachmentError(error)
      return
    }
    setScreenshot(file)
    setAttachmentError(null)
    setSubmitError(null)
  }

  const handleSubmit = async () => {
    const parsed = featureRequestSchema.safeParse(form)
    if (!parsed.success) {
      const nextErrors: Partial<Record<FeatureRequestField, string>> = {}
      for (const issue of parsed.error.issues) {
        const field = issue.path[0] as FeatureRequestField
        if (!nextErrors[field]) nextErrors[field] = issue.message
      }
      setErrors(nextErrors)
      const firstField = parsed.error.issues[0]?.path[0]
      if (firstField !== undefined) document.getElementById(`feature-${String(firstField)}`)?.focus()
      return
    }
    if (screenshot) {
      const nextAttachmentError = validateScreenshot(screenshot, config)
      if (nextAttachmentError) {
        setAttachmentError(nextAttachmentError)
        return
      }
    }

    const payload: ReportProblemPayload = {
      kind: "feature",
      area: parsed.data.area,
      summary: parsed.data.summary,
      expected: parsed.data.expected,
      use_case: parsed.data.use_case,
      email: parsed.data.email || null,
      turnstile_token: null,
    }
    const draft = createReportDraft(payload)
    const abortController = new AbortController()
    submissionAbortRef.current?.abort()
    submissionAbortRef.current = abortController
    setSubmitting(true)
    setPreparing(false)
    setSubmitError(null)
    try {
      await submitReport(
        config?.endpoint || "/api/v1/support/report-dry-run",
        payload,
        { screenshots: screenshot ? [screenshot] : [] },
        {
          supportCode: draft.supportCode,
          signal: abortController.signal,
          onChallengeProgress: () => setPreparing(true),
        },
      )
      // Successful submission is the one automatic draft-clearing event.
      // Release the private attachment object and entered contact information
      // before rendering the confirmation.
      setForm({ ...initialForm })
      setErrors({})
      setScreenshot(null)
      setAttachmentError(null)
      setSubmitError(null)
      setConfirmDiscard(false)
      setSubmitted(true)
    } catch (error) {
      if (abortController.signal.aborted) return
      setSubmitError(error instanceof Error ? error.message : "The request could not be submitted. Your draft is still here.")
    } finally {
      if (submissionAbortRef.current === abortController) submissionAbortRef.current = null
      setSubmitting(false)
      setPreparing(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button type="button" variant="outline">
            <Lightbulb className="h-4 w-4" aria-hidden="true" />
            {t("feedback.feature.action")}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent
        className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-2xl grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden"
        onInteractOutside={(event) => {
          if (hasDraft || submitting) event.preventDefault()
        }}
      >
        <DialogHeader>
          <DialogTitle>{t("feedback.feature.title")}</DialogTitle>
          <DialogDescription>{t("feedback.feature.description")}</DialogDescription>
        </DialogHeader>

        {submitted ? (
          <div className="space-y-5 py-2" data-testid="feature-request-success">
            <Alert>
              <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden="true" />
              <AlertTitle>{t("feedback.feature.successTitle")}</AlertTitle>
              <AlertDescription>{t("feedback.feature.successDescription")}</AlertDescription>
            </Alert>
            <DialogFooter>
              <Button
                type="button"
                onClick={() => {
                  clearDraft()
                  setOpen(false)
                }}
              >
                {t("common.close")}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            <div className="min-h-0 overflow-y-auto pr-1" data-testid="feature-request-form">
              <div className="space-y-5 pb-2">
            {configError ? (
              <Alert variant="destructive">
                <AlertTitle>{t("feedback.feature.configErrorTitle")}</AlertTitle>
                <AlertDescription className="space-y-3">
                  <span className="block">{configError}</span>
                  <Button type="button" variant="outline" size="sm" className="min-h-11" onClick={() => void loadConfig()} disabled={configLoading}>
                    {configLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
                    {t("feedback.feature.retryConfig")}
                  </Button>
                </AlertDescription>
              </Alert>
            ) : null}

            <div className="space-y-2">
              <Label htmlFor="feature-summary">{t("feedback.feature.summaryLabel")}</Label>
              <Input
                id="feature-summary"
                value={form.summary}
                onChange={(event) => updateField("summary", event.target.value)}
                maxLength={160}
                aria-invalid={Boolean(errors.summary)}
                aria-describedby={errors.summary ? "feature-summary-error" : undefined}
              />
              {errors.summary ? <p id="feature-summary-error" role="alert" className="text-sm text-destructive">{errors.summary}</p> : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="feature-expected">{t("feedback.feature.changeLabel")}</Label>
              <Textarea
                id="feature-expected"
                value={form.expected}
                onChange={(event) => updateField("expected", event.target.value)}
                className="min-h-28"
                maxLength={2000}
                aria-invalid={Boolean(errors.expected)}
                aria-describedby={errors.expected ? "feature-expected-error" : undefined}
              />
              {errors.expected ? <p id="feature-expected-error" role="alert" className="text-sm text-destructive">{errors.expected}</p> : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="feature-use_case">{t("feedback.feature.helpLabel")}</Label>
              <Textarea
                id="feature-use_case"
                value={form.use_case}
                onChange={(event) => updateField("use_case", event.target.value)}
                className="min-h-24"
                maxLength={2000}
                aria-invalid={Boolean(errors.use_case)}
                aria-describedby={errors.use_case ? "feature-use-case-error" : undefined}
              />
              {errors.use_case ? <p id="feature-use-case-error" role="alert" className="text-sm text-destructive">{errors.use_case}</p> : null}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="feature-area">{t("feedback.feature.areaLabel")}</Label>
                <Select value={form.area} onValueChange={(value) => updateField("area", value as ReportArea)}>
                  <SelectTrigger id="feature-area" className="min-h-11">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {areaOptions.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="feature-email">{t("feedback.feature.contactLabel")}</Label>
                <Input
                  id="feature-email"
                  type="email"
                  value={form.email}
                  onChange={(event) => updateField("email", event.target.value)}
                  aria-invalid={Boolean(errors.email)}
                  aria-describedby={errors.email ? "feature-email-error" : "feature-email-help"}
                />
                {errors.email ? (
                  <p id="feature-email-error" role="alert" className="text-sm text-destructive">{errors.email}</p>
                ) : (
                  <p id="feature-email-help" className="text-sm text-muted-foreground">{t("feedback.feature.contactHelp")}</p>
                )}
              </div>
            </div>

            <div className="space-y-3 rounded-lg border border-border p-4">
              <div>
                <p className="text-sm font-medium">{t("feedback.feature.screenshotLabel")}</p>
                <p className="mt-1 text-sm text-muted-foreground">{t("feedback.feature.screenshotHelp")}</p>
              </div>
              <input
                ref={screenshotInputRef}
                className="sr-only"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label={t("feedback.feature.screenshotLabel")}
                onChange={handleScreenshot}
              />
              {screenshot ? (
                <div className="flex min-w-0 items-center justify-between gap-3 rounded-md bg-muted/40 px-3 py-2">
                  <span className="truncate text-sm" title={screenshot.name}>{screenshot.name}</span>
                  <Button type="button" variant="ghost" size="sm" className="min-h-11" onClick={() => setScreenshot(null)}>
                    <X className="h-4 w-4" aria-hidden="true" />
                    {t("common.remove")}
                  </Button>
                </div>
              ) : (
                <Button type="button" variant="outline" className="min-h-11" onClick={() => screenshotInputRef.current?.click()}>
                  <ImagePlus className="h-4 w-4" aria-hidden="true" />
                  {t("feedback.feature.addScreenshot")}
                </Button>
              )}
              {attachmentError ? <p role="alert" className="text-sm text-destructive">{attachmentError}</p> : null}
            </div>

            <Alert>
              <AlertTitle>{t("feedback.feature.privacyTitle")}</AlertTitle>
              <AlertDescription>{t("feedback.feature.privacyDescription")}</AlertDescription>
            </Alert>

            {submitError ? (
              <Alert variant="destructive">
                <AlertTitle>{t("feedback.feature.submitErrorTitle")}</AlertTitle>
                <AlertDescription>{submitError} {t("feedback.feature.draftPreserved")}</AlertDescription>
              </Alert>
            ) : null}

            {confirmDiscard ? (
              <div className="flex flex-col gap-3 rounded-lg border border-destructive/40 p-4 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm">{t("feedback.feature.discardConfirm")}</p>
                <div className="flex gap-2">
                  <Button type="button" variant="outline" className="min-h-11" onClick={() => setConfirmDiscard(false)}>{t("common.cancel")}</Button>
                  <Button type="button" variant="destructive" className="min-h-11" onClick={() => { clearDraft(); setOpen(false) }}>
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    {t("feedback.feature.discard")}
                  </Button>
                </div>
              </div>
            ) : null}
              </div>
            </div>

            <DialogFooter className="gap-2 border-t pt-4 sm:space-x-0">
              {hasDraft ? (
                <Button type="button" variant="ghost" className="min-h-11" onClick={() => setConfirmDiscard(true)} disabled={submitting}>
                  {t("feedback.feature.discard")}
                </Button>
              ) : null}
              <Button type="button" variant="outline" className="min-h-11" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
              <Button type="button" className="min-h-11" onClick={handleSubmit} disabled={submitting || configLoading || Boolean(configError) || !config}>
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Lightbulb className="h-4 w-4" aria-hidden="true" />}
                {preparing ? t("feedback.feature.preparing") : submitting ? t("feedback.feature.submitting") : t("feedback.feature.submit")}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
