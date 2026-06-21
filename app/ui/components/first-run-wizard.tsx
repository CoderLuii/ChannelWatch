"use client"

import { useState } from "react"
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
    } catch {
      setTestState("fail")
      setTestError(t("wizard.manual.requestFailed"))
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
        description: err instanceof Error ? err.message : t("wizard.save.failedFallback"),
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
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/85 backdrop-blur-sm" />

      <div className="relative z-10 w-full max-w-md mx-4">
        <div className="bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl overflow-hidden">
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
              <ConfirmStep dvr={pendingDvr} isSaving={isSaving} onSave={handleSave} onBack={() => setStep(pendingDvr.host ? "manual" : "discover")} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function StepDots({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-1.5 justify-center">
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          className={cn(
            "h-1.5 rounded-full transition-all duration-300",
            i < current
              ? "w-4 bg-blue-500"
              : i === current
              ? "w-6 bg-blue-400"
              : "w-4 bg-zinc-700",
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
          <div className="w-16 h-16 rounded-2xl bg-blue-500/20 flex items-center justify-center">
            <Tv className="h-8 w-8 text-blue-400" />
          </div>
        </div>
        <div>
          <h2 className="text-xl font-semibold text-zinc-100">{t("wizard.welcome.title")}</h2>
          <p className="text-sm text-zinc-400 mt-1">
            {t("wizard.welcome.subtitle")}
          </p>
        </div>
      </div>

      <div className="space-y-3">
        <Button
          className="w-full bg-blue-600 hover:bg-blue-700 text-white gap-2"
          onClick={onDiscover}
        >
          <Wifi className="h-4 w-4" />
          {t("wizard.welcome.discoverBtn")}
        </Button>
        <Button
          variant="outline"
          className="w-full border-zinc-600 text-zinc-300 hover:bg-zinc-800 gap-2"
          onClick={onManual}
        >
          <PenLine className="h-4 w-4" />
          {t("wizard.welcome.manualBtn")}
        </Button>
      </div>

      <div className="text-center">
        <button
          type="button"
          className="text-xs text-zinc-500 hover:text-zinc-400 underline underline-offset-2 transition-colors"
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
        <button type="button" onClick={onBack} className="text-zinc-500 hover:text-zinc-300 transition-colors">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h2 className="text-base font-semibold text-zinc-100">{t("wizard.discover.title")}</h2>
          <p className="text-xs text-zinc-500">{t("wizard.discover.subtitle")}</p>
        </div>
      </div>

      {isDiscovering && (
        <div className="flex flex-col items-center gap-3 py-6">
          <Loader2 className="h-8 w-8 animate-spin text-blue-400" />
          <p className="text-sm text-zinc-400">{t("wizard.discover.scanning")}</p>
        </div>
      )}

      {discoveryDone && servers.length === 0 && (
        <div className="rounded-xl border border-zinc-700 bg-zinc-800/50 p-4 text-center space-y-3">
          <Search className="h-6 w-6 text-zinc-500 mx-auto" />
          <p className="text-sm text-zinc-400">
            {error ? t("wizard.discover.scanError", { error }) : t("wizard.discover.noServers")}
          </p>
          <p className="text-xs text-zinc-500">
            {t("wizard.discover.hint")}
          </p>
        </div>
      )}

      {servers.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-zinc-500 font-medium">
            {t(servers.length === 1 ? "wizard.discover.serverFound" : "wizard.discover.serversFound", { count: servers.length })}
          </p>
          {servers.map((s, i) => (
            <div
              key={`${s.host}:${s.port}-${i}`}
              className="flex items-center justify-between p-3 rounded-lg border border-zinc-700 bg-zinc-800/50 hover:bg-zinc-800 transition-colors"
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <Server className="h-4 w-4 text-blue-400 shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm text-zinc-200 font-medium truncate">{s.name || s.host}</p>
                  <p className="text-xs text-zinc-500">
                    {s.host}:{s.port}
                    {s.version ? ` · v${s.version}` : ""}
                  </p>
                </div>
              </div>
              <Button
                size="sm"
                className="h-7 text-xs bg-blue-600 hover:bg-blue-700 text-white shrink-0 ml-2"
                onClick={() => onSelect(s)}
              >
                Use <ChevronRight className="h-3 w-3 ml-0.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {discoveryDone && (
        <Button
          variant="outline"
          className="w-full border-zinc-600 text-zinc-300 hover:bg-zinc-800 gap-2 text-sm"
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
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <button type="button" onClick={onBack} className="text-zinc-500 hover:text-zinc-300 transition-colors">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h2 className="text-base font-semibold text-zinc-100">{t("wizard.manual.title")}</h2>
          <p className="text-xs text-zinc-500">{t("wizard.manual.subtitle")}</p>
        </div>
      </div>

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label className="text-xs text-zinc-400">{t("wizard.manual.nameLbl")}</Label>
          <Input
            placeholder="e.g. Living Room DVR"
            value={name}
            onChange={(e) => onChangeName(e.target.value)}
            className="bg-zinc-800 border-zinc-600 text-zinc-100 placeholder:text-zinc-600 h-9 text-sm"
          />
        </div>

        <div className="flex gap-2">
          <div className="flex-1 space-y-1.5">
            <Label className="text-xs text-zinc-400">{t("wizard.manual.hostLbl")}</Label>
            <Input
              placeholder="192.168.1.100"
              value={host}
              onChange={(e) => onChangeHost(e.target.value)}
              className="bg-zinc-800 border-zinc-600 text-zinc-100 placeholder:text-zinc-600 h-9 text-sm"
            />
          </div>
          <div className="w-24 space-y-1.5">
            <Label className="text-xs text-zinc-400">{t("wizard.manual.portLbl")}</Label>
            <Input
              placeholder="8089"
              value={port}
              onChange={(e) => onChangePort(e.target.value)}
              type="number"
              min={1}
              max={65535}
              className="bg-zinc-800 border-zinc-600 text-zinc-100 placeholder:text-zinc-600 h-9 text-sm"
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-zinc-400">{t("wizard.manual.apiKeyLbl")}</Label>
          <Input
            placeholder={t("wizard.manual.apiKeyPlaceholder")}
            value={apiKey}
            onChange={(e) => onChangeApiKey(e.target.value)}
            type="password"
            className="bg-zinc-800 border-zinc-600 text-zinc-100 placeholder:text-zinc-600 h-9 text-sm"
          />
        </div>
      </div>

      {testState === "ok" && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-green-500/10 border border-green-500/20">
          <CheckCircle className="h-4 w-4 text-green-400 shrink-0" />
          <p className="text-xs text-green-300">
            {t("wizard.manual.connected")}{testedName ? ` · ${testedName}` : ""}
          </p>
        </div>
      )}

      {testState === "fail" && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20">
          <XCircle className="h-4 w-4 text-red-400 shrink-0" />
          <p className="text-xs text-red-300">{testError || t("wizard.manual.connectionFailed")}</p>
        </div>
      )}

      <div className="flex gap-2">
        <Button
          variant="outline"
          className="border-zinc-600 text-zinc-300 hover:bg-zinc-800 text-sm h-9"
          disabled={!host.trim() || testState === "testing"}
          onClick={onTest}
        >
          {testState === "testing" ? (
            <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
          ) : null}
          {t("wizard.manual.testBtn")}
        </Button>
        <Button
          className="flex-1 bg-blue-600 hover:bg-blue-700 text-white text-sm h-9"
          disabled={!canProceed}
          onClick={onProceed}
        >
          {t("wizard.manual.continueBtn")}
          <ChevronRight className="h-4 w-4 ml-1" />
        </Button>
      </div>
    </div>
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
          <div className="w-14 h-14 rounded-xl bg-green-500/20 flex items-center justify-center">
            <CheckCircle className="h-7 w-7 text-green-400" />
          </div>
        </div>
        <div>
          <h2 className="text-base font-semibold text-zinc-100">{t("wizard.confirm.title")}</h2>
          <p className="text-xs text-zinc-500 mt-0.5">{t("wizard.confirm.subtitle")}</p>
        </div>
      </div>

      <div className="rounded-xl border border-zinc-700 bg-zinc-800/60 p-4 space-y-2">
        <div className="flex items-center gap-2.5">
          <Server className="h-5 w-5 text-blue-400 shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-zinc-100 truncate">{dvr.name}</p>
            <p className="text-xs text-zinc-500">
              {dvr.host}:{dvr.port}
            </p>
          </div>
        </div>
      </div>

      <div className="flex gap-2">
        <Button
          variant="outline"
          className="border-zinc-600 text-zinc-300 hover:bg-zinc-800 text-sm h-9"
          onClick={onBack}
          disabled={isSaving}
        >
          <ArrowLeft className="h-4 w-4 mr-1" />
          {t("common.back")}
        </Button>
        <Button
          className="flex-1 bg-blue-600 hover:bg-blue-700 text-white text-sm h-9"
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
