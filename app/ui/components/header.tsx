"use client"

import { useState, useEffect, useRef, useContext, createContext } from "react"
import { ModeToggle } from "@/components/mode-toggle"
import { Button } from "@/components/base/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { ApiError, signalContainerRestart, fetchSettings, pollForRecovery, fetchSecurityStatus } from "@/lib/api"
import { t } from "@/lib/i18n"
import { useToast } from "@/hooks/use-toast"
import { SecurityModeBadge } from "@/components/settings/security-section"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import type { SecurityStatus } from "@/lib/types"
import {
  Menu,
  Power,
  Loader2,
  CheckCircle,
  XCircle,
  RefreshCw,
  Server,
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

type OverlayState = "idle" | "restarting" | "success" | "failed" | "dismissing"

export function Header() {
  const [isRestarting, setIsRestarting] = useState(false)
  const [overlayState, setOverlayState] = useState<OverlayState>("idle")
  const [elapsed, setElapsed] = useState(0)
  const [securityStatus, setSecurityStatus] = useState<SecurityStatus | null>(null)
  const cancelPollRef = useRef<(() => void) | null>(null)
  const { toast } = useToast()
  const { setActiveView } = useContext(HeaderContext)
  const { selectedDvr, setSelectedDvr, availableDvrs } = useDvrSelection()

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

    loadSecurityStatus()
    const handleRefresh = () => {
      loadSecurityStatus()
    }
    window.addEventListener("channelwatch-auth-state-changed", handleRefresh)
    return () => {
      cancelled = true
      window.removeEventListener("channelwatch-auth-state-changed", handleRefresh)
    }
  }, [])

  const handleRestart = async () => {
    try {
      if (!confirm(t("header.restartConfirm"))) {
        return
      }

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
          // Re-bootstrap API key
          try {
            await fetchSettings()
          } catch {
            // Non-critical, will be picked up on next poll
          }

          setOverlayState("success")

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
      onTick: (elapsedMs) => {
        setElapsed(Math.floor(elapsedMs / 1000))
      },
      onRecovered: async () => {
        try {
          await fetchSettings()
        } catch {}
        setOverlayState("success")
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
    // Reload the page to get fresh state
    window.location.reload()
  }

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-40 border-b bg-background/95 backdrop-blur-lg supports-[backdrop-filter]:bg-background/60 w-full">
        <div className="flex h-16 items-center justify-between px-4 md:px-6 md:pl-[76px] w-full">
          <div className="flex items-center gap-2 md:gap-4">
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              onClick={() => window.dispatchEvent(new CustomEvent("toggle-mobile-sidebar"))}
            >
              <Menu className="h-5 w-5" />
              <span className="sr-only">{t("header.toggleMenu")}</span>
            </Button>

            {availableDvrs.length > 0 && (
              <Select value={selectedDvr} onValueChange={setSelectedDvr}>
                <SelectTrigger
                  className="h-8 text-xs min-w-[120px] max-w-[180px] border-border/60"
                  aria-label={t("header.selectDvr")}
                >
                  <div className="flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
                    <Server className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <SelectValue placeholder={t("header.allDvrs")} />
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

          {/* Mobile title */}
          <div className="absolute left-1/2 transform -translate-x-1/2 flex items-center gap-2 md:hidden">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
            <img
              src="/images/channelwatch-logo.png"
              alt={t("sidebar.logoAlt")}
              className="h-6 w-auto"
            />
            <span className="text-lg font-semibold">{t("header.brandName")}</span>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2 text-xs"
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
                    className="hidden md:flex items-center gap-1"
                    onClick={handleRestart}
                    disabled={isRestarting}
                  >
                    <Power className={`h-4 w-4 ${isRestarting ? "animate-spin" : ""}`} />
                    {t("header.restart")}
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
                    className="md:hidden flex items-center gap-1"
                    onClick={handleRestart}
                    disabled={isRestarting}
                  >
                    <Power className={`h-4 w-4 ${isRestarting ? "animate-spin" : ""}`} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{t("header.restart")}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            <ModeToggle />
          </div>
        </div>
      </header>

      {/* Restart Overlay */}
      {overlayState !== "idle" && (
        <div
          className={`fixed inset-0 z-50 flex items-center justify-center ${
            overlayState === "dismissing" ? "animate-overlay-out" : "animate-overlay-in"
          }`}
          onAnimationEnd={() => {
            if (overlayState === "dismissing") {
              handleDismissOverlay()
            }
          }}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" />

          {/* Card */}
          <div
            className={`relative z-10 bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl p-8 max-w-sm w-full mx-4 text-center ${
              overlayState === "dismissing" ? "animate-overlay-card-out" : "animate-overlay-card-in"
            }`}
          >
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
        </div>
      )}
    </>
  )
}
