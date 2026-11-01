"use client"

import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef, type FormEvent } from "react"
import { Sidebar } from "@/components/sidebar"
import { Header, HeaderContext } from "@/components/header"
import { SettingsForm } from "@/components/settings-form"
import { getEnforcedSecurityMode } from "@/components/settings/security-section"
import { DiagnosticsPanel } from "@/components/diagnostics-panel"
import { AboutSection } from "@/components/about-section"
import { HelpFeedback } from "@/components/help-feedback"
import { HomeSkeleton } from "@/components/dashboard/home-skeleton"
import { StatusOverview } from "@/components/status-overview"
import { WatchHistory } from "@/components/watch-history"
import { NotificationLog } from "@/components/notification-log"
import { FirstRunWizard } from "@/components/first-run-wizard"
import { OfficialRecoveryUpdateActions } from "@/components/recovery-update-actions"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Label } from "@/components/base/label"
import { DvrSelectionProvider } from "@/lib/dvr-selection-context"
import { useToast } from "@/hooks/use-toast"
import type { AppSettings, AuthMode, AuthSetupStatus, SecurityStatus, RuntimePreflightStatus } from "@/lib/types"
import { AuthRequiredError, RESTART_RECOVERED_EVENT, RUNTIME_PREFLIGHT_CHANGED_EVENT, SESSION_EXPIRED_EVENT, SESSION_RESTORED_EVENT, SessionRequiredError, cacheApiKey, clearCachedAuthState, completeInitialSetup, fetchRuntimePreflight, fetchSecurityStatus, fetchSettings, fetchSetupStatus, loginWithPassword } from "@/lib/api"
import { t } from "@/lib/i18n"
import { ErrorCode, describeError, formatErrorWithRemediation } from "@/lib/error-catalog"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { Loader2 } from "lucide-react"
import { consumeUpdatedDashboardHandoff } from "@/lib/update-reconnect"
import { isRateLimited } from "@/lib/rate-limit"

const VALID_VIEWS = ["overview", "watch-history", "notification-log", "settings", "diagnostics", "help-feedback", "about"]
const VALID_SETTINGS_TABS = ["general", "alerts", "advanced", "notifications", "routing", "security", "backup", "updates"]
const KEPT_VIEWS = ["settings", "help-feedback"]
const WIZARD_SKIPPED_KEY = "cw_first_run_skipped"
const LOAD_RETRY_MS = 5000
const VIEW_TITLE_KEYS: Record<string, string> = {
  "watch-history": "nav.watchHistory",
  "notification-log": "nav.notificationLog",
  settings: "nav.settings",
  diagnostics: "nav.diagnostics",
  "help-feedback": "nav.helpFeedback",
  about: "nav.about",
}

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

export function hasActiveDvrs(settings: AppSettings | null): boolean {
  if (!settings) return false
  const servers = settings.dvr_servers || []
  return servers.some((s) => !s.deleted_at)
}

