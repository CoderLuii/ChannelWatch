"use client"

import { useState, useEffect, useCallback } from "react"
import { Sidebar } from "@/components/sidebar"
import { Header, HeaderContext } from "@/components/header"
import { SettingsForm } from "@/components/settings-form"
import { DiagnosticsPanel } from "@/components/diagnostics-panel"
import { AboutSection } from "@/components/about-section"
import { StatusOverview } from "@/components/status-overview"
import { WatchHistory } from "@/components/watch-history"
import { NotificationLog } from "@/components/notification-log"
import { FirstRunWizard } from "@/components/first-run-wizard"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { DvrSelectionProvider } from "@/lib/dvr-selection-context"
import { useToast } from "@/hooks/use-toast"
import type { AppSettings, AuthMode, AuthSetupStatus, SecurityStatus, RuntimePreflightStatus } from "@/lib/types"
import { AuthRequiredError, RESTART_RECOVERED_EVENT, SessionRequiredError, cacheApiKey, clearCachedAuthState, completeInitialSetup, fetchRuntimePreflight, fetchSecurityStatus, fetchSettings, fetchSetupStatus, loginWithPassword } from "@/lib/api"
import { t } from "@/lib/i18n"
import { Loader2 } from "lucide-react"

const VALID_VIEWS = ["overview", "watch-history", "notification-log", "settings", "diagnostics", "about"]
const VALID_SETTINGS_TABS = ["general", "alerts", "advanced", "notifications", "routing", "security", "backup", "updates"]

function getViewFromHash(): string {
  if (typeof window === "undefined") return "overview"
  const hash = window.location.hash.replace("#", "")
  const base = hash.split(":")[0]
  if (base === "settings" && hash.includes(":")) {
    const tab = hash.split(":")[1]
    return VALID_SETTINGS_TABS.includes(tab) ? hash : "settings"
  }
  return VALID_VIEWS.includes(base) ? hash : "overview"
}

function hasActiveDvrs(settings: AppSettings | null): boolean {
  if (!settings) return false
  const servers = settings.dvr_servers || []
  return servers.some((s) => !s.deleted_at)
}

export type DashboardAuthShell = "app" | "runtime" | "bootstrap" | "login" | "api_key" | "noauth"

export function resolveDashboardBootstrapShell(
  setupStatus: Pick<AuthSetupStatus, "configured_mode" | "effective_mode" | "persisted_mode" | "setup_required">,
  securityStatus: Pick<SecurityStatus, "configured_mode" | "effective_mode" | "persisted_mode" | "runtime_auth_override_active" | "session_setup_required" | "setup_required" | "security_mode">,
): Exclude<DashboardAuthShell, "app"> {
  if (setupStatus.setup_required || securityStatus.setup_required || securityStatus.session_setup_required) {
    return "bootstrap"
  }

  const persistedNoAuth =
    !securityStatus.runtime_auth_override_active &&
    securityStatus.configured_mode === "none" &&
    securityStatus.effective_mode === "none"

  if (persistedNoAuth) {
    return "noauth"
  }

  if (securityStatus.security_mode === "API_KEY_ONLY" && securityStatus.effective_mode === "api_key") {
    return "api_key"
  }

  return "login"
}

