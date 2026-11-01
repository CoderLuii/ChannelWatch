"use client"

import { useEffect, useLayoutEffect, useRef, useState, type ComponentType } from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import {
  Bell,
  ChevronRight,
  HeartPulse,
  History,
  Home,
  Info,
  LifeBuoy,
  MoreHorizontal,
  Settings,
  X,
} from "lucide-react"

import { Button } from "@/components/base/button"
import { SheetHandle } from "@/components/base/dialog"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/base/tooltip"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

interface SidebarProps {
  activeView: string
  setActiveView: (view: string) => void
  isMobile?: boolean
}

interface NavigationItem {
  view: string
  labelKey: string
  icon: ComponentType<{ className?: string; strokeWidth?: number; "aria-hidden"?: boolean | "true" | "false" }>
  matches?: (activeView: string) => boolean
}

const primaryNavigation: NavigationItem[] = [
  { view: "overview", labelKey: "nav.dashboard", icon: Home },
  { view: "watch-history", labelKey: "nav.watchHistory", icon: History },
  { view: "notification-log", labelKey: "nav.notificationLog", icon: Bell },
  { view: "settings", labelKey: "nav.settings", icon: Settings, matches: (view) => view.startsWith("settings") },
  { view: "diagnostics", labelKey: "nav.diagnostics", icon: HeartPulse },
]

const utilityNavigation: NavigationItem[] = [
  { view: "help-feedback", labelKey: "nav.helpFeedback", icon: LifeBuoy },
  { view: "about", labelKey: "nav.about", icon: Info },
]

const mobilePrimaryNavigation = ["overview", "watch-history", "notification-log", "settings"]
  .map((view) => primaryNavigation.find((item) => item.view === view)!)
export const mobileMoreNavigation = ["diagnostics", "help-feedback", "about"]
  .map((view) => [...primaryNavigation, ...utilityNavigation].find((item) => item.view === view)!)

const mobileTabLabelKeys: Record<string, string> = {
  "overview": "nav.tabHome",
  "watch-history": "nav.tabHistory",
  "notification-log": "nav.tabLogs",
}

// Tab labels and icons use pixel sizes so enlarged root text never splits a
// label mid-word. The accessible name comes from aria-label at full size.
const mobileTabClassName = "flex min-h-[56px] min-w-0 flex-1 flex-col items-center justify-center gap-[2px] rounded-control px-[2px] py-[4px] text-[clamp(10px,0.6875rem,12px)] font-semibold leading-[14px] text-muted-foreground transition-colors active:[&>span:first-child]:bg-selected active:[&>span:first-child]:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
const mobileTabPillClassName = "flex h-[28px] w-[48px] max-w-full items-center justify-center rounded-full transition-colors motion-reduce:transition-none"
const SIDEBAR_EXPANDED_KEY = "channelwatch-sidebar-expanded"
const mobileTabLabelClassName = "block max-w-full overflow-hidden text-ellipsis whitespace-nowrap"

