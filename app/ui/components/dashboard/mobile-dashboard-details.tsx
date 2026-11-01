"use client"

import { createContext, useEffect, useRef, useState, type ReactNode } from "react"
import { AlertTriangle, ChevronDown, ChevronRight, CircleAlert, type LucideIcon } from "lucide-react"
import { Button } from "@/components/base/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/base/dialog"
import { Figure } from "@/components/base/figure"
import { StatusBadge } from "@/components/base/status-badge"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

type SummaryStatus = {
  tone: "warning" | "critical"
  label: string
}

/** True inside a tile's sheet, where a card shows its detail directly instead of opening a second sheet. */
export const TileSheetContext = createContext(false)

export interface MobileDetailsTile {
  label: string
  value: string | null
  icon: LucideIcon
  loading?: boolean
}

export function MobileDashboardDetails({ id, title, summary, errorSummary, summaryStatus, children, className, hasError = false, integratedCard = false, tile, openSignal = 0 }: { id: string; title: string; summary?: string; errorSummary?: string; summaryStatus?: SummaryStatus; children: ReactNode; className?: string; hasError?: boolean; integratedCard?: boolean; tile?: MobileDetailsTile; /** Each new value opens the sheet, or brings focus to the card where it sits inline. */ openSignal?: number }) {
  const [expanded, setExpanded] = useState(false)
  const open = expanded
  const tileLoading = Boolean(tile?.loading)
  const displaySummary = hasError ? errorSummary ?? t("common.unavailable") : summary
  const seenSignal = useRef(openSignal)
  const tileRoot = useRef<HTMLDivElement>(null)
  // Set while the sheet was opened from elsewhere on the page, so closing it can't scroll to the tile.
  const signalOpener = useRef<{ element: Element | null } | null>(null)
  useEffect(() => {
    if (openSignal === seenSignal.current) return
    seenSignal.current = openSignal
    if (window.matchMedia("(min-width: 1280px)").matches) {
      const card = document.getElementById(id)?.firstElementChild
      if (!(card instanceof HTMLElement)) return
      card.scrollIntoView({ block: "nearest", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })
      // The card is usually on screen already, so moving focus and outlining it is the visible answer.
      card.tabIndex = -1
      card.focus({ preventScroll: true })
      card.classList.remove("attention-outline")
      void card.offsetWidth
      card.classList.add("attention-outline")
      card.addEventListener("animationend", () => card.classList.remove("attention-outline"), { once: true })
      return
    }
    signalOpener.current = { element: document.activeElement }
    setExpanded(true)
  }, [id, openSignal])
  const restoreSignalFocus = (event: Event) => {
    const opener = signalOpener.current
    signalOpener.current = null
    if (!opener) return
    event.preventDefault()
    const element = opener.element
    const target = element instanceof HTMLElement && element !== document.body && element.isConnected && element.getClientRects().length > 0
      ? element
      : tileRoot.current?.querySelector<HTMLElement>("[data-tile-button]")
    target?.focus({ preventScroll: true })
  }
  const SummaryStatusIcon = summaryStatus?.tone === "critical" ? CircleAlert : AlertTriangle
  useEffect(() => {
    if (!tile) return
    // The sheet only exists below 1280, where the cards are not on the page.
    const wide = window.matchMedia("(min-width: 1280px)")
    const closeWhenWide = () => {
      if (wide.matches) setExpanded(false)
    }
    wide.addEventListener("change", closeWhenWide)
    return () => wide.removeEventListener("change", closeWhenWide)
  }, [tile])

  if (tile) {
    const TileIcon = tile.icon
    return (
      <div ref={tileRoot} data-testid={`${id}-tile`} className={cn("flex min-w-0 flex-col rounded-group border border-edge bg-card xl:contents", className)}>
        <Dialog open={open} onOpenChange={setExpanded}>
          <DialogTrigger asChild>
            <Button type="button" variant="ghost" data-tile-button="" className="press-row relative [&_svg]:size-4 h-auto min-h-11 w-full flex-1 flex-col items-stretch justify-start gap-0 whitespace-normal rounded-group p-4 text-left font-normal active:scale-100 xl:hidden" aria-label={`${title}: ${tileLoading ? t("common.loading") : hasError || tile.value === null ? displaySummary ?? t("common.unavailable") : tile.value}`}>
              <span className="flex min-w-0 items-center justify-between gap-2">
                <span className="min-w-0 text-xs font-normal text-muted-foreground">{tile.label}</span>
                <TileIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              </span>
              {tileLoading ? (
                <span className="block animate-pulse" aria-hidden="true">
                  <span className="mt-1.5 block h-7 w-16 rounded-thumb bg-muted" />
                  <span className="mt-1.5 flex h-5 items-center"><span className="block h-3 w-3/4 rounded-thumb bg-muted" /></span>
                </span>
              ) : (
                <>
                  {hasError || tile.value === null ? (
                    <span className="mt-1.5 block text-title font-semibold leading-none tracking-tight tabular-nums text-muted-foreground">–</span>
                  ) : (
                    <Figure value={tile.value} className="mt-1.5 block break-words text-title font-semibold leading-none tracking-tight text-foreground" />
                  )}
                  <span className={cn("mt-1.5 flex min-w-0 items-start gap-1 text-xs leading-5", "text-muted-foreground")}>
                    <span data-tile-summary="" className="min-w-0 break-words">
                      {!hasError && summaryStatus ? (
                        <span className={cn("font-medium", summaryStatus.tone === "critical" ? "text-danger-fg" : "text-warning-fg")}>{summaryStatus.label}</span>
                      ) : null}
                      {!hasError && summaryStatus && displaySummary ? " · " : null}
                      <span className="inline-block max-w-full">{displaySummary}</span>
                    </span>
                    <ChevronRight className="ml-auto mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  </span>
                </>
              )}
            </Button>
          </DialogTrigger>
          <DialogContent aria-describedby={undefined} onCloseAutoFocus={restoreSignalFocus} className="xl:hidden">
            <DialogHeader>
              <DialogTitle className="flex min-w-0 items-center gap-2 pr-9">
                <span className="min-w-0 break-words">{tile.label}</span>
                {!hasError && summaryStatus ? <StatusBadge variant={summaryStatus.tone === "critical" ? "danger" : "warning"}>{summaryStatus.label}</StatusBadge> : null}
              </DialogTitle>
            </DialogHeader>
            <TileSheetContext.Provider value={true}>
              <div data-testid={`${id}-sheet`} className="min-w-0 [&>div]:rounded-none [&>div]:border-0 [&>div]:bg-transparent [&>div]:p-0 [&>div]:shadow-none [&>div>div:first-child]:hidden [&>div>div:last-child]:pt-0">
                {children}
              </div>
            </TileSheetContext.Provider>
          </DialogContent>
        </Dialog>
        <div id={id} className="hidden xl:contents">
          {children}
        </div>
      </div>
    )
  }

  return (
    <div className={cn("min-w-0 rounded-group border border-edge bg-card self-start xl:contents", className, integratedCard && open && "self-stretch")}>
      <Button type="button" variant="ghost" className="press-row h-auto min-h-11 w-full justify-between gap-3 whitespace-normal rounded-group p-4 text-left active:scale-100 xl:hidden" aria-expanded={open} aria-controls={id} onClick={() => setExpanded(!open)}>
        <span className="min-w-0">
          <span className="block text-base font-semibold">{title}</span>
          {displaySummary || summaryStatus ? (
            <span className={cn("mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs font-normal text-muted-foreground")}>
              {!hasError && summaryStatus ? (
                <span className={cn("inline-flex items-center gap-1 font-semibold", summaryStatus.tone === "critical" ? "text-danger-fg" : "text-warning-fg")}>
                  <SummaryStatusIcon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  {summaryStatus.label}
                </span>
              ) : null}
              {displaySummary ? <span>{displaySummary}</span> : null}
            </span>
          ) : null}
        </span>
        <ChevronDown className={cn("h-5 w-5 shrink-0 transition-transform duration-[var(--motion-base)] ease-out motion-reduce:transition-none", open && "rotate-180")} aria-hidden="true" />
      </Button>
      <div id={id} className={cn("xl:contents", !open && "hidden", integratedCard && "max-xl:[&>div]:rounded-none max-xl:[&>div]:border-0 max-xl:[&>div]:bg-transparent max-xl:[&>div]:shadow-none max-xl:[&>div>div:first-child]:hidden")}>
        {children}
      </div>
    </div>
  )
}