export function Dashboard() {
  const [activeView, setActiveView] = useState<string>(getViewFromHash)
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [showWizard, setShowWizard] = useState(false)
  const [authShell, setAuthShell] = useState<DashboardAuthShell>("app")
  const [runtimePreflight, setRuntimePreflight] = useState<RuntimePreflightStatus | null>(null)
  const [showSetupPrompt, setShowSetupPrompt] = useState(false)
  const [showLoginPrompt, setShowLoginPrompt] = useState(false)
  const [setupMode, setSetupMode] = useState<AuthMode>("rbac")
  const [setupUsername, setSetupUsername] = useState("")
  const [setupPassword, setSetupPassword] = useState("")
  const [loginUsername, setLoginUsername] = useState("")
  const [loginPassword, setLoginPassword] = useState("")
  const [apiKeyInput, setApiKeyInput] = useState("")
  const [authFormError, setAuthFormError] = useState<string | null>(null)
  const [isSubmittingAuthForm, setIsSubmittingAuthForm] = useState(false)
  const { toast } = useToast()

  const loadSettings = useCallback(async (nextShell: Extract<DashboardAuthShell, "app" | "noauth"> = "app"): Promise<boolean> => {
    try {
      const data = await fetchSettings()
      setSettings(data)
      setAuthShell(nextShell)
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("channelwatch-auth-state-changed"))
      }
      setShowSetupPrompt(false)
      setShowLoginPrompt(false)
      setAuthFormError(null)
      if (!hasActiveDvrs(data)) {
        setShowWizard(true)
      }
      return true
    } catch (err) {
      if (err instanceof AuthRequiredError) {
        setSettings(null)
      } else {
        toast({
          variant: "destructive",
          title: t("dashboard.errorLoadingSettings"),
          description: err instanceof Error ? err.message : "An unknown error occurred",
        })
        setSettings(null)
      }
      return false
    }
  }, [toast])

  const bootstrapApp = useCallback(async () => {
    try {
      setIsLoading(true)
      setAuthShell("app")
      setShowSetupPrompt(false)
      setShowLoginPrompt(false)

      const nextRuntimePreflight = await fetchRuntimePreflight()
      setRuntimePreflight(nextRuntimePreflight)
      if (nextRuntimePreflight.setup_required) {
        setAuthShell("runtime")
        setSettings(null)
        return
      }

      const [setupStatus, securityStatus] = await Promise.all([fetchSetupStatus(), fetchSecurityStatus()])
      const bootstrapShell = resolveDashboardBootstrapShell(setupStatus, securityStatus)

      if (bootstrapShell === "bootstrap") {
        setAuthShell("bootstrap")
        setShowSetupPrompt(true)
        setSettings(null)
        return
      }

      if (bootstrapShell === "noauth") {
        clearCachedAuthState()
        await loadSettings("noauth")
        return
      }

      const loaded = await loadSettings("app")
      if (loaded) {
        return
      }

      setAuthShell(bootstrapShell === "api_key" ? "api_key" : "login")
      setShowLoginPrompt(true)
      setSettings(null)
    } catch (err) {
      toast({
        variant: "destructive",
        title: t("dashboard.errorLoadingSettings"),
        description: err instanceof Error ? err.message : "An unknown error occurred",
      })
      setSettings(null)
    } finally {
      setIsLoading(false)
    }
  }, [loadSettings, toast])

  // Sync hash to state on popstate (back/forward)
  useEffect(() => {
    const onHashChange = () => setActiveView(getViewFromHash())
    window.addEventListener("hashchange", onHashChange)
    return () => window.removeEventListener("hashchange", onHashChange)
  }, [])

  // Sync state to hash
  const navigate = useCallback((view: string) => {
    setActiveView(view)
    window.location.hash = `#${view}`
  }, [])

  useEffect(() => {
    bootstrapApp()
  }, [bootstrapApp])

  useEffect(() => {
    const handleRestartRecovered = () => {
      void bootstrapApp()
    }
    window.addEventListener(RESTART_RECOVERED_EVENT, handleRestartRecovered)
    return () => window.removeEventListener(RESTART_RECOVERED_EVENT, handleRestartRecovered)
  }, [bootstrapApp])

  const handleSettingsSaved = useCallback((updatedSettings: AppSettings) => {
    setSettings(updatedSettings)
    if (!hasActiveDvrs(updatedSettings)) {
      setShowWizard(true)
    }
  }, [])

  const handleWizardComplete = useCallback((updatedSettings: AppSettings) => {
    setSettings(updatedSettings)
    setShowWizard(false)
    navigate("overview")
  }, [navigate])

  const handleWizardSkip = useCallback(() => {
    setShowWizard(false)
    navigate("settings:general")
  }, [navigate])

  const handleSetupSubmit = useCallback(async () => {
    setIsSubmittingAuthForm(true)
    setAuthFormError(null)
    try {
      await completeInitialSetup(setupMode, setupUsername.trim(), setupPassword)
      await bootstrapApp()
    } catch (error) {
      setAuthFormError(error instanceof Error ? error.message : t("dashboard.auth.setupFailed"))
    } finally {
      setIsSubmittingAuthForm(false)
    }
  }, [bootstrapApp, setupMode, setupPassword, setupUsername])

  const handleLoginSubmit = useCallback(async () => {
    setIsSubmittingAuthForm(true)
    setAuthFormError(null)
    try {
      await loginWithPassword(loginUsername.trim(), loginPassword)
      await bootstrapApp()
    } catch (error) {
      if (error instanceof SessionRequiredError) {
        setAuthFormError(t("dashboard.auth.invalidCredentials"))
      } else {
        setAuthFormError(error instanceof Error ? error.message : t("dashboard.auth.loginFailed"))
      }
    } finally {
      setIsSubmittingAuthForm(false)
    }
  }, [bootstrapApp, loginPassword, loginUsername])

  const handleApiKeySubmit = useCallback(async () => {
    setIsSubmittingAuthForm(true)
    setAuthFormError(null)
    try {
      cacheApiKey(apiKeyInput.trim())
      const loaded = await loadSettings("app")
      if (!loaded) {
        throw new AuthRequiredError("Invalid API key", 401)
      }
    } catch (error) {
      clearCachedAuthState()
      setAuthShell("api_key")
      setShowLoginPrompt(true)
      setAuthFormError(error instanceof Error ? error.message : t("dashboard.auth.loginFailed"))
    } finally {
      setIsSubmittingAuthForm(false)
    }
  }, [apiKeyInput, loadSettings])

  const renderContent = () => {
    if (authShell === "runtime") {
      const blocker = runtimePreflight?.blockers[0] ?? "secret_storage_key_missing"
      return (
        <div data-testid="runtime-setup-required-shell" className="flex min-h-[75vh] w-full items-center justify-center px-4">
          <Card className="w-full max-w-2xl border-amber-500/40 shadow-lg">
            <CardHeader>
              <CardTitle><h1>{t("runtimeSetup.title")}</h1></CardTitle>
              <CardDescription>{t("runtimeSetup.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                <p className="font-medium">{t(`runtimeSetup.blocker.${blocker}`)}</p>
                <p className="mt-1 text-muted-foreground">{t("runtimeSetup.protection")}</p>
              </div>
              <ol className="list-decimal space-y-2 pl-5 text-sm text-muted-foreground">
                <li>{t("runtimeSetup.step.generate")}</li>
                <li>{t("runtimeSetup.step.configure")}</li>
                <li>{t("runtimeSetup.step.restart")}</li>
              </ol>
              <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 text-xs"><code>openssl rand -base64 48</code></pre>
              <div className="space-y-3 text-sm">
                <div>
                  <p className="mb-1 font-medium">{t("runtimeSetup.examples.compose")}</p>
                  <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 text-xs"><code>{`environment:
  CHANNELWATCH_SECRET_STORAGE_KEY: "\${CHANNELWATCH_SECRET_STORAGE_KEY}"`}</code></pre>
                </div>
                <div>
                  <p className="mb-1 font-medium">{t("runtimeSetup.examples.unraid")}</p>
                  <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 text-xs"><code>Variable name: CHANNELWATCH_SECRET_STORAGE_KEY</code></pre>
                </div>
                <div>
                  <p className="mb-1 font-medium">{t("runtimeSetup.examples.kubernetes")}</p>
                  <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 text-xs"><code>Secret data key: CHANNELWATCH_SECRET_STORAGE_KEY</code></pre>
                </div>
              </div>
              <p className="text-sm font-medium text-foreground">{t("runtimeSetup.preserve")}</p>
              <Button type="button" variant="outline" onClick={() => window.location.reload()}>
                {t("runtimeSetup.checkAgain")}
              </Button>
            </CardContent>
          </Card>
        </div>
      )
    }

    if (showSetupPrompt) {
      return (
        <div data-testid="auth-bootstrap-shell" className="flex h-[80vh] w-full items-center justify-center px-4">
          <Card className="w-full max-w-lg border-border/60 shadow-lg">
            <CardHeader>
              <CardTitle>{t("dashboard.setup.title")}</CardTitle>
              <CardDescription>{t("dashboard.setup.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="space-y-2">
                <Label htmlFor="auth-mode">{t("dashboard.setup.modeLabel")}</Label>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Button id="auth-mode" variant={setupMode === "rbac" ? "default" : "outline"} onClick={() => setSetupMode("rbac")}>
                    {t("dashboard.setup.secureMode")}
                  </Button>
                  <Button variant={setupMode === "none" ? "destructive" : "outline"} onClick={() => setSetupMode("none")}>
                    {t("dashboard.setup.noAuthMode")}
                  </Button>
                </div>
              </div>

              {setupMode === "rbac" ? (
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="setup-username">{t("dashboard.setup.username")}</Label>
                    <Input id="setup-username" value={setupUsername} onChange={(event) => setSetupUsername(event.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="setup-password">{t("dashboard.setup.password")}</Label>
                    <Input id="setup-password" type="password" value={setupPassword} onChange={(event) => setSetupPassword(event.target.value)} />
                  </div>
                </div>
              ) : (
                <p className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
                  {t("dashboard.setup.noAuthWarning")}
                </p>
              )}

              {authFormError && <p className="text-sm text-destructive">{authFormError}</p>}

              <Button
                onClick={handleSetupSubmit}
                disabled={isSubmittingAuthForm || (setupMode === "rbac" && (!setupUsername.trim() || !setupPassword))}
              >
                {isSubmittingAuthForm ? t("dashboard.setup.submitting") : t("dashboard.setup.submit")}
              </Button>
            </CardContent>
          </Card>
        </div>
      )
    }

    if (showLoginPrompt) {
      return (
        <div data-testid="auth-login-shell" className="flex h-[80vh] w-full items-center justify-center px-4">
          <Card className="w-full max-w-md border-border/60 shadow-lg">
            <CardHeader>
              <CardTitle>{t("dashboard.login.title")}</CardTitle>
              <CardDescription>{t("dashboard.login.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <>
                {authShell === "api_key" ? (
                  <div className="space-y-2">
                    <Label htmlFor="login-api-key">API key</Label>
                    <Input id="login-api-key" type="password" value={apiKeyInput} onChange={(event) => setApiKeyInput(event.target.value)} />
                  </div>
                ) : (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="login-username">{t("dashboard.login.username")}</Label>
                      <Input id="login-username" value={loginUsername} onChange={(event) => setLoginUsername(event.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="login-password">{t("dashboard.login.password")}</Label>
                      <Input id="login-password" type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} />
                    </div>
                  </>
                )}
                {authFormError && <p className="text-sm text-destructive">{authFormError}</p>}
                <Button
                  onClick={authShell === "api_key" ? handleApiKeySubmit : handleLoginSubmit}
                  disabled={isSubmittingAuthForm || (authShell === "api_key" ? !apiKeyInput.trim() : (!loginUsername.trim() || !loginPassword))}
                >
                  {isSubmittingAuthForm ? t("dashboard.login.submitting") : t("dashboard.login.submit")}
                </Button>
              </>
            </CardContent>
          </Card>
        </div>
      )
    }

    if (isLoading) {
      return (
        <div className="flex h-[80vh] w-full items-center justify-center">
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
          </div>
        </div>
      )
    }

    const settingsTab = activeView.startsWith("settings:") ? activeView.split(":")[1] : undefined

    switch (activeView.split(":")[0]) {
      case "overview":
        return <StatusOverview settings={settings} onNavigate={navigate} />
      case "watch-history":
        return <WatchHistory />
      case "notification-log":
        return <NotificationLog />
      case "settings":
        return <SettingsForm settings={settings} onSettingsSaved={handleSettingsSaved} initialTab={settingsTab} />
      case "diagnostics":
        return <DiagnosticsPanel />
      case "about":
        return <AboutSection />
      default:
        return <StatusOverview settings={settings} onNavigate={navigate} />
    }
  }

  return (
    <>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-md focus:bg-background focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        {t("common.skipToContent")}
      </a>

      <div className="flex min-h-screen">
        <HeaderContext.Provider value={{ activeView, setActiveView: navigate }}>
          <DvrSelectionProvider availableDvrs={settings?.dvr_servers ?? []}>
            <Sidebar activeView={activeView} setActiveView={navigate} />
            <div className="flex flex-col flex-1 overflow-hidden relative">
              <Header />
              <main
                id="main-content"
                tabIndex={-1}
                className="flex-1 overflow-y-auto overflow-x-hidden p-3 pt-24 md:p-6 md:pt-24"
              >
                {runtimePreflight?.warnings.includes("legacy_plaintext_key_migration_recommended") && (
                  <div data-testid="runtime-migration-warning" role="status" className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-foreground">
                    {t("runtimeSetup.migrationWarning")}
                  </div>
                )}
                {authShell === "noauth" ? <div data-testid="auth-noauth-shell">{renderContent()}</div> : renderContent()}
              </main>
            </div>
          </DvrSelectionProvider>
        </HeaderContext.Provider>

        {showWizard && !isLoading && settings && !showSetupPrompt && !showLoginPrompt && (
          <FirstRunWizard
            currentSettings={settings}
            onComplete={handleWizardComplete}
            onSkip={handleWizardSkip}
          />
        )}
      </div>
    </>
  )
}