export function Sidebar({ activeView, setActiveView, isMobile: propIsMobile }: SidebarProps) {
  const [isMobile, setIsMobile] = useState(propIsMobile || false)
  const [isOpen, setIsOpen] = useState(false)
  const [collapsedChoice, setCollapsedChoice] = useState(true)
  const [isNarrow, setIsNarrow] = useState(false)
  const isCollapsed = collapsedChoice || isNarrow
  const previousFocus = useRef<HTMLElement | null>(null)
  const mobileNavigationRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (propIsMobile !== undefined) {
      setIsMobile(propIsMobile)
      return
    }
    const checkIsMobile = () => setIsMobile(window.innerWidth < 768)
    checkIsMobile()
    window.addEventListener("resize", checkIsMobile)
    return () => window.removeEventListener("resize", checkIsMobile)
  }, [propIsMobile])

  useEffect(() => {
    const handler = (event: Event) => {
      if (!isOpen) {
        const trigger = (event as CustomEvent<HTMLElement>).detail
        previousFocus.current = trigger instanceof HTMLElement ? trigger : document.activeElement as HTMLElement
      }
      setIsOpen(current => !current)
    }
    window.addEventListener("toggle-mobile-sidebar", handler)
    return () => window.removeEventListener("toggle-mobile-sidebar", handler)
  }, [isOpen])

  useEffect(() => {
    setIsOpen(false)
  }, [activeView, isMobile])

  useLayoutEffect(() => {
    const navigation = mobileNavigationRef.current
    if (!isMobile || !navigation) {
      document.documentElement.style.removeProperty("--mobile-navigation-height")
      return
    }
    const publishHeight = () => {
      document.documentElement.style.setProperty("--mobile-navigation-height", `${Math.ceil(navigation.getBoundingClientRect().height)}px`)
    }
    publishHeight()
    const observer = new ResizeObserver(publishHeight)
    observer.observe(navigation)
    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty("--mobile-navigation-height")
    }
  }, [isMobile])

  useLayoutEffect(() => {
    try {
      if (localStorage.getItem(SIDEBAR_EXPANDED_KEY) === "1") setCollapsedChoice(false)
    } catch {}
    const narrow = window.matchMedia("(max-width: 1023.98px)")
    const syncNarrow = () => setIsNarrow(narrow.matches)
    syncNarrow()
    narrow.addEventListener("change", syncNarrow)
    return () => narrow.removeEventListener("change", syncNarrow)
  }, [])

  const toggleCollapsed = () => {
    const next = !collapsedChoice
    setCollapsedChoice(next)
    try {
      localStorage.setItem(SIDEBAR_EXPANDED_KEY, next ? "0" : "1")
    } catch {}
  }

  useLayoutEffect(() => {
    const root = document.documentElement.style
    if (isMobile) {
      root.removeProperty("--app-sidebar-width")
      return
    }
    root.setProperty("--app-sidebar-width", `calc(${isCollapsed ? "68px" : "16rem"} + env(safe-area-inset-left))`)
    return () => { root.removeProperty("--app-sidebar-width") }
  }, [isMobile, isCollapsed])

  const renderNavigationItem = (item: NavigationItem) => {
    const selected = item.matches ? item.matches(activeView) : activeView === item.view
    const label = t(item.labelKey)
    const Icon = item.icon
    const button = (
      <Button
        key={item.view}
        type="button"
        variant="ghost"
        size="sm"
        aria-label={label}
        aria-current={selected ? "page" : undefined}
        className={cn(
          "min-h-11 w-full text-[15px] active:scale-100",
          selected && "bg-primary/12 text-primary hover:bg-primary/12 hover:text-primary",
          !isMobile && isCollapsed ? "justify-center px-2" : "justify-start",
        )}
        onClick={() => setActiveView(item.view)}
      >
        <Icon className={cn("h-[18px] w-[18px] shrink-0", !isMobile && isCollapsed ? "" : "mr-2")} strokeWidth={1.8} aria-hidden="true" />
        {(!isMobile && !isCollapsed) || isMobile ? label : null}
      </Button>
    )
    if (!isCollapsed) return button
    return (
      <Tooltip key={item.view}>
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    )
  }

  const renderMobileBottomItem = (item: NavigationItem) => {
    const selected = item.matches ? item.matches(activeView) : activeView === item.view
    const label = t(item.labelKey)
    const Icon = item.icon
    return (
      <button
        key={item.view}
        type="button"
        className={cn(mobileTabClassName, selected && "text-primary")}
        aria-label={label}
        aria-current={selected ? "page" : undefined}
        onClick={() => setActiveView(item.view)}
      >
        <span className={cn(mobileTabPillClassName, selected && "bg-primary/12")} aria-hidden="true">
          <Icon className="h-[21px] w-[21px]" strokeWidth={selected ? 2.2 : 1.8} aria-hidden="true" />
        </span>
        <span className={mobileTabLabelClassName} aria-hidden="true">{mobileTabLabelKeys[item.view] ? t(mobileTabLabelKeys[item.view]) : label}</span>
      </button>
    )
  }

  const collapseLabel = isCollapsed ? t("sidebar.expandSidebar") : t("sidebar.collapseSidebar")
  const collapseButton = (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("min-h-11 w-full max-lg:hidden", isCollapsed ? "justify-center px-2" : "justify-start")}
      onClick={toggleCollapsed}
      aria-label={collapseLabel}
      aria-expanded={!isCollapsed}
    >
      <ChevronRight
        className={cn(
          "h-4 w-4 transition-transform duration-[var(--motion-base)] ease-out motion-reduce:transition-none",
          isCollapsed ? "" : "mr-2 rotate-180",
        )}
        aria-hidden="true"
      />
      {!isCollapsed ? collapseLabel : null}
    </Button>
  )

  const sidebar = (
      <aside
        className={cn(
          "app-sidebar fixed inset-y-0 left-0 z-50 flex flex-col max-md:hidden border-r border-hairline-strong bg-background pl-[env(safe-area-inset-left)]",
          isCollapsed ? "w-[calc(68px+env(safe-area-inset-left))]" : "w-[calc(16rem+env(safe-area-inset-left))]",
        )}
      >
        <div className={cn(
          "sidebar-brand flex min-h-16 shrink-0 items-center border-b border-hairline-strong p-2",
          isCollapsed ? "justify-center" : "justify-between gap-2 px-4",
        )}>
          <div className="flex min-w-0 items-center gap-2">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
            <img src="/images/channelwatch-logo.png" alt={t("sidebar.logoAlt")} className="h-8 w-auto shrink-0" />
            {!isCollapsed ? <span className="truncate text-base font-semibold tracking-[-0.02em]">{t("sidebar.brandName")}</span> : null}
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-3" aria-label={t("sidebar.navigation")}>
          <div className="space-y-1">{primaryNavigation.map(renderNavigationItem)}</div>
        </nav>

        <nav className="shrink-0 border-t border-hairline-strong px-3 py-2" aria-label={t("sidebar.utilityNavigation")}>
          <div className="space-y-1">{utilityNavigation.map(renderNavigationItem)}</div>
        </nav>

        <div className="sidebar-collapse shrink-0 border-t border-hairline-strong p-2">
          {isCollapsed ? (
            <Tooltip>
              <TooltipTrigger asChild>{collapseButton}</TooltipTrigger>
              <TooltipContent side="right">{collapseLabel}</TooltipContent>
            </Tooltip>
          ) : collapseButton}
        </div>
      </aside>
  )

  const moreSelected = mobileMoreNavigation.some((item) => item.view === activeView)

  const moreSheet = (
    <DialogPrimitive.Root open={isOpen} onOpenChange={setIsOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="mobile-sheet-overlay fixed inset-0 z-40 bg-scrim" />
        <DialogPrimitive.Content
          data-testid="mobile-more-sheet"
          aria-describedby={undefined}
          className="mobile-sheet fixed inset-x-0 bottom-0 z-50 flex max-h-[85dvh] flex-col rounded-t-sheet border-t border-hairline-strong bg-raised pb-[max(16px,env(safe-area-inset-bottom))] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)] shadow-3 focus:outline-none"
          onCloseAutoFocus={event => {
            event.preventDefault()
            requestAnimationFrame(() => {
              const trigger = previousFocus.current
              if (trigger?.isConnected && trigger.getClientRects().length > 0) trigger.focus()
              else document.querySelector<HTMLElement>('nav button[aria-current="page"]')?.focus()
            })
          }}
        >
          <SheetHandle onDismiss={() => setIsOpen(false)} maxWidth="767.98px" className="shrink-0">
            <div className="flex items-center justify-between gap-2 px-4 pb-1 pt-2">
              <DialogPrimitive.Title className="min-w-0 truncate text-base font-semibold">{t("nav.moreSheetTitle")}</DialogPrimitive.Title>
              <DialogPrimitive.Close asChild>
                <Button type="button" variant="ghost" size="icon" className="min-h-11 min-w-11 shrink-0" aria-label={t("nav.closeMore")}>
                  <X className="h-5 w-5" aria-hidden="true" />
                </Button>
              </DialogPrimitive.Close>
            </div>
          </SheetHandle>
          <nav className="min-h-0 overflow-y-auto overscroll-contain px-4 pb-1" aria-label={t("sidebar.utilityNavigation")}>
            <ul className="divide-y divide-hairline overflow-hidden rounded-control bg-fill">
              {mobileMoreNavigation.map((item) => {
                const selected = activeView === item.view
                const Icon = item.icon
                return (
                  <li key={item.view}>
                    <button
                      type="button"
                      className={cn(
                        "press-row flex min-h-[52px] w-full items-center gap-3 px-3 py-2 text-left text-[15px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                        selected && "text-accent-foreground",
                      )}
                      aria-current={selected ? "page" : undefined}
                      onClick={() => {
                        setActiveView(item.view)
                        setIsOpen(false)
                      }}
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center" aria-hidden="true">
                        <Icon className="h-[18px] w-[18px]" strokeWidth={1.8} aria-hidden="true" />
                      </span>
                      <span className="min-w-0 flex-1 truncate">{t(item.labelKey)}</span>
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    </button>
                  </li>
                )
              })}
            </ul>
          </nav>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )

  return (
    <TooltipProvider delayDuration={300}>
      {isMobile ? moreSheet : sidebar}
      {!isMobile ? <div className={cn("hidden md:block", isCollapsed ? "ml-[68px]" : "ml-64")} /> : null}
      {isMobile ? (
        <nav
          ref={mobileNavigationRef}
          className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-hairline-strong bg-background pb-[max(4px,env(safe-area-inset-bottom))] pl-[max(4px,env(safe-area-inset-left))] pr-[max(4px,env(safe-area-inset-right))] pt-[4px] md:hidden"
          aria-label={t("sidebar.navigation")}
        >
          {mobilePrimaryNavigation.map(renderMobileBottomItem)}
          <button
            type="button"
            className={cn(mobileTabClassName, moreSelected && "text-primary")}
            aria-label={t("nav.more")}
            aria-haspopup="dialog"
            aria-expanded={isOpen}
            onClick={(event) => {
              previousFocus.current = event.currentTarget
              setIsOpen(true)
            }}
          >
            <span className={cn(mobileTabPillClassName, moreSelected && "bg-primary/12")} aria-hidden="true">
              <MoreHorizontal className="h-[21px] w-[21px]" strokeWidth={1.8} aria-hidden="true" />
            </span>
            <span className={mobileTabLabelClassName} aria-hidden="true">{t("nav.more")}</span>
          </button>
        </nav>
      ) : null}
    </TooltipProvider>
  )
}
