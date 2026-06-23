"use client"

import React, { useEffect, useMemo, useRef, useState } from "react"
import Image from "next/image"
import {
  AlertCircle,
  Archive,
  ArrowLeft,
  Bug,
  Camera,
  CheckCircle,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Loader2,
  Mail,
  Paperclip,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react"
import { z } from "zod"

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
  DialogTrigger,
} from "@/components/base/dialog"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { Textarea } from "@/components/base/textarea"
import {
  ApiError,
  createReportSupportCode,
  downloadDebugBundle,
  downloadOfflineReportPackage,
  fetchReportConfig,
  submitReport,
  type ReportAttachmentSummary,
  type ReportConfig,
  type ReportDiagnostics,
  type ReportMode,
  type ReportPreviewResponse,
  type ReportProblemPayload,
} from "@/lib/api"
import { t } from "@/lib/i18n"
import type { AppSettings, SystemInfo } from "@/lib/types"

const githubUsernamePattern = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/
const getchannelsUsernamePattern = /^[A-Za-z0-9_.-]{1,64}$/
const defaultMaxAttachmentBytes = 8 * 1024 * 1024
const defaultMaxTotalAttachmentBytes = 20 * 1024 * 1024
const defaultMaxScreenshotCount = 5
const maxDebugBundleEntries = 8
const maxDebugBundleUncompressedBytes = 20 * 1024 * 1024
const screenshotTypes = new Set(["image/png", "image/jpeg", "image/webp"])
const debugBundleTypes = new Set(["application/zip", "application/x-zip-compressed", "application/octet-stream"])
const publicReportAssetFilenames = new Set<string>()
const defaultSupportPortalUrl = "https://channelwatch.coderluii.dev/report"
const requiredDebugBundleMembers = new Set([
  "manifest.json",
  "settings_sanitized.json",
  "logs/app.log",
  "health_snapshot.json",
])

const optionalUsername = (pattern: RegExp, message: string) =>
  z
    .string()
    .trim()
    .transform((value) => value.replace(/^@+/, ""))
    .refine((value) => value === "" || pattern.test(value), { message })

const reportFormSchema = z.object({
  summary: z
    .string()
    .trim()
    .min(1, { message: t("supportReport.error.summaryRequired") })
    .max(500, { message: t("supportReport.error.summaryMax") }),
  expected: z
    .string()
    .trim()
    .max(2000, { message: t("supportReport.error.expectedMax") }),
  getchannels_username: optionalUsername(
    getchannelsUsernamePattern,
    t("supportReport.error.getchannels"),
  ),
  github_username: optionalUsername(githubUsernamePattern, t("supportReport.error.github")),
  email: z
    .string()
    .trim()
    .refine((value) => value === "" || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value), {
      message: t("supportReport.error.email"),
    }),
})

type ReportForm = z.infer<typeof reportFormSchema>
type ReportField = keyof ReportForm

const initialForm: ReportForm = {
  summary: "",
  expected: "",
  getchannels_username: "",
  github_username: "",
  email: "",
}

interface ReportProblemDialogProps {
  systemInfo: SystemInfo | null
  appSettings: AppSettings | null
}

function modeLabel(mode: ReportMode): string {
  if (mode === "email-test") return t("supportReport.mode.emailTest")
  if (mode === "live") return t("supportReport.mode.live")
  return t("supportReport.mode.dryRun")
}

function activeProviders(settings: AppSettings | null): string[] {
  if (!settings) return []
  const providers: string[] = []
  if (settings.apprise_pushover) providers.push(t("provider.pushover.name"))
  if (settings.apprise_discord) providers.push(t("provider.discord.name"))
  if (settings.apprise_email) providers.push(t("provider.email.name"))
  if (settings.apprise_telegram) providers.push(t("provider.telegram.name"))
  if (settings.apprise_slack) providers.push(t("provider.slack.name"))
  if (settings.apprise_gotify) providers.push(t("provider.gotify.name"))
  if (settings.apprise_matrix) providers.push(t("provider.matrix.name"))
  if (settings.apprise_custom) providers.push(t("provider.custom.name"))
  return providers
}

function monitoringSummary(systemInfo: SystemInfo | null): string[] {
  const statuses = systemInfo?.dvr_status ?? []
  if (statuses.length === 0) return []
  const counts = new Map<string, number>()
  for (const dvr of statuses) {
    const status =
      dvr.monitoring_ready === false
        ? (dvr.monitoring_status || "degraded").toLowerCase()
        : dvr.connected
          ? "healthy"
          : "offline"
    counts.set(status, (counts.get(status) ?? 0) + 1)
  }
  return [...counts.entries()].map(([status, count]) => `${status}: ${count}`)
}

function buildDiagnostics(
  systemInfo: SystemInfo | null,
  appSettings: AppSettings | null,
): ReportDiagnostics {
  const dvrStatus = systemInfo?.dvr_status ?? []
  const configuredDvrs =
    dvrStatus.length ||
    (appSettings?.dvr_servers ?? []).filter((server) => server.enabled && !server.deleted_at).length
  return {
    channelwatch_version: systemInfo?.channelwatch_version ?? null,
    dvr_count: configuredDvrs,
    connected_dvr_count: dvrStatus.filter((dvr) => dvr.connected).length,
    core_status: systemInfo?.core_status ?? null,
    monitoring_statuses: monitoringSummary(systemInfo),
    notification_providers: activeProviders(appSettings),
    feature_toggles: {
      channel_watching: Boolean(appSettings?.alert_channel_watching),
      vod_watching: Boolean(appSettings?.alert_vod_watching),
      disk_space: Boolean(appSettings?.alert_disk_space),
      recording_events: Boolean(appSettings?.alert_recording_events),
      stream_counter: Boolean(appSettings?.stream_count),
    },
  }
}

function redactPublicText(value: string): string {
  return value
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, "[redacted-email]")
    .replace(
      /\b(api[_-]?key|token|secret|password|passwd|webhook|dsn)\s*[:=]\s*([^\s,;]+)/gi,
      (_match, key) => `${key}=[redacted]`,
    )
    .replace(/\b[A-Za-z0-9_-]{32,}\b/g, "[redacted-secret]")
}

