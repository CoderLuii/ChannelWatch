"use client"

import { useState, useEffect, useLayoutEffect, useRef, useContext, createContext, type MouseEvent } from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { ModeToggle } from "@/components/mode-toggle"
import { Button } from "@/components/base/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { ApiError, RESTART_RECOVERED_EVENT, signalContainerRestart, fetchMonitoringReadiness, fetchSettings, pollForRecovery, fetchSecurityStatus, fetchWhoAmI, logoutSession } from "@/lib/api"
import { t } from "@/lib/i18n"
import { useToast } from "@/hooks/use-toast"
import { SecurityModeBadge } from "@/components/settings/security-section"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import type { SecurityStatus, WhoAmIResponse } from "@/lib/types"
import {
  Menu,
  Power,
  Loader2,
  CheckCircle,
  XCircle,
  RefreshCw,
  Server,
  LogOut,
} from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/base/tooltip"


export const HeaderContext = createContext<{
  activeView: string;
  setActiveView: (view: string) => void;
}>({
  activeView: "overview",
  setActiveView: () => {}
});

type OverlayState = "idle" | "restarting" | "success" | "degraded" | "failed" | "dismissing"

export function Header() {
  const [isRestarting, setIsRestarting] = useState(false)
  const [overlayState, setOverlayState] = useState<OverlayState>("idle")
  const [elapsed, setElapsed] = useState(0)
  const [securityStatus, setSecurityStatus] = useState<SecurityStatus | null>(null)
  const [whoAmI, setWhoAmI] = useState<WhoAmIResponse | null>(null)
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [restartDialog, setRestartDialog] = useState<HTMLDivElement | null>(null)
  const overlayOpen = overlayState !== "idle"
  const restartFocusRef = useRef<HTMLElement | null>(null)
  const cancelPollRef = useRef<(() => void) | null>(null)
  const headerRef = useRef<HTMLElement | null>(null)
  const { toast } = useToast()
  const { setActiveView } = useContext(HeaderContext)
  const { selectedDvr, setSelectedDvr, availableDvrs } = useDvrSelection()

  useLayoutEffect(() => {
    const dialog = restartDialog
    if (!overlayOpen || !dialog) return
    const background = Array.from(document.body.children)
      .filter((element): element is HTMLElement => element instanceof HTMLElement && !element.contains(dialog) && !element.hasAttribute("data-radix-focus-guard"))
      .map(element => ({ element, wasInert: element.inert }))
    background.forEach(({ element }) => { element.inert = true })
    return () => { background.forEach(({ element, wasInert }) => { element.inert = wasInert }) }
  }, [overlayOpen, restartDialog])

  useLayoutEffect(() => {
    const header = headerRef.current
    if (!header) return
    const publishHeight = () => document.documentElement.style.setProperty("--app-header-height", `${Math.ceil(header.getBoundingClientRect().height)}px`)
    publishHeight()
    const observer = new ResizeObserver(publishHeight)
    observer.observe(header)
    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty("--app-header-height")
    }
  }, [])

  useEffect(() => {
    try {
      if (sessionStorage.getItem("channelwatch-restart-focus") !== "restore") return
      sessionStorage.removeItem("channelwatch-restart-focus")
      requestAnimationFrame(() => {
        const trigger = Array.from(document.querySelectorAll<HTMLButtonElement>("[data-restart-trigger]"))
          .find(button => button.getClientRects().length > 0)
        trigger?.focus()
      })
    } catch {
      // Session storage can be unavailable in restricted browser contexts.
    }
  }, [])

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (cancelPollRef.current) cancelPollRef.current()
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    const loadSecurityStatus = async () => {
      try {
        const nextStatus = await fetchSecurityStatus()
        if (!cancelled) setSecurityStatus(nextStatus)
      } catch {
        if (!cancelled) setSecurityStatus(null)
      }
    }

    const loadWhoAmI = async () => {
      try {
        const nextWhoAmI = await fetchWhoAmI()
        if (!cancelled) setWhoAmI(nextWhoAmI)
      } catch {
        if (!cancelled) setWhoAmI(null)
      }
    }

    loadSecurityStatus()
    loadWhoAmI()
    const handleRefresh = () => {
      loadSecurityStatus()
      loadWhoAmI()
    }
    window.addEventListener("channelwatch-auth-state-changed", handleRefresh)
    return () => {
      cancelled = true
      window.removeEventListener("channelwatch-auth-state-changed", handleRefresh)
    }
  }, [])

  const handleLogout = async () => {
    setIsLoggingOut(true)
    try {
      await logoutSession()
      window.dispatchEvent(new CustomEvent("channelwatch-auth-state-changed"))
      window.location.reload()
    } catch (error) {
      setIsLoggingOut(false)
      toast({
        variant: "destructive",
        title: t("header.signOutFailed"),
        description: error instanceof Error ? error.message : t("errors.tryAgain"),
      })
    }
  }

  const handleRestart = async (event: MouseEvent<HTMLButtonElement>) => {
    const trigger = event.currentTarget
    try {
      if (!confirm(t("header.restartConfirm"))) {
        return
      }

      restartFocusRef.current = trigger
      setIsRestarting(true)
      setOverlayState("restarting")
      setElapsed(0)

      // Fire the restart request (may or may not get a response before container dies)
      try {
        await signalContainerRestart()
      } catch (error) {
        if (error instanceof ApiError) throw error
        // Expected: container may die before response arrives
      }

      // Start health polling
      cancelPollRef.current = pollForRecovery({
        initialDelay: 3000,
        interval: 2000,
        timeout: 60000,
        onTick: (elapsedMs) => {
          setElapsed(Math.floor(elapsedMs / 1000))
        },
        onRecovered: async () => {
          window.dispatchEvent(new CustomEvent("channelwatch-auth-state-changed"))
          window.dispatchEvent(new CustomEvent(RESTART_RECOVERED_EVENT))
          // Re-bootstrap API key
          try {
            await fetchSettings()
          } catch {
            // Non-critical, will be picked up on next poll
          }

          const monitoringReady = await fetchMonitoringReadiness().catch(() => null)
          setOverlayState(monitoringReady === true ? "success" : "degraded")

          // Auto-dismiss after 3 seconds
          setTimeout(() => {
            setOverlayState("dismissing")
          }, 3000)
        },
        onTimeout: () => {
          setOverlayState("failed")
        },
      })
    } catch (error) {
      setOverlayState("idle")
      setIsRestarting(false)
      const description =
        error instanceof ApiError
          ? [error.message, error.payload.remediation].filter(Boolean).join(" ")
          : t("header.restartFailedDesc")
      toast({
        variant: "destructive",
        title: t("header.restartFailed"),
        description,
      })
    }
  }

  const handleRetry = () => {
    setOverlayState("restarting")
    setElapsed(0)

    cancelPollRef.current = pollForRecovery({
      initialDelay: 0,
      interval: 2000,
      timeout: 60000,
      minimumRecoveryMs: 0,
      onTick: (elapsedMs) => {
        setElapsed(Math.floor(elapsedMs / 1000))
      },
      onRecovered: async () => {
        window.dispatchEvent(new CustomEvent("channelwatch-auth-state-changed"))
        window.dispatchEvent(new CustomEvent(RESTART_RECOVERED_EVENT))
        try {
          await fetchSettings()
        } catch {}
        const monitoringReady = await fetchMonitoringReadiness().catch(() => null)
        setOverlayState(monitoringReady === true ? "success" : "degraded")
        setTimeout(() => {
          setOverlayState("dismissing")
        }, 3000)
      },
      onTimeout: () => {
        setOverlayState("failed")
      },
    })
  }

  const handleDismissOverlay = () => {
    setOverlayState("idle")
    setIsRestarting(false)
    // Keep the existing fresh-page recovery while restoring keyboard context.
    try { sessionStorage.setItem("channelwatch-restart-focus", "restore") } catch {}
    window.location.reload()
  }

  return (
    <>
      <header ref={headerRef} className="ios-navigation-material fixed inset-x-0 top-0 z-40 w-full border-b border-border/70 bg-background/88 shadow-[0_10px_30px_-30px_hsl(220_40%_10%/0.5)] backdrop-blur-xl">
        <div className="flex min-h-16 w-full items-center justify-between gap-2 px-2 py-2 sm:px-4 md:px-6 md:pl-[84px]">
          <div data-testid="header-primary-controls" className="flex min-w-0 items-center gap-1 sm:gap-2 md:gap-4">
            <Button
              variant="ghost"
              size="icon"
              className="min-h-11 min-w-11 shrink-0 md:hidden"
              onClick={(event) => window.dispatchEvent(new CustomEvent("toggle-mobile-sidebar", { detail: event.currentTarget }))}
              aria-label={t("header.toggleMenu")}
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </Button>

            {availableDvrs.length > 0 && (
              <Select value={selectedDvr} onValueChange={setSelectedDvr}>
                <SelectTrigger
                  className="h-auto min-h-11 w-[104px] min-w-0 max-w-[104px] whitespace-normal border-border/60 px-2.5 py-2 text-[13px] min-[360px]:w-[132px] min-[360px]:max-w-[132px] sm:w-[140px] sm:min-w-[140px] sm:max-w-[200px] [&>span]:break-words [&>span]:whitespace-normal [&>span]:[overflow-wrap:anywhere]"
                  aria-label={t("header.selectDvr")}
                  title={selectedDvr === "all" ? t("header.allDvrs") : availableDvrs.find((dvr) => dvr.id === selectedDvr)?.name || availableDvrs.find((dvr) => dvr.id === selectedDvr)?.host}
                >
                  <div className="flex min-w-0 flex-1 items-center gap-1.5 [&>span]:min-w-0 [&>span]:flex-1 [&>span]:break-words [&>span]:[overflow-wrap:anywhere]">
                    <Server className="h-3.5 w-3.5 shrink-0 text-muted-foreground hidden min-[360px]:block" />
                    <SelectValue className="min-w-0 break-words text-left [overflow-wrap:anywhere]" placeholder={t("header.allDvrs")} />
                  </div>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("header.allDvrs")}</SelectItem>
                  {availableDvrs.map((dvr) => (
                    <SelectItem key={dvr.id} value={dvr.id}>
                      {dvr.name || dvr.host}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div data-testid="header-utility-controls" className="flex shrink-0 items-center gap-1 sm:gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="hidden h-auto min-h-11 whitespace-normal px-3 py-2 text-center text-[13px] xl:inline-flex"
              onClick={() => setActiveView("settings:security")}
            >
              {securityStatus ? <SecurityModeBadge status={securityStatus} compact /> : t("header.security")}
            </Button>

            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="hidden min-h-11 min-w-11 items-center gap-1.5 px-0 md:flex xl:h-auto xl:min-w-0 xl:whitespace-normal xl:px-3 xl:py-2"
                    aria-label={t("header.restart")}
                    data-restart-trigger
                    onClick={handleRestart}
                    disabled={isRestarting}
                  >
                    <Power className={`h-4 w-4 ${isRestarting ? "animate-spin" : ""}`} />
                    <span className="hidden xl:inline">{t("header.restart")}</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{t("header.restartTooltip")}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            {/* Mobile restart button */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex min-h-11 min-w-11 items-center gap-1 px-0 md:hidden"
                    data-restart-trigger
                    onClick={handleRestart}
                    disabled={isRestarting}
                    aria-label={t("header.restart")}
                  >
                    <Power className={`h-4 w-4 ${isRestarting ? "animate-spin" : ""}`} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{t("header.restart")}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            {whoAmI?.authenticated && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex min-h-11 min-w-11 items-center gap-1.5 px-0 xl:h-auto xl:min-w-0 xl:whitespace-normal xl:px-3 xl:py-2"
                      onClick={handleLogout}
                      disabled={isLoggingOut}
                      aria-label={t("header.signOut")}
                      data-testid="header-sign-out"
                    >
                      {isLoggingOut ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <LogOut className="h-4 w-4" />
                      )}
                      <span className="hidden xl:inline">
                        {isLoggingOut ? t("header.signingOut") : t("header.signOut")}
                      </span>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>{t("header.signOut")}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}

            <ModeToggle />
          </div>
        </div>
      </header>

      {/* Restart Overlay */}
      {overlayState !== "idle" && (
        <DialogPrimitive.Root open onOpenChange={(open) => {
          if (!open && overlayState !== "restarting") handleDismissOverlay()
        }}>
        <DialogPrimitive.Portal>
        <div
          className={`fixed inset-0 z-50 flex items-center justify-center px-4 ${
            overlayState === "dismissing" ? "animate-overlay-out" : "animate-overlay-in"
          }`}
          onAnimationEnd={() => {
            if (overlayState === "dismissing") {
              handleDismissOverlay()
            }
          }}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />

          {/* Card */}
          <DialogPrimitive.Content
            ref={setRestartDialog}
            aria-modal="true"
            aria-describedby={undefined}
            onPointerDownOutside={(event) => event.preventDefault()}
            onEscapeKeyDown={(event) => {
              if (overlayState === "restarting") event.preventDefault()
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault()
              requestAnimationFrame(() => restartFocusRef.current?.focus())
            }}
            className={`relative z-10 w-full max-w-sm rounded-[20px] border border-zinc-700/80 bg-zinc-900 p-7 text-center shadow-[0_30px_90px_-20px_rgb(0_0_0/0.7)] ${
              overlayState === "dismissing" ? "animate-overlay-card-out" : "animate-overlay-card-in"
            }`}
          >
            <DialogPrimitive.Title className="sr-only">{t("header.restart")}</DialogPrimitive.Title>
            <div role="status" aria-live="polite" aria-atomic="true">
            {/* Restarting State */}
            {overlayState === "restarting" && (
              <>
                <div className="flex justify-center mb-4">
                  <div className="rounded-full bg-blue-500/20 p-4">
                    <Loader2 className="h-8 w-8 text-blue-400 animate-spin" />
                  </div>
                </div>
                <h2 className="text-lg font-semibold text-zinc-100 mb-1">
                  {t("header.overlay.restarting")}
                </h2>
                <p className="text-sm text-zinc-400 mb-4">
                  {t("header.overlay.waiting", { elapsed })}
                </p>
                <p className="text-xs text-zinc-500">
                  {t("header.overlay.reconnectNote")}
                </p>
              </>
            )}

            {/* Success State */}
            {overlayState === "success" && (
              <>
                <div className="flex justify-center mb-4">
                  <div className="rounded-full bg-green-500/20 p-4 animate-icon-bounce">
                    <CheckCircle className="h-8 w-8 text-green-400" />
                  </div>
                </div>
                <h2 className="text-lg font-semibold text-zinc-100 mb-1">
                  {t("header.overlay.success")}
                </h2>
                <p className="text-sm text-zinc-400">
                  {t("header.overlay.reconnected", { elapsed })}
                </p>
              </>
            )}

            {overlayState === "degraded" && (
              <>
                <div className="flex justify-center mb-4">
                  <div className="rounded-full bg-amber-500/20 p-4 animate-icon-bounce">
                    <Server className="h-8 w-8 text-amber-400" />
                  </div>
                </div>
                <h2 className="text-lg font-semibold text-zinc-100 mb-1">
                  {t("header.overlay.degraded")}
                </h2>
                <p className="text-sm text-zinc-400">
                  {t("header.overlay.degradedHint")}
                </p>
              </>
            )}

            {/* Failed State */}
            {overlayState === "failed" && (
              <>
                <div className="flex justify-center mb-4">
                  <div className="rounded-full bg-red-500/20 p-4 animate-icon-bounce">
                    <XCircle className="h-8 w-8 text-red-400" />
                  </div>
                </div>
                <h2 className="text-lg font-semibold text-zinc-100 mb-1">
                  {t("header.overlay.failed")}
                </h2>
                <p className="text-sm text-zinc-400 mb-6">
                  {t("header.overlay.failedHint")}
                </p>
                <div className="flex gap-3 justify-center">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleRetry}
                    className="border-zinc-600 text-zinc-300 hover:bg-zinc-800"
                  >
                    <RefreshCw className="h-4 w-4 mr-1" />
                    {t("header.overlay.retry")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => window.location.reload()}
                    className="border-zinc-600 text-zinc-300 hover:bg-zinc-800"
                  >
                    {t("header.overlay.reloadPage")}
                  </Button>
                </div>
              </>
            )}
            </div>
          </DialogPrimitive.Content>
        </div>
        </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
      )}
    </>
  )
}