export type DashboardAuthShell = "app" | "bootstrap" | "login" | "api_key" | "noauth"

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
  const [activeView, setActiveView] = useState<string>("overview")
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [noSignIn, setNoSignIn] = useState(false)
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
  // Home is mounted the first time it is opened, then kept, so coming back is instant.
  const [homeVisited, setHomeVisited] = useState(false)
  const scrollByView = useRef<Record<string, number>>({})
  const pendingScroll = useRef<number | null>(null)
  // Settings and Help hold work in progress, so they stay mounted once opened, like Home.
  const [keptViews, setKeptViews] = useState<string[]>([])
  const activeBase = activeView.split(":")[0]
  if (KEPT_VIEWS.includes(activeBase) && !keptViews.includes(activeBase)) setKeptViews([...keptViews, activeBase])
  const lastSettingsTab = useRef<string | undefined>(undefined)
  const seenViews = useRef(new Set<string>())
  const lastBase = useRef("")
  const [loadFailed, setLoadFailed] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [sessionExpired, setSessionExpired] = useState(false)
  const settingsLoaded = useRef(false)
  const checkingSession = useRef(false)
  const sessionExpiredNow = useRef(false)
  const signIns = useRef(0)
  const activeDvrs = useMemo(() => (settings?.dvr_servers ?? []).filter((server) => !server.deleted_at), [settings])

  useEffect(() => {
    settingsLoaded.current = settings !== null
  }, [settings])

  const loadSettings = useCallback(async (nextShell: Extract<DashboardAuthShell, "app" | "noauth"> = "app"): Promise<"ok" | "auth" | "unavailable"> => {
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
      setLoadFailed(false)
      if (!hasActiveDvrs(data) && !sessionStorage.getItem(WIZARD_SKIPPED_KEY)) {
        setShowWizard(true)
      }
      return "ok"
    } catch (err) {
      if (err instanceof AuthRequiredError) {
        setSettings(null)
        return "auth"
      }
      // Whatever is already on screen stays. Only a first load has nothing to show.
      if (!settingsLoaded.current) setSettings(null)
      return "unavailable"
    }
  }, [])

  const bootstrapApp = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}) => {
    try {
      if (!quiet) {
        setIsLoading(true)
        setAuthShell("app")
        setShowSetupPrompt(false)
        setShowLoginPrompt(false)
      }

      const nextRuntimePreflight = await fetchRuntimePreflight()
      setRuntimePreflight(nextRuntimePreflight)

      const [setupStatus, securityStatus] = await Promise.all([fetchSetupStatus(), fetchSecurityStatus()])
      const bootstrapShell = resolveDashboardBootstrapShell(setupStatus, securityStatus)
      setNoSignIn(getEnforcedSecurityMode(securityStatus) === "NO_AUTH")

      if (bootstrapShell === "bootstrap") {
        setAuthShell("bootstrap")
        setShowSetupPrompt(true)
        setLoadFailed(false)
        setSettings(null)
        return
      }

      if (bootstrapShell === "noauth") {
        clearCachedAuthState()
        if (await loadSettings("noauth") === "unavailable" && !settingsLoaded.current) setLoadFailed(true)
        return
      }

      const loaded = await loadSettings("app")
      if (loaded === "ok") {
        return
      }
      if (loaded === "unavailable") {
        if (!settingsLoaded.current) setLoadFailed(true)
        return
      }

      setAuthShell(bootstrapShell === "api_key" ? "api_key" : "login")
      setShowLoginPrompt(true)
      setLoadFailed(false)
      setSettings(null)
    } catch {
      if (!settingsLoaded.current) {
        setLoadFailed(true)
        setSettings(null)
      }
    } finally {
      setIsLoading(false)
    }
  }, [loadSettings])

  const retryLoad = async () => {
    setRetrying(true)
    await bootstrapApp({ quiet: true })
    setRetrying(false)
  }

  // A restart or a busy server clears up by itself, so this keeps trying without being asked.
  useEffect(() => {
    if (!loadFailed) return
    const timer = window.setInterval(() => {
      if (!document.hidden && !isRateLimited()) void bootstrapApp({ quiet: true })
    }, LOAD_RETRY_MS)
    return () => window.clearInterval(timer)
  }, [bootstrapApp, loadFailed])

  // Any call can be the first to learn the session ended. Sign-in opens above the page so nothing on it is lost.
  useEffect(() => {
    const onSessionExpired = async () => {
      if (checkingSession.current || sessionExpiredNow.current || !settingsLoaded.current) return
      checkingSession.current = true
      const signInsAtStart = signIns.current
      try {
        await fetchSettings()
      } catch (err) {
        if (err instanceof AuthRequiredError) {
          const [setupStatus, securityStatus] = await Promise.all([fetchSetupStatus(), fetchSecurityStatus()]).catch(() => [null, null])
          // A sign-in that finished while this check was out already answered it.
          if (signIns.current !== signInsAtStart) return
          const shell = setupStatus && securityStatus ? resolveDashboardBootstrapShell(setupStatus, securityStatus) : "login"
          clearCachedAuthState()
          setAuthShell(shell === "api_key" ? "api_key" : "login")
          setAuthFormError(null)
          sessionExpiredNow.current = true
          setSessionExpired(true)
        }
      } finally {
        checkingSession.current = false
      }
    }
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
  }, [])

  // Known before Home first renders, so the Status row never pops in and pushes the page down.
  useEffect(() => {
    const refresh = () => {
      fetchSecurityStatus()
        .then((status) => setNoSignIn(getEnforcedSecurityMode(status) === "NO_AUTH"))
        .catch(() => undefined)
    }
    window.addEventListener("channelwatch-auth-state-changed", refresh)
    return () => window.removeEventListener("channelwatch-auth-state-changed", refresh)
  }, [])

  // Sync hash to state on popstate (back/forward)
  useEffect(() => {
    const onHashChange = (event: HashChangeEvent) => {
      const next = getViewFromHash()
      setActiveView(next)
      // Home stays mounted behind other tabs, so a sheet or menu it opened would outlive the view.
      // Escape is the one close path every layer already honors, draft guards included.
      // Help is the exception: its sheets hold a draft and step aside until you come back.
      const from = new URL(event.oldURL).hash.slice(1).split(":")[0] || "overview"
      if (from !== next.split(":")[0] && from !== "help-feedback") document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }))
    }
    setActiveView(getViewFromHash())
    window.addEventListener("hashchange", onHashChange)
    return () => window.removeEventListener("hashchange", onHashChange)
  }, [])

  // Sync state to hash
  const navigate = useCallback((view: string) => {
    setActiveView((current) => {
      const from = current.split(":")[0]
      const to = view.split(":")[0]
      if (current === view) {
        // Tapping the tab you are on goes back to its top.
        window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })
      } else if (from !== to) {
        scrollByView.current[from] = window.scrollY
        pendingScroll.current = to === "overview" ? scrollByView.current.overview ?? 0 : 0
      }
      return view
    })
    window.location.hash = `#${view}`
  }, [])

  // Only navigate() sets a target, so browser back keeps its own scroll restoration.
  useLayoutEffect(() => {
    if (pendingScroll.current === null) return
    window.scrollTo({ top: pendingScroll.current, behavior: "instant" })
    pendingScroll.current = null
  }, [activeView])

  // Going from hidden to shown re-fires @starting-style, so a kept view would replay its entry fades.
  useLayoutEffect(() => {
    lastBase.current = activeBase
    const returning = seenViews.current.has(activeBase)
    seenViews.current.add(activeBase)
    const view = returning ? document.querySelector<HTMLElement>(`[data-view="${activeBase}"]`) : null
    if (!view) return
    view.dataset.returning = ""
    requestAnimationFrame(() => requestAnimationFrame(() => {
      delete view.dataset.returning
    }))
  }, [activeBase])

  useEffect(() => {
    const titleKey = VIEW_TITLE_KEYS[activeBase]
    document.title = titleKey ? `${t(titleKey)} - ChannelWatch` : "ChannelWatch"
  }, [activeBase])

  useEffect(() => {
    const base = activeView.split(":")[0]
    // Waits for loading to end, by which point the hash has been read. A deep link to another tab reads nothing for Home.
    if (!isLoading && (!VALID_VIEWS.includes(base) || base === "overview")) setHomeVisited(true)
  }, [activeView, isLoading])

  useEffect(() => {
    if (consumeUpdatedDashboardHandoff()) {
      setActiveView("overview")
      window.history.replaceState(null, "", "#overview")
    }
  }, [])

  useEffect(() => {
    bootstrapApp()
  }, [bootstrapApp])

  useEffect(() => {
    const refreshRuntimePreflight = () => {
      void fetchRuntimePreflight().then(setRuntimePreflight).catch(() => {
        // The recovery card reports its own actionable error. Retain the last
        // known banner state if this best-effort refresh cannot complete.
      })
    }
    window.addEventListener(RUNTIME_PREFLIGHT_CHANGED_EVENT, refreshRuntimePreflight)
    return () => window.removeEventListener(RUNTIME_PREFLIGHT_CHANGED_EVENT, refreshRuntimePreflight)
  }, [])

  useEffect(() => {
    // Quiet, so a restart does not throw away what is open or being edited.
    const handleRestartRecovered = () => {
      void bootstrapApp({ quiet: true })
    }
    window.addEventListener(RESTART_RECOVERED_EVENT, handleRestartRecovered)
    return () => window.removeEventListener(RESTART_RECOVERED_EVENT, handleRestartRecovered)
  }, [bootstrapApp])

  const handleSettingsSaved = useCallback((updatedSettings: AppSettings) => {
    setSettings(updatedSettings)
    if (!hasActiveDvrs(updatedSettings) && !sessionStorage.getItem(WIZARD_SKIPPED_KEY)) {
      setShowWizard(true)
    }
  }, [])

  const handleWizardComplete = useCallback((updatedSettings: AppSettings) => {
    setSettings(updatedSettings)
    setShowWizard(false)
    navigate("overview")
  }, [navigate])

  const handleWizardSkip = useCallback(() => {
    sessionStorage.setItem(WIZARD_SKIPPED_KEY, "1")
    setShowWizard(false)
    navigate("settings:general")
  }, [navigate])

  const handleSessionRestored = useCallback(() => {
    signIns.current += 1
    sessionExpiredNow.current = false
    setSessionExpired(false)
    setLoginPassword("")
    setApiKeyInput("")
    window.dispatchEvent(new CustomEvent("channelwatch-auth-state-changed"))
    window.dispatchEvent(new CustomEvent(SESSION_RESTORED_EVENT))
  }, [])

  const handleSetupSubmit = useCallback(async () => {
    setIsSubmittingAuthForm(true)
    setAuthFormError(null)
    try {
      await completeInitialSetup(setupMode, setupUsername.trim(), setupPassword)
      await bootstrapApp()
    } catch (error) {
      if (describeError(error, "").code === ErrorCode.AUTH_ADMIN_EXISTS) {
        // Another tab or admin finished setup first. Reload the auth state so
        // the sign-in card replaces the setup card that can no longer succeed.
        await bootstrapApp().catch(() => undefined)
        setAuthFormError(t("dashboard.auth.setupAlreadyCompleted"))
        return
      }
      setAuthFormError(formatErrorWithRemediation(error, t("dashboard.auth.setupFailed")))
    } finally {
      setIsSubmittingAuthForm(false)
    }
  }, [bootstrapApp, setupMode, setupPassword, setupUsername])

  const handleLoginSubmit = useCallback(async () => {
    setIsSubmittingAuthForm(true)
    setAuthFormError(null)
    try {
      await loginWithPassword(loginUsername.trim(), loginPassword)
      if (sessionExpired) handleSessionRestored()
      else await bootstrapApp()
    } catch (error) {
      if (error instanceof SessionRequiredError) {
        setAuthFormError(t("dashboard.auth.invalidCredentials"))
      } else {
        setAuthFormError(formatErrorWithRemediation(error, t("dashboard.auth.loginFailed")))
      }
    } finally {
      setIsSubmittingAuthForm(false)
    }
  }, [bootstrapApp, handleSessionRestored, loginPassword, loginUsername, sessionExpired])

  const handleApiKeySubmit = useCallback(async () => {
    setIsSubmittingAuthForm(true)
    setAuthFormError(null)
    try {
      cacheApiKey(apiKeyInput.trim())
      if (sessionExpired) {
        await fetchSettings()
        handleSessionRestored()
        return
      }
      const loaded = await loadSettings("app")
      if (loaded !== "ok") {
        throw new AuthRequiredError("Invalid API key", 401)
      }
    } catch (error) {
      clearCachedAuthState()
      setAuthShell("api_key")
      if (!sessionExpired) setShowLoginPrompt(true)
      setAuthFormError(error instanceof AuthRequiredError ? "Invalid API key" : error instanceof Error ? error.message : t("dashboard.auth.loginFailed"))
    } finally {
      setIsSubmittingAuthForm(false)
    }
  }, [apiKeyInput, handleSessionRestored, loadSettings, sessionExpired])

  const renderLoginCard = (description: string) => (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle id="login-title">{t("dashboard.login.title")}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={preventSubmit(authShell === "api_key" ? handleApiKeySubmit : handleLoginSubmit)}>
          {authShell === "api_key" ? (
            <div className="space-y-2">
              <Label htmlFor="login-api-key">API key</Label>
              <Input id="login-api-key" type="password" autoComplete="off" autoCapitalize="none" spellCheck={false} enterKeyHint="go" value={apiKeyInput} onChange={(event) => setApiKeyInput(event.target.value)} />
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <Label htmlFor="login-username">{t("dashboard.login.username")}</Label>
                <Input id="login-username" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} enterKeyHint="next" value={loginUsername} onChange={(event) => setLoginUsername(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="login-password">{t("dashboard.login.password")}</Label>
                <Input id="login-password" type="password" autoComplete="current-password" enterKeyHint="go" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} />
              </div>
            </>
          )}
          {authFormError && <p role="alert" className="text-sm text-danger-fg">{authFormError}</p>}
          <Button
            type="submit"
            disabled={isSubmittingAuthForm || (authShell === "api_key" ? !apiKeyInput.trim() : (!loginUsername.trim() || !loginPassword))}
          >
            {isSubmittingAuthForm ? t("dashboard.login.submitting") : t("dashboard.login.submit")}
          </Button>
        </form>
      </CardContent>
    </Card>
  )

  const preventSubmit = (handler: () => void) => (event: FormEvent) => {
    event.preventDefault()
    handler()
  }

  const renderContent = () => {
    if (showSetupPrompt) {
      return (
        <div data-testid="auth-bootstrap-shell" className="flex min-h-[80vh] w-full items-center justify-center px-4 py-6">
          <Card className="w-full max-w-lg">
            <CardHeader>
              <CardTitle>{t("dashboard.setup.title")}</CardTitle>
              <CardDescription>{t("dashboard.setup.description")}</CardDescription>
            </CardHeader>
            <CardContent>
            <form className="space-y-5" onSubmit={preventSubmit(handleSetupSubmit)}>
              <OfficialRecoveryUpdateActions compact />
              <div className="space-y-2">
                <Label htmlFor="auth-mode">{t("dashboard.setup.modeLabel")}</Label>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Button id="auth-mode" type="button" aria-pressed={setupMode === "rbac"} variant={setupMode === "rbac" ? "default" : "outline"} onClick={() => setSetupMode("rbac")}>
                    {t("dashboard.setup.secureMode")}
                  </Button>
                  <Button type="button" aria-pressed={setupMode === "none"} variant={setupMode === "none" ? "destructive" : "outline"} onClick={() => setSetupMode("none")}>
                    {t("dashboard.setup.noAuthMode")}
                  </Button>
                </div>
              </div>

              {setupMode === "rbac" ? (
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="setup-username">{t("dashboard.setup.username")}</Label>
                    <Input id="setup-username" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} enterKeyHint="next" value={setupUsername} onChange={(event) => setSetupUsername(event.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="setup-password">{t("dashboard.setup.password")}</Label>
                    <Input id="setup-password" type="password" autoComplete="new-password" enterKeyHint="go" value={setupPassword} onChange={(event) => setSetupPassword(event.target.value)} />
                  </div>
                </div>
              ) : (
                <p className="rounded-control border border-warning-border bg-warning-bg p-3 text-sm text-warning-fg">
                  {t("dashboard.setup.noAuthWarning")}
                </p>
              )}

              {authFormError && <p role="alert" className="text-sm text-danger-fg">{authFormError}</p>}

              <Button
                type="submit"
                disabled={isSubmittingAuthForm || (setupMode === "rbac" && (!setupUsername.trim() || !setupPassword))}
              >
                {isSubmittingAuthForm ? t("dashboard.setup.submitting") : t("dashboard.setup.submit")}
              </Button>
            </form>
            </CardContent>
          </Card>
        </div>
      )
    }

    if (showLoginPrompt) {
      return (
        <div data-testid="auth-login-shell" className="flex min-h-[80vh] w-full items-center justify-center px-4 py-6">
          {renderLoginCard(t("dashboard.login.description"))}
        </div>
      )
    }

    if (loadFailed) {
      return (
        <div data-testid="app-load-failed" role="alert" className="flex min-h-[80vh] w-full items-center justify-center px-4 py-6">
          <Card className="w-full max-w-md">
            <CardHeader>
              <CardTitle>{t("dashboard.loadFailed.title")}</CardTitle>
              <CardDescription>{t("dashboard.loadFailed.description")}</CardDescription>
            </CardHeader>
            <CardContent>
              <Button type="button" variant="outline" disabled={retrying} onClick={() => void retryLoad()}>
                {retrying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
                {t("common.tryAgain")}
              </Button>
            </CardContent>
          </Card>
        </div>
      )
    }

    if (isLoading && activeView.split(":")[0] === "overview") return <HomeSkeleton />

    if (isLoading) {
      return (
        <div className="flex h-[80vh] w-full items-center justify-center">
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
          </div>
        </div>
      )
    }

    const base = activeView.split(":")[0]
    const onHome = !VALID_VIEWS.includes(base) || base === "overview"
    // The tab is left alone while Settings is out of view, so an open section keeps its state.
    if (activeView.startsWith("settings:")) lastSettingsTab.current = activeView.split(":")[1]
    // Coming back keeps the section you left. Asking for an unknown section while here goes to the first one.
    else if (base === "settings" && lastBase.current === "settings") lastSettingsTab.current = undefined

    const otherView = () => {
      switch (base) {
        case "watch-history":
          return <WatchHistory onNavigate={navigate} />
        case "notification-log":
          return <NotificationLog onNavigate={navigate} />
        case "diagnostics":
          return <DiagnosticsPanel />
        case "about":
          return <AboutSection />
        default:
          return null
      }
    }

    return (
      <>
        {onHome || homeVisited ? (
          <div data-view="overview" hidden={!onHome}>
            <StatusOverview settings={settings} onNavigate={navigate} noSignIn={noSignIn} paused={!onHome} />
          </div>
        ) : null}
        {keptViews.includes("settings") ? (
          <div data-view="settings" hidden={base !== "settings"}>
            <SettingsForm settings={settings} onSettingsSaved={handleSettingsSaved} activeTab={lastSettingsTab.current} onTabChange={(tab) => navigate(`settings:${tab}`)} active={base === "settings"} />
          </div>
        ) : null}
        {keptViews.includes("help-feedback") ? (
          <div data-view="help-feedback" hidden={base !== "help-feedback"}>
            <HelpFeedback appSettings={settings} active={base === "help-feedback"} />
          </div>
        ) : null}
        {onHome ? null : otherView()}
      </>
    )
  }

  const signedOut = showSetupPrompt || showLoginPrompt

  return (
    <>
      <a
        href="#main-content"
        // A plain link would change the hash, which is how this app navigates.
        onClick={(event) => {
          event.preventDefault()
          document.getElementById("main-content")?.focus()
        }}
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-control focus:bg-background focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:shadow-3 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        {t("common.skipToContent")}
      </a>

      <div inert={sessionExpired} className="flex min-h-[100dvh] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]">
        <HeaderContext.Provider value={{ activeView, setActiveView: navigate }}>
          <DvrSelectionProvider availableDvrs={activeDvrs}>
            {signedOut ? null : <Sidebar activeView={activeView} setActiveView={navigate} />}
            <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
              {signedOut ? null : <Header />}
              <main
                id="main-content"
                tabIndex={-1}
                className="mx-auto w-full max-w-[1440px] flex-1 overflow-y-auto overflow-x-hidden px-[min(1rem,16px)] pb-[calc(var(--mobile-navigation-height,4rem)+24px)] pt-[calc(var(--app-header-height,4rem)+16px)] sm:px-6 md:px-8 md:pb-10 md:pt-[calc(var(--app-header-height,4rem)+2rem)]"
              >
                {(runtimePreflight?.setup_required || runtimePreflight?.status === "recovery_required" || runtimePreflight?.status === "storage_unavailable") && (
                  <div data-testid="runtime-recovery-warning" role="alert" className="mb-5 rounded-group border border-warning-border bg-warning-bg p-4 text-xs text-foreground">
                    <p className="font-medium">{t("runtimeRecovery.bannerTitle")}</p>
                    <p className="mt-1 text-muted-foreground">{t("runtimeRecovery.bannerDescription")}</p>
                    {settings ? (
                      <Button type="button" variant="outline" className="mt-3" onClick={() => navigate("settings:security")}>
                        {t("runtimeRecovery.openSecurity")}
                      </Button>
                    ) : (
                      <div className="mt-3 space-y-3">
                        <p className="font-medium">
                          {showSetupPrompt ? t("runtimeRecovery.setupHint") : t("runtimeRecovery.signInHint")}
                        </p>
                        {!showSetupPrompt ? <OfficialRecoveryUpdateActions compact /> : null}
                      </div>
                    )}
                  </div>
                )}
                {(runtimePreflight?.warnings.includes("legacy_plaintext_key_migration_recommended") || runtimePreflight?.warnings.includes("legacy_envelope_migration_available")) && (
                  <div data-testid="runtime-migration-warning" role="status" className="mb-5 rounded-group border border-warning-border bg-warning-bg p-4 text-xs text-foreground">
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

      {sessionExpired ? (
        // A modal layer of its own, so it takes pointer and focus from any sheet that was open, and that sheet keeps its draft.
        <DialogPrimitive.Root open>
          <DialogPrimitive.Portal>
            <DialogPrimitive.Content
              data-testid="session-expired"
              aria-describedby={undefined}
              onEscapeKeyDown={(event) => event.preventDefault()}
              onPointerDownOutside={(event) => event.preventDefault()}
              className="fixed inset-0 z-[70] flex items-center justify-center overflow-y-auto bg-background px-4 py-6 focus:outline-none"
            >
              <DialogPrimitive.Title className="sr-only">{t("dashboard.login.title")}</DialogPrimitive.Title>
              {renderLoginCard(t("dashboard.login.expiredDescription"))}
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
      ) : null}
    </>
  )
}