function renderIssueTitle(payload: ReportProblemPayload): string {
  const summary = redactPublicText(payload.summary)
  return `[In-App] ${summary.length > 90 ? `${summary.slice(0, 87).trim()}...` : summary}`
}

function isPrivateAttachmentNameExposed(issueBody: string, filename: string): boolean {
  return !publicReportAssetFilenames.has(filename) && issueBody.includes(filename)
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function getChannelsProfileUrl(username: string): string {
  return `https://community.getchannels.com/u/${encodeURIComponent(username)}`
}

function githubProfileUrl(username: string): string {
  return `https://github.com/${encodeURIComponent(username)}`
}

function renderPublicContact(payload: ReportProblemPayload): string {
  const lines: string[] = []
  if (payload.getchannels_username) {
    lines.push(
      `- GetChannels community: [@${payload.getchannels_username}](${getChannelsProfileUrl(payload.getchannels_username)})`,
    )
  }
  if (payload.github_username) {
    lines.push(`- GitHub: [@${payload.github_username}](${githubProfileUrl(payload.github_username)})`)
  }
  return lines.length > 0 ? lines.join("\n") : "- No public contact handle provided."
}

function markdownTableValue(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\r?\n/g, " ")
}

function renderDiagnostics(diagnostics: ReportDiagnostics): string {
  const enabled = [
    diagnostics.feature_toggles.channel_watching ? "Channel watching" : null,
    diagnostics.feature_toggles.vod_watching ? "VOD watching" : null,
    diagnostics.feature_toggles.disk_space ? "Disk space" : null,
    diagnostics.feature_toggles.recording_events ? "Recording events" : null,
    diagnostics.feature_toggles.stream_counter ? "Stream counter" : null,
  ].filter(Boolean)
  return [
    "| Field | Value |",
    "| --- | --- |",
    `| ChannelWatch version | ${markdownTableValue(diagnostics.channelwatch_version || "Unknown")} |`,
    `| DVRs configured | ${diagnostics.dvr_count} |`,
    `| DVRs connected | ${diagnostics.connected_dvr_count} |`,
    `| Core status | ${markdownTableValue(diagnostics.core_status || "Unknown")} |`,
    `| Monitoring | ${markdownTableValue(diagnostics.monitoring_statuses.length ? diagnostics.monitoring_statuses.join(", ") : "Not reported")} |`,
    `| Notification providers | ${markdownTableValue(diagnostics.notification_providers.length ? diagnostics.notification_providers.join(", ") : "None reported")} |`,
    `| Enabled feature toggles | ${markdownTableValue(enabled.length ? enabled.join(", ") : "None reported")} |`,
  ].join("\n")
}

function renderIssueBody(payload: ReportProblemPayload): string {
  return [
    "# ChannelWatch Support Report",
    `## Summary\n\n${redactPublicText(payload.summary)}`,
    `## Expected behavior\n\n${redactPublicText(payload.expected || "Not provided.")}`,
    `## Reporter\n\n${renderPublicContact(payload)}`,
    `## Diagnostics\n\n${renderDiagnostics(payload.diagnostics)}`,
  ].join("\n\n")
}

function diagnosticsRows(diagnostics: ReportDiagnostics): Array<[string, string]> {
  const enabled = [
    diagnostics.feature_toggles.channel_watching ? "Channel watching" : null,
    diagnostics.feature_toggles.vod_watching ? "VOD" : null,
    diagnostics.feature_toggles.disk_space ? "Disk space" : null,
    diagnostics.feature_toggles.recording_events ? "Recordings" : null,
    diagnostics.feature_toggles.stream_counter ? "Stream counter" : null,
  ].filter(Boolean)
  return [
    ["Version", diagnostics.channelwatch_version || "Unknown"],
    ["DVRs", `${diagnostics.connected_dvr_count} connected of ${diagnostics.dvr_count}`],
    ["Core", diagnostics.core_status || "Unknown"],
    ["Monitoring", diagnostics.monitoring_statuses.join(", ") || "Not reported"],
    ["Providers", diagnostics.notification_providers.join(", ") || "None reported"],
    ["Feature toggles", enabled.join(", ") || "None reported"],
  ]
}

