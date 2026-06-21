"use client"

import { useRef, useState } from "react"
import { AlertTriangle, Archive, Bug, Check, Download, Loader2, RotateCcw, Upload } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/base/alert"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { TabsContent } from "@/components/base/tabs"
import { downloadBackup, downloadDebugBundle, restoreFromBackup } from "@/lib/api"
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
  return err instanceof Error ? err.message : t("backup.restoreDefaultError")
}

export function restoreSuccessMessage(result: { message?: string }): string {
  return result.message || t("backup.restoreDefaultMessage")
}

export function BackupSettingsSection() {
  const [downloadState, setDownloadState] = useState<"idle" | "loading" | "done" | "error">("idle")
  const [debugBundleState, setDebugBundleState] = useState<"idle" | "loading" | "done" | "error">("idle")
  const [restoreState, setRestoreState] = useState<"idle" | "loading" | "done" | "error">("idle")
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [restoreMessage, setRestoreMessage] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleDownload = async () => {
    setDownloadState("loading")
    try {
      const blob = await downloadBackup()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = backupFilename()
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
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
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = debugBundleFilename()
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
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

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!e.target.files) return
    e.target.value = ""
    if (!file) return

    setRestoreState("loading")
    setRestoreError(null)
    setRestoreMessage(null)

    try {
      const result = await restoreFromBackup(file)
      setRestoreState("done")
      setRestoreMessage(restoreSuccessMessage(result))
    } catch (err) {
      setRestoreState("error")
      setRestoreError(restoreErrorMessage(err))
    }
  }

  return (
    <TabsContent value="backup" className="space-y-6">
      <Card className="border-blue-400/20 overflow-hidden">
        <div className="relative">
          <div className="absolute inset-0 bg-gradient-to-r from-blue-900/10 to-cyan-900/10 z-0" />
          <CardHeader className="relative z-10 border-b border-blue-200/10">
            <div className="flex items-center gap-3">
              <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                <Archive className="h-5 w-5 text-blue-400" />
              </div>
              <div>
                <CardTitle className="text-xl">{t("backup.title")}</CardTitle>
                <CardDescription>
                  {t("backup.description")}
                </CardDescription>
              </div>
            </div>
          </CardHeader>
        </div>

        <CardContent className="space-y-6 pt-6">
          <div className="rounded-xl border border-blue-400/15 bg-blue-500/5 p-5 space-y-3">
            <p className="text-sm font-semibold">{t("backup.downloadSection")}</p>
            <p className="text-sm text-muted-foreground">
              {t("backup.downloadDescPre")}<code>settings.json</code>{t("backup.downloadDescPost")}
            </p>
            <Button
              type="button"
              variant="outline"
              onClick={handleDownload}
              disabled={downloadState === "loading"}
              className="gap-2"
            >
              {downloadState === "loading" && <Loader2 className="h-4 w-4 animate-spin" />}
              {downloadState === "done" && <Check className="h-4 w-4 text-emerald-500" />}
              {downloadState === "error" && <AlertTriangle className="h-4 w-4 text-red-500" />}
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

          <div className="rounded-xl border border-blue-400/15 bg-blue-500/5 p-5 space-y-3">
            <p className="text-sm font-semibold">{t("backup.debugBundleSection")}</p>
            <p className="text-sm text-muted-foreground">{t("backup.debugBundleDesc")}</p>
            <Button
              type="button"
              variant="outline"
              onClick={handleDownloadDebugBundle}
              disabled={debugBundleState === "loading"}
              className="gap-2"
            >
              {debugBundleState === "loading" && <Loader2 className="h-4 w-4 animate-spin" />}
              {debugBundleState === "done" && <Check className="h-4 w-4 text-emerald-500" />}
              {debugBundleState === "error" && <AlertTriangle className="h-4 w-4 text-red-500" />}
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

          <div className="rounded-xl border border-amber-400/25 bg-amber-500/5 p-5 space-y-4">
            <p className="text-sm font-semibold">{t("backup.restoreSection")}</p>

            <Alert variant="destructive" className="border-amber-600/40 bg-amber-500/10 text-amber-800 dark:text-amber-200 [&>svg]:text-amber-600">
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
                type="button"
                variant="outline"
                onClick={handleRestoreSelect}
                disabled={restoreState === "loading"}
                className="gap-2 border-amber-400/40 hover:border-amber-400/60"
              >
                {restoreState === "loading" && <Loader2 className="h-4 w-4 animate-spin" />}
                {restoreState === "done" && <Check className="h-4 w-4 text-emerald-500" />}
                {restoreState === "error" && <AlertTriangle className="h-4 w-4 text-red-500" />}
                {restoreState === "idle" && <Upload className="h-4 w-4" />}
                {restoreState === "loading"
                  ? t("backup.restoringBtn")
                  : restoreState === "done"
                    ? t("backup.restoredBtn")
                    : restoreState === "error"
                      ? t("backup.restoreFailedBtn")
                      : t("backup.restoreBtn")}
              </Button>

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
              <Alert className="border-emerald-400/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200 [&>svg]:text-emerald-600">
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
