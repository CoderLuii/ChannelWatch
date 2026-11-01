"use client"

import { useState } from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import {
  Loader2,
  Tv,
  Search,
  PenLine,
  CheckCircle,
  XCircle,
  Server,
  ChevronRight,
  ArrowLeft,
  Wifi,
} from "lucide-react"
import { Button } from "@/components/base/button"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { useToast } from "@/hooks/use-toast"
import {
  discoverServers,
  testDvrConnection,
  saveSettings,
  fetchSettings,
  type DiscoveredServer,
} from "@/lib/api"
import { canonicalDvrId } from "@/lib/dvr-id"
import { t } from "@/lib/i18n"
import { describeError, formatErrorWithRemediation } from "@/lib/error-catalog"
import type { AppSettings, DVRServer } from "@/lib/types"
import { cn } from "@/lib/utils"

type WizardStep = "welcome" | "discover" | "manual" | "confirm"

interface PendingDvr {
  name: string
  host: string
  port: number
  api_key: string
}

interface FirstRunWizardProps {
  currentSettings: AppSettings
  onComplete: (updatedSettings: AppSettings) => void
  onSkip: () => void
}

export function FirstRunWizard({ currentSettings, onComplete, onSkip }: FirstRunWizardProps) {
  const [step, setStep] = useState<WizardStep>("welcome")

  const [isDiscovering, setIsDiscovering] = useState(false)
  const [discoveredServers, setDiscoveredServers] = useState<DiscoveredServer[]>([])
  const [discoveryError, setDiscoveryError] = useState<string | null>(null)
  const [discoveryDone, setDiscoveryDone] = useState(false)

  const [manualName, setManualName] = useState("")
  const [manualHost, setManualHost] = useState("")
  const [manualPort, setManualPort] = useState("8089")
  const [manualApiKey, setManualApiKey] = useState("")
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "fail">("idle")
  const [testError, setTestError] = useState<string | null>(null)
  const [testedName, setTestedName] = useState<string | null>(null)

  const [pendingDvr, setPendingDvr] = useState<PendingDvr | null>(null)
  const [confirmFrom, setConfirmFrom] = useState<"discover" | "manual">("manual")
  const [isSaving, setIsSaving] = useState(false)

  const { toast } = useToast()

  const startDiscover = async () => {
    setStep("discover")
    setIsDiscovering(true)
    setDiscoveredServers([])
    setDiscoveryError(null)
    setDiscoveryDone(false)
    try {
      const result = await discoverServers()
      setDiscoveredServers(result.servers || [])
      if (result.error) setDiscoveryError(result.error)
    } catch {
      setDiscoveryError(t("wizard.save.networkScanFailed"))
    } finally {
      setIsDiscovering(false)
      setDiscoveryDone(true)
    }
  }

  const selectDiscovered = (server: DiscoveredServer) => {
    setPendingDvr({
      name: server.name || server.host,
      host: server.host,
      port: server.port,
      api_key: "",
    })
    setConfirmFrom("discover")
    setStep("confirm")
  }

  const runConnectionTest = async () => {
    if (!manualHost.trim()) return
    setTestState("testing")
    setTestError(null)
    setTestedName(null)
    try {
      const result = await testDvrConnection(
        manualHost.trim(),
        Number(manualPort) || 8089,
        manualApiKey.trim() || undefined,
      )
      if (result.success) {
        setTestState("ok")
        setTestedName(result.name ?? manualHost.trim())
      } else {
        setTestState("fail")
        setTestError(result.error ?? t("wizard.manual.connectionRefused"))
      }
    } catch (err) {
      setTestState("fail")
      setTestError(formatErrorWithRemediation(err, t("wizard.manual.requestFailed")))
    }
  }

  const proceedFromManual = () => {
    const name = manualName.trim() || testedName || manualHost.trim()
    setPendingDvr({
      name,
      host: manualHost.trim(),
      port: Number(manualPort) || 8089,
      api_key: manualApiKey.trim(),
    })
    setConfirmFrom("manual")
    setStep("confirm")
  }

  const handleSave = async () => {
    if (!pendingDvr) return
    setIsSaving(true)
    try {
      const id = canonicalDvrId(pendingDvr.host, pendingDvr.port)
      const newServer: DVRServer = {
        id,
        name: pendingDvr.name,
        host: pendingDvr.host,
        port: pendingDvr.port,
        enabled: true,
        ...(pendingDvr.api_key ? { api_key: pendingDvr.api_key } : {}),
      }

      const updatedSettings: AppSettings = {
        ...currentSettings,
        dvr_servers: [...(currentSettings.dvr_servers || []), newServer],
      }
      await saveSettings(updatedSettings)
      const refreshed = await fetchSettings()
      toast({
        title: t("wizard.save.success"),
        description: t("wizard.save.successDesc", { name: pendingDvr.name }),
      })
      onComplete(refreshed)
    } catch (err) {
      toast({
        variant: "destructive",
        title: t("wizard.save.failed"),
        description: describeError(err, t("wizard.save.failedFallback")).message,
      })
    } finally {
      setIsSaving(false)
    }
  }

  const resetToWelcome = () => {
    setStep("welcome")
    setDiscoveredServers([])
    setDiscoveryError(null)
    setDiscoveryDone(false)
    setManualHost("")
    setManualName("")
    setManualPort("8089")
    setManualApiKey("")
    setTestState("idle")
    setTestError(null)
    setTestedName(null)
    setPendingDvr(null)
  }

  const manualCanProceed = manualHost.trim().length > 0 && testState === "ok"

  const stepIndex = { welcome: 0, discover: 1, manual: 1, confirm: 2 }[step]

  return (
    <DialogPrimitive.Root open>
    <DialogPrimitive.Portal>
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4">
      <div className="fixed inset-0 bg-black/85" />

      <DialogPrimitive.Content
        aria-describedby={undefined}
        onEscapeKeyDown={(event) => event.preventDefault()}
        onPointerDownOutside={(event) => event.preventDefault()}
        className="relative z-10 my-auto w-full max-w-md focus:outline-none"
      >
        <DialogPrimitive.Title className="sr-only">{t("wizard.dialogLabel")}</DialogPrimitive.Title>
        <div className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-group border border-hairline-strong bg-raised shadow-3">
          <div className="px-6 pt-6 pb-4">
            <StepDots current={stepIndex} total={3} />
          </div>

          <div className="px-6 pb-6">
            {step === "welcome" && (
              <WelcomeStep onDiscover={startDiscover} onManual={() => setStep("manual")} onSkip={onSkip} />
            )}
            {step === "discover" && (
              <DiscoverStep
                isDiscovering={isDiscovering}
                discoveryDone={discoveryDone}
                servers={discoveredServers}
                error={discoveryError}
                onSelect={selectDiscovered}
                onManual={() => setStep("manual")}
                onBack={resetToWelcome}
              />
            )}
            {step === "manual" && (
              <ManualStep
                name={manualName}
                host={manualHost}
                port={manualPort}
                apiKey={manualApiKey}
                testState={testState}
                testError={testError}
                testedName={testedName}
                canProceed={manualCanProceed}
                onChangeName={setManualName}
                onChangeHost={(v) => { setManualHost(v); setTestState("idle") }}
                onChangePort={(v) => { setManualPort(v); setTestState("idle") }}
                onChangeApiKey={(v) => { setManualApiKey(v); setTestState("idle") }}
                onTest={runConnectionTest}
                onProceed={proceedFromManual}
                onBack={resetToWelcome}
              />
            )}
            {step === "confirm" && pendingDvr && (
              <ConfirmStep dvr={pendingDvr} isSaving={isSaving} onSave={handleSave} onBack={() => setStep(confirmFrom)} />
            )}
          </div>
        </div>
      </DialogPrimitive.Content>
    </div>
    </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

function StepDots({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-1.5 justify-center">
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          className={cn(
            "h-1.5 rounded-full transition-colors",
            i < current
              ? "w-4 bg-primary/60"
              : i === current
              ? "w-6 bg-primary"
              : "w-4 bg-muted-foreground/30",
          )}
        />
      ))}
    </div>
  )
}

