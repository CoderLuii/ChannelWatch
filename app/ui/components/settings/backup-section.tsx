"use client"

import { useRef, useState } from "react"
import { AlertTriangle, Archive, Bug, Check, Download, Loader2, RotateCcw, Upload } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { TabsContent } from "@/components/base/tabs"
import { downloadBackup, downloadDebugBundle, restoreFromBackup } from "@/lib/api"
import { describeError } from "@/lib/error-catalog"
import { downloadBlob } from "@/lib/utils"
import { t } from "@/lib/i18n"

export function backupTimestamp(now: Date = new Date()): string {
  return now.toISOString().replace(/[:.]/g, "-").slice(0, 19)
}

export function backupFilename(now: Date = new Date()): string {
  return `channelwatch_backup_${backupTimestamp(now)}.zip`
}

export function debugBundleFilename(now: Date = new Date()): string {
  return `channelwatch_debug_${backupTimestamp(now)}.zip`
}

export function restoreErrorMessage(err: unknown): string {
  return describeError(err, t("backup.restoreDefaultError")).message
}

export function restoreSuccessMessage(result: { message?: string }): string {
  return result.message || t("backup.restoreDefaultMessage")
}

export function BackupSettingsSection({ onRestored, hasUnsavedChanges = false }: { onRestored?: () => Promise<void>; hasUnsavedChanges?: boolean }) {
  const [downloadState, setDownloadState] = useState<"idle" | "loading" | "done" | "error">("idle")
  const [debugBundleState, setDebugBundleState] = useState<"idle" | "loading" | "done" | "error">("idle")
  const [restoreState, setRestoreState] = useState<"idle" | "loading" | "done" | "error">("idle")
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [restoreMessage, setRestoreMessage] = useState<string | null>(null)
  const [pendingRestoreFile, setPendingRestoreFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const restoreButtonRef = useRef<HTMLButtonElement>(null)

  // The question appears below the fold on a phone, so it is brought to the reader.
  const focusRestoreConfirm = (confirm: HTMLDivElement | null) => {
    if (!confirm) return
    confirm.scrollIntoView({ block: "center", behavior: "instant" })
    confirm.focus({ preventScroll: true })
  }

  const handleDownload = async () => {
    setDownloadState("loading")
    try {
      const blob = await downloadBackup()
      downloadBlob(blob, backupFilename())
      setDownloadState("done")
      setTimeout(() => setDownloadState("idle"), 3000)
    } catch {
      setDownloadState("error")
      setTimeout(() => setDownloadState("idle"), 4000)
    }
  }

  const handleDownloadDebugBundle = async () => {
    setDebugBundleState("loading")
    try {
      const blob = await downloadDebugBundle()
      downloadBlob(blob, debugBundleFilename())
      setDebugBundleState("done")
      setTimeout(() => setDebugBundleState("idle"), 3000)
    } catch {
      setDebugBundleState("error")
      setTimeout(() => setDebugBundleState("idle"), 4000)
    }
  }

  const handleRestoreSelect = () => {
    fileInputRef.current?.click()
  }

  // Picking a file only stages it. Restore overwrites settings, the database
  // and the encryption key, so it runs after an explicit confirmation.
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!e.target.files) return
    e.target.value = ""
    if (!file) return
    setRestoreError(null)
    setRestoreMessage(null)
    setRestoreState("idle")
    setPendingRestoreFile(file)
  }

  const handleCancelRestore = () => {
    setPendingRestoreFile(null)
    setTimeout(() => restoreButtonRef.current?.focus())
  }

  const handleConfirmRestore = async () => {
    const file = pendingRestoreFile
    if (!file) return
    setPendingRestoreFile(null)
    setRestoreState("loading")
    setRestoreError(null)
    setRestoreMessage(null)

    try {
      const result = await restoreFromBackup(file)
      setRestoreState("done")
      setRestoreMessage(restoreSuccessMessage(result))
      await onRestored?.()
    } catch (err) {
      setRestoreState("error")
      setRestoreError(restoreErrorMessage(err))
    }
  }

  return (
    <TabsContent value="backup" className="space-y-6">
      <Card className="overflow-hidden">
        <CardHeader className="border-b border-hairline">
            <div className="flex items-center gap-2">
              <Archive className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div>
                <CardTitle>{t("backup.title")}</CardTitle>
              </div>
            </div>
          </CardHeader>

        <CardContent className="divide-y divide-hairline p-0">
          <div className="space-y-3 p-4">
            <p className="text-sm font-semibold">{t("backup.downloadSection")}</p>
            <p className="text-sm text-muted-foreground">
              {t("backup.downloadDescPre")}<code>settings.json</code>{t("backup.downloadDescPost")}
            </p>
            <Button
              type="button"
              variant="outline"
              onClick={handleDownload}
              disabled={downloadState === "loading"}
              className="h-auto min-h-11 max-w-full gap-2 whitespace-normal py-2 text-center"
            >
              {downloadState === "loading" && <Loader2 className="h-4 w-4 animate-spin" />}
              {downloadState === "done" && <Check className="h-4 w-4 text-success" />}
              {downloadState === "error" && <AlertTriangle className="h-4 w-4 text-danger" />}
              {downloadState === "idle" && <Download className="h-4 w-4" />}
              {downloadState === "loading"
                ? t("backup.downloadingBtn")
                : downloadState === "done"
                  ? t("backup.downloadedBtn")
                  : downloadState === "error"
                    ? t("backup.downloadFailedBtn")
                    : t("backup.downloadBtn")}
            </Button>
          </div>

          <div className="space-y-3 p-4">
            <p className="text-sm font-semibold">{t("backup.debugBundleSection")}</p>
            <p className="max-w-prose text-sm text-muted-foreground">{t("backup.debugBundleDesc")}</p>
            <Button
              type="button"
              variant="outline"
              onClick={handleDownloadDebugBundle}
              disabled={debugBundleState === "loading"}
              className="h-auto min-h-11 max-w-full gap-2 whitespace-normal py-2 text-center"
            >
              {debugBundleState === "loading" && <Loader2 className="h-4 w-4 animate-spin" />}
              {debugBundleState === "done" && <Check className="h-4 w-4 text-success" />}
              {debugBundleState === "error" && <AlertTriangle className="h-4 w-4 text-danger" />}
              {debugBundleState === "idle" && <Bug className="h-4 w-4" />}
              {debugBundleState === "loading"
                ? t("backup.debugBundleDownloadingBtn")
                : debugBundleState === "done"
                  ? t("backup.debugBundleDownloadedBtn")
                  : debugBundleState === "error"
                    ? t("backup.debugBundleFailedBtn")
                    : t("backup.debugBundleBtn")}
            </Button>
          </div>

          <div className="space-y-4 p-4">
            <p className="text-sm font-semibold">{t("backup.restoreSection")}</p>

            <Alert variant="destructive" className="border-warning-border bg-warning-bg text-warning-fg [&>svg]:text-warning-fg">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{t("backup.destructiveTitle")}</AlertTitle>
              <AlertDescription>
                {t("backup.destructiveDescPre")}<code>settings.json</code>{", "}<code>channelwatch.db</code>{t("backup.destructiveDescMid")}<code>/config/backups/</code>{t("backup.destructiveDescPost")}
              </AlertDescription>
            </Alert>

            <p className="text-sm text-muted-foreground">
              {t("backup.uploadDescPre")}<code>.zip</code>{t("backup.uploadDescPost")}
            </p>

            <div className="flex items-center gap-3 flex-wrap">
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip"
                className="hidden"
                onChange={handleFileChange}
              />
              <Button
                ref={restoreButtonRef}
                type="button"
                variant="outline"
                onClick={handleRestoreSelect}
                disabled={restoreState === "loading" || pendingRestoreFile !== null}
                className="h-auto min-h-11 max-w-full gap-2 whitespace-normal py-2 text-center"
              >
                {restoreState === "loading" && <Loader2 className="h-4 w-4 animate-spin" />}
                {restoreState === "done" && <Check className="h-4 w-4 text-success" />}
                {restoreState === "error" && <AlertTriangle className="h-4 w-4 text-danger" />}
                {restoreState === "idle" && <Upload className="h-4 w-4" />}
                {restoreState === "loading"
                  ? t("backup.restoringBtn")
                  : restoreState === "done"
                    ? t("backup.restoredBtn")
                    : restoreState === "error"
                      ? t("backup.restoreFailedBtn")
                      : t("backup.restoreBtn")}
              </Button>

              {pendingRestoreFile && (
                <div ref={focusRestoreConfirm} tabIndex={-1} role="alertdialog" aria-labelledby="restore-confirm-title" aria-describedby="restore-confirm-desc" className="basis-full space-y-3 rounded-control border border-warning-border p-4 focus:outline-none">
                  <p id="restore-confirm-title" className="text-sm font-semibold">{t("backup.confirmTitle")}</p>
                  <p id="restore-confirm-desc" className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
                    {t("backup.confirmDesc", { file: pendingRestoreFile.name })}
                    {hasUnsavedChanges ? ` ${t("backup.confirmUnsaved")}` : ""}
                  </p>
                  <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                    <Button type="button" variant="outline" onClick={handleCancelRestore} className="min-h-11">
                      {t("backup.confirmCancel")}
                    </Button>
                    <Button type="button" variant="destructive" onClick={handleConfirmRestore} className="min-h-11">
                      {t("backup.confirmRestore")}
                    </Button>
                  </div>
                </div>
              )}

              {restoreState === "done" && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-2"
                  onClick={() => window.location.reload()}
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  {t("backup.reloadPage")}
                </Button>
              )}
            </div>

            {restoreState === "error" && restoreError && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>{t("backup.restoreFailedTitle")}</AlertTitle>
                <AlertDescription>{restoreError}</AlertDescription>
              </Alert>
            )}

            {restoreState === "done" && restoreMessage && (
              <Alert className="border-success-border bg-success-bg text-success-fg [&>svg]:text-success-fg">
                <Check className="h-4 w-4" />
                <AlertTitle>{t("backup.restoreCompleteTitle")}</AlertTitle>
                <AlertDescription>{restoreMessage}</AlertDescription>
              </Alert>
            )}
          </div>
        </CardContent>
      </Card>
    </TabsContent>
  )
}