function fieldError(errors: Partial<Record<ReportField, string>>, field: ReportField): string | undefined {
  return errors[field]
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${Math.round(value / 1024)} KB`
  return `${value} B`
}

function safeFileType(file: File): string {
  return file.type || "application/octet-stream"
}

function debugBundleFilename(): string {
  return `channelwatch_debug_${new Date().toISOString().replace(/[:.]/g, "-")}.zip`
}

function offlinePackageFilename(): string {
  return `channelwatch_support_report_${new Date().toISOString().replace(/[:.]/g, "-")}.zip`
}

function screenshotFilename(contentType: string, index: number): string {
  const suffix =
    contentType === "image/jpeg" ? "jpg" : contentType === "image/webp" ? "webp" : "png"
  return `channelwatch_screenshot_${new Date().toISOString().replace(/[:.]/g, "-")}_${index + 1}.${suffix}`
}

function normalizeScreenshotFile(file: File, index: number): File {
  if (/\.(png|jpe?g|webp)$/i.test(file.name)) return file
  const contentType = safeFileType(file)
  return new File([file], screenshotFilename(contentType, index), { type: contentType })
}

function fileRows(
  screenshots: File[],
  debugBundle: File | null,
): Array<{ filename: string; contentType: string; sizeBytes: number; kind: "screenshot" | "debug_bundle" }> {
  return [
    ...screenshots.map((file) => ({
      filename: file.name,
      contentType: safeFileType(file),
      sizeBytes: file.size,
      kind: "screenshot" as const,
    })),
    ...(debugBundle
      ? [
          {
            filename: debugBundle.name,
            contentType: safeFileType(debugBundle),
            sizeBytes: debugBundle.size,
            kind: "debug_bundle" as const,
          },
        ]
      : []),
  ]
}

function validateAttachmentSelection(
  config: ReportConfig | null,
  screenshots: File[],
  debugBundle: File | null,
): string | null {
  if (!config?.attachments_enabled && (screenshots.length > 0 || debugBundle)) {
    return t("supportReport.error.attachmentsDisabled")
  }
  const maxScreenshotCount = config?.max_screenshot_count ?? defaultMaxScreenshotCount
  const maxAttachmentBytes = config?.max_attachment_bytes ?? defaultMaxAttachmentBytes
  const maxTotalBytes = config?.max_total_attachment_bytes ?? defaultMaxTotalAttachmentBytes
  if (screenshots.length > maxScreenshotCount) {
    return t("supportReport.error.tooManyScreenshots").replace("{count}", String(maxScreenshotCount))
  }
  const files = fileRows(screenshots, debugBundle)
  const totalBytes = files.reduce((sum, file) => sum + file.sizeBytes, 0)
  if (totalBytes > maxTotalBytes) {
    return t("supportReport.error.totalAttachmentsTooLarge").replace("{size}", formatBytes(maxTotalBytes))
  }
  for (const file of screenshots) {
    const type = safeFileType(file)
    const lowerName = file.name.toLowerCase()
    if (!screenshotTypes.has(type) || !/\.(png|jpe?g|webp)$/.test(lowerName)) {
      return t("supportReport.error.screenshotType")
    }
    if (file.size > maxAttachmentBytes) {
      return t("supportReport.error.attachmentTooLarge").replace("{size}", formatBytes(maxAttachmentBytes))
    }
  }
  if (debugBundle) {
    const type = safeFileType(debugBundle)
    if (!debugBundleTypes.has(type) || !debugBundle.name.toLowerCase().endsWith(".zip")) {
      return t("supportReport.error.debugBundleType")
    }
    if (debugBundle.size > maxAttachmentBytes) {
      return t("supportReport.error.attachmentTooLarge").replace("{size}", formatBytes(maxAttachmentBytes))
    }
  }
  return null
}

function readUint16LE(bytes: Uint8Array, offset: number): number {
  if (offset + 2 > bytes.length) throw new Error("out-of-bounds")
  return bytes[offset] | (bytes[offset + 1] << 8)
}

function readUint32LE(bytes: Uint8Array, offset: number): number {
  if (offset + 4 > bytes.length) throw new Error("out-of-bounds")
  return (
    bytes[offset] |
    (bytes[offset + 1] << 8) |
    (bytes[offset + 2] << 16) |
    (bytes[offset + 3] << 24)
  ) >>> 0
}

function isUnsafeZipPath(name: string): boolean {
  return (
    !name ||
    name.includes("\\") ||
    name.startsWith("/") ||
    name.includes("\0") ||
    name.includes(":") ||
    `/${name}`.includes("/../")
  )
}

function assertChannelWatchDebugBundleZip(bytes: Uint8Array): void {
  if (
    bytes.length < 22 ||
    !(
      (bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04) ||
      (bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x05 && bytes[3] === 0x06)
    )
  ) {
    throw new Error("invalid-zip")
  }

  const searchStart = Math.max(0, bytes.length - 65557)
  let eocdOffset = -1
  for (let index = bytes.length - 22; index >= searchStart; index -= 1) {
    if (
      bytes[index] === 0x50 &&
      bytes[index + 1] === 0x4b &&
      bytes[index + 2] === 0x05 &&
      bytes[index + 3] === 0x06
    ) {
      eocdOffset = index
      break
    }
  }
  if (eocdOffset < 0) throw new Error("missing-eocd")

  const entryCount = readUint16LE(bytes, eocdOffset + 10)
  const centralDirectorySize = readUint32LE(bytes, eocdOffset + 12)
  const centralDirectoryOffset = readUint32LE(bytes, eocdOffset + 16)
  if (
    entryCount <= 0 ||
    entryCount > maxDebugBundleEntries ||
    centralDirectoryOffset + centralDirectorySize > bytes.length
  ) {
    throw new Error("invalid-directory")
  }

  const decoder = new TextDecoder()
  const roots = new Set<string>()
  const relatives = new Set<string>()
  let totalUncompressed = 0
  let offset = centralDirectoryOffset
  for (let index = 0; index < entryCount; index += 1) {
    if (
      bytes[offset] !== 0x50 ||
      bytes[offset + 1] !== 0x4b ||
      bytes[offset + 2] !== 0x01 ||
      bytes[offset + 3] !== 0x02
    ) {
      throw new Error("invalid-entry")
    }
    const flags = readUint16LE(bytes, offset + 8)
    const uncompressedSize = readUint32LE(bytes, offset + 24)
    const filenameLength = readUint16LE(bytes, offset + 28)
    const extraLength = readUint16LE(bytes, offset + 30)
    const commentLength = readUint16LE(bytes, offset + 32)
    const nameStart = offset + 46
    const nameEnd = nameStart + filenameLength
    if (nameEnd > bytes.length) throw new Error("invalid-name")
    const name = decoder.decode(bytes.slice(nameStart, nameEnd))
    offset = nameEnd + extraLength + commentLength
    if (name.endsWith("/")) continue
    if (flags & 0x1) throw new Error("encrypted")
    if (isUnsafeZipPath(name) || !name.includes("/")) throw new Error("unsafe-path")
    totalUncompressed += uncompressedSize
    if (totalUncompressed > maxDebugBundleUncompressedBytes) throw new Error("expanded-too-large")
    const [root, ...rest] = name.split("/")
    roots.add(root)
    relatives.add(rest.join("/"))
  }

  if (roots.size !== 1) throw new Error("invalid-root")
  const [root] = [...roots]
  if (!root.startsWith("channelwatch_debug_")) throw new Error("not-channelwatch")
  for (const required of requiredDebugBundleMembers) {
    if (!relatives.has(required)) throw new Error("missing-required")
  }
  for (const relative of relatives) {
    if (!requiredDebugBundleMembers.has(relative)) throw new Error("unsupported-file")
  }
}

async function validateDebugBundleFile(
  config: ReportConfig | null,
  screenshots: File[],
  debugBundle: File,
): Promise<string | null> {
  const selectionError = validateAttachmentSelection(config, screenshots, debugBundle)
  if (selectionError) return selectionError
  try {
    assertChannelWatchDebugBundleZip(new Uint8Array(await debugBundle.arrayBuffer()))
  } catch {
    return t("supportReport.error.debugBundleInvalid")
  }
  return null
}

function privateAttachmentRows(
  serverAttachments: ReportAttachmentSummary[] | null,
  screenshots: File[],
  debugBundle: File | null,
) {
  if (serverAttachments) {
    return serverAttachments.map((item) => ({
      filename: item.filename,
      contentType: item.content_type,
      sizeBytes: item.size_bytes,
      kind: item.kind,
      digest: item.sha256.slice(0, 12),
    }))
  }
  return fileRows(screenshots, debugBundle).map((item) => ({ ...item, digest: null }))
}

function ReportPreviewCard({
  title,
  payload,
}: {
  title: string
  payload: ReportProblemPayload
}) {
  const contacts = [
    payload.getchannels_username
      ? {
          label: "GetChannels",
          value: `@${payload.getchannels_username}`,
          href: getChannelsProfileUrl(payload.getchannels_username),
        }
      : null,
    payload.github_username
      ? {
          label: "GitHub",
          value: `@${payload.github_username}`,
          href: githubProfileUrl(payload.github_username),
        }
      : null,
  ].filter((item): item is { label: string; value: string; href: string } => Boolean(item))

  return (
    <div className="overflow-hidden rounded-md border border-border bg-muted/25">
      <div className="border-b border-border bg-background/60 px-3 py-2">
        <p className="text-sm font-semibold">{title}</p>
      </div>
      <div className="space-y-3 p-3 text-sm">
        <section className="border-l-2 border-primary/60 pl-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Summary
          </h4>
          <p className="mt-1 break-words">{redactPublicText(payload.summary)}</p>
        </section>
        <section className="border-l-2 border-primary/60 pl-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Expected behavior
          </h4>
          <p className="mt-1 break-words">{redactPublicText(payload.expected || "Not provided.")}</p>
        </section>
        <section className="border-l-2 border-primary/60 pl-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Reporter
          </h4>
          {contacts.length > 0 ? (
            <ul className="mt-1 space-y-1">
              {contacts.map((contact) => (
                <li key={contact.label}>
                  <span className="text-muted-foreground">{contact.label}: </span>
                  <a
                    className="inline-flex items-center gap-1 text-primary hover:underline"
                    href={contact.href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {contact.value}
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-muted-foreground">No public contact handle provided.</p>
          )}
        </section>
        <section className="border-l-2 border-primary/60 pl-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Diagnostics
          </h4>
          <dl className="mt-2 overflow-hidden rounded-md border border-border">
            {diagnosticsRows(payload.diagnostics).map(([label, value]) => (
              <div
                key={label}
                className="grid gap-1 border-b border-border px-3 py-2 last:border-b-0 sm:grid-cols-[170px_minmax(0,1fr)]"
              >
                <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {label}
                </dt>
                <dd className="break-words">{value}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </div>
  )
}

export function ReportProblemDialog({ systemInfo, appSettings }: ReportProblemDialogProps) {
  const screenshotInputRef = useRef<HTMLInputElement | null>(null)
  const debugBundleInputRef = useRef<HTMLInputElement | null>(null)
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState<"form" | "review" | "success">("form")
  const [config, setConfig] = useState<ReportConfig | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [loadingConfig, setLoadingConfig] = useState(false)
  const [form, setForm] = useState<ReportForm>(initialForm)
  const [errors, setErrors] = useState<Partial<Record<ReportField, string>>>({})
  const [draftPayload, setDraftPayload] = useState<ReportProblemPayload | null>(null)
  const [serverPreview, setServerPreview] = useState<ReportPreviewResponse | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [screenshots, setScreenshots] = useState<File[]>([])
  const [debugBundle, setDebugBundle] = useState<File | null>(null)
  const [debugBundleLoading, setDebugBundleLoading] = useState(false)
  const [screenshotDropActive, setScreenshotDropActive] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [manualUploadOpen, setManualUploadOpen] = useState(false)
  const [supportCodeStatus, setSupportCodeStatus] = useState<"idle" | "copied" | "error">("idle")
  const [offlinePackageStatus, setOfflinePackageStatus] = useState<
    "idle" | "downloading" | "downloaded" | "error"
  >("idle")

  const diagnostics = useMemo(
    () => buildDiagnostics(systemInfo, appSettings),
    [systemInfo, appSettings],
  )

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoadingConfig(true)
    setConfigError(null)
    fetchReportConfig()
      .then((nextConfig) => {
        if (!cancelled) setConfig(nextConfig)
      })
      .catch((error) => {
        if (!cancelled) {
          setConfigError(error instanceof Error ? error.message : t("supportReport.configError"))
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingConfig(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  useEffect(() => {
    if (open) return
    setStep("form")
    setErrors({})
    setDraftPayload(null)
    setServerPreview(null)
    setSubmitError(null)
    setAttachmentError(null)
    setScreenshots([])
    setDebugBundle(null)
    setDebugBundleLoading(false)
    setScreenshotDropActive(false)
    setSubmitting(false)
    setManualUploadOpen(false)
    setSupportCodeStatus("idle")
    setOfflinePackageStatus("idle")
  }, [open])

  const updateField = (field: ReportField, value: string) => {
    setForm((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
    setSupportCodeStatus("idle")
    setOfflinePackageStatus("idle")
  }

  const buildPayload = (validated: ReportForm): ReportProblemPayload => ({
    summary: validated.summary,
    expected: validated.expected || null,
    getchannels_username: validated.getchannels_username || null,
    github_username: validated.github_username || null,
    email: validated.email || null,
    diagnostics,
    turnstile_token: null,
  })

  const addScreenshots = (files: File[]) => {
    const selected = files.map((file, index) => normalizeScreenshotFile(file, index))
    if (selected.length === 0) return
    const nextScreenshots = [...screenshots, ...selected]
    const error = validateAttachmentSelection(config, nextScreenshots, debugBundle)
    if (error) {
      setAttachmentError(error)
      return
    }
    setAttachmentError(null)
    setScreenshots(nextScreenshots)
  }

  const handleScreenshotChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    addScreenshots(Array.from(event.target.files ?? []))
    event.target.value = ""
  }

  const handleScreenshotPaste = (event: React.ClipboardEvent) => {
    const pastedFiles = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file))
      .filter((file) => screenshotTypes.has(safeFileType(file)))
    if (pastedFiles.length === 0) return
    event.preventDefault()
    addScreenshots(pastedFiles)
  }

  const handleScreenshotDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setScreenshotDropActive(false)
    addScreenshots(Array.from(event.dataTransfer.files ?? []))
  }

  const removeScreenshot = (index: number) => {
    const nextScreenshots = screenshots.filter((_file, fileIndex) => fileIndex !== index)
    setScreenshots(nextScreenshots)
    setAttachmentError(validateAttachmentSelection(config, nextScreenshots, debugBundle))
  }

  const handleDebugBundleChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null
    event.target.value = ""
    if (!selected) return
    const error = await validateDebugBundleFile(config, screenshots, selected)
    if (error) {
      setAttachmentError(error)
      return
    }
    setAttachmentError(null)
    setDebugBundle(selected)
  }

  const handleCreateDebugBundle = async () => {
    setDebugBundleLoading(true)
    setAttachmentError(null)
    try {
      const blob = await downloadDebugBundle()
      const file = new File([blob], debugBundleFilename(), {
        type: blob.type || "application/zip",
      })
      const error = await validateDebugBundleFile(config, screenshots, file)
      if (error) {
        setAttachmentError(error)
        return
      }
      setDebugBundle(file)
    } catch (error) {
      setAttachmentError(error instanceof Error ? error.message : t("supportReport.error.debugBundleCreate"))
    } finally {
      setDebugBundleLoading(false)
    }
  }

  const handleReview = () => {
    const parsed = reportFormSchema.safeParse(form)
    if (!parsed.success) {
      const nextErrors: Partial<Record<ReportField, string>> = {}
      for (const issue of parsed.error.issues) {
        const field = issue.path[0] as ReportField | undefined
        if (field && !nextErrors[field]) nextErrors[field] = issue.message
      }
      setErrors(nextErrors)
      return
    }

    const payload = buildPayload(parsed.data)
    const maxBytes = config?.max_bytes ?? 262144
    const payloadBytes = new TextEncoder().encode(JSON.stringify(payload)).length
    if (payloadBytes > maxBytes) {
      setErrors({ summary: t("supportReport.error.payloadTooLarge") })
      return
    }
    const attachmentValidation = validateAttachmentSelection(config, screenshots, debugBundle)
    if (attachmentValidation) {
      setAttachmentError(attachmentValidation)
      return
    }

    setDraftPayload(payload)
    setSubmitError(null)
    setManualUploadOpen(false)
    setSupportCodeStatus("idle")
    setOfflinePackageStatus("idle")
    setStep("review")
  }

  const handleSubmit = async () => {
    if (!draftPayload) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const response = await submitReport(
        config?.endpoint || "/api/v1/support/report-dry-run",
        draftPayload,
        { screenshots, debugBundle },
      )
      if (response.email_in_public_issue) {
        throw new Error("Email was detected in the report preview.")
      }
      for (const attachment of response.attachments ?? []) {
        if (isPrivateAttachmentNameExposed(response.issue_body, attachment.filename)) {
          throw new Error("Attachment filename was detected in the report preview.")
        }
      }
      setServerPreview(response)
      setStep("success")
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : t("supportReport.error.submit")
      setSubmitError(message)
      setManualUploadOpen(true)
    } finally {
      setSubmitting(false)
    }
  }

  const handleCopySupportCode = async () => {
    if (!draftPayload) return
    setSupportCodeStatus("idle")
    try {
      await navigator.clipboard.writeText(createReportSupportCode(draftPayload))
      setSupportCodeStatus("copied")
    } catch {
      setSupportCodeStatus("error")
    }
  }

  const handleDownloadOfflinePackage = async () => {
    if (!draftPayload || offlinePackageStatus === "downloading") return
    setOfflinePackageStatus("downloading")
    try {
      const blob = await downloadOfflineReportPackage(draftPayload, { screenshots, debugBundle })
      downloadBlob(blob, offlinePackageFilename())
      setOfflinePackageStatus("downloaded")
    } catch {
      setOfflinePackageStatus("error")
    }
  }

  const noContact = !form.getchannels_username.trim() && !form.github_username.trim() && !form.email.trim()
  const activePayload = serverPreview ? null : draftPayload
  const previewTitle = serverPreview?.issue_title || (activePayload ? renderIssueTitle(activePayload) : "")
  const supportPortalUrl = config?.portal_url || defaultSupportPortalUrl
  const attachmentRows = privateAttachmentRows(
    serverPreview?.attachments ?? null,
    screenshots,
    debugBundle,
  )
  const hasAttachments = attachmentRows.length > 0
  const manualUploadPanel = draftPayload ? (
    <section
      className="rounded-lg border border-primary/35 bg-primary/5 p-4 text-sm"
      data-testid="manual-upload-panel"
    >
      <div className="flex items-start gap-3">
        <ExternalLink className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-foreground">{t("supportReport.manual.title")}</h3>
          <p className="mt-1 text-muted-foreground">{t("supportReport.manual.description")}</p>
          <div className="mt-3 rounded-md border border-border bg-background/70 px-3 py-2">
            <div className="text-xs font-medium uppercase text-muted-foreground">
              {t("supportReport.manual.uploadSite")}
            </div>
            <div className="mt-1 break-all text-xs text-foreground">{supportPortalUrl}</div>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="justify-center md:justify-start"
              onClick={handleCopySupportCode}
            >
              <Copy className="h-4 w-4" />
              {t("supportReport.manual.copyCode")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="justify-center md:justify-start"
              onClick={handleDownloadOfflinePackage}
              disabled={offlinePackageStatus === "downloading"}
            >
              {offlinePackageStatus === "downloading" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {offlinePackageStatus === "downloading"
                ? t("supportReport.manual.packageDownloading")
                : t("supportReport.manual.downloadPackage")}
            </Button>
            <a
              href={supportPortalUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background md:justify-start"
            >
              <ExternalLink className="h-4 w-4" />
              {t("supportReport.manual.openPortal")}
            </a>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            {t("supportReport.manual.privacyNote")}
          </p>
          <div aria-live="polite" className="mt-2 min-h-4 text-xs text-muted-foreground">
            {supportCodeStatus === "copied" && t("supportReport.manual.codeCopied")}
            {supportCodeStatus === "error" && (
              <span className="text-destructive">{t("supportReport.manual.codeCopyFailed")}</span>
            )}
            {offlinePackageStatus === "downloaded" && t("supportReport.manual.packageDownloaded")}
            {offlinePackageStatus === "error" && (
              <span className="text-destructive">{t("supportReport.manual.packageDownloadFailed")}</span>
            )}
          </div>
        </div>
      </div>
    </section>
  ) : null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 text-xs flex-shrink-0"
          aria-label={t("diagnostics.reportProblem.aria")}
          data-testid="report-problem-open"
        >
          <Bug className="h-3.5 w-3.5" />
          {t("diagnostics.reportProblem.btn")}
        </Button>
      </DialogTrigger>
      <DialogContent className="w-[calc(100vw-2rem)] max-w-3xl overflow-hidden p-0">
        <div className="max-h-[88vh] overflow-x-hidden overflow-y-auto" data-testid="report-problem-scroll-body">
          <div className="border-b border-border/80 bg-card/60 px-5 py-4 sm:px-6">
            <DialogHeader className="text-left">
              <div className="flex items-start gap-3 pr-8">
                <Image
                  src="/images/channelwatch-logo.png"
                  alt=""
                  width={40}
                  height={40}
                  className="mt-0.5 h-10 w-10 rounded-md"
                />
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <DialogTitle>{t("supportReport.title")}</DialogTitle>
                    <Badge variant="outline" className="h-6">
                      {modeLabel(config?.mode || "dry-run")}
                    </Badge>
                  </div>
                  <DialogDescription>{t("supportReport.description")}</DialogDescription>
                </div>
              </div>
            </DialogHeader>
          </div>

          <div className="space-y-4 px-5 py-5 sm:px-6">
            {loadingConfig && (
              <div className="flex items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("supportReport.loadingConfig")}
              </div>
            )}

            {configError && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>{t("supportReport.configError")}</AlertTitle>
                <AlertDescription>{configError}</AlertDescription>
              </Alert>
            )}

            {step === "form" && (
              <div className="space-y-5" data-testid="report-problem-form">
                <div className="space-y-2">
                  <Label htmlFor="report-summary">{t("supportReport.form.summaryLabel")}</Label>
                  <Textarea
                    id="report-summary"
                    value={form.summary}
                    onChange={(event) => updateField("summary", event.target.value)}
                    placeholder={t("supportReport.form.summaryPlaceholder")}
                    aria-invalid={Boolean(fieldError(errors, "summary"))}
                    aria-describedby={fieldError(errors, "summary") ? "report-summary-error" : undefined}
                    className="min-h-[112px]"
                    maxLength={500}
                  />
                  {fieldError(errors, "summary") && (
                    <p id="report-summary-error" className="text-sm text-destructive" role="alert">
                      {fieldError(errors, "summary")}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="report-expected">
                    {t("supportReport.form.expectedLabel")}{" "}
                    <span className="text-muted-foreground">({t("supportReport.form.optional")})</span>
                  </Label>
                  <Textarea
                    id="report-expected"
                    value={form.expected}
                    onChange={(event) => updateField("expected", event.target.value)}
                    placeholder={t("supportReport.form.expectedPlaceholder")}
                    aria-invalid={Boolean(fieldError(errors, "expected"))}
                    aria-describedby={fieldError(errors, "expected") ? "report-expected-error" : undefined}
                    maxLength={2000}
                  />
                  {fieldError(errors, "expected") && (
                    <p id="report-expected-error" className="text-sm text-destructive" role="alert">
                      {fieldError(errors, "expected")}
                    </p>
                  )}
                </div>

                <div className="grid gap-3 md:grid-cols-3">
                  <div className="space-y-2">
                    <Label htmlFor="report-getchannels" className="inline-flex items-baseline gap-1 whitespace-nowrap">
                      <span>{t("supportReport.form.getchannelsLabel")}</span>
                      {" "}
                      <span className="text-xs font-medium text-muted-foreground">
                        ({t("supportReport.form.optional")})
                      </span>
                    </Label>
                    <Input
                      id="report-getchannels"
                      value={form.getchannels_username}
                      onChange={(event) => updateField("getchannels_username", event.target.value)}
                      placeholder={t("supportReport.form.usernamePlaceholder")}
                      aria-invalid={Boolean(fieldError(errors, "getchannels_username"))}
                      aria-describedby={
                        fieldError(errors, "getchannels_username")
                          ? "report-getchannels-error"
                          : undefined
                      }
                    />
                    {fieldError(errors, "getchannels_username") && (
                      <p id="report-getchannels-error" className="text-sm text-destructive" role="alert">
                        {fieldError(errors, "getchannels_username")}
                      </p>
                    )}
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="report-github" className="inline-flex items-baseline gap-1 whitespace-nowrap">
                      <span>{t("supportReport.form.githubLabel")}</span>
                      {" "}
                      <span className="text-xs font-medium text-muted-foreground">
                        ({t("supportReport.form.optional")})
                      </span>
                    </Label>
                    <Input
                      id="report-github"
                      value={form.github_username}
                      onChange={(event) => updateField("github_username", event.target.value)}
                      placeholder={t("supportReport.form.usernamePlaceholder")}
                      aria-invalid={Boolean(fieldError(errors, "github_username"))}
                      aria-describedby={fieldError(errors, "github_username") ? "report-github-error" : undefined}
                    />
                    {fieldError(errors, "github_username") && (
                      <p id="report-github-error" className="text-sm text-destructive" role="alert">
                        {fieldError(errors, "github_username")}
                      </p>
                    )}
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="report-email" className="inline-flex items-baseline gap-1 whitespace-nowrap">
                      <span>{t("supportReport.form.emailLabel")}</span>
                      {" "}
                      <span className="text-xs font-medium text-muted-foreground">
                        ({t("supportReport.form.optional")})
                      </span>
                    </Label>
                    <Input
                      id="report-email"
                      type="email"
                      value={form.email}
                      onChange={(event) => updateField("email", event.target.value)}
                      placeholder={t("supportReport.form.emailPlaceholder")}
                      aria-invalid={Boolean(fieldError(errors, "email"))}
                      aria-describedby={
                        fieldError(errors, "email") ? "report-email-error" : "report-email-help"
                      }
                    />
                    {fieldError(errors, "email") ? (
                      <p id="report-email-error" className="text-sm text-destructive" role="alert">
                        {fieldError(errors, "email")}
                      </p>
                    ) : (
                      <p id="report-email-help" className="text-xs text-muted-foreground">
                        {t("supportReport.form.emailHelp")}
                      </p>
                    )}
                  </div>
                </div>

                {noContact && (
                  <div className="rounded-md border border-amber-500/60 bg-background px-3 py-3 text-sm">
                    <div className="font-medium text-foreground">
                      {t("supportReport.form.contactWarningTitle")}
                    </div>
                    <p className="mt-1 text-muted-foreground">
                      {t("supportReport.form.contactWarningDesc")}
                    </p>
                  </div>
                )}

                <section className="space-y-3 rounded-md border border-border bg-background p-3">
                  <div className="flex items-start gap-2">
                    <Paperclip className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                    <div>
                      <h3 className="text-sm font-semibold">{t("supportReport.form.attachmentsTitle")}</h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {t("supportReport.form.attachmentsDesc")}
                      </p>
                    </div>
                  </div>

                  <div className="grid gap-3">
                    <div
                      data-testid="report-screenshot-dropzone"
                      tabIndex={0}
                      className={`space-y-3 rounded-md border p-3 transition-colors ${
                        screenshotDropActive
                          ? "border-primary bg-primary/10"
                          : "border-border bg-muted/20 focus-within:border-primary focus-within:ring-1 focus-within:ring-primary/40"
                      }`}
                      onPaste={handleScreenshotPaste}
                      onDragOver={(event) => {
                        event.preventDefault()
                        setScreenshotDropActive(true)
                      }}
                      onDragLeave={() => setScreenshotDropActive(false)}
                      onDrop={handleScreenshotDrop}
                    >
                      <div className="flex items-start gap-2">
                        <Camera className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                        <div>
                          <Label htmlFor="report-screenshots">
                            {t("supportReport.form.screenshotsLabel")}
                          </Label>
                          <p id="report-screenshots-help" className="mt-1 text-xs text-muted-foreground">
                            {t("supportReport.form.screenshotsHelp").replace(
                              "{count}",
                              String(config?.max_screenshot_count ?? defaultMaxScreenshotCount),
                            )}
                          </p>
                        </div>
                      </div>
                      <div className="rounded-md border border-dashed border-border bg-background/70 px-3 py-4 text-center">
                        <div className="text-sm font-medium text-foreground">
                          {t("supportReport.form.screenshotsDropTitle")}
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {t("supportReport.form.screenshotsDropDesc")}
                        </p>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="mt-3"
                          onClick={() => screenshotInputRef.current?.click()}
                        >
                          <Upload className="h-4 w-4" />
                          {t("supportReport.form.screenshotsChoose")}
                        </Button>
                      </div>
                      <Input
                        ref={screenshotInputRef}
                        id="report-screenshots"
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        multiple
                        onChange={handleScreenshotChange}
                        aria-describedby="report-screenshots-help"
                        className="hidden"
                      />
                      <p className="text-xs text-muted-foreground">
                        {t("supportReport.form.screenshotsPasteAnywhere")}
                      </p>
                    </div>

                    <div className="space-y-3 rounded-md border border-border bg-muted/20 p-3">
                      <div className="flex items-center gap-2">
                        <Archive className="h-4 w-4 text-primary" />
                        <Label htmlFor="report-debug-bundle">{t("supportReport.form.debugBundleLabel")}</Label>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <Button
                          type="button"
                          variant="outline"
                          onClick={handleCreateDebugBundle}
                          disabled={debugBundleLoading}
                          className="h-auto min-h-10 min-w-0 justify-start whitespace-normal text-left leading-snug"
                        >
                          {debugBundleLoading ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Upload className="h-4 w-4" />
                          )}
                          {debugBundleLoading
                            ? t("supportReport.form.debugBundleCreating")
                            : t("supportReport.form.debugBundleCreate")}
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          className="h-auto min-h-10 min-w-0 justify-start whitespace-normal text-left leading-snug"
                          onClick={() => debugBundleInputRef.current?.click()}
                        >
                          <Paperclip className="h-4 w-4" />
                          {t("supportReport.form.debugBundleAttachExisting")}
                        </Button>
                        <Input
                          ref={debugBundleInputRef}
                          id="report-debug-bundle"
                          type="file"
                          accept=".zip,application/zip,application/x-zip-compressed"
                          onChange={handleDebugBundleChange}
                          aria-describedby="report-debug-bundle-help"
                          className="hidden"
                        />
                      </div>
                      <p id="report-debug-bundle-help" className="text-xs text-muted-foreground">
                        {t("supportReport.form.debugBundleHelp")}
                      </p>
                    </div>
                  </div>

                  {hasAttachments && (
                    <div className="rounded-md border border-border bg-muted/30">
                      <div className="border-b border-border px-3 py-2 text-xs font-medium uppercase text-muted-foreground">
                        {t("supportReport.form.attachedFiles")}
                      </div>
                      <ul className="divide-y divide-border text-sm">
                        {attachmentRows.map((file, index) => (
                          <li key={`${file.kind}-${file.filename}-${index}`} className="flex items-center gap-3 px-3 py-2">
                            <FileText className="h-4 w-4 flex-shrink-0 text-primary" />
                            <div className="min-w-0 flex-1">
                              <div className="truncate font-medium">{file.filename}</div>
                              <div className="text-xs text-muted-foreground">
                                {file.kind === "debug_bundle"
                                  ? t("supportReport.attachment.debugBundle")
                                  : t("supportReport.attachment.screenshot")}{" "}
                                - {formatBytes(file.sizeBytes)}
                              </div>
                            </div>
                            {file.kind === "screenshot" && !serverPreview && (
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                aria-label={t("supportReport.form.removeAttachment")}
                                onClick={() => removeScreenshot(index)}
                              >
                                <X className="h-4 w-4" />
                              </Button>
                            )}
                            {file.kind === "debug_bundle" && !serverPreview && (
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                aria-label={t("supportReport.form.removeAttachment")}
                                onClick={() => {
                                  setDebugBundle(null)
                                  setAttachmentError(validateAttachmentSelection(config, screenshots, null))
                                }}
                              >
                                <X className="h-4 w-4" />
                              </Button>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {attachmentError && (
                    <p className="text-sm text-destructive" role="alert">
                      {attachmentError}
                    </p>
                  )}

                  <div className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-3 text-sm text-muted-foreground">
                    <ShieldCheck className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                    <p>{t("supportReport.form.attachmentsPrivate")}</p>
                  </div>
                </section>
              </div>
            )}

            {step === "review" && draftPayload && (
              <div className="space-y-4" data-testid="report-problem-review">
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
                  <section className="rounded-lg border border-border bg-background">
                    <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                      <FileText className="h-4 w-4 text-primary" />
                      <h3 className="text-sm font-semibold">{t("supportReport.review.publicPreview")}</h3>
                    </div>
                    <div className="space-y-3 p-3">
                      <ReportPreviewCard title={previewTitle} payload={draftPayload} />
                    </div>
                  </section>

                  <aside className="space-y-3">
                    <section className="rounded-lg border border-border bg-background">
                      <div className="border-b border-border px-3 py-2">
                        <h3 className="text-sm font-semibold">{t("supportReport.review.diagnostics")}</h3>
                      </div>
                      <dl className="divide-y divide-border text-sm">
                        {diagnosticsRows(draftPayload.diagnostics).map(([label, value]) => (
                          <div key={label} className="px-3 py-2">
                            <dt className="text-xs text-muted-foreground">{label}</dt>
                            <dd className="mt-0.5 break-words">{value}</dd>
                          </div>
                        ))}
                      </dl>
                    </section>
                    <section className="rounded-lg border border-border bg-background p-3 text-sm text-muted-foreground">
                      <div className="mb-2 flex items-center gap-2 font-medium text-foreground">
                        <Mail className="h-4 w-4 text-primary" />
                        {t("supportReport.review.emailPrivate")}
                      </div>
                      <p>{t("supportReport.review.privateBoundary")}</p>
                    </section>
                    <section className="rounded-lg border border-border bg-background">
                      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                        <Paperclip className="h-4 w-4 text-primary" />
                        <h3 className="text-sm font-semibold">{t("supportReport.review.privateAttachments")}</h3>
                      </div>
                      {hasAttachments ? (
                        <ul className="divide-y divide-border text-sm">
                          {attachmentRows.map((file, index) => (
                            <li key={`${file.kind}-${file.filename}-${index}`} className="px-3 py-2">
                              <div className="break-words font-medium">{file.filename}</div>
                              <div className="text-xs text-muted-foreground">
                                {file.kind === "debug_bundle"
                                  ? t("supportReport.attachment.debugBundle")
                                  : t("supportReport.attachment.screenshot")}{" "}
                                - {formatBytes(file.sizeBytes)}
                              </div>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="px-3 py-3 text-sm text-muted-foreground">
                          {t("supportReport.review.noAttachments")}
                        </p>
                      )}
                    </section>
                  </aside>
                </div>

                {submitError && (
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>{t("supportReport.error.submit")}</AlertTitle>
                    <AlertDescription>{submitError}</AlertDescription>
                  </Alert>
                )}
                {manualUploadOpen && manualUploadPanel}
                <div aria-live="polite" className="sr-only">
                  {submitting ? t("supportReport.status.submitting") : ""}
                </div>
              </div>
            )}

            {step === "success" && serverPreview && draftPayload && (
              <div className="space-y-4" data-testid="report-problem-success">
                <div className="rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-4 py-4">
                  <div className="flex items-start gap-3">
                    <CheckCircle className="mt-0.5 h-5 w-5 flex-shrink-0 text-emerald-500" />
                    <div>
                      <h3 className="font-semibold text-emerald-500">{t("supportReport.success.title")}</h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {t("supportReport.success.description")}
                      </p>
                    </div>
                  </div>
                </div>
                <section className="rounded-lg border border-border bg-background">
                  <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                    <Paperclip className="h-4 w-4 text-primary" />
                    <h3 className="text-sm font-semibold">{t("supportReport.review.privateAttachments")}</h3>
                  </div>
                  {hasAttachments ? (
                    <ul className="divide-y divide-border text-sm">
                      {attachmentRows.map((file, index) => (
                        <li key={`${file.kind}-${file.filename}-${index}`} className="px-3 py-2">
                          <div className="break-words font-medium">{file.filename}</div>
                          <div className="text-xs text-muted-foreground">
                            {file.kind === "debug_bundle"
                              ? t("supportReport.attachment.debugBundle")
                              : t("supportReport.attachment.screenshot")}{" "}
                            - {formatBytes(file.sizeBytes)}
                            {file.digest ? ` - sha256 ${file.digest}...` : ""}
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="px-3 py-3 text-sm text-muted-foreground">
                      {t("supportReport.review.noAttachments")}
                    </p>
                  )}
                </section>
                <section className="rounded-lg border border-border bg-background">
                  <div className="border-b border-border px-3 py-2">
                    <h3 className="text-sm font-semibold">{t("supportReport.review.publicPreview")}</h3>
                  </div>
                  <div className="space-y-3 p-3">
                    <ReportPreviewCard title={serverPreview.issue_title} payload={draftPayload} />
                  </div>
                </section>
              </div>
            )}
          </div>

          <DialogFooter className="gap-3 border-t border-border bg-card/50 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:space-x-0 sm:px-6">
            {step === "form" && (
              <>
                <Button variant="outline" onClick={() => setOpen(false)}>
                  {t("supportReport.form.cancel")}
                </Button>
                <Button onClick={handleReview} disabled={loadingConfig || Boolean(configError)}>
                  {t("supportReport.form.continue")}
                </Button>
              </>
            )}
            {step === "review" && (
              <>
                <Button
                  variant="outline"
                  className="w-full sm:w-auto"
                  onClick={() => setStep("form")}
                  disabled={submitting}
                >
                  <ArrowLeft className="h-4 w-4" />
                  {t("supportReport.review.back")}
                </Button>
                <div className="flex w-full flex-col-reverse gap-2 sm:w-auto sm:flex-row">
                  <Button
                    variant="outline"
                    className="w-full sm:w-auto"
                    onClick={() => setManualUploadOpen((current) => !current)}
                    disabled={submitting}
                  >
                    <ExternalLink className="h-4 w-4" />
                    {t("supportReport.manual.footerButton")}
                  </Button>
                  <Button className="w-full sm:w-auto" onClick={handleSubmit} disabled={submitting}>
                    {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                    {submitting ? t("supportReport.status.submitting") : t("supportReport.review.submitDryRun")}
                  </Button>
                </div>
              </>
            )}
            {step === "success" && (
              <Button onClick={() => setOpen(false)}>{t("supportReport.success.close")}</Button>
            )}
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