function WelcomeStep({
  onDiscover,
  onManual,
  onSkip,
}: {
  onDiscover: () => void
  onManual: () => void
  onSkip: () => void
}) {
  return (
    <div className="space-y-6">
      <div className="text-center space-y-3">
        <div className="flex justify-center">
          <div className="w-16 h-16 rounded-control bg-fill flex items-center justify-center">
            <Tv className="h-8 w-8 text-muted-foreground" />
          </div>
        </div>
        <div>
          <h2 className="text-xl font-semibold text-foreground">{t("wizard.welcome.title")}</h2>
          <p className="text-sm text-muted-foreground mt-1">
            {t("wizard.welcome.subtitle")}
          </p>
        </div>
      </div>

      <div className="space-y-3">
        <Button
          className="w-full bg-primary hover:bg-primary/90 text-primary-foreground gap-2"
          onClick={onDiscover}
        >
          <Wifi className="h-4 w-4" />
          {t("wizard.welcome.discoverBtn")}
        </Button>
        <Button
          variant="outline"
          className="w-full border-input bg-raised text-foreground hover:bg-fill hover:text-foreground gap-2"
          onClick={onManual}
        >
          <PenLine className="h-4 w-4" />
          {t("wizard.welcome.manualBtn")}
        </Button>
      </div>

      <div className="text-center">
        <button
          type="button"
          className="inline-flex min-h-11 items-center justify-center rounded-control px-3 text-xs text-muted-foreground underline underline-offset-2 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-raised"
          onClick={onSkip}
        >
          {t("wizard.welcome.skip")}
        </button>
      </div>
    </div>
  )
}

function DiscoverStep({
  isDiscovering,
  discoveryDone,
  servers,
  error,
  onSelect,
  onManual,
  onBack,
}: {
  isDiscovering: boolean
  discoveryDone: boolean
  servers: DiscoveredServer[]
  error: string | null
  onSelect: (s: DiscoveredServer) => void
  onManual: () => void
  onBack: () => void
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-fill hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={t("common.back")}
          title={t("common.back")}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        </button>
        <div>
          <h2 className="text-base font-semibold text-foreground">{t("wizard.discover.title")}</h2>
        </div>
      </div>

      {isDiscovering && (
        <div className="flex flex-col items-center gap-3 py-6">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">{t("wizard.discover.scanning")}</p>
        </div>
      )}

      {discoveryDone && servers.length === 0 && (
        <div className="rounded-control bg-fill p-4 text-center space-y-3">
          <Search className="h-6 w-6 text-muted-foreground mx-auto" />
          <p className="text-sm text-muted-foreground">
            {error ? t("wizard.discover.scanError", { error }) : t("wizard.discover.noServers")}
          </p>
          <p className="text-xs text-muted-foreground">
            {t("wizard.discover.hint")}
          </p>
        </div>
      )}

      {servers.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground font-medium">
            {t(servers.length === 1 ? "wizard.discover.serverFound" : "wizard.discover.serversFound", { count: servers.length })}
          </p>
          {servers.map((s, i) => (
            <div
              key={`${s.host}:${s.port}-${i}`}
              className="flex items-center justify-between p-3 rounded-control border border-hairline bg-fill hover:bg-fill transition-colors"
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <Server className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm text-foreground font-medium truncate">{s.name || s.host}</p>
                  <p className="text-xs text-muted-foreground">
                    {s.host}:{s.port}
                    {s.version ? ` · v${s.version}` : ""}
                  </p>
                </div>
              </div>
              <Button
                size="sm"
                className="h-7 text-xs bg-primary hover:bg-primary/90 text-primary-foreground shrink-0 ml-2"
                onClick={() => onSelect(s)}
              >
                {t("wizard.discover.useBtn")} <ChevronRight className="h-3 w-3 ml-0.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {discoveryDone && (
        <Button
          variant="outline"
          className="w-full border-input bg-raised text-foreground hover:bg-fill hover:text-foreground gap-2 text-sm"
          onClick={onManual}
        >
          <PenLine className="h-4 w-4" />
          {t("wizard.discover.addManual")}
        </Button>
      )}
    </div>
  )
}

function ManualStep({
  name,
  host,
  port,
  apiKey,
  testState,
  testError,
  testedName,
  canProceed,
  onChangeName,
  onChangeHost,
  onChangePort,
  onChangeApiKey,
  onTest,
  onProceed,
  onBack,
}: {
  name: string
  host: string
  port: string
  apiKey: string
  testState: "idle" | "testing" | "ok" | "fail"
  testError: string | null
  testedName: string | null
  canProceed: boolean
  onChangeName: (v: string) => void
  onChangeHost: (v: string) => void
  onChangePort: (v: string) => void
  onChangeApiKey: (v: string) => void
  onTest: () => void
  onProceed: () => void
  onBack: () => void
}) {
  return (
    <form
      className="space-y-5"
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        if (canProceed) onProceed()
      }}
      onKeyDown={(event) => {
        // A disabled Continue stops the browser's own Enter submit, so Enter runs the test until it passes.
        if (event.key !== "Enter" || canProceed || (event.target as HTMLElement).tagName !== "INPUT") return
        event.preventDefault()
        if (host.trim() && testState !== "testing") onTest()
      }}
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-fill hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={t("common.back")}
          title={t("common.back")}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        </button>
        <div>
          <h2 className="text-base font-semibold text-foreground">{t("wizard.manual.title")}</h2>
        </div>
      </div>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="wizard-name" className="text-xs text-muted-foreground">{t("wizard.manual.nameLbl")}</Label>
          <Input
            id="wizard-name"
            placeholder="e.g. Living Room DVR"
            value={name}
            onChange={(e) => onChangeName(e.target.value)}
            className="h-9 text-sm"
          />
        </div>

        <div className="flex gap-2">
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="wizard-host" className="text-xs text-muted-foreground">{t("wizard.manual.hostLbl")}</Label>
            <Input
              id="wizard-host"
              inputMode="url"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              placeholder="192.168.1.100"
              value={host}
              onChange={(e) => onChangeHost(e.target.value)}
              className="h-9 text-sm"
            />
          </div>
          <div className="w-24 space-y-1.5">
            <Label htmlFor="wizard-port" className="text-xs text-muted-foreground">{t("wizard.manual.portLbl")}</Label>
            <Input
              id="wizard-port"
              placeholder="8089"
              value={port}
              onChange={(e) => onChangePort(e.target.value)}
              type="number"
              min={1}
              max={65535}
              className="h-9 text-sm"
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="wizard-api-key" className="text-xs text-muted-foreground">{t("wizard.manual.apiKeyLbl")}</Label>
          <Input
            id="wizard-api-key"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder={t("wizard.manual.apiKeyPlaceholder")}
            value={apiKey}
            onChange={(e) => onChangeApiKey(e.target.value)}
            type="password"
            className="h-9 text-sm"
          />
        </div>
      </div>

      {testState === "ok" && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-control bg-success-bg border border-success-border">
          <CheckCircle className="h-4 w-4 text-success shrink-0" />
          <p className="text-xs text-success-fg">
            {t("wizard.manual.connected")}{testedName ? ` · ${testedName}` : ""}
          </p>
        </div>
      )}

      {testState === "fail" && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-control bg-danger-bg border border-danger-border">
          <XCircle className="h-4 w-4 text-danger shrink-0" />
          <p className="text-xs text-danger-fg">{testError || t("wizard.manual.connectionFailed")}</p>
        </div>
      )}

      <div className="grid min-w-0 grid-cols-2 gap-2">
        <Button
          type="button"
          variant="outline"
          className="h-auto min-h-11 min-w-0 w-full whitespace-normal border-input bg-raised px-2 py-2 text-center text-sm leading-tight text-foreground hover:bg-fill hover:text-foreground"
          disabled={!host.trim() || testState === "testing"}
          onClick={onTest}
        >
          {testState === "testing" ? (
            <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
          ) : null}
          {t("wizard.manual.testBtn")}
        </Button>
        <Button
          type="submit"
          className="h-auto min-h-11 min-w-0 w-full whitespace-normal bg-primary px-2 py-2 text-center text-sm leading-tight text-primary-foreground hover:bg-primary/90"
          disabled={!canProceed}
        >
          {t("wizard.manual.continueBtn")}
          <ChevronRight className="h-4 w-4 ml-1" />
        </Button>
      </div>
    </form>
  )
}

function ConfirmStep({
  dvr,
  isSaving,
  onSave,
  onBack,
}: {
  dvr: PendingDvr
  isSaving: boolean
  onSave: () => void
  onBack: () => void
}) {
  return (
    <div className="space-y-5">
      <div className="text-center space-y-3">
        <div className="flex justify-center">
          <div className="w-14 h-14 rounded-control bg-success-bg flex items-center justify-center">
            <CheckCircle className="h-7 w-7 text-success" />
          </div>
        </div>
        <div>
          <h2 className="text-base font-semibold text-foreground">{t("wizard.confirm.title")}</h2>
        </div>
      </div>

      <div className="rounded-control bg-fill p-4 space-y-2">
        <div className="flex items-center gap-2.5">
          <Server className="h-5 w-5 text-muted-foreground shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground truncate">{dvr.name}</p>
            <p className="text-xs text-muted-foreground">
              {dvr.host}:{dvr.port}
            </p>
          </div>
        </div>
      </div>

      <div className="grid min-w-0 grid-cols-2 gap-2">
        <Button
          variant="outline"
          className="h-auto min-h-11 min-w-0 w-full whitespace-normal border-input bg-raised px-2 py-2 text-center text-sm leading-tight text-foreground hover:bg-fill hover:text-foreground"
          onClick={onBack}
          disabled={isSaving}
        >
          <ArrowLeft className="h-4 w-4 mr-1" />
          {t("common.back")}
        </Button>
        <Button
          className="h-auto min-h-11 min-w-0 w-full whitespace-normal bg-primary px-2 py-2 text-center text-sm leading-tight text-primary-foreground hover:bg-primary/90"
          onClick={onSave}
          disabled={isSaving}
        >
          {isSaving ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
              {t("wizard.confirm.savingBtn")}
            </>
          ) : (
            t("wizard.confirm.saveBtn")
          )}
        </Button>
      </div>
    </div>
  )
}
